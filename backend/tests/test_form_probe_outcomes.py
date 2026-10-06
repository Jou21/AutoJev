"""A real negative submission is not proof of an unrelated field's rule."""

from copy import deepcopy
from dataclasses import asdict

import pytest

from autojev.domain.models import ActionResult, AgentDecision, Observation, ScenarioStatus, utc_now
from autojev.services.business_checks import check_business_outcomes
from autojev.services.exploration import ExplorationManager
from autojev.services.scenario_timeline import prepare_scenario_timelines


def observations(missing, errors):
    fields = [
        {"identity": f"checkout::input:id={marker}", "form": "checkout", "ref": marker,
         "label": label, "required": True, "has_value": marker not in missing, "enabled": True}
        for marker, label in [("first-name", "First Name"), ("last-name", "Last Name"), ("postal-code", "Zip/Postal Code")]
    ]
    before = Observation("https://app.test/checkout", "Checkout", "Checkout", [
        {"ref": "submit", "name": "Continue", "type": "submit", "form": "checkout"}],
        page_state={"fields": fields, "forms": [{"id": "checkout", "submit_refs": ["submit"], "validation_errors": []}]})
    after = deepcopy(before)
    after.page_state["forms"][0]["validation_errors"] = errors
    return before, after


def execute_probe(before, after, marker, *, extra=None):
    decision = AgentDecision("click", "Check required validation", target="submit", scenario_hint={
        "validation_probe": {"kind": "required_field", "field_identity": f"checkout::input:id={marker}", "name": marker}})
    result = ActionResult("click", True, utc_now(), utc_now(), before.url, after.url, target="submit")
    result.assertions = check_business_outcomes(before, decision, result, after)
    result.assertions.extend(extra or [])
    return result, ExplorationManager._scenario_from_evidence(after, decision, result, 1)


@pytest.mark.parametrize("marker,error", [
    ("first-name", "Error: First Name is required"),
    ("last-name", "Error: Last Name is required"),
    ("postal-code", "Error: Postal Code is required"),
])
def test_isolated_target_has_required_validation_pass(marker, error):
    before, after = observations({marker}, [error])
    result, scenario = execute_probe(before, after, marker)
    assert scenario.status == ScenarioStatus.PASS
    assert result.assertions[-1].kind == "form_required_validation"
    assert result.assertions[-1].passed


def test_broken_peer_does_not_hide_observable_first_name_rejection():
    before, after = observations({"first-name", "last-name"}, ["First Name is required"])
    result, scenario = execute_probe(before, after, "first-name")
    assert scenario.status == ScenarioStatus.PASS
    precondition = result.assertions[0]
    assert precondition.actual["isolation_established"] is False
    assert precondition.actual["target_feedback_attributed"] is True
    assert precondition.actual["blocking_fields"] == ["Last Name"]


@pytest.mark.parametrize("feedback", [["Last Name is required"], ["Please complete required fields"], []])
def test_postal_validation_masked_by_last_name_is_blocked_not_pass_or_fail(feedback):
    before, after = observations({"last-name", "postal-code"}, feedback)
    result, scenario = execute_probe(before, after, "postal-code")
    assert scenario.status == ScenarioStatus.BLOCKED
    assert [check.kind for check in result.assertions] == ["form_validation_probe_precondition"]
    assert result.assertions[0].actual["blocking_fields"] == ["Last Name"]
    assert scenario.evidence["verification_summary"]["business_checks"] == 0


def test_named_postal_feedback_can_be_attributed_even_with_broken_peer():
    before, after = observations({"last-name", "postal-code"}, ["Last Name and Postal Code are required"])
    _, scenario = execute_probe(before, after, "postal-code")
    assert scenario.status == ScenarioStatus.PASS


def test_native_validity_attributes_target_without_a_label_in_feedback():
    before, after = observations({"last-name", "postal-code"}, ["Last Name is required"])
    after.page_state["fields"][2].update(valid=False, validation_message="Please fill out this field")
    result, scenario = execute_probe(before, after, "postal-code")
    assert result.assertions[0].actual["target_feedback_attributed"] is True
    assert scenario.status == ScenarioStatus.PASS


def test_wrong_or_populated_probe_target_is_not_validated():
    before, after = observations({"last-name"}, ["Last Name is required"])
    for marker in ("postal-code", "not-observed"):
        _, scenario = execute_probe(before, after, marker)
        assert scenario.status == ScenarioStatus.BLOCKED


def test_advance_with_missing_target_is_fail_even_when_a_peer_is_broken():
    before, after = observations({"last-name", "postal-code"}, [])
    after.url = "https://app.test/review"
    after.page_state = {"fields": [], "forms": []}
    result, scenario = execute_probe(before, after, "postal-code")
    assert scenario.status == ScenarioStatus.FAIL
    assert result.assertions[-1].kind == "form_required_validation"
    assert not result.assertions[-1].passed


def test_isolated_empty_target_no_feedback_is_validation_failure():
    before, after = observations({"postal-code"}, [])
    _, scenario = execute_probe(before, after, "postal-code")
    assert scenario.status == ScenarioStatus.FAIL


def test_masked_probe_does_not_override_independent_real_failure():
    from autojev.domain.models import AssertionResult
    before, after = observations({"last-name", "postal-code"}, ["Last Name is required"])
    _, scenario = execute_probe(before, after, "postal-code", extra=[
        AssertionResult("cart_persistence", "same cart", "lost cart", False, "Cart changed unexpectedly")])
    assert scenario.status == ScenarioStatus.FAIL


def test_masked_probe_presentation_preserves_blocked_and_explains_dependency():
    before, after = observations({"last-name", "postal-code"}, ["Last Name is required"])
    _, scenario = execute_probe(before, after, "postal-code")
    data = prepare_scenario_timelines({"id": "fixture", "app_url": before.url, "scenarios": [asdict(scenario)]})
    displayed = data["scenarios"][0]
    assert displayed["status"] == ScenarioStatus.BLOCKED
    assert displayed["evidence"]["verdict"]["kind"] == "not_validated"
    assert "pré-condição" in displayed["evidence"]["verdict"]["label"]
    assert any(entry.get("assertion", {}).get("actual", {}).get("blocking_fields") == ["Last Name"]
               for entry in displayed["evidence"]["timeline"])


def test_auxiliary_cannot_retarget_masked_postal_probe_to_blocking_last_name():
    before, after = observations({"last-name", "postal-code"}, ["Last Name is required"])
    decision = AgentDecision("click", "Check required validation", target="submit", scenario_hint={
        "validation_probe": {"kind": "required_field", "field_identity": "checkout::input:id=postal-code",
                             "name": "Zip/Postal Code"}})
    result = ActionResult("click", True, utc_now(), utc_now(), before.url, after.url, target="submit")
    result.assertions = check_business_outcomes(before, decision, result, after)
    scenario = ExplorationManager._scenario_from_evidence(after, decision, result, 1)
    original = {key: deepcopy(getattr(scenario, key)) for key in
                ("title", "steps", "expected_result", "actual_result", "preconditions")}
    scenario.title = "Last Name Required"
    scenario.expected_result = "Last Name error is shown"
    scenario.actual_result = "Last Name validation passed"
    scenario.status = ScenarioStatus.PASS
    scenario.evidence["documentation_provenance"] = {"source": "auxiliary_llm", "model_input": "text"}
    ExplorationManager._merge_scenario_enrichment(scenario, after, decision, result)
    assert scenario.status == ScenarioStatus.BLOCKED
    assert {key: getattr(scenario, key) for key in original} == original
    assert "Zip/Postal Code" in scenario.title
