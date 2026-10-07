from types import SimpleNamespace

import pytest

from autojev.services.auxiliary_review_policy import (
    DEFAULT_REVIEW_MAX_ATTEMPTS,
    MAX_REVIEW_MAX_ATTEMPTS,
    clamp_review_max_attempts,
    is_retryable_review_reason,
    review_retry_reason,
)


@pytest.mark.parametrize("reason", [
    "auxiliary_timeout", "TimeoutError", "auxiliary_unavailable",
    "auxiliary_provider_temporary_error", "auxiliary_rate_limited",
    "auxiliary_response_invalid", "auxiliary_response_schema_invalid",
    "visual_assessment_not_returned", "visual_assessment_schema_invalid",
    "visual_findings_invalid",
])
def test_only_explicit_transient_or_malformed_reasons_are_retryable(reason):
    assert is_retryable_review_reason(reason)


@pytest.mark.parametrize("reason", [
    None, "", "auxiliary_provider_error", "auxiliary_authentication_rejected",
    "auxiliary_rate_limited_daily_quota", "auxiliary_rate_limit_budget_exhausted",
    "auxiliary_request_budget_exhausted", "vision_input_not_supported",
    "screenshot_not_available", "after_screenshot_not_available",
    "auxiliary_backlog_full", "exploration_cancelled", "backend_shutdown",
    "documentation_time_limit", "awaiting_provider_retry", "unknown_reason",
])
def test_nonretryable_failures_cannot_generate_repeat_calls(reason):
    assert not is_retryable_review_reason(reason)


@pytest.mark.parametrize("value,expected", [
    (None, 3), (True, 3), ("invalid", 3), (float("inf"), 3),
    (0, 1), (-20, 1), (1, 1), (3, 3), ("5", 5), (9, 5),
])
def test_attempt_budget_counts_first_attempt_and_is_bounded(value, expected):
    assert DEFAULT_REVIEW_MAX_ATTEMPTS == 3
    assert MAX_REVIEW_MAX_ATTEMPTS == 5
    assert clamp_review_max_attempts(value) == expected


@pytest.mark.parametrize("as_mapping", [True, False])
def test_invalid_visual_reason_wins_over_accepted_transport_provenance(as_mapping):
    evidence = {
        "visual_assessment": {"status": "unavailable", "reason": "visual_assessment_not_returned"},
        "visual_review_provenance": {"source": "auxiliary_llm", "reason": "auxiliary_response_accepted"},
        "documentation_provenance": {"source": "local_evidence", "reason": "deterministic_browser_evidence"},
    }
    scenario = {"evidence": evidence} if as_mapping else SimpleNamespace(evidence=evidence)
    assert review_retry_reason(scenario) == "visual_assessment_not_returned"


@pytest.mark.parametrize("reason", ["no_visual_deviation_reported", "visual_findings_require_review", "visual_findings_uncertain"])
def test_valid_visual_review_is_not_retried_to_remove_findings(reason):
    scenario = {"evidence": {
        "visual_assessment": {"status": "assessed", "reason": reason},
        "visual_review_provenance": {"source": "auxiliary_llm"},
    }}
    assert review_retry_reason(scenario) is None


def test_documentation_retry_reason_and_unavailable_auth_are_preserved():
    assert review_retry_reason({"evidence": {"documentation_provenance": {
        "source": "local_fallback", "reason": "auxiliary_response_schema_invalid",
    }}}) == "auxiliary_response_schema_invalid"
    assert review_retry_reason({"evidence": {"visual_assessment": {
        "status": "unavailable", "reason": "auxiliary_authentication_rejected",
    }}}) == "auxiliary_authentication_rejected"
    assert review_retry_reason({"evidence": {"documentation_provenance": {
        "source": "auxiliary_cache", "reason": "identical_evidence_reused",
    }}}) is None
    assert review_retry_reason({}) is None


@pytest.mark.parametrize("visual_reason", [None, "auxiliary_visual_evidence_unavailable", "awaiting_provider_retry"])
@pytest.mark.parametrize("fallback_reason", ["auxiliary_rate_limited", "auxiliary_timeout", "auxiliary_authentication_rejected"])
def test_concrete_provider_failure_is_not_hidden_by_generic_visual_marker(visual_reason, fallback_reason):
    scenario = {"evidence": {
        "visual_assessment": {"status": "unavailable", "reason": visual_reason},
        "documentation_provenance": {"source": "local_fallback", "reason": fallback_reason},
    }}
    assert review_retry_reason(scenario) == fallback_reason
