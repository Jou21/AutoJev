"""Persist compact, redacted inputs for visual-only replay without a browser."""

from __future__ import annotations

import copy
from dataclasses import asdict
from typing import Any, Iterable

from autojev.adapters.secrets import redact
from autojev.domain.models import ActionResult, AgentDecision, AssertionResult, Observation


def _compact(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return None
    if isinstance(value, str):
        return value[:6000]
    if isinstance(value, dict):
        return {str(key): _compact(item, depth + 1) for key, item in list(value.items())[:80]}
    if isinstance(value, (list, tuple)):
        return [_compact(item, depth + 1) for item in value[:80]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:6000]


def build_visual_review_input(
    observation: Observation, decision: AgentDecision, result: ActionResult,
    sequence: int, secret_values: Iterable[str] = (),
) -> dict[str, Any]:
    """Only the visual prompt's bounded semantic fields, never credential values."""
    elements = []
    for item in observation.interactive_elements[:35]:
        if not isinstance(item, dict):
            continue
        # Input values can contain passwords even under generic DOM keys.
        elements.append({key: value for key, value in item.items() if key not in {"value", "input_value", "text_value"}})
    result_data = result.to_dict()
    result_data["value"] = None
    result_data["evidence"] = {
        key: value for key, value in result.evidence.items()
        if key in {"auxiliary_evidence_mode", "before_screenshot", "before_text",
                   "before_observed_at", "action_step", "technical_signals", "business_settle"}
    }
    result_data["evidence"]["auxiliary_evidence_mode"] = "visual"
    result_data["evidence"]["visual_assessment"] = {"requested": True, "status": "pending"}
    decision_data = asdict(decision)
    decision_data["value"] = None
    payload = {
        "version": 1, "context_limited": False, "sequence": sequence,
        "observation": {
            "url": observation.url, "title": observation.title,
            "text": observation.text, "interactive_elements": elements,
            "screenshot": observation.screenshot, "captured_at": observation.captured_at,
            "page_state": {"products": [
                {key: item.get(key) for key in ("name", "price", "description", "image_alt", "image_loaded")}
                for item in observation.page_state.get("products", [])[:12] if isinstance(item, dict)
            ]},
        },
        "decision": decision_data, "result": result_data,
    }
    # Redact before clipping: a secret straddling a truncation boundary must
    # not leave a persisted credential prefix that no longer matches it.
    safe = redact(payload, list(secret_values))
    safe["observation"]["text"] = safe["observation"]["text"][:3500]  # type: ignore[index]
    return _compact(safe)


def _assertions(values: Any) -> list[AssertionResult]:
    return [AssertionResult(
        kind=str(item.get("kind") or "recorded_check"), expected=item.get("expected"),
        actual=item.get("actual"), passed=item.get("passed") is True,
        message=str(item.get("message") or ""),
    ) for item in values if isinstance(item, dict) and item.get("kind") != "visual_model"] if isinstance(values, list) else []


def restore_visual_review_input(
    scenario: dict[str, Any], sequence: int,
) -> tuple[Observation, AgentDecision, ActionResult, bool]:
    """Legacy captures are usable, but missing current DOM/text stays explicit."""
    evidence = scenario.get("evidence") or {}
    saved = evidence.get("visual_review_input")
    if isinstance(saved, dict) and saved.get("version") == 1:
        observation_data = copy.deepcopy(saved.get("observation") or {})
        decision_data = copy.deepcopy(saved.get("decision") or {})
        result_data = copy.deepcopy(saved.get("result") or {})
        if observation_data.get("url") and result_data.get("after_url"):
            result_data["assertions"] = _assertions(result_data.get("assertions", []))
            observation = Observation(**observation_data)
            decision = AgentDecision(**decision_data)
            result = ActionResult(**result_data)
            result.evidence["auxiliary_evidence_mode"] = "visual"
            result.evidence["visual_assessment"] = {"requested": True, "status": "pending"}
            return observation, decision, result, bool(saved.get("context_limited"))
    timeline = evidence.get("timeline") or []
    after = next((entry for entry in reversed(timeline) if isinstance(entry, dict)
                  and entry.get("kind") == "observation" and entry.get("screenshot")
                  and entry.get("title") == "Depois da ação"), {})
    assessment_images = (evidence.get("visual_assessment") or {}).get("images", [])
    after_image = next((image for image in assessment_images if isinstance(image, dict) and image.get("ref") == "after"), {})
    # Never substitute an arbitrary earlier capture for the final outcome.
    shot = after.get("screenshot") or after_image.get("screenshot")
    after_url = str(evidence.get("after_url") or scenario.get("url") or "")
    finished = str(evidence.get("action_finished_at") or scenario.get("created_at") or "")
    observation = Observation(url=after_url, title="", text="", interactive_elements=[],
                              screenshot=shot, captured_at=finished)
    decision = AgentDecision(action=str(evidence.get("action") or "observe"),
                             rationale="Revisão de capturas históricas; DOM e texto atual não foram preservados.",
                             target=evidence.get("target"))
    result = ActionResult(
        action=decision.action, success=evidence.get("action_succeeded") is True,
        started_at=str(evidence.get("action_started_at") or finished), finished_at=finished,
        before_url=str(evidence.get("before_url") or after_url), after_url=after_url,
        target=evidence.get("target"), message=str(evidence.get("message") or ""),
        assertions=_assertions(evidence.get("assertions", [])), screenshot=shot,
        evidence={key: copy.deepcopy(evidence[key]) for key in
                  ("before_screenshot", "before_text", "before_observed_at", "action_step", "technical_signals")
                  if key in evidence},
    )
    result.evidence["auxiliary_evidence_mode"] = "visual"
    result.evidence["visual_assessment"] = {"requested": True, "status": "pending"}
    return observation, decision, result, True
