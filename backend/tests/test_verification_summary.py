from autojev.services.verification_summary import verification_summary


def test_interaction_visual_and_missing_assertions_are_not_business_validation():
    scenarios = [
        {"status": "PASS", "evidence": {"assertions": [{"kind": "observable_state_change", "passed": True}]}},
        {"status": "PASS", "evidence": {"assertions": [{"kind": "visual_model", "passed": True}]}},
        {"status": "PASS", "evidence": {"assertions": [{"passed": True}]}},
        {"status": "PASS", "evidence": {"assertions": [{"kind": "cart_count", "passed": True}]}},
        {"status": "FAIL", "evidence": {"action_succeeded": False}},
        {"status": "FAIL", "evidence": {"verdict": {"kind": "visual_failure"}}},
        {"status": "FAIL", "evidence": {"verdict": {"kind": "technical_failure"}}},
        {"status": "FAIL", "evidence": {"verdict": {"kind": "assertion_failure"}, "assertions": [{"kind": "cart_membership", "passed": False}]}},
        {"status": "FAIL", "evidence": {"assertions": [{"kind": "url_equals", "passed": False}]}},
        {"status": "BLOCKED"},
    ]
    assert verification_summary(scenarios) == {
        "business_verified": 1, "interaction_only": 3, "execution_failures": 1,
        "technical_failures": 1, "business_failures": 1, "visual_findings": 1,
        "interaction_failures": 1, "not_validated": 1,
    }
