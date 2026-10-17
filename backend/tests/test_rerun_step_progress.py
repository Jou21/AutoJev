from copy import deepcopy

import pytest

from autojev.api.routes import _attach_rerun_state, _exploration_payload
from autojev.services.rerun_step_progress import build_step_plan, project_step_progress, progress_check_bindings
from autojev.services.scenario_timeline import TIMELINE_VERSION
from test_scenario_rerun import make_service, install_browser, wait_phase


def source_steps():
    return [
        {"kind": "observation", "title": "Antes da ação", "screenshot": "old-before.png"},
        {"kind": "action", "title": "Selecionar opção", "success": True, "message": "private old action"},
        {"kind": "observation", "title": "Depois da ação", "screenshot": "old-after.png"},
        {"kind": "assertion", "title": "Opção selecionada", "assertion": {
            "kind": "selection_matches", "expected": {"option_matches": True}, "actual": {"private": "old actual"}, "passed": True}},
        {"kind": "assertion", "title": "Ordenação dos produtos", "assertion": {
            "kind": "product_sort_order", "expected": {"by": "name"}, "actual": {"private": "old actual"}, "passed": True}},
        {"kind": "assertion", "title": "Ausência de novas falhas técnicas da aplicação", "assertion": {
            "kind": "no_new_browser_errors", "expected": "No new errors", "actual": "old actual", "passed": True}},
    ]


def plan():
    return build_step_plan(source_steps(), {"version": 1, "checkpoints": [
        {"id": "db-1", "gap": 2, "order": 0}, {"id": "db-2", "gap": 2, "order": 1},
    ]})


def current_item(*, status="running", entries=None, events=None):
    return {"id": "new", "status": status, "timeline_version": TIMELINE_VERSION, "events": events or [],
            "scenarios": [{"id": "CT001", "evidence": {"timeline": entries or []}}]}


def current_entries():
    entries = source_steps()
    for entry in entries:
        entry["occurred_at"] = "2026-10-16T18:00:00Z"
        if "screenshot" in entry:
            entry["screenshot"] = "new-" + entry["screenshot"]
        if entry["kind"] == "assertion":
            entry["assertion"]["actual"] = "current value"
    return entries


def test_safe_plan_has_exact_script_order_but_no_original_results_or_values():
    result = plan()
    assert [step["id"] for step in result["steps"]] == ["source-0", "source-1", "checkpoint-db-1", "checkpoint-db-2", "source-2", "source-3", "source-4", "source-5"]
    assert [step["title"] for step in result["steps"]][:5] == ["Antes da ação", "Selecionar opção", "Aguardar print", "Aguardar print", "Depois da ação"]
    encoded = str(result)
    for forbidden in ("old actual", "option_matches", "private old", "old-before.png", "passed", "success", "actual", "'expected':"):
        assert forbidden not in encoded
    assert "expected_sha256" in encoded


def test_waiting_preparation_does_not_inherit_a_single_original_success():
    item = current_item(status="waiting_input")
    unchanged = deepcopy(item)
    result = project_step_progress(item, {"phase": "waiting_preparation", "step_plan": plan()})
    assert result["completed"] == 0
    assert result["current_id"] == "source-0"
    assert result["steps"][0]["state"] == "waiting"
    assert all(step["state"] == "pending" for step in result["steps"][1:])
    assert item == unchanged


def test_wait_after_action_uses_real_batch_results_without_skipping_manual_wait():
    item = current_item(status="waiting_input", entries=current_entries())
    state = {"phase": "waiting_capture", "capture_checkpoint": {"id": "db-1"}, "captures_completed": 2}
    unchanged = deepcopy(item)
    result = project_step_progress(item, state, plan())
    assert result["steps"][2]["state"] == "waiting"
    assert result["steps"][3]["state"] == "pending"
    assert result["completed"] == 6  # The local-check batch is already saved.
    assert result["current_id"] == "checkpoint-db-1"
    assert "juntas" in result["notice"]
    assert item == unchanged


def test_action_false_failed_check_false_missing_result_and_dispatch_only_are_not_green():
    entries = current_entries()
    entries[1]["success"] = False
    entries[3]["assertion"]["passed"] = False
    entries[4]["assertion"].pop("passed")
    result = project_step_progress(current_item(entries=entries), {"phase": "executing"}, plan())
    assert result["steps"][1]["state"] == "failed"
    assert result["steps"][5]["state"] == "failed"
    assert result["steps"][6]["state"] == "unavailable"
    result = project_step_progress(current_item(), {"phase": "executing", "action_dispatched": True}, plan())
    assert result["steps"][1]["state"] == "active"
    assert result["completed"] == 0


def test_only_committed_current_upload_completes_own_checkpoint_even_if_it_is_not_yet_in_ct():
    upload = {"kind": "observation", "image_source": "user_upload", "checkpoint_id": "db-1",
              "screenshot": "user-upload.png", "attached_at": "2026-10-16T18:03:05Z"}
    item = current_item(status="waiting_input", events=[{"type": "manual_print_attached", "payload": upload}])
    result = project_step_progress(item, {"phase": "waiting_capture", "capture_checkpoint": {"id": "db-2"}}, plan())
    assert result["steps"][2]["state"] == "done"
    assert result["steps"][2]["occurred_at"] == upload["attached_at"]
    assert result["steps"][3]["state"] == "waiting"
    item["events"][0]["type"] = "not_committed"
    result = project_step_progress(item, {"phase": "capturing_print", "capture_checkpoint": {"id": "db-1"}}, plan())
    assert result["steps"][2]["state"] == "active"
    assert result["completed"] == 0


@pytest.mark.parametrize("status", ["partial", "cancelled", "failed"])
def test_interrupted_pending_steps_blocked_but_saved_result_remains(status):
    entries = current_entries()[:2]
    result = project_step_progress(current_item(status=status, entries=entries), {"phase": "finished"}, plan())
    assert result["steps"][0]["state"] == "done"
    assert result["steps"][1]["state"] == "done"
    assert all(step["state"] == "blocked" for step in result["steps"][2:])


def test_old_upload_and_visual_review_never_reused_as_current_pass():
    source = source_steps() + [
        {"kind": "observation", "image_source": "user_upload", "title": "old upload", "checkpoint_id": "db-1"},
        {"kind": "assertion", "assertion": {"kind": "visual_model", "expected": True, "passed": True}},
    ]
    safe_plan = build_step_plan(source, {})
    result = project_step_progress(current_item(status="completed", entries=current_entries()), {"phase": "finished"}, safe_plan)
    assert result["steps"][-2]["state"] == "unavailable"
    assert result["steps"][-1]["state"] == "skipped"
    assert result["completed"] == 6


def test_duplicate_assertion_and_different_expected_dont_gain_false_green():
    source = source_steps()
    source.append(deepcopy(source[3]))
    safe_plan = build_step_plan(source, {})
    result = project_step_progress(current_item(status="completed", entries=current_entries()), {"phase": "finished"}, safe_plan)
    assert result["steps"][3]["state"] == "unavailable"
    assert result["steps"][-1]["state"] == "unavailable"
    entries = current_entries()
    entries[3]["assertion"]["expected"] = "A different request"
    result = project_step_progress(current_item(status="completed", entries=entries), {"phase": "finished"}, build_step_plan(source_steps(), {}))
    assert result["steps"][3]["state"] == "unavailable"


@pytest.mark.parametrize("phase", ["waiting_capture", "capturing_print"])
@pytest.mark.parametrize("status", ["partial", "cancelled", "failed", "completed"])
def test_terminal_stale_capture_state_cannot_leave_card_running(status, phase):
    result = project_step_progress(current_item(status=status),
        {"phase": phase, "capture_checkpoint": {"id": "db-1"}}, plan())
    assert all(step["state"] not in {"active", "waiting", "pending"} for step in result["steps"])
    assert result["completed"] == 0
    assert "current_id" not in result


def test_authorized_element_visible_rebinding_uses_digests_not_kind_only():
    source = [{"kind": "assertion", "assertion": {"kind": "element_visible", "expected": "e-original", "passed": True}}]
    current = [{"kind": "assertion", "assertion": {"kind": "element_visible", "expected": "e-current", "passed": True}}]
    safe_plan = build_step_plan(source, {})
    item = current_item(status="completed", entries=current)
    assert project_step_progress(item, {}, safe_plan)["steps"][0]["state"] == "unavailable"
    state = {"step_check_bindings": progress_check_bindings(
        {"decision": {"assertions": [{"kind": "element_visible", "expected": "e-original"}]}},
        [{"kind": "element_visible", "expected": "e-current"}])}
    assert "e-original" not in str(state) and "e-current" not in str(state)
    assert project_step_progress(item, state, safe_plan)["steps"][0]["state"] == "done"


def test_api_legacy_plan_fallback_read_only_and_source_missing_explained(tmp_path):
    service, repository, source_id, _, _ = make_service(tmp_path)
    source = repository.get_exploration(source_id)
    item = current_item(status="waiting_input")
    state = {"source_exploration_id": source_id, "source_scenario_id": "CT001", "phase": "waiting_preparation", "capture_plan": {}}
    result = _attach_rerun_state(_exploration_payload(item), state, repository)
    assert result["rerun"]["step_progress"]["total"] >= 4
    assert result["rerun"]["step_progress"]["completed"] == 0
    assert "step_plan" not in state
    assert repository.get_exploration(source_id) == source
    state["source_exploration_id"] = "missing-source"
    missing = _attach_rerun_state(_exploration_payload(item), state, repository)
    assert missing["rerun"]["step_progress"]["total"] == 0
    assert "não está disponível" in missing["rerun"]["step_progress"]["notice"]


@pytest.mark.asyncio
async def test_new_rerun_persists_safe_plan_and_finishes_from_current_results(tmp_path, monkeypatch):
    service, repository, source_id, _, _ = make_service(tmp_path)
    install_browser(monkeypatch)
    new_id = service.submit(source_id, "CT001")
    await wait_phase(service, new_id, "waiting_preparation")
    snapshot = service.state_for(new_id)
    assert snapshot["step_plan"]["version"] == 1
    queued = _attach_rerun_state(_exploration_payload(repository.get_exploration(new_id)), snapshot, repository)
    assert queued["rerun"]["step_progress"]["completed"] == 0
    task = service.tasks[new_id]
    service.resume(new_id)
    await task
    completed = _attach_rerun_state(_exploration_payload(repository.get_exploration(new_id)), service.state_for(new_id), repository)
    progress = completed["rerun"]["step_progress"]
    assert progress["completed"] == progress["total"]
    assert all(step["state"] == "done" for step in progress["steps"])
