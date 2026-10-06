"""Recognize only executor-proven, undispatched stale-control attempts."""

from typing import Any

from autojev.services.verification_summary import NAVIGATION_CHECKS


def is_stale_undispatched_action(result: Any) -> bool:
    """A stale ref is not a functional failure, but never discard native checks.

    Messages are intentionally irrelevant: a runtime error after a real
    dispatch cannot acquire this exemption by containing the word "stale".
    Model-declared navigation/visibility checks after a rejected dispatch do
    not establish that the action was executed. Native business, technical
    and visual assertions contradict an undispatched-only interpretation and
    keep their original verdict.
    """
    def value(key: str, default: Any = None) -> Any:
        return result.get(key, default) if isinstance(result, dict) else getattr(result, key, default)

    evidence = value("evidence") or {}
    dispatch = evidence.get("action_dispatch") if isinstance(evidence, dict) else None
    if (value("success") is not False or not isinstance(dispatch, dict)
            or dispatch.get("status") != "not_executed"
            or dispatch.get("reason") != "stale_observed_control"):
        return False
    provisional = NAVIGATION_CHECKS | {"observable_state_change"}
    for assertion in value("assertions", []) or []:
        kind = assertion.get("kind") if isinstance(assertion, dict) else getattr(assertion, "kind", None)
        if kind not in provisional:
            return False
    return True
