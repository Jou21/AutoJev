"""One ephemeral preparation checkbox shared by both trusted UI surfaces."""
import asyncio
import json
from copy import deepcopy

import autojev.services.scenario_rerun as rerun_module
import pytest
import test_scenario_rerun_api as rerun_api_fixtures
from autojev.services.scenario_rerun import ScenarioRerunService
from test_scenario_rerun import (
    install_browser,
    install_filled_login_browser,
    make_service,
    wait_preparation,
)

runtime = rerun_api_fixtures.runtime


async def cancel_fixture(service, run_id, task):
    if not task.done():
        service.cancel(run_id)
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_shared_checkbox_is_ephemeral_cas_state_and_never_attempts_login_or_continues(tmp_path, monkeypatch):
    service, repository, source_id, _, _ = make_service(tmp_path)
    browsers = install_filled_login_browser(monkeypatch)
    run_id = service.submit(source_id, "CT001")
    task = service.tasks[run_id]
    try:
        await wait_preparation(service, run_id, 1)
        original_state = deepcopy(service.states[run_id])
        original_record = deepcopy(repository.get_exploration(run_id))
        assert service.preparation_consent_for(run_id, 1) == {"preparation_version": 1, "revision": 0, "checked": False}
        selected = service.set_preparation_consent(run_id, 1, 0, True)
        assert selected == {"preparation_version": 1, "revision": 1, "checked": True}
        assert service.preparation_consent_for(run_id, 1) == selected
        app = service.state_for(run_id)["preparation"]
        panel = service._browser_panel_payload(run_id, [])["preparation"]
        assert app["consent"] == panel["consent"] == selected
        assert app["presentation"] == panel["presentation"]
        assert app["presentation"]
        assert not service.input_events[run_id].is_set()
        await asyncio.sleep(0.01)
        assert not browsers[0].login_attempts and not browsers[0].actions and not task.done()
        assert service.states[run_id] == original_state
        assert repository.get_exploration(run_id) == original_record
        assert "consent" not in json.dumps(service.states[run_id])
        assert "presentation" not in json.dumps(service.states[run_id])
        # A stale tab cannot overwrite a later checkbox selection.
        with pytest.raises(ValueError):
            service.set_preparation_consent(run_id, 1, 0, False)
        cleared = service.set_preparation_consent(run_id, 1, 1, False)
        assert cleared == {"preparation_version": 1, "revision": 2, "checked": False}
        assert not service.input_events[run_id].is_set()
        selected["checked"] = False
        assert service.preparation_consent_for(run_id, 1) == cleared
    finally:
        await cancel_fixture(service, run_id, task)


@pytest.mark.asyncio
async def test_checkbox_selection_is_scoped_by_run_even_when_versions_match(tmp_path, monkeypatch):
    service, _, source_id, _, _ = make_service(tmp_path)
    service.manager.semaphore = asyncio.Semaphore(2)
    install_browser(monkeypatch)
    first = service.submit(source_id, "CT001")
    second = service.submit(source_id, "CT001")
    tasks = {run_id: service.tasks[run_id] for run_id in (first, second)}
    try:
        for run_id in tasks:
            await wait_preparation(service, run_id, 1)
        service.set_preparation_consent(first, 1, 0, True)
        assert service.preparation_consent_for(second, 1) == {"preparation_version": 1, "revision": 0, "checked": False}
        assert not service.input_events[first].is_set() and not service.input_events[second].is_set()
    finally:
        for run_id, task in tasks.items():
            await cancel_fixture(service, run_id, task)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["new_version", "validation", "finish", "cancel", "forget", "shutdown"])
async def test_preparation_selection_expires_and_cannot_authorize_a_new_phase(tmp_path, monkeypatch, boundary):
    service, _, source_id, _, _ = make_service(tmp_path)
    install_browser(monkeypatch)
    run_id = service.submit(source_id, "CT001")
    task = service.tasks[run_id]
    try:
        await wait_preparation(service, run_id, 1)
        service.set_preparation_consent(run_id, 1, 0, True)
        if boundary == "new_version":
            service._persist(run_id, preparation={**service.states[run_id]["preparation"], "version": 2})
        elif boundary == "validation":
            service._persist(run_id, phase="validating_preparation")
        elif boundary == "finish":
            service._persist(run_id, running=False, phase="finished")
        elif boundary == "cancel":
            service.cancel(run_id)
        elif boundary == "forget":
            service._forget(run_id)
        else:
            await service.shutdown()
        assert run_id not in service.preparation_consents
        with pytest.raises(ValueError):
            service.set_preparation_consent(run_id, 1, 1, True)
        if boundary == "new_version":
            assert service.preparation_consent_for(run_id, 2) == {"preparation_version": 2, "revision": 0, "checked": False}
    finally:
        # _forget intentionally removes the task map in its lifecycle test.
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_checkbox_operations_reject_an_already_consumed_preparation_event(tmp_path, monkeypatch):
    service, _, source_id, _, _ = make_service(tmp_path)
    install_browser(monkeypatch)
    run_id = service.submit(source_id, "CT001")
    task = service.tasks[run_id]
    try:
        await wait_preparation(service, run_id, 1)
        service.set_preparation_consent(run_id, 1, 0, True)
        service.resume(run_id)
        # No await: the phase is still waiting, but the explicit continue was
        # accepted already. A checkbox must not reopen or authorize that gate.
        assert service.input_events[run_id].is_set()
        with pytest.raises(ValueError):
            service.preparation_consent_for(run_id, 1)
        with pytest.raises(ValueError):
            service.set_preparation_consent(run_id, 1, 1, False)
        assert service.state_for(run_id)["preparation"]["consent"] is None
        await asyncio.wait_for(task, 3)
        assert run_id not in service.preparation_consents
    finally:
        await cancel_fixture(service, run_id, task)


@pytest.mark.asyncio
async def test_browser_callback_is_optional_and_only_shares_the_checkbox(tmp_path, monkeypatch):
    service, _, source_id, _, _ = make_service(tmp_path)
    browsers = install_filled_login_browser(monkeypatch)
    executor_type = rerun_module.PlaywrightExecutor

    def configure(self, handler):
        assert not self.started
        self.consent_handler = handler

    monkeypatch.setattr(executor_type, "configure_rerun_consent", configure, raising=False)
    run_id = service.submit(source_id, "CT001")
    task = service.tasks[run_id]
    try:
        await wait_preparation(service, run_id, 1)
        selected = await browsers[0].consent_handler(1, 0, True)
        assert selected["checked"] and selected["revision"] == 1
        assert service.state_for(run_id)["preparation"]["consent"] == selected
        assert not service.input_events[run_id].is_set()
        assert not browsers[0].login_attempts and not browsers[0].actions
    finally:
        await cancel_fixture(service, run_id, task)


@pytest.mark.asyncio
async def test_executor_without_new_consent_hook_keeps_the_existing_explicit_resume_flow(tmp_path, monkeypatch):
    service, repository, source_id, _, _ = make_service(tmp_path)
    browsers = install_browser(monkeypatch)
    run_id = service.submit(source_id, "CT001")
    task = service.tasks[run_id]
    try:
        await wait_preparation(service, run_id, 1)
        assert not hasattr(browsers[0], "configure_rerun_consent")
        service.resume(run_id)
        await asyncio.wait_for(task, 3)
        assert repository.get_exploration(run_id)["status"] == "completed"
        assert len(browsers[0].actions) == 1
    finally:
        await cancel_fixture(service, run_id, task)


def prepare_api(runtime):
    app, client, run_id = runtime
    service = app.state.scenario_rerun_service
    service.repository.update_exploration(run_id, status="waiting_input")
    service.states[run_id] = {"running": True, "phase": "waiting_preparation", "preparation": {"version": 1}}
    service.input_events[run_id] = asyncio.Event()
    return service, client, run_id, f"/api/explorations/{run_id}/preparation-consent"


def test_api_reads_and_shares_checkbox_without_persisting_or_continuing(runtime):
    service, client, run_id, path = prepare_api(runtime)
    original = deepcopy(service.repository.get_exploration(run_id))
    headers = {"Origin": "http://localhost:3000"}
    initial = client.get(path, params={"preparation_version": 1}, headers=headers)
    assert initial.status_code == 200 and initial.headers["cache-control"] == "no-store"
    assert initial.json() == {"preparation_version": 1, "revision": 0, "checked": False}
    selected = client.post(path, json={"preparation_version": 1, "expected_revision": 0, "checked": True}, headers=headers)
    assert selected.status_code == 200 and selected.headers["cache-control"] == "no-store"
    assert selected.json() == {"preparation_version": 1, "revision": 1, "checked": True}
    assert client.get(path, params={"preparation_version": 1}, headers=headers).json() == selected.json()
    assert service.repository.get_exploration(run_id) == original
    assert service.states[run_id] == {"running": True, "phase": "waiting_preparation", "preparation": {"version": 1}}
    assert not service.input_events[run_id].is_set()
    uncheck = client.post(path, json={"preparation_version": 1, "expected_revision": 1, "checked": False}, headers=headers)
    assert uncheck.json() == {"preparation_version": 1, "revision": 2, "checked": False}


@pytest.mark.parametrize("origin", ["null", "https://unrelated.test", "http://localhost:3000.evil.test"])
def test_both_consent_read_and_write_reject_untrusted_browser_origins(runtime, origin):
    service, client, _, path = prepare_api(runtime)
    for response in (client.get(path, params={"preparation_version": 1}, headers={"Origin": origin}),
                     client.post(path, json={"preparation_version": 1, "expected_revision": 0, "checked": True}, headers={"Origin": origin})):
        assert response.status_code == 403 and response.headers["cache-control"] == "no-store"
    assert not service.preparation_consents


def test_consent_gate_rejects_wrong_version_stale_revision_cancel_and_consumed_event(runtime):
    service, client, run_id, path = prepare_api(runtime)
    body = {"preparation_version": 1, "expected_revision": 0, "checked": True}
    assert client.post(path, json=body).status_code == 200
    assert client.post(path, json=body).status_code == 409
    assert client.get(path, params={"preparation_version": 2}).status_code == 409
    assert client.post(path, json={**body, "preparation_version": 2}).status_code == 409
    service.input_events[run_id].set()
    response = client.post(path, json={**body, "expected_revision": 1})
    assert response.status_code == 409 and response.headers["cache-control"] == "no-store"
    service.input_events[run_id].clear()
    service.repository.request_cancel(run_id)
    assert client.get(path, params={"preparation_version": 1}).status_code == 409
    assert client.post(path, json={**body, "expected_revision": 1}).status_code == 409


@pytest.mark.parametrize("changes", [{"preparation_version": True}, {"preparation_version": "1"},
                                     {"expected_revision": True}, {"expected_revision": -1},
                                     {"checked": "true"}, {"checked": 1}, {"checked": None},
                                     {"confirm": True}, {"password": "private-do-not-echo"}])
def test_consent_schema_is_strict_non_cacheable_and_does_not_echo_private_inputs(runtime, changes):
    _, client, _, path = prepare_api(runtime)
    response = client.post(path, json={"preparation_version": 1, "expected_revision": 0, "checked": True, **changes})
    assert response.status_code == 422 and response.headers["cache-control"] == "no-store"
    assert "private-do-not-echo" not in response.text


def test_backend_restart_cannot_recover_checkbox_selection_from_durable_state(runtime):
    service, _, run_id, _ = prepare_api(runtime)
    service.set_preparation_consent(run_id, 1, 0, True)
    service._persist(run_id)
    restarted = ScenarioRerunService(service.manager, service.settings)
    assert restarted.preparation_consents == {}
    assert restarted.state_for(run_id)["phase"] == "finished"
    assert restarted.state_for(run_id)["preparation"]["consent"] is None
    with pytest.raises(ValueError):
        restarted.preparation_consent_for(run_id, 1)
