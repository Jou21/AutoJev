"""Durable, fail-closed setup and action plans (not browser dispatch authority).

Only persisted v1 recipes are copied. Checks retain kind/expected, never old
actual values, model messages, decisions or observations. An incomplete build
is diagnostic only; validate before execution, then resolve each recipe live.
"""
from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import Any

from autojev.services.rerun_recipe import (
    RerunRecipeBlocked, _origin, _public_literal, _public_url,
    required_recipe_inputs, validate_rerun_recipe,
)

JOURNEY_VERSION = "ct-journey-v1"
# Deterministic checks emitted by business_checks and the local executor. Visual
# model opinions cannot be reproduced by local replay and are deliberately absent.
LOCAL_CHECK_KINDS = frozenset({
    "url_equals", "url_contains", "title_contains", "text_contains", "element_visible",
    "cart_control_state", "cart_count", "cart_membership", "cart_reset",
    "cart_contents_match", "cart_persistence", "cart_item_prices",
    "checkout_items_match_cart", "checkout_item_prices", "checkout_cancel_cart_count",
    "checkout_cancel_progress", "checkout_cancel_selected_products",
    "checkout_form_cancel_progress", "checkout_subtotal", "checkout_total",
    "field_value_matches", "form_fields_preserved_on_dismiss", "form_progress",
    "form_required_validation", "form_validation_feedback_dismissed",
    "form_validation_probe_precondition", "order_confirmation", "other_fields_unchanged",
    "product_detail_identity", "product_detail_price", "product_image_loaded",
    "product_sort_order", "selection_matches", "no_new_browser_errors",
    "no_new_application_errors", "observable_state_change",
})
_DIRECT = frozenset({"url_equals", "url_contains", "title_contains", "text_contains", "element_visible"})


def _public_expected(value: Any, depth: int = 0) -> None:
    if depth > 12:
        raise RerunRecipeBlocked("invalid_journey_check")
    if isinstance(value, str):
        _public_literal(value, ())
    elif value is None or isinstance(value, bool) or type(value) is int:
        return
    elif type(value) is float and isfinite(value):
        return
    elif isinstance(value, list) and len(value) <= 100:
        for item in value:
            _public_expected(item, depth + 1)
    elif isinstance(value, dict) and len(value) <= 100:
        for key, item in value.items():
            _public_literal(key, ())
            _public_expected(item, depth + 1)
    else:
        raise RerunRecipeBlocked("invalid_journey_check")


def _checks(assertions: Any) -> list[dict[str, Any]]:
    if not isinstance(assertions, list) or not assertions or len(assertions) > 100:
        raise RerunRecipeBlocked("missing_or_invalid_journey_checks")
    checks = []
    for assertion in assertions:
        if (not isinstance(assertion, dict)
                or set(assertion) - {"kind", "expected", "actual", "passed", "message"}
                or not isinstance(assertion.get("kind"), str)
                or assertion["kind"] not in LOCAL_CHECK_KINDS
                or "expected" not in assertion or assertion["expected"] is None
                or ("passed" in assertion and type(assertion["passed"]) is not bool)):
            raise RerunRecipeBlocked("unsupported_journey_check")
        expected = assertion["expected"]
        if assertion["kind"] in _DIRECT and (not isinstance(expected, str) or not expected.strip()):
            raise RerunRecipeBlocked("invalid_journey_check")
        _public_expected(expected)
        checks.append({"kind": assertion["kind"], "expected": deepcopy(expected)})
    return checks


def _outcome(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) != {"success", "checks"}
            or type(value["success"]) is not bool or not isinstance(value["checks"], list)):
        raise RerunRecipeBlocked("invalid_journey_outcome")
    checks = []
    for check in value["checks"]:
        if (not isinstance(check, dict) or set(check) != {"kind", "expected", "passed"}
                or type(check["passed"]) is not bool):
            raise RerunRecipeBlocked("invalid_journey_outcome")
        normalized = _checks([check])[0]
        checks.append({**normalized, "passed": check["passed"]})
    return {"success": value["success"], "checks": checks}


def _result_outcome(result: dict[str, Any]) -> dict[str, Any] | None:
    if type(result.get("success")) is not bool:
        return None
    raw = result.get("assertions", [])
    if not isinstance(raw, list):
        raise RerunRecipeBlocked("invalid_journey_outcome")
    normalized = _checks(raw) if raw else []
    checks = []
    for check, original in zip(normalized, raw):
        if type(original.get("passed")) is not bool:
            raise RerunRecipeBlocked("invalid_journey_outcome")
        checks.append({**check, "passed": original["passed"]})
    return {"success": result["success"], "checks": checks}


def _missing_direct_checks(steps: list[dict[str, Any]], checks: list[dict[str, Any]]) -> bool:
    # Textual checks have no independent replay evaluator: their exact expected
    # value must be dispatched by a saved decision. Visibility refs are rebound
    # through v1 fingerprints instead and remain subject to runtime validation.
    available = [assertion for step in steps
                 for assertion in step["recipe"]["decision"]["assertions"]]
    return any(check["kind"] in _DIRECT - {"element_visible"} and check not in available
               for check in checks)


def _recipe(recipe: Any, app_url: str) -> dict[str, Any]:
    validate_rerun_recipe(recipe, app_url)
    # v1 validation permits extra top-level metadata; do not persist it.
    safe = {key: recipe[key] for key in (
        "version", "enabled", "origin", "before_url", "before_effect_digest",
        "before_state_digest", "target_fingerprint", "decision",
    )}
    if "state_inputs" in recipe:
        safe["state_inputs"] = recipe["state_inputs"]
    if "journey_capture" in recipe:
        if recipe["journey_capture"] is not True:
            raise RerunRecipeBlocked("invalid_journey_recipe")
        safe["journey_capture"] = True
    return deepcopy(safe)


def build_rerun_journey(
    actions: list[dict[str, Any]], action_step: int, app_url: str, assertions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build all ordered steps 1..action_step, including unsuccessful actions.

    Later actions are irrelevant. Missing, duplicate, disabled or malformed
    recipes produce explicit gaps, never a runnable partial journey. Entry URL
    is the first persisted precondition, not the final action's current route.
    Assertion failures are valid checks to rerun; unsupported checks are gaps.
    """
    journey: dict[str, Any] = {
        "version": JOURNEY_VERSION, "action_step": action_step,
        "entry_url": None, "steps": [], "checks": [], "complete": False, "gaps": [],
    }
    gaps = journey["gaps"]
    if type(action_step) is not int or action_step < 1:
        gaps.append({"reason": "invalid_action_step"})
        journey["action_step"] = None
        return journey
    try:
        _origin(app_url)
        journey["checks"] = _checks(assertions)
    except RerunRecipeBlocked:
        gaps.append({"reason": "invalid_journey_checks_or_origin"})
    by_step: dict[int, list[dict[str, Any]]] = {}
    if not isinstance(actions, list):
        gaps.append({"reason": "invalid_actions"})
        actions = []
    for action in actions:
        if (not isinstance(action, dict) or type(action.get("step_number")) is not int
                or action["step_number"] < 1):
            gaps.append({"reason": "invalid_action_step"})
            continue
        if action["step_number"] <= action_step:
            by_step.setdefault(action["step_number"], []).append(action)
    for step in range(1, action_step + 1):
        candidates = by_step.get(step, [])
        if len(candidates) != 1:
            gaps.append({"step_number": step, "reason": "missing_action" if not candidates else "duplicate_action"})
            continue
        try:
            result = candidates[0].get("result")
            evidence = result.get("evidence") if isinstance(result, dict) else None
            raw = evidence.get("rerun_recipe") if isinstance(evidence, dict) else None
            recipe = _recipe(raw, app_url)
        except (RerunRecipeBlocked, KeyError):
            gaps.append({"step_number": step, "reason": "missing_or_invalid_recipe"})
            continue
        entry = {"step_number": step, "recipe": recipe}
        try:
            outcome = _result_outcome(result)
            if outcome is not None:
                entry["outcome"] = outcome
        except RerunRecipeBlocked:
            gaps.append({"step_number": step, "reason": "invalid_journey_outcome"})
        journey["steps"].append(entry)
        if step == 1:
            journey["entry_url"] = recipe["before_url"]
    if _missing_direct_checks(journey["steps"], journey["checks"]):
        gaps.append({"reason": "missing_direct_check_recipe"})
    journey["complete"] = not gaps
    return journey


def _authentications(journey: dict[str, Any], app_url: str) -> list[dict[str, Any]]:
    """Validate ordered login boundaries; equal boundaries preserve event order."""
    from autojev.services.rerun_setup import validate_authentication_setup

    count = journey.get("action_step")
    if type(count) is not int or count < 1:
        raise RerunRecipeBlocked("invalid_journey_sequence")
    events = journey.get("authentications", [])
    if not isinstance(events, list):
        raise RerunRecipeBlocked("invalid_journey_authentications")
    previous = 0
    for event in events:
        if (not isinstance(event, dict) or set(event) != {"after_step", "setup"}
                or type(event["after_step"]) is not int
                or not previous <= event["after_step"] < count):
            raise RerunRecipeBlocked("invalid_journey_authentications")
        validate_authentication_setup(event["setup"], app_url)
        previous = event["after_step"]
    if "setup" in journey:
        validate_authentication_setup(journey["setup"], app_url)
        if "authentications" in journey:
            if not events or events[0]["after_step"] != 0 or events[0]["setup"] != journey["setup"]:
                raise RerunRecipeBlocked("conflicting_journey_authentication")
        else:
            events = [{"after_step": 0, "setup": journey["setup"]}]
    return events


def required_journey_inputs(journey: dict[str, Any]) -> list[str]:
    """Return vault aliases only; callers must supply fresh values at execution."""
    if not isinstance(journey, dict) or not isinstance(journey.get("steps"), list):
        raise RerunRecipeBlocked("invalid_journey")
    try:
        inputs = {key for step in journey["steps"]
                  for key in required_recipe_inputs(step["recipe"])}
        for event in _authentications(journey, journey["entry_url"]):
            inputs.update(event["setup"]["required_inputs"])
        return sorted(inputs)
    except (KeyError, TypeError, AttributeError) as exc:
        raise RerunRecipeBlocked("invalid_journey") from exc


def validate_rerun_journey(journey: dict[str, Any], app_url: str) -> None:
    """Reject incomplete/tampered plans before opening a browser.

    This structural contract does not authorize skipping fresh v1 precondition
    resolution, or claim that a previously failing check will pass on replay.
    """
    try:
        required = {"version", "action_step", "entry_url", "steps", "checks", "complete", "gaps"}
        if (not isinstance(journey, dict) or not required <= set(journey)
                or set(journey) - required - {"setup", "required_inputs", "authentications"}
                or journey["version"] != JOURNEY_VERSION):
            raise RerunRecipeBlocked("invalid_journey")
        if journey["complete"] is not True or journey["gaps"] != []:
            raise RerunRecipeBlocked("incomplete_journey")
        count = journey["action_step"]
        steps = journey["steps"]
        if (type(count) is not int or count < 1 or not isinstance(steps, list)
                or len(steps) != count):
            raise RerunRecipeBlocked("invalid_journey_sequence")
        if _checks(journey["checks"]) != journey["checks"]:
            raise RerunRecipeBlocked("invalid_journey_checks")
        if "required_inputs" in journey:
            inputs = journey["required_inputs"]
            if not isinstance(inputs, list) or inputs != required_journey_inputs(journey):
                raise RerunRecipeBlocked("invalid_journey_inputs")
        for number, step in enumerate(steps, 1):
            if (not isinstance(step, dict) or not {"step_number", "recipe"} <= set(step)
                    or set(step) - {"step_number", "recipe", "outcome"}
                    or type(step["step_number"]) is not int or step["step_number"] != number):
                raise RerunRecipeBlocked("invalid_journey_sequence")
            if "outcome" in step:
                _outcome(step["outcome"])
            if _recipe(step["recipe"], app_url) != step["recipe"]:
                raise RerunRecipeBlocked("invalid_journey_recipe")
        if _missing_direct_checks(steps, journey["checks"]):
            raise RerunRecipeBlocked("missing_direct_check_recipe")
        events = _authentications(journey, app_url)
        initial_authentication = events and events[0]["after_step"] == 0
        expected_entry = (events[0]["setup"]["before_url"] if initial_authentication
                          else steps[0]["recipe"]["before_url"])
        entry = (journey["entry_url"] if initial_authentication
                 else _public_url(journey["entry_url"], ()))
        if _origin(entry) != _origin(app_url) or entry != expected_entry:
            raise RerunRecipeBlocked("invalid_journey_entry_url")
    except RerunRecipeBlocked:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as exc:
        raise RerunRecipeBlocked("invalid_journey") from exc
