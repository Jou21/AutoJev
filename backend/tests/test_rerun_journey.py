from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from autojev.domain.models import AgentDecision, Observation
from autojev.services.rerun_journey import (
    build_rerun_journey, required_journey_inputs, validate_rerun_journey,
)
from autojev.services.rerun_recipe import RerunRecipeBlocked, build_rerun_recipe

APP = "https://app.test/catalog"
CHECKS = [{"kind": "title_contains", "expected": "Catalog", "actual": "Catalog", "passed": True,
           "message": "Private model commentary"}]


def action(number, *, url=APP, success=True):
    before = Observation(url=url, title="Catalog", text="Public catalog", page_state={},
                         interactive_elements=[])
    recipe = build_rerun_recipe(before, AgentDecision(action="reload", rationale="Private reasoning",
        assertions=[{"kind": "title_contains", "expected": "Catalog"}]), APP)
    assert recipe["enabled"]
    return {"step_number": number, "decision": {"value": "private credential"},
            "result": {"success": success, "evidence": {"rerun_recipe": recipe}}}


def build(actions=None, count=3, checks=None):
    return build_rerun_journey(actions if actions is not None else [action(i) for i in range(1, count + 1)],
                               count, APP, CHECKS if checks is None else checks)


def test_ordered_complete_prefix_includes_setup_and_failed_final_action():
    actions = [action(3, success=False), action(1), action(4), action(2)]
    original = copy.deepcopy(actions)
    journey = build(actions)
    assert journey["version"] == "ct-journey-v1"
    assert journey["complete"] is True
    assert journey["gaps"] == []
    assert [step["step_number"] for step in journey["steps"]] == [1, 2, 3]
    assert journey["entry_url"] == APP
    assert journey["checks"] == [{"kind": "title_contains", "expected": "Catalog"}]
    validate_rerun_journey(journey, APP)
    assert actions == original
    serialized = json.dumps(journey)
    assert "private credential" not in serialized
    assert "Private reasoning" not in serialized
    assert "Private model commentary" not in serialized
    journey["steps"][0]["recipe"]["decision"]["action"] = "navigate"
    assert actions == original


@pytest.mark.parametrize("kind", ["text_contains", "title_contains", "url_contains", "url_equals"])
def test_direct_checks_require_exact_saved_dispatch_expectations(kind):
    checks = [{"kind": kind, "expected": "Public expectation"}]
    actions = [action(1), action(2)]
    journey = build(actions, count=2, checks=checks)
    assert not journey["complete"]
    assert {"reason": "missing_direct_check_recipe"} in journey["gaps"]
    journey.update(complete=True, gaps=[])
    with pytest.raises(RerunRecipeBlocked, match="missing_direct_check_recipe"):
        validate_rerun_journey(journey, APP)
    actions[0]["result"]["evidence"]["rerun_recipe"]["decision"]["assertions"] = checks
    journey = build(actions, count=2, checks=checks)
    assert journey["complete"]
    validate_rerun_journey(journey, APP)
    journey["checks"][0]["expected"] = "Different expectation"
    with pytest.raises(RerunRecipeBlocked, match="missing_direct_check_recipe"):
        validate_rerun_journey(journey, APP)


def test_direct_check_of_different_kind_or_later_action_is_not_available():
    actions = [action(1), action(2)]
    actions[1]["result"]["evidence"]["rerun_recipe"]["decision"]["assertions"] = [
        {"kind": "text_contains", "expected": "Catalog"}]
    journey = build(actions, count=1, checks=[{"kind": "text_contains", "expected": "Catalog"}])
    assert not journey["complete"]
    assert {"reason": "missing_direct_check_recipe"} in journey["gaps"]


def test_step_outcomes_preserve_expected_failures_without_private_evidence():
    actions = [action(1), action(2, success=False)]
    actions[0]["result"]["assertions"] = [
        {"kind": "title_contains", "expected": "Catalog", "passed": False,
         "actual": "private credential", "message": "private commentary"},
        {"kind": "cart_count", "expected": 2, "passed": True, "actual": 2},
    ]
    journey = build(actions, count=2)
    assert journey["complete"]
    assert journey["steps"][0]["outcome"] == {"success": True, "checks": [
        {"kind": "title_contains", "expected": "Catalog", "passed": False},
        {"kind": "cart_count", "expected": 2, "passed": True},
    ]}
    assert journey["steps"][1]["outcome"] == {"success": False, "checks": []}
    assert "private credential" not in json.dumps(journey)
    assert "private commentary" not in json.dumps(journey)
    validate_rerun_journey(json.loads(json.dumps(journey)), APP)
    for step in journey["steps"]:
        step.pop("outcome")
    validate_rerun_journey(journey, APP)


@pytest.mark.parametrize("checks", [None, [None], [{"kind": "visual_model", "expected": "Fine", "passed": True}],
    [{"kind": "title_contains", "expected": "Catalog"}],
    [{"kind": "title_contains", "expected": "Catalog", "passed": "false"}],
    [{"kind": "title_contains", "expected": "password=secret", "passed": True}],
])
def test_invalid_source_outcome_checks_are_explicit_gaps(checks):
    actions = [action(1)]
    actions[0]["result"]["assertions"] = checks
    journey = build(actions, count=1)
    assert not journey["complete"]
    assert {"step_number": 1, "reason": "invalid_journey_outcome"} in journey["gaps"]
    with pytest.raises(RerunRecipeBlocked):
        validate_rerun_journey(journey, APP)


@pytest.mark.parametrize("outcome", [None, {}, {"success": 1, "checks": []},
    {"success": True, "checks": None}, {"success": True, "checks": [{"kind": "cart_count", "expected": 1}]},
    {"success": True, "checks": [{"kind": "cart_count", "expected": 1, "passed": True, "actual": "private"}]},
    {"success": True, "checks": [], "message": "private"},
])
def test_tampered_outcomes_are_rejected(outcome):
    journey = build(count=1)
    journey["steps"][0]["outcome"] = outcome
    with pytest.raises(RerunRecipeBlocked):
        validate_rerun_journey(journey, APP)


def test_non_boolean_source_success_omits_legacy_outcome():
    actions = [action(1)]
    actions[0]["result"]["success"] = "unknown"
    journey = build(actions, count=1)
    assert "outcome" not in journey["steps"][0]
    validate_rerun_journey(journey, APP)


def test_entry_route_is_first_action_not_final_route():
    journey = build([action(1), action(2, url="https://app.test/cart")], count=2)
    assert journey["entry_url"] == APP
    validate_rerun_journey(journey, APP)


@pytest.mark.parametrize("mode", ["missing", "duplicate", "missing_result", "missing_evidence", "disabled", "malformed", "cross_origin"])
def test_incomplete_recipe_never_silently_disappears(mode):
    actions = [action(1), action(2), action(3)]
    if mode == "missing":
        actions.pop(1)
    elif mode == "duplicate":
        actions.append(action(2))
    elif mode == "missing_result":
        actions[1].pop("result")
    elif mode == "missing_evidence":
        actions[1]["result"].pop("evidence")
    else:
        recipe = actions[1]["result"]["evidence"]["rerun_recipe"]
        if mode == "disabled":
            recipe.update(enabled=False, reason="private credential")
        elif mode == "malformed":
            recipe["before_state_digest"] = "bad"
        else:
            recipe["origin"] = "https://foreign.test"
    journey = build(actions)
    assert journey["complete"] is False
    assert any(gap.get("step_number") == 2 for gap in journey["gaps"])
    assert "private credential" not in json.dumps(journey)
    with pytest.raises(RerunRecipeBlocked):
        validate_rerun_journey(journey, APP)


@pytest.mark.parametrize("checks", [[], None, {}, [None], [{"kind": "visual_model", "expected": "Fine"}],
    [{"kind": "invented", "expected": True}], [{"kind": "title_contains"}],
    [{"kind": "title_contains", "expected": ""}], [{"kind": "title_contains", "expected": 1}],
    [{"kind": "title_contains", "expected": "password=secret"}],
    [{"kind": "title_contains", "expected": "Catalog", "passed": "yes"}],
    [{"kind": "title_contains", "expected": "Catalog", "credential": "private"}],
    [{"kind": "cart_count", "expected": float("nan")}],
])
def test_invalid_empty_private_or_unsupported_checks_are_incomplete(checks):
    journey = build_rerun_journey([action(1)], 1, APP, checks)
    assert journey["complete"] is False
    assert journey["checks"] == []
    with pytest.raises(RerunRecipeBlocked):
        validate_rerun_journey(journey, APP)


def test_failed_check_can_be_repeated_and_business_expectation_is_retained():
    checks = [{"kind": "cart_membership", "expected": {"product": "Backpack", "present": True},
               "actual": {"product": "Private product", "present": False}, "passed": False}]
    journey = build(count=1, checks=checks)
    assert journey["complete"] is True
    assert journey["checks"] == [{"kind": "cart_membership", "expected": checks[0]["expected"]}]
    assert "Private product" not in json.dumps(journey)
    validate_rerun_journey(journey, APP)


@pytest.mark.parametrize("mutate", [
    lambda j: j.update(version="unknown"),
    lambda j: j.update(complete=1),
    lambda j: j.update(gaps=[{"reason": "missing_action"}]),
    lambda j: j.update(action_step=True),
    lambda j: j.update(action_step=4),
    lambda j: j["steps"].reverse(),
    lambda j: j["steps"][0].update(step_number=True),
    lambda j: j["steps"].pop(),
    lambda j: j.update(checks=[]),
    lambda j: j.update(entry_url="https://foreign.test"),
    lambda j: j.update(entry_url="https://app.test/wrong"),
    lambda j: j["steps"][0]["recipe"].update(before_effect_digest="bad"),
    lambda j: j["steps"][0]["recipe"].update(credential="private"),
    lambda j: j.update(credential="private"),
])
def test_tampering_fails_preflight(mutate):
    journey = build()
    mutate(journey)
    with pytest.raises(RerunRecipeBlocked):
        validate_rerun_journey(journey, APP)


@pytest.mark.parametrize("count", [None, True, 0, -1, "2", 1.5])
def test_invalid_action_step_fails_closed(count):
    journey = build_rerun_journey([action(1)], count, APP, CHECKS)
    assert journey["complete"] is False
    with pytest.raises(RerunRecipeBlocked):
        validate_rerun_journey(journey, APP)


@pytest.mark.parametrize("actions", [None, {}, [None], [{"step_number": True}], [{"step_number": 0}]])
def test_malformed_actions_fail_closed(actions):
    journey = build_rerun_journey(actions, 1, APP, CHECKS)
    assert journey["complete"] is False
    with pytest.raises(RerunRecipeBlocked):
        validate_rerun_journey(journey, APP)


@pytest.mark.parametrize("kind", ["no_new_browser_errors", "no_new_application_errors", "observable_state_change"])
def test_standard_local_execution_checks_are_supported(kind):
    journey = build(count=1, checks=[{"kind": kind, "expected": "No new failures", "passed": True}])
    assert journey["complete"]
    validate_rerun_journey(journey, APP)


def test_optional_required_inputs_must_match_actual_recipes():
    journey = build(count=1)
    journey["required_inputs"] = []
    validate_rerun_journey(journey, APP)
    journey["required_inputs"] = ["invented"]
    with pytest.raises(RerunRecipeBlocked, match="invalid_journey_inputs"):
        validate_rerun_journey(journey, APP)


def test_recipe_metadata_is_not_copied_into_durable_journey():
    actions = [action(1)]
    actions[0]["result"]["evidence"]["rerun_recipe"]["credential"] = "private credential"
    journey = build(actions, count=1)
    assert "private credential" not in json.dumps(journey)
    validate_rerun_journey(journey, APP)


def test_guarded_authentication_setup_has_its_own_entry_route_and_inputs():
    from autojev.services.rerun_setup import build_authentication_setup

    before = Observation(url="https://app.test/login", title="Login", text="Sign in", page_state={},
        interactive_elements=[
            {"ref": "e1", "tag": "input", "type": "text", "name": "Username"},
            {"ref": "e2", "tag": "input", "type": "password", "name": "Password"},
            {"ref": "e3", "tag": "button", "name": "Login"},
        ])
    journey = build(count=1)
    journey["setup"] = build_authentication_setup(before, None, APP)
    assert journey["setup"]["enabled"] is True
    journey["entry_url"] = before.url
    journey["required_inputs"] = ["password", "username"]
    assert required_journey_inputs(journey) == ["password", "username"]
    validate_rerun_journey(journey, APP)
    journey["setup"]["credential"] = "never persist"
    with pytest.raises(RerunRecipeBlocked):
        validate_rerun_journey(journey, APP)


def authentication_setup(url="https://app.test/login"):
    from autojev.services.rerun_setup import build_authentication_setup

    before = Observation(url=url, title="Login", text="Sign in", page_state={},
        interactive_elements=[
            {"ref": "e1", "tag": "input", "type": "text", "name": "Username"},
            {"ref": "e2", "tag": "input", "type": "password", "name": "Password"},
            {"ref": "e3", "tag": "button", "name": "Login"},
        ])
    setup = build_authentication_setup(before, None, APP)
    assert setup["enabled"] is True
    return setup


@pytest.mark.parametrize("boundaries", [[0], [1], [0, 1, 2], [1, 1, 2], []])
def test_repeated_authentication_boundaries_are_durable_and_gather_inputs(boundaries):
    journey = build()
    journey["authentications"] = [
        {"after_step": step, "setup": authentication_setup(f"https://app.test/login/{index}")}
        for index, step in enumerate(boundaries)
    ]
    if boundaries and boundaries[0] == 0:
        journey["entry_url"] = journey["authentications"][0]["setup"]["before_url"]
    journey["required_inputs"] = ["password", "username"] if boundaries else []
    validate_rerun_journey(journey, APP)
    restored = json.loads(json.dumps(journey))
    validate_rerun_journey(restored, APP)
    assert required_journey_inputs(restored) == journey["required_inputs"]
    assert restored["authentications"] == journey["authentications"]


@pytest.mark.parametrize("events", [None, {}, [None], [{"after_step": 0, "extra": True}],
    [{"after_step": True}], [{"after_step": -1}], [{"after_step": 3}],
    [{"after_step": 1.0}], [{"after_step": "1"}],
    [{"after_step": 2}, {"after_step": 1}],
])
def test_invalid_authentication_boundaries_fail_closed(events):
    journey = build()
    journey["authentications"] = copy.deepcopy(events)
    if isinstance(events, list):
        for event in journey["authentications"]:
            if isinstance(event, dict):
                event["setup"] = authentication_setup()
    with pytest.raises(RerunRecipeBlocked):
        validate_rerun_journey(journey, APP)
    with pytest.raises(RerunRecipeBlocked):
        required_journey_inputs(journey)


def test_authentication_setup_alias_compatibility_and_conflicts():
    journey = build()
    setup = authentication_setup()
    journey.update(setup=setup, entry_url=setup["before_url"],
                   authentications=[{"after_step": 0, "setup": copy.deepcopy(setup)}])
    validate_rerun_journey(journey, APP)
    journey["authentications"][0]["setup"] = authentication_setup("https://app.test/other-login")
    with pytest.raises(RerunRecipeBlocked, match="conflicting_journey_authentication"):
        validate_rerun_journey(journey, APP)


@pytest.mark.parametrize("change", ["private", "foreign", "wrong_entry"])
def test_mid_journey_authentication_does_not_bypass_safety(change):
    journey = build()
    setup = authentication_setup()
    journey["authentications"] = [{"after_step": 1, "setup": setup}]
    if change == "private":
        setup["password"] = "never persist"
    elif change == "foreign":
        setup["before_url"] = "https://foreign.test/login"
    else:
        journey["entry_url"] = setup["before_url"]
    with pytest.raises(RerunRecipeBlocked):
        validate_rerun_journey(journey, APP)


def test_state_input_aliases_and_capture_marker_survive_roundtrip():
    actions = [action(1)]
    recipe = actions[0]["result"]["evidence"]["rerun_recipe"]
    recipe.update(state_inputs={"b" * 64: "existing_field"}, journey_capture=True)
    journey = build(actions, count=1)
    assert journey["steps"][0]["recipe"]["state_inputs"] == recipe["state_inputs"]
    assert journey["steps"][0]["recipe"]["journey_capture"] is True
    assert required_journey_inputs(journey) == ["existing_field"]
    validate_rerun_journey(json.loads(json.dumps(journey)), APP)


def test_vault_aliases_aggregate_without_persisting_values():
    actions = [action(1), action(2), action(3)]
    for item, alias in zip(actions, ["second_input", "first_input", "second_input"]):
        recipe = item["result"]["evidence"]["rerun_recipe"]
        recipe["decision"].update(action="fill", input_ref=alias)
        recipe["target_fingerprint"] = "a" * 64
    journey = build(actions)
    validate_rerun_journey(journey, APP)
    assert required_journey_inputs(journey) == ["first_input", "second_input"]
    assert required_journey_inputs(build(count=1)) == []


def test_sqlite_json_roundtrip_remains_valid_after_connection_reopen():
    # Use a project-local database, not pytest's system temporary directory.
    path = Path(__file__).parent / f".journey-{uuid4().hex}.sqlite3"
    try:
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE durable (kind TEXT PRIMARY KEY, payload TEXT)")
            db.execute("INSERT INTO durable VALUES (?, ?)", ("actions", json.dumps([action(1), action(2)])))
        with sqlite3.connect(path) as db:
            actions = json.loads(db.execute("SELECT payload FROM durable WHERE kind='actions'").fetchone()[0])
            journey = build(actions, count=2)
            db.execute("INSERT INTO durable VALUES (?, ?)", ("journey", json.dumps(journey)))
        with sqlite3.connect(path) as db:
            restored = json.loads(db.execute("SELECT payload FROM durable WHERE kind='journey'").fetchone()[0])
        assert restored == journey
        validate_rerun_journey(restored, APP)
    finally:
        path.unlink(missing_ok=True)
