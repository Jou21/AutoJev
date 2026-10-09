"""Action target captures remain local, correctly attributed execution facts."""

from copy import deepcopy

import pytest

from autojev.domain.models import ActionResult, AgentDecision, Observation
from autojev.services.scenario_timeline import (
    TIMELINE_VERSION,
    prepare_scenario_timelines,
)
from autojev.services.technical_signals import (
    build_scenario_evidence,
    scenario_screenshots,
)
from autojev.services.visual_review_input import build_visual_review_input


def preview(step=1):
    return {
        "version": "action-target-v1",
        "screenshot": f"{step:03d}-action-target.png",
        "captured_at": "2026-10-08T12:00:01+00:00",
        "url": "https://example.test/items",
        "action": "click",
        "target": f"e{step}",
        "coordinate_space": "screenshot-css-pixels",
        "image": {"width": 1440, "height": 900},
        "box": {"x": 1040, "y": 120, "width": 180, "height": 40},
    }


def inputs(step=1):
    observation = Observation(
        url="https://example.test/items", title="Products", text="Products",
        interactive_elements=[], screenshot="after.png",
    )
    decision = AgentDecision(action="click", rationale="Open cart", target=f"e{step}")
    result = ActionResult(
        action="click", success=True, started_at="2026-10-08T12:00:02+00:00",
        finished_at="2026-10-08T12:00:04+00:00",
        before_url=observation.url, after_url="https://example.test/cart",
        target=decision.target, screenshot="after.png",
        evidence={"action_step": step, "action_preview": preview(step)},
    )
    return observation, decision, result


def exploration(*steps):
    return {
        "app_url": "https://example.test", "status": "completed",
        "summary": {"total_scenarios": 1, "pass": 1, "fail": 0},
        "actions": [
            {
                "step_number": step,
                "decision": {"action": "click", "target": f"e{step}"},
                "result": inputs(step)[2].to_dict(),
            }
            for step in steps
        ],
        "scenarios": [{
            "id": "CT001", "status": "PASS", "screenshots": ["after.png"],
            "evidence": {"action_steps": list(steps)},
        }],
        "screenshots": [], "issues": [],
    }


def action_events(data):
    return [
        entry
        for entry in data["scenarios"][0]["evidence"]["timeline"]
        if entry["kind"] == "action"
    ]


def test_new_scenario_action_preserves_preview_and_capture_without_mutation():
    observation, decision, result = inputs()
    original = deepcopy(result.evidence)
    evidence = build_scenario_evidence(observation, decision, result)
    event = next(entry for entry in evidence["timeline"] if entry["kind"] == "action")
    assert event["action_preview"] == preview()
    assert "001-action-target.png" in scenario_screenshots(evidence, result)
    assert result.evidence == original
    event["action_preview"]["box"]["x"] = 0
    assert evidence["action_preview"]["box"]["x"] == 1040
    assert result.evidence == original


def test_retry_draft_does_not_assign_latest_capture_to_previous_action():
    observation, decision, result = inputs(2)
    previous = {
        "kind": "action", "step_number": 1, "action_step": 1,
        "occurred_at": result.started_at, "target": "e2",
        "action_preview": preview(1),
    }
    current = {
        "kind": "action", "step_number": 2,
        "occurred_at": result.started_at, "target": "e2",
    }
    result.evidence["timeline"] = [previous, current]
    evidence = build_scenario_evidence(observation, decision, result)
    assert evidence["timeline"][0]["action_preview"] == preview(1)
    assert evidence["timeline"][1]["action_preview"] == preview(2)
    assert "action_preview" not in current


def test_missing_previous_preview_is_not_filled_from_latest_attempt():
    observation, decision, result = inputs(2)
    result.evidence["timeline"] = [
        {"kind": "action", "step_number": 1, "target": "e1"},
        {"kind": "action", "action_step": 2, "target": "e2"},
    ]
    evidence = build_scenario_evidence(observation, decision, result)
    assert "action_preview" not in evidence["timeline"][0]
    assert evidence["timeline"][1]["action_preview"] == preview(2)


def test_unindexed_action_requires_unique_timestamp_and_target_match():
    observation, decision, result = inputs()
    result.evidence.pop("action_step")
    result.evidence["timeline"] = [
        {"kind": "action", "occurred_at": result.started_at, "target": decision.target},
        {"kind": "action", "occurred_at": result.started_at, "target": "different"},
    ]
    evidence = build_scenario_evidence(observation, decision, result)
    assert evidence["timeline"][0]["action_preview"] == preview()
    assert "action_preview" not in evidence["timeline"][1]
    result.evidence["timeline"][1]["target"] = decision.target
    evidence = build_scenario_evidence(observation, decision, result)
    assert all("action_preview" not in item for item in evidence["timeline"])


def test_legacy_projection_copies_individual_action_previews_and_keeps_verdict():
    original = exploration(1, 2)
    before = deepcopy(original)
    projected = prepare_scenario_timelines(original)
    events = action_events(projected)
    assert [entry["action_preview"] for entry in events] == [preview(1), preview(2)]
    assert original == before
    assert projected["summary"] == original["summary"]
    assert projected["scenarios"][0]["status"] == "PASS"
    assert set(projected["scenarios"][0]["screenshots"]) == {
        "after.png", "001-action-target.png", "002-action-target.png",
    }
    events[0]["action_preview"]["box"]["x"] = 0
    assert original == before
    assert events[1]["action_preview"]["box"]["x"] == 1040


@pytest.mark.parametrize("already_projected", [False, True])
def test_modern_timeline_enrichment_uses_exact_step_and_preserves_original(already_projected):
    original = exploration(1, 2)
    if already_projected:
        original["timeline_version"] = TIMELINE_VERSION
    original["scenarios"][0]["evidence"].update({
        "verdict": {"kind": "validated", "explanation": "Original"},
        "timeline": [
            {"kind": "action", "step_number": 1, "id": "first"},
            {"kind": "action", "action_step": 2, "id": "second"},
            {"kind": "action", "step_number": 3, "id": "unknown"},
            {"kind": "observation", "step_number": 1, "id": "before"},
        ],
    })
    before = deepcopy(original)
    projected = prepare_scenario_timelines(original)
    events = action_events(projected)
    assert events[0]["action_preview"] == preview(1)
    assert events[1]["action_preview"] == preview(2)
    assert "action_preview" not in events[2]
    assert original == before
    if already_projected:
        assert projected["scenarios"][0]["evidence"]["verdict"] == before["scenarios"][0]["evidence"]["verdict"]
    assert prepare_scenario_timelines(projected) == projected


def test_duplicate_step_records_and_unindexed_multi_action_do_not_guess_preview():
    original = exploration(1)
    original["timeline_version"] = TIMELINE_VERSION
    original["actions"].append(deepcopy(original["actions"][0]))
    original["scenarios"][0]["evidence"].update({
        "action_step": 1,
        "timeline": [
            {"kind": "action", "step_number": 1},
            {"kind": "action"},
        ],
    })
    assert all("action_preview" not in entry for entry in action_events(prepare_scenario_timelines(original)))


def test_existing_action_preview_is_not_replaced_or_aliased():
    original = exploration(1)
    original["timeline_version"] = TIMELINE_VERSION
    saved = {**preview(1), "screenshot": "original-target.png"}
    original["scenarios"][0]["evidence"]["timeline"] = [
        {"kind": "action", "step_number": 1, "action_preview": saved},
    ]
    projected = prepare_scenario_timelines(original)
    event = action_events(projected)[0]
    assert event["action_preview"]["screenshot"] == "original-target.png"
    event["action_preview"]["box"]["x"] = 42
    assert saved["box"]["x"] == 1040


@pytest.mark.parametrize("stored_preview", [None, "invalid", {}])
def test_projection_never_invents_missing_capture_coordinates(stored_preview):
    original = exploration(1)
    original["actions"][0]["result"]["evidence"]["action_preview"] = stored_preview
    event = action_events(prepare_scenario_timelines(original))[0]
    assert event.get("action_preview") == ({} if stored_preview == {} else None)
    assert "box" not in event.get("action_preview", {})


def test_action_preview_is_local_only_and_not_in_visual_prompt_input():
    observation, decision, result = inputs()
    result.evidence = build_scenario_evidence(observation, decision, result)
    payload = build_visual_review_input(observation, decision, result, 1)
    assert "action_preview" not in payload["result"]["evidence"]
    assert "timeline" not in payload["result"]["evidence"]
    assert "001-action-target.png" not in str(payload)


@pytest.mark.parametrize("status", ["confirmed", "mismatch", "unverified"])
def test_selection_labels_follow_their_action_without_mutating_verdict_or_leaking_to_llm(status):
    observation, decision, result = inputs()
    decision.action = result.action = "select"
    selection = {
        "version": "select-labels-v1", "requested_label": "Nome (Z a A)",
        "before_label": "Nome (A a Z)", "status": status,
    }
    if status != "unverified":
        selection["after_label"] = "Nome (Z a A)" if status == "confirmed" else "Nome (A a Z)"
    result.evidence["action_preview"].update(action="select", selection=selection)
    original = deepcopy(result.evidence)
    evidence = build_scenario_evidence(observation, decision, result)
    event = next(entry for entry in evidence["timeline"] if entry["kind"] == "action")
    assert event["action_preview"]["selection"] == selection
    event["action_preview"]["selection"]["requested_label"] = "Outra opção"
    assert result.evidence == original
    assert evidence["action_preview"]["selection"] == selection
    result.evidence = evidence
    payload = build_visual_review_input(observation, decision, result, 1)
    assert "selection" not in payload["result"]["evidence"]
    assert "Nome (Z a A)" not in str(payload)


def test_projection_keeps_different_dropdown_options_for_individual_attempts():
    original = exploration(1, 2)
    for attempt, action in enumerate(original["actions"], 1):
        action["decision"]["action"] = action["result"]["action"] = "select"
        action["result"]["evidence"]["action_preview"].update(
            action="select", selection={
                "version": "select-labels-v1", "requested_label": f"Opção {attempt}",
                "after_label": f"Opção {attempt}", "status": "confirmed",
            },
        )
    backup = deepcopy(original)
    projected = prepare_scenario_timelines(original)
    assert [entry["action_preview"]["selection"]["requested_label"] for entry in action_events(projected)] == [
        "Opção 1", "Opção 2",
    ]
    assert projected["summary"] == original["summary"]
    assert projected["scenarios"][0]["status"] == "PASS"
    assert original == backup
