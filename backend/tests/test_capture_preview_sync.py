"""Ephemeral, checkpoint-scoped image selection shared by trusted surfaces."""
import asyncio
import copy
import json
import threading

import pytest

import autojev.services.scenario_rerun as rerun_module
from autojev.services.user_image_upload import UploadedImageError, validate_user_image
from test_rerun_browser_panel_service import install_panel_browser, wait_panel
from test_scenario_rerun import capture_plan, make_service, wait_capture, wait_phase
from test_scenario_rerun_api import runtime  # noqa: F401 - shared local API fixture


def waiting_service(tmp_path):
    service, repository, run_id, _, _ = make_service(tmp_path)
    repository.update_exploration(run_id, status="waiting_input")
    service.states[run_id] = {
        "running": True, "phase": "waiting_capture", "capture_action_index": 2,
        "capture_checkpoint": {"id": "db-1", "version": 1, "gap": 1, "order": 0},
    }
    service.capture_events[run_id] = asyncio.Event()
    service.capture_records[run_id] = []
    return service, repository, run_id


@pytest.mark.asyncio
async def test_preview_stages_one_image_in_memory_without_evidence_or_advance(tmp_path, user_print_image):
    service, repository, run_id = waiting_service(tmp_path)
    original = repository.get_exploration(run_id)
    state = copy.deepcopy(service.states[run_id])
    assert service.preview_for(run_id, "db-1", 1) == {
        "checkpoint_id": "db-1", "checkpoint_version": 1, "revision": 0, "changed": True, "image": None}
    staged = await service.preview_from_browser_panel(run_id, "db-1", 1, 0, user_print_image["data_url"])
    assert staged["revision"] == 1 and staged["changed"] is True
    assert staged["image"] == {"data_url": user_print_image["data_url"], "name": "Print sincronizado",
                               "size": len(user_print_image["bytes"])}
    assert service.preview_for(run_id, "db-1", 1, 1) == {
        "checkpoint_id": "db-1", "checkpoint_version": 1, "revision": 1, "changed": False}
    assert service.states[run_id] == state
    assert repository.get_exploration(run_id) == original
    assert not service.capture_events[run_id].is_set()
    assert not service.pending_images and not service.capture_records[run_id]
    assert not list(service.settings.artifact_dir.rglob("*"))
    assert user_print_image["data_url"] not in json.dumps(service.states)
    assert user_print_image["data_url"] not in json.dumps(service._browser_panel_payload(run_id, []))
    cleared = await service.preview_from_browser_panel(run_id, "db-1", 1, 1, None)
    assert cleared["revision"] == 2 and cleared["image"] is None
    assert service.states[run_id] == state


@pytest.mark.asyncio
async def test_preview_cas_rejects_stale_replacement_and_cannot_confirm(tmp_path, user_print_image):
    service, _, run_id = waiting_service(tmp_path)
    await service.preview_from_browser_panel(run_id, "db-1", 1, 0, user_print_image["data_url"])
    for stale in (0, True, "1", -1, None):
        with pytest.raises(ValueError):
            await service.preview_from_browser_panel(run_id, "db-1", 1, stale, None)
    assert service.preview_for(run_id, "db-1", 1)["image"]["data_url"] == user_print_image["data_url"]
    assert not service.capture_events[run_id].is_set()
    service.capture_print(run_id, "db-1", 1, user_print_image["data_url"])
    assert service.capture_events[run_id].is_set()
    assert run_id not in service.capture_previews
    with pytest.raises(ValueError):
        service.preview_for(run_id, "db-1", 1)


@pytest.mark.asyncio
async def test_failed_final_acceptance_retains_preview_for_an_explicit_retry(tmp_path, monkeypatch, user_print_image):
    service, repository, run_id = waiting_service(tmp_path)
    await service.preview_from_browser_panel(run_id, "db-1", 1, 0, user_print_image["data_url"])

    def unavailable_event_store(*_args):
        raise OSError("Unavailable local event store")

    monkeypatch.setattr(repository, "add_event", unavailable_event_store)
    with pytest.raises(OSError):
        service.capture_print(run_id, "db-1", 1, user_print_image["data_url"])
    assert service.states[run_id]["phase"] == "waiting_capture"
    assert service.preview_for(run_id, "db-1", 1)["image"]["data_url"] == user_print_image["data_url"]
    assert not service.capture_events[run_id].is_set()
    assert not service.pending_images
    assert not any(value.is_file() for value in service.settings.artifact_dir.rglob("*"))


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["cancel", "new_checkpoint", "finished", "forget", "shutdown"])
async def test_preview_is_erased_at_lifecycle_boundaries(tmp_path, user_print_image, change):
    service, _, run_id = waiting_service(tmp_path)
    await service.preview_from_browser_panel(run_id, "db-1", 1, 0, user_print_image["data_url"])
    if change == "cancel":
        service.cancel(run_id)
    elif change == "new_checkpoint":
        service._persist(run_id, capture_checkpoint={"id": "db-2", "version": 2, "gap": 2, "order": 0})
    elif change == "finished":
        service._persist(run_id, running=False, phase="finished")
    elif change == "forget":
        service._forget(run_id)
    else:
        await service.shutdown()
    assert run_id not in service.capture_previews
    with pytest.raises(ValueError):
        await service.preview_from_browser_panel(run_id, "db-1", 1, 1, None)
    if change == "new_checkpoint":
        assert service.preview_for(run_id, "db-2", 2)["revision"] == 0


@pytest.mark.asyncio
async def test_preview_rechecks_cancellation_after_off_loop_validation(tmp_path, monkeypatch, user_print_image):
    service, _, run_id = waiting_service(tmp_path)
    entered, release = threading.Event(), threading.Event()
    event_loop_thread = threading.get_ident()

    def slow_validate(data_url):
        assert threading.get_ident() != event_loop_thread
        entered.set()
        assert release.wait(3)
        return validate_user_image(data_url)

    monkeypatch.setattr(rerun_module, "validate_user_image", slow_validate)
    pending = asyncio.create_task(service.preview_from_browser_panel(run_id, "db-1", 1, 0, user_print_image["data_url"]))
    try:
        async with asyncio.timeout(3):
            while not entered.is_set():
                await asyncio.sleep(0.001)
        service.cancel(run_id)
        release.set()
        with pytest.raises(ValueError):
            await pending
        assert run_id not in service.capture_previews
        assert not service.capture_events[run_id].is_set()
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_delayed_preview_cannot_overwrite_a_newer_clear(tmp_path, monkeypatch, user_print_image):
    service, _, run_id = waiting_service(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def slow_validate(data_url):
        entered.set()
        assert release.wait(3)
        return validate_user_image(data_url)

    monkeypatch.setattr(rerun_module, "validate_user_image", slow_validate)
    pending = asyncio.create_task(service.preview_from_browser_panel(run_id, "db-1", 1, 0, user_print_image["data_url"]))
    try:
        async with asyncio.timeout(3):
            while not entered.is_set():
                await asyncio.sleep(0.001)
        cleared = await service.preview_from_browser_panel(run_id, "db-1", 1, 0, None)
        assert cleared["revision"] == 1 and cleared["image"] is None
        release.set()
        with pytest.raises(ValueError):
            await pending
        assert service.preview_for(run_id, "db-1", 1)["image"] is None
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("image", ["", "data:image/svg+xml;base64,PHN2Zy8+", "data:image/png;base64,broken!"])
async def test_invalid_preview_never_replaces_the_existing_valid_image(tmp_path, user_print_image, image):
    service, _, run_id = waiting_service(tmp_path)
    await service.preview_from_browser_panel(run_id, "db-1", 1, 0, user_print_image["data_url"])
    with pytest.raises(UploadedImageError):
        await service.preview_from_browser_panel(run_id, "db-1", 1, 1, image)
    assert service.preview_for(run_id, "db-1", 1)["revision"] == 1
    assert service.preview_for(run_id, "db-1", 1)["image"]["data_url"] == user_print_image["data_url"]


@pytest.mark.asyncio
async def test_service_configures_shared_preview_and_streams_only_changed_revisions(tmp_path, monkeypatch, user_print_image):
    service, repository, source_id, _, _ = make_service(tmp_path)
    browsers = install_panel_browser(monkeypatch)
    executor_type = rerun_module.PlaywrightExecutor

    def configure(self, handler):
        assert not self.started
        self.preview_handler = handler
        self.preview_updates = []

    async def update(self, snapshot):
        assert not self.closed
        self.preview_updates.append(copy.deepcopy(snapshot))

    monkeypatch.setattr(executor_type, "configure_rerun_preview", configure, raising=False)
    monkeypatch.setattr(executor_type, "update_rerun_preview", update, raising=False)
    run_id = service.submit(source_id, "CT001", capture_plan=capture_plan(("db-1", 0, 0)))
    task = service.tasks[run_id]
    try:
        await wait_phase(service, run_id, "waiting_preparation")
        browser = browsers[0]
        service.resume(run_id)
        checkpoint = await wait_capture(service, run_id, "db-1")
        await wait_panel(browser, "waiting_capture")
        selected = await browser.preview_handler("db-1", checkpoint["version"], 0, user_print_image["data_url"])
        assert selected["revision"] == 1
        async with asyncio.timeout(3):
            while not any(value["revision"] == 1 for value in browser.preview_updates):
                await asyncio.sleep(0.01)
        assert browser.preview_updates[-1]["image"]["data_url"] == user_print_image["data_url"]
        await wait_panel(browser, "waiting_capture", minimum=3)
        assert sum(value["revision"] == 1 for value in browser.preview_updates) == 1
        assert not browser.actions and not service.capture_events[run_id].is_set()
        assert user_print_image["data_url"] not in json.dumps(browser.panel_payloads)
        await browser.capture_handler("db-1", checkpoint["version"], user_print_image["data_url"])
        await task
        assert run_id not in service.capture_previews
        assert len(repository.get_exploration(run_id)["scenarios"][0]["evidence"]["manual_captures"]) == 1
    finally:
        if not task.done():
            service.cancel(run_id)
            await task


def prepare_api(runtime):
    app, client, run_id = runtime
    service = app.state.scenario_rerun_service
    service.repository.update_exploration(run_id, status="waiting_input")
    service.states[run_id] = {"running": True, "phase": "waiting_capture", "capture_action_index": 2,
                              "capture_checkpoint": {"id": "db-1", "version": 1, "gap": 1, "order": 0}}
    service.capture_events[run_id] = asyncio.Event()
    service.capture_records[run_id] = []
    return service, client, run_id, f"/api/explorations/{run_id}/capture-print/preview"


def test_api_synchronizes_preview_but_remains_paused_and_never_persists_image(runtime, user_print_image):
    service, client, run_id, path = prepare_api(runtime)
    initial = service.repository.get_exploration(run_id)
    query = {"checkpoint_id": "db-1", "checkpoint_version": 1}
    headers = {"Origin": "http://localhost:3000"}
    assert client.get(path, params=query, headers=headers).json()["image"] is None
    body = {**query, "expected_revision": 0, "image_data_url": user_print_image["data_url"]}
    staged = client.post(path, json=body, headers=headers)
    assert staged.status_code == 200 and staged.headers["cache-control"] == "no-store"
    assert staged.json()["image"]["data_url"] == user_print_image["data_url"]
    remote = client.get(path, params=query, headers=headers)
    assert remote.status_code == 200 and remote.headers["cache-control"] == "no-store"
    assert remote.json() == staged.json()
    unchanged = client.get(path, params={**query, "known_revision": 1}, headers=headers)
    assert unchanged.json()["changed"] is False and "image" not in unchanged.json()
    assert service.states[run_id]["phase"] == "waiting_capture"
    assert not service.capture_events[run_id].is_set()
    assert service.repository.get_exploration(run_id) == initial
    assert user_print_image["data_url"] not in json.dumps(service.states)
    clear = client.post(path, json={**query, "expected_revision": 1, "image_data_url": None}, headers=headers)
    assert clear.json()["revision"] == 2 and clear.json()["image"] is None


@pytest.mark.parametrize("origin", ["null", "https://unrelated.test", "http://localhost:3000.evil.test"])
def test_preview_read_and_write_are_origin_gated(runtime, user_print_image, origin):
    service, client, _, path = prepare_api(runtime)
    query = {"checkpoint_id": "db-1", "checkpoint_version": 1}
    body = {**query, "expected_revision": 0, "image_data_url": user_print_image["data_url"]}
    for response in (client.get(path, params=query, headers={"Origin": origin}),
                     client.post(path, json=body, headers={"Origin": origin})):
        assert response.status_code == 403 and response.headers["cache-control"] == "no-store"
        assert user_print_image["data_url"] not in response.text
    assert not service.capture_previews


def test_preview_endpoint_rejects_stale_cancelled_and_duplicate_revision(runtime, user_print_image):
    service, client, run_id, path = prepare_api(runtime)
    query = {"checkpoint_id": "db-1", "checkpoint_version": 1}
    body = {**query, "expected_revision": 0, "image_data_url": user_print_image["data_url"]}
    assert client.post(path, json=body).status_code == 200
    response = client.post(path, json={**body, "image_data_url": None})
    assert response.status_code == 409 and response.headers["cache-control"] == "no-store"
    assert client.get(path, params={**query, "checkpoint_version": 2}).status_code == 409
    service.repository.request_cancel(run_id)
    assert client.get(path, params=query).status_code == 409
    assert client.post(path, json={**body, "expected_revision": 1}).status_code == 409


@pytest.mark.parametrize("change", [{"expected_revision": True}, {"expected_revision": -1},
                                    {"checkpoint_version": "1"}, {"image_data_url": 42},
                                    {"filename": "../../sensitive.png"}, {"confirm": True}])
def test_preview_schema_rejects_authority_or_untrusted_fields_without_leaking_images(runtime, user_print_image, change):
    _, client, _, path = prepare_api(runtime)
    body = {"checkpoint_id": "db-1", "checkpoint_version": 1, "expected_revision": 0,
            "image_data_url": user_print_image["data_url"], **change}
    response = client.post(path, json=body)
    assert response.status_code == 422 and response.headers["cache-control"] == "no-store"
    assert user_print_image["data_url"] not in response.text


def test_preview_invalid_raster_retains_pause_and_is_not_cacheable(runtime):
    service, client, run_id, path = prepare_api(runtime)
    body = {"checkpoint_id": "db-1", "checkpoint_version": 1, "expected_revision": 0,
            "image_data_url": "data:image/svg+xml;base64,PHN2Zy8+"}
    response = client.post(path, json=body)
    assert response.status_code == 422 and response.headers["cache-control"] == "no-store"
    assert service.states[run_id]["phase"] == "waiting_capture"
    assert not service.capture_events[run_id].is_set()
