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


def _policy_observation(elements, *, network_errors=None):
    return Observation(
        url="https://app.test/products",
        title="Products",
        text="Products",
        interactive_elements=elements,
        network_errors=network_errors or [],
    )


def test_clear_control_is_routed_locally_without_a_jev_call():
    observed = _policy_observation(
        [{"ref": "e1", "tag": "button", "name": "Open menu"}]
    )
    decision, reason, consultation_key = ExplorationManager._deterministic_decision(
        observed,
        {
            "state_key": "products",
            "current_untried": [
                {
                    "ref": "e1",
                    "name": "Open menu",
                    "suggested_action": "click",
                }
            ],
        },
        consulted_policy_keys=set(),
        consecutive_no_progress=0,
    )

    assert decision is not None
    assert (decision.action, decision.target) == ("click", "e1")
    assert reason == "single_clear_control"
    assert consultation_key is None


def test_http_error_is_recorded_locally_and_does_not_force_a_jev_call():
    observed = _policy_observation(
        [{"ref": "e1", "tag": "button", "name": "Retry"}],
        network_errors=[
            {
                "type": "http",
                "url": "https://app.test/api/products",
                "status_code": 500,
            }
        ],
    )
    decision, reason, _ = ExplorationManager._deterministic_decision(
        observed,
        {
            "state_key": "products-error",
            "current_untried": [
                {
                    "ref": "e1",
                    "name": "Retry",
                    "suggested_action": "click",
                }
            ],
        },
        consulted_policy_keys=set(),
        consecutive_no_progress=0,
    )

    assert decision is not None
    assert reason == "single_clear_control"


def test_duplicate_control_names_escalate_once_then_use_coverage_memory():
    observed = _policy_observation(
        [
            {"ref": "e1", "tag": "button", "name": "Add to cart"},
            {"ref": "e2", "tag": "button", "name": "Add to cart"},
        ]
    )
    context = {
        "state_key": "products",
        "current_untried": [
            {"ref": "e1", "name": "Add to cart", "suggested_action": "click"},
            {"ref": "e2", "name": "Add to cart", "suggested_action": "click"},
        ],
    }

    decision, reason, key = ExplorationManager._deterministic_decision(
        observed,
        context,
        consulted_policy_keys=set(),
        consecutive_no_progress=0,
    )
    assert decision is None
    assert reason == "ambiguous_duplicate_controls"
    assert key is not None

    decision, reason, _ = ExplorationManager._deterministic_decision(
        observed,
        context,
        consulted_policy_keys={key},
        consecutive_no_progress=0,
    )
    assert decision is not None
    assert reason == "clear_safe_frontier"


def test_conventional_form_is_deterministic_but_stalled_progress_escalates():
    observed = _policy_observation(
        [
            {"ref": "e1", "tag": "input", "type": "text", "name": "Name"},
            {"ref": "e2", "tag": "button", "name": "Continue"},
        ]
    )
    context = {
        "state_key": "checkout",
        "current_untried": [
            {
                "ref": "e1",
                "name": "Name",
                "suggested_action": "fill",
                "suggested_value": "AutoJev QA",
            },
            {"ref": "e2", "name": "Continue", "suggested_action": "click"},
        ],
    }

    decision, reason, _ = ExplorationManager._deterministic_decision(
        observed,
        context,
        consulted_policy_keys=set(),
        consecutive_no_progress=0,
    )
    assert decision is not None
    assert decision.action == "fill"
    assert reason == "conventional_form"

    decision, reason, key = ExplorationManager._deterministic_decision(
        observed,
        context,
        consulted_policy_keys=set(),
        consecutive_no_progress=2,
    )
    assert decision is None
    assert reason == "two_attempts_without_progress"
    assert key == "state:checkout"


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


def test_new_browser_error_without_visual_delta_is_still_fail_evidence():
    decision = AgentDecision(action="click", target="e1", rationale="Load data")
    result = ActionResult(
        action="click",
        success=True,
        started_at=utc_now(),
        finished_at=utc_now(),
        before_url="https://app.test/orders",
        after_url="https://app.test/orders",
    )
    observation = Observation(
        url=result.after_url,
        title="Orders",
        text="Orders",
        interactive_elements=[],
        network_errors=[
            {
                "type": "http",
                "url": "https://app.test/api/orders",
                "status_code": 500,
            }
        ],
    )

    ExplorationManager._append_observable_state_evidence(
        result,
        decision,
        observation,
        state_changed=False,
        capture_console_errors=True,
        capture_network_errors=True,
        new_issues=observation.network_errors,
    )

    assert [item.kind for item in result.assertions] == ["no_new_browser_errors"]
    assert result.assertions[0].passed is False
    assert ExplorationManager._scenario_capture_policy(
        decision_source="jev",
        is_restoration_action=False,
        result=result,
    ) == (True, None)
    assert (
        ExplorationManager._scenario_from_evidence(
            observation, decision, result, 3
        ).status
        == ScenarioStatus.FAIL
    )


def test_issue_emitted_while_jev_plans_does_not_fail_the_following_action():
    class RecordingRepository:
        def __init__(self):
            self.issues = []
            self.events = []

        def add_issue(self, exploration_id, issue):
            self.issues.append(issue)

        def add_event(self, exploration_id, level, event_type, message, payload=None):
            self.events.append((event_type, payload or {}))

    manager = object.__new__(ExplorationManager)
    manager.repository = RecordingRepository()
    known = set()
    first = Observation(
        url="https://app.test/orders",
        title="Orders",
        text="Ready",
        interactive_elements=[],
        console_errors=[
            {
                "type": "console",
                "message": (
                    "Failed to load resource: the server responded with a "
                    "status of 401 (Unauthorized)"
                ),
                "url": "https://app.test/orders",
                "occurred_at": "2026-01-01T12:00:00.100000+00:00",
            }
        ],
        network_errors=[
            {
                "type": "http",
                "method": "POST",
                "message": "POST returned HTTP 401",
                "url": "https://telemetry.test/events",
                "status_code": 401,
                "occurred_at": "2026-01-01T12:00:00.120000+00:00",
            }
        ],
        screenshot="initial.png",
    )

    baseline = manager._record_observation_issues("run-1", first, known)

    assert len(baseline) == 1
    assert baseline[0]["type"] == "http"
    assert baseline[0]["evidence_channels"] == ["network", "console"]
    assert baseline[0]["related_console_errors"][0]["type"] == "console"
    assert len(manager.repository.issues) == 1

    recurring = Observation(
        url=first.url,
        title=first.title,
        text=first.text,
        interactive_elements=[],
        console_errors=[
            {
                **first.console_errors[0],
                "occurred_at": "2026-01-01T12:01:00.100000+00:00",
            }
        ],
        network_errors=[
            {
                **first.network_errors[0],
                "occurred_at": "2026-01-01T12:01:00.120000+00:00",
            }
        ],
    )
    action_issues = manager._issues_relevant_to_action(
        recurring, "2026-01-01T12:01:01+00:00"
    )
    new_issues = manager._record_observation_issues("run-1", recurring, known)
    result = ActionResult(
        action="click",
        success=True,
        started_at="2026-01-01T12:01:01+00:00",
        finished_at=utc_now(),
        before_url="https://app.test/orders",
        after_url="https://app.test/orders/1",
    )

    manager._append_observable_state_evidence(
        result,
        AgentDecision(action="click", target="e1", rationale="Open order"),
        recurring,
        state_changed=True,
        capture_console_errors=True,
        capture_network_errors=True,
        new_issues=action_issues,
    )
    manager._keep_only_new_observation_issues(recurring, action_issues)

    assert new_issues == []
    assert action_issues == []
    # Recurrences remain visible in the correct CT/context even though only a
    # new signature is returned for model-prompt deduplication.
    assert len(manager.repository.issues) == 2
    assert manager.repository.issues[0]["occurrence_id"] != manager.repository.issues[1]["occurrence_id"]
    assert result.assertions[-1].passed is True
    assert result.assertions[-1].actual == {
        "console_errors": 0,
        "network_errors": 0,
    }
    assert recurring.console_errors == []
    assert recurring.network_errors == []


def test_repeated_nonbaseline_first_party_failure_remains_action_evidence():
    observation = Observation(
        url="https://app.test/orders",
        title="Orders",
        text="Orders",
        interactive_elements=[],
        network_errors=[
            {
                "type": "http",
                "method": "GET",
                "message": "GET returned HTTP 500",
                "url": "https://app.test/api/orders",
                "status_code": 500,
            }
        ],
    )
    first_action = ExplorationManager._issues_relevant_to_action(observation)
    later_action = ExplorationManager._issues_relevant_to_action(observation)

    assert len(first_action) == 1
    assert len(later_action) == 1


def test_issue_deduplication_does_not_merge_two_same_status_requests():
    observation = Observation(
        url="https://app.test/orders",
        title="Orders",
        text="Orders",
        interactive_elements=[],
        console_errors=[
            {
                "type": "console",
                "message": (
                    "Failed to load resource: the server responded with a "
                    "status of 401 (Unauthorized)"
                ),
                "occurred_at": "2026-01-01T12:00:00.100000+00:00",
            }
        ],
        network_errors=[
            {
                "type": "http",
                "method": "POST",
                "message": "POST returned HTTP 401",
                "url": "https://telemetry.test/unique-events",
                "status_code": 401,
                "occurred_at": "2026-01-01T12:00:00.120000+00:00",
            },
            {
                "type": "http",
                "method": "POST",
                "message": "POST returned HTTP 401",
                "url": "https://telemetry.test/summed-events",
                "status_code": 401,
                "occurred_at": "2026-01-01T12:00:00.800000+00:00",
            },
        ],
    )

    issues = ExplorationManager._deduplicate_observation_issues(observation)

    # One generic console record and two plausible requests are ambiguous.
    # Preserve all records instead of inventing a request association.
    assert len(issues) == 3
    assert issues[0]["type"] == "console"
    assert issues[1]["url"] == "https://telemetry.test/unique-events"
    assert "evidence_channels" not in issues[1]
    assert issues[2]["url"] == "https://telemetry.test/summed-events"


def test_action_issue_attribution_excludes_errors_emitted_before_action_start():
    observation = Observation(
        url="https://app.test/orders",
        title="Orders",
        text="Orders",
        interactive_elements=[],
        network_errors=[
            {
                "type": "http",
                "method": "POST",
                "message": "POST returned HTTP 401",
                "url": "https://telemetry.test/events",
                "status_code": 401,
                "occurred_at": "2026-01-01T12:00:00+00:00",
            },
            {
                "type": "http",
                "method": "GET",
                "message": "GET returned HTTP 500",
                "url": "https://app.test/api/orders",
                "status_code": 500,
                "occurred_at": "2026-01-01T12:00:02+00:00",
            },
        ],
    )

    relevant = ExplorationManager._issues_relevant_to_action(
        observation,
        "2026-01-01T12:00:01+00:00",
    )

    assert [issue["status_code"] for issue in relevant] == [500]


def test_scenario_capture_policy_excludes_mechanics_and_successful_noops():
    success_noop = ActionResult(
        action="click",
        success=True,
        started_at=utc_now(),
        finished_at=utc_now(),
        before_url="https://app.test/cart",
        after_url="https://app.test/cart",
    )
    failed = ActionResult(
        action="click",
        success=False,
        started_at=utc_now(),
        finished_at=utc_now(),
        before_url="https://app.test/cart",
        after_url="https://app.test/cart",
        message="Timeout",
    )
    blocked = ActionResult(
        action="go_back",
        success=False,
        started_at=utc_now(),
        finished_at=utc_now(),
        before_url="https://app.test/cart",
        after_url="https://app.test/cart",
        message="PolicyBlocked: authentication boundary",
    )

    assert ExplorationManager._scenario_capture_policy(
        decision_source="coverage",
        is_restoration_action=False,
        result=failed,
    ) == (False, "mechanical_coverage_action")
    assert ExplorationManager._scenario_capture_policy(
        decision_source="jev",
        is_restoration_action=False,
        result=success_noop,
    ) == (False, "successful_action_without_verifiable_evidence")
    assert ExplorationManager._scenario_capture_policy(
        decision_source="jev",
        is_restoration_action=False,
        result=failed,
    ) == (True, None)
    assert ExplorationManager._scenario_capture_policy(
        decision_source="jev",
        is_restoration_action=False,
        result=blocked,
    ) == (True, None)


def test_scenario_retry_replaces_provisional_failure_and_keeps_sequence():
    pending = {}
    omitted = set()
    first_observation = Observation(
        url="https://app.test/cart",
        title="Cart",
        text="Cart",
        interactive_elements=[],
    )
    retry_observation = Observation(
        url="https://app.test/checkout",
        title="Checkout",
        text="Checkout",
        interactive_elements=[],
    )
    failed_decision = AgentDecision(
        action="click", target="e1", rationale="Continue"
    )
    retry_decision = AgentDecision(
        action="click", target="e19", rationale="Continue"
    )
    failed = ActionResult(
        action="click",
        success=False,
        started_at=utc_now(),
        finished_at=utc_now(),
        before_url=first_observation.url,
        after_url=first_observation.url,
        message="Locator became stale",
    )
    recovered = ActionResult(
        action="click",
        success=True,
        started_at=utc_now(),
        finished_at=utc_now(),
        before_url=first_observation.url,
        after_url=retry_observation.url,
        message="Opened checkout",
    )
    ExplorationManager._append_observable_state_evidence(
        recovered,
        retry_decision,
        retry_observation,
        state_changed=True,
        capture_console_errors=False,
        capture_network_errors=False,
    )

    sequence, _, _ = ExplorationManager._reconcile_pending_scenario(
        pending,
        omitted,
        "semantic-continue",
        first_observation,
        failed_decision,
        failed,
        0,
        decision_source="jev",
        is_restoration_action=False,
    )
    sequence, reason, emit = ExplorationManager._reconcile_pending_scenario(
        pending,
        omitted,
        "semantic-continue",
        retry_observation,
        retry_decision,
        recovered,
        sequence,
        decision_source="jev",
        is_restoration_action=False,
    )

    assert sequence == 1
    assert reason is None
    assert emit is False
    assert pending["semantic-continue"] == (
        retry_observation,
        retry_decision,
        recovered,
        1,
    )
    scenario = ExplorationManager._scenario_from_evidence(
        *pending["semantic-continue"]
    )
    assert scenario.status == ScenarioStatus.PASS


def test_successful_no_evidence_retry_removes_false_failure_once():
    pending = {}
    omitted = set()
    observation = Observation(
        url="https://app.test/cart",
        title="Cart",
        text="Cart",
        interactive_elements=[],
    )
    failed_decision = AgentDecision(
        action="click", target="e1", rationale="Refresh cart"
    )
    retry_decision = AgentDecision(
        action="click", target="e8", rationale="Refresh cart"
    )
    failed = ActionResult(
        action="click",
        success=False,
        started_at=utc_now(),
        finished_at=utc_now(),
        before_url=observation.url,
        after_url=observation.url,
        message="Locator became stale",
    )
    recovered_noop = ActionResult(
        action="click",
        success=True,
        started_at=utc_now(),
        finished_at=utc_now(),
        before_url=observation.url,
        after_url=observation.url,
        message="Clicked; no observable state change",
    )

    sequence, _, _ = ExplorationManager._reconcile_pending_scenario(
        pending,
        omitted,
        "semantic-refresh",
        observation,
        failed_decision,
        failed,
        0,
        decision_source="jev",
        is_restoration_action=False,
    )
    sequence, reason, emit = ExplorationManager._reconcile_pending_scenario(
        pending,
        omitted,
        "semantic-refresh",
        observation,
        retry_decision,
        recovered_noop,
        sequence,
        decision_source="jev",
        is_restoration_action=False,
    )
    _, repeated_reason, repeated_emit = (
        ExplorationManager._reconcile_pending_scenario(
            pending,
            omitted,
            "semantic-refresh",
            observation,
            retry_decision,
            recovered_noop,
            sequence,
            decision_source="jev",
            is_restoration_action=False,
        )
    )

    assert sequence == 1
    assert pending == {}
    assert reason == "successful_action_without_verifiable_evidence"
    assert emit is True
    assert repeated_reason == reason
    assert repeated_emit is False


def test_redundant_noop_does_not_erase_an_assertion_backed_scenario():
    pending = {}
    omitted = set()
    observation = Observation(
        url="https://app.test/cart",
        title="Cart",
        text="Cart",
        interactive_elements=[],
    )
    decision = AgentDecision(action="click", target="e1", rationale="Refresh cart")
    verified = ActionResult(
        action="click",
        success=True,
        started_at=utc_now(),
        finished_at=utc_now(),
        before_url=observation.url,
        after_url=observation.url,
        message="Cart refreshed",
    )
    ExplorationManager._append_observable_state_evidence(
        verified,
        decision,
        observation,
        state_changed=True,
        capture_console_errors=False,
        capture_network_errors=False,
    )
    redundant_noop = ActionResult(
        action="click",
        success=True,
        started_at=utc_now(),
        finished_at=utc_now(),
        before_url=observation.url,
        after_url=observation.url,
        message="Clicked; no observable state change",
    )

    sequence, _, _ = ExplorationManager._reconcile_pending_scenario(
        pending,
        omitted,
        "semantic-refresh",
        observation,
        decision,
        verified,
        0,
        decision_source="jev",
        is_restoration_action=False,
    )
    sequence, reason, emit = ExplorationManager._reconcile_pending_scenario(
        pending,
        omitted,
        "semantic-refresh",
        observation,
        decision,
        redundant_noop,
        sequence,
        decision_source="jev",
        is_restoration_action=False,
    )

    assert sequence == 1
    assert reason == "successful_action_without_verifiable_evidence"
    assert emit is False
    assert pending["semantic-refresh"][2] is verified
    assert omitted == set()


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


class _LiveScenarioRepository:
    """Small in-memory contract used by the live CT pipeline tests."""

    def __init__(self):
        self.cancel_requested = False
        self.scenarios = {}
        self.writes = []
        self.events = []

    def add_scenario(self, exploration_id, sequence, scenario):
        self.scenarios[sequence] = scenario
        self.writes.append((sequence, scenario.title))

    def delete_scenario(self, exploration_id, scenario_id):
        self.scenarios = {
            sequence: scenario
            for sequence, scenario in self.scenarios.items()
            if scenario.id != scenario_id
        }

    # Keep the fake compatible with either repository verb while the internal
    # pipeline remains private to ExplorationManager.
    remove_scenario = delete_scenario

    def add_screenshot(self, *args):
        return None

    def add_event(self, exploration_id, level, event_type, message, payload=None):
        self.events.append((event_type, payload or {}))

    def is_cancel_requested(self, exploration_id):
        return self.cancel_requested


def _live_scenario_pipeline(repository, auxiliary, *, use_local_evidence=False):
    from autojev.services import exploration as exploration_service

    manager = object.__new__(ExplorationManager)
    manager.repository = repository

    async def ignore_usage(*args, **kwargs):
        return None

    manager._record_model_usage = ignore_usage
    pipeline_type = exploration_service._LiveScenarioPipeline
    return pipeline_type(
        manager=manager,
        exploration_id="run-live-scenarios",
        auxiliary=auxiliary,
        auxiliary_client=object(),
        use_local_evidence=use_local_evidence,
    )


def _live_scenario_evidence(
    *,
    title="Cart",
    target="e1",
    success=True,
    message="Cart opened",
):
    observation = Observation(
        url=f"https://example.test/{title.lower()}",
        title=title,
        text=f"{title} ready",
        interactive_elements=[],
    )
    decision = AgentDecision(
        action="click",
        target=target,
        rationale=f"Open {title}",
    )
    result = ActionResult(
        action="click",
        success=success,
        started_at=utc_now(),
        finished_at=utc_now(),
        before_url="https://example.test/",
        after_url=observation.url if success else "https://example.test/",
        message=message,
    )
    if success:
        ExplorationManager._append_observable_state_evidence(
            result,
            decision,
            observation,
            state_changed=True,
            capture_console_errors=False,
            capture_network_errors=False,
        )
    return observation, decision, result


@pytest.mark.asyncio
async def test_live_scenario_capture_persists_draft_without_waiting_for_auxiliary():
    repository = _LiveScenarioRepository()
    enrichment_started = asyncio.Event()
    release_enrichment = asyncio.Event()

    class Auxiliary:
        async def create_scenario(self, observation, decision, result, number):
            enrichment_started.set()
            await release_enrichment.wait()
            scenario = ExplorationManager._scenario_from_evidence(
                observation, decision, result, number
            )
            scenario.title = f"Enriched {number}"
            return scenario

    pipeline = _live_scenario_pipeline(repository, Auxiliary())
    await pipeline.start()
    first = _live_scenario_evidence(title="Cart", target="e1")

    # capture() belongs to the browser loop. It must only persist/enqueue; a
    # slow provider call cannot hold the next Playwright action hostage.
    await asyncio.wait_for(
        pipeline.capture("cart", *first, sequence=1), timeout=0.2
    )
    assert repository.scenarios[1].title == "Explore click on Cart"
    await asyncio.wait_for(enrichment_started.wait(), timeout=0.2)

    second = _live_scenario_evidence(title="Checkout", target="e2")
    await asyncio.wait_for(
        pipeline.capture("checkout", *second, sequence=2), timeout=0.2
    )
    assert repository.scenarios[2].title == "Explore click on Checkout"

    release_enrichment.set()
    await asyncio.wait_for(pipeline.finish(), timeout=1)
    assert [repository.scenarios[index].title for index in (1, 2)] == [
        "Enriched 1",
        "Enriched 2",
    ]


@pytest.mark.asyncio
async def test_exploration_loop_advances_while_first_scenario_is_being_enriched(
    monkeypatch, tmp_path
):
    enrichment_started = asyncio.Event()
    release_enrichment = asyncio.Event()
    enrichment_finished = asyncio.Event()
    second_planning_started = asyncio.Event()

    class Repository:
        def __init__(self):
            self.events = []
            self.actions = []
            self.scenarios = {}
            self.scenario_writes = []
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
                    "max_steps": 2,
                    "max_duration_seconds": None,
                    "capture_screenshots": False,
                    "capture_console_errors": False,
                    "capture_network_errors": False,
                    "generate_test_cases": True,
                    "generate_html_report": False,
                },
            }

        def update_exploration(self, exploration_id, **values):
            self.updates.append(values)

        def add_event(self, exploration_id, level, event_type, message, payload=None):
            self.events.append((event_type, message, payload or {}))

        def add_action(self, exploration_id, sequence, decision, result):
            self.actions.append((sequence, decision, result))

        def add_scenario(self, exploration_id, sequence, scenario):
            self.scenarios[sequence] = scenario
            self.scenario_writes.append((sequence, scenario.title))

        def delete_scenario(self, exploration_id, scenario_id):
            self.scenarios = {
                sequence: scenario
                for sequence, scenario in self.scenarios.items()
                if scenario.id != scenario_id
            }

        def add_screenshot(self, *args):
            return None

        def add_issue(self, *args):
            return None

        def is_cancel_requested(self, exploration_id):
            return False

    class Vault:
        def get(self, exploration_id):
            return {}

        def discard(self, exploration_id):
            return None

    class Agent:
        def __init__(self, *args):
            self.calls = 0

        async def decide(self, *args):
            self.calls += 1
            if self.calls == 1:
                return AgentDecision(
                    action="click", target="e1", rationale="Open cart"
                )

            # The next JEV planning cycle must start even though the auxiliary
            # provider is still documenting CT001.
            await asyncio.wait_for(enrichment_started.wait(), timeout=0.5)
            assert enrichment_finished.is_set() is False
            second_planning_started.set()
            return AgentDecision(
                action="click", target="e2", rationale="Open checkout"
            )

    class Auxiliary:
        def __init__(self, *args):
            pass

        async def create_scenario(self, observation, decision, result, number):
            if number == 1:
                enrichment_started.set()
                await release_enrichment.wait()
                enrichment_finished.set()
            scenario = ExplorationManager._scenario_from_evidence(
                observation, decision, result, number
            )
            scenario.title = f"Enriched {number}"
            return scenario

    class Executor:
        def __init__(self, *args, **kwargs):
            self.execute_calls = 0

        async def start(self, url):
            return Observation(
                url=url,
                title="Start",
                text="Ready",
                interactive_elements=[
                    {"ref": "e1", "tag": "button", "name": "Cart"},
                    {"ref": "e2", "tag": "button", "name": "Checkout"},
                ],
            )

        async def execute(self, decision, credentials):
            self.execute_calls += 1
            if self.execute_calls == 2:
                assert second_planning_started.is_set() is True
                assert enrichment_finished.is_set() is False
                # CT001 is already durable while its enrichment is blocked.
                assert repository.scenarios[1].title == "Explore click on Page 1"
                release_enrichment.set()
            observation = Observation(
                url=f"https://example.test/page-{self.execute_calls}",
                title=f"Page {self.execute_calls}",
                text="Ready",
                interactive_elements=[
                    {"ref": "e1", "tag": "button", "name": "Cart"},
                    {"ref": "e2", "tag": "button", "name": "Checkout"},
                ],
            )
            return (
                ActionResult(
                    action=decision.action,
                    success=True,
                    started_at=utc_now(),
                    finished_at=utc_now(),
                    before_url="https://example.test/",
                    after_url=observation.url,
                    target=decision.target,
                    message="Opened",
                ),
                observation,
            )

        async def show_live_panel(self):
            return None

        async def update_live_panel(self, *args):
            return None

        async def wait_until_inactive(self):
            await asyncio.Event().wait()

        def is_active(self):
            return True

        async def close(self):
            return None

    class Coverage:
        def __init__(self, *args):
            pass

        def observe(self, observation):
            return None

        def can_finish(self):
            return False

        def next_restoration_action(self, observation):
            return None

        def reconcile(self, observation, decision):
            return decision, None

        def state_key(self, observation):
            return observation.title

        def effect_key(self, observation):
            return observation.title

        def decision_key(self, observation, decision):
            return f"{decision.action}:{decision.target}"

        def record_action(self, *args):
            return None

        def context(self, observation):
            return {
                "known_states": 1,
                "frontier_states": 1,
                "frontier_candidates": 1,
                "candidates_attempted": len(repository.actions),
                "current_untried": [],
                "path_depth": 0,
            }

    repository = Repository()
    settings = SimpleNamespace(
        artifact_dir=Path(tmp_path),
        headless=True,
        browser="chromium",
    )
    manager = ExplorationManager(repository, Vault(), object(), settings)
    manager._client_for = lambda provider_id: object()

    async def ignore_usage(*args, **kwargs):
        return None

    manager._record_model_usage = ignore_usage
    monkeypatch.setattr("autojev.services.exploration.OpenAIJevAgent", Agent)
    monkeypatch.setattr(
        "autojev.services.exploration.OpenAIAuxiliaryScenarioGenerator", Auxiliary
    )
    monkeypatch.setattr("autojev.services.exploration.PlaywrightExecutor", Executor)
    monkeypatch.setattr("autojev.services.exploration.CoveragePlanner", Coverage)

    await asyncio.wait_for(manager._run("run-parallel-cts"), timeout=2)

    assert [event for event in repository.events if event[0] == "failed"] == []
    assert len(repository.actions) == 2, repository.events[-5:]
    assert second_planning_started.is_set() is True
    assert repository.scenario_writes[0] == (1, "Explore click on Page 1")
    assert [repository.scenarios[index].title for index in (1, 2)] == [
        "Enriched 1",
        "Enriched 2",
    ]


@pytest.mark.asyncio
async def test_live_scenario_retry_prevents_stale_enrichment_from_overwriting_draft():
    repository = _LiveScenarioRepository()
    first_enrichment_started = asyncio.Event()
    release_first_enrichment = asyncio.Event()

    class Auxiliary:
        async def create_scenario(self, observation, decision, result, number):
            scenario = ExplorationManager._scenario_from_evidence(
                observation, decision, result, number
            )
            if result.message == "stale locator failure":
                first_enrichment_started.set()
                await release_first_enrichment.wait()
                scenario.title = "STALE ENRICHMENT"
            else:
                scenario.title = "Fresh recovered scenario"
            return scenario

    pipeline = _live_scenario_pipeline(repository, Auxiliary())
    await pipeline.start()
    failed = _live_scenario_evidence(
        success=False,
        message="stale locator failure",
        target="e1",
    )
    await pipeline.capture("checkout", *failed, sequence=1)
    await asyncio.wait_for(first_enrichment_started.wait(), timeout=0.2)

    recovered = _live_scenario_evidence(
        success=True,
        message="checkout opened",
        target="e99",
    )
    await pipeline.capture("checkout", *recovered, sequence=1)
    assert repository.scenarios[1].status == ScenarioStatus.PASS

    # The response for revision 1 arrives after revision 2 was captured. It
    # must be discarded instead of reverting the current PASS to a stale FAIL.
    release_first_enrichment.set()
    await asyncio.wait_for(pipeline.finish(), timeout=1)
    assert repository.scenarios[1].title == "Fresh recovered scenario"
    assert "STALE ENRICHMENT" not in [title for _sequence, title in repository.writes]


@pytest.mark.asyncio
async def test_live_scenario_remove_invalidates_in_flight_provisional_draft():
    repository = _LiveScenarioRepository()
    enrichment_started = asyncio.Event()
    release_enrichment = asyncio.Event()

    class Auxiliary:
        async def create_scenario(self, observation, decision, result, number):
            enrichment_started.set()
            await release_enrichment.wait()
            scenario = ExplorationManager._scenario_from_evidence(
                observation, decision, result, number
            )
            scenario.title = "Late provisional failure"
            return scenario

    pipeline = _live_scenario_pipeline(repository, Auxiliary())
    await pipeline.start()
    failed = _live_scenario_evidence(
        success=False,
        message="temporary locator failure",
    )
    await pipeline.capture("refresh-cart", *failed, sequence=1)
    await asyncio.wait_for(enrichment_started.wait(), timeout=0.2)
    assert 1 in repository.scenarios

    removal = pipeline.remove("refresh-cart", sequence=1)
    if asyncio.iscoroutine(removal):
        await removal
    assert repository.scenarios == {}

    release_enrichment.set()
    await asyncio.wait_for(pipeline.finish(), timeout=1)
    assert repository.scenarios == {}
    assert "Late provisional failure" not in [
        title for _sequence, title in repository.writes
    ]


@pytest.mark.asyncio
async def test_live_scenario_finish_drains_current_enrichment_before_returning():
    repository = _LiveScenarioRepository()
    enrichment_started = asyncio.Event()
    release_enrichment = asyncio.Event()

    class Auxiliary:
        async def create_scenario(self, observation, decision, result, number):
            enrichment_started.set()
            await release_enrichment.wait()
            scenario = ExplorationManager._scenario_from_evidence(
                observation, decision, result, number
            )
            scenario.title = "Documented while exploring"
            return scenario

    pipeline = _live_scenario_pipeline(repository, Auxiliary())
    await pipeline.start()
    evidence = _live_scenario_evidence()
    await pipeline.capture("cart", *evidence, sequence=1)
    await asyncio.wait_for(enrichment_started.wait(), timeout=0.2)

    finish = asyncio.create_task(pipeline.finish())
    await asyncio.sleep(0)
    assert finish.done() is False
    release_enrichment.set()
    await asyncio.wait_for(finish, timeout=1)

    assert repository.scenarios[1].title == "Documented while exploring"
    assert any(
        event_type == "scenario_postprocessing_completed"
        for event_type, _payload in repository.events
    )


@pytest.mark.asyncio
async def test_live_scenario_cancel_stops_provider_and_preserves_local_draft():
    repository = _LiveScenarioRepository()
    enrichment_started = asyncio.Event()
    enrichment_cancelled = asyncio.Event()

    class Auxiliary:
        async def create_scenario(self, observation, decision, result, number):
            enrichment_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                enrichment_cancelled.set()

    pipeline = _live_scenario_pipeline(repository, Auxiliary())
    await pipeline.start()
    evidence = _live_scenario_evidence()
    await pipeline.capture("cart", *evidence, sequence=1)
    await asyncio.wait_for(enrichment_started.wait(), timeout=0.2)

    await asyncio.wait_for(pipeline.finish(cancelled=True), timeout=0.5)

    assert enrichment_cancelled.is_set() is True
    assert repository.scenarios[1].title == "Explore click on Cart"
    assert repository.scenarios[1].status == ScenarioStatus.PASS


@pytest.mark.asyncio
async def test_live_scenario_cancelled_finish_awaits_provider_cleanup():
    repository = _LiveScenarioRepository()
    enrichment_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    active_provider_calls = 0

    class Auxiliary:
        async def create_scenario(self, observation, decision, result, number):
            nonlocal active_provider_calls
            active_provider_calls += 1
            enrichment_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_started.set()
                await release_cleanup.wait()
                active_provider_calls -= 1

    pipeline = _live_scenario_pipeline(repository, Auxiliary())
    await pipeline.start()
    await pipeline.capture(
        "cart", *_live_scenario_evidence(), sequence=1
    )
    await asyncio.wait_for(enrichment_started.wait(), timeout=0.2)

    finish = asyncio.create_task(pipeline.finish(cancelled=True))
    await asyncio.wait_for(cleanup_started.wait(), timeout=0.2)

    # Returning before the provider coroutine unwinds would leave paid work
    # running after AutoJev already told the user that cancellation completed.
    assert finish.done() is False
    assert active_provider_calls == 1

    release_cleanup.set()
    await asyncio.wait_for(finish, timeout=0.5)

    assert pipeline.worker is not None
    assert pipeline.worker.done() is True
    assert active_provider_calls == 0


@pytest.mark.asyncio
async def test_live_scenario_finish_does_not_enqueue_sentinel_for_stopped_worker():
    repository = _LiveScenarioRepository()

    class Auxiliary:
        async def create_scenario(self, observation, decision, result, number):
            raise AssertionError("No enrichment should start in this test")

    pipeline = _live_scenario_pipeline(repository, Auxiliary())
    await pipeline.start()
    assert pipeline.worker is not None

    # Model a worker that has already terminated unexpectedly while the
    # producer filled its bounded backlog. finish() must inspect the worker
    # before trying to enqueue a sentinel into a queue nobody can consume.
    pipeline.worker.cancel()
    await asyncio.gather(pipeline.worker, return_exceptions=True)
    while not pipeline.queue.full():
        pipeline.queue.put_nowait(None)

    await asyncio.wait_for(pipeline.finish(), timeout=0.2)

    assert any(
        event_type == "scenario_postprocessing_completed"
        for event_type, _payload in repository.events
    )


@pytest.mark.asyncio
async def test_live_scenario_cancel_drains_auxiliary_usage_before_finish_returns():
    class UsageRepository(_LiveScenarioRepository):
        def __init__(self):
            super().__init__()
            self.usage = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost": None,
                "cost_known": False,
            }

        def get_exploration(self, exploration_id, include_details=False):
            return {"usage": dict(self.usage)}

        def update_exploration(self, exploration_id, **values):
            for source, target in (
                ("prompt_tokens", "prompt_tokens"),
                ("completion_tokens", "completion_tokens"),
                ("total_tokens", "total_tokens"),
                ("cost", "cost"),
                ("cost_known", "cost_known"),
            ):
                if source in values:
                    self.usage[target] = values[source]

    class UsageClient:
        def __init__(self):
            self.drains = 0

        def drain_usage(self):
            self.drains += 1
            return {
                "prompt_tokens": 31,
                "completion_tokens": 11,
                "total_tokens": 42,
                "cost": 0.0042,
                "cost_known": True,
            }

    enrichment_started = asyncio.Event()

    class Auxiliary:
        async def create_scenario(self, observation, decision, result, number):
            enrichment_started.set()
            await asyncio.Event().wait()

    from autojev.services import exploration as exploration_service

    repository = UsageRepository()
    manager = object.__new__(ExplorationManager)
    manager.repository = repository
    manager.usage_locks = {}
    auxiliary_client = UsageClient()
    pipeline = exploration_service._LiveScenarioPipeline(
        manager=manager,
        exploration_id="run-cancel-usage",
        auxiliary=Auxiliary(),
        auxiliary_client=auxiliary_client,
        use_local_evidence=False,
    )
    await pipeline.start()
    await pipeline.capture(
        "cart", *_live_scenario_evidence(), sequence=1
    )
    await asyncio.wait_for(enrichment_started.wait(), timeout=0.2)

    await asyncio.wait_for(pipeline.finish(cancelled=True), timeout=0.5)

    assert auxiliary_client.drains == 1
    assert repository.usage == {
        "prompt_tokens": 31,
        "completion_tokens": 11,
        "total_tokens": 42,
        "cost": 0.0042,
        "cost_known": True,
    }


@pytest.mark.asyncio
async def test_cancelling_postprocessing_stops_in_flight_auxiliary_enrichment():
    class RecordingRepository:
        def __init__(self):
            self.cancel_requested = False
            self.scenarios = []
            self.events = []

        def add_scenario(self, exploration_id, sequence, scenario):
            self.scenarios.append((sequence, scenario.title))

        def add_screenshot(self, *args):
            raise AssertionError("This fixture has no screenshots")

        def add_event(self, exploration_id, level, event_type, message, payload=None):
            self.events.append((event_type, payload or {}))

        def is_cancel_requested(self, exploration_id):
            return self.cancel_requested

    enrichment_started = asyncio.Event()
    enrichment_cancelled = asyncio.Event()

    class Auxiliary:
        async def create_scenario(self, observation, decision, result, number):
            enrichment_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                enrichment_cancelled.set()

    repository = RecordingRepository()
    manager = object.__new__(ExplorationManager)
    manager.repository = repository

    async def unexpected_usage(*args, **kwargs):
        raise AssertionError("Usage recording must not delay a cancelled call")

    manager._record_model_usage = unexpected_usage
    observation = Observation(
        url="https://example.test/dashboard",
        title="Dashboard",
        text="Dashboard ready",
        interactive_elements=[],
    )
    decision = AgentDecision(action="click", target="e1", rationale="Open")
    result = ActionResult(
        action="click",
        success=True,
        started_at=utc_now(),
        finished_at=utc_now(),
        before_url="https://example.test/",
        after_url=observation.url,
        message="Opened",
    )

    materialization = asyncio.create_task(
        manager._materialize_scenarios(
            "run-cancel-postprocessing",
            [(observation, decision, result, 1)],
            Auxiliary(),
            object(),
            use_local_evidence=False,
        )
    )
    await asyncio.wait_for(enrichment_started.wait(), timeout=0.5)
    repository.cancel_requested = True
    await asyncio.wait_for(materialization, timeout=0.5)

    assert enrichment_cancelled.is_set() is True
    assert len(repository.scenarios) == 1
    assert repository.events[-1] == (
        "scenario_postprocessing_completed",
        {
            "current": 1,
            "total": 1,
            "enriched": 0,
            "local_evidence": 1,
            "reason": "exploration_cancelled",
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
@pytest.mark.parametrize("cancel_at", ["report", "terminal_update"])
async def test_postprocessing_cancel_wins_over_completion(
    monkeypatch, tmp_path, cancel_at
):
    class Repository:
        def __init__(self):
            self.cancel_requested = False
            self.status = "queued"
            self.events = []
            self.updates = []

        def get_exploration(self, exploration_id, include_details=True):
            return {
                "id": exploration_id,
                "app_url": "https://example.test/",
                "status": self.status,
                "cancel_requested": self.cancel_requested,
                "agent": {"provider_id": "provider", "model_id": "jev"},
                "auxiliary_llm": {
                    "provider_id": "provider",
                    "model_id": "auxiliary",
                },
                "options": {
                    "max_steps": None,
                    "max_duration_seconds": None,
                    "generate_test_cases": False,
                    "generate_html_report": True,
                },
                "events": [
                    {"type": event_type, "message": message, "payload": payload}
                    for _level, event_type, message, payload in self.events
                ],
                "scenarios": [],
                "issues": [],
                "summary": {},
                "usage": {},
            }

        def update_exploration(self, exploration_id, **values):
            status = values.get("status")
            if status is not None:
                # Simulate the cancel write landing after the manager's last
                # pre-update check, while the persisted phase was still live.
                if (
                    cancel_at == "terminal_update"
                    and str(status) == "completed"
                    and str(self.status) == "postprocessing"
                ):
                    self.cancel_requested = True
                self.status = status
            self.updates.append(values)

        def add_event(self, exploration_id, level, event_type, message, payload=None):
            self.events.append((level, event_type, message, payload or {}))

        def is_cancel_requested(self, exploration_id):
            return self.cancel_requested

    class Vault:
        def get(self, exploration_id):
            return {}

        def discard(self, exploration_id):
            return None

    class Executor:
        def __init__(self, *args, **kwargs):
            pass

        async def start(self, url):
            return Observation(
                url=url,
                title="Empty application",
                text="No controls",
                interactive_elements=[],
            )

        async def show_live_panel(self):
            return None

        async def update_live_panel(self, *args):
            return None

        def is_active(self):
            return True

        async def close(self):
            return None

    class Renderer:
        def __init__(self, repository):
            self.repository = repository
            self.statuses = []

        def generate(self, exploration, output_path):
            self.statuses.append(str(exploration["status"]))
            if cancel_at == "report" and len(self.statuses) == 1:
                self.repository.cancel_requested = True
            return output_path

    repository = Repository()
    renderer = Renderer(repository)
    settings = SimpleNamespace(
        artifact_dir=Path(tmp_path),
        headless=True,
        browser="chromium",
    )
    manager = ExplorationManager(repository, Vault(), renderer, settings)
    manager._client_for = lambda provider_id: object()
    monkeypatch.setattr("autojev.services.exploration.PlaywrightExecutor", Executor)

    await manager._run("run-terminal-cancel-race")

    assert str(repository.status) == "cancelled"
    assert renderer.statuses == ["completed", "cancelled"]
    assert any(
        event_type == "cancelled" and payload["reason"] == "postprocessing_cancelled"
        for _level, event_type, _message, payload in repository.events
    )


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


@pytest.mark.asyncio
async def test_guarded_run_persists_shutdown_cancellation_from_finalization():
    class Repository:
        def __init__(self):
            self.events = []
            self.updates = []

        def add_event(self, exploration_id, level, event_type, message, payload=None):
            self.events.append((event_type, payload or {}))

        def update_exploration(self, exploration_id, **values):
            self.updates.append(values)

    class Vault:
        def __init__(self):
            self.discarded = []

        def discard(self, exploration_id):
            self.discarded.append(exploration_id)

    entered = asyncio.Event()
    manager = object.__new__(ExplorationManager)
    manager.repository = Repository()
    manager.credential_vault = Vault()
    manager.input_events = {"run-shutdown": asyncio.Event()}
    manager.semaphore = asyncio.Semaphore(1)

    async def interrupted_finalization(exploration_id):
        entered.set()
        await asyncio.Event().wait()

    manager._run = interrupted_finalization
    task = asyncio.create_task(manager._guarded_run("run-shutdown"))
    await entered.wait()
    task.cancel()
    await task

    assert manager.repository.events == [
        ("cancelled", {"reason": "backend_shutdown"})
    ]
    assert str(manager.repository.updates[-1]["status"]) == "cancelled"
    assert manager.credential_vault.discarded == ["run-shutdown"]


@pytest.mark.parametrize(
    "issue",
    [
        {"type": "http", "url": "https://events.backtrace.io/api/unique-events/submit", "status_code": 401, "message": "POST returned HTTP 401"},
        {"type": "network", "url": "https://app.test/api/poll", "message": "net::ERR_ABORTED"},
        {"type": "http", "url": "https://third-party.test/widgets", "status_code": 500, "message": "GET returned HTTP 500"},
        {"type": "console", "url": "https://app.test/orders", "message": "Failed to load resource: net::ERR_CONNECTION_RESET"},
    ],
)
def test_background_signals_do_not_reject_functional_success_or_fabricate_noop_pass(issue):
    observation = Observation(
        url="https://app.test/orders", title="Orders", text="Orders ready",
        interactive_elements=[],
        console_errors=[issue] if issue["type"] == "console" else [],
        network_errors=[issue] if issue["type"] != "console" else [],
        screenshot="after.png",
    )
    decision = AgentDecision(action="click", target="e1", rationale="Open orders")
    for state_changed in (False, True):
        result = ActionResult(
            action="click", success=True, started_at=utc_now(), finished_at=utc_now(),
            before_url=observation.url, after_url=observation.url,
            screenshot=observation.screenshot,
            evidence={"before_screenshot": "before.png"},
        )
        ExplorationManager._append_observable_state_evidence(
            result, decision, observation, state_changed=state_changed,
            capture_console_errors=True, capture_network_errors=True,
        )
        scenario = ExplorationManager._scenario_from_evidence(observation, decision, result, 1)
        assert scenario.status == (ScenarioStatus.PASS if state_changed else ScenarioStatus.SKIPPED)
        assert scenario.evidence["technical_signals"][0]["affects_verdict"] is False
        assert [entry["kind"] for entry in scenario.evidence["timeline"]].count("signal") == 1
        assert scenario.screenshots == ["before.png", "after.png"]
        if not state_changed:
            assert not result.assertions
            assert ExplorationManager._scenario_capture_policy(
                decision_source="local_policy", is_restoration_action=False, result=result,
            )[0] is False


def test_transport_error_console_and_network_channels_form_one_occurrence():
    observation = Observation(
        url="https://app.test/orders", title="Orders", text="Ready", interactive_elements=[],
        console_errors=[{
            "type": "console", "url": "https://app.test/orders",
            "message": "Failed to load resource: net::ERR_CONNECTION_RESET",
            "occurred_at": "2026-01-01T12:00:00.010+00:00",
        }],
        network_errors=[{
            "type": "network", "method": "POST", "url": "https://events.backtrace.io/api/events",
            "message": "net::ERR_CONNECTION_RESET", "occurred_at": "2026-01-01T12:00:00.011+00:00",
        }],
    )
    issues = ExplorationManager._deduplicate_observation_issues(observation)
    assert len(issues) == 1
    assert issues[0]["evidence_channels"] == ["network", "console"]
    assert issues[0]["url"].startswith("https://events.backtrace.io/")


def test_transport_errors_without_an_unambiguous_request_are_not_merged():
    from autojev.services.technical_signals import correlate_issues

    issues = correlate_issues([
        {"type": "console", "message": "Failed to load resource: net::ERR_CONNECTION_RESET", "occurred_at": "2026-01-01T12:00:00+00:00"},
        {"type": "network", "url": "https://app.test/a", "message": "net::ERR_CONNECTION_RESET", "occurred_at": "2026-01-01T12:00:00.01+00:00"},
        {"type": "network", "url": "https://app.test/b", "message": "net::ERR_CONNECTION_RESET", "occurred_at": "2026-01-01T12:00:00.02+00:00"},
    ])
    assert len(issues) == 3


@pytest.mark.parametrize("same_request", [False, True])
def test_http_channel_correlation_respects_capture_boundaries(same_request):
    from autojev.services.technical_signals import correlate_issues

    console = {
        "type": "console", "screenshot": "first.png", "url": "https://app.test/orders",
        "message": "Failed to load resource: the server responded with a status of 500",
        "occurred_at": "2026-01-01T12:00:00+00:00",
    }
    network = {
        "type": "http", "status_code": 500, "screenshot": "second.png",
        "url": "https://app.test/api/orders", "message": "GET returned HTTP 500",
        "occurred_at": "2026-01-01T12:00:00.01+00:00",
    }
    if same_request:
        console["request_id"] = network["request_id"] = "request-12"
    assert len(correlate_issues([console, network])) == (1 if same_request else 2)


def test_recorded_signal_keeps_step_and_temporal_association_without_claiming_causality():
    class Repository:
        def __init__(self):
            self.issues = []

        def add_issue(self, _exploration_id, issue):
            self.issues.append(issue)

        def add_event(self, *_args):
            pass

    manager = object.__new__(ExplorationManager)
    manager.repository = Repository()
    observation = Observation(
        url="https://app.test/orders", title="Orders", text="Ready", interactive_elements=[],
        network_errors=[{
            "type": "http", "url": "https://app.test/api/orders", "status_code": 500,
            "message": "GET returned HTTP 500", "occurred_at": "2026-01-01T12:00:00+00:00",
        }],
    )
    known = set()
    manager._record_observation_issues(
        "run", observation, known, action_step=12,
        action_started_at="2026-01-01T12:00:01+00:00",
        action_finished_at="2026-01-01T12:00:02+00:00", scenario_id="CT003",
    )
    manager._record_observation_issues("run", observation, known)
    assert len(manager.repository.issues) == 1
    issue = manager.repository.issues[0]
    assert issue["step_number"] == 12
    assert issue["scenario_id"] == "CT003"
    assert issue["association"]["kind"] == "before_action"
    assert issue["association"]["causal_link_confirmed"] is False
    assert issue["affects_verdict"] is False
    assert issue["potential_technical_failure"] is True
    assert "fora da execução" in issue["impact_message"]


def test_retry_retains_all_attempts_and_screenshots_in_one_ct():
    observation = Observation(
        url="https://app.test/orders", title="Orders", text="Ready", interactive_elements=[], screenshot="first-after.png",
    )
    decision = AgentDecision(action="click", target="e1", rationale="Open order")
    failed = ActionResult(
        action="click", success=False,
        started_at="2026-01-01T12:00:00+00:00", finished_at="2026-01-01T12:00:01+00:00",
        before_url=observation.url, after_url=observation.url,
        screenshot=observation.screenshot, message="Locator timeout",
        evidence={"before_screenshot": "first-before.png", "action_step": 1},
    )
    pending = {}
    number, _, _ = ExplorationManager._reconcile_pending_scenario(
        pending, set(), "same-feature", observation, decision, failed, 0,
        decision_source="local_policy", is_restoration_action=False,
    )
    observation.screenshot = "retry-after.png"
    passed = ActionResult(
        action="click", success=True,
        started_at="2026-01-01T12:00:03+00:00", finished_at="2026-01-01T12:00:04+00:00",
        before_url=observation.url, after_url="https://app.test/orders/1",
        screenshot=observation.screenshot,
        evidence={"before_screenshot": "retry-before.png", "action_step": 2},
    )
    ExplorationManager._append_observable_state_evidence(
        passed, decision, observation, state_changed=True,
        capture_console_errors=False, capture_network_errors=False,
    )
    number, _, _ = ExplorationManager._reconcile_pending_scenario(
        pending, set(), "same-feature", observation, decision, passed, number,
        decision_source="local_policy", is_restoration_action=False,
    )
    scenario = ExplorationManager._scenario_from_evidence(*pending["same-feature"])
    assert number == 1
    assert scenario.id == "CT001"
    assert scenario.status == ScenarioStatus.PASS
    assert scenario.screenshots == ["first-before.png", "first-after.png", "retry-before.png", "retry-after.png"]
    timeline = scenario.evidence["timeline"]
    assert len([entry for entry in timeline if entry["kind"] == "action"]) == 2
    assert any(entry["message"] == "Locator timeout" for entry in timeline)
    assert len({entry["id"] for entry in timeline}) == len(timeline)


def test_real_runtime_failure_still_fails_the_application_technical_check():
    from autojev.services.technical_signals import classify_signal

    observation = Observation(
        url="https://app.test/orders", title="Orders", text="Ready", interactive_elements=[],
        console_errors=[{"type": "pageerror", "url": "https://app.test/orders", "message": "TypeError: Cannot read properties of null"}],
    )
    decision = AgentDecision(action="click", target="e1", rationale="Open order")
    result = ActionResult(
        action="click", success=True, started_at=utc_now(), finished_at=utc_now(),
        before_url=observation.url, after_url=observation.url,
    )
    ExplorationManager._append_observable_state_evidence(
        result, decision, observation, state_changed=True,
        capture_console_errors=True, capture_network_errors=True,
    )
    scenario = ExplorationManager._scenario_from_evidence(observation, decision, result, 1)
    assert scenario.status == ScenarioStatus.FAIL
    assert scenario.evidence["technical_signals"][0]["classification"] == "application_runtime"
    assert scenario.evidence["technical_signals"][0]["affects_verdict"] is True
    own_telemetry_service = classify_signal(
        {"type": "http", "url": "https://events.backtrace.io/api/orders", "status_code": 500},
        "https://events.backtrace.io/",
    )
    assert own_telemetry_service["classification"] == "application_http"


def test_explicit_failed_functional_assertion_is_not_hidden_by_telemetry_triage():
    from autojev.domain.models import AssertionResult

    observation = Observation(
        url="https://app.test/orders", title="Orders", text="Ready", interactive_elements=[],
        network_errors=[{"type": "http", "url": "https://events.backtrace.io/api/events", "status_code": 401}],
    )
    decision = AgentDecision(action="click", target="e1", rationale="Open order")
    result = ActionResult(
        action="click", success=True, started_at=utc_now(), finished_at=utc_now(),
        before_url=observation.url, after_url=observation.url,
        assertions=[AssertionResult(kind="text_contains", expected="Order saved", actual="Missing", passed=False, message="Not observed")],
    )
    ExplorationManager._append_observable_state_evidence(
        result, decision, observation, state_changed=True,
        capture_console_errors=True, capture_network_errors=True,
    )
    assert ExplorationManager._scenario_from_evidence(observation, decision, result, 1).status == ScenarioStatus.FAIL
    assert result.assertions[0].passed is False
    assert result.assertions[-1].passed is True


def test_only_new_action_relevant_signatures_reach_prompts_but_recurrences_remain_ct_evidence():
    from autojev.services.technical_signals import classify_signal

    class Repository:
        def __init__(self):
            self.issues = []

        def add_issue(self, _run, issue):
            self.issues.append(issue)

        def add_event(self, *_args):
            pass

    manager = object.__new__(ExplorationManager)
    manager.repository = Repository()
    known = set()
    for step in (1, 2):
        started = f"2026-01-01T12:00:0{step}+00:00"
        finished = f"2026-01-01T12:00:0{step}.900+00:00"
        observation = Observation(
            url="https://app.test/orders", title="Orders", text="Ready", interactive_elements=[],
            network_errors=[
                {"type": "http", "url": "https://app.test/api/orders", "status_code": 500, "message": "GET returned HTTP 500", "occurred_at": f"2026-01-01T12:00:0{step}.100+00:00"},
                {"type": "http", "url": "https://events.backtrace.io/api/events", "status_code": 401, "message": "POST returned HTTP 401", "occurred_at": f"2026-01-01T12:00:0{step}.200+00:00"},
                {"type": "http", "url": "https://app.test/api/background", "status_code": 500, "message": "GET returned HTTP 500", "occurred_at": "2026-01-01T12:00:00+00:00"},
            ],
        )
        all_occurrences = manager._deduplicate_observation_issues(observation)
        new_issues = manager._record_observation_issues(
            "run", observation, known, action_step=step,
            action_started_at=started, action_finished_at=finished,
        )
        decision = AgentDecision(action="click", target="e1", rationale="Load order")
        result = ActionResult(
            action="click", success=True, started_at=started, finished_at=finished,
            before_url=observation.url, after_url=observation.url,
            evidence={"technical_signals": [classify_signal(issue, observation.url) for issue in all_occurrences]},
        )
        manager._append_observable_state_evidence(
            result, decision, observation, state_changed=True,
            capture_console_errors=True, capture_network_errors=True,
            new_issues=manager._issues_relevant_to_action(observation, started, finished),
        )
        manager._keep_only_new_observation_issues(observation, new_issues, started, finished)
        scenario = manager._scenario_from_evidence(observation, decision, result, step)
        # The repeated same-origin error still fails the technical guard and
        # all local signals survive, despite deduplication of model context.
        assert scenario.status == ScenarioStatus.FAIL
        assert len(scenario.evidence["technical_signals"]) == 3
        assert len([entry for entry in scenario.evidence["timeline"] if entry["kind"] == "signal"]) == 3
        assert not observation.console_errors
        assert [issue["url"] for issue in observation.network_errors] == (
            ["https://app.test/api/orders"] if step == 1 else []
        )
    # Two temporally distinct HTTP errors and telemetry occurrences, plus one
    # unchanged background record: no occurrence is lost or persisted twice.
    assert len(manager.repository.issues) == 5
