import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from autojev.adapters.playwright_executor import BrowserUnavailableError
from autojev.domain.models import (
    ActionResult,
    AgentDecision,
    Observation,
    ScenarioStatus,
    utc_now,
)
from autojev.services.exploration import ExplorationManager


def test_login_form_requires_user_intervention_even_without_login_label():
    observation = Observation(
        url="https://www.saucedemo.com/",
        title="Swag Labs",
        text="Swag Labs Username Password for all users",
        interactive_elements=[
            {"ref": "e0", "tag": "input", "type": "text", "name": "user-name"},
            {"ref": "e1", "tag": "input", "type": "password", "name": ""},
            {"ref": "e2", "tag": "input", "type": "submit", "name": "Login"},
        ],
    )

    assert ExplorationManager._needs_login(observation) is True


def test_action_fingerprint_distinguishes_spa_hash_routes():
    decision = AgentDecision(action="click", target="e0", rationale="test")

    first = ExplorationManager._fingerprint("https://app.test/#/clientes", decision)
    second = ExplorationManager._fingerprint("https://app.test/#/produtos", decision)

    assert first != second


def test_action_fingerprint_scopes_reused_refs_to_semantic_state():
    decision = AgentDecision(action="click", target="e0", rationale="test")

    modal = ExplorationManager._fingerprint(
        "https://app.test/dashboard", decision, "state:modal"
    )
    submenu = ExplorationManager._fingerprint(
        "https://app.test/dashboard", decision, "state:submenu"
    )

    assert modal != submenu


def test_successful_restorations_never_accumulate_as_a_stalled_loop():
    streak = 0
    for _ in range(5):
        streak = ExplorationManager._next_failed_restoration_streak(
            streak,
            is_restoration=True,
            success=True,
            state_changed=True,
        )

    assert streak == 0


def test_only_consecutive_restorations_without_progress_trip_the_guard():
    streak = ExplorationManager._next_failed_restoration_streak(
        0,
        is_restoration=True,
        success=False,
        state_changed=False,
    )
    streak = ExplorationManager._next_failed_restoration_streak(
        streak,
        is_restoration=True,
        success=True,
        state_changed=False,
    )

    assert streak == 2
    assert (
        ExplorationManager._next_failed_restoration_streak(
            streak,
            is_restoration=True,
            success=True,
            state_changed=True,
        )
        == 0
    )


def test_observable_state_delta_becomes_verified_pass_evidence():
    decision = AgentDecision(action="click", target="e0", rationale="Open details")
    result = ActionResult(
        action="click",
        success=True,
        started_at=utc_now(),
        finished_at=utc_now(),
        before_url="https://app.test/",
        after_url="https://app.test/details",
    )
    observation = Observation(
        url=result.after_url,
        title="Details",
        text="Details opened",
        interactive_elements=[],
    )

    ExplorationManager._append_observable_state_evidence(
        result,
        decision,
        observation,
        state_changed=True,
        capture_console_errors=True,
        capture_network_errors=True,
    )

    assert [item.kind for item in result.assertions] == [
        "observable_state_change",
        "no_new_browser_errors",
    ]
    assert all(item.passed for item in result.assertions)
    scenario = ExplorationManager._scenario_from_evidence(
        observation, decision, result, 1
    )
    assert scenario.status == ScenarioStatus.PASS


def test_state_delta_with_correlated_browser_error_is_fail_evidence():
    decision = AgentDecision(action="select", target="e1", rationale="Filter status")
    result = ActionResult(
        action="select",
        success=True,
        started_at=utc_now(),
        finished_at=utc_now(),
        before_url="https://app.test/orders",
        after_url="https://app.test/orders",
    )
    observation = Observation(
        url=result.after_url,
        title="Orders",
        text="Loading failed",
        interactive_elements=[],
        network_errors=[{"url": "/api/orders", "status_code": 500}],
    )

    ExplorationManager._append_observable_state_evidence(
        result,
        decision,
        observation,
        state_changed=True,
        capture_console_errors=True,
        capture_network_errors=True,
    )

    assert result.assertions[-1].kind == "no_new_browser_errors"
    assert result.assertions[-1].passed is False
    scenario = ExplorationManager._scenario_from_evidence(
        observation, decision, result, 2
    )
    assert scenario.status == ScenarioStatus.FAIL


@pytest.mark.asyncio
async def test_all_local_scenarios_are_persisted_before_auxiliary_enrichment():
    class RecordingRepository:
        def __init__(self):
            self.scenarios = []
            self.events = []

        def add_scenario(self, exploration_id, sequence, scenario):
            self.scenarios.append((sequence, scenario.title))

        def add_screenshot(self, *args):
            raise AssertionError("This fixture has no screenshots")

        def add_event(self, exploration_id, level, event_type, message, payload=None):
            self.events.append((event_type, payload or {}))

        def is_cancel_requested(self, exploration_id):
            return False

    repository = RecordingRepository()

    class Auxiliary:
        async def create_scenario(self, observation, decision, result, number):
            # The complete local set must exist before the first provider call.
            assert repository.scenarios[:2] == [
                (1, "Explore click on Dashboard"),
                (2, "Explore click on Dashboard"),
            ]
            scenario = ExplorationManager._scenario_from_evidence(
                observation, decision, result, number
            )
            scenario.title = f"Enriched {number}"
            return scenario

    manager = object.__new__(ExplorationManager)
    manager.repository = repository

    async def ignore_usage(*args, **kwargs):
        return None

    manager._record_model_usage = ignore_usage
    observation = Observation(
        url="https://example.test/dashboard",
        title="Dashboard",
        text="Dashboard ready",
        interactive_elements=[],
    )
    decision = AgentDecision(action="click", target="e1", rationale="Open dashboard")
    result = ActionResult(
        action="click",
        success=True,
        started_at=utc_now(),
        finished_at=utc_now(),
        before_url="https://example.test/",
        after_url=observation.url,
        message="Opened",
    )
    pending = [
        (observation, decision, result, 1),
        (observation, decision, result, 2),
    ]

    await manager._materialize_scenarios(
        "run-1", pending, Auxiliary(), object(), use_local_evidence=False
    )

    assert repository.scenarios[:2] == [
        (1, "Explore click on Dashboard"),
        (2, "Explore click on Dashboard"),
    ]
    assert repository.scenarios[2:] == [(1, "Enriched 1"), (2, "Enriched 2")]
    assert repository.events[1][0] == "scenario_drafts_ready"
    assert repository.events[-1] == (
        "scenario_postprocessing_completed",
        {
            "current": 2,
            "total": 2,
            "enriched": 2,
            "local_evidence": 0,
            "reason": "documentation_complete",
            "stage": "scenarios_complete",
        },
    )


@pytest.mark.asyncio
async def test_waiting_for_login_stops_as_soon_as_browser_closes():
    class Repository:
        def __init__(self):
            self.events = []

        def update_exploration(self, *args, **kwargs):
            return None

        def add_event(self, exploration_id, level, event_type, message, payload=None):
            self.events.append(event_type)

        def is_cancel_requested(self, exploration_id):
            return False

    class Executor:
        async def show_intervention_panel(self, message):
            return None

        async def wait_for_user_input(self):
            await asyncio.Event().wait()

        async def wait_until_inactive(self):
            await asyncio.sleep(0)

        async def is_login_page(self):
            return True

    manager = object.__new__(ExplorationManager)
    manager.repository = Repository()
    manager.input_events = {}
    observation = Observation(
        url="https://example.test/login",
        title="Login",
        text="Username Password",
        interactive_elements=[],
    )

    outcome = await asyncio.wait_for(
        manager._wait_for_user_input(
            "run-closed-login", observation, Executor()
        ),
        timeout=0.5,
    )

    assert outcome == "browser_closed"
    assert manager.repository.events == ["waiting_input"]


@pytest.mark.asyncio
async def test_closed_browser_cancels_run_before_any_jev_call(monkeypatch, tmp_path):
    class Repository:
        def __init__(self):
            self.events = []
            self.updates = []

        def get_exploration(self, exploration_id, include_details=False):
            return {
                "id": exploration_id,
                "app_url": "https://example.test/",
                "cancel_requested": False,
                "agent": {"provider_id": "provider", "model_id": "jev"},
                "auxiliary_llm": {
                    "provider_id": "provider",
                    "model_id": "auxiliary",
                },
                "options": {
                    "max_steps": None,
                    "max_duration_seconds": None,
                    "generate_test_cases": False,
                    "generate_html_report": False,
                },
            }

        def update_exploration(self, exploration_id, **values):
            self.updates.append(values)

        def add_event(self, exploration_id, level, event_type, message, payload=None):
            self.events.append((level, event_type, message))

        def is_cancel_requested(self, exploration_id):
            return False

    class Vault:
        def __init__(self):
            self.discarded = []

        def get(self, exploration_id):
            return {}

        def discard(self, exploration_id):
            self.discarded.append(exploration_id)

    class Client:
        async def chat_json(self, *args, **kwargs):
            raise AssertionError("JEV must not be called after the browser closes")

    class ClosedExecutor:
        def __init__(self, *args, **kwargs):
            self.closed = False

        async def start(self, url):
            return Observation(
                url=url,
                title="Home",
                text="Ready",
                interactive_elements=[
                    {"ref": "e0", "tag": "button", "name": "Open"}
                ],
            )

        async def show_live_panel(self):
            return None

        async def update_live_panel(self, *args):
            return None

        def is_active(self):
            return False

        async def close(self):
            self.closed = True

    repository = Repository()
    vault = Vault()
    settings = SimpleNamespace(
        artifact_dir=Path(tmp_path),
        headless=True,
        browser="chromium",
    )
    manager = ExplorationManager(repository, vault, object(), settings)
    manager._client_for = lambda provider_id: Client()
    monkeypatch.setattr(
        "autojev.services.exploration.PlaywrightExecutor", ClosedExecutor
    )

    await manager._run("run-browser-closed")

    assert any(event[1] == "browser_closed" for event in repository.events)
    assert not any(event[1] == "failed" for event in repository.events)
    final_update = repository.updates[-1]
    assert final_update["status"].value == "cancelled"
    assert final_update["error"] is None
    assert vault.discarded == ["run-browser-closed"]


@pytest.mark.asyncio
async def test_browser_close_cancels_an_in_flight_jev_decision():
    inactive = asyncio.Event()
    model_cancelled = asyncio.Event()

    class Executor:
        async def wait_until_inactive(self):
            await inactive.wait()

    async def slow_decision():
        try:
            await asyncio.Event().wait()
        finally:
            model_cancelled.set()

    task = asyncio.create_task(
        ExplorationManager._await_decision_while_browser_active(
            Executor(), slow_decision(), timeout=5
        )
    )
    await asyncio.sleep(0)
    inactive.set()

    with pytest.raises(BrowserUnavailableError, match="closed"):
        await task
    assert model_cancelled.is_set() is True
