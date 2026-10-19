"""The first real observation is durable before a print placed after it."""
from copy import deepcopy

import pytest
import test_scenario_rerun as rerun_fixtures
from autojev.domain.models import utc_now
from autojev.services.rerun_initial_observation import INITIAL_OBSERVATION_EVENT
from autojev.services.rerun_step_progress import project_step_progress
from autojev.services.scenario_step_order import presentation_scenario_steps
from autojev.services.scenario_timeline import prepare_scenario_timelines
from test_scenario_rerun import (
    capture_plan,
    install_browser,
    make_service,
    observed,
    wait_capture,
    wait_phase,
)


@pytest.fixture(autouse=True)
def chronological_browser_results(monkeypatch):
    # The shared legacy fixture fixes action dates in October 2026 while its
    # observations use today's clock. This test needs real chronological gaps.
    original_result = rerun_fixtures.action_result

    def result_at_dispatch(before, after, decision):
        result = original_result(before, after, decision)
        result.started_at = utc_now()
        result.finished_at = utc_now()
        return result

    monkeypatch.setattr(rerun_fixtures, "action_result", result_at_dispatch)


def initial_events(item):
    return [event for event in item["events"] if event["type"] == INITIAL_OBSERVATION_EVENT]


@pytest.mark.asyncio
async def test_initial_card_is_done_before_gap_one_print_without_dispatch(tmp_path, monkeypatch, user_print_image):
    service, repo, source_id, _, reports = make_service(tmp_path, with_products=True)
    source = deepcopy(repo.get_exploration(source_id))
    browsers = install_browser(monkeypatch, before=observed("e42", with_products=True),
                               after=observed("e42", selected="za", with_products=True))
    run_id = service.submit(source_id, "CT001", capture_plan=capture_plan(("db-1", 1, 0)))
    task = service.tasks[run_id]
    await wait_phase(service, run_id, "waiting_preparation")
    assert initial_events(repo.get_exploration(run_id)) == []
    service.resume(run_id)
    checkpoint = await wait_capture(service, run_id, "db-1")
    paused = repo.get_exploration(run_id)
    progress = project_step_progress(paused, service.state_for(run_id))
    assert progress["completed"] == 1 and progress["total"] == 7
    assert progress["steps"][0]["title"] == "Antes da ação"
    assert progress["steps"][0]["state"] == "done"
    assert progress["steps"][1]["state"] == "waiting"
    assert progress["steps"][2]["state"] == "pending"
    assert progress["current_id"] == "checkpoint-db-1"
    assert len(initial_events(paused)) == 1
    initial = initial_events(paused)[0]["payload"]["entry"]
    assert progress["steps"][0]["occurred_at"] == initial["occurred_at"]
    event_types = [event["type"] for event in paused["events"]]
    assert event_types.index(INITIAL_OBSERVATION_EVENT) < event_types.index("waiting_capture")
    assert not paused["actions"] and not browsers[0].actions
    assert not paused["scenarios"]  # No premature CT verdict.
    assert not service.capture_events[run_id].is_set()
    for _ in range(3):
        project_step_progress(repo.get_exploration(run_id), service.state_for(run_id))
    assert len(initial_events(repo.get_exploration(run_id))) == 1
    service.capture_print(run_id, checkpoint["id"], checkpoint["version"], user_print_image["data_url"])
    await task
    finished = repo.get_exploration(run_id)
    assert len(initial_events(finished)) == 1
    assert len(browsers[0].actions) == 1
    assert project_step_progress(finished, service.state_for(run_id))["completed"] == 7
    assert repo.get_exploration(source_id) == source and len(reports) == 1


@pytest.mark.asyncio
async def test_gap_zero_stays_pending_then_initial_record_survives_fresh_guard_observations(
    tmp_path, monkeypatch, user_print_image,
):
    service, repo, source_id, _, _ = make_service(tmp_path, with_products=True)
    browsers = install_browser(monkeypatch, before=observed("e42", with_products=True),
                               after=observed("e42", selected="za", with_products=True))
    run_id = service.submit(source_id, "CT001", capture_plan=capture_plan(("db-1", 0, 0), ("db-2", 1, 0)))
    task = service.tasks[run_id]
    await wait_phase(service, run_id, "waiting_preparation")
    original_observe = browsers[0].observe
    observations = []

    async def distinct_observations(label):
        observation = await original_observe(label)
        ordinal = len(observations) + 1
        observation.screenshot = f"new-observation-{ordinal}.png"
        observation.captured_at = utc_now()
        observations.append(deepcopy(observation))
        return observation

    browsers[0].observe = distinct_observations
    service.resume(run_id)
    first = await wait_capture(service, run_id, "db-1")
    before_first = repo.get_exploration(run_id)
    assert initial_events(before_first) == []
    progress = project_step_progress(before_first, service.state_for(run_id))
    assert progress["completed"] == 0
    assert progress["steps"][0]["state"] == "waiting"
    assert progress["steps"][1]["state"] == "pending"
    service.capture_print(run_id, first["id"], first["version"], user_print_image["data_url"])
    second = await wait_capture(service, run_id, "db-2")
    initial = initial_events(repo.get_exploration(run_id))[0]["payload"]["entry"]
    assert initial["screenshot"] == observations[1].screenshot
    assert not browsers[0].actions
    service.capture_print(run_id, second["id"], second["version"], user_print_image["data_url"])
    await task
    item = repo.get_exploration(run_id)
    evidence = item["scenarios"][0]["evidence"]
    entries = presentation_scenario_steps(evidence)
    before = [entry for entry in entries if entry.get("title") == "Antes da ação"]
    assert len(before) == 1 and before[0] == initial
    assert evidence["before_screenshot"] == initial["screenshot"]
    assert evidence["dispatch_before_observation"]["screenshot"] == observations[2].screenshot
    assert item["actions"][0]["result"]["evidence"]["before_screenshot"] == observations[2].screenshot
    assert [entry.get("checkpoint_id") for entry in entries[:4]] == ["db-1", None, "db-2", None]
    assert all(entry.get("presentation_position", {}).get("status") == "planned"
               for entry in entries if entry.get("image_source") == "user_upload")
    prepared = prepare_scenario_timelines(item)
    assert len(presentation_scenario_steps(prepared["scenarios"][0]["evidence"])) == len(entries)
    assert project_step_progress(item, service.state_for(run_id))["completed"] == 8
    assert len(browsers[0].actions) == 1


@pytest.mark.asyncio
async def test_cancellation_after_initial_record_keeps_only_that_card_completed(tmp_path, monkeypatch):
    service, repo, source_id, _, _ = make_service(tmp_path)
    browsers = install_browser(monkeypatch)
    run_id = service.submit(source_id, "CT001", capture_plan=capture_plan(("db-1", 1, 0)))
    task = service.tasks[run_id]
    await wait_phase(service, run_id, "waiting_preparation")
    service.resume(run_id)
    await wait_capture(service, run_id, "db-1")
    initial = initial_events(repo.get_exploration(run_id))[0]["payload"]["entry"]
    service.cancel(run_id)
    await task
    item = repo.get_exploration(run_id)
    progress = project_step_progress(item, service.state_for(run_id))
    assert progress["completed"] == 1
    assert progress["steps"][0]["state"] == "done"
    assert progress["steps"][0]["evidence_entry_id"] == initial["id"]
    assert all(row["state"] != "done" for row in progress["steps"][1:])
    assert not item["actions"] and not browsers[0].actions
    assert item["scenarios"][0]["status"] == "BLOCKED"
    assert initial in item["scenarios"][0]["evidence"]["timeline"]
    assert browsers[0].closed
