from __future__ import annotations

import asyncio
import hashlib
import json
import time
from contextlib import suppress
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
    ActionResult,
    AgentDecision,
    AssertionResult,
    ExplorationOptions,
    ExplorationStatus,
    Observation,
    ScenarioStatus,
    TestScenario,
    utc_now,
)
from autojev.services.coverage import CoveragePlanner, canonical_url


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
            str(element.get("name") or "")
            for element in observation.interactive_elements
        )
        text = f"{observation.title} {observation.text} {observation.url} {element_text}".lower()
        login_words = (
            "log in",
            "login",
            "sign in",
            "sign-in",
            "entrar",
            "acessar",
            "username",
            "user-name",
        )
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
        await executor.show_intervention_panel(
            panel_message or self.LOGIN_PANEL_MESSAGE
        )
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
        if (
            not any(
                usage[key]
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            )
            and not usage["cost_known"]
        ):
            return
        item = self.repository.get_exploration(exploration_id, include_details=False)
        previous = item.get("usage", {})
        total_tokens = int(previous.get("total_tokens") or 0) + int(
            usage["total_tokens"]
        )
        cost_known = bool(previous.get("cost_known")) or bool(usage["cost_known"])
        cost = float(previous.get("cost") or 0.0) + float(usage["cost"])
        self.repository.update_exploration(
            exploration_id,
            prompt_tokens=int(previous.get("prompt_tokens") or 0)
            + int(usage["prompt_tokens"]),
            completion_tokens=int(previous.get("completion_tokens") or 0)
            + int(usage["completion_tokens"]),
            total_tokens=total_tokens,
            cost=cost if cost_known else None,
            cost_known=cost_known,
        )
        if executor:
            await executor.update_usage_panel(
                total_tokens, cost if cost_known else None, cost_known
            )

    @staticmethod
    def _fingerprint(
        url: str, decision: AgentDecision, state_key: str | None = None
    ) -> str:
        raw = json.dumps(
            {
                "url": canonical_url(url),
                # Refs are only meaningful inside one semantic browser state.
                # The same e0 may legitimately identify a different control in
                # another modal/hash/React surface at the same URL.
                "state": state_key,
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
        result: ActionResult,
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
            title=str(
                hint.get("title")
                or f"Explore {decision.action} on {observation.title or observation.url}"
            )[:300],
            description=str(hint.get("description") or decision.rationale)[:3000],
            preconditions=[
                str(item)[:500] for item in hint.get("preconditions", []) if item
            ][:20],
            steps=[f"Execute {decision.action} on the observed page"],
            expected_result=str(
                hint.get("expected_result")
                or "The declared assertion should be observed."
            )[:3000],
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

    @staticmethod
    def _append_observable_state_evidence(
        result: ActionResult,
        decision: AgentDecision,
        observation: Observation,
        *,
        state_changed: bool,
        capture_console_errors: bool,
        capture_network_errors: bool,
    ) -> None:
        """Turn a verified post-action delta into deterministic assertions.

        JEV predicts useful behavior before execution, but Playwright owns the
        verdict. A visible route/DOM/control-state delta is concrete evidence
        that an interaction produced an observable result; correlated browser
        errors are evaluated alongside it so a changed screen with a failed API
        request cannot be reported as PASS.
        """

        if not result.success or not state_changed or decision.action in {
            "wait",
            "screenshot",
        }:
            return
        if not any(item.kind == "observable_state_change" for item in result.assertions):
            result.assertions.append(
                AssertionResult(
                    kind="observable_state_change",
                    expected="The route or interactive page state changes",
                    actual="An observable state delta was captured after the action",
                    passed=True,
                    message="Observed",
                )
            )
        if not (capture_console_errors or capture_network_errors):
            return
        console_count = len(observation.console_errors) if capture_console_errors else 0
        network_count = len(observation.network_errors) if capture_network_errors else 0
        error_count = console_count + network_count
        result.assertions.append(
            AssertionResult(
                kind="no_new_browser_errors",
                expected="0 new console or network errors",
                actual={
                    "console_errors": console_count,
                    "network_errors": network_count,
                },
                passed=error_count == 0,
                message=(
                    "Observed"
                    if error_count == 0
                    else "The action produced new browser errors"
                ),
            )
        )

    async def _materialize_scenarios(
        self,
        exploration_id: str,
        pending: list[tuple[Observation, AgentDecision, ActionResult, int]],
        auxiliary: OpenAIAuxiliaryScenarioGenerator,
        auxiliary_client: OpenAICompatibleClient,
        use_local_evidence: bool,
    ) -> None:
        """Persist every captured scenario, then enrich it when time allows.

        The deterministic evidence pass is intentionally first: the user sees
        all captured cases immediately and a slow auxiliary model can never
        make cases disappear. The second pass upgrades those drafts with the
        auxiliary LLM inside a bounded documentation window.
        """

        if not pending:
            return
        total = len(pending)
        self.repository.add_event(
            exploration_id,
            "info",
            "scenario_preparation_started",
            f"Preparando {total} casos de teste a partir das evidências capturadas",
            {"current": 0, "total": total, "stage": "drafts"},
        )

        # First make the complete, evidence-backed set durable. This pass is
        # local and fast, so a report remains complete even if the auxiliary
        # provider is unavailable or its documentation budget expires.
        for observation, decision, result, scenario_number in pending:
            scenario = self._scenario_from_evidence(
                observation, decision, result, scenario_number
            )
            self.repository.add_scenario(exploration_id, scenario_number, scenario)
            if result.screenshot:
                self.repository.add_screenshot(
                    exploration_id,
                    result.screenshot,
                    f"Evidence for {scenario.id}",
                    scenario.id,
                )
        self.repository.add_event(
            exploration_id,
            "info",
            "scenario_drafts_ready",
            f"{total} casos de teste já estão seguros com evidência do navegador",
            {"current": total, "total": total, "stage": "drafts"},
        )

        if use_local_evidence or self.repository.is_cancel_requested(exploration_id):
            reason = (
                "exploration_cancelled"
                if self.repository.is_cancel_requested(exploration_id)
                else "local_evidence_mode"
            )
            self.repository.add_event(
                exploration_id,
                "info",
                "scenario_postprocessing_completed",
                f"Casos finalizados: {total} com evidência validada pelo navegador",
                {
                    "current": total,
                    "total": total,
                    "enriched": 0,
                    "local_evidence": total,
                    "reason": reason,
                    "stage": "scenarios_complete",
                },
            )
            return

        documentation_deadline = time.monotonic() + min(max(len(pending) * 8, 45), 180)
        enriched = 0
        processed = 0
        completion_reason = "documentation_complete"
        self.repository.add_event(
            exploration_id,
            "info",
            "scenario_enrichment_started",
            "A LLM auxiliar começou a aprimorar os casos de teste",
            {"current": 0, "total": total, "stage": "enrichment"},
        )
        for observation, decision, result, scenario_number in pending:
            if self.repository.is_cancel_requested(exploration_id):
                completion_reason = "exploration_cancelled"
                break
            if time.monotonic() >= documentation_deadline:
                completion_reason = "documentation_time_limit"
                break

            remaining = max(documentation_deadline - time.monotonic(), 0.1)
            try:
                scenario = await asyncio.wait_for(
                    auxiliary.create_scenario(
                        observation, decision, result, scenario_number
                    ),
                    timeout=min(45.0, remaining),
                )
                self.repository.add_scenario(exploration_id, scenario_number, scenario)
                enriched += 1
                event_type = "scenario_enriched"
                event_level = "info"
                event_message = f"{scenario.id} aprimorado pela LLM auxiliar"
            except Exception as exc:
                completion_reason = exc.__class__.__name__
                event_type = "scenario_enrichment_skipped"
                event_level = "warning"
                event_message = (
                    f"CT{scenario_number:03d} manteve a evidência local; "
                    "a LLM auxiliar não respondeu a tempo"
                )
            processed += 1
            await self._record_model_usage(exploration_id, [auxiliary_client], None)
            self.repository.add_event(
                exploration_id,
                event_level,
                event_type,
                event_message,
                {
                    "scenario_id": f"CT{scenario_number:03d}",
                    "current": processed,
                    "total": total,
                    "enriched": enriched,
                    "local_evidence": total - enriched,
                    "stage": "enrichment",
                },
            )

        local_evidence = total - enriched
        self.repository.add_event(
            exploration_id,
            "info",
            "scenario_postprocessing_completed",
            (
                f"Casos finalizados: {enriched} aprimorados pela LLM e "
                f"{local_evidence} preservados com evidência do navegador"
            ),
            {
                "current": total,
                "total": total,
                "enriched": enriched,
                "local_evidence": local_evidence,
                "reason": completion_reason,
                "stage": "scenarios_complete",
            },
        )

    async def _run(self, exploration_id: str) -> None:
        credentials = self.credential_vault.get(exploration_id)
        secret_values = list(credentials.values())
        executor: PlaywrightExecutor | None = None
        options: ExplorationOptions | None = None
        coverage: CoveragePlanner | None = None
        auxiliary: OpenAIAuxiliaryScenarioGenerator | None = None
        auxiliary_client: OpenAICompatibleClient | None = None
        agent_model_id = ""
        auxiliary_model_id = ""
        pending_scenarios: list[
            tuple[Observation, AgentDecision, ActionResult, int]
        ] = []
        terminal = ExplorationStatus.FAILED
        error_message: str | None = None
        try:
            item = self.repository.get_exploration(
                exploration_id, include_details=False
            )
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
            agent_model_id = item["agent"]["model_id"]
            auxiliary_model_id = item["auxiliary_llm"]["model_id"]
            agent = OpenAIJevAgent(agent_client, agent_model_id)
            screenshot_dir = self.settings.artifact_dir / exploration_id / "screenshots"
            auxiliary = OpenAIAuxiliaryScenarioGenerator(
                auxiliary_client,
                auxiliary_model_id,
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
            self.repository.update_exploration(
                exploration_id, current_url=observation.url
            )
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
            known_issues: set[str] = set()
            scenario_fingerprints: set[str] = set()
            scenario_number = 0
            consecutive_model_errors = 0
            login_retried = False
            authentication_boundary_crossed = False
            actions_executed = 0
            consecutive_loop_rejections = 0
            planning_attempts = 0
            max_planning_attempts = max(options.max_steps * 6, 30)
            # Teto de teimosia: após N rejeições seguidas sem nenhuma ação
            # nova, encerra (o resto é fumaça do modelo, não exploração).
            MAX_CONSECUTIVE_LOOP_REJECTIONS = 8
            coverage = CoveragePlanner(item["app_url"], options.focus_area)
            coverage.observe(observation)
            self.repository.add_event(
                exploration_id,
                "info",
                "coverage_scope",
                (
                    f"Focused exploratory scope: {options.focus_area}"
                    if options.focus_area
                    else "Exploratory scope: entire same-origin application"
                ),
                {"focus_area": options.focus_area},
            )
            if not self._needs_login(observation):
                self._record_observation_issues(
                    exploration_id, observation, known_issues
                )

            # max_steps is an action budget. Invalid or rejected model
            # decisions use a separate bounded planning budget.
            while actions_executed < options.max_steps:
                if self.repository.is_cancel_requested(exploration_id):
                    terminal = ExplorationStatus.CANCELLED
                    self.repository.add_event(
                        exploration_id, "warning", "cancelled", "Cancellation requested"
                    )
                    break
                if time.monotonic() - started >= options.max_duration_seconds:
                    terminal = ExplorationStatus.PARTIAL
                    self.repository.add_event(
                        exploration_id,
                        "warning",
                        "coverage_partial",
                        "Maximum exploration duration reached; coverage is partial",
                        {"reason": "duration_limit", **coverage.context(observation)},
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
                    if outcome == "cancelled" or self.repository.is_cancel_requested(
                        exploration_id
                    ):
                        terminal = ExplorationStatus.CANCELLED
                        break
                    if outcome == "timeout":
                        terminal = ExplorationStatus.PARTIAL
                        self.repository.add_event(
                            exploration_id,
                            "warning",
                            "coverage_partial",
                            "Maximum exploration duration reached while waiting for login; coverage is partial",
                            {
                                "reason": "duration_limit",
                                **coverage.context(observation),
                            },
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
                    login_retried = False
                    executor.protect_authenticated_history()
                    if not authentication_boundary_crossed:
                        # The login document is an entry gate, not the parent
                        # of every authenticated screen. Start coverage at the
                        # first authenticated observation so logical
                        # backtracking can never target the sign-in page.
                        coverage = CoveragePlanner(observation.url, options.focus_area)
                        coverage.observe(observation)
                        authentication_boundary_crossed = True
                        self.repository.add_event(
                            exploration_id,
                            "info",
                            "authenticated_scope_started",
                            "Coverage root moved to the authenticated application",
                            {"url": observation.url},
                        )
                    self._record_observation_issues(
                        exploration_id, observation, known_issues
                    )
                planning_attempts += 1
                if planning_attempts > max_planning_attempts:
                    terminal = ExplorationStatus.PARTIAL
                    self.repository.add_event(
                        exploration_id,
                        "warning",
                        "coverage_partial",
                        "Exploration planning limit reached before the coverage frontier was exhausted",
                        coverage.context(observation),
                    )
                    break
                try:
                    decision = await asyncio.wait_for(
                        agent.decide(
                            observation,
                            history,
                            sorted(credentials.keys()),
                            coverage.context(observation),
                        ),
                        timeout=max(deadline - time.monotonic(), 0.1),
                    )
                    consecutive_model_errors = 0
                except ModelResponseError as exc:
                    await self._record_model_usage(
                        exploration_id, [agent_client], executor
                    )
                    consecutive_model_errors += 1
                    self.repository.add_event(
                        exploration_id,
                        "warning",
                        "invalid_agent_decision",
                        str(exc),
                    )
                    if consecutive_model_errors >= 3:
                        raise RuntimeError(
                            "JEV returned three invalid decisions in sequence"
                        ) from exc
                    continue
                await self._record_model_usage(exploration_id, [agent_client], executor)
                decision, coverage_override = coverage.reconcile(observation, decision)
                if coverage_override:
                    self.repository.add_event(
                        exploration_id,
                        "info",
                        "coverage_decision_overridden",
                        "Coverage planner replaced the model decision to continue systematic exploration",
                        {
                            "reason": coverage_override,
                            "action": decision.action,
                            "target": decision.target,
                            "url": decision.url,
                        },
                    )
                is_restoration_action = decision.action == "go_back" or bool(
                    coverage_override
                    and (
                        "restore" in coverage_override
                        or "backtrack" in coverage_override
                        or coverage_override == "focus_boundary_return"
                    )
                )
                if decision.action == "finish":
                    if not coverage.can_finish():
                        state = coverage.context(observation)
                        history.append(
                            {
                                "action": "finish",
                                "target": None,
                                "result": "rejected: coverage frontier or backtracking path is not exhausted",
                                "coverage_state": state,
                            }
                        )
                        self.repository.add_event(
                            exploration_id,
                            "warning",
                            "finish_rejected",
                            "Premature finish rejected; coverage frontier is not exhausted",
                            state,
                        )
                        await executor.update_live_panel(
                            f"Etapa {actions_executed + 1} de {options.max_steps}",
                            "JEV tentou encerrar cedo — retomando a cobertura.",
                        )
                        continue
                    if options.focus_area and not coverage.focus_reached:
                        terminal = ExplorationStatus.PARTIAL
                        self.repository.add_event(
                            exploration_id,
                            "warning",
                            "coverage_scope_not_found",
                            "The requested screen/menu scope was not found among reachable safe routes",
                            coverage.context(observation),
                        )
                        break
                    if not coverage.coverage_complete():
                        terminal = ExplorationStatus.PARTIAL
                        self.repository.add_event(
                            exploration_id,
                            "warning",
                            "coverage_partial",
                            "The reachable frontier settled with blocked controls; coverage is partial",
                            {
                                "reason": "blocked_candidates",
                                **coverage.context(observation),
                            },
                        )
                        break
                    terminal = ExplorationStatus.COMPLETED
                    self.repository.add_event(
                        exploration_id,
                        "info",
                        "coverage_complete",
                        "Coverage frontier exhausted; exploration complete",
                        {
                            "rationale": decision.rationale,
                            **coverage.context(observation),
                        },
                    )
                    break
                fingerprint = self._fingerprint(
                    observation.url,
                    decision,
                    coverage.state_key(observation),
                )
                action_counts[fingerprint] = action_counts.get(fingerprint, 0) + 1
                if action_counts[fingerprint] > 2 and not is_restoration_action:
                    consecutive_loop_rejections += 1
                    # Direciona o modelo para elementos ainda não tentados,
                    # em vez de só dizer "não" e desistir em seguida.
                    hint = (
                        f"rejected by loop protection: '{decision.action} on "
                        f"{decision.target}' was already tried "
                        f"{action_counts[fingerprint]}x on this page. You MUST "
                        "choose a DIFFERENT element."
                    )
                    untried = coverage.context(observation)["current_untried"][:6]
                    if untried:
                        hint += " Untried semantic options include: " + ", ".join(
                            str(item.get("name") or item.get("ref")) for item in untried
                        )
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
                        terminal = ExplorationStatus.PARTIAL
                        self.repository.add_event(
                            exploration_id,
                            "warning",
                            "coverage_partial",
                            "JEV kept repeating tried actions; coverage is partial",
                            coverage.context(observation),
                        )
                        break
                    continue
                self.repository.add_event(
                    exploration_id,
                    "info",
                    "action_started",
                    f"Executing {decision.action}",
                    {
                        "step": actions_executed + 1,
                        "action": decision.action,
                        "target": decision.target,
                        "rationale": decision.rationale,
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
                before_state_key = coverage.effect_key(observation)
                after_state_key = coverage.effect_key(next_observation)
                state_changed = before_state_key != after_state_key
                if (
                    decision.action == "go_back"
                    and result.success
                    and not state_changed
                ):
                    result.success = False
                    result.message = (
                        "Playwright attempted go_back, but no route or interactive-state "
                        "change was observed"
                    )
                elif (
                    decision.action in {"click", "hover"}
                    and result.success
                    and not state_changed
                ):
                    result.message = (
                        f"{result.message}; no new route or interactive-state change was observed"
                    )
                self._append_observable_state_evidence(
                    result,
                    decision,
                    next_observation,
                    state_changed=state_changed,
                    capture_console_errors=options.capture_console_errors,
                    capture_network_errors=options.capture_network_errors,
                )
                safe_decision = redact(asdict(decision), secret_values)
                safe_result = redact(result.to_dict(), secret_values)
                self.repository.add_action(
                    exploration_id, next_action, safe_decision, safe_result
                )
                actions_executed += 1
                coverage.record_action(observation, decision, result, next_observation)
                coverage_after = coverage.context(next_observation)
                consecutive_loop_rejections = 0
                self.repository.update_exploration(
                    exploration_id,
                    current_url=next_observation.url,
                    steps_completed=actions_executed,
                )
                self._record_screenshot(
                    exploration_id, next_observation, decision.action
                )
                self._record_observation_issues(
                    exploration_id, next_observation, known_issues
                )
                self.repository.add_event(
                    exploration_id,
                    "info" if result.success else "error",
                    "action_completed",
                    result.message,
                    {
                        "step": actions_executed,
                        "action": decision.action,
                        "success": result.success,
                        "url": result.after_url,
                        "state_changed": state_changed,
                        "coverage": {
                            "known_states": coverage_after["known_states"],
                            "frontier_states": coverage_after["frontier_states"],
                            "frontier_candidates": coverage_after[
                                "frontier_candidates"
                            ],
                            "candidates_attempted": coverage_after[
                                "candidates_attempted"
                            ],
                        },
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
                        "rationale": decision.rationale,
                        "success": result.success,
                        "result": result.message,
                        "before_url": result.before_url,
                        "after_url": result.after_url,
                        "state_changed": state_changed,
                        "next_page_title": next_observation.title,
                        "new_console_errors": len(next_observation.console_errors),
                        "new_network_errors": len(next_observation.network_errors),
                        "coverage_after": {
                            "known_states": coverage_after["known_states"],
                            "frontier_states": coverage_after["frontier_states"],
                            "frontier_candidates": coverage_after[
                                "frontier_candidates"
                            ],
                            "path_depth": coverage_after["path_depth"],
                        },
                        "assertions": [asdict(value) for value in result.assertions],
                    }
                )
                # Preserve evidence now and document it after navigation. The
                # auxiliary LLM never blocks or shortens browser exploration.
                if (
                    options.generate_test_cases
                    and not is_restoration_action
                    and fingerprint not in scenario_fingerprints
                ):
                    scenario_fingerprints.add(fingerprint)
                    scenario_number += 1
                    pending_scenarios.append(
                        (next_observation, decision, result, scenario_number)
                    )
                observation = next_observation
            else:
                terminal = ExplorationStatus.PARTIAL
                self.repository.add_event(
                    exploration_id,
                    "warning",
                    "limit_reached",
                    "Maximum number of actions reached; coverage is partial",
                    {"reason": "action_limit", **coverage.context(observation)},
                )
        except asyncio.CancelledError:
            terminal = ExplorationStatus.CANCELLED
            error_message = "Backend shutdown interrupted this exploration"
        except asyncio.TimeoutError:
            terminal = ExplorationStatus.PARTIAL
            self.repository.add_event(
                exploration_id,
                "warning",
                "coverage_partial",
                "Maximum exploration duration reached; coverage is partial",
                {
                    "reason": "duration_limit",
                    **(coverage.context(observation) if coverage else {}),
                },
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
                with suppress(Exception):
                    await executor.update_live_panel(
                        "Exploração concluída",
                        f"Status: {terminal.value} — veja o relatório no AutoJev.",
                    )
                await executor.close()
                executor = None
            self.input_events.pop(exploration_id, None)
            self.credential_vault.discard(exploration_id)
            finished = utc_now()
            needs_postprocessing = bool(
                (pending_scenarios and auxiliary and auxiliary_client)
                or (options and options.generate_html_report)
            )
            persisted_status = (
                ExplorationStatus.POSTPROCESSING
                if needs_postprocessing
                else terminal
            )
            # Browser work has ended. Keep a distinct live phase so polling,
            # cancellation and token/cost updates continue until the report is
            # actually available, without pretending the browser is running.
            self.repository.update_exploration(
                exploration_id,
                status=persisted_status,
                finished_at=finished,
                error=error_message,
            )
            if pending_scenarios and auxiliary and auxiliary_client:
                try:
                    await self._materialize_scenarios(
                        exploration_id,
                        pending_scenarios,
                        auxiliary,
                        auxiliary_client,
                        self._is_jev_model(auxiliary_model_id)
                        or terminal
                        in {ExplorationStatus.CANCELLED, ExplorationStatus.FAILED},
                    )
                except Exception as scenario_error:
                    self.repository.add_event(
                        exploration_id,
                        "error",
                        "scenario_postprocessing_failed",
                        str(redact(str(scenario_error), secret_values))[:1000],
                    )
            if (
                persisted_status == ExplorationStatus.POSTPROCESSING
                and self.repository.is_cancel_requested(exploration_id)
            ):
                terminal = ExplorationStatus.CANCELLED
            if options and options.generate_html_report:
                try:
                    self.repository.add_event(
                        exploration_id,
                        "info",
                        "report_generation_started",
                        "Casos de teste prontos; montando o relatório final",
                        {"current": 0, "total": 1, "stage": "report"},
                    )
                    exploration = self.repository.get_exploration(exploration_id)
                    # Render the durable terminal outcome rather than freezing
                    # the transient "postprocessing" phase into the report.
                    exploration["status"] = terminal.value
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
                        "Relatório final pronto para abrir",
                        {"current": 1, "total": 1, "stage": "report"},
                    )
                except Exception as report_error:
                    self.repository.add_event(
                        exploration_id,
                        "error",
                        "report_failed",
                        str(redact(str(report_error), secret_values))[:1000],
                    )
            if persisted_status == ExplorationStatus.POSTPROCESSING:
                self.repository.update_exploration(
                    exploration_id,
                    status=terminal,
                    finished_at=utc_now(),
                    error=error_message,
                )
