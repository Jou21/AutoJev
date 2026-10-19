"""Repeated passive navigation failures cannot extend the login wait budget."""
import asyncio
import time
from copy import deepcopy

import autojev.services.scenario_rerun as rerun_module
import pytest
from test_rerun_manual_login_watch import fast_watch, install_passive_browser
from test_scenario_rerun import make_service, wait_preparation


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["transient_navigation", "read_timeout"])
async def test_passive_navigation_failures_keep_absolute_deadline_and_clean_waiters(
    tmp_path, monkeypatch, failure,
):
    service, repository, source_id, _, _ = make_service(tmp_path)
    original_source = deepcopy(repository.get_exploration(source_id))
    fast_watch(service)
    service.intervention_timeout_seconds = 0.18
    service.manual_login_observation_timeout_seconds = 0.015
    never_ready = asyncio.Event() if failure == "read_timeout" else None
    browsers = install_passive_browser(monkeypatch, block_read=never_ready)
    executor_type = rerun_module.PlaywrightExecutor
    passive_observe = executor_type.observe_preparation
    browser_wait = executor_type.wait_for_user_input
    close_wait = executor_type.wait_until_inactive
    watcher = service._watch_manual_login
    observed_tasks = set()
    absolute_deadlines = []

    async def observe_with_transient_failure(executor):
        observed_tasks.add(asyncio.current_task())
        result = await passive_observe(executor)
        if failure == "transient_navigation":
            raise RuntimeError("Execution context was destroyed by fixture navigation")
        return result

    async def tracked_browser_wait(executor):
        observed_tasks.add(asyncio.current_task())
        return await browser_wait(executor)

    async def tracked_close_wait(executor):
        observed_tasks.add(asyncio.current_task())
        return await close_wait(executor)

    async def tracked_watcher(exploration_id, executor, deadline, *args):
        observed_tasks.add(asyncio.current_task())
        absolute_deadlines.append(deadline)
        return await watcher(exploration_id, executor, deadline, *args)

    monkeypatch.setattr(executor_type, "observe_preparation", observe_with_transient_failure)
    monkeypatch.setattr(executor_type, "wait_for_user_input", tracked_browser_wait)
    monkeypatch.setattr(executor_type, "wait_until_inactive", tracked_close_wait)
    monkeypatch.setattr(service, "_watch_manual_login", tracked_watcher)
    run_id = service.submit(source_id, "CT001")
    task = service.tasks[run_id]
    try:
        await wait_preparation(service, run_id, 1)
        service.set_preparation_consent(run_id, 1, 0, True)
        continuation_event = service.input_events[run_id]
        # A renewed wait on each transient DOM error would never finish. Keep
        # this external ceiling generous relative to the one 180 ms budget.
        await asyncio.wait_for(asyncio.shield(task), 2)
        browser = browsers[0]
        saved = repository.get_exploration(run_id)
        assert len(absolute_deadlines) == 1
        assert time.monotonic() >= absolute_deadlines[0]
        assert browser.passive_reads >= 3
        assert saved["status"] == "partial" and saved["error"] == "preparation_timeout"
        assert saved["actions"] == [] and saved["steps_completed"] == 0
        assert saved["scenarios"][0]["evidence"]["action_dispatched"] is False
        assert not browser.actions and not browser.login_attempts
        assert browser.closed and browser.active_passive_reads == 0
        assert observed_tasks and all(waiter.done() for waiter in observed_tasks)
        assert not continuation_event.is_set()
        assert service.state_for(run_id)["phase"] == "finished"
        assert run_id not in service.preparation_consents
        assert repository.get_exploration(source_id) == original_source
        if failure == "read_timeout":
            assert browser.cancelled_passive_reads == browser.passive_reads
    finally:
        if not task.done():
            service.cancel(run_id)
        await asyncio.gather(task, return_exceptions=True)
