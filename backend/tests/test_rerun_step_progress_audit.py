"""Independent regression checks for live-board evidence claims.

These projections are deliberately exercised with no browser, running user CT
or source database. Old verdicts and timestamps are never replay authority.
"""

from copy import deepcopy
import json

from autojev.services.rerun_step_progress import build_step_plan, project_step_progress
from autojev.services.scenario_timeline import TIMELINE_VERSION


def item(entries=(), *, status="running", events=()):
    return {
        "id": "this-replay", "status": status, "timeline_version": TIMELINE_VERSION,
        "scenarios": [{"id": "CT001", "evidence": {"timeline": deepcopy(list(entries))}}],
        "events": deepcopy(list(events)),
    }


def assertion(expected=True, passed=True):
    return {
        "id": "new-check", "kind": "assertion", "title": "Opção selecionada",
        "occurred_at": "2026-10-16T15:00:02Z",
        "assertion": {"kind": "selection_matches", "expected": expected, "actual": expected, "passed": passed},
    }


def test_unknown_observation_with_before_word_is_not_a_replayed_before_capture():
    source = [{"kind": "observation", "title": "Estado anterior do carrinho", "screenshot": "old-cart.png"}]
    current = [{"kind": "observation", "title": "Antes da ação", "screenshot": "new-before.png"}]
    progress = project_step_progress(item(current, status="completed"), {"phase": "finished"}, build_step_plan(source, {}))
    assert progress["steps"][0]["state"] == "unavailable"
    assert progress["completed"] == 0


def test_duplicate_checks_bind_their_own_occurrence_and_do_not_share_first_verdict():
    source = [assertion(), assertion()]
    current = [assertion(passed=True), assertion(passed=False)]
    progress = project_step_progress(item(current, status="completed"), {"phase": "finished"}, build_step_plan(source, {}))
    assert [row["state"] for row in progress["steps"]] == ["done", "failed"]
    assert progress["completed"] == 1


def test_a_forged_completed_counter_without_a_committed_image_does_not_finish_wait():
    plan = build_step_plan([], {"checkpoints": [{"id": "db-1", "gap": 0, "order": 0}]})
    progress = project_step_progress(item(), {
        "phase": "capturing_print", "capture_checkpoint": {"id": "db-1"}, "captures_completed": 100,
    }, plan)
    assert progress["steps"][0]["state"] == "active"
    assert progress["completed"] == 0


def test_an_upload_only_finishes_its_own_checkpoint_without_altering_its_real_timestamp():
    plan = build_step_plan([], {"checkpoints": [
        {"id": "db-1", "gap": 0, "order": 0}, {"id": "db-2", "gap": 0, "order": 1},
    ]})
    payload = {
        "checkpoint_id": "db-2", "image_source": "user_upload", "screenshot": "new-user.png",
        "attached_at": "2026-10-16T15:10:11Z",
    }
    current = item(events=[{"type": "manual_print_attached", "payload": payload}])
    unchanged = deepcopy(current)
    progress = project_step_progress(current, {"phase": "waiting_capture", "capture_checkpoint": {"id": "db-1"}}, plan)
    assert [row["state"] for row in progress["steps"]] == ["waiting", "done"]
    assert "occurred_at" not in progress["steps"][0]
    assert progress["steps"][1]["occurred_at"] == "2026-10-16T15:10:11Z"
    assert current == unchanged


def test_the_safe_plan_and_public_progress_exclude_source_values_images_and_verdicts():
    source = [assertion(expected={"private-value": "source-password-123"})]
    source[0].update(screenshot="source-private.png", message="source-secret-message", occurred_at="2000-01-01T00:00:00Z")
    plan = build_step_plan(source, {})
    current = item()
    progress = project_step_progress(current, {"phase": "waiting_preparation"}, plan)
    encoded = json.dumps({"plan": plan, "progress": progress})
    for forbidden in ("source-password-123", "source-private.png", "source-secret-message", "2000-01-01", '"passed"', '"actual"'):
        assert forbidden not in encoded
    assert progress["completed"] == 0


def test_a_successful_old_source_never_overrides_new_unconfirmed_action_or_failed_check():
    source = [{"kind": "action", "title": "Selecionar opção", "success": True}, assertion(passed=True)]
    current = [{"kind": "action", "title": "Selecionar opção"}, assertion(passed=False)]
    progress = project_step_progress(item(current, status="partial"), {"phase": "finished"}, build_step_plan(source, {}))
    assert [row["state"] for row in progress["steps"]] == ["unavailable", "failed"]
    assert progress["completed"] == 0
