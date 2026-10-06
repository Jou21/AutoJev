"""Undispatched stale refs are automation context, never invented CT bugs."""

import asyncio
import hashlib
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from autojev.adapters.report import StandaloneHtmlReport
from autojev.domain.models import ActionResult, AgentDecision, AssertionResult, Observation, ScenarioStatus, utc_now
from autojev.services.action_dispatch import is_stale_undispatched_action
from autojev.services.business_checks import check_business_outcomes
from autojev.services.coverage import CoveragePlanner
from autojev.services.exploration import ExplorationManager, _LiveScenarioPipeline
from autojev.services.scenario_timeline import prepare_scenario_timelines


URL = "https://owner.fixture.test/catalog"


def observation(*, expanded=False):
    elements = [{"ref": "e0", "tag": "button", "name": "Add to cart", "enabled": True,
                 "semantic_identity": 'owner:["article","data-product-id","sku-a"]'}]
    if expanded:
        elements.append({"ref": "e1", "tag": "button", "name": "Help", "enabled": True,
                         "semantic_identity": 'control:["button","id","help"]'})
    return Observation(URL, "Catalog", "Desk Lamp", elements)


def decision():
    return AgentDecision(action="click", target="e0", rationale="Add original product")


def result(*, stale=True, success=False, assertions=None):
    return ActionResult(action="click", success=success, started_at=utc_now(), finished_at=utc_now(),
                        before_url=URL, after_url=URL,
                        message="RuntimeError: reference is stale after the real click",
                        evidence={"action_dispatch": {"status": "not_executed", "reason": "stale_observed_control", "retryable": True}} if stale else {},
                        assertions=assertions or [])


@pytest.mark.parametrize("source,restoration", [("jev", False), ("local_policy", False), ("coverage", True)])
def test_proven_undispatched_attempt_is_not_captured_as_failed_ct(source, restoration):
    attempt = result()
    assert is_stale_undispatched_action(attempt)
    assert check_business_outcomes(observation(), decision(), attempt, observation()) == []
    assert ExplorationManager._scenario_capture_policy(
        decision_source=source, is_restoration_action=restoration, result=attempt,
    ) == (False, "stale_control_not_dispatched")
    assert ExplorationManager._scenario_from_evidence(observation(), decision(), attempt, 1).status == ScenarioStatus.BLOCKED


@pytest.mark.parametrize("dispatch,success", [
    ({}, False), ({"status": "executed", "reason": "stale_observed_control"}, False),
    ({"status": "not_executed", "reason": "runtime_error"}, False),
    ({"status": "not_executed", "reason": "stale_observed_control"}, True),
])
def test_stale_message_or_inconsistent_marker_cannot_hide_real_failure(dispatch, success):
    attempt = result(success=success)
    attempt.evidence = {"action_dispatch": dispatch}
    assert not is_stale_undispatched_action(attempt)
    if not success:
        assert ExplorationManager._scenario_capture_policy(
            decision_source="jev", is_restoration_action=False, result=attempt) == (True, None)
        assert ExplorationManager._scenario_from_evidence(observation(), decision(), attempt, 1).status == ScenarioStatus.FAIL


@pytest.mark.parametrize("kind", ["cart_control_state", "cart_item_removed", "no_new_browser_errors", "form_validation_probe_precondition", "visual_model"])
def test_native_failed_assertion_is_never_suppressed_by_dispatch_marker(kind):
    assertion = AssertionResult(kind=kind, expected="Required condition", actual="Not met", passed=False, message="Native check failed")
    attempt = result(assertions=[assertion])
    assert not is_stale_undispatched_action(attempt)
    assert ExplorationManager._scenario_capture_policy(
        decision_source="jev", is_restoration_action=False, result=attempt) == (True, None)
    assert ExplorationManager._scenario_from_evidence(observation(), decision(), attempt, 1).status == ScenarioStatus.FAIL


def test_model_declared_visibility_check_cannot_invent_dispatch_of_stale_action():
    attempt = result(assertions=[AssertionResult("element_visible", "e0", False, False, "Not visible")])
    assert is_stale_undispatched_action(attempt)
    assert ExplorationManager._scenario_capture_policy(
        decision_source="jev", is_restoration_action=False, result=attempt) == (False, "stale_control_not_dispatched")


def test_two_refusals_preserve_active_retry_restriction_without_ct_or_pass():
    obs, action = observation(), decision()
    planner = CoveragePlanner(URL)
    planner.observe(obs)
    pending, omitted, number = {}, set(), 0
    for _ in range(2):
        attempt = result()
        planner.record_action(obs, action, attempt, obs)
        number, reason, _ = ExplorationManager._reconcile_pending_scenario(
            pending, omitted, "original-move", obs, action, attempt, number,
            decision_source="jev", is_restoration_action=False)
        assert reason == "stale_control_not_dispatched"
    assert pending == {} and number == 0
    context = planner.context(obs)
    assert context["coverage_complete"] is False
    assert len(context["restrictions"]) == 1
    assert context["restrictions"][0]["reason"] == "action_retry_exhausted"
    assert context["restrictions"][0]["status"] == "active"
    assert planner.coverage_complete() is False


def test_recovered_changed_state_has_no_orphan_false_fail():
    old, fresh, action = observation(), observation(expanded=True), decision()
    planner = CoveragePlanner(URL)
    def fingerprint(obs):
        return hashlib.sha256(f"{planner.state_key(obs)}:{planner.decision_key(obs, action)}".encode()).hexdigest()
    assert fingerprint(old) != fingerprint(fresh)
    pending, omitted = {}, set()
    number, _, _ = ExplorationManager._reconcile_pending_scenario(
        pending, omitted, fingerprint(old), old, action, result(), 0,
        decision_source="jev", is_restoration_action=False)
    recovered = result(stale=False, success=True, assertions=[
        AssertionResult("cart_control_state", "Remove", "Remove", True, "Product toggled"),
    ])
    number, _, _ = ExplorationManager._reconcile_pending_scenario(
        pending, omitted, fingerprint(fresh), fresh, action, recovered, number,
        decision_source="local_policy", is_restoration_action=False)
    assert number == 1 and len(pending) == 1
    assert ExplorationManager._scenario_from_evidence(*next(iter(pending.values()))).status == ScenarioStatus.PASS


@pytest.mark.parametrize("mode", ["text", "visual"])
async def test_undispatched_attempt_never_enters_auxiliary_queue(mode):
    class Repository:
        scenarios = []
        def add_scenario(self, *_args):
            self.scenarios.append(_args[-1])
        def add_event(self, *_args):
            pass
        def is_cancel_requested(self, *_args):
            return False
    async def no_usage(*_args):
        pass
    async def forbidden_provider(*_args):
        pytest.fail("An undispatched stale ref must not consume auxiliary tokens")
    repository = Repository()
    manager = SimpleNamespace(repository=repository, settings=SimpleNamespace(auxiliary_tail_timeout_seconds=1),
                              _scenario_from_evidence=ExplorationManager._scenario_from_evidence,
                              _record_model_usage=no_usage)
    auxiliary = SimpleNamespace(model="local-fixture", evidence_mode=mode, create_scenario=forbidden_provider)
    pipeline = _LiveScenarioPipeline(manager, "fixture", auxiliary, object(), use_local_evidence=False)
    await pipeline.start()
    attempt = result()
    if mode == "visual":
        attempt.evidence["visual_assessment"] = {"requested": True, "status": "pending"}
    await pipeline.capture("original-move", observation(), decision(), attempt, sequence=1)
    await asyncio.wait_for(pipeline.finish(), timeout=2)
    assert pipeline.total == 0 and pipeline.drafts == {} and pipeline.queue.empty()
    assert repository.scenarios == []


def test_undispatched_action_is_automation_context_in_report_without_fake_ct(tmp_path):
    attempt = result()
    attempt.screenshot = "after-stale.png"
    run = {"id": "fixture", "status": "completed", "app_url": URL, "scenarios": [], "issues": [],
           "actions": [{"step_number": 1, "decision": asdict(decision()), "result": asdict(attempt)}],
           "summary": {"total_scenarios": 0, "pass": 0, "fail": 0}}
    before = deepcopy(run)
    prepared = prepare_scenario_timelines(run)
    assert run == before
    assert prepared["scenarios"] == [] and len(prepared["scenario_contexts"]) == 1
    context = prepared["scenario_contexts"][0]
    assert context["status"] == "INFO"
    assert context["evidence"]["action_dispatch"]["status"] == "not_executed"
    assert context["screenshots"] == ["after-stale.png"]
    assert any("Nenhum comando foi executado" in entry.get("message", "") for entry in context["evidence"]["timeline"])
    path = tmp_path / "stale-context.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(path))
    html = path.read_text()
    assert "Contexto de automação" in html and "Nenhum comando foi executado" in html
    assert 'data-status="FAIL"' not in html and 'data-status="PASS"' not in html


def test_legacy_ct_and_signals_are_not_reclassified_from_a_message():
    attempt = result(stale=False)
    run = {"app_url": URL, "scenarios": [{"id": "CT001", "status": "FAIL", "evidence": {"action_succeeded": False}}],
           "actions": [{"step_number": 1, "decision": asdict(decision()), "result": asdict(attempt)}], "issues": []}
    prepared = prepare_scenario_timelines(run)
    assert prepared["scenarios"][0]["status"] == "FAIL"
    assert prepared["scenarios"][0]["evidence"]["verdict"]["kind"] == "execution_failure"
    assert prepared["scenario_contexts"] == []
