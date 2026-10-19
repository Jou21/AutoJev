"""Native-site login is observed passively, never submitted by the watcher."""
import asyncio
from copy import deepcopy

import pytest

import autojev.services.scenario_rerun as rerun_module
from test_scenario_rerun import (
    URL, capture_plan, install_filled_login_browser, make_service, observed, wait_capture, wait_preparation,
)


def install_passive_browser(monkeypatch, *, initial_url=None, block_read=None):
    browsers = install_filled_login_browser(monkeypatch)
    base = rerun_module.PlaywrightExecutor

    class PassiveExecutor(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            if initial_url:
                self.page.url = initial_url
            self.passive_reads = 0
            self.active_passive_reads = 0
            self.cancelled_passive_reads = 0

        async def observe_preparation(self):
            self.passive_reads += 1
            self.active_passive_reads += 1
            try:
                if block_read is not None:
                    await block_read.wait()
                snapshot = deepcopy(self.page)
                snapshot.screenshot = None
                return snapshot
            except asyncio.CancelledError:
                self.cancelled_passive_reads += 1
                raise
            finally:
                self.active_passive_reads -= 1

        async def observe(self, label):
            assert self.active_passive_reads == 0, "Passive reads must stop before authoritative observations"
            return await super().observe(label)

    monkeypatch.setattr(rerun_module, "PlaywrightExecutor", PassiveExecutor)
    return browsers


async def wait_until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.001)


async def cleanup(service, run_id, task):
    if not task.done():
        service.cancel(run_id)
    await asyncio.gather(task, return_exceptions=True)


def fast_watch(service):
    service.manual_login_poll_seconds = 0.002
    service.manual_login_observation_timeout_seconds = 0.1


@pytest.mark.asyncio
@pytest.mark.parametrize("login_url", ["https://example.test/", URL])
async def test_native_login_resumes_once_with_current_authorization_including_same_url_spa(tmp_path, monkeypatch, login_url):
    service, repository, source_id, _, _ = make_service(tmp_path)
    original = deepcopy(repository.get_exploration(source_id))
    fast_watch(service)
    browsers = install_passive_browser(monkeypatch, initial_url=login_url)
    run_id = service.submit(source_id, "CT001")
    task = service.tasks[run_id]
    try:
        await wait_preparation(service, run_id, 1)
        browser = browsers[0]
        service.set_preparation_consent(run_id, 1, 0, True)
        await wait_until(lambda: browser.passive_reads >= 3)
        assert not browser.login_attempts and not browser.actions
        assert not service.input_events[run_id].is_set()
        # This represents clicking the tested site's own Login button. The
        # AutoJev continue button and login-submission method are never used.
        browser.page = observed("e42")
        await asyncio.wait_for(task, 3)
        replay = repository.get_exploration(run_id)
        assert len(browser.actions) == len(replay["actions"]) == 1
        assert not browser.login_attempts and browser.active_passive_reads == 0
        assert replay["status"] == "completed" and replay["scenarios"][0]["status"] == "PASS"
        assert any(event["type"] == "scenario_rerun_state" and
                   (event.get("payload", {}).get("preparation", {}).get("manual_login") or {}).get("status") == "ready"
                   for event in replay["events"])
        assert repository.get_exploration(source_id) == original
    finally:
        await cleanup(service, run_id, task)


@pytest.mark.asyncio
async def test_login_is_recognized_without_authorization_but_waits_for_checkbox(tmp_path, monkeypatch):
    service, repository, source_id, _, _ = make_service(tmp_path)
    fast_watch(service)
    browsers = install_passive_browser(monkeypatch)
    run_id = service.submit(source_id, "CT001")
    task = service.tasks[run_id]
    try:
        await wait_preparation(service, run_id, 1)
        browser = browsers[0]
        browser.page = observed("e42")
        await wait_until(lambda: service.states[run_id]["preparation"].get("manual_login", {}).get("status") == "ready")
        await asyncio.sleep(0.02)
        state = service.state_for(run_id)
        assert state["phase"] == "waiting_preparation" and state["preparation"]["consent"]["checked"] is False
        assert state["preparation"]["reason_code"] is None
        assert not browser.actions and not browser.login_attempts and not task.done()
        assert not service.input_events[run_id].is_set()
        assert repository.get_exploration(run_id)["actions"] == []
        service.set_preparation_consent(run_id, 1, 0, True)
        await asyncio.wait_for(task, 3)
        assert len(browser.actions) == 1 and not browser.login_attempts
    finally:
        await cleanup(service, run_id, task)


@pytest.mark.asyncio
async def test_unchecking_before_login_finishes_prevents_automatic_dispatch(tmp_path, monkeypatch):
    service, _, source_id, _, _ = make_service(tmp_path)
    fast_watch(service)
    browsers = install_passive_browser(monkeypatch)
    run_id = service.submit(source_id, "CT001")
    task = service.tasks[run_id]
    try:
        await wait_preparation(service, run_id, 1)
        service.set_preparation_consent(run_id, 1, 0, True)
        service.set_preparation_consent(run_id, 1, 1, False)
        browsers[0].page = observed("e42")
        await wait_until(lambda: service.states[run_id]["preparation"].get("manual_login", {}).get("status") == "ready")
        await asyncio.sleep(0.02)
        assert not browsers[0].actions and not browsers[0].login_attempts and not task.done()
        assert service.preparation_consent_for(run_id, 1)["checked"] is False
    finally:
        await cleanup(service, run_id, task)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["origin", "url", "cart", "missing_target", "login_error"])
async def test_no_false_login_completion_and_guard_recovery_stays_observable(tmp_path, monkeypatch, change):
    service, _, source_id, _, _ = make_service(tmp_path)
    fast_watch(service)
    browsers = install_passive_browser(monkeypatch)
    run_id = service.submit(source_id, "CT001")
    task = service.tasks[run_id]
    try:
        await wait_preparation(service, run_id, 1)
        service.set_preparation_consent(run_id, 1, 0, True)
        browser = browsers[0]
        page = observed("e42")
        if change == "origin":
            page.url = "https://other.test/inventory.html"
        elif change == "url":
            page.url = "https://example.test/wrong.html"
        elif change == "cart":
            page.page_state["cart"] = {"count": 9}
        elif change == "missing_target":
            page.interactive_elements = []
        else:
            page = deepcopy(browser.page)
            page.text = "Login failed: invalid credentials"
        browser.page = page
        minimum = browser.passive_reads + 4
        await wait_until(lambda: browser.passive_reads >= minimum)
        assert not browser.actions and not browser.login_attempts and not task.done()
        state = service.state_for(run_id)
        assert state["phase"] == "waiting_preparation"
        assert state["preparation"]["manual_login"]["status"] == ("waiting" if change == "login_error" else "blocked")
        # A changed explanatory reason must not disable the watcher which was
        # eligible when this same preparation first showed the login form.
        browser.page = observed("e42")
        await asyncio.wait_for(task, 3)
        assert len(browser.actions) == 1 and not browser.login_attempts
    finally:
        await cleanup(service, run_id, task)


@pytest.mark.asyncio
async def test_authorization_revoked_between_detection_and_consumption_stays_paused(tmp_path, monkeypatch):
    service, _, source_id, _, _ = make_service(tmp_path)
    fast_watch(service)
    browsers = install_passive_browser(monkeypatch)
    run_id = service.submit(source_id, "CT001")
    task = service.tasks[run_id]
    try:
        await wait_preparation(service, run_id, 1)
        browser = browsers[0]
        original_hide = browser.hide_intervention_panel
        revoked = asyncio.Event()

        async def revoke_while_hiding():
            if not revoked.is_set():
                snapshot = service.preparation_consent_for(run_id, 1)
                service.set_preparation_consent(run_id, 1, snapshot["revision"], False)
                revoked.set()
            await original_hide()

        browser.hide_intervention_panel = revoke_while_hiding
        service.set_preparation_consent(run_id, 1, 0, True)
        browser.page = observed("e42")
        await wait_preparation(service, run_id, 2)
        assert revoked.is_set() and not browser.actions and not browser.login_attempts and not task.done()
        assert service.preparation_consent_for(run_id, 2)["checked"] is False
    finally:
        await cleanup(service, run_id, task)


@pytest.mark.asyncio
async def test_fresh_observation_reappearing_login_never_causes_automatic_submission(tmp_path, monkeypatch):
    service, _, source_id, _, _ = make_service(tmp_path)
    fast_watch(service)
    browsers = install_passive_browser(monkeypatch)
    run_id = service.submit(source_id, "CT001")
    task = service.tasks[run_id]
    try:
        await wait_preparation(service, run_id, 1)
        browser = browsers[0]
        login_page = deepcopy(browser.page)
        original_observe = browser.observe

        async def login_returned(label):
            browser.page = deepcopy(login_page)
            return await original_observe(label)

        browser.observe = login_returned
        service.set_preparation_consent(run_id, 1, 0, True)
        browser.page = observed("e42")
        await wait_preparation(service, run_id, 2)
        assert service.states[run_id]["preparation"]["reason_code"] == "authentication_required"
        assert not browser.actions and not browser.login_attempts and not task.done()
    finally:
        await cleanup(service, run_id, task)


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["cancel", "browser_close"])
async def test_pending_passive_read_is_cancelled_and_awaited_before_cleanup(tmp_path, monkeypatch, stop):
    service, _, source_id, _, _ = make_service(tmp_path)
    service.manual_login_observation_timeout_seconds = 5
    browsers = install_passive_browser(monkeypatch, block_read=asyncio.Event())
    run_id = service.submit(source_id, "CT001")
    task = service.tasks[run_id]
    try:
        await wait_preparation(service, run_id, 1)
        browser = browsers[0]
        await wait_until(lambda: browser.active_passive_reads == 1)
        if stop == "cancel":
            service.cancel(run_id)
        else:
            browser.inactive.set()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 3)
        assert browser.active_passive_reads == 0 and browser.cancelled_passive_reads == 1
        assert not browser.actions and not browser.login_attempts and browser.closed
    finally:
        await cleanup(service, run_id, task)


@pytest.mark.asyncio
async def test_explicit_continue_racing_native_login_still_dispatches_only_once(tmp_path, monkeypatch):
    service, repository, source_id, _, _ = make_service(tmp_path)
    fast_watch(service)
    browsers = install_passive_browser(monkeypatch)
    run_id = service.submit(source_id, "CT001")
    task = service.tasks[run_id]
    try:
        await wait_preparation(service, run_id, 1)
        service.set_preparation_consent(run_id, 1, 0, True)
        browsers[0].page = observed("e42")
        service.resume(run_id)
        await asyncio.wait_for(task, 3)
        assert len(browsers[0].actions) == len(repository.get_exploration(run_id)["actions"]) == 1
        assert not browsers[0].login_attempts
    finally:
        await cleanup(service, run_id, task)


@pytest.mark.asyncio
async def test_manual_print_pause_is_never_consumed_by_manual_login_watcher(tmp_path, monkeypatch, user_print_image):
    service, repository, source_id, _, _ = make_service(tmp_path)
    fast_watch(service)
    browsers = install_passive_browser(monkeypatch)
    # Gap zero is unambiguously before the sole original action, independent
    # of historical fixture timestamp ordering.
    run_id = service.submit(source_id, "CT001", capture_plan=capture_plan(("db-1", 0, 0)))
    task = service.tasks[run_id]
    try:
        await wait_preparation(service, run_id, 1)
        service.set_preparation_consent(run_id, 1, 0, True)
        browser = browsers[0]
        browser.page = observed("e42")
        checkpoint = await wait_capture(service, run_id, "db-1")
        reads = browser.passive_reads
        await asyncio.sleep(0.02)
        assert browser.passive_reads == reads
        assert not service.capture_events[run_id].is_set() and not browser.actions and not task.done()
        service.capture_print(run_id, checkpoint["id"], checkpoint["version"], user_print_image["data_url"])
        await asyncio.wait_for(task, 3)
        assert len(browser.actions) == 1 and not browser.login_attempts
        assert len(repository.get_exploration(run_id)["scenarios"][0]["evidence"]["manual_captures"]) == 1
    finally:
        await cleanup(service, run_id, task)
