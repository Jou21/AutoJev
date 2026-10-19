"""A completed before-card requires a durable observation of this replay."""
from copy import deepcopy

import pytest
from autojev.services.rerun_initial_observation import (
    INITIAL_OBSERVATION_EVENT,
    initial_observation_from_events,
    with_initial_observation,
)
from autojev.services.rerun_step_progress import build_step_plan, project_step_progress
from autojev.services.scenario_step_order import (
    capture_position_anchor,
    presentation_scenario_steps,
)
from test_rerun_step_progress import current_entries, current_item, source_steps


def initial_entry(**changes):
    return {
        "id": "rerun-source-0-before", "source_step_id": "source-0", "kind": "observation",
        "title": "Antes da ação", "occurred_at": "2026-10-16T17:59:00Z", "url": "https://example.test/inventory.html",
        "screenshot": "initial-before.png", "message": "Estado inicial registrado antes de continuar o roteiro.", **changes,
    }


def initial_event(entry=None, **changes):
    return {"type": INITIAL_OBSERVATION_EVENT,
            "payload": {"scenario_id": "CT001", "entry": initial_entry() if entry is None else entry}, **changes}


def checkpoint_plan(steps=None):
    return build_step_plan(source_steps() if steps is None else steps,
                           {"version": 1, "checkpoints": [{"id": "db-1", "gap": 1, "order": 0}]})


def waiting_capture():
    return {"phase": "waiting_capture", "running": True,
            "capture_checkpoint": {"id": "db-1", "version": 1, "gap": 1, "order": 0}}


def test_gap_or_waiting_phase_alone_never_completes_an_observation():
    item = current_item(status="waiting_input")
    original = deepcopy(item)
    progress = project_step_progress(item, waiting_capture(), checkpoint_plan())
    assert progress["completed"] == 0 and progress["total"] == 7
    assert progress["steps"][0]["state"] == "pending"
    assert progress["steps"][1]["state"] == "waiting"
    assert item == original


@pytest.mark.parametrize("screenshot", ["initial-before.png", None])
def test_explicit_current_event_completes_only_its_initial_step_before_ct_exists(screenshot):
    entry = initial_entry(screenshot=screenshot)
    item = current_item(status="waiting_input", events=[initial_event(entry)])
    item["scenarios"] = []
    original = deepcopy(item)
    progress = project_step_progress(item, waiting_capture(), checkpoint_plan())
    assert progress["completed"] == 1 and progress["total"] == 7
    assert progress["steps"][0]["state"] == "done"
    assert progress["steps"][0]["label"] == ("Captura registrada" if screenshot else "Estado registrado")
    assert progress["steps"][0]["evidence_entry_id"] == entry["id"]
    assert progress["steps"][0]["occurred_at"] == entry["occurred_at"]
    assert progress["steps"][1]["state"] == "waiting"
    assert all(row["state"] == "pending" for row in progress["steps"][2:])
    assert item == original


@pytest.mark.parametrize("change", [
    {"id": "wrong"}, {"source_step_id": "source-1"}, {"kind": "action"},
    {"title": "Depois da ação"}, {"occurred_at": "not a timestamp"}, {"occurred_at": "2026-10-16T17:59:00"},
    {"screenshot": "../other.png"}, {"screenshot": "/private/image.png"}, {"screenshot": "image.svg"},
    {"url": "data:text/html,anything"}, {"url": "https://user:secret@example.test"},
    {"message": "Original scenario passed"}, {"image_source": "user_upload"}, {"success": True},
])
def test_malformed_event_never_becomes_evidence(change):
    item = current_item(status="waiting_input", events=[initial_event(initial_entry(**change))])
    assert initial_observation_from_events(item) is None
    assert project_step_progress(item, waiting_capture(), checkpoint_plan())["completed"] == 0


def test_wrong_scenario_foreign_run_and_unrelated_events_do_not_complete_steps():
    for event in (initial_event(payload={"scenario_id": "CT002", "entry": initial_entry()}),
                  initial_event(exploration_id="old-source-run"),
                  initial_event(type="browser_loaded"),
                  initial_event(payload={"scenario_id": "CT001", "entry": initial_entry(), "passed": True})):
        item = current_item(status="waiting_input", events=[event])
        assert initial_observation_from_events(item) is None
        assert project_step_progress(item, waiting_capture(), checkpoint_plan())["completed"] == 0


def test_first_valid_event_is_immutable_and_return_value_does_not_alias_saved_event():
    first = initial_entry()
    later = initial_entry(screenshot="later-before.png", occurred_at="2026-10-16T18:00:00Z")
    item = current_item(events=[initial_event(initial_entry(kind="action")), initial_event(first), initial_event(later)])
    selected = initial_observation_from_events(item)
    assert selected == first
    selected["screenshot"] = "changed-local-copy.png"
    assert initial_observation_from_events(item) == first


def test_initial_merge_preserves_pre_dispatch_observation_and_never_changes_checks_or_action():
    entries = current_entries()
    initial = initial_entry()
    evidence = {"timeline": entries, "before_screenshot": entries[0]["screenshot"],
                "before_observed_at": entries[0]["occurred_at"], "before_url": initial["url"],
                "assertions": [row["assertion"] for row in entries if row["kind"] == "assertion"],
                "action_started_at": "2026-10-16T18:00:00Z", "action_finished_at": "2026-10-16T18:00:01Z"}
    original = deepcopy(evidence)
    merged = with_initial_observation(evidence, initial)
    assert evidence == original
    assert merged["timeline"][0] == initial
    assert merged["before_screenshot"] == initial["screenshot"]
    assert merged["before_observed_at"] == initial["occurred_at"]
    assert merged["dispatch_before_observation"]["screenshot"] == entries[0]["screenshot"]
    assert merged["dispatch_before_observation"]["occurred_at"] == entries[0]["occurred_at"]
    assert merged["dispatch_before_observation"]["title"] == "Revalidação imediatamente antes da ação"
    assert merged["timeline"][1:] == entries[1:]
    for key in ("assertions", "action_started_at", "action_finished_at"):
        assert merged[key] == original[key]
    assert with_initial_observation(merged, initial) == merged


def test_projection_keeps_initial_evidence_id_and_time_after_final_dispatch_timeline_arrives():
    item = current_item(entries=current_entries(), events=[initial_event()])
    progress = project_step_progress(item, {"phase": "finished"}, checkpoint_plan())
    first = progress["steps"][0]
    assert first["state"] == "done" and first["evidence_entry_id"] == initial_entry()["id"]
    assert first["occurred_at"] == initial_entry()["occurred_at"]
    assert progress["completed"] == 6


def test_selected_print_remains_between_initial_observation_and_action_after_merge():
    source = source_steps()
    capture = {"id": "capture-db-1", "kind": "observation", "image_source": "user_upload", "source": "user_upload",
               "screenshot": "arbitrary-user-image.png", "planned_gap": 1, "planned_order": 0,
               "occurred_at": "2026-10-16T17:59:30Z", "position_anchor": capture_position_anchor(source, 1)}
    entries = current_entries()
    evidence = {"timeline": [*entries, capture], "before_screenshot": entries[0]["screenshot"]}
    merged = with_initial_observation(evidence, initial_entry())
    ordered = presentation_scenario_steps(merged)
    assert [row["kind"] for row in ordered[:3]] == ["observation", "observation", "action"]
    assert ordered[0]["id"] == initial_entry()["id"]
    assert ordered[1]["id"] == capture["id"]
    assert ordered[1]["presentation_position"]["status"] == "planned"
    assert len(ordered) == 7


def test_initial_event_cannot_approve_a_different_or_ambiguous_source_step():
    for steps, entry in ((source_steps(), initial_entry(id="rerun-source-8-before", source_step_id="source-8")),
                         ([source_steps()[0], *source_steps()], initial_entry())):
        item = current_item(status="waiting_input", events=[initial_event(entry)])
        progress = project_step_progress(item, waiting_capture(), checkpoint_plan(steps))
        assert progress["completed"] == 0
        assert all(row["state"] != "done" for row in progress["steps"])


def test_merge_does_not_silently_erase_multiple_before_attempts():
    entries = [current_entries()[0], {**current_entries()[0], "screenshot": "another-before.png"}]
    evidence = {"timeline": entries, "before_screenshot": entries[0]["screenshot"]}
    assert with_initial_observation(evidence, initial_entry()) == evidence
