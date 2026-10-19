"""One explicitly authorized, deterministic CT replay in a fresh browser.

This is deliberately not another exploration. No planner/provider is created;
only an allowlisted manual screenshot checkpoint plan is admitted. Captures
require confirmation through the trusted AutoJev application or its isolated
browser panel, never an authority exposed to the tested page. Integration drafts
are never executed and an absent historical recipe is never replaced with a
guessed action. The original run is read-only.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import json
import math
import re
import time
import uuid
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from autojev.adapters.playwright_executor import (
    BrowserUnavailableError,
    LoginAttempt,
    PlaywrightExecutor,
)
from autojev.adapters.repository import NotFoundError
from autojev.adapters.secrets import redact
from autojev.domain.models import (
    ActionResult,
    AgentDecision,
    ExplorationOptions,
    ExplorationRequest,
    ExplorationStatus,
    ModelSelection,
    Observation,
    ScenarioStatus,
    TestScenario,
    utc_now,
)
from autojev.services.business_checks import check_business_outcomes
from autojev.services.exploration import ExplorationManager
from autojev.services.report_localization import check_label
from autojev.services.rerun_initial_observation import (
    INITIAL_OBSERVATION_EVENT,
    initial_observation_from_events,
    with_initial_observation,
)
from autojev.services.rerun_recipe import (
    RerunRecipeBlocked,
    recipe_effect_digest,
    resolve_rerun_decision,
    validate_rerun_recipe,
)
from autojev.services.rerun_step_progress import (
    build_step_plan,
    progress_check_bindings,
    project_step_progress,
)
from autojev.services.scenario_step_order import (
    capture_position_anchor,
    presentation_scenario_steps,
)
from autojev.services.scenario_timeline import prepare_scenario_timelines
from autojev.services.user_image_upload import ValidatedUserImage, validate_user_image
from autojev.services.rerun_preparation_presentation import preparation_presentation

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
        "capture_timeout": "O prazo para anexar a imagem terminou. Nenhuma imagem foi adicionada nem houve continuação automática.",
        "capture_failed": "Não foi possível concluir a inclusão da imagem. A reexecução não continuou automaticamente.",
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
        self.capture_events: dict[str, asyncio.Event] = {}
        self.capture_records: dict[str, list[dict[str, Any]]] = {}
        self.pending_images: dict[str, dict[str, Any]] = {}
        # Unconfirmed images exist only in memory, scoped to one live pause.
        # Never include these bytes in durable state/events or AI payloads.
        self.capture_previews: dict[str, dict[str, Any]] = {}
        # The temporary shared checkbox never submits anything by itself.
        # It gates passive native-login continuation only together with an
        # observed completed login and all original precondition guards.
        self.preparation_consents: dict[str, dict[str, Any]] = {}
        self.states: dict[str, dict[str, Any]] = {}
        self.intervention_timeout_seconds = 300.0
        self.manual_login_poll_seconds = 0.75
        self.manual_login_observation_timeout_seconds = 5.0
        self.operation_timeout_seconds = 120.0
        self.capture_timeout_seconds = 900.0
        self.capture_total_timeout_seconds = 3600.0
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
        # Auxiliary screenshot interpretation is not itself an assertion.
        # Omit it honestly in a local-only replay, without inventing a missing
        # mandatory check. Explicit visual_model assertions still remain due.
        visual_review_omitted = visual or bool((evidence.get("visual_assessment") or {}).get("requested"))
        result = {
            "available": False, "code": None, "reason": None,
            "title": str(scenario.get("title") or scenario_id),
            "url": str(evidence.get("before_url") or scenario.get("url") or ""),
            "checks": [str(a["kind"]) for a in local],
            "scope": {"local_checks": True, "visual_review": False, "database_steps": False,
                      "missing_visual_checks": visual, "visual_review_omitted": visual_review_omitted},
        }
        timeline = self._source_timeline(item, scenario_id)
        action_positions = [index for index, entry in enumerate(timeline) if entry.get("kind") == "action"]
        result.update(timeline_count=len(timeline), action_index=action_positions[0] if len(action_positions) == 1 else None)
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
            code, reason = ("visual_only" if visual_review_omitted else "missing_checks"), "Este CT não possui conferências locais salvas que possam ser repetidas sem uma nova análise por modelo."
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
                          + (" A conferência visual original não será repetida; o novo CT ficará bloqueado nessa conferência." if visual else
                             " A revisão visual auxiliar não será repetida. O resultado será das conferências locais executadas, sem aprovação visual." if visual_review_omitted else ""))
        return result

    @staticmethod
    def _source_timeline(item: dict[str, Any], scenario_id: str) -> list[dict[str, Any]]:
        prepared = prepare_scenario_timelines(item)
        scenario = next((entry for entry in prepared.get("scenarios", []) if entry.get("id") == scenario_id), {})
        # A rerun of a rerun uses the very same roteiro order as its report
        # editor. Its uploaded images must not shift the next numeric gap just
        # because their real attachment timestamps appear later in the log.
        return presentation_scenario_steps(scenario.get("evidence") or {})

    @staticmethod
    def _capture_plan(plan: Any, timeline_count: int, action_index: int | None) -> dict[str, Any]:
        if plan is None:
            return {"version": 1, "checkpoints": []}
        if (not isinstance(plan, dict) or set(plan) != {"version", "checkpoints"}
                or type(plan["version"]) is not int or plan["version"] != 1
                or not isinstance(plan["checkpoints"], list) or len(plan["checkpoints"]) > 100):
            raise ValueError("O plano de capturas não é válido.")
        checkpoints, ids, positions = [], set(), set()
        for checkpoint in plan["checkpoints"]:
            if not isinstance(checkpoint, dict) or set(checkpoint) != {"id", "gap", "order"}:
                raise ValueError("O plano de capturas contém um passo não autorizado.")
            identity, gap, order = checkpoint["id"], checkpoint["gap"], checkpoint["order"]
            if (not isinstance(identity, str) or not re.fullmatch(r"db-[0-9]{1,9}", identity)
                    or type(gap) is not int or not 0 <= gap <= min(10000, timeline_count)
                    or type(order) is not int or not 0 <= order <= 10000
                    or identity in ids or (gap, order) in positions or action_index is None):
                raise ValueError("A posição de uma captura não corresponde aos passos do teste original.")
            ids.add(identity)
            positions.add((gap, order))
            checkpoints.append({"id": identity, "gap": gap, "order": order})
        return {"version": 1, "checkpoints": sorted(checkpoints, key=lambda checkpoint: (checkpoint["gap"], checkpoint["order"]))}

    def submit(self, exploration_id: str, scenario_id: str,
               credentials: dict[str, str] | None = None, capture_plan: dict[str, Any] | None = None) -> str:
        preflight = self.preflight(exploration_id, scenario_id)
        if not preflight["available"]:
            raise ValueError(preflight["reason"])
        item, scenario, action, recipe = self._source(exploration_id, scenario_id)
        capture_plan = self._capture_plan(capture_plan, preflight["timeline_count"], preflight["action_index"])
        source_steps = self._source_timeline(item, scenario_id)
        position_anchors = {
            checkpoint["id"]: capture_position_anchor(source_steps, checkpoint["gap"], scenario.get("evidence") or {})
            for checkpoint in capture_plan["checkpoints"]
        }
        options_data = {key: value for key, value in (item.get("options") or {}).items()
                        if key in ExplorationOptions.__dataclass_fields__}
        options_data.update(mode="single_ct_rerun", max_steps=1, max_duration_seconds=420,
                            auxiliary_evidence_mode="text", auxiliary_max_requests=0,
                            generate_test_cases=True, generate_html_report=True)
        if capture_plan["checkpoints"]:
            options_data["capture_screenshots"] = True
        request = ExplorationRequest(item["app_url"], ModelSelection(**item["agent"]),
                                     ModelSelection(**item["auxiliary_llm"]), ExplorationOptions(**options_data))
        new_id = self.repository.create_exploration(request)
        self.manager.credential_vault.put(new_id, credentials or {})
        source = {"source_exploration_id": exploration_id, "source_scenario_id": scenario_id,
                  "scope": preflight["scope"], "manual_preparation_required": True,
                  "custom_steps_executed": False, "model_calls": False,
                  "capture_plan": capture_plan, "capture_count": len(capture_plan["checkpoints"]),
                  "captures_completed": 0, "capture_action_index": preflight["action_index"],
                  "capture_position_anchors": position_anchors,
                  "step_plan": build_step_plan(source_steps, capture_plan, scenario.get("evidence") or {})}
        self.repository.add_event(new_id, "info", SOURCE_EVENT,
                                  "Reexecução individual autorizada; a execução original permanece inalterada", source)
        self.states[new_id] = {**source, "exploration_id": new_id, "running": True, "phase": "queued"}
        self._persist(new_id)
        self.input_events[new_id] = asyncio.Event()
        self.capture_events[new_id] = asyncio.Event()
        self.capture_records[new_id] = []
        task = asyncio.create_task(self._guarded_run(new_id, item, scenario, action, recipe))
        self.tasks[new_id] = task
        task.add_done_callback(lambda _task: self._forget(new_id))
        return new_id

    def _forget(self, exploration_id: str) -> None:
        self.tasks.pop(exploration_id, None)
        self.input_events.pop(exploration_id, None)
        self.capture_events.pop(exploration_id, None)
        self.capture_records.pop(exploration_id, None)
        self.capture_previews.pop(exploration_id, None)
        self.preparation_consents.pop(exploration_id, None)
        pending_image = self.pending_images.pop(exploration_id, None)
        if pending_image is not None:
            # Only this generated, never committed file is eligible for
            # cleanup; never touch a user-supplied path or existing evidence.
            self._uploaded_image_path(exploration_id, pending_image["screenshot"]).unlink(missing_ok=True)
        self.manager.credential_vault.discard(exploration_id)

    def _persist(self, exploration_id: str, **changes: Any) -> None:
        self.states[exploration_id].update(changes)
        preview = self.capture_previews.get(exploration_id)
        state = self.states[exploration_id]
        checkpoint = state.get("capture_checkpoint") or {}
        if preview is not None and (state.get("running") is not True or state.get("phase") != "waiting_capture"
                                    or (preview["checkpoint_id"], preview["checkpoint_version"]) !=
                                    (checkpoint.get("id"), checkpoint.get("version"))):
            self.capture_previews.pop(exploration_id, None)
        consent = self.preparation_consents.get(exploration_id)
        if consent is not None and (state.get("running") is not True or state.get("phase") != "waiting_preparation"
                                    or consent["preparation_version"] != (state.get("preparation") or {}).get("version")):
            self.preparation_consents.pop(exploration_id, None)
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
        if isinstance(result.get("preparation"), dict):
            result["preparation"]["consent"] = self._optional_preparation_consent(exploration_id, result["preparation"].get("version"))
            result["preparation"]["presentation"] = preparation_presentation(result["preparation"], result.get("phase"))
        return result

    def _require_preparation_consent(self, exploration_id: str, preparation_version: int) -> None:
        self.repository.get_exploration(exploration_id, include_details=False)
        state = self.states.get(exploration_id) or {}
        event = self.input_events.get(exploration_id)
        if (event is None or event.is_set() or state.get("running") is not True
                or state.get("phase") != "waiting_preparation" or type(preparation_version) is not int
                or (state.get("preparation") or {}).get("version") != preparation_version
                or self.repository.is_cancel_requested(exploration_id)):
            raise ValueError("Esta preparação não está aguardando confirmação ou foi atualizada. Confira o painel e tente novamente.")

    def preparation_consent_for(self, exploration_id: str, preparation_version: int) -> dict[str, Any]:
        """Read only this current preparation's temporary shared checkbox."""
        self._require_preparation_consent(exploration_id, preparation_version)
        consent = self.preparation_consents.get(exploration_id)
        if consent is None or consent["preparation_version"] != preparation_version:
            consent = {"preparation_version": preparation_version, "revision": 0, "checked": False}
            self.preparation_consents[exploration_id] = consent
        return copy.deepcopy(consent)

    def _optional_preparation_consent(self, exploration_id: str, preparation_version: Any) -> dict[str, Any] | None:
        try:
            return self.preparation_consent_for(exploration_id, preparation_version)
        except (NotFoundError, ValueError):
            return None

    def set_preparation_consent(self, exploration_id: str, preparation_version: int,
                                expected_revision: int, checked: bool) -> dict[str, Any]:
        """Synchronize UI selection only; never set the continuation event."""
        current = self.preparation_consent_for(exploration_id, preparation_version)
        if type(expected_revision) is not int or expected_revision < 0 or type(checked) is not bool:
            raise ValueError("A seleção da preparação não é válida. Confira o painel e tente novamente.")
        if current["revision"] != expected_revision:
            raise ValueError("A preparação foi alterada em outra janela. Confira a seleção atual e tente novamente.")
        current.update(revision=expected_revision + 1, checked=checked)
        self.preparation_consents[exploration_id] = current
        return copy.deepcopy(current)

    def resume(self, exploration_id: str) -> None:
        self.repository.get_exploration(exploration_id, include_details=False)
        event = self.input_events.get(exploration_id)
        if event is not None and self.states.get(exploration_id, {}).get("phase") == "waiting_preparation":
            event.set()

    async def _resume_from_browser_panel(self, exploration_id: str, preparation_version: int) -> dict[str, Any]:
        """Confirm only the current preparation, using the durable app gate.

        A panel snapshot can be half a second behind the service. It must not
        resume a newer retry or lose an early click when a browser-side waiter
        later clears its generic event. This check and the app event set have
        no await between them and therefore form one event-loop transaction.
        """
        state = self.states.get(exploration_id) or {}
        preparation = state.get("preparation") or {}
        event = self.input_events.get(exploration_id)
        if (event is None or event.is_set() or state.get("running") is not True
                or state.get("phase") != "waiting_preparation"
                or type(preparation_version) is not int or preparation.get("version") != preparation_version
                or self.repository.is_cancel_requested(exploration_id)):
            raise ValueError("Esta preparação não está aguardando confirmação ou foi atualizada. Confira o painel e tente novamente.")
        self.resume(exploration_id)
        return {"accepted": True, "preparation_version": preparation_version}

    def _browser_panel_payload(self, exploration_id: str, secrets: list[str]) -> dict[str, Any]:
        """Only the live roteiro and fixed presentation text cross into the panel.

        Do not forward a saved state/event wholesale: it contains the private
        source recipe and can grow to include browser evidence or attachments.
        Verdicts come only from evidence of this replay, never the original CT.
        """
        item = self.repository.get_exploration(exploration_id)
        state = self.states.get(exploration_id) or {}
        projected = project_step_progress(item, state, state.get("step_plan"))

        def text(value: Any, limit: int = 1000) -> str:
            return str(redact(str(value or ""), secrets))[:limit]

        def number(value: Any, *, maximum: int = 10000) -> int:
            return min(maximum, max(0, value)) if type(value) is int else 0

        steps = []
        for row in projected.get("steps", [])[:10000]:
            if not isinstance(row, dict):
                continue
            steps.append({
                "id": str(row.get("id") or "")[:120],
                "title": text(row.get("title"), 240),
                "kind": row.get("kind") if row.get("kind") in {"observation", "action", "assertion", "signal", "capture_wait"} else "observation",
                "state": row.get("state") if row.get("state") in {"pending", "active", "waiting", "done", "failed", "blocked", "skipped", "unavailable"} else "pending",
                "label": text(row.get("label"), 120),
                "description": text(row.get("description")),
                **({"checkpoint_id": str(row["checkpoint_id"])[:120]} if isinstance(row.get("checkpoint_id"), str) else {}),
                **({"source_index": number(row["source_index"])} if type(row.get("source_index")) is int else {}),
            })
        checkpoint = state.get("capture_checkpoint") or {}
        active_checkpoint = None
        if (isinstance(checkpoint, dict) and isinstance(checkpoint.get("id"), str)
                and all(type(checkpoint.get(key)) is int for key in ("gap", "order", "version"))):
            active_checkpoint = {"id": checkpoint["id"][:120], **{
                key: number(checkpoint[key]) for key in ("gap", "order", "version")}}
        preparation = state.get("preparation") or {}
        login = preparation.get("login_attempt") or {}
        safe_preparation = {"version": number(preparation.get("version")),
                            **{key: text(preparation.get(key), 2000) for key in (
                                "reason_label", "expected_url", "current_url", "instruction")},
                            "reason_code": (preparation.get("reason_code")
                                            if preparation.get("reason_code") in RECOVERABLE_PREPARATION_REASONS else None)}
        if isinstance(login, dict) and login:
            safe_preparation["login_attempt"] = {
                "status": login.get("status") if login.get("status") in {"success", "rejected", "manual_required"} else "manual_required",
                "reason_label": text(login.get("reason_label")),
            }
        manual_login = preparation.get("manual_login")
        if isinstance(manual_login, dict) and manual_login.get("status") in {"waiting", "ready", "blocked"}:
            safe_preparation["manual_login"] = {
                "status": manual_login["status"],
                "reason_code": (manual_login.get("reason_code")
                                if manual_login.get("reason_code") in RECOVERABLE_PREPARATION_REASONS else None),
            }
        safe_preparation["consent"] = self._optional_preparation_consent(exploration_id, preparation.get("version"))
        safe_preparation["presentation"] = preparation_presentation(safe_preparation, state.get("phase"))
        usage = item.get("usage") or {}
        cost = usage.get("cost")
        cost = float(cost) if type(cost) in {int, float} and math.isfinite(cost) and cost >= 0 else None
        timeout = state.get("capture_timeout_seconds")
        timeout = float(timeout) if type(timeout) in {int, float} and math.isfinite(timeout) and timeout >= 0 else 0
        return {
            "version": 1, "exploration_id": exploration_id, "scenario_id": "CT001",
            "phase": state.get("phase") if state.get("phase") in {
                "queued", "preparing", "waiting_preparation", "validating_preparation", "executing", "waiting_capture", "capturing_print", "finished"} else "queued",
            "running": state.get("running") is True,
            "progress": {"steps": steps, "completed": number(projected.get("completed")), "total": len(steps),
                         "current_id": str(projected.get("current_id") or "")[:120], "notice": text(projected.get("notice"))},
            "checkpoint": active_checkpoint, "preparation": safe_preparation,
            "capture_count": number(state.get("capture_count"), maximum=100),
            "captures_completed": number(state.get("captures_completed"), maximum=100),
            "capture_timeout_seconds": min(timeout, self.capture_total_timeout_seconds),
            "usage": {"total_tokens": number(usage.get("total_tokens"), maximum=1_000_000_000),
                      "cost": cost, "cost_known": usage.get("cost_known") is True},
        }

    async def _stream_browser_panel(self, exploration_id: str, executor: Any, secrets: list[str]) -> None:
        """Refresh safely after page navigation without changing saved evidence.

        The executor owns frame-identity/payload deduplication: it must still be
        called on unchanged data to restore its isolated frame after a reload.
        A presentation failure cannot dispatch, accept a print, or abort a CT.
        """
        update = getattr(executor, "update_rerun_panel", None)
        if not callable(update):
            return
        update_preview = getattr(executor, "update_rerun_preview", None)
        last_preview_key = None
        while True:
            try:
                if callable(update_preview):
                    state = self.states.get(exploration_id) or {}
                    checkpoint = state.get("capture_checkpoint") or {}
                    if state.get("phase") == "waiting_capture" and state.get("running") is True:
                        known_revision = (last_preview_key[2] if last_preview_key is not None and
                                          last_preview_key[:2] == (checkpoint.get("id"), checkpoint.get("version")) else None)
                        snapshot = self.preview_for(exploration_id, checkpoint.get("id"), checkpoint.get("version"), known_revision)
                    else:
                        snapshot = {"checkpoint_id": None, "checkpoint_version": None,
                                    "revision": 0, "changed": True, "image": None}
                    preview_key = (snapshot["checkpoint_id"], snapshot["checkpoint_version"], snapshot["revision"])
                    if preview_key != last_preview_key:
                        await update_preview(snapshot)
                        last_preview_key = preview_key
                await update(self._browser_panel_payload(exploration_id, secrets))
            except asyncio.CancelledError:
                raise
            except Exception:
                # The application upload remains available if the browser
                # closes or a page prevents this optional embedded surface.
                pass
            await asyncio.sleep(0.5)

    def _store_uploaded_image(self, exploration_id: str, image: ValidatedUserImage) -> dict[str, Any]:
        """Write only a generated filename in this new run's artifact directory."""
        artifact_root = self.settings.artifact_dir.resolve()
        expected_directory = artifact_root / exploration_id / "screenshots"
        directory = expected_directory.resolve()
        if directory != expected_directory or directory.parent.parent != artifact_root:
            raise ValueError("O destino desta imagem não é válido.")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"user-upload-{uuid.uuid4().hex}{image.extension}"
        created = False
        try:
            # Exclusive creation prevents replacement of any existing evidence.
            with path.open("xb") as destination:
                created = True
                destination.write(image.content)
                destination.flush()
        except Exception:
            if created:
                path.unlink(missing_ok=True)
            raise
        return {"screenshot": path.name, "attachment": {
            "mime_type": image.mime_type, "size_bytes": len(image.content),
            "width": image.width, "height": image.height, "sha256": image.sha256,
        }}

    def _uploaded_image_path(self, exploration_id: str, filename: str) -> Path:
        if not isinstance(filename, str) or not re.fullmatch(r"user-upload-[0-9a-f]{32}\.(?:png|jpg|webp)", filename):
            raise ValueError("O nome da imagem anexada não é válido.")
        artifact_root = self.settings.artifact_dir.resolve()
        path = artifact_root / exploration_id / "screenshots" / filename
        if path.resolve() != path or path.parent.parent.parent != artifact_root:
            raise ValueError("O destino desta imagem não é válido.")
        return path

    def _require_capture_checkpoint(self, exploration_id: str, checkpoint_id: str,
                                    checkpoint_version: int) -> dict[str, Any]:
        """A synchronous, authoritative gate shared by preview and acceptance."""
        self.repository.get_exploration(exploration_id, include_details=False)
        state = self.states.get(exploration_id) or {}
        checkpoint = state.get("capture_checkpoint") or {}
        event = self.capture_events.get(exploration_id)
        if (event is None or state.get("running") is not True or state.get("phase") != "waiting_capture"
                or checkpoint.get("id") != checkpoint_id or type(checkpoint_version) is not int
                or checkpoint.get("version") != checkpoint_version
                or self.repository.is_cancel_requested(exploration_id)):
            raise ValueError("Esta captura não está aguardando confirmação ou já foi solicitada. Atualize a execução.")
        return state

    def preview_for(self, exploration_id: str, checkpoint_id: str, checkpoint_version: int,
                    known_revision: int | None = None) -> dict[str, Any]:
        """Read only the current ephemeral preview, never evidence or state."""
        self._require_capture_checkpoint(exploration_id, checkpoint_id, checkpoint_version)
        if known_revision is not None and (type(known_revision) is not int or known_revision < 0):
            raise ValueError("A revisão da prévia não é válida. Atualize a execução.")
        preview = self.capture_previews.get(exploration_id)
        if preview is None or (preview["checkpoint_id"], preview["checkpoint_version"]) != (checkpoint_id, checkpoint_version):
            preview = {"checkpoint_id": checkpoint_id, "checkpoint_version": checkpoint_version,
                       "revision": 0, "image": None}
            self.capture_previews[exploration_id] = preview
        changed = known_revision != preview["revision"]
        result = {"checkpoint_id": checkpoint_id, "checkpoint_version": checkpoint_version,
                  "revision": preview["revision"], "changed": changed}
        if changed:
            image = preview["image"]
            result["image"] = ({"data_url": f"data:{image.mime_type};base64,{base64.b64encode(image.content).decode('ascii')}",
                                "name": "Print sincronizado", "size": len(image.content)} if image is not None else None)
        return result

    async def preview_from_browser_panel(self, exploration_id: str, checkpoint_id: str, checkpoint_version: int,
                                         expected_revision: int, image_data_url: str | None) -> dict[str, Any]:
        """Stage/clear a validated image without attachment or continuation.

        CAS prevents a delayed validation or another window from overwriting a
        newer choice. State is checked both before and after worker decoding.
        """
        self._require_capture_checkpoint(exploration_id, checkpoint_id, checkpoint_version)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("A revisão da prévia não é válida. Atualize a execução.")
        initial = self.preview_for(exploration_id, checkpoint_id, checkpoint_version, expected_revision)
        if initial["changed"]:
            raise ValueError("O print foi alterado em outra janela. Confira a prévia atual e tente novamente.")
        image = await asyncio.to_thread(validate_user_image, image_data_url) if image_data_url is not None else None
        self._require_capture_checkpoint(exploration_id, checkpoint_id, checkpoint_version)
        preview = self.capture_previews.get(exploration_id)
        if (preview is None or preview["checkpoint_id"] != checkpoint_id or preview["checkpoint_version"] != checkpoint_version
                or preview["revision"] != expected_revision):
            raise ValueError("O print foi alterado em outra janela. Confira a prévia atual e tente novamente.")
        self.capture_previews[exploration_id] = {"checkpoint_id": checkpoint_id, "checkpoint_version": checkpoint_version,
                                               "revision": expected_revision + 1, "image": image}
        return self.preview_for(exploration_id, checkpoint_id, checkpoint_version)

    def capture_print(self, exploration_id: str, checkpoint_id: str, checkpoint_version: int,
                      image_data_url: str | ValidatedUserImage) -> dict[str, Any]:
        """Accept one explicit attachment; untrusted page code cannot confirm it."""
        state = self._require_capture_checkpoint(exploration_id, checkpoint_id, checkpoint_version)
        checkpoint = state["capture_checkpoint"]
        event = self.capture_events[exploration_id]
        # Both the HTTP route and the isolated browser-panel callback validate
        # in a worker thread, then return here to recheck the live checkpoint.
        # Direct service clients are validated too.
        image = image_data_url if isinstance(image_data_url, ValidatedUserImage) else validate_user_image(image_data_url)
        upload = self._store_uploaded_image(exploration_id, image)
        attached_at = utc_now()
        entry = {
            "id": f"capture-{checkpoint_id}", "kind": "observation", "title": "Imagem anexada pelo usuário",
            "message": "Imagem fornecida por você neste passo. Pode mostrar qualquer conteúdo; não é uma captura automática da página nem uma aprovação do teste.",
            "occurred_at": attached_at, "attached_at": attached_at,
            "image_source": "user_upload", "source": "user_upload", **upload,
            "checkpoint_id": checkpoint_id, "planned_gap": checkpoint["gap"], "planned_order": checkpoint["order"],
            "capture_phase": "before_action" if checkpoint["gap"] <= state["capture_action_index"] else "after_action",
        }
        anchor = (state.get("capture_position_anchors") or {}).get(checkpoint_id)
        if anchor is not None:
            entry["position_anchor"] = copy.deepcopy(anchor)
        original_state = copy.deepcopy(state)
        original_preview = self.capture_previews.get(exploration_id)
        try:
            # Synchronous phase change precedes the event: concurrent/double
            # clicks cannot overwrite this image or consume the next checkpoint.
            self.pending_images[exploration_id] = entry
            self._persist(exploration_id, phase="capturing_print")
        except Exception:
            self.pending_images.pop(exploration_id, None)
            self.states[exploration_id] = original_state
            if original_preview is not None:
                self.capture_previews[exploration_id] = original_preview
            self._uploaded_image_path(exploration_id, upload["screenshot"]).unlink(missing_ok=True)
            raise
        event.set()
        return copy.deepcopy(self.states[exploration_id])

    async def _capture_from_browser_panel(self, exploration_id: str, checkpoint_id: str,
                                          checkpoint_version: int, image_data_url: str) -> dict[str, Any]:
        """Use the same upload transaction after a trusted-frame confirmation.

        The executor checks isolated frame identity, a private rotating token
        and a real user confirmation. Rechecking state in capture_print after
        decoding also rejects cancellation, duplicates, or a stale checkpoint
        that raced image validation. No image bytes are logged or sent to AI.
        """
        image = await asyncio.to_thread(validate_user_image, image_data_url)
        self.capture_print(exploration_id, checkpoint_id, checkpoint_version, image)
        return {"accepted": True, "checkpoint_id": checkpoint_id, "checkpoint_version": checkpoint_version}

    def _commit_uploaded_image(self, exploration_id: str) -> dict[str, Any] | None:
        """Attach an accepted image even if cancellation races its wake-up event."""
        entry = self.pending_images.get(exploration_id)
        if entry is None:
            return None
        records = self.capture_records[exploration_id]
        if not any(item.get("id") == entry["id"] for item in records):
            self.repository.add_user_image_evidence(exploration_id, entry, "CT001")
            records.append(entry)
        self.states[exploration_id]["captures_completed"] = len(records)
        self.pending_images.pop(exploration_id, None)
        return entry

    def cancel(self, exploration_id: str) -> None:
        self.repository.request_cancel(exploration_id)
        self.capture_previews.pop(exploration_id, None)
        self.preparation_consents.pop(exploration_id, None)
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
        self.capture_previews.clear()
        self.preparation_consents.clear()
        tasks = list(self.tasks.values())
        for exploration_id, task in list(self.tasks.items()):
            self._persist(exploration_id, running=False, phase="finished", reason="backend_shutdown",
                          outcome="cancelled", finished_at=utc_now())
            self.repository.update_exploration(exploration_id, status=ExplorationStatus.CANCELLED,
                                              finished_at=utc_now(), error="Backend shutdown interrupted this rerun")
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _preparation_reason(observation: Observation, expected_url: str,
                            fallback: str | None = None) -> str | None:
        """Recognize the observed login before reporting a same-site redirect.

        This is presentation of a prerequisite, not permission to submit a
        form. A foreign origin retains its safety warning and every attempt
        still needs the existing explicit user confirmation and resolver.
        """
        current_origin = _origin(observation.url)
        if current_origin is None or current_origin != _origin(expected_url):
            return "cross_origin_source"
        if ExplorationManager._needs_login(observation):
            return "authentication_required"
        return fallback or ("before_url_changed" if observation.url != expected_url else None)

    async def _watch_manual_login(self, exploration_id: str, executor: Any, deadline: float,
                                 preparation_version: int, recipe: dict[str, Any], app_url: str) -> str:
        """Observe native-site login without submitting or photographing it.

        A completed login alone is not authorization. The current temporary
        checkbox and all original guards must hold together; the main path
        still takes a fresh observation before any original action dispatch.
        """
        observe = executor.observe_preparation
        while time.monotonic() < deadline:
            state = self.states.get(exploration_id) or {}
            preparation = state.get("preparation") or {}
            event = self.input_events.get(exploration_id)
            if (state.get("running") is not True or state.get("phase") != "waiting_preparation"
                    or preparation.get("version") != preparation_version or event is None or event.is_set()
                    or self.repository.is_cancel_requested(exploration_id)):
                return "superseded"
            try:
                observation = await asyncio.wait_for(observe(), min(self.manual_login_observation_timeout_seconds,
                                                                   max(0.001, deadline - time.monotonic())))
                reason = self._preparation_reason(observation, recipe["before_url"])
                if reason is None:
                    try:
                        resolve_rerun_decision(recipe, observation, app_url)
                    except RerunRecipeBlocked as exc:
                        reason = str(exc)
                status = "waiting" if reason == "authentication_required" else "blocked" if reason else "ready"
                # Observation may yield while a new preparation/continue/cancel
                # wins. Never publish or authorize against that stale read.
                self._require_preparation_consent(exploration_id, preparation_version)
                current = self.states[exploration_id]["preparation"]
                manual_login = {"status": status, "reason_code": reason}
                if current.get("manual_login") != manual_login or current.get("current_url") != observation.url:
                    label = ("Você está na tela de login. Preencha usuário e senha para continuar."
                             if reason == "authentication_required" else rerun_block_reason(reason) if reason else None)
                    updated = {**current, "manual_login": manual_login, "reason_code": reason,
                               "reason_label": label, "current_url": observation.url}
                    updated["instruction"] = preparation_presentation(updated, "waiting_preparation")["instruction"]
                    self._persist(exploration_id, preparation=updated)
                    self.repository.update_exploration(exploration_id, current_url=observation.url)
                if reason is None and self.preparation_consent_for(exploration_id, preparation_version)["checked"] is True:
                    return "manual_login_ready"
            except asyncio.CancelledError:
                raise
            except BrowserUnavailableError:
                return "browser_closed"
            except Exception:
                # A transient navigation/DOM read is not proof of login, and
                # cannot skip the finite preparation deadline or confirm it.
                pass
            await asyncio.sleep(min(self.manual_login_poll_seconds, max(0, deadline - time.monotonic())))
        return "preparation_timeout"

    async def _wait_for_preparation(self, exploration_id: str, executor: Any,
                                    deadline: float, preparation: dict[str, Any], *,
                                    recipe: dict[str, Any] | None = None, app_url: str | None = None) -> str:
        event = self.input_events[exploration_id]
        event.clear()
        self.repository.update_exploration(exploration_id, status=ExplorationStatus.WAITING_INPUT)
        expected_url, current_url = preparation["expected_url"], preparation["current_url"]
        login_feedback = preparation.get("login_attempt") or {}
        presentation = preparation_presentation(preparation, "waiting_preparation")
        message = ((login_feedback.get("reason_label") + " " if login_feedback else "")
                   + (preparation.get("reason_label") + " " if preparation.get("reason_label") else "")
                   + presentation["instruction"] + " "
                   + f"Página esperada: {expected_url}. Página aberta: {current_url}. "
                   + "Nenhuma ação do CT foi executada. " + presentation["note"])
        preparation = {**preparation, "instruction": message}
        self._persist(exploration_id, phase="waiting_preparation", preparation=preparation)
        self.repository.add_event(exploration_id, "warning", "waiting_input", message,
                                  {"kind": "scenario_rerun_preparation", "manual_preparation_required": True,
                                   "preparation": copy.deepcopy(preparation)})
        await executor.show_intervention_panel(message, title=presentation["title"], button_label=presentation["button_label"])
        app_resume = asyncio.create_task(event.wait())
        browser_resume = asyncio.create_task(executor.wait_for_user_input())
        browser_closed = asyncio.create_task(executor.wait_until_inactive())
        pending_tasks = {app_resume, browser_resume, browser_closed}
        manual_login = None
        if (preparation.get("reason_code") == "authentication_required" and recipe is not None and app_url
                and callable(getattr(executor, "observe_preparation", None))):
            manual_login = asyncio.create_task(self._watch_manual_login(
                exploration_id, executor, deadline, preparation["version"], recipe, app_url))
            pending_tasks.add(manual_login)
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
            # An explicit continue wins any race with the passive observer.
            if app_resume not in done and browser_resume not in done and not event.is_set() and manual_login in done:
                result = manual_login.result()
                if result != "manual_login_ready":
                    return "browser_closed" if result == "browser_closed" else "preparation_timeout"
                await executor.hide_intervention_panel()
                return result
            for task in done:
                task.result()
            await executor.hide_intervention_panel()
            return "ready"
        finally:
            for task in pending_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending_tasks, return_exceptions=True)

    async def _wait_for_capture(self, exploration_id: str, executor: Any,
                                checkpoint: dict[str, Any], remaining: float,
                                capture_phase: str) -> str:
        event = self.capture_events[exploration_id]
        event.clear()
        version = self.states[exploration_id].get("captures_completed", 0) + 1
        active = {**checkpoint, "version": version}
        timeout = min(self.capture_timeout_seconds, remaining)
        upload_surface = ("Use o painel AutoJev deste navegador ou a aplicação AutoJev para colar uma imagem com Ctrl+V, "
                          if callable(getattr(executor, "update_rerun_panel", None)) else
                          "Volte à aplicação AutoJev para colar uma imagem com Ctrl+V, ")
        message = ("A reexecução está pausada neste passo. " + upload_surface +
                   "arrastar um arquivo ou escolhê-lo no computador. A imagem pode mostrar qualquer conteúdo. "
                   "Depois, clique em Anexar print e continuar. Não há captura automática da página e nenhuma ação do teste será repetida. "
                   f"Prazo restante para anexar: {max(0, int(timeout))} segundos.")
        self.repository.update_exploration(exploration_id, status=ExplorationStatus.WAITING_INPUT)
        self._persist(exploration_id, phase="waiting_capture", capture_checkpoint=active,
                      capture_timeout_seconds=max(0, timeout), capture_wait_started_at=utc_now(),
                      capture_instruction=message)
        self.repository.add_event(exploration_id, "info", "waiting_capture", message, active)

        app_capture = asyncio.create_task(event.wait())
        passive_panel = asyncio.create_task(executor.wait_for_capture_confirmation(message))
        browser_closed = asyncio.create_task(executor.wait_until_inactive())
        pending_tasks = {app_capture, passive_panel, browser_closed}
        try:
            done, _ = await asyncio.wait(pending_tasks, timeout=max(0, timeout), return_when=asyncio.FIRST_COMPLETED)
            if self.repository.is_cancel_requested(exploration_id):
                return "cancelled"
            if not done:
                return "capture_timeout"
            if browser_closed in done:
                return "browser_closed"
            for task in done:
                task.result()
            # Untrusted tested-page code has no capture authority. The generic
            # preparation resume channel can never authorize this checkpoint.
            if app_capture not in done or self.states[exploration_id].get("phase") != "capturing_print" or not event.is_set():
                return "capture_failed"
            # Stop wait-panel restoration. The image comes from the user,
            # never an automatic screenshot of the browser under test.
            if not passive_panel.done():
                passive_panel.cancel()
            await asyncio.gather(passive_panel, return_exceptions=True)
            await executor.hide_intervention_panel()
            entry = self._commit_uploaded_image(exploration_id)
            if entry is None or not entry.get("screenshot"):
                return "capture_failed"
            self.repository.update_exploration(exploration_id, status=ExplorationStatus.RUNNING)
            self._persist(exploration_id, phase="preparing" if capture_phase == "before_action" else "executing",
                          captures_completed=version, capture_checkpoint=None)
            return "ready"
        finally:
            for task in pending_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending_tasks, return_exceptions=True)

    def _record_initial_observation(self, exploration_id: str, observation: Observation,
                                    reached_gap: int, secrets: list[str]) -> bool:
        """Complete the real initial record when its roteiro position is reached.

        A pause before step one must not complete that step. Conversely, a
        pause after the initial observation must not wait for action dispatch
        to publish evidence already obtained during validated preparation.
        Subsequent guard observations never replace this immutable record.
        """
        state = self.states[exploration_id]
        item = self.repository.get_exploration(exploration_id)
        if initial_observation_from_events(item) is not None:
            return True
        rows = [row for row in (state.get("step_plan") or {}).get("steps", [])
                if row.get("kind") == "observation"
                and (row.get("identity") or {}).get("role") == "before_action"]
        if (len(rows) != 1 or (rows[0].get("identity") or {}).get("total") != 1
                or type(rows[0].get("source_index")) is not int
                or rows[0]["source_index"] >= reached_gap
                or self.repository.is_cancel_requested(exploration_id)):
            return False
        row = rows[0]
        entry = {
            "id": f"rerun-{row['id']}-before", "source_step_id": row["id"],
            "kind": "observation", "title": "Antes da ação",
            "occurred_at": observation.captured_at,
            "url": redact(observation.url, secrets),
            "screenshot": Path(observation.screenshot).name if observation.screenshot else None,
            "message": "Estado inicial registrado antes de continuar o roteiro.",
        }
        self.repository.add_event(exploration_id, "info", INITIAL_OBSERVATION_EVENT,
                                  "Estado inicial da reexecução registrado",
                                  {"scenario_id": "CT001", "entry": entry})
        if entry["screenshot"]:
            self.repository.add_screenshot(exploration_id, entry["screenshot"], "Antes da reexecução", "CT001")
        return True

    def _apply_captures(self, exploration_id: str, scenario: TestScenario) -> None:
        initial = initial_observation_from_events(self.repository.get_exploration(exploration_id))
        if initial is not None:
            previous_before = scenario.evidence.get("before_screenshot")
            scenario.evidence = with_initial_observation(scenario.evidence, initial)
            # Revalidation remains in raw action evidence and explicit metadata,
            # not as a second 'Antes da ação' step that would break print anchors.
            represented = {entry.get("screenshot") for entry in scenario.evidence.get("timeline", [])}
            represented.update((entry.get("action_preview") or {}).get("screenshot")
                               for entry in scenario.evidence.get("timeline", [])
                               if isinstance(entry.get("action_preview"), dict))
            scenario.screenshots = [shot for shot in scenario.screenshots
                                    if shot != previous_before or shot in represented]
            if initial.get("screenshot"):
                scenario.screenshots = list(dict.fromkeys([initial["screenshot"], *scenario.screenshots]))
        captures = copy.deepcopy(self.capture_records.get(exploration_id) or [])
        scenario.evidence["capture_plan"] = copy.deepcopy(self.states[exploration_id].get("capture_plan"))
        scenario.evidence["manual_captures"] = captures
        original_entries = [entry for entry in scenario.evidence.get("timeline", [])
                            if not str(entry.get("id", "")).startswith("capture-db-")]
        # Actual capture times still establish the evidence log's chronology.
        # presentation_scenario_steps separately projects the selected roteiro
        # slot for report/editor/API consumers. Checks remain the one read-only
        # post-action batch, never rerun following a manual print.
        scenario.evidence["timeline"] = sorted([*original_entries, *captures], key=lambda entry: str(entry.get("occurred_at") or ""))
        scenario.screenshots = list(dict.fromkeys([*scenario.screenshots, *(entry["screenshot"] for entry in captures)]))

    def _save_capture_scenario(self, exploration_id: str, scenario: TestScenario, secrets: list[str]) -> None:
        self._apply_captures(exploration_id, scenario)
        safe_scenario = redact(asdict(scenario), secrets)
        safe_scenario["status"] = ScenarioStatus(safe_scenario["status"])
        self.repository.add_scenario(exploration_id, 1, TestScenario(**safe_scenario))

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
        self._apply_captures(exploration_id, scenario)
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
        browser_panel_task = None
        observation = None
        dispatched = False
        scenario_saved = False
        scenario = None
        initial_recorded = False
        capture_wait_total = 0.0
        terminal, reason = ExplorationStatus.PARTIAL, None
        secrets = list(self.manager.credential_vault.get(exploration_id).values())
        try:
            # Manual print waits have their own finite budget. They must not
            # consume the original operation/preparation budget, nor make the
            # browser immortal if requests never arrive.
            manual_budget = self.capture_total_timeout_seconds if self.states[exploration_id].get("capture_count") else 0
            async with asyncio.timeout(420 + manual_budget):
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
                configure_panel = getattr(executor, "configure_rerun_panel", None)
                if callable(configure_panel):
                    async def capture_handler(checkpoint_id: str, checkpoint_version: int,
                                              image_data_url: str) -> dict[str, Any]:
                        return await self._capture_from_browser_panel(
                            exploration_id, checkpoint_id, checkpoint_version, image_data_url)
                    configure_panel(capture_handler)
                configure_preview = getattr(executor, "configure_rerun_preview", None)
                if callable(configure_preview):
                    async def preview_handler(checkpoint_id: str, checkpoint_version: int,
                                              expected_revision: int, image_data_url: str | None) -> dict[str, Any]:
                        return await self.preview_from_browser_panel(
                            exploration_id, checkpoint_id, checkpoint_version, expected_revision, image_data_url)
                    configure_preview(preview_handler)
                configure_resume = getattr(executor, "configure_rerun_resume", None)
                if callable(configure_resume):
                    async def resume_handler(preparation_version: int) -> dict[str, Any]:
                        return await self._resume_from_browser_panel(exploration_id, preparation_version)
                    configure_resume(resume_handler)
                configure_consent = getattr(executor, "configure_rerun_consent", None)
                if callable(configure_consent):
                    async def consent_handler(preparation_version: int, expected_revision: int,
                                              checked: bool) -> dict[str, Any]:
                        return self.set_preparation_consent(exploration_id, preparation_version, expected_revision, checked)
                    configure_consent(consent_handler)
                self.repository.update_exploration(exploration_id, status=ExplorationStatus.RUNNING, started_at=utc_now())
                self._persist(exploration_id, phase="preparing")
                observation = await asyncio.wait_for(executor.start(recipe["before_url"]), operation_timeout())
                if callable(getattr(executor, "update_rerun_panel", None)):
                    browser_panel_task = asyncio.create_task(self._stream_browser_panel(exploration_id, executor, secrets))
                self.repository.update_exploration(exploration_id, current_url=observation.url)
                preparation_deadline = min(deadline, time.monotonic() + self.intervention_timeout_seconds)
                attempts, version = 0, 0
                login_feedback = None
                preparation_reason = self._preparation_reason(observation, recipe["before_url"])
                decision = None
                while decision is None:
                    version += 1
                    preparation = {
                        "version": version, "attempts": attempts,
                        "expected_url": recipe["before_url"], "current_url": observation.url,
                        "reason_code": preparation_reason,
                        "reason_label": ("Você está na tela de login. Preencha usuário e senha para continuar."
                                         if preparation_reason == "authentication_required" else
                                         rerun_block_reason(preparation_reason) if preparation_reason else None),
                        **({"login_attempt": login_feedback} if login_feedback else {}),
                    }
                    reason = await self._wait_for_preparation(exploration_id, executor, preparation_deadline, preparation,
                                                              recipe=recipe, app_url=source["app_url"])
                    automatic_preparation = reason == "manual_login_ready"
                    if reason not in {"ready", "manual_login_ready"}:
                        break
                    reason = "ready"
                    if automatic_preparation:
                        try:
                            authorized = self.preparation_consent_for(exploration_id, version)["checked"] is True
                        except ValueError:
                            # A real explicit continue may win while the panel
                            # hides. It is not an automatic login submission.
                            if self.input_events[exploration_id].is_set():
                                automatic_preparation = False
                                authorized = True
                            else:
                                authorized = False
                        if not authorized:
                            continue
                    attempts += 1
                    self._persist(exploration_id, phase="validating_preparation",
                                  preparation={**self.states[exploration_id]["preparation"], "attempts": attempts})
                    # Always read again after user changes. Passive login
                    # completion needs current authorization plus fresh guards;
                    # other preparation still requires explicit continuation.
                    remaining = preparation_deadline - time.monotonic()
                    if remaining <= 0:
                        reason = "preparation_timeout"
                        break
                    try:
                        observation = await asyncio.wait_for(executor.observe("rerun_before"), min(operation_timeout(), remaining))
                        if automatic_preparation and ExplorationManager._needs_login(observation):
                            preparation_reason = self._preparation_reason(observation, recipe["before_url"])
                            continue
                        if (_origin(observation.url) == _origin(recipe["before_url"])
                                and ExplorationManager._needs_login(observation)):
                            await executor.show_intervention_panel(
                                "Tentando entrar com os campos que você preencheu. Aguarde; nenhuma ação do CT foi executada.",
                                title="Entrando e conferindo a preparação", button_label="Conferindo…", button_disabled=True)
                            self.repository.add_event(exploration_id, "info", "confirmed_login_started",
                                "Tentando entrar com os campos preenchidos após confirmação do usuário")
                            try:
                                login = await asyncio.wait_for(executor.attempt_filled_login(observation),
                                    min(operation_timeout(), max(0.1, preparation_deadline - time.monotonic())))
                            except asyncio.TimeoutError:
                                login = LoginAttempt("manual_required", "login_not_confirmed")
                            login_feedback = ExplorationManager._filled_login_feedback(login)
                            self._persist(exploration_id, preparation={
                                **self.states[exploration_id]["preparation"], "login_attempt": login_feedback})
                            self.repository.add_event(exploration_id,
                                "info" if login_feedback["status"] == "success" else "warning", "confirmed_login_result",
                                login_feedback["reason_label"], login_feedback)
                            await executor.hide_intervention_panel()
                            remaining = preparation_deadline - time.monotonic()
                            if remaining <= 0:
                                reason = "preparation_timeout"
                                break
                            observation = await asyncio.wait_for(executor.observe("rerun_after_confirmed_login"),
                                min(operation_timeout(), remaining))
                    except asyncio.TimeoutError:
                        if time.monotonic() >= preparation_deadline:
                            reason = "preparation_timeout"
                            break
                        raise
                    self.repository.update_exploration(exploration_id, current_url=observation.url)
                    try:
                        decision = resolve_rerun_decision(recipe, observation, source["app_url"])
                    except RerunRecipeBlocked as exc:
                        blocked_reason = str(exc)
                        if blocked_reason not in RECOVERABLE_PREPARATION_REASONS:
                            raise
                        preparation_reason = self._preparation_reason(observation, recipe["before_url"], blocked_reason)
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
                checkpoints = self.states[exploration_id]["capture_plan"]["checkpoints"]
                action_index = self.states[exploration_id]["capture_action_index"]
                for checkpoint in (entry for entry in checkpoints if entry["gap"] <= action_index):
                    initial_recorded = self._record_initial_observation(
                        exploration_id, observation, checkpoint["gap"], secrets) or initial_recorded
                    wait_started = time.monotonic()
                    reason = await self._wait_for_capture(exploration_id, executor, checkpoint,
                        self.capture_total_timeout_seconds - capture_wait_total, "before_action")
                    waited = time.monotonic() - wait_started
                    capture_wait_total += waited
                    deadline += waited
                    if reason != "ready":
                        terminal = ExplorationStatus.CANCELLED if reason in {"cancelled", "browser_closed"} else ExplorationStatus.PARTIAL
                        self._blocked_scenario(exploration_id, original, recipe, reason, observation)
                        return
                    # Manual preparation may change the page while paused.
                    # Bind fresh handles and enforce every original guard
                    # again before the one and only action dispatch.
                    observation = await asyncio.wait_for(executor.observe("rerun_before_manual_print"), operation_timeout())
                    decision = resolve_rerun_decision(recipe, observation, source["app_url"])
                initial_recorded = self._record_initial_observation(
                    exploration_id, observation, action_index, secrets) or initial_recorded
                if time.monotonic() >= deadline:
                    raise asyncio.TimeoutError("Original operation budget expired before action dispatch")
                self.repository.update_exploration(exploration_id, status=ExplorationStatus.RUNNING, current_url=observation.url)
                self._persist(exploration_id, phase="executing",
                              step_check_bindings=progress_check_bindings(action, decision.assertions))
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
                if self.states[exploration_id]["scope"].get("visual_review_omitted"):
                    scenario.actual_result += "\nA revisão visual da execução original não foi repetida. Este resultado não representa aprovação visual."
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
                self._save_capture_scenario(exploration_id, scenario, secrets)
                scenario_saved = True
                for shot, label in [(None if initial_recorded else observation.screenshot, "Antes da reexecução"),
                                    (after.screenshot, "Depois da reexecução")]:
                    if shot:
                        self.repository.add_screenshot(exploration_id, shot, label, scenario.id)
                self.repository.update_exploration(exploration_id, current_url=after.url, steps_completed=1)
                terminal = ExplorationStatus.PARTIAL if missing or scenario.status in {ScenarioStatus.BLOCKED, ScenarioStatus.SKIPPED} else ExplorationStatus.COMPLETED
                # The action and its local assertions are already durable.
                # A cancelled/timed-out manual print cannot erase or redispatch
                # that observed result. It only limits capture completeness.
                checks_reason = reason
                for checkpoint in (entry for entry in checkpoints if entry["gap"] > action_index):
                    wait_started = time.monotonic()
                    reason = await self._wait_for_capture(exploration_id, executor, checkpoint,
                        self.capture_total_timeout_seconds - capture_wait_total, "after_action")
                    waited = time.monotonic() - wait_started
                    capture_wait_total += waited
                    deadline += waited
                    self._save_capture_scenario(exploration_id, scenario, secrets)
                    if reason != "ready":
                        terminal = ExplorationStatus.CANCELLED if reason in {"cancelled", "browser_closed"} else ExplorationStatus.PARTIAL
                        return
                reason = checks_reason
        except RerunRecipeBlocked as exc:
            reason = str(exc)
            if not scenario_saved:
                self._blocked_scenario(exploration_id, original, recipe, reason, observation, dispatched=dispatched)
        except asyncio.CancelledError:
            terminal = ExplorationStatus.CANCELLED
            reason = self.states[exploration_id].get("reason") or "cancelled"
            if not scenario_saved and (dispatched or self.capture_records.get(exploration_id) or initial_recorded):
                self._blocked_scenario(exploration_id, original, recipe, reason, observation, dispatched=dispatched)
        except BrowserUnavailableError:
            terminal, reason = ExplorationStatus.CANCELLED, "browser_closed"
            if not scenario_saved and (dispatched or self.capture_records.get(exploration_id) or initial_recorded):
                self._blocked_scenario(exploration_id, original, recipe, reason, observation, dispatched=dispatched)
        except Exception as exc:
            reason = str(redact(f"{type(exc).__name__}: {exc}", secrets))[:1000]
            terminal = ExplorationStatus.PARTIAL
            if not scenario_saved:
                self._blocked_scenario(exploration_id, original, recipe, reason, observation, dispatched=dispatched)
            self.repository.add_event(exploration_id, "error", "scenario_rerun_failed", reason)
        finally:
            if browser_panel_task is not None:
                browser_panel_task.cancel()
                await asyncio.gather(browser_panel_task, return_exceptions=True)
            if executor is not None:
                try:
                    await asyncio.wait_for(executor.close(), 15)
                except Exception:
                    pass
            # A trusted upload accepted just before cancellation/browser close
            # remains evidence. Its provenance never changes into page capture.
            accepted_image = self._commit_uploaded_image(exploration_id)
            if accepted_image is not None and not scenario_saved:
                self._blocked_scenario(exploration_id, original, recipe, reason or "capture_failed", observation, dispatched=dispatched)
            if self.repository.is_cancel_requested(exploration_id):
                terminal, reason = ExplorationStatus.CANCELLED, "cancelled"
            if scenario_saved and scenario is not None:
                if self.states[exploration_id].get("captures_completed", 0) < self.states[exploration_id].get("capture_count", 0):
                    scenario.evidence["capture_interruption"] = {"reason": reason, "reason_label": rerun_block_reason(reason or "capture_failed")}
                    scenario.actual_result += "\nA coleta dos prints planejados não foi concluída. As ações e conferências já registradas foram preservadas."
                self._save_capture_scenario(exploration_id, scenario, secrets)
            self.repository.update_exploration(exploration_id, status=terminal, finished_at=utc_now(), error=reason)
            self._persist(exploration_id, running=False, phase="finished", reason=reason, capture_checkpoint=None,
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
