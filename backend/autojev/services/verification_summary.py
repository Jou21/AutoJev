"""Shared vocabulary for how much a CT actually verified.

A navigational/interaction success is useful evidence, but does not validate
the business outcome. Visual findings remain a separate negative-evidence
channel and are never treated as a complete visual approval.
"""
from typing import Any


NAVIGATION_CHECKS = frozenset({
    "url_contains", "url_equals", "title_contains", "element_visible", "text_contains",
})
TECHNICAL_CHECKS = frozenset({"no_new_browser_errors", "no_new_application_errors"})
# This shared set is intentionally exhaustive for generic model/executor
# assertions, so URL/title/visibility checks cannot inflate business coverage.
INTERACTION_CHECKS = frozenset({"observable_state_change"}) | NAVIGATION_CHECKS | TECHNICAL_CHECKS
NON_BUSINESS_CHECKS = INTERACTION_CHECKS | {"visual_model", "form_validation_probe_precondition"}


def evidence_verification_summary(evidence: dict[str, Any]) -> dict[str, Any]:
    assertions = evidence.get("assertions") or []
    business = [a for a in assertions if a.get("kind") and a.get("kind") not in NON_BUSINESS_CHECKS]
    visual = evidence.get("visual_assessment") or {}
    return {
        "level": "business" if business else "interaction_only" if assertions else "unverified",
        "business_checks": len(business),
        "business_failures": sum(a.get("passed") is False for a in business),
        "interaction_checks": sum(a.get("kind") == "observable_state_change" for a in assertions),
        "navigation_checks": sum(a.get("kind") in NAVIGATION_CHECKS for a in assertions),
        "technical_checks": sum(a.get("kind") in TECHNICAL_CHECKS for a in assertions),
        "visual_status": visual.get("status", "disabled"),
        "visual_reused_identical_evidence": bool(visual.get("reused_identical_evidence")),
        "limitation": None if business else "A ação foi observada; isso não comprova uma regra de negócio completa.",
    }


def verification_summary(scenarios: list[dict[str, Any]]) -> dict[str, int]:
    """Count CT outcomes without turning generic interactions into business PASS."""
    counts = {
        "business_verified": 0, "interaction_only": 0, "execution_failures": 0,
        "technical_failures": 0, "business_failures": 0, "visual_findings": 0,
        "interaction_failures": 0, "not_validated": 0,
    }
    for scenario in scenarios:
        evidence = scenario.get("evidence") or {}
        assertions = evidence.get("assertions") or []
        status = str(scenario.get("status") or "SKIPPED").upper()
        kind = (evidence.get("verdict") or {}).get("kind")
        business = [a for a in assertions if a.get("kind") and a.get("kind") not in NON_BUSINESS_CHECKS]
        if status == "PASS":
            counts["business_verified" if any(a.get("passed") is True for a in business) else "interaction_only"] += 1
        elif status == "FAIL":
            category = (
                "execution_failures" if kind == "execution_failure" or evidence.get("action_succeeded") is False
                else "technical_failures" if kind == "technical_failure"
                else "visual_findings" if kind == "visual_failure"
                else "business_failures" if any(a.get("passed") is False for a in business)
                else "interaction_failures"
            )
            counts[category] += 1
        else:
            counts["not_validated"] += 1
    return counts
