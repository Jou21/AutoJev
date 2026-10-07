"""Shared cost-bounded retry classification for live and resumed reviews.

This module never performs a retry. Callers count the first attempt and apply
their own request/cooldown/cancellation gates before every subsequent attempt.
Unknown provider errors are deliberately not treated as transient failures.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


DEFAULT_REVIEW_MAX_ATTEMPTS = 3
MAX_REVIEW_MAX_ATTEMPTS = 5

_RETRYABLE_REASONS = frozenset({
    "auxiliary_timeout",
    "TimeoutError",
    "auxiliary_unavailable",
    "auxiliary_provider_temporary_error",
    "auxiliary_rate_limited",
    "auxiliary_response_invalid",
    "auxiliary_response_schema_invalid",
    "visual_assessment_not_returned",
    "visual_assessment_schema_invalid",
    "visual_findings_invalid",
})


def clamp_review_max_attempts(
    value: Any, default: int = DEFAULT_REVIEW_MAX_ATTEMPTS
) -> int:
    """Return total attempts (including the first), never more than five."""
    try:
        attempts = int(value) if value is not None and not isinstance(value, bool) else int(default)
    except (TypeError, ValueError, OverflowError):
        attempts = int(default)
    return min(MAX_REVIEW_MAX_ATTEMPTS, max(1, attempts))


def is_retryable_review_reason(reason: str | None) -> bool:
    """Do not retry auth, quota, missing evidence or local budget failures."""
    return reason in _RETRYABLE_REASONS


def review_retry_reason(scenario: Any) -> str | None:
    """Return a failed review's stable reason, not its functional CT verdict.

    Both stored dictionaries and TestScenario instances are accepted. A valid
    visual review with accepted or uncertain findings is complete: retrying it
    until findings disappear could hide evidence and waste requests.
    """
    evidence = scenario.get("evidence") if isinstance(scenario, Mapping) else getattr(scenario, "evidence", None)
    if not isinstance(evidence, Mapping):
        return None
    provenance = evidence.get("visual_review_provenance") or evidence.get("documentation_provenance") or {}
    if not isinstance(provenance, Mapping):
        provenance = {}
    visual = evidence.get("visual_assessment")
    visual_reason = None
    if isinstance(visual, Mapping):
        visual_reason = visual.get("reason")
        if visual.get("status") == "assessed" and visual_reason != "visual_findings_invalid":
            return None
        if visual_reason and visual_reason not in {
            "auxiliary_visual_evidence_unavailable", "awaiting_provider_retry",
            "awaiting_auxiliary_review", "visual_review_pending",
        }:
            return str(visual_reason)
    # The merge guard can turn a draft/pending assessment into a generic
    # unavailable marker. Do not let that marker conceal an actual HTTP 429,
    # timeout or other explicit failure recorded by the adapter.
    fallback_reason = provenance.get("reason")
    if provenance.get("source") not in {"auxiliary_llm", "auxiliary_cache"} and fallback_reason:
        return str(fallback_reason)
    if visual_reason:
        return str(visual_reason)
    if provenance.get("source") in {"auxiliary_llm", "auxiliary_cache"}:
        return "visual_assessment_not_returned" if evidence.get("visual_review_provenance") else None
    return None
