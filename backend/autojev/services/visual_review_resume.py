"""Explicit, bounded visual-only review batches for already executed CTs."""

from __future__ import annotations

import asyncio
import copy
import math
import re
import time
from typing import Any
from uuid import uuid4

from autojev.adapters.openai_compatible import OpenAIAuxiliaryScenarioGenerator
from autojev.domain.models import ScenarioStatus, TestScenario, utc_now
from autojev.services.auxiliary_review_policy import (
    clamp_review_max_attempts, is_retryable_review_reason, review_retry_reason,
)
from autojev.services.visual_checks import unavailable_visual_assessment
from autojev.services.visual_review_input import restore_visual_review_input


TERMINAL_STATUSES = {"completed", "partial", "failed", "cancelled"}
STATE_EVENT = "visual_review_resume_state"


def pending_visual_scenarios(item: dict[str, Any]) -> list[dict[str, Any]]:
    return [scenario for scenario in item.get("scenarios", [])
            if ((scenario.get("evidence") or {}).get("visual_assessment", {}).get("status") != "assessed"
                or (scenario.get("evidence") or {}).get("visual_assessment", {}).get("reason") == "visual_findings_invalid")]


def visual_review_resume_summary(item: dict[str, Any]) -> dict[str, Any]:
    """Pure, read-only projection. Reading a report never starts paid work."""
    visual = (item.get("options") or {}).get("auxiliary_evidence_mode") == "visual"
    pending = len(pending_visual_scenarios(item)) if visual else 0
    state = next((event.get("payload") for event in reversed(item.get("events", []))
                  if event.get("type") == STATE_EVENT and isinstance(event.get("payload"), dict)), None)
    result = {
        "running": False, "total": pending, "completed": 0, "failed": 0,
        "pending": pending, "requests_used": 0, "request_budget": 120,
        "max_attempts": 3, "context_limited_count": 0, "reason": None,
        "eligible": visual and str(item.get("status")) in TERMINAL_STATUSES and pending > 0,
    }
    if state:
        result.update(copy.deepcopy(state))
        if not result.get("running"):
            result["pending"] = pending
    result["eligible"] = (visual and str(item.get("status")) in TERMINAL_STATUSES
                          and pending > 0 and not result.get("running"))
    return result


class VisualReviewResumeService:
    def __init__(self, manager: Any, settings: Any) -> None:
        self.manager = manager
        self.repository = manager.repository
        self.settings = settings
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.states: dict[str, dict[str, Any]] = {}
        self._semaphore = asyncio.Semaphore(2)
        # Durable state is event-backed. A process restart must not leave a
        # completed exploration displaying a fictitious running review batch.
        for exploration_id in self.repository.running_visual_review_resume_ids():
            self.state_for(exploration_id)

    def _persist(self, exploration_id: str) -> None:
        state = self.states[exploration_id]
        self.repository.add_event(exploration_id, "info", STATE_EVENT,
                                  "Estado da retomada visual preservado", copy.deepcopy(state))

    def state_for(self, exploration_id: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        item = item if item is not None else self.repository.get_exploration(exploration_id)
        state = visual_review_resume_summary(item)
        task = self.tasks.get(exploration_id)
        if state.get("running") and (task is None or task.done()):
            state.update(running=False, reason="backend_restart_interrupted", finished_at=utc_now())
            self.states[exploration_id] = state
            self._persist(exploration_id)
        return copy.deepcopy(self.states.get(exploration_id, state) if task and not task.done() else state)

    def resume(self, exploration_id: str, max_requests: int = 120, max_attempts: int = 3) -> dict[str, Any]:
        if not 1 <= max_requests <= 5000 or not 1 <= max_attempts <= 5:
            raise ValueError("O orçamento deve ser de 1 a 5000 chamadas e as tentativas de 1 a 5.")
        item = self.repository.get_exploration(exploration_id)
        if str(item.get("status")) not in TERMINAL_STATUSES:
            raise ValueError("Aguarde o encerramento da exploração antes de retomar revisões visuais.")
        if (item.get("options") or {}).get("auxiliary_evidence_mode") != "visual":
            raise ValueError("Somente explorações no modo visual podem retomar revisões.")
        if exploration_id in self.tasks and not self.tasks[exploration_id].done():
            raise RuntimeError("Já existe uma retomada visual em andamento para esta exploração.")
        pending = pending_visual_scenarios(item)
        if not pending:
            raise ValueError("Não há revisões visuais pendentes.")
        state = {
            "batch_id": str(uuid4()), "running": True, "total": len(pending),
            "completed": 0, "failed": 0, "pending": len(pending),
            "requests_used": 0, "request_budget": max_requests,
            "max_attempts": clamp_review_max_attempts(max_attempts),
            "context_limited_count": 0, "reason": None, "eligible": True,
            "started_at": utc_now(),
        }
        self.states[exploration_id] = state
        self.repository.add_event(exploration_id, "info", "visual_review_resume_started",
                                  "Retomada visual autorizada; testes não serão repetidos", copy.deepcopy(state))
        self._persist(exploration_id)
        self.tasks[exploration_id] = asyncio.create_task(self._run(exploration_id, item, pending))
        return copy.deepcopy(state)

    async def cancel(self, exploration_id: str) -> dict[str, Any]:
        self.repository.get_exploration(exploration_id, include_details=False)
        task = self.tasks.get(exploration_id)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self.states.get(exploration_id, {}).get("running"):
            # A task cancelled before its first event-loop turn never enters
            # _run's finally block; cancellation still needs a durable state.
            self.states[exploration_id].update(running=False, reason="cancelled", finished_at=utc_now())
            self.repository.add_event(exploration_id, "info", "visual_review_resume_finished",
                                      "Retomada visual cancelada antes de iniciar chamadas", copy.deepcopy(self.states[exploration_id]))
            self._persist(exploration_id)
        return self.state_for(exploration_id)

    async def shutdown(self) -> None:
        tasks = [task for task in self.tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _merge_review(self, original: dict[str, Any], generated: TestScenario,
                      observation: Any, decision: Any, result: Any,
                      context_limited: bool, attempts: int, requests: int) -> TestScenario:
        # Locally revalidate model findings against the actual saved capture
        # references. The provider never chooses a status or rewrites the CT.
        self.manager._merge_scenario_enrichment(generated, observation, decision, result)
        allowed = {field: copy.deepcopy(original[field]) for field in TestScenario.__dataclass_fields__ if field in original}
        allowed["status"] = ScenarioStatus(str(original["status"]))
        scenario = TestScenario(**allowed)
        evidence = scenario.evidence
        old = evidence.get("visual_assessment")
        history = copy.deepcopy(evidence.get("visual_assessment_history") or [])
        if isinstance(old, dict):
            history.append({**copy.deepcopy(old), "archived_at": utc_now(), "reason_archived": "explicit_visual_resume"})
        evidence["visual_assessment_history"] = history
        assessment = copy.deepcopy(generated.evidence.get("visual_assessment") or {})
        assessment.update(context_limited=context_limited,
                          review_scope="saved_capture_only_legacy" if context_limited else "saved_capture_and_semantics",
                          attempts=attempts, provider_requests=requests)
        if context_limited:
            assessment["limitations"] = list(assessment.get("limitations") or [])
            if "current_dom_and_visible_text_not_preserved" not in assessment["limitations"]:
                assessment["limitations"].append("current_dom_and_visible_text_not_preserved")
        evidence["visual_assessment"] = assessment
        provenance = copy.deepcopy(generated.evidence.get("visual_review_provenance") or {})
        provenance["context_limited"] = context_limited
        evidence["visual_review_provenance"] = provenance
        for assertion in generated.evidence.get("assertions", []):
            if assertion.get("kind") == "visual_model" and assertion not in evidence.setdefault("assertions", []):
                evidence["assertions"].append(copy.deepcopy(assertion))
                if scenario.status == ScenarioStatus.PASS and assertion.get("passed") is False:
                    scenario.status = ScenarioStatus.FAIL
        known = {entry.get("id") for entry in evidence.get("timeline", [])}
        for entry in generated.evidence.get("timeline", []):
            if (entry.get("assertion") or {}).get("kind") == "visual_model" and entry.get("id") not in known:
                evidence.setdefault("timeline", []).append(copy.deepcopy(entry))
        evidence.setdefault("timeline", []).append({
            "id": f"visual-resume-{uuid4()}", "kind": "observation", "occurred_at": utc_now(),
            "title": "Revisão visual retomada", "url": observation.url,
            "screenshot": observation.screenshot,
            "message": "Revisão das capturas salvas; DOM e texto atual indisponíveis neste registro histórico."
            if context_limited else "Revisão das capturas e do contexto semântico salvos, sem repetir o teste.",
            "visual_review": copy.deepcopy(assessment),
        })
        evidence["auxiliary_review_metrics"] = {"provider_requests": requests, "attempts": attempts, "resumed": True}
        evidence["verification_summary"] = self.manager._verification_summary(evidence)
        return scenario

    async def _run(self, exploration_id: str, item: dict[str, Any], pending: list[dict[str, Any]]) -> None:
        state = self.states[exploration_id]
        client = None
        reason = "completed"
        try:
            async with self._semaphore:
                selection = item.get("auxiliary_llm") or {}
                client = self.manager._client_for(selection["provider_id"])
                client.chat_request_budget = state["request_budget"]
                auxiliary = OpenAIAuxiliaryScenarioGenerator(
                    client, selection["model_id"], self.settings.artifact_dir / exploration_id / "screenshots", evidence_mode="visual")
                async with asyncio.timeout(float(self.settings.auxiliary_tail_timeout_seconds)):
                    reason = await self._review_pending(exploration_id, item, pending, auxiliary, client)
        except asyncio.CancelledError:
            reason = "cancelled"
        except TimeoutError:
            reason = "auxiliary_review_batch_timeout"
        except Exception:
            # Provider/client exception strings can contain confidential URLs.
            reason = "auxiliary_review_batch_failed"
        finally:
            if client is not None:
                state["requests_used"] = int(getattr(client, "chat_request_count", state["requests_used"]))
                try:
                    await self.manager._record_model_usage(exploration_id, [client], role="auxiliary")
                except Exception:
                    state["usage_accounting_pending"] = True
            state.update(running=False, reason=reason, finished_at=utc_now())
            state["pending"] = len(pending_visual_scenarios(self.repository.get_exploration(exploration_id)))
            self.repository.add_event(exploration_id, "info", "visual_review_resume_finished",
                                      "Retomada visual encerrada; exploração original preservada", copy.deepcopy(state))
            self._persist(exploration_id)

    async def _review_pending(self, exploration_id: str, item: dict[str, Any],
                              pending: list[dict[str, Any]], auxiliary: Any, client: Any) -> str:
        state = self.states[exploration_id]
        sequences = {}
        for index, scenario in enumerate(item.get("scenarios", []), 1):
            saved = (scenario.get("evidence") or {}).get("visual_review_input") or {}
            saved_sequence = saved.get("sequence")
            match = re.fullmatch(r"CT(\d+)", str(scenario["id"]))
            # Removed/no-op drafts can leave gaps. Never renumber surviving
            # CTs or their repository ordering merely to resume a review.
            sequences[scenario["id"]] = (
                saved_sequence if isinstance(saved_sequence, int) and not isinstance(saved_sequence, bool) and saved_sequence > 0
                else int(match.group(1)) if match else index
            )
        batch_stop_reason = None
        for original in pending:
            sequence = sequences[original["id"]]
            observation, decision, result, context_limited = restore_visual_review_input(original, sequence)
            if context_limited:
                state["context_limited_count"] += 1
            start_requests = int(getattr(client, "chat_request_count", 0))
            generated = await auxiliary.cached_scenario(observation, decision, result, sequence)
            attempts = 0
            stop_reason = None
            while generated is None or review_retry_reason(generated):
                used = int(getattr(client, "chat_request_count", 0)) - start_requests
                if used >= state["max_attempts"] or attempts >= state["max_attempts"]:
                    break
                # Cache lookup deliberately precedes every budget/cooldown gate.
                if int(getattr(client, "chat_request_count", 0)) >= state["request_budget"]:
                    stop_reason = "auxiliary_request_budget_exhausted"
                    break
                non_retryable = getattr(auxiliary, "non_retryable_reason", None)
                if non_retryable:
                    stop_reason = str(non_retryable)
                    break
                if int(getattr(auxiliary, "rate_limit_count", 0)) >= int(self.settings.auxiliary_max_rate_limits):
                    stop_reason = "auxiliary_rate_limit_budget_exhausted"
                    break
                delay = float(getattr(auxiliary, "cooldown_remaining_seconds", 0))
                if not math.isfinite(delay):
                    stop_reason = "auxiliary_authentication_rejected"
                    break
                if delay > 0:
                    await asyncio.sleep(delay)
                remaining = state["max_attempts"] - used
                client.chat_request_budget = min(state["request_budget"], int(client.chat_request_count) + remaining)
                attempts += 1
                generated = await auxiliary.create_scenario(observation, decision, result, sequence)
                state["requests_used"] = int(getattr(client, "chat_request_count", 0))
                await self.manager._record_model_usage(exploration_id, [client], role="auxiliary")
                failure = review_retry_reason(generated)
                if not failure or not is_retryable_review_reason(failure):
                    break
                cached = await auxiliary.cached_scenario(observation, decision, result, sequence)
                if cached is not None:
                    generated = cached
                    break
                if ":free" in str(auxiliary.model) and state["requests_used"] > start_requests:
                    await asyncio.sleep(float(self.settings.auxiliary_request_interval_seconds))
                elif attempts < state["max_attempts"]:
                    await asyncio.sleep(min(0.25 * attempts, 1.0))
            client.chat_request_budget = state["request_budget"]
            if stop_reason:
                batch_stop_reason = stop_reason
                if generated is None:
                    # Continue cache-only work for subsequent identical saved
                    # evidence; an exhausted HTTP cap does not disable cache.
                    continue
            if generated is None:
                # No transport attempt can occur without an image; the adapter
                # normally supplies this fallback, retained defensively here.
                fields = {key: copy.deepcopy(original[key]) for key in TestScenario.__dataclass_fields__ if key in original}
                generated = TestScenario(**{**fields, "status": ScenarioStatus(str(original["status"]))})
                generated.evidence["visual_assessment"] = unavailable_visual_assessment(
                    requested=True, model=auxiliary.model, reason="auxiliary_review_retry_exhausted")
            requests = int(getattr(client, "chat_request_count", 0)) - start_requests
            assessment = generated.evidence.get("visual_assessment") or {}
            if (assessment.get("reason") == "auxiliary_request_budget_exhausted"
                    and state["requests_used"] < state["request_budget"]
                    and requests >= state["max_attempts"]):
                assessment["reason"] = "auxiliary_review_attempts_exhausted"
            scenario = self._merge_review(original, generated, observation, decision, result,
                                          context_limited, attempts, requests)
            self.repository.add_scenario(exploration_id, sequence, scenario)
            assessed = scenario.evidence.get("visual_assessment", {}).get("status") == "assessed"
            state["completed" if assessed else "failed"] += 1
            state["pending"] = state["total"] - state["completed"]
            state["requests_used"] = int(getattr(client, "chat_request_count", 0))
            self.repository.add_event(exploration_id, "info", "visual_review_resume_scenario",
                                      "Revisão visual retomada a partir das capturas salvas", {
                                          "scenario_id": scenario.id, "assessed": assessed,
                                          "context_limited": context_limited, "attempts": attempts,
                                          "provider_requests": requests,
                                          "reason": scenario.evidence.get("visual_assessment", {}).get("reason"),
                                      })
            self._persist(exploration_id)
            if ":free" in str(auxiliary.model) and requests > 0:
                await asyncio.sleep(float(self.settings.auxiliary_request_interval_seconds))
        return batch_stop_reason or ("completed" if not state["failed"] else "completed_with_unavailable_reviews")
