"""observe() rides out a navigation that starts after an action returned."""

from pathlib import Path

import pytest

from autojev.adapters.playwright_executor import BrowserUnavailableError, PlaywrightExecutor
from autojev.domain.models import ExplorationOptions, Observation


class FakePage:
    def __init__(self):
        self.waited = []

    def is_closed(self):
        return False

    async def wait_for_load_state(self, state, timeout=None):
        self.waited.append(("load", state))

    async def wait_for_timeout(self, milliseconds):
        self.waited.append(("pause", milliseconds))


def executor_failing(errors, tmp_path):
    executor = PlaywrightExecutor(Path(tmp_path), ExplorationOptions())
    executor._page = FakePage()
    calls = []

    async def once(label="page", *, capture_screenshot=True):
        calls.append((label, capture_screenshot))
        if errors:
            raise errors.pop(0)
        return Observation(url="https://app.test/", title="t", text="", interactive_elements=[])

    executor._observe_once = once
    return executor, calls


async def test_a_destroyed_context_is_waited_out_and_the_page_is_read_again(tmp_path):
    errors = [
        Exception("Locator.count: Execution context was destroyed, most likely because of a navigation"),
        Exception("Frame was detached"),
    ]
    executor, calls = executor_failing(errors, tmp_path)
    observation = await executor.observe("go_back", capture_screenshot=False)
    assert observation.url == "https://app.test/" and len(calls) == 3
    assert all(call == ("go_back", False) for call in calls)                       # same request each time
    assert [w for w in executor._page.waited if w[0] == "load"]                    # it waited for the new document


async def test_a_real_error_is_not_swallowed(tmp_path):
    executor, calls = executor_failing([ValueError("boom")], tmp_path)
    with pytest.raises(ValueError, match="boom"):
        await executor.observe("x")
    assert len(calls) == 1


async def test_a_navigation_that_never_ends_still_fails_after_bounded_retries(tmp_path):
    errors = [Exception("Execution context was destroyed") for _ in range(10)]
    executor, calls = executor_failing(errors, tmp_path)
    with pytest.raises(Exception, match="Execution context was destroyed"):
        await executor.observe("x")
    assert len(calls) == 4


async def test_a_closed_browser_is_reported_not_retried(tmp_path):
    executor, calls = executor_failing([BrowserUnavailableError("closed")], tmp_path)
    with pytest.raises(BrowserUnavailableError):
        await executor.observe("x")
    assert len(calls) == 1
