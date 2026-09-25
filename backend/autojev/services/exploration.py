from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import asdict
from typing import Any

from autojev.adapters.openai_compatible import (
    ModelResponseError,
    OpenAIAuxiliaryScenarioGenerator,
    OpenAICompatibleClient,
    OpenAIJevAgent,
)
from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.adapters.report import StandaloneHtmlReport
from autojev.adapters.repository import SQLiteRepository
from autojev.adapters.secrets import EphemeralCredentialVault, redact
from autojev.config import Settings
from autojev.domain.models import (
    AgentDecision,
    ExplorationOptions,
    ExplorationStatus,
    Observation,
    utc_now,
    ScenarioStatus,
    TestScenario,
)


class ExplorationManager:
    def __init__(
        self,
        repository: SQLiteRepository,
        credential_vault: EphemeralCredentialVault,
        report_renderer: StandaloneHtmlReport,
        settings: Settings,
        max_concurrent: int = 2,
    ) -> None:
        self.repository = repository
        self.credential_vault = credential_vault
        self.report_renderer = report_renderer
        self.settings = settings
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.input_events: dict[str, asyncio.Event] = {}
        self.semaphore = asyncio.Semaphore(max_concurrent)

    def submit(self, exploration_id: str) -> None:
        self.input_events[exploration_id] = asyncio.Event()
        task = asyncio.create_task(self._guarded_run(exploration_id))
        self.tasks[exploration_id] = task
        task.add_done_callback(lambda _: self.tasks.pop(exploration_id, None))

    def resume_after_user_input(self, exploration_id: str) -> None:
        event = self.input_events.get(exploration_id)
        if event is None:
            return
        event.set()

    def cancel(self, exploration_id: str) -> None:
        event = self.input_events.get(exploration_id)
        if event is not None:
            event.set()

    @staticmethod
    def _needs_login(observation: Observation) -> bool:
        element_text = " ".join(
            str(element.get("name") or "") for element in observation.interactive_elements
        )
        text = f"{observation.title} {observation.text} {observation.url} {element_text}".lower()
        login_words = ("log in", "login", "sign in", "sign-in", "entrar", "acessar", "username", "user-name")
        has_login_copy = any(word in text for word in login_words)
        has_password = any(
            str(element.get("type") or "").lower() == "password"
            or "password" in str(element.get("name") or "").lower()
            or "senha" in str(element.get("name") or "").lower()
            for element in observation.interactive_elements
        )
        return has_password and has_login_copy

    LOGIN_PANEL_MESSAGE = (
        "1) Preencha usuário e senha na página. "
        "2) Clique no botão Login do próprio site e aguarde entrar. "
        "3) Só então clique em Continuar exploração abaixo."
    )

    async def _poll_login_gone(
        self, executor: PlaywrightExecutor, deadline: float
    ) -> bool:
        """Retorna True quando a tela de login some (login feito no site).

        Exige 2 leituras seguidas sem formulário para não disparar durante
        o carregamento da navegação. Retorna False se o prazo estourar.
        """
        clear_reads = 0
        while True:
            await asyncio.sleep(1.5)
            if time.monotonic() >= deadline:
                return False
            try:
                if await executor.is_login_page():
                    clear_reads = 0
                else:
                    clear_reads += 1
                    if clear_reads >= 2:
                        return True
            except Exception:
                pass

    async def _wait_for_user_input(
        self,
        exploration_id: str,
        observation: Observation,
        executor: PlaywrightExecutor,
        panel_message: str | None = None,
        deadline: float | None = None,
    ) -> str:
        """Aguarda login manual. Retorna 'manual', 'auto' ou 'timeout'."""
        event = self.input_events.setdefault(exploration_id, asyncio.Event())
        event.clear()
        self.repository.update_exploration(
            exploration_id, status=ExplorationStatus.WAITING_INPUT
        )
        self.repository.add_event(
            exploration_id,
            "warning",
            "waiting_input",
            "Login detected. Complete the login in the browser window, then continue the exploration.",
            {"kind": "login", "url": observation.url},
        )
        await executor.show_intervention_panel(panel_message or self.LOGIN_PANEL_MESSAGE)
        limit = deadline if deadline is not None else time.monotonic() + 3600
        app_resume = asyncio.create_task(event.wait())
        browser_resume = asyncio.create_task(executor.wait_for_user_input())
        auto_resume = asyncio.create_task(self._poll_login_gone(executor, limit))
        done, pending = await asyncio.wait(
            {app_resume, browser_resume, auto_resume},
            return_when=asyncio.FIRST_COMPLETED,
        )
        auto = auto_resume in done and auto_resume.result() is True
        timed_out = auto_resume in done and auto_resume.result() is False
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            if task is not auto_resume:
                task.result()
        if self.repository.is_cancel_requested(exploration_id):
            return "cancelled"
        if timed_out:
            return "timeout"
        if auto:
            # Login feito direto no site: segue sozinho, sem exigir clique.
            # Garante que o loop do executor seja liberado e a seção de
            # ação seja escondida (o modo cancelado acima não limpa o painel).
            executor.notify_user_input()
            await executor.hide_intervention_panel()
            self.repository.update_exploration(
                exploration_id, status=ExplorationStatus.RUNNING
            )
            self.repository.add_event(
                exploration_id,
                "info",
                "login_auto_detected",
                "Login completed in the browser. Exploration resumed automatically.",
            )
            await executor.update_live_panel(
                "Exploração retomada", "Login detectado — seguindo automaticamente."
            )
            return "auto"
        self.repository.update_exploration(
            exploration_id, status=ExplorationStatus.RUNNING
        )
        self.repository.add_event(
            exploration_id,
            "info",
            "input_received",
            "User confirmed that the login step is complete.",
        )
        return "manual"

    async def shutdown(self) -> None:
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _guarded_run(self, exploration_id: str) -> None:
        async with self.semaphore:
            await self._run(exploration_id)

    def _client_for(self, provider_id: str) -> OpenAICompatibleClient:
        provider = self.repository.get_provider(provider_id)
        if not provider.enabled:
            raise RuntimeError(f"Provider {provider.name} is disabled")
        return OpenAICompatibleClient(
            provider.base_url,
            self.repository.get_provider_secret(provider_id),
            self.settings.http_timeout_seconds,
        )

    async def _record_model_usage(
        self,
        exploration_id: str,
        clients: list[OpenAICompatibleClient],
        executor: PlaywrightExecutor | None = None,
    ) -> None:
        usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost": 0.0,
            "cost_known": False,
        }
        for client in clients:
            current = client.drain_usage()
            for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cost"):
                usage[key] += current[key]
            usage["cost_known"] = usage["cost_known"] or current["cost_known"]
        if not any(usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")) and not usage["cost_known"]:
            return
        item = self.repository.get_exploration(exploration_id, include_details=False)
        previous = item.get("usage", {})
        total_tokens = int(previous.get("total_tokens") or 0) + int(usage["total_tokens"])
        cost_known = bool(previous.get("cost_known")) or bool(usage["cost_known"])
        cost = float(previous.get("cost") or 0.0) + float(usage["cost"])
        self.repository.update_exploration(
            exploration_id,
            prompt_tokens=int(previous.get("prompt_tokens") or 0) + int(usage["prompt_tokens"]),
            completion_tokens=int(previous.get("completion_tokens") or 0) + int(usage["completion_tokens"]),
            total_tokens=total_tokens,
            cost=cost if cost_known else None,
            cost_known=cost_known,
        )
        if executor:
            await executor.update_usage_panel(total_tokens, cost if cost_known else None, cost_known)

    @staticmethod
    def _fingerprint(url: str, decision: AgentDecision) -> str:
        raw = json.dumps(
            {
                "url": url.split("#", 1)[0],
                "action": decision.action,
                "target": decision.target,
                "value": decision.value,
                "credential_key": decision.credential_key,
                "key": decision.key,
                "destination": decision.url,
            },
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def _is_jev_model(model_id: str) -> bool:
        return model_id.removeprefix("~").startswith("typesafe/jev-")

    def _record_observation_issues(
        self,
        exploration_id: str,
        observation: Observation,
        known: set[str],
    ) -> None:
        for issue in observation.console_errors + observation.network_errors:
            stable_issue = {
                "type": issue.get("type"),
                "message": issue.get("message"),
                "url": issue.get("url"),
                "status_code": issue.get("status_code"),
            }
            key = hashlib.sha256(
                json.dumps(stable_issue, sort_keys=True).encode()
            ).hexdigest()
            if key in known:
                continue
            known.add(key)
            record = {**issue, "screenshot": observation.screenshot}
            self.repository.add_issue(exploration_id, record)
            self.repository.add_event(
                exploration_id,
                "error",
                "issue_detected",
                str(issue.get("message", "Browser issue observed")),
                {"type": issue.get("type"), "status_code": issue.get("status_code")},
            )

    def _record_screenshot(
        self,
        exploration_id: str,
        observation: Observation,
        label: str,
        scenario_id: str | None = None,
    ) -> None:
        if observation.screenshot:
            self.repository.add_screenshot(
                exploration_id, observation.screenshot, label, scenario_id
            )

    @staticmethod
    def _scenario_from_evidence(
        observation: Observation,
        decision: AgentDecision,
        result: Any,
        scenario_number: int,
    ) -> TestScenario:
        status = ScenarioStatus.SKIPPED
        if not result.success:
            status = (
                ScenarioStatus.BLOCKED
                if "PolicyBlocked" in result.message
                or "unavailable" in result.message.lower()
                else ScenarioStatus.FAIL
            )
        elif result.assertions:
            status = (
                ScenarioStatus.PASS
                if all(assertion.passed for assertion in result.assertions)
                else ScenarioStatus.FAIL
            )
        hint = decision.scenario_hint or {}
        return TestScenario(
            id=f"CT{scenario_number:03d}",
            title=str(hint.get("title") or f"Explore {decision.action} on {observation.title or observation.url}")[:300],
            description=str(hint.get("description") or decision.rationale)[:3000],
            preconditions=[str(item)[:500] for item in hint.get("preconditions", []) if item][:20],
            steps=[f"Execute {decision.action} on the observed page"],
            expected_result=str(hint.get("expected_result") or "The declared assertion should be observed.")[:3000],
            actual_result=result.message or f"Browser ended at {result.after_url}",
            status=status,
            url=result.after_url,
            created_at=utc_now(),
            screenshots=[result.screenshot] if result.screenshot else [],
            evidence={
                "action": decision.action,
                "target": decision.target,
                "before_url": result.before_url,
                "after_url": result.after_url,
                "action_succeeded": result.success,
                "assertions": [asdict(assertion) for assertion in result.assertions],
                "message": result.message,
            },
        )

    async def _run(self, exploration_id: str) -> None:
        credentials = self.credential_vault.get(exploration_id)
        secret_values = list(credentials.values())
        executor: PlaywrightExecutor | None = None
        options: ExplorationOptions | None = None
        terminal = ExplorationStatus.FAILED
        error_message: str | None = None
        try:
            item = self.repository.get_exploration(exploration_id, include_details=False)
            options = ExplorationOptions(**item["options"])
            if item["cancel_requested"]:
                terminal = ExplorationStatus.CANCELLED
                self.repository.add_event(
                    exploration_id,
                    "warning",
                    "cancelled",
                    "Exploration was cancelled before a browser was started",
                )
                return
            started = time.monotonic()
            deadline = started + options.max_duration_seconds
            agent_client = self._client_for(item["agent"]["provider_id"])
            auxiliary_client = self._client_for(item["auxiliary_llm"]["provider_id"])
            agent = OpenAIJevAgent(agent_client, item["agent"]["model_id"])
            screenshot_dir = self.settings.artifact_dir / exploration_id / "screenshots"
            auxiliary = OpenAIAuxiliaryScenarioGenerator(
                auxiliary_client,
                item["auxiliary_llm"]["model_id"],
                screenshot_dir,
            )
            executor = PlaywrightExecutor(
                screenshot_dir,
                options,
                headless=self.settings.headless,
                browser_name=self.settings.browser,
                secret_values=secret_values,
            )
            self.repository.update_exploration(
                exploration_id, status=ExplorationStatus.RUNNING, started_at=utc_now()
            )
            self.repository.add_event(
                exploration_id, "info", "started", "Exploration started"
            )
            observation = await asyncio.wait_for(
                executor.start(item["app_url"]),
                timeout=max(deadline - time.monotonic(), 0.1),
            )
            self.repository.update_exploration(exploration_id, current_url=observation.url)
            self._record_screenshot(exploration_id, observation, "Initial page")
            self.repository.add_event(
                exploration_id,
                "info",
                "browser_launched",
                "Playwright opened the application",
                {"url": observation.url},
            )
            await executor.show_live_panel()
            await executor.update_live_panel(
                "Exploração iniciada", f"Navegador aberto em {observation.url[:80]}"
            )
            history: list[dict[str, Any]] = []
            action_counts: dict[str, int] = {}
            url_visits: dict[str, int] = {}
            known_issues: set[str] = set()
            scenario_fingerprints: set[str] = set()
            scenario_number = 0
            consecutive_model_errors = 0
            last_canonical_url: str | None = None
            login_retried = False
            actions_executed = 0
            finish_rejections = 0
            consecutive_loop_rejections = 0
            tried_targets: set[tuple[str, str | None]] = set()
            # Guarda contra encerramento precoce: o JEV às vezes responde
            # "finish" na primeira decisão de uma página inexplorada.
            MIN_ACTIONS_BEFORE_FINISH = 5
            MAX_FINISH_REJECTIONS = 5
            # Teto de teimosia: após N rejeições seguidas sem nenhuma ação
            # nova, encerra (o resto é fumaça do modelo, não exploração).
            MAX_CONSECUTIVE_LOOP_REJECTIONS = 8
            self._record_observation_issues(exploration_id, observation, known_issues)

            for step in range(1, options.max_steps + 1):
                if self.repository.is_cancel_requested(exploration_id):
                    terminal = ExplorationStatus.CANCELLED
                    self.repository.add_event(
                        exploration_id, "warning", "cancelled", "Cancellation requested"
                    )
                    break
                if time.monotonic() - started >= options.max_duration_seconds:
                    terminal = ExplorationStatus.COMPLETED
                    self.repository.add_event(
                        exploration_id,
                        "warning",
                        "limit_reached",
                        "Maximum exploration duration reached",
                    )
                    break
                if self._needs_login(observation):
                    panel_message = None
                    if login_retried:
                        panel_message = (
                            "Login ainda não detectado! Você preencheu mas "
                            "não clicou no Login do site? Clique no botão Login "
                            "da página e aguarde entrar — ao entrar, a exploração "
                            "retoma sozinha."
                        )
                    outcome = await self._wait_for_user_input(
                        exploration_id,
                        observation,
                        executor,
                        panel_message,
                        deadline,
                    )
                    if outcome == "cancelled" or self.repository.is_cancel_requested(exploration_id):
                        terminal = ExplorationStatus.CANCELLED
                        break
                    if outcome == "timeout":
                        terminal = ExplorationStatus.COMPLETED
                        self.repository.add_event(
                            exploration_id,
                            "warning",
                            "limit_reached",
                            "Maximum exploration duration reached while waiting for login",
                        )
                        break
                    observation = await asyncio.wait_for(
                        executor.observe("after-user-input"),
                        timeout=max(deadline - time.monotonic(), 0.1),
                    )
                    self._record_screenshot(exploration_id, observation, "After login")
                    self.repository.update_exploration(
                        exploration_id, current_url=observation.url
                    )
                    if self._needs_login(observation):
                        # Usuário clicou "continuar" sem concluir o login:
                        # volta a aguardar em vez de deixar o JEV dar
                        # "finish" com 0 ações e relatório vazio.
                        # O painel mostra aviso explícito para não parecer
                        # que "nada acontece".
                        login_retried = True
                        self.repository.add_event(
                            exploration_id,
                            "warning",
                            "login_not_completed",
                            "Login page still detected after user confirmation. Waiting for login again.",
                            {"url": observation.url},
                        )
                        await executor.show_intervention_panel(
                            "Login ainda não detectado! Clique no botão Login "
                            "do próprio site, aguarde a próxima página carregar "
                            "e só então clique em Continuar exploração."
                        )
                        continue
                canonical_url = observation.url.split("#", 1)[0]
                if canonical_url != last_canonical_url:
                    url_visits[canonical_url] = url_visits.get(canonical_url, 0) + 1
                    last_canonical_url = canonical_url
                if url_visits[canonical_url] > 8:
                    terminal = ExplorationStatus.COMPLETED
                    self.repository.add_event(
                        exploration_id,
                        "warning",
                        "loop_prevented",
                        "Page visit limit reached; exploration stopped safely",
                    )
                    break
                try:
                    decision = await asyncio.wait_for(
                        agent.decide(
                            observation, history, sorted(credentials.keys())
                        ),
                        timeout=max(deadline - time.monotonic(), 0.1),
                    )
                    consecutive_model_errors = 0
                except ModelResponseError as exc:
                    await self._record_model_usage(exploration_id, [agent_client], executor)
                    consecutive_model_errors += 1
                    self.repository.add_event(
                        exploration_id,
                        "warning",
                        "invalid_agent_decision",
                        str(exc),
                    )
                    if consecutive_model_errors >= 3:
                        raise RuntimeError("JEV returned three invalid decisions in sequence") from exc
                    continue
                await self._record_model_usage(exploration_id, [agent_client], executor)
                if decision.action == "finish":
                    if (
                        actions_executed < MIN_ACTIONS_BEFORE_FINISH
                        and finish_rejections < MAX_FINISH_REJECTIONS
                    ):
                        finish_rejections += 1
                        history.append(
                            {
                                "action": "finish",
                                "target": None,
                                "result": (
                                    "rejected: exploration just started "
                                    f"({actions_executed} actions so far); "
                                    "choose a useful interactive action instead"
                                ),
                            }
                        )
                        self.repository.add_event(
                            exploration_id,
                            "warning",
                            "finish_rejected",
                            "Premature finish rejected; exploration continues",
                            {"actions_so_far": actions_executed},
                        )
                        await executor.update_live_panel(
                            f"Decisão {step} de {options.max_steps}",
                            "JEV tentou encerrar cedo — exploração continua.",
                        )
                        continue
                    terminal = ExplorationStatus.COMPLETED
                    self.repository.add_event(
                        exploration_id,
                        "info",
                        "agent_finished",
                        "JEV concluded that exploration was complete",
                        {"rationale": decision.rationale},
                    )
                    break
                fingerprint = self._fingerprint(observation.url, decision)
                action_counts[fingerprint] = action_counts.get(fingerprint, 0) + 1
                tried_targets.add((decision.action, decision.target))
                if action_counts[fingerprint] > 2:
                    consecutive_loop_rejections += 1
                    # Direciona o modelo para elementos ainda não tentados,
                    # em vez de só dizer "não" e desistir em seguida.
                    valid_refs = sorted(
                        (
                            str(element.get("ref"))
                            for element in observation.interactive_elements
                            if element.get("ref")
                        ),
                        key=lambda ref: int(ref[1:]) if ref[1:].isdigit() else 9999,
                    )
                    alternatives = [
                        ref
                        for ref in valid_refs
                        if (decision.action, ref) not in tried_targets
                    ][:6]
                    hint = (
                        f"rejected by loop protection: '{decision.action} on "
                        f"{decision.target}' was already tried "
                        f"{action_counts[fingerprint]}x on this page. You MUST "
                        "choose a DIFFERENT element."
                    )
                    if alternatives:
                        hint += f" Untried options include: {', '.join(alternatives)}."
                    history.append(
                        {
                            "action": decision.action,
                            "target": decision.target,
                            "result": hint,
                        }
                    )
                    self.repository.add_event(
                        exploration_id,
                        "warning",
                        "loop_prevented",
                        "Repeated action rejected",
                        {
                            "action": decision.action,
                            "target": decision.target,
                            "attempt": action_counts[fingerprint],
                        },
                    )
                    if consecutive_loop_rejections >= MAX_CONSECUTIVE_LOOP_REJECTIONS:
                        terminal = ExplorationStatus.COMPLETED
                        self.repository.add_event(
                            exploration_id,
                            "warning",
                            "agent_stuck",
                            "JEV kept repeating tried actions; exploration stopped",
                        )
                        break
                    continue
                self.repository.add_event(
                    exploration_id,
                    "info",
                    "action_started",
                    f"Executing {decision.action}",
                    {
                        "step": step,
                        "action": decision.action,
                        "target": decision.target,
                    },
                )
                next_action = actions_executed + 1
                await executor.update_live_panel(
                    f"Etapa {next_action} de {options.max_steps}",
                    f"Executando · {decision.action}"
                    f"{' ' + str(decision.target) if decision.target else ''}",
                )
                result, next_observation = await asyncio.wait_for(
                    executor.execute(decision, credentials),
                    timeout=max(deadline - time.monotonic(), 0.1),
                )
                safe_decision = redact(asdict(decision), secret_values)
                safe_result = redact(result.to_dict(), secret_values)
                self.repository.add_action(
                    exploration_id, step, safe_decision, safe_result
                )
                actions_executed += 1
                consecutive_loop_rejections = 0
                self.repository.update_exploration(
                    exploration_id,
                    current_url=next_observation.url,
                    steps_completed=actions_executed,
                )
                self._record_screenshot(exploration_id, next_observation, decision.action)
                self._record_observation_issues(
                    exploration_id, next_observation, known_issues
                )
                self.repository.add_event(
                    exploration_id,
                    "info" if result.success else "error",
                    "action_completed",
                    result.message,
                    {
                        "step": step,
                        "action": decision.action,
                        "success": result.success,
                        "url": result.after_url,
                    },
                )
                await executor.update_live_panel(
                    f"Etapa {actions_executed} de {options.max_steps}",
                    f"{'OK' if result.success else 'FALHA'} · {decision.action}"
                    f"{' ' + str(decision.target) if decision.target else ''} · "
                    f"{(result.after_url or '')[:80]}",
                )
                history.append(
                    {
                        "action": decision.action,
                        "target": decision.target,
                        "success": result.success,
                        "before_url": result.before_url,
                        "after_url": result.after_url,
                        "assertions": [asdict(value) for value in result.assertions],
                    }
                )
                # Gera cenário para toda ação executada (sucesso ou falha).
                # O JEV tipado (typesafe/jev-*) nunca retorna scenario_hint ou
                # assertions, então a condição antiga deixava o relatório vazio
                # mesmo com dezenas de ações bem-sucedidas.
                if options.generate_test_cases and fingerprint not in scenario_fingerprints:
                    scenario_fingerprints.add(fingerprint)
                    scenario_number += 1
                    if self._is_jev_model(item["agent"]["model_id"]):
                        scenario = self._scenario_from_evidence(
                            next_observation, decision, result, scenario_number
                        )
                    else:
                        scenario = await asyncio.wait_for(
                            auxiliary.create_scenario(
                                next_observation, decision, result, scenario_number
                            ),
                            timeout=max(deadline - time.monotonic(), 0.1),
                        )
                    await self._record_model_usage(exploration_id, [auxiliary_client], executor)
                    self.repository.add_scenario(
                        exploration_id, scenario_number, scenario
                    )
                    if result.screenshot:
                        # Associate the existing file with the scenario as a separate metadata row.
                        self.repository.add_screenshot(
                            exploration_id,
                            result.screenshot,
                            f"Evidence for {scenario.id}",
                            scenario.id,
                        )
                    self.repository.add_event(
                        exploration_id,
                        "info",
                        "scenario_created",
                        f"{scenario.id} — {scenario.title}",
                        {"scenario_id": scenario.id, "status": scenario.status},
                    )
                observation = next_observation
            else:
                terminal = ExplorationStatus.COMPLETED
                self.repository.add_event(
                    exploration_id,
                    "warning",
                    "limit_reached",
                    "Maximum number of steps reached",
                )
        except asyncio.CancelledError:
            terminal = ExplorationStatus.CANCELLED
            error_message = "Backend shutdown interrupted this exploration"
        except asyncio.TimeoutError:
            terminal = ExplorationStatus.COMPLETED
            self.repository.add_event(
                exploration_id,
                "warning",
                "limit_reached",
                "Maximum exploration duration reached",
            )
        except Exception as exc:
            terminal = ExplorationStatus.FAILED
            error_message = str(redact(str(exc), secret_values))[:2000]
            self.repository.add_event(
                exploration_id,
                "error",
                "failed",
                error_message,
            )
        finally:
            if executor:
                try:
                    await executor.update_live_panel(
                        "Exploração concluída",
                        f"Status: {terminal.value} — veja o relatório no AutoJev.",
                    )
                except Exception:
                    pass
                await executor.close()
            self.input_events.pop(exploration_id, None)
            self.credential_vault.discard(exploration_id)
            finished = utc_now()
            self.repository.update_exploration(
                exploration_id,
                status=terminal,
                finished_at=finished,
                error=error_message,
            )
            if options and options.generate_html_report:
                try:
                    exploration = self.repository.get_exploration(exploration_id)
                    report_path = (
                        self.settings.artifact_dir / exploration_id / "report.html"
                    )
                    generated = await asyncio.to_thread(
                        self.report_renderer.generate,
                        exploration,
                        str(report_path),
                    )
                    self.repository.update_exploration(
                        exploration_id, report_path=generated
                    )
                    self.repository.add_event(
                        exploration_id,
                        "info",
                        "report_ready",
                        "Standalone HTML report generated",
                    )
                except Exception as report_error:
                    self.repository.add_event(
                        exploration_id,
                        "error",
                        "report_failed",
                        str(redact(str(report_error), secret_values))[:1000],
                    )
