import copy

import pytest

from autojev.services.scenario_step_order import capture_position_anchor, presentation_scenario_steps


def original_steps():
    return [
        {"id": "before", "kind": "observation", "title": "Antes da ação", "occurred_at": "2026-10-16T10:00:00Z"},
        {"id": "action", "kind": "action", "title": "Selecionar opção", "success": True, "occurred_at": "2026-10-16T10:00:01Z"},
        {"id": "after", "kind": "observation", "title": "Depois da ação", "occurred_at": "2026-10-16T10:00:02Z"},
        {"id": "option", "kind": "assertion", "assertion": {"kind": "selection_matches", "expected": {"option_matches": True},
                                                                  "actual": {"option_matches": True}, "passed": True}, "occurred_at": "2026-10-16T10:00:02Z"},
        {"id": "sort", "kind": "assertion", "assertion": {"kind": "product_sort_order", "expected": {"by": "name", "descending": True},
                                                                "actual": {"products": ["B", "A"]}, "passed": True}, "occurred_at": "2026-10-16T10:00:02Z"},
        {"id": "errors", "kind": "assertion", "assertion": {"kind": "no_new_browser_errors", "expected": "no errors", "actual": {},
                                                                  "passed": True}, "occurred_at": "2026-10-16T10:00:02Z"},
    ]


def upload(gap=2, order=0, identity="upload", **extra):
    return {"id": identity, "kind": "observation", "title": "Imagem anexada pelo usuário", "source": "user_upload",
            "image_source": "user_upload", "checkpoint_id": "db-1", "planned_gap": gap, "planned_order": order,
            "screenshot": f"{identity}.png", "attached_at": "2026-10-16T10:05:00Z", "occurred_at": "2026-10-16T10:05:00Z", **extra}


@pytest.mark.parametrize("gap", range(7))
def test_legacy_image_projects_at_selected_boundary_without_changing_evidence(gap):
    source = {"timeline": [*original_steps(), upload(gap)], "assertions": [{"passed": True}], "action_succeeded": True}
    snapshot = copy.deepcopy(source)
    projected = presentation_scenario_steps(source)
    assert projected[gap]["id"] == "upload"
    assert [entry["id"] for entry in projected if entry["id"] != "upload"] == [entry["id"] for entry in original_steps()]
    assert projected[gap]["occurred_at"] == projected[gap]["attached_at"] == "2026-10-16T10:05:00Z"
    assert projected[gap]["presentation_position"]["status"] == "planned"
    assert source == snapshot
    assert presentation_scenario_steps(source) == projected
    projected[gap]["screenshot"] = "changed-by-consumer.png"
    assert source == snapshot


def test_same_gap_images_follow_saved_order_not_attachment_time_or_input_order():
    evidence = {"timeline": [*original_steps(), upload(2, 8, "second"), upload(2, 1, "first"), upload(2, 8, "third")]}
    projected = presentation_scenario_steps(evidence)
    assert [entry["id"] for entry in projected] == ["before", "action", "first", "second", "third", "after", "option", "sort", "errors"]


@pytest.mark.parametrize("gap,order", [(True, 0), (-1, 0), (10001, 0), (2, True), (2, -1), (2, "0"), (7, 0)])
def test_invalid_legacy_position_remains_visible_and_explicitly_unresolved(gap, order):
    source = {"timeline": [*original_steps(), upload(gap, order)]}
    result = presentation_scenario_steps(source)
    assert result[-1]["id"] == "upload"
    assert result[-1]["presentation_position"]["status"] == "unresolved"
    assert len(result) == len(source["timeline"])


@pytest.mark.parametrize("extra", [{"missing_original_checks": ["visual_model"]}, {"rerun_blocked_reason": "before_url_changed"}])
def test_partial_legacy_source_does_not_guess_a_numeric_position(extra):
    evidence = {"timeline": [*original_steps(), upload(2)], **extra}
    result = presentation_scenario_steps(evidence)
    assert result[-1]["presentation_position"] == {"status": "unresolved", "gap": 2, "order": 0,
                                                   "method": "legacy_gap", "reason": "source_steps_unavailable"}


@pytest.mark.parametrize("gap", range(7))
def test_new_anchor_matches_reproduced_semantics_not_volatile_ids_or_times(gap):
    steps = original_steps()
    anchor = capture_position_anchor(steps, gap)
    current = copy.deepcopy(steps)
    for entry in current:
        entry["id"] = "new-" + entry["id"]
        entry["occurred_at"] = "2026-10-17T11:00:00Z"
    # A failed observed check is not repaired by the projection.
    current[3]["assertion"]["actual"] = {"option_matches": False}
    current[3]["assertion"]["passed"] = False
    evidence = {"timeline": [*current, upload(gap, position_anchor=anchor)]}
    snapshot = copy.deepcopy(evidence)
    projected = presentation_scenario_steps(evidence)
    assert projected[gap]["id"] == "upload"
    assert projected[gap]["presentation_position"]["method"] == "source_anchor"
    assertion = next(entry["assertion"] for entry in projected if entry["id"] == "new-option")
    assert assertion["passed"] is False and assertion["actual"] == {"option_matches": False}
    assert evidence == snapshot


def test_new_anchor_can_locate_known_slot_despite_a_missing_unrelated_original_check():
    anchor = capture_position_anchor(original_steps(), 2)
    steps = original_steps()[:-1]
    evidence = {"timeline": [*steps, upload(2, position_anchor=anchor)], "missing_original_checks": ["no_new_browser_errors"]}
    assert presentation_scenario_steps(evidence)[2]["id"] == "upload"


@pytest.mark.parametrize("change", ["missing_left", "missing_right", "reversed", "new_between", "changed_expectation"])
def test_missing_or_changed_source_neighbors_never_silently_move_an_image(change):
    steps = original_steps()
    anchor = capture_position_anchor(steps, 4)
    if change == "missing_left":
        steps.pop(3)
    elif change == "missing_right":
        steps.pop(4)
    elif change == "reversed":
        steps[3], steps[4] = steps[4], steps[3]
    elif change == "new_between":
        steps.insert(4, {"id": "extra", "kind": "signal", "title": "New signal"})
    elif change == "changed_expectation":
        steps[3]["assertion"]["expected"] = {"option_matches": False}
    evidence = {"timeline": [*steps, upload(4, position_anchor=anchor)]}
    assert presentation_scenario_steps(evidence)[-1]["presentation_position"]["status"] == "unresolved"


def test_rerun_of_rerun_uses_displayed_source_order_and_does_not_copy_old_user_image():
    first = {"timeline": [*original_steps(), upload(2, identity="old-image")]}
    displayed = presentation_scenario_steps(first)
    assert displayed[2]["id"] == "old-image"
    # Source slot after the following after-observation is gap 4, not the raw
    # chronology's gap 3. Match the selected boundary by its actual neighbors.
    anchor = capture_position_anchor(displayed, 4)
    second = {"timeline": [*original_steps(), upload(4, identity="new-image", position_anchor=anchor)]}
    projected = presentation_scenario_steps(second)
    assert [entry["id"] for entry in projected][:5] == ["before", "action", "after", "new-image", "option"]
    assert not any(entry["id"] == "old-image" for entry in projected)
    assert projected[3]["presentation_position"]["gap"] == 4
    # A boundary *next to* the old image cannot be reproduced without claiming
    # that the historical image was attached again. Keep it unresolved instead.
    second["timeline"][-1]["position_anchor"] = capture_position_anchor(displayed, 3)
    second["timeline"][-1]["planned_gap"] = 3
    assert presentation_scenario_steps(second)[-1]["presentation_position"]["status"] == "unresolved"


def test_identical_assertion_count_mismatch_is_not_rebound_by_ordinal():
    steps = original_steps()
    duplicate = copy.deepcopy(steps[3])
    duplicate["id"] = "duplicate"
    steps.insert(4, duplicate)
    anchor = capture_position_anchor(steps, 4)
    target = {"timeline": [*original_steps(), upload(4, position_anchor=anchor)]}
    assert presentation_scenario_steps(target)[-1]["presentation_position"]["status"] == "unresolved"


@pytest.mark.parametrize("bad", [None, {}, {"version": True}, {"version": 1, "source_count": 6, "gap": 3, "left": None, "right": None}])
def test_malformed_new_anchor_does_not_fall_back_to_legacy_numeric_guess(bad):
    evidence = {"timeline": [*original_steps(), upload(2, position_anchor=bad)]}
    assert presentation_scenario_steps(evidence)[-1]["presentation_position"]["status"] == "unresolved"


def test_projection_does_not_reorder_browser_captures_or_invent_missing_upload_records():
    steps = original_steps()
    steps.append({"id": "automatic", "kind": "observation", "planned_gap": 2, "planned_order": 0, "screenshot": "automatic.png"})
    evidence = {"timeline": steps, "manual_captures": [upload(2)], "presentation_steps": [upload(0)]}
    assert presentation_scenario_steps(evidence) == steps


@pytest.mark.parametrize("gap", [True, -1, 7, "2"])
def test_anchor_builder_rejects_invalid_source_gap(gap):
    with pytest.raises(ValueError):
        capture_position_anchor(original_steps(), gap)


@pytest.mark.asyncio
async def test_rerun_service_persists_source_anchor_and_preflight_reuses_displayed_order(tmp_path, monkeypatch, user_print_image):
    # Exercise the actual service boundary without external pages, models,
    # credentials, server restarts or modifying a user's historical run.
    from test_scenario_rerun import capture_plan, install_browser, make_service, observed, wait_capture, wait_phase

    service, repository, source_id, _, _ = make_service(tmp_path, with_products=True)
    source = repository.get_exploration(source_id)
    source_steps = service._source_timeline(source, "CT001")
    browsers = install_browser(monkeypatch, before=observed("e42", with_products=True),
                               after=observed("e42", selected="za", with_products=True))
    first_id = service.submit(source_id, "CT001", capture_plan=capture_plan(("db-1", 2, 0)))
    task = service.tasks[first_id]
    await wait_phase(service, first_id, "waiting_preparation")
    service.resume(first_id)
    checkpoint = await wait_capture(service, first_id, "db-1")
    service.capture_print(first_id, checkpoint["id"], checkpoint["version"], user_print_image["data_url"])
    await task
    first = repository.get_exploration(first_id)
    evidence = first["scenarios"][0]["evidence"]
    attachment = evidence["manual_captures"][0]
    assert attachment["position_anchor"] == capture_position_anchor(source_steps, 2, source["scenarios"][0]["evidence"])
    displayed = presentation_scenario_steps(evidence)
    assert displayed[2]["id"] == attachment["id"]
    assert displayed[2]["presentation_position"]["status"] == "planned"
    assert evidence["timeline"][-1]["id"] == attachment["id"]  # true attachment chronology retained
    assert [entry["id"] for entry in service._source_timeline(first, "CT001")] == [entry["id"] for entry in displayed]
    preflight = service.preflight(first_id, "CT001")
    assert preflight["timeline_count"] == len(displayed)
    assert preflight["action_index"] == next(index for index, entry in enumerate(displayed) if entry["kind"] == "action")

    # A subsequent source gap includes the prior image, whereas the new
    # execution does not pretend the old attachment was provided again.
    second_id = service.submit(first_id, "CT001", capture_plan=capture_plan(("db-2", 4, 0)))
    task = service.tasks[second_id]
    await wait_phase(service, second_id, "waiting_preparation")
    service.resume(second_id)
    checkpoint = await wait_capture(service, second_id, "db-2")
    service.capture_print(second_id, checkpoint["id"], checkpoint["version"], user_print_image["data_url"])
    await task
    second = repository.get_exploration(second_id)
    second_evidence = second["scenarios"][0]["evidence"]
    second_steps = presentation_scenario_steps(second_evidence)
    second_image = next(entry for entry in second_steps if entry.get("source") == "user_upload")
    assert second_image["planned_gap"] == 4 and second_image["presentation_position"]["status"] == "planned"
    assert second_steps.index(second_image) == 3
    assert all(len(browser.actions) == 1 for browser in browsers)
    assert repository.get_exploration(source_id) == source
