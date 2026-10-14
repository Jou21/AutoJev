"""One explicitly authorized, deterministic CT replay in a fresh browser.

This is deliberately not another exploration. No planner/provider is created,
no draft custom step is interpreted, and an absent historical recipe is never
replaced with a guessed action. The original run is read-only.
"""
from __future__ import annotations

import asyncio
import copy
import json
import time
from collections import Counter
from dataclasses import asdict
from typing import Any
from urllib.parse import urlsplit

from autojev.adapters.playwright_executor import BrowserUnavailableError, PlaywrightExecutor
from autojev.adapters.repository import NotFoundError
from autojev.adapters.secrets import redact
from autojev.domain.models import (
    ActionResult, AgentDecision, ExplorationOptions, ExplorationRequest,
    ExplorationStatus, ModelSelection, Observation, ScenarioStatus, TestScenario, utc_now,
)
from autojev.services.business_checks import check_business_outcomes
from autojev.services.exploration import ExplorationManager
from autojev.services.report_localization import check_label
from autojev.services.rerun_recipe import (
    RerunRecipeBlocked, recipe_effect_digest, resolve_rerun_decision, validate_rerun_recipe,
)


SOURCE_EVENT = "scenario_rerun_source"
STATE_EVENT = "scenario_rerun_state"
TERMINAL_STATUSES = {"completed", "partial", "failed", "cancelled"}
# These failures describe a page the user can still prepare, not authority to
# relax the recipe. Every retry requires a new explicit confirmation and the
# exact same resolver checks; policy/recipe failures remain terminal.
RECOVERABLE_PREPARATION_REASONS = {
    "before_url_changed", "before_effect_changed", "before_state_changed",
    "cross_origin_source", "missing_target", "ambiguous_target",
    "authentication_required", "unverifiable_field_state",
    "unverifiable_selection_state", "unbound_state_reference",
}


def rerun_block_reason(reason: str) -> str:
    """Human presentation only; the machine reason remains in evidence/state."""
    messages = {
        "before_url_changed": "A página aberta não é a mesma em que o teste original começou.",
        "before_effect_changed": "A página apresenta conteúdo ou controles diferentes do estado original.",
        "before_state_changed": "Os dados ou pré-requisitos da página estão diferentes do teste original.",
        "cross_origin_source": "A página aberta está fora do site autorizado para este teste.",
        "cross_origin_destination": "A ação levaria a outro site, fora da origem autorizada.",
        "invalid_source_url": "O endereço salvo para preparar o teste não é válido ou seguro.",
        "missing_target": "O controle que o teste usou não foi encontrado na página preparada.",
        "ambiguous_target": "Há mais de um controle correspondente; não é seguro escolher um deles automaticamente.",
        "unsupported_target": "O controle salvo não pode ser identificado com segurança nesta página.",
        "unsafe_target": "A política de segurança não permite interagir com esse controle.",
        "sensitive_target": "O controle contém dados sensíveis e não pode ser repetido automaticamente.",
        "authentication_required": "Conclua o login ou a etapa adicional de autenticação antes de repetir o teste.",
        "credential_action": "A ação original usa credenciais; essa etapa deve ser preparada manualmente.",
        "unverifiable_field_state": "Não foi possível confirmar que os campos têm o mesmo estado do teste original.",
        "unportable_field_state": "O estado original contém valores privados que não podem ser comparados entre sessões.",
        "unverifiable_selection_state": "Não foi possível confirmar a opção selecionada no controle original.",
        "unbound_state_reference": "Um controle ligado aos pré-requisitos originais não pôde ser identificado nesta página.",
        "preparation_timeout": "O prazo para preparar a página terminou sem uma confirmação para continuar.",
        "browser_closed": "O navegador foi fechado antes de concluir a reexecução.",
        "cancelled": "A reexecução foi cancelada pelo usuário.",
        "backend_shutdown": "O backend foi encerrado durante a reexecução.",
        "backend_restart_interrupted": "O backend reiniciou e não pode reconectar à sessão anterior.",
        "missing_original_checks": "Não foi possível repetir todas as conferências do teste original.",
        "unsupported_validation_probe": "A preparação necessária para esta validação de formulário ainda não pode ser reproduzida com segurança.",
        "unsupported_action": "A ação original ainda não possui suporte para reexecução segura.",
        "unsupported_assertion": "Uma conferência original ainda não pode ser reexecutada com segurança.",
        "unsafe_replay_value": "O valor exigido pela ação não atende à política de reexecução segura.",
        "sensitive_replay_value": "A ação usa um valor sensível que não pode ser armazenado para reexecução.",
        "non_deterministic_fill": "O preenchimento original não é um valor de teste determinístico autorizado.",
        "unsupported_selection": "A opção original não está entre as opções autorizadas do controle.",
        "sensitive_source_url": "O endereço original contém parâmetros sensíveis e não pode ser repetido automaticamente.",
        "sensitive_assertion": "Uma conferência original contém dados sensíveis e não pode ser repetida automaticamente.",
    }
    if reason in messages:
        return messages[reason]
    if reason.startswith("TimeoutError"):
        return "O tempo limite da operação terminou antes de confirmar o resultado."
    if reason in {"invalid_recipe", "invalid_decision", "invalid_target_fingerprint", "invalid_observation", "invalid_precondition_digest",
                  "missing_or_unsupported_recipe", "unsupported_recipe", "unsupported_key",
                  "unexpected_action_target", "unexpected_action_key", "unexpected_action_value", "unexpected_action_url"}:
        return "A receita original está incompleta ou não pode ser interpretada com segurança."
    return "Não foi possível concluir a reexecução. Consulte os detalhes técnicos da execução."


def _origin(url: Any) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(str(url))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None


def _latest_event(item: dict[str, Any], event_type: str) -> dict[str, Any] | None:
    return next((copy.deepcopy(event["payload"]) for event in reversed(item.get("events", []))
                 if event.get("type") == event_type and isinstance(event.get("payload"), dict)), None)


class ScenarioRerunService:
    def __init__(self, manager: Any, settings: Any) -> None:
        self.manager, self.repository, self.settings = manager, manager.repository, settings
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.input_events: dict[str, asyncio.Event] = {}
        self.states: dict[str, dict[str, Any]] = {}
        self.intervention_timeout_seconds = 300.0
        self.operation_timeout_seconds = 120.0
        # A restart cannot reconnect to the old browser or safely redispatch.
        # Reconcile recent unfinished reruns at startup, and all others on read.
        for item in self.repository.list_explorations(limit=10000):
            if item.get("status") not in TERMINAL_STATUSES:
                self.state_for(item["id"])

    def _source(self, exploration_id: str, scenario_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
        item = self.repository.get_exploration(exploration_id)
        scenario = next((s for s in item.get("scenarios", []) if s.get("id") == scenario_id), None)
        if scenario is None:
            raise NotFoundError(f"Scenario {scenario_id} was not found")
        evidence = scenario.get("evidence") or {}
        step = evidence.get("action_step")
        matching = [a for a in item.get("actions", []) if step is not None and a.get("step_number") == step]
        action = matching[0] if len(matching) == 1 else None
        # Recipes are private browser-dispatch records, never scenario/model
        # inputs. Bind to exactly one persisted action via its step number.
        recipe = ((((action or {}).get("result") or {}).get("evidence") or {}).get("rerun_recipe"))
        return item, scenario, action, recipe if isinstance(recipe, dict) else None

    def preflight(self, exploration_id: str, scenario_id: str) -> dict[str, Any]:
        item, scenario, action, recipe = self._source(exploration_id, scenario_id)
        evidence = scenario.get("evidence") or {}
        raw_assertions = evidence.get("assertions") or []
        assertions = raw_assertions if isinstance(raw_assertions, list) else []
        invalid_checks = not isinstance(raw_assertions, list) or any(
            not isinstance(a, dict) or not isinstance(a.get("kind"), str) or not a.get("kind") or "expected" not in a
            for a in assertions)
        local = [a for a in assertions if isinstance(a, dict) and a.get("kind") and a["kind"] != "visual_model"]
        visual = any(isinstance(a, dict) and a.get("kind") == "visual_model" for a in assertions)
        visual = visual or bool((evidence.get("visual_assessment") or {}).get("requested"))
        result = {
            "available": False, "code": None, "reason": None,
            "title": str(scenario.get("title") or scenario_id),
            "url": str(evidence.get("before_url") or scenario.get("url") or ""),
            "checks": [str(a["kind"]) for a in local],
            "scope": {"local_checks": True, "visual_review": False, "database_steps": False,
                      "missing_visual_checks": visual},
        }
        code, reason = None, None
        action_entries = [e for e in evidence.get("timeline", []) if isinstance(e, dict) and e.get("kind") == "action"]
        if str(item.get("status")) not in TERMINAL_STATUSES:
            code, reason = "source_running", "Aguarde o encerramento da execução original."
        elif len(action_entries) > 1:
            code, reason = "multiple_actions", "Este CT reúne várias ações; ainda não há uma receita completa para reexecutá-lo com segurança."
        elif not recipe:
            code, reason = "missing_recipe", "Este CT antigo não possui uma receita segura de reexecução. Execute uma nova exploração para capturá-la."
        elif recipe.get("version") != "ct-rerun-v1" or recipe.get("enabled") is not True:
            code, reason = "unsupported_recipe", "A ação deste CT não pode ser reproduzida com segurança pela receita salva."
        elif invalid_checks:
            code, reason = "invalid_checks", "As conferências originais deste CT estão incompletas e não podem ser comparadas com segurança."
        elif not local:
            code, reason = ("visual_only" if visual else "missing_checks"), "Este CT não possui conferências locais salvas que possam ser repetidas sem uma nova análise por modelo."
        elif (_origin(item.get("app_url")) is None or _origin(recipe.get("before_url")) != _origin(item.get("app_url"))
              or (evidence.get("before_url") and recipe.get("before_url") != evidence["before_url"])):
            code, reason = "unsafe_origin", "A página inicial da receita não corresponde à origem e ao estado autorizados."
        elif not isinstance(recipe.get("decision"), dict):
            code, reason = "unsupported_recipe", "A receita salva não contém uma ação determinística válida."
        if code:
            result.update(code=code, reason=reason)
        else:
            try:
                validate_rerun_recipe(recipe, item["app_url"])
            except RerunRecipeBlocked:
                result.update(code="invalid_recipe", reason="A receita salva está incompleta ou não atende à política de reexecução segura.")
                return result
            result.update(available=True, url=recipe["before_url"], code="ready",
                          reason="Prepare a página e o estado original no navegador antes de continuar."
                          + (" A análise visual anterior não será repetida; o novo CT ficará bloqueado nessa conferência." if visual else ""))
        return result

    def submit(self, exploration_id: str, scenario_id: str,
               credentials: dict[str, str] | None = None) -> str:
        preflight = self.preflight(exploration_id, scenario_id)
        if not preflight["available"]:
            raise ValueError(preflight["reason"])
        item, scenario, action, recipe = self._source(exploration_id, scenario_id)
        options_data = {key: value for key, value in (item.get("options") or {}).items()
                        if key in ExplorationOptions.__dataclass_fields__}
        options_data.update(mode="single_ct_rerun", max_steps=1, max_duration_seconds=420,
                            auxiliary_evidence_mode="text", auxiliary_max_requests=0,
                            generate_test_cases=True, generate_html_report=True)
        request = ExplorationRequest(item["app_url"], ModelSelection(**item["agent"]),
                                     ModelSelection(**item["auxiliary_llm"]), ExplorationOptions(**options_data))
        new_id = self.repository.create_exploration(request)
        self.manager.credential_vault.put(new_id, credentials or {})
        source = {"source_exploration_id": exploration_id, "source_scenario_id": scenario_id,
                  "scope": preflight["scope"], "manual_preparation_required": True,
                  "custom_steps_executed": False, "model_calls": False}
        self.repository.add_event(new_id, "info", SOURCE_EVENT,
                                  "Reexecução individual autorizada; a execução original permanece inalterada", source)
        self.states[new_id] = {**source, "exploration_id": new_id, "running": True, "phase": "queued"}
        self._persist(new_id)
        self.input_events[new_id] = asyncio.Event()
        task = asyncio.create_task(self._guarded_run(new_id, item, scenario, action, recipe))
        self.tasks[new_id] = task
        task.add_done_callback(lambda _task: self._forget(new_id))
        return new_id

    def _forget(self, exploration_id: str) -> None:
        self.tasks.pop(exploration_id, None)
        self.input_events.pop(exploration_id, None)
        self.manager.credential_vault.discard(exploration_id)

    def _persist(self, exploration_id: str, **changes: Any) -> None:
        self.states[exploration_id].update(changes)
        self.repository.add_event(exploration_id, "info", STATE_EVENT,
                                  "Estado da reexecução individual", copy.deepcopy(self.states[exploration_id]))

    def state_for(self, exploration_id: str) -> dict[str, Any]:
        item = self.repository.get_exploration(exploration_id)
        state = copy.deepcopy(self.states.get(exploration_id) or _latest_event(item, STATE_EVENT) or {})
        if state.get("running") and exploration_id not in self.tasks:
            state.update(running=False, phase="finished", reason="backend_restart_interrupted",
                         outcome="cancelled", finished_at=utc_now())
            self.states[exploration_id] = state
            self._persist(exploration_id)
            self.repository.update_exploration(exploration_id, status=ExplorationStatus.CANCELLED,
                                              finished_at=state["finished_at"],
                                              error="A reexecução foi interrompida pelo reinício do backend; nenhuma ação será repetida automaticamente.")
        result = copy.deepcopy(state)
        if result.get("reason"):
            # Presentation projection also covers historical machine codes.
            # Do not rewrite already preserved reports or terminal events.
            result["reason_label"] = rerun_block_reason(str(result["reason"]))
        return result

    def resume(self, exploration_id: str) -> None:
        self.repository.get_exploration(exploration_id, include_details=False)
        event = self.input_events.get(exploration_id)
        if event is not None and self.states.get(exploration_id, {}).get("phase") == "waiting_preparation":
            event.set()

    def cancel(self, exploration_id: str) -> None:
        self.repository.request_cancel(exploration_id)
        event = self.input_events.get(exploration_id)
        if event is not None:
            event.set()
        task = self.tasks.get(exploration_id)
        if task and not task.done():
            # Persist even if cancellation arrives before the first task turn.
            self._persist(exploration_id, running=False, phase="finished", reason="cancelled",
                          outcome="cancelled", finished_at=utc_now())
            self.repository.update_exploration(exploration_id, status=ExplorationStatus.CANCELLED,
                                              finished_at=utc_now())
            task.cancel()

    async def shutdown(self) -> None:
        tasks = list(self.tasks.values())
        for exploration_id, task in list(self.tasks.items()):
            self._persist(exploration_id, running=False, phase="finished", reason="backend_shutdown",
                          outcome="cancelled", finished_at=utc_now())
            self.repository.update_exploration(exploration_id, status=ExplorationStatus.CANCELLED,
                                              finished_at=utc_now(), error="Backend shutdown interrupted this rerun")
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _wait_for_preparation(self, exploration_id: str, executor: Any,
                                    deadline: float, preparation: dict[str, Any]) -> str:
        event = self.input_events[exploration_id]
        event.clear()
        self.repository.update_exploration(exploration_id, status=ExplorationStatus.WAITING_INPUT)
        expected_url, current_url = preparation["expected_url"], preparation["current_url"]
        message = ((preparation.get("reason_label") + " " if preparation.get("reason_label") else "")
                   + "Prepare a página original e recrie os pré-requisitos do CT (incluindo login, carrinho ou formulário). "
                   + f"Página esperada: {expected_url}. Página aberta: {current_url}. "
                   + "Nenhuma ação foi executada. Depois de corrigir a preparação, confirme novamente para continuar.")
        preparation = {**preparation, "instruction": message}
        self._persist(exploration_id, phase="waiting_preparation", preparation=preparation)
        self.repository.add_event(exploration_id, "warning", "waiting_input", message,
                                  {"kind": "scenario_rerun_preparation", "manual_preparation_required": True,
                                   "preparation": copy.deepcopy(preparation)})
        await executor.show_intervention_panel(message)
        app_resume = asyncio.create_task(event.wait())
        browser_resume = asyncio.create_task(executor.wait_for_user_input())
        browser_closed = asyncio.create_task(executor.wait_until_inactive())
        pending_tasks = {app_resume, browser_resume, browser_closed}
        try:
            # The caller supplies one absolute preparation deadline. An early
            # confirmation must never buy another full five-minute window.
            done, _ = await asyncio.wait(pending_tasks, timeout=max(0.0, deadline - time.monotonic()),
                                         return_when=asyncio.FIRST_COMPLETED)
            if self.repository.is_cancel_requested(exploration_id):
                return "cancelled"
            if not done:
                return "preparation_timeout"
            if browser_closed in done:
                return "browser_closed"
            for task in done:
                task.result()
            await executor.hide_intervention_panel()
            return "ready"
        finally:
            for task in pending_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending_tasks, return_exceptions=True)

    @staticmethod
    def _missing_checks(original: dict[str, Any], action: dict[str, Any] | None,
                        decision: AgentDecision, result: ActionResult) -> list[str]:
        original_assertions = (original.get("evidence") or {}).get("assertions") or []
        saved_direct = ((action or {}).get("decision") or {}).get("assertions") or []
        # element_visible uses volatile executor refs. Only exact saved direct
        # assertions may be rebound, using the recipe's semantic target match.
        rebound = {}
        for old, new in zip(saved_direct, decision.assertions):
            if old.get("kind") == new.get("kind") == "element_visible":
                rebound[str(old.get("expected"))] = new.get("expected")
        def key(kind: str, expected: Any) -> str:
            return json.dumps([kind, expected], sort_keys=True, ensure_ascii=False, default=str)
        available = Counter(key(a.kind, a.expected) for a in result.assertions)
        missing = []
        for assertion in original_assertions:
            kind = assertion.get("kind")
            expected = assertion.get("expected")
            if kind == "element_visible" and str(expected) in rebound:
                expected = rebound[str(expected)]
            identity = key(kind, expected)
            if kind == "visual_model" or available[identity] <= 0:
                missing.append(str(kind))
            else:
                available[identity] -= 1
        if ((original.get("evidence") or {}).get("visual_assessment") or {}).get("requested"):
            if "visual_model" not in missing:
                missing.append("visual_model")
        return missing

    def _blocked_scenario(self, exploration_id: str, original: dict[str, Any],
                          recipe: dict[str, Any], reason: str, observation: Observation | None,
                          *, dispatched: bool = False) -> None:
        source = self.states[exploration_id]
        explanation = rerun_block_reason(reason)
        actual = (f"A ação foi iniciada, mas o resultado não pôde ser confirmado. {explanation}" if dispatched
                  else f"Ação não executada. {explanation}")
        scenario = TestScenario(
            id="CT001", title=str(original.get("title") or "Reexecução individual"),
            description=("A reexecução foi interrompida depois de iniciar a ação; seu efeito não está confirmado." if dispatched
                         else "A reexecução não despachou a ação original porque seus pré-requisitos não foram confirmados."),
            preconditions=list(original.get("preconditions") or []), steps=list(original.get("steps") or []),
            expected_result=str(original.get("expected_result") or "Repetir as conferências originais."),
            actual_result=actual, status=ScenarioStatus.BLOCKED,
            url=observation.url if observation else str(recipe.get("before_url") or ""), created_at=utc_now(),
            screenshots=[observation.screenshot] if observation and observation.screenshot else [],
            evidence={"source_exploration_id": source["source_exploration_id"],
                      "source_scenario_id": source["source_scenario_id"], "rerun_scope": source["scope"],
                      "action_dispatched": dispatched, "rerun_blocked_reason": reason,
                      "rerun_blocked_explanation": explanation,
                      "assertions": [], "timeline": [{"kind": "observation", "title": "Reexecução bloqueada",
                          "message": actual, "occurred_at": utc_now()}]},
        )
        self.repository.add_scenario(exploration_id, 1, scenario)

    async def _guarded_run(self, exploration_id: str, source: dict[str, Any],
                           original: dict[str, Any], action: dict[str, Any] | None, recipe: dict[str, Any]) -> None:
        try:
            # Keep the shared browser slot through cleanup/report generation,
            # not just through dispatch. A closing browser is still active.
            async with self.manager.semaphore:
                await self._run(exploration_id, source, original, action, recipe)
        except asyncio.CancelledError:
            if self.states[exploration_id].get("running"):
                self._persist(exploration_id, running=False, phase="finished", reason="cancelled",
                              outcome="cancelled", finished_at=utc_now())
                self.repository.update_exploration(exploration_id, status=ExplorationStatus.CANCELLED,
                                                  finished_at=utc_now())

    async def _run(self, exploration_id: str, source: dict[str, Any],
                   original: dict[str, Any], action: dict[str, Any] | None, recipe: dict[str, Any]) -> None:
        executor = None
        observation = None
        dispatched = False
        scenario_saved = False
        terminal, reason = ExplorationStatus.PARTIAL, None
        secrets = list(self.manager.credential_vault.get(exploration_id).values())
        try:
            async with asyncio.timeout(420):
                if self.repository.is_cancel_requested(exploration_id):
                    terminal, reason = ExplorationStatus.CANCELLED, "cancelled"
                    return
                item = self.repository.get_exploration(exploration_id, include_details=False)
                options = ExplorationOptions(**item["options"])
                deadline = time.monotonic() + (options.max_duration_seconds or 420)
                def operation_timeout() -> float:
                    return min(self.operation_timeout_seconds, max(0.1, deadline - time.monotonic()))
                executor = PlaywrightExecutor(self.settings.artifact_dir / exploration_id / "screenshots", options,
                                               headless=False, browser_name=self.settings.browser, secret_values=secrets)
                self.repository.update_exploration(exploration_id, status=ExplorationStatus.RUNNING, started_at=utc_now())
                self._persist(exploration_id, phase="preparing")
                observation = await asyncio.wait_for(executor.start(recipe["before_url"]), operation_timeout())
                self.repository.update_exploration(exploration_id, current_url=observation.url)
                preparation_deadline = min(deadline, time.monotonic() + self.intervention_timeout_seconds)
                attempts, version = 0, 0
                preparation_reason = "before_url_changed" if observation.url != recipe["before_url"] else None
                decision = None
                while decision is None:
                    version += 1
                    preparation = {
                        "version": version, "attempts": attempts,
                        "expected_url": recipe["before_url"], "current_url": observation.url,
                        "reason_code": preparation_reason,
                        "reason_label": rerun_block_reason(preparation_reason) if preparation_reason else None,
                    }
                    reason = await self._wait_for_preparation(exploration_id, executor, preparation_deadline, preparation)
                    if reason != "ready":
                        break
                    attempts += 1
                    self._persist(exploration_id, phase="validating_preparation",
                                  preparation={**self.states[exploration_id]["preparation"], "attempts": attempts})
                    # Always read again after the user's changes. Correcting a
                    # page by itself is not a confirmation and never dispatches.
                    remaining = preparation_deadline - time.monotonic()
                    if remaining <= 0:
                        reason = "preparation_timeout"
                        break
                    try:
                        observation = await asyncio.wait_for(executor.observe("rerun_before"), min(operation_timeout(), remaining))
                    except asyncio.TimeoutError:
                        if time.monotonic() >= preparation_deadline:
                            reason = "preparation_timeout"
                            break
                        raise
                    self.repository.update_exploration(exploration_id, current_url=observation.url)
                    try:
                        decision = resolve_rerun_decision(recipe, observation, source["app_url"])
                    except RerunRecipeBlocked as exc:
                        preparation_reason = str(exc)
                        if preparation_reason not in RECOVERABLE_PREPARATION_REASONS:
                            raise
                        # Keep the browser open so login redirects, an incorrect
                        # cart, or a premature click can be corrected. Nothing is
                        # saved as a CT and no original action has been started.
                        continue
                    self._persist(exploration_id, preparation={
                        **self.states[exploration_id]["preparation"], "current_url": observation.url,
                        "reason_code": None, "reason_label": None,
                    })
                if reason != "ready":
                    if reason in {"cancelled", "browser_closed"}:
                        terminal = ExplorationStatus.CANCELLED
                    else:
                        self._blocked_scenario(exploration_id, original, recipe, reason, observation)
                    return
                self.repository.update_exploration(exploration_id, status=ExplorationStatus.RUNNING, current_url=observation.url)
                self._persist(exploration_id, phase="executing")
                dispatched = True
                result, after = await asyncio.wait_for(executor.execute(decision, {}), operation_timeout())
                result.assertions.extend(check_business_outcomes(observation, decision, result, after))
                state_changed = recipe_effect_digest(observation) != recipe_effect_digest(after)
                result.evidence["action_effect"] = {"state_changed": state_changed}
                issues = ExplorationManager._issues_relevant_to_action(after, result.started_at, result.finished_at)
                ExplorationManager._append_observable_state_evidence(
                    result, decision, after, state_changed=state_changed,
                    capture_console_errors=options.capture_console_errors,
                    capture_network_errors=options.capture_network_errors, new_issues=issues)
                missing = self._missing_checks(original, action, decision, result)
                result.evidence.update(
                    action_step=1, before_screenshot=observation.screenshot,
                    before_observed_at=observation.captured_at,
                    source_exploration_id=source["id"], source_scenario_id=original["id"],
                    rerun_scope=copy.deepcopy(self.states[exploration_id]["scope"]),
                    action_dispatched=True, missing_original_checks=missing,
                    visual_assessment={"requested": False, "status": "disabled", "reason": "single_ct_rerun_local_only"})
                scenario = ExplorationManager._scenario_from_evidence(after, decision, result, 1)
                scenario.title = str(original.get("title") or scenario.title)
                if missing:
                    # Missing evidence limits completeness, but cannot erase a
                    # concrete failed local check or failed browser execution.
                    if scenario.status != ScenarioStatus.FAIL:
                        scenario.status = ScenarioStatus.BLOCKED
                    scenario.actual_result += "\nConferências originais não reproduzidas: " + ", ".join(check_label(kind) for kind in missing)
                    reason = "missing_original_checks"
                else:
                    reason = None
                safe_result = redact(result.to_dict(), secrets)
                # Hashes are not secrets; redact only browser evidence, then
                # attach the private recipe without corrupting a short hash
                # substring that happens to equal a supplied credential.
                safe_result["evidence"]["rerun_recipe"] = copy.deepcopy(recipe)
                self.repository.add_action(exploration_id, 1, redact(asdict(decision), secrets), safe_result)
                safe_scenario = redact(asdict(scenario), secrets)
                safe_scenario["status"] = ScenarioStatus(safe_scenario["status"])
                self.repository.add_scenario(exploration_id, 1, TestScenario(**safe_scenario))
                scenario_saved = True
                for shot, label in [(observation.screenshot, "Antes da reexecução"), (after.screenshot, "Depois da reexecução")]:
                    if shot:
                        self.repository.add_screenshot(exploration_id, shot, label, scenario.id)
                self.repository.update_exploration(exploration_id, current_url=after.url, steps_completed=1)
                terminal = ExplorationStatus.PARTIAL if missing or scenario.status in {ScenarioStatus.BLOCKED, ScenarioStatus.SKIPPED} else ExplorationStatus.COMPLETED
        except RerunRecipeBlocked as exc:
            reason = str(exc)
            self._blocked_scenario(exploration_id, original, recipe, reason, observation, dispatched=dispatched)
        except asyncio.CancelledError:
            terminal = ExplorationStatus.CANCELLED
            reason = self.states[exploration_id].get("reason") or "cancelled"
            if dispatched and not scenario_saved:
                self._blocked_scenario(exploration_id, original, recipe, reason, observation, dispatched=True)
        except BrowserUnavailableError:
            terminal, reason = ExplorationStatus.CANCELLED, "browser_closed"
            if dispatched and not scenario_saved:
                self._blocked_scenario(exploration_id, original, recipe, reason, observation, dispatched=True)
        except Exception as exc:
            reason = str(redact(f"{type(exc).__name__}: {exc}", secrets))[:1000]
            terminal = ExplorationStatus.PARTIAL
            self._blocked_scenario(exploration_id, original, recipe, reason, observation, dispatched=dispatched)
            self.repository.add_event(exploration_id, "error", "scenario_rerun_failed", reason)
        finally:
            if executor is not None:
                try:
                    await asyncio.wait_for(executor.close(), 15)
                except Exception:
                    pass
            if self.repository.is_cancel_requested(exploration_id):
                terminal, reason = ExplorationStatus.CANCELLED, "cancelled"
            self.repository.update_exploration(exploration_id, status=terminal, finished_at=utc_now(), error=reason)
            self._persist(exploration_id, running=False, phase="finished", reason=reason,
                          outcome=str(terminal), finished_at=utc_now())
            self.manager.credential_vault.discard(exploration_id)
            try:
                item = self.repository.get_exploration(exploration_id)
                report_path = self.settings.artifact_dir / exploration_id / "report.html"
                generated = await asyncio.to_thread(self.manager.report_renderer.generate, item, str(report_path))
                self.repository.update_exploration(exploration_id, report_path=generated)
                self.repository.add_event(exploration_id, "info", "report_ready", "Relatório da reexecução individual pronto")
            except Exception as exc:
                self.repository.add_event(exploration_id, "error", "report_failed", str(redact(str(exc), secrets))[:1000])
