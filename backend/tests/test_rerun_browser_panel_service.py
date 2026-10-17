"""Service-side integration of the isolated browser rerun panel.

The trusted frame itself is exercised separately; these tests establish that
its callback uses the existing attachment transaction, not a second CT path.
"""
import asyncio
import copy
import json
import threading

import pytest

import autojev.services.scenario_rerun as rerun_module
from autojev.services.rerun_step_progress import build_step_plan
from autojev.services.user_image_upload import validate_user_image
from test_scenario_rerun import capture_plan, install_browser, make_service, observed, wait_capture, wait_phase, wait_preparation


def install_panel_browser(monkeypatch, **kwargs):
    browsers = install_browser(monkeypatch, **kwargs)
    executor_type = rerun_module.PlaywrightExecutor

    def configure(self, handler):
        assert not self.started, "The trusted handler must be configured before browser creation"
        self.capture_handler = handler
        self.panel_payloads = []

    def configure_resume(self, handler):
        assert not self.started
        self.resume_handler = handler

    async def update(self, payload):
        assert self.started and not self.closed
        self.panel_payloads.append(copy.deepcopy(payload))

    monkeypatch.setattr(executor_type, "configure_rerun_panel", configure, raising=False)
    monkeypatch.setattr(executor_type, "configure_rerun_resume", configure_resume, raising=False)
    monkeypatch.setattr(executor_type, "update_rerun_panel", update, raising=False)
    return browsers


async def wait_panel(browser, phase, minimum=1):
    async with asyncio.timeout(3):
        while sum(payload["phase"] == phase for payload in browser.panel_payloads) < minimum:
            await asyncio.sleep(0.01)
    return next(payload for payload in reversed(browser.panel_payloads) if payload["phase"] == phase)


@pytest.mark.asyncio
async def test_browser_panel_projects_current_run_and_attachment_uses_same_transaction(
    tmp_path, monkeypatch, user_print_image,
):
    service, repository, source_id, _, reports = make_service(tmp_path, with_products=True)
    source = repository.get_exploration(source_id)
    browsers = install_panel_browser(monkeypatch, before=observed("e42", with_products=True),
                                     after=observed("e42", selected="za", with_products=True))
    run_id = service.submit(source_id, "CT001", capture_plan=capture_plan(("db-1", 2, 0)))
    task = service.tasks[run_id]
    try:
        await wait_phase(service, run_id, "waiting_preparation")
        browser = browsers[0]
        pending = await wait_panel(browser, "waiting_preparation")
        assert pending["progress"]["completed"] == 0
        assert not any(row["state"] == "done" for row in pending["progress"]["steps"])
        assert pending["preparation"]["version"] == 1
        accepted_preparation = await browser.resume_handler(1)
        assert accepted_preparation == {"accepted": True, "preparation_version": 1}
        checkpoint = await wait_capture(service, run_id, "db-1")
        waiting = await wait_panel(browser, "waiting_capture")
        assert waiting["checkpoint"] == checkpoint
        assert waiting["progress"]["current_id"] == "checkpoint-db-1"
        assert [row["id"] for row in waiting["progress"]["steps"]][2] == "checkpoint-db-1"
        assert len(browser.actions) == 1
        # Unchanged updates still reach the executor so it can restore its
        # private frame after navigation. It owns actual frame-update dedup.
        repeated = await wait_panel(browser, "waiting_capture", minimum=2)
        assert repeated == waiting
        accepted = await browser.capture_handler(checkpoint["id"], checkpoint["version"], user_print_image["data_url"])
        assert accepted == {"accepted": True, "checkpoint_id": "db-1", "checkpoint_version": 1}
        await task
        replay = repository.get_exploration(run_id)
        attachment = replay["scenarios"][0]["evidence"]["manual_captures"][0]
        assert attachment["checkpoint_id"] == "db-1" and attachment["planned_gap"] == 2
        assert attachment["attachment"]["sha256"] == user_print_image["sha256"]
        assert len(browser.actions) == 1 and not browser.manual_captures
        assert browser.closed and len(reports) == 1 and replay["status"] == "completed"
        assert user_print_image["data_url"] not in json.dumps(replay)
        assert repository.get_exploration(source_id) == source
        with pytest.raises(ValueError):
            await browser.capture_handler(checkpoint["id"], checkpoint["version"], user_print_image["data_url"])
    finally:
        if not task.done():
            service.cancel(run_id)
            await task


@pytest.mark.asyncio
async def test_browser_panel_callback_rechecks_cancellation_after_worker_validation(
    tmp_path, monkeypatch, user_print_image,
):
    service, repository, source_id, _, _ = make_service(tmp_path)
    browsers = install_panel_browser(monkeypatch)
    run_id = service.submit(source_id, "CT001", capture_plan=capture_plan(("db-1", 0, 0)))
    run_task = service.tasks[run_id]
    entered, release = threading.Event(), threading.Event()
    loop_thread = threading.get_ident()

    def worker_validate(data_url):
        assert threading.get_ident() != loop_thread
        entered.set()
        assert release.wait(3)
        return validate_user_image(data_url)

    try:
        await wait_phase(service, run_id, "waiting_preparation")
        service.resume(run_id)
        checkpoint = await wait_capture(service, run_id, "db-1")
        monkeypatch.setattr(rerun_module, "validate_user_image", worker_validate)
        upload = asyncio.create_task(browsers[0].capture_handler("db-1", checkpoint["version"], user_print_image["data_url"]))
        async with asyncio.timeout(3):
            while not entered.is_set():
                await asyncio.sleep(0.001)
        # Decoding cannot freeze the event loop or grant stale authority.
        service.cancel(run_id)
        await run_task
        release.set()
        with pytest.raises(ValueError):
            await upload
        assert not browsers[0].actions
        assert not repository.get_exploration(run_id)["screenshots"]
        assert run_id not in service.pending_images
    finally:
        release.set()
        if not run_task.done():
            service.cancel(run_id)
            await run_task


def test_browser_panel_payload_whitelists_live_structure_and_redacts_display_text(tmp_path):
    service, repository, run_id, _, _ = make_service(tmp_path)
    repository.update_exploration(run_id, status="running")
    service.states[run_id] = {
        "running": True, "phase": "waiting_capture", "capture_count": 1,
        "captures_completed": 0, "capture_checkpoint": {"id": "db-1", "version": 1, "gap": 0, "order": 0, "image": "private-bytes"},
        "step_plan": build_step_plan([], capture_plan(("db-1", 0, 0))),
        "source_exploration_id": "private-source", "recipe": {"password": "private-password"},
        "capture_instruction": "unbounded-private-payload", "image_data_url": "data:image/private",
        "preparation": {"version": 2, "instruction": "Use private-password here", "current_url": "https://example.test/private-password",
                        "login_attempt": {"status": "rejected", "password": "private-password", "reason_label": "Try again"}},
    }
    payload = service._browser_panel_payload(run_id, ["private-password"])
    encoded = json.dumps(payload)
    for forbidden in ("private-source", "recipe", "private-password", "private-bytes", "unbounded-private-payload", "data:image/private"):
        assert forbidden not in encoded
    assert "[REDACTED]" in encoded
    assert payload["checkpoint"] == {"id": "db-1", "version": 1, "gap": 0, "order": 0}
    assert payload["progress"]["steps"][0]["state"] == "waiting"
    assert payload["progress"]["completed"] == 0
    assert set(payload["preparation"]["login_attempt"]) == {"status", "reason_label"}
    assert "events" not in payload and "step_plan" not in payload


@pytest.mark.asyncio
async def test_browser_panel_resume_rechecks_live_version_without_waking_newer_waiter(tmp_path, monkeypatch):
    service, repository, source_id, _, _ = make_service(tmp_path)
    browsers = install_panel_browser(monkeypatch, before=observed("e42", selected="za"))
    run_id = service.submit(source_id, "CT001")
    task = service.tasks[run_id]
    try:
        await wait_preparation(service, run_id, 1)
        browser = browsers[0]
        await browser.resume_handler(1)
        await wait_preparation(service, run_id, 2)
        for stale in (1, True, "2", None, 0, 3):
            with pytest.raises(ValueError):
                await browser.resume_handler(stale)
            assert not service.input_events[run_id].is_set()
            assert service.states[run_id]["preparation"]["version"] == 2
            assert not browser.actions

        async def corrected_observation(label):
            return observed("e42")

        browser.observe = corrected_observation
        await browser.resume_handler(2)
        with pytest.raises(ValueError):
            # A duplicate click cannot confirm the same event twice, even
            # before the service has a chance to advance to validation.
            await browser.resume_handler(2)
        await task
        assert len(browser.actions) == 1
        assert repository.get_exploration(run_id)["status"] == "completed"
    finally:
        if not task.done():
            service.cancel(run_id)
            await task


@pytest.mark.asyncio
async def test_browser_panel_resume_before_generic_browser_waiter_is_not_lost(tmp_path, monkeypatch):
    service, repository, source_id, _, _ = make_service(tmp_path)
    browsers = install_panel_browser(monkeypatch)
    executor_type = rerun_module.PlaywrightExecutor

    async def show(self, message, **kwargs):
        # The panel can be clicked before _wait_for_preparation has started
        # its browser waiter. That waiter clearing its event must not eat the
        # service's independently accepted app confirmation.
        self.prepared.set()
        self.accepted_preparation = await self.resume_handler(1)

    async def browser_waiter(self):
        self.prepared.clear()
        self.browser_waiter_started = True
        await self.prepared.wait()

    monkeypatch.setattr(executor_type, "show_intervention_panel", show)
    monkeypatch.setattr(executor_type, "wait_for_user_input", browser_waiter)
    run_id = service.submit(source_id, "CT001")
    await asyncio.wait_for(service.tasks[run_id], 3)
    browser = browsers[0]
    assert browser.browser_waiter_started
    assert browser.accepted_preparation == {"accepted": True, "preparation_version": 1}
    assert len(browser.actions) == 1
    assert repository.get_exploration(run_id)["status"] == "completed"


@pytest.mark.asyncio
async def test_browser_panel_resume_rejects_cancelled_preparation(tmp_path, monkeypatch):
    service, repository, source_id, _, _ = make_service(tmp_path)
    browsers = install_panel_browser(monkeypatch)
    run_id = service.submit(source_id, "CT001")
    task = service.tasks[run_id]
    try:
        await wait_preparation(service, run_id, 1)
        repository.request_cancel(run_id)
        with pytest.raises(ValueError):
            await browsers[0].resume_handler(1)
        assert not service.input_events[run_id].is_set()
        assert not browsers[0].actions
    finally:
        service.cancel(run_id)
        await task


@pytest.mark.asyncio
async def test_optional_panel_failure_never_aborts_ct_and_stream_is_cancelled_before_close(tmp_path, monkeypatch):
    service, repository, source_id, _, _ = make_service(tmp_path)
    browsers = install_panel_browser(monkeypatch)
    executor_type = rerun_module.PlaywrightExecutor
    entered = asyncio.Event()

    async def update(self, payload):
        self.panel_payloads.append(payload)
        if len(self.panel_payloads) == 1:
            raise RuntimeError("Temporary presentation failure")
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.stream_cancelled = True

    async def close(self):
        assert self.stream_cancelled, "Never race a frame update with browser shutdown"
        self.closed = True

    monkeypatch.setattr(executor_type, "update_rerun_panel", update)
    monkeypatch.setattr(executor_type, "close", close)
    run_id = service.submit(source_id, "CT001")
    task = service.tasks[run_id]
    try:
        await wait_phase(service, run_id, "waiting_preparation")
        await asyncio.wait_for(entered.wait(), 3)
        service.resume(run_id)
        await task
        assert browsers[0].closed and len(browsers[0].actions) == 1
        assert repository.get_exploration(run_id)["status"] == "completed"
    finally:
        if not task.done():
            service.cancel(run_id)
            await task
