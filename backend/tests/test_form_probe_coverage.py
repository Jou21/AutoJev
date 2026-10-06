"""Bounded required-field exploration keeps defects and masked checks distinct."""

from copy import deepcopy

import pytest

from autojev.domain.models import ActionResult, AgentDecision, AssertionResult, Observation, utc_now
from autojev.services.business_checks import check_business_outcomes
from autojev.services.coverage import CoveragePlanner
from autojev.services.exploration import ExplorationManager


def form_page(*, values=(True, False, True), cart_count=None, optional=False):
    names = ("First Name", "Last Name", "Zip/Postal Code")
    elements = [
        {"ref": f"e{i}", "tag": "input", "type": "text", "name": name,
         "form": "checkout", "required": True, "has_value": values[i],
         "enabled": True, "interactable": True}
        for i, name in enumerate(names)
    ]
    if optional:
        elements.append({"ref": "optional", "tag": "input", "type": "text", "name": "Company",
                         "form": "checkout", "required": False, "has_value": False})
    elements.append({"ref": "submit", "tag": "button", "type": "submit", "name": "Continue",
                     "form": "checkout", "enabled": True, "interactable": True})
    state = {
        "fields": [{"ref": item["ref"], "identity": f"field-{item['ref']}", "label": item["name"],
                    "form": "checkout", "required": item.get("required", False),
                    "has_value": item["has_value"], "enabled": True}
                   for item in elements if item["tag"] == "input"],
        "forms": [{"id": "checkout", "submit_refs": ["submit"], "validation_errors": []}],
    }
    if cart_count is not None:
        state["cart"] = {"count": cart_count}
    return Observation(url="https://app.test/checkout-step-one", title="Checkout",
                       text="Your information", interactive_elements=elements, page_state=state)


def action_result(before, after, decision):
    return ActionResult(decision.action, True, utc_now(), utc_now(), before.url, after.url,
                        target=decision.target)


def prepared_planner(page, *, broken_ref="e1"):
    planner = CoveragePlanner(page.url)
    state = planner.states[planner.observe(page)]
    # Native fill succeeded but the application immediately lost this value.
    if broken_ref:
        fillable = next(item for item in state.candidates.values() if item.get("ref") == broken_ref
                        and planner._move_action(item) == "fill")
        planner.failed_form_fields.add((state.url, fillable["_element_fingerprint"]))
    for move_key, element in state.candidates.items():
        if planner._move_action(element) == "fill":
            state.attempted.add(move_key)
            planner.global_route_moves_attempted.add((state.url, move_key))
    # The ordinary submit was already exercised; its business result is not
    # fabricated as a successful journey merely to enable negative probes.
    submit = AgentDecision("click", "Exercise the conventional form", target="submit")
    planner.record_action(page, submit, action_result(page, page, submit), page)
    return planner


def apply_action(page, decision, *, broken_ref="e1", cross_clear=None, clear_noop=False):
    after = deepcopy(page)
    if decision.action == "fill":
        populated = bool(decision.value) and decision.target != broken_ref
        if clear_noop and decision.value == "":
            populated = True
        for elements in (after.interactive_elements, after.page_state["fields"]):
            for item in elements:
                if item.get("ref") == decision.target:
                    item["has_value"] = populated
                elif cross_clear and item.get("ref") == cross_clear:
                    item["has_value"] = False
    else:
        missing = next((item for item in after.page_state["fields"]
                        if item["required"] and not item["has_value"]), None)
        after.page_state["forms"][0]["validation_errors"] = (
            [f"{missing['label']} is required"] if missing else []
        )
    result = action_result(page, after, decision)
    result.assertions.extend(check_business_outcomes(page, decision, result, after))
    return after, result


@pytest.mark.parametrize("cart_count", [None, 0, 1])
def test_broken_peer_does_not_blanket_block_required_field_probes(cart_count):
    page = form_page(cart_count=cart_count)
    planner = prepared_planner(page)
    probes = []
    for _ in range(12):
        selected = planner.next_restoration_action(page)
        if selected is None:
            break
        decision, reason = selected
        assert reason == "isolated_required_field_probe"
        assert not (decision.action == "fill" and decision.target == "e1" and decision.value)
        after, result = apply_action(page, decision)
        if decision.action == "click":
            scenario = ExplorationManager._scenario_from_evidence(after, decision, result, len(probes) + 1)
            probes.append((decision.scenario_hint["validation_probe"], scenario.status.value))
        planner.record_action(page, decision, result, after)
        page = after
    assert len(probes) == 3
    assert [(probe["name"], status) for probe, status in probes] == [
        ("First Name", "PASS"), ("Last Name", "PASS"), ("Zip/Postal Code", "BLOCKED"),
    ]
    assert [probe["strategy"] for probe, _ in probes] == [
        "blocked_peer_observed", "isolated_required_field", "blocked_peer_observed",
    ]
    assert [probe["isolation_established"] for probe, _ in probes] == [False, True, False]
    for probe, _ in probes:
        assert "value" not in probe
        assert all("value" not in field for field in probe["blocking_fields"])
    restrictions = planner.drain_restrictions()
    assert len(restrictions) == 1
    assert restrictions[0]["reason"] == "required_field_validation_masked"
    assert restrictions[0]["field_name"] == "Zip/Postal Code"
    assert restrictions[0]["validation_probe"]["blocking_fields"][0]["name"] == "Last Name"
    assert planner.can_finish() and not planner.coverage_complete()
    assert planner.next_restoration_action(page) is None


def test_optional_blank_field_cannot_block_or_invalidate_required_probe_isolation():
    page = form_page(values=(False, True, True), optional=True)
    planner = prepared_planner(page, broken_ref=None)
    decision = planner._form_probe_action(planner.states[planner.current_key])
    assert decision.action == "click"
    assert decision.scenario_hint["validation_probe"]["isolation_established"] is True
    assert decision.scenario_hint["validation_probe"]["blocking_fields"] == []


@pytest.mark.parametrize("secret_evidence", ["field_metadata", "legacy_password_control"])
def test_negative_probe_policy_never_clears_or_submits_an_authentication_form(secret_evidence):
    page = form_page()
    if secret_evidence == "field_metadata":
        page.page_state["fields"][0]["sensitive"] = True
    else:
        page.interactive_elements.append({"ref": "secret", "tag": "input", "type": "password",
                                          "name": "Password", "form": "checkout", "has_value": True})
    planner = prepared_planner(page, broken_ref=None)
    state = planner.states[planner.current_key]
    assert not any(item.get("_probe_target") for item in state.candidates.values())
    assert planner._form_probe_action(state) is None
    assert not planner.form_probes_completed
    assert planner._form_probe_setup_attempts == {}


def test_setup_cross_field_mutation_is_bounded_without_fake_input_injection():
    page = form_page(values=(False, True, False))
    planner = prepared_planner(page, broken_ref=None)
    issued = []
    for _ in range(24):
        selected = planner.next_restoration_action(page)
        if selected is None:
            break
        decision, _ = selected
        issued.append(decision)
        # Each healthy-looking fill retains itself but erases a peer, which
        # previously could alternate fills indefinitely without failed_field.
        other = "e2" if decision.target == "e1" else "e1"
        after, result = apply_action(page, decision, broken_ref=None,
                                    cross_clear=other if decision.action == "fill" else None)
        planner.record_action(page, decision, result, after)
        page = after
    assert len(issued) < 24
    assert len([item for item in issued if item.action == "click"]) == 3
    assert all(attempts <= 2 for attempts in planner._form_probe_setup_attempts.values())
    assert not any(item.action not in {"fill", "click"} for item in issued)
    assert planner.can_finish()


def test_target_clear_noop_is_bounded_and_never_submitted_as_an_empty_field():
    page = form_page(values=(True, True, True))
    planner = prepared_planner(page, broken_ref=None)
    clears = []
    for _ in range(12):
        selected = planner.next_restoration_action(page)
        if selected is None:
            break
        decision, _ = selected
        assert decision.action == "fill" and decision.value == ""
        clears.append(decision.target)
        after, result = apply_action(page, decision, broken_ref=None, clear_noop=True)
        planner.record_action(page, decision, result, after)
        page = after
    assert clears == ["e0", "e0", "e1", "e1", "e2", "e2"]
    assert len(planner.form_probes_completed) == 3
    assert {entry["reason"] for entry in planner.drain_restrictions()} == {"form_probe_target_not_empty"}
    assert planner.can_finish() and not planner.coverage_complete()


def test_masked_probe_is_persisted_once_with_live_dependency_metadata():
    page = form_page(values=(True, False, False))
    planner = prepared_planner(page)
    state = planner.states[planner.current_key]
    probes = [item for item in state.candidates.values() if item.get("_probe_target")]
    target = next(item for item in probes if item["_validation_probe"]["name"] == "Zip/Postal Code")
    hint = {**target["_validation_probe"], "strategy": "blocked_peer_observed",
            "isolation_established": False,
            "blocking_fields": [{"field_identity": "field-e1", "name": "Last Name",
                                 "reason": "input_value_not_retained"}]}
    decision = AgentDecision("click", "Verify empty postal field", target="submit",
                             scenario_hint={"validation_probe": hint})
    result = action_result(page, page, decision)
    result.assertions.append(AssertionResult("form_validation_probe_precondition", {},
                                            {"status": "masked"}, False, "Peer masks target"))
    planner.record_action(page, decision, result, page)
    planner.record_action(page, decision, result, page)
    entries = planner.drain_restrictions()
    assert len(entries) == 1
    assert entries[0]["validation_probe"] == hint
    assert planner.drain_restrictions() == []
