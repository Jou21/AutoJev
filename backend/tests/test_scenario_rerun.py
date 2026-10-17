import asyncio
import base64
import copy
import hashlib
import json
import re
import time
from dataclasses import asdict
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from autojev.adapters.playwright_executor import LoginAttempt
from autojev.adapters.repository import SQLiteRepository
from autojev.adapters.secrets import EphemeralCredentialVault, SecretCipher
from autojev.config import Settings
from autojev.domain.models import (
    ActionResult, AgentDecision, AssertionResult, ExplorationOptions,
    ExplorationRequest, ModelSelection, Observation, ScenarioStatus,
)
from autojev.services.business_checks import check_business_outcomes
from autojev.services.exploration import ExplorationManager
from autojev.services.rerun_recipe import build_rerun_recipe
from autojev.services.scenario_rerun import ScenarioRerunService, STATE_EVENT, rerun_block_reason


URL = "https://example.test/inventory.html"


def observed(ref="e1", *, selected="az", with_products=False):
    element = {"ref": ref, "tag": "select", "role": "combobox", "name": "Sort products",
               "enabled": True, "has_value": False, "selected_values": [selected],
               "selected_index": 0 if selected == "az" else 1,
               "options": [{"value": "az", "label": "Name (A to Z)"},
                           {"value": "za", "label": "Name (Z to A)"}]}
    observation = Observation(URL, "Products", "Sort products", [element], screenshot=f"{selected}.png",
                              page_state={"fields": [{"ref": ref, "id": "sort", "identity": "sort",
                                                       "name": "Sort products", "type": "select-one",
                                                       "has_value": False, "selected_values": [selected]}]})
    if with_products:
        products = [{"id": "backpack", "name": "Backpack", "price": "$29.99"},
                    {"id": "light", "name": "Bike Light", "price": "$9.99"}]
        observation.page_state["products"] = list(reversed(products)) if selected == "za" else products
    return observation


def action_result(before, after, decision):
    result = ActionResult(decision.action, True, "2026-10-14T12:00:00+00:00", "2026-10-14T12:00:01+00:00",
                          before.url, after.url, target=decision.target, screenshot=after.screenshot)
    for assertion in decision.assertions:
        result.assertions.append(AssertionResult(assertion["kind"], assertion["expected"],
                                                assertion["expected"], True, "Observed"))
    return result


def make_service(tmp_path, *, with_products=False):
    settings = Settings(data_dir=tmp_path, database_path=tmp_path / "db.sqlite3",
                        artifact_dir=tmp_path / "artifacts", secret_key="test",
                        cors_origins=(), headless=True, browser="chromium", http_timeout_seconds=1)
    repository = SQLiteRepository(settings.database_path, SecretCipher(tmp_path, "test"))
    repository.initialize()
    source_id = repository.create_exploration(ExplorationRequest(URL,
        ModelSelection("unused", "unused-agent"), ModelSelection("unused", "unused-aux"), ExplorationOptions()))
    repository.update_exploration(source_id, status="completed", finished_at="2026-10-14T12:00:02+00:00")
    before, after = observed(with_products=with_products), observed(selected="za", with_products=with_products)
    decision = AgentDecision("select", "Change sorting", target="e1", value="za")
    result = action_result(before, after, decision)
    result.assertions.extend(check_business_outcomes(before, decision, result, after))
    ExplorationManager._append_observable_state_evidence(result, decision, after, state_changed=True,
        capture_console_errors=True, capture_network_errors=True)
    result.evidence.update(action_step=1, before_screenshot=before.screenshot,
                           before_observed_at=before.captured_at)
    scenario = ExplorationManager._scenario_from_evidence(after, decision, result, 1)
    recipe = build_rerun_recipe(before, decision, URL)
    assert recipe["enabled"] is True
    result.evidence["rerun_recipe"] = recipe
    repository.add_action(source_id, 1, asdict(decision), result.to_dict())
    repository.add_scenario(source_id, 1, scenario)
    reports = []

    def render(item, path):
        reports.append(copy.deepcopy(item))
        return path

    def no_models(*args, **kwargs):
        raise AssertionError("A single-CT rerun must never create a model client")

    manager = SimpleNamespace(repository=repository, credential_vault=EphemeralCredentialVault(),
                              report_renderer=SimpleNamespace(generate=render), semaphore=asyncio.Semaphore(1),
                              _client_for=no_models)
    service = ScenarioRerunService(manager, settings)
    return service, repository, source_id, scenario, reports


def install_browser(monkeypatch, *, before=None, after=None, closed=False,
                    execute_error=None, execute_block=None, close_block=None, action_success=True):
    instances = []

    class Executor:
        def __init__(self, artifact_dir, options, **kwargs):
            self.artifact_dir, self.options, self.kwargs = artifact_dir, options, kwargs
            self.actions, self.started, self.closed = [], [], False
            self.manual_captures = []
            self.inactive = asyncio.Event()
            if closed:
                self.inactive.set()
            self.prepared = asyncio.Event()
            instances.append(self)

        async def start(self, url):
            self.started.append(url)
            return copy.deepcopy(before or observed("e42"))

        async def observe(self, label):
            return copy.deepcopy(before or observed("e42"))

        async def attempt_filled_login(self, observation=None):
            reason = ("authentication_challenge" if observation and
                      observation.page_state.get("authentication_challenge") else "incomplete_credentials")
            return LoginAttempt("manual_required", reason, observation)

        async def show_intervention_panel(self, message, **kwargs):
            self.preparation_message = message
            self.intervention_options = kwargs

        async def hide_intervention_panel(self):
            pass

        async def wait_for_user_input(self):
            await self.prepared.wait()
            self.prepared.clear()

        async def wait_until_inactive(self):
            await self.inactive.wait()

        async def wait_for_capture_confirmation(self, message):
            self.capture_confirmation_message = message
            # The tested site's overlay is informational only. Its DOM/bindings
            # cannot grant capture authority; the application endpoint owns it.
            await asyncio.Event().wait()

        async def capture_print(self, label):
            raise AssertionError("User-provided evidence must not capture the tested page")

        async def execute(self, decision, credentials):
            assert credentials == {}, "No credentials or draft steps are executed automatically"
            self.actions.append(copy.deepcopy(decision))
            if execute_error:
                raise execute_error
            if execute_block is not None:
                await execute_block.wait()
            final = copy.deepcopy(after or observed("e42", selected="za"))
            result = action_result(before or observed("e42"), final, decision)
            result.success = action_success
            if not action_success:
                result.message = "Browser execution failed"
            return result, final

        async def close(self):
            if close_block is not None:
                await close_block.wait()
            self.closed = True

    monkeypatch.setattr("autojev.services.scenario_rerun.PlaywrightExecutor", Executor)
    return instances


async def wait_phase(service, new_id, phase):
    for _ in range(100):
        if service.states[new_id].get("phase") == phase:
            return
        await asyncio.sleep(0.001)
    raise AssertionError(f"Expected phase {phase}; got {service.states[new_id]}")


async def wait_preparation(service, new_id, version):
    for _ in range(200):
        state = service.states[new_id]
        if state.get("phase") == "waiting_preparation" and state.get("preparation", {}).get("version") == version:
            return
        await asyncio.sleep(0.001)
    raise AssertionError(f"Expected preparation {version}; got {service.states[new_id]}")


def replace_scenario(repository, source_id, scenario):
    repository.add_scenario(source_id, 1, scenario)


def test_preflight_is_read_only_and_does_not_expose_private_recipe(tmp_path):
    service, repository, source_id, _, _ = make_service(tmp_path)
    original = repository.get_exploration(source_id)
    preflight = service.preflight(source_id, "CT001")
    assert preflight["available"] is True
    assert preflight["checks"] == ["selection_matches", "no_new_browser_errors"]
    assert preflight["scope"]["missing_visual_checks"] is False
    assert preflight["scope"]["visual_review_omitted"] is False
    assert preflight["scope"]["visual_review"] is False
    assert "digest" not in json.dumps(preflight)
    assert "fingerprint" not in json.dumps(preflight)
    assert repository.get_exploration(source_id) == original
    assert len(repository.list_explorations()) == 1


@pytest.mark.asyncio
async def test_one_real_action_one_new_ct_no_model_calls_source_immutable(tmp_path, monkeypatch):
    service, repository, source_id, _, reports = make_service(tmp_path)
    original = repository.get_exploration(source_id)
    browsers = install_browser(monkeypatch)
    new_id = service.submit(source_id, "CT001")
    await wait_phase(service, new_id, "waiting_preparation")
    assert browsers[0].actions == []
    assert repository.get_exploration(new_id)["status"] == "waiting_input"
    service.resume(new_id)
    task = service.tasks[new_id]
    await task
    new = repository.get_exploration(new_id)
    assert new_id != source_id and new["status"] == "completed"
    assert len(new["actions"]) == len(new["scenarios"]) == 1
    assert new["scenarios"][0]["status"] == "PASS"
    assert new["scenarios"][0]["evidence"]["source_scenario_id"] == "CT001"
    assert new["scenarios"][0]["evidence"]["missing_original_checks"] == []
    assert new["actions"][0]["decision"]["target"] == "e42"
    assert "rerun_recipe" not in new["scenarios"][0]["evidence"]
    assert new["actions"][0]["result"]["evidence"]["rerun_recipe"]["enabled"] is True
    assert browsers[0].started == [URL] and len(browsers[0].actions) == 1 and browsers[0].closed
    assert browsers[0].kwargs["headless"] is False
    assert new["usage"]["total_tokens"] == 0
    assert new["options"]["auxiliary_max_requests"] == 0 and len(reports) == 1
    assert repository.get_exploration(source_id) == original
    assert new["report_available"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["title", "fields", "origin", "cart", "target", "authentication"])
async def test_changed_preconditions_block_before_dispatch(tmp_path, monkeypatch, change):
    service, repository, source_id, _, _ = make_service(tmp_path)
    service.intervention_timeout_seconds = 0.04
    before = observed("e42")
    if change == "title":
        before.title = "Other screen"
    elif change == "fields":
        before.page_state["fields"][0]["selected_values"] = ["za"]
    elif change == "origin":
        before.url = "https://other.test/inventory.html"
    elif change == "target":
        before.interactive_elements = []
    elif change == "authentication":
        before.page_state["authentication_challenge"] = True
    else:
        before.page_state["cart"] = {"count": 9}
    browsers = install_browser(monkeypatch, before=before)
    new_id = service.submit(source_id, "CT001")
    await wait_phase(service, new_id, "waiting_preparation")
    service.resume(new_id)
    await wait_preparation(service, new_id, 2)
    assert browsers[0].actions == [] and repository.get_exploration(new_id)["scenarios"] == []
    await service.tasks[new_id]
    item = repository.get_exploration(new_id)
    assert item["status"] == "partial" and item["steps_completed"] == 0
    assert item["scenarios"][0]["status"] == "BLOCKED" and item["actions"] == []
    assert item["scenarios"][0]["evidence"]["action_dispatched"] is False
    evidence = item["scenarios"][0]["evidence"]
    assert evidence["rerun_blocked_reason"] not in item["scenarios"][0]["actual_result"]
    assert evidence["rerun_blocked_explanation"] in item["scenarios"][0]["actual_result"]
    assert evidence["rerun_blocked_reason"] == "preparation_timeout"
    assert browsers[0].actions == [] and browsers[0].closed


@pytest.mark.asyncio
async def test_manual_browser_confirmation_is_rearmed_after_mismatch(tmp_path, monkeypatch):
    service, repository, source_id, _, _ = make_service(tmp_path)
    page = observed("e42")
    page.url = "https://example.test/"
    browsers = install_browser(monkeypatch, before=page)
    new_id = service.submit(source_id, "CT001")
    task = service.tasks[new_id]
    await wait_preparation(service, new_id, 1)
    browsers[0].prepared.set()
    await wait_preparation(service, new_id, 2)
    await asyncio.sleep(0.01)
    assert service.state_for(new_id)["preparation"]["version"] == 2
    assert not browsers[0].actions and not task.done()
    page.url = URL
    browsers[0].prepared.set()
    await task
    assert repository.get_exploration(new_id)["scenarios"][0]["status"] == "PASS"
    assert len(browsers[0].actions) == 1


@pytest.mark.asyncio
async def test_premature_confirmation_can_be_corrected_then_executes_once(tmp_path, monkeypatch):
    service, repository, source_id, _, _ = make_service(tmp_path)
    original = repository.get_exploration(source_id)
    page = observed("e42")
    page.url = "https://example.test/"
    browsers = install_browser(monkeypatch, before=page)
    new_id = service.submit(source_id, "CT001")
    task = service.tasks[new_id]
    await wait_preparation(service, new_id, 1)
    initial = service.state_for(new_id)["preparation"]
    assert initial["expected_url"] == URL and initial["current_url"] == page.url
    assert initial["attempts"] == 0 and URL in initial["instruction"]
    service.resume(new_id)
    await wait_preparation(service, new_id, 2)
    retry = service.state_for(new_id)["preparation"]
    assert retry["reason_code"] == "before_url_changed" and retry["attempts"] == 1
    assert "página aberta não é a mesma" in retry["reason_label"]
    waiting = repository.get_exploration(new_id)
    assert waiting["status"] == "waiting_input" and waiting["actions"] == waiting["scenarios"] == []
    assert not browsers[0].actions and not browsers[0].closed
    event = next(event for event in reversed(waiting["events"]) if event["type"] == "waiting_input")
    assert event["payload"]["preparation"] == retry
    page.url = URL
    await asyncio.sleep(0.01)
    # Finishing a login/navigation cannot trigger the original action by itself.
    assert browsers[0].actions == [] and not task.done()
    service.resume(new_id)
    await task
    item = repository.get_exploration(new_id)
    assert item["status"] == "completed" and item["scenarios"][0]["status"] == "PASS"
    assert len(item["actions"]) == len(browsers[0].actions) == 1
    assert service.state_for(new_id)["preparation"]["attempts"] == 2
    assert repository.get_exploration(source_id) == original


@pytest.mark.asyncio
async def test_repeated_mismatch_uses_one_absolute_preparation_deadline(tmp_path, monkeypatch):
    service, repository, source_id, _, _ = make_service(tmp_path)
    # Leave room for SQLite/event-loop work when Chromium tests run in
    # parallel, while still distinguishing one shared deadline from a new
    # full timeout after every mismatch (which would take at least 1.85 s).
    service.intervention_timeout_seconds = 1.2
    page = observed("e42")
    page.url = "https://example.test/"
    browsers = install_browser(monkeypatch, before=page)
    new_id = service.submit(source_id, "CT001")
    task = service.tasks[new_id]
    await wait_preparation(service, new_id, 1)
    started = time.monotonic()
    await asyncio.sleep(0.65)
    service.resume(new_id)
    await wait_preparation(service, new_id, 2)
    service.resume(new_id)
    await wait_preparation(service, new_id, 3)
    await task
    assert time.monotonic() - started < 1.5
    item = repository.get_exploration(new_id)
    assert item["status"] == "partial" and item["error"] == "preparation_timeout"
    assert item["actions"] == [] and browsers[0].actions == [] and browsers[0].closed
    assert item["scenarios"][0]["status"] == "BLOCKED"
    assert "prazo para preparar" in item["scenarios"][0]["actual_result"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["cancel", "closed"])
async def test_retry_preparation_remains_cancellable(tmp_path, monkeypatch, stop):
    service, repository, source_id, _, _ = make_service(tmp_path)
    page = observed("e42")
    page.url = "https://example.test/"
    browsers = install_browser(monkeypatch, before=page)
    new_id = service.submit(source_id, "CT001")
    task = service.tasks[new_id]
    await wait_preparation(service, new_id, 1)
    service.resume(new_id)
    await wait_preparation(service, new_id, 2)
    if stop == "cancel":
        service.cancel(new_id)
    else:
        browsers[0].inactive.set()
    await asyncio.gather(task, return_exceptions=True)
    item = repository.get_exploration(new_id)
    assert item["status"] == "cancelled" and item["actions"] == [] and item["scenarios"] == []
    assert browsers[0].closed and browsers[0].actions == []


def test_old_ct_without_recipe_is_explicitly_unsupported(tmp_path):
    service, repository, source_id, _, _ = make_service(tmp_path)
    # The only authoritative recipe lives in the private action record.
    with repository._connect() as connection:
        connection.execute("UPDATE actions SET result_json = json_remove(result_json, '$.evidence.rerun_recipe') WHERE exploration_id = ?", (source_id,))
    assert service.preflight(source_id, "CT001")["code"] == "missing_recipe"
    with pytest.raises(ValueError, match="antigo"):
        service.submit(source_id, "CT001")
    assert len(repository.list_explorations()) == 1


def test_malformed_private_recipe_is_rejected_before_a_browser_opens(tmp_path):
    service, repository, source_id, _, _ = make_service(tmp_path)
    with repository._connect() as connection:
        connection.execute("UPDATE actions SET result_json = json_set(result_json, '$.evidence.rerun_recipe.before_state_digest', 'invalid') WHERE exploration_id = ?", (source_id,))
    assert service.preflight(source_id, "CT001")["code"] == "invalid_recipe"
    with pytest.raises(ValueError, match="receita salva"):
        service.submit(source_id, "CT001")
    assert len(repository.list_explorations()) == 1


@pytest.mark.asyncio
async def test_shared_browser_slot_is_held_until_browser_cleanup_finishes(tmp_path, monkeypatch):
    service, repository, source_id, _, _ = make_service(tmp_path)
    close_block = asyncio.Event()
    browsers = install_browser(monkeypatch, close_block=close_block)
    first_id = service.submit(source_id, "CT001")
    await wait_phase(service, first_id, "waiting_preparation")
    service.resume(first_id)
    for _ in range(100):
        if repository.get_exploration(first_id)["steps_completed"] == 1:
            break
        await asyncio.sleep(0.001)
    second_id = service.submit(source_id, "CT001")
    await asyncio.sleep(0.01)
    assert len(browsers) == 1 and service.states[second_id]["phase"] == "queued"
    close_block.set()
    await service.tasks[first_id]
    await wait_phase(service, second_id, "waiting_preparation")
    assert len(browsers) == 2
    service.cancel(second_id)
    await asyncio.gather(service.tasks[second_id], return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["expected", "kind", "after_state"])
async def test_original_checks_cannot_disappear_or_change_expectation(tmp_path, monkeypatch, missing):
    service, repository, source_id, scenario, _ = make_service(tmp_path)
    after = observed("e42", selected="za")
    if missing == "expected":
        scenario.evidence["assertions"][0]["expected"] = ["different-value"]
    elif missing == "kind":
        scenario.evidence["assertions"].append({"kind": "product_sort_order", "expected": {"products": [1]}, "passed": True})
    else:
        after.page_state["fields"] = []
    replace_scenario(repository, source_id, scenario)
    install_browser(monkeypatch, after=after)
    new_id = service.submit(source_id, "CT001")
    await wait_phase(service, new_id, "waiting_preparation")
    service.resume(new_id)
    await service.tasks[new_id]
    item = repository.get_exploration(new_id)
    assert len(item["actions"]) == 1
    assert item["status"] == "partial" and item["scenarios"][0]["status"] == "BLOCKED"
    assert item["scenarios"][0]["evidence"]["missing_original_checks"]


@pytest.mark.asyncio
async def test_a_failed_reproduced_check_is_a_real_fail_not_blocked(tmp_path, monkeypatch):
    service, repository, source_id, _, _ = make_service(tmp_path)
    install_browser(monkeypatch, after=observed("e42", selected="az"))
    new_id = service.submit(source_id, "CT001")
    await wait_phase(service, new_id, "waiting_preparation")
    service.resume(new_id)
    await service.tasks[new_id]
    item = repository.get_exploration(new_id)
    assert item["status"] == "completed" and item["scenarios"][0]["status"] == "FAIL"
    assert item["scenarios"][0]["evidence"]["missing_original_checks"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("assessment_status", ["assessed", "pending", "unavailable"])
async def test_optional_visual_review_cannot_invent_a_missing_original_check(
    tmp_path, monkeypatch, assessment_status,
):
    service, repository, source_id, scenario, reports = make_service(tmp_path, with_products=True)
    original_assessment = {
        "requested": True, "status": assessment_status,
        "model": "original-review-model", "summary": "Original review, not evidence of the replay",
    }
    scenario.evidence["visual_assessment"] = original_assessment
    replace_scenario(repository, source_id, scenario)
    original = repository.get_exploration(source_id)
    original_checks = original["scenarios"][0]["evidence"]["assertions"]
    assert [item["kind"] for item in original_checks] == [
        "selection_matches", "product_sort_order", "no_new_browser_errors",
    ]
    preflight = service.preflight(source_id, "CT001")
    assert preflight["available"] is True
    assert preflight["scope"]["missing_visual_checks"] is False
    assert preflight["scope"]["visual_review_omitted"] is True
    assert preflight["scope"]["visual_review"] is False
    assert "bloqueado" not in preflight["reason"]
    browsers = install_browser(
        monkeypatch, before=observed("e42", with_products=True),
        after=observed("e42", selected="za", with_products=True),
    )
    new_id = service.submit(source_id, "CT001")
    await wait_phase(service, new_id, "waiting_preparation")
    assert browsers[0].actions == []
    service.resume(new_id)
    await service.tasks[new_id]
    replay = repository.get_exploration(new_id)
    result = replay["scenarios"][0]
    evidence = result["evidence"]
    assert replay["status"] == "completed" and result["status"] == "PASS"
    assert not replay.get("error")
    assert evidence["missing_original_checks"] == []
    assert [(item["kind"], item["expected"]) for item in evidence["assertions"]] == [
        (item["kind"], item["expected"]) for item in original_checks
    ]
    assert all(item["passed"] is True for item in evidence["assertions"])
    assert evidence["visual_assessment"] == {
        "requested": False, "status": "disabled", "reason": "single_ct_rerun_local_only",
    }
    assert evidence["rerun_scope"] == preflight["scope"]
    assert len(browsers[0].actions) == len(replay["actions"]) == len(replay["scenarios"]) == 1
    assert browsers[0].closed and len(reports) == 1
    assert replay["usage"]["total_tokens"] == 0 and replay["options"]["auxiliary_max_requests"] == 0
    assert repository.get_exploration(source_id) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", ["business_check", "execution"])
async def test_optional_visual_review_does_not_mask_a_failed_local_replay(tmp_path, monkeypatch, failed):
    service, repository, source_id, scenario, _ = make_service(tmp_path, with_products=True)
    scenario.evidence["visual_assessment"] = {"requested": True, "status": "assessed"}
    replace_scenario(repository, source_id, scenario)
    install_browser(
        monkeypatch, before=observed("e42", with_products=True),
        after=observed("e42", selected="az", with_products=True),
        action_success=failed != "execution",
    )
    new_id = service.submit(source_id, "CT001")
    await wait_phase(service, new_id, "waiting_preparation")
    service.resume(new_id)
    await service.tasks[new_id]
    replay = repository.get_exploration(new_id)
    result = replay["scenarios"][0]
    assert result["status"] == "FAIL"
    missing = result["evidence"]["missing_original_checks"]
    assert "visual_model" not in missing
    if failed == "execution":
        # A failed dispatch prevents local checks from running. Those are
        # genuinely missing local checks, not a phantom auxiliary review.
        assert replay["status"] == "partial" and replay["error"] == "missing_original_checks"
        assert missing == ["selection_matches", "product_sort_order", "no_new_browser_errors"]
    else:
        assert replay["status"] == "completed" and not replay.get("error")
        assert missing == []
    assert result["evidence"]["visual_assessment"]["status"] == "disabled"
    assert (any(item["passed"] is False for item in result["evidence"]["assertions"])
            or result["evidence"]["action_succeeded"] is False)


@pytest.mark.asyncio
@pytest.mark.parametrize("review_requested", [False, True])
async def test_visual_checks_never_reuse_the_original_visual_approval(tmp_path, monkeypatch, review_requested):
    service, repository, source_id, scenario, _ = make_service(tmp_path)
    scenario.evidence["assertions"].append({"kind": "visual_model", "expected": "No overlap", "passed": True})
    scenario.evidence["visual_assessment"] = {"requested": review_requested, "status": "assessed"}
    replace_scenario(repository, source_id, scenario)
    assert service.preflight(source_id, "CT001")["scope"]["missing_visual_checks"] is True
    assert service.preflight(source_id, "CT001")["scope"]["visual_review_omitted"] is True
    install_browser(monkeypatch)
    new_id = service.submit(source_id, "CT001")
    await wait_phase(service, new_id, "waiting_preparation")
    service.resume(new_id)
    await service.tasks[new_id]
    replay = repository.get_exploration(new_id)
    result = replay["scenarios"][0]
    assert replay["status"] == "partial" and replay["error"] == "missing_original_checks"
    assert result["status"] == "BLOCKED"
    assert result["evidence"]["missing_original_checks"] == ["visual_model"]
    assert result["evidence"]["visual_assessment"]["status"] == "disabled"
    assert all(a["kind"] != "visual_model" for a in result["evidence"]["assertions"])


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", ["business_check", "execution"])
async def test_missing_visual_checks_do_not_erase_a_real_local_failure(tmp_path, monkeypatch, failed):
    service, repository, source_id, scenario, _ = make_service(tmp_path)
    scenario.evidence["assertions"].append({"kind": "visual_model", "expected": "No overlap", "passed": True})
    scenario.evidence["visual_assessment"] = {"requested": True, "status": "assessed"}
    replace_scenario(repository, source_id, scenario)
    install_browser(monkeypatch, after=observed("e42", selected="az"), action_success=failed != "execution")
    new_id = service.submit(source_id, "CT001")
    await wait_phase(service, new_id, "waiting_preparation")
    service.resume(new_id)
    await service.tasks[new_id]
    item = repository.get_exploration(new_id)
    result = item["scenarios"][0]
    assert result["status"] == "FAIL" and item["status"] == "partial"
    assert "visual_model" in result["evidence"]["missing_original_checks"]
    assert any(a["passed"] is False for a in result["evidence"]["assertions"]) or result["evidence"]["action_succeeded"] is False


@pytest.mark.parametrize("reason, expected", [
    ("before_effect_changed", "conteúdo ou controles diferentes"),
    ("before_state_changed", "pré-requisitos da página estão diferentes"),
    ("before_url_changed", "página aberta não é a mesma"),
    ("missing_target", "não foi encontrado"),
    ("ambiguous_target", "mais de um controle"),
    ("preparation_timeout", "prazo para preparar"),
    ("authentication_required", "Conclua o login"),
    ("unportable_field_state", "valores privados"),
    ("invalid_precondition_digest", "receita original está incompleta"),
    ("TimeoutError: deadline", "tempo limite"),
])
def test_blocked_reasons_have_human_readable_explanations(reason, expected):
    assert expected in rerun_block_reason(reason)
    assert reason not in rerun_block_reason(reason)


def test_visual_only_missing_checks_running_and_multi_action_are_unsupported(tmp_path):
    service, repository, source_id, scenario, _ = make_service(tmp_path)
    original_assertions = copy.deepcopy(scenario.evidence["assertions"])
    scenario.evidence["assertions"] = [{"kind": "visual_model", "expected": "No overlap"}]
    replace_scenario(repository, source_id, scenario)
    assert service.preflight(source_id, "CT001")["code"] == "visual_only"
    scenario.evidence["assertions"] = []
    replace_scenario(repository, source_id, scenario)
    assert service.preflight(source_id, "CT001")["code"] == "missing_checks"
    scenario.evidence["assertions"] = original_assertions
    scenario.evidence["timeline"].append({"kind": "action", "action_step": 2})
    replace_scenario(repository, source_id, scenario)
    assert service.preflight(source_id, "CT001")["code"] == "multiple_actions"
    repository.update_exploration(source_id, status="running")
    assert service.preflight(source_id, "CT001")["code"] == "source_running"


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["timeout", "closed", "cancel", "shutdown", "cancel_queued"])
async def test_wait_is_bounded_cancellable_and_browser_cleanup_is_reliable(tmp_path, monkeypatch, stop):
    service, repository, source_id, _, _ = make_service(tmp_path)
    browsers = install_browser(monkeypatch, closed=stop == "closed")
    service.intervention_timeout_seconds = 0.02
    new_id = service.submit(source_id, "CT001")
    task = service.tasks[new_id]
    if stop == "cancel_queued":
        service.cancel(new_id)
        await asyncio.gather(task, return_exceptions=True)
    else:
        await wait_phase(service, new_id, "waiting_preparation")
        if stop == "cancel":
            service.cancel(new_id)
        elif stop == "shutdown":
            await service.shutdown()
        await asyncio.gather(task, return_exceptions=True)
    item = repository.get_exploration(new_id)
    assert item["status"] == ("partial" if stop == "timeout" else "cancelled")
    assert item["actions"] == [] and item["finished_at"]
    assert not service.state_for(new_id)["running"]
    assert all(browser.closed and not browser.actions for browser in browsers)
    assert service.manager.credential_vault.get(new_id) == {}
    if stop == "timeout":
        assert item["scenarios"][0]["status"] == "BLOCKED"


@pytest.mark.asyncio
async def test_manual_browser_continue_also_respects_the_preparation_gate(tmp_path, monkeypatch):
    service, repository, source_id, _, _ = make_service(tmp_path)
    browsers = install_browser(monkeypatch)
    new_id = service.submit(source_id, "CT001")
    await wait_phase(service, new_id, "waiting_preparation")
    browsers[0].prepared.set()
    await service.tasks[new_id]
    assert repository.get_exploration(new_id)["scenarios"][0]["status"] == "PASS"


@pytest.mark.asyncio
async def test_interrupted_dispatch_does_not_claim_the_action_was_not_executed(tmp_path, monkeypatch):
    service, repository, source_id, _, _ = make_service(tmp_path)
    block = asyncio.Event()
    browsers = install_browser(monkeypatch, execute_block=block)
    new_id = service.submit(source_id, "CT001")
    await wait_phase(service, new_id, "waiting_preparation")
    service.resume(new_id)
    await wait_phase(service, new_id, "executing")
    await asyncio.sleep(0)
    service.cancel(new_id)
    await asyncio.gather(service.tasks[new_id], return_exceptions=True)
    item = repository.get_exploration(new_id)
    assert item["status"] == "cancelled" and len(browsers[0].actions) == 1
    assert item["scenarios"][0]["evidence"]["action_dispatched"] is True
    assert "resultado não pôde ser confirmado" in item["scenarios"][0]["actual_result"]


@pytest.mark.asyncio
async def test_credential_values_are_memory_only_even_on_executor_error(tmp_path, monkeypatch):
    service, repository, source_id, _, _ = make_service(tmp_path)
    secret = "my-super-secret-password"
    browsers = install_browser(monkeypatch, execute_error=RuntimeError(f"Failed with {secret}"))
    new_id = service.submit(source_id, "CT001", {"password": secret})
    await wait_phase(service, new_id, "waiting_preparation")
    service.resume(new_id)
    await service.tasks[new_id]
    assert browsers[0].kwargs["secret_values"] == [secret]
    assert secret not in json.dumps(repository.get_exploration(new_id))
    assert service.manager.credential_vault.get(new_id) == {}


def test_restart_reconciles_durable_rerun_without_redispatch(tmp_path):
    service, repository, source_id, _, _ = make_service(tmp_path)
    repository.update_exploration(source_id, status="waiting_input")
    repository.add_event(source_id, "info", STATE_EVENT, "running", {
        "running": True, "phase": "waiting_preparation", "exploration_id": source_id,
        "source_exploration_id": "original", "source_scenario_id": "CT007"})
    restarted = ScenarioRerunService(service.manager, service.settings)
    assert restarted.tasks == {}
    assert restarted.state_for(source_id)["reason"] == "backend_restart_interrupted"
    assert repository.get_exploration(source_id)["status"] == "cancelled"


def capture_plan(*checkpoints):
    return {"version": 1, "checkpoints": [
        {"id": identity, "gap": gap, "order": order}
        for identity, gap, order in checkpoints
    ]}


async def wait_capture(service, run_id, identity):
    for _ in range(300):
        state = service.state_for(run_id)
        if state.get("phase") == "waiting_capture" and state.get("capture_checkpoint", {}).get("id") == identity:
            return copy.deepcopy(state["capture_checkpoint"])
        if state.get("phase") == "finished":
            raise AssertionError(f"Capture finished prematurely: {state}")
        await asyncio.sleep(0.001)
    raise AssertionError(f"Expected capture {identity}; got {service.states[run_id]}")


@pytest.mark.asyncio
@pytest.mark.parametrize("gap, expected_actions, capture_phase", [
    (0, 0, "before_action"), (3, 1, "after_action"),
])
async def test_print_checkpoint_waits_for_explicit_capture_and_never_redispatches(
    tmp_path, monkeypatch, user_print_image, gap, expected_actions, capture_phase,
):
    service, repository, source_id, _, reports = make_service(tmp_path)
    original = repository.get_exploration(source_id)
    browsers = install_browser(monkeypatch)
    plan = capture_plan(("db-1", gap, 0))
    new_id = service.submit(source_id, "CT001", capture_plan=plan)
    task = service.tasks[new_id]
    await wait_phase(service, new_id, "waiting_preparation")
    service.resume(new_id)
    checkpoint = await wait_capture(service, new_id, "db-1")
    assert len(browsers[0].actions) == expected_actions
    assert not browsers[0].manual_captures and not task.done()
    assert repository.get_exploration(new_id)["status"] == "waiting_input"
    assert service.state_for(new_id)["capture_count"] == 1
    assert service.state_for(new_id)["captures_completed"] == 0
    # Continue is preparation authority only, never capture authority. A fresh
    # GET or unrelated event must not release this separate checkpoint gate.
    service.resume(new_id)
    await asyncio.sleep(0.02)
    assert not task.done() and not browsers[0].manual_captures
    assert len(browsers[0].actions) == expected_actions
    accepted = service.capture_print(new_id, checkpoint["id"], checkpoint["version"], user_print_image["data_url"])
    assert accepted["phase"] == "capturing_print"
    with pytest.raises(ValueError):
        service.capture_print(new_id, checkpoint["id"], checkpoint["version"], user_print_image["data_url"])
    await task
    replay = repository.get_exploration(new_id)
    assert replay["status"] == "completed" and replay["scenarios"][0]["status"] == "PASS"
    assert len(replay["actions"]) == len(browsers[0].actions) == 1
    evidence = replay["scenarios"][0]["evidence"]
    manual = evidence["manual_captures"]
    assert len(manual) == 1 and not browsers[0].manual_captures
    assert manual[0]["checkpoint_id"] == "db-1" and manual[0]["planned_gap"] == gap
    assert manual[0]["capture_phase"] == capture_phase
    assert manual[0]["kind"] == "observation" and "passed" not in manual[0]
    assert manual[0]["title"] == "Imagem anexada pelo usuário"
    assert manual[0]["source"] == "user_upload" and "url" not in manual[0]
    assert manual[0]["attachment"]["sha256"] == user_print_image["sha256"]
    assert manual[0]["attachment"]["width"] == user_print_image["width"]
    assert manual[0]["attachment"]["height"] == user_print_image["height"]
    attachment_path = Path(manual[0]["screenshot"])
    assert re.fullmatch(r"user-upload-[a-f0-9]{32}\.png", str(attachment_path))
    if not attachment_path.is_absolute():
        attachment_path = service.settings.artifact_dir / new_id / "screenshots" / attachment_path
    assert attachment_path.read_bytes() == user_print_image["bytes"]
    assert hashlib.sha256(attachment_path.read_bytes()).hexdigest() == user_print_image["sha256"]
    assert manual[0]["occurred_at"] == manual[0]["attached_at"]
    # Neither persisted state nor logs may contain the image's base64 body.
    assert user_print_image["data_url"] not in json.dumps(replay)
    assert any(entry["screenshot"] == manual[0]["screenshot"] for entry in evidence["timeline"]
               if entry.get("screenshot"))
    assert any(shot["path"] == manual[0]["screenshot"] and shot["scenario_id"] == "CT001"
               for shot in replay["screenshots"])
    assert all(item["passed"] is True for item in evidence["assertions"])
    assert replay["usage"]["total_tokens"] == 0 and replay["options"]["auxiliary_max_requests"] == 0
    assert service.state_for(new_id)["captures_completed"] == 1
    assert browsers[0].closed and len(reports) == 1
    assert repository.get_exploration(source_id) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("first_gap", [0, 3])
async def test_multiple_print_checkpoints_have_distinct_nonces_and_app_confirmation_is_explicit(
    tmp_path, monkeypatch, user_print_image, first_gap,
):
    service, repository, source_id, _, _ = make_service(tmp_path)
    browsers = install_browser(monkeypatch)
    # Input order is deliberately reversed; source gap/order is authoritative.
    plan = capture_plan(("db-2", 3, 1), ("db-1", first_gap, 0))
    new_id = service.submit(source_id, "CT001", capture_plan=plan)
    task = service.tasks[new_id]
    await wait_phase(service, new_id, "waiting_preparation")
    service.resume(new_id)
    first = await wait_capture(service, new_id, "db-1")
    assert len(browsers[0].actions) == (0 if first_gap == 0 else 1)
    service.capture_print(new_id, first["id"], first["version"], user_print_image["data_url"])
    second = await wait_capture(service, new_id, "db-2")
    assert second["version"] > first["version"]
    for identity, version in [(first["id"], first["version"]),
                              (second["id"], first["version"]), ("db-99", second["version"])]:
        with pytest.raises(ValueError):
            service.capture_print(new_id, identity, version, user_print_image["data_url"])
    await asyncio.sleep(0.02)
    assert not task.done() and not browsers[0].manual_captures
    assert service.state_for(new_id)["captures_completed"] == 1
    assert len(browsers[0].actions) == 1
    service.capture_print(new_id, second["id"], second["version"], user_print_image["data_url"])
    await task
    replay = repository.get_exploration(new_id)
    assert replay["status"] == "completed"
    assert [item["checkpoint_id"] for item in replay["scenarios"][0]["evidence"]["manual_captures"]] == ["db-1", "db-2"]
    assert len({item["screenshot"] for item in replay["scenarios"][0]["evidence"]["manual_captures"]}) == 2
    assert len(replay["actions"]) == len(browsers[0].actions) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["timeout", "cancel", "closed", "shutdown"])
@pytest.mark.parametrize("gap", [0, 3])
async def test_unfinished_capture_never_fabricates_print_and_preserves_dispatched_checks(
    tmp_path, monkeypatch, stop, gap,
):
    service, repository, source_id, _, _ = make_service(tmp_path)
    original = repository.get_exploration(source_id)
    service.capture_timeout_seconds = 0.03 if stop == "timeout" else 2
    browsers = install_browser(monkeypatch)
    new_id = service.submit(source_id, "CT001", capture_plan=capture_plan(("db-1", gap, 0)))
    task = service.tasks[new_id]
    await wait_phase(service, new_id, "waiting_preparation")
    service.resume(new_id)
    await wait_capture(service, new_id, "db-1")
    if stop == "cancel":
        service.cancel(new_id)
    elif stop == "closed":
        browsers[0].inactive.set()
    elif stop == "shutdown":
        await service.shutdown()
    await asyncio.gather(task, return_exceptions=True)
    replay = repository.get_exploration(new_id)
    assert replay["status"] == ("partial" if stop == "timeout" else "cancelled")
    assert len(replay["actions"]) == len(browsers[0].actions) == (1 if gap == 3 else 0)
    assert browsers[0].closed and not browsers[0].manual_captures
    assert service.state_for(new_id)["captures_completed"] == 0
    if gap == 3:
        scenario = replay["scenarios"][0]
        assert scenario["status"] == "PASS", "A pending print must not erase the already observed local checks"
        assert len(scenario["evidence"]["assertions"]) == 2
        assert all(item["passed"] is True for item in scenario["evidence"]["assertions"])
        assert not scenario["evidence"].get("manual_captures")
    assert repository.get_exploration(source_id) == original


@pytest.mark.parametrize("plan", [
    {"version": True, "checkpoints": []},
    {"version": 2, "checkpoints": []},
    {"version": 1, "checkpoints": "db-1"},
    {"version": 1, "checkpoints": [], "sql": "SELECT private"},
    capture_plan(("db-1", -1, 0)), capture_plan(("db-1", 10000, 0)),
    capture_plan(("db-1", True, 0)), capture_plan(("other-id", 1, 0)),
    capture_plan(("db-1", 1, 0), ("db-1", 2, 0)),
    {"version": 1, "checkpoints": [{"id": "db-1", "gap": 1, "order": 0, "sql": "SELECT private"}]},
    capture_plan(*[(f"db-{index + 1}", 1, index) for index in range(101)]),
])
def test_capture_plan_is_bounded_data_not_custom_execution(tmp_path, plan):
    service, repository, source_id, _, _ = make_service(tmp_path)
    original = repository.get_exploration(source_id)
    with pytest.raises(ValueError):
        service.submit(source_id, "CT001", capture_plan=plan)
    assert len(repository.list_explorations()) == 1 and not service.tasks
    assert repository.get_exploration(source_id) == original


@pytest.mark.asyncio
async def test_before_action_print_does_not_weaken_original_precondition_guards(tmp_path, monkeypatch, user_print_image):
    service, repository, source_id, _, _ = make_service(tmp_path)
    page = observed("e42")
    browsers = install_browser(monkeypatch, before=page)
    new_id = service.submit(source_id, "CT001", capture_plan=capture_plan(("db-1", 0, 0)))
    task = service.tasks[new_id]
    await wait_phase(service, new_id, "waiting_preparation")
    service.resume(new_id)
    checkpoint = await wait_capture(service, new_id, "db-1")
    page.url = "https://other.test/inventory.html"
    service.capture_print(new_id, checkpoint["id"], checkpoint["version"], user_print_image["data_url"])
    await task
    replay = repository.get_exploration(new_id)
    assert replay["status"] == "partial" and not replay["actions"] and not browsers[0].actions
    assert replay["scenarios"][0]["status"] == "BLOCKED"
    assert browsers[0].closed


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["empty", "wrong_type", "svg", "html", "malformed", "mismatched_mime", "oversized", "too_many_pixels", "too_wide", "corrupt", "animated"])
async def test_invalid_attachment_keeps_checkpoint_waiting_without_files_or_redispatch(
    tmp_path, monkeypatch, user_print_image, invalid,
):
    service, repository, source_id, _, _ = make_service(tmp_path)
    browsers = install_browser(monkeypatch)
    new_id = service.submit(source_id, "CT001", capture_plan=capture_plan(("db-1", 3, 0)))
    task = service.tasks[new_id]
    await wait_phase(service, new_id, "waiting_preparation")
    service.resume(new_id)
    checkpoint = await wait_capture(service, new_id, "db-1")

    def data_url(mime, content):
        return f"data:{mime};base64," + base64.b64encode(content).decode("ascii")

    invalid_data = {
        "empty": "",
        "wrong_type": None,
        "svg": data_url("image/svg+xml", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'),
        "html": data_url("text/html", b"<script>alert(1)</script>"),
        "malformed": "data:image/png;base64,not!base64",
        "mismatched_mime": user_print_image["data_url"].replace("image/png", "image/jpeg"),
    }.get(invalid)
    if invalid == "oversized":
        invalid_data = data_url("image/png", b"x" * (10 * 1024 * 1024 + 1))
    elif invalid == "too_many_pixels":
        buffer = BytesIO()
        Image.new("RGB", (6000, 5001)).save(buffer, format="PNG")
        invalid_data = data_url("image/png", buffer.getvalue())
    elif invalid == "too_wide":
        buffer = BytesIO()
        Image.new("RGB", (16385, 1)).save(buffer, format="PNG")
        invalid_data = data_url("image/png", buffer.getvalue())
    elif invalid == "corrupt":
        invalid_data = data_url("image/png", user_print_image["bytes"][:-40])
    elif invalid == "animated":
        buffer = BytesIO()
        Image.new("RGB", (2, 2), "red").save(
            buffer, format="PNG", save_all=True, append_images=[Image.new("RGB", (2, 2), "blue")],
        )
        invalid_data = data_url("image/png", buffer.getvalue())
    run_files = sorted(str(path) for path in (service.settings.artifact_dir / new_id).rglob("*") if path.is_file())
    with pytest.raises(ValueError):
        service.capture_print(new_id, checkpoint["id"], checkpoint["version"], invalid_data)
    await asyncio.sleep(0.02)
    assert not task.done() and service.state_for(new_id)["phase"] == "waiting_capture"
    assert not service.capture_events[new_id].is_set()
    assert service.state_for(new_id)["captures_completed"] == 0
    assert sorted(str(path) for path in (service.settings.artifact_dir / new_id).rglob("*") if path.is_file()) == run_files
    assert not browsers[0].manual_captures
    assert len(browsers[0].actions) == 1
    # A rejected image does not consume the nonce: replace it, confirm once,
    # and finish without replaying the already executed select action.
    service.capture_print(new_id, checkpoint["id"], checkpoint["version"], user_print_image["data_url"])
    await task
    replay = repository.get_exploration(new_id)
    assert replay["status"] == "completed" and replay["scenarios"][0]["status"] == "PASS"
    assert len(replay["actions"]) == len(browsers[0].actions) == 1
    assert service.state_for(new_id)["captures_completed"] == 1
    assert len(replay["scenarios"][0]["evidence"]["manual_captures"]) == 1
    assert all(item["passed"] is True for item in replay["scenarios"][0]["evidence"]["assertions"])
    assert browsers[0].closed


@pytest.mark.asyncio
async def test_attachment_storage_failure_preserves_checks_without_fabricating_evidence(
    tmp_path, monkeypatch, user_print_image,
):
    service, repository, source_id, _, _ = make_service(tmp_path)
    original = repository.get_exploration(source_id)
    browsers = install_browser(monkeypatch)
    new_id = service.submit(source_id, "CT001", capture_plan=capture_plan(("db-1", 3, 0)))
    task = service.tasks[new_id]
    await wait_phase(service, new_id, "waiting_preparation")
    service.resume(new_id)
    checkpoint = await wait_capture(service, new_id, "db-1")

    def storage_failure(_run, _image):
        raise OSError("Fixture image cannot be written")

    store_image = service._store_uploaded_image
    monkeypatch.setattr(service, "_store_uploaded_image", storage_failure)
    with pytest.raises(OSError):
        service.capture_print(new_id, checkpoint["id"], checkpoint["version"], user_print_image["data_url"])
    await asyncio.sleep(0.02)
    assert not task.done() and service.state_for(new_id)["phase"] == "waiting_capture"
    assert not service.capture_events[new_id].is_set()
    assert service.state_for(new_id)["captures_completed"] == 0
    pending = repository.get_exploration(new_id)
    assert not pending["scenarios"][0]["evidence"].get("manual_captures")
    assert all(item["passed"] is True for item in pending["scenarios"][0]["evidence"]["assertions"])
    monkeypatch.setattr(service, "_store_uploaded_image", store_image)
    service.capture_print(new_id, checkpoint["id"], checkpoint["version"], user_print_image["data_url"])
    await task
    replay = repository.get_exploration(new_id)
    assert replay["status"] == "completed" and replay["scenarios"][0]["status"] == "PASS"
    assert len(replay["actions"]) == len(browsers[0].actions) == 1
    assert service.state_for(new_id)["captures_completed"] == 1
    assert len(replay["scenarios"][0]["evidence"]["manual_captures"]) == 1
    assert all(item["passed"] is True for item in replay["scenarios"][0]["evidence"]["assertions"])
    assert repository.get_exploration(source_id) == original and browsers[0].closed


@pytest.mark.asyncio
@pytest.mark.parametrize("format_name,mime,extension", [("JPEG", "image/jpeg", ".jpg"), ("WEBP", "image/webp", ".webp")])
async def test_supported_raster_attachments_preserve_bytes_and_metadata(
    tmp_path, monkeypatch, format_name, mime, extension,
):
    service, repository, source_id, _, _ = make_service(tmp_path)
    browsers = install_browser(monkeypatch)
    buffer = BytesIO()
    Image.new("RGB", (48, 24), "purple").save(buffer, format=format_name)
    content = buffer.getvalue()
    image_data = f"data:{mime};base64," + base64.b64encode(content).decode("ascii")
    new_id = service.submit(source_id, "CT001", capture_plan=capture_plan(("db-1", 3, 0)))
    task = service.tasks[new_id]
    await wait_phase(service, new_id, "waiting_preparation")
    service.resume(new_id)
    checkpoint = await wait_capture(service, new_id, "db-1")
    service.capture_print(new_id, checkpoint["id"], checkpoint["version"], image_data)
    await task
    replay = repository.get_exploration(new_id)
    assert replay["status"] == "completed"
    entry = replay["scenarios"][0]["evidence"]["manual_captures"][0]
    assert entry["attachment"]["mime_type"] == mime
    assert entry["attachment"]["size_bytes"] == len(content)
    assert entry["attachment"]["sha256"] == hashlib.sha256(content).hexdigest()
    assert (entry["attachment"]["width"], entry["attachment"]["height"]) == (48, 24)
    image_path = Path(entry["screenshot"])
    if not image_path.is_absolute():
        image_path = service.settings.artifact_dir / new_id / "screenshots" / image_path
    assert image_path.suffix == extension and image_path.read_bytes() == content
    assert len(browsers[0].actions) == 1 and not browsers[0].manual_captures


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["cancel", "closed"])
async def test_accepted_image_survives_interruption_racing_checkpoint_wakeup(
    tmp_path, monkeypatch, user_print_image, interruption,
):
    service, repository, source_id, _, _ = make_service(tmp_path)
    original = repository.get_exploration(source_id)
    browsers = install_browser(monkeypatch)
    new_id = service.submit(source_id, "CT001", capture_plan=capture_plan(("db-1", 3, 0)))
    task = service.tasks[new_id]
    await wait_phase(service, new_id, "waiting_preparation")
    service.resume(new_id)
    checkpoint = await wait_capture(service, new_id, "db-1")
    service.capture_print(new_id, checkpoint["id"], checkpoint["version"], user_print_image["data_url"])
    # Do not yield between accepting the image and interrupting the waiter.
    if interruption == "cancel":
        service.cancel(new_id)
    else:
        browsers[0].inactive.set()
    await asyncio.gather(task, return_exceptions=True)
    replay = repository.get_exploration(new_id)
    assert replay["status"] == "cancelled"
    assert replay["scenarios"][0]["status"] == "PASS"
    assert len(replay["actions"]) == len(browsers[0].actions) == 1
    images = replay["scenarios"][0]["evidence"]["manual_captures"]
    assert len(images) == 1 and images[0]["attachment"]["sha256"] == user_print_image["sha256"]
    image_path = service.settings.artifact_dir / new_id / "screenshots" / images[0]["screenshot"]
    assert image_path.read_bytes() == user_print_image["bytes"]
    assert len([event for event in replay["events"] if event["type"] == "manual_print_attached"]) == 1
    assert len([shot for shot in replay["screenshots"] if shot.get("image_source") == "user_upload"]) == 1
    assert repository.get_exploration(source_id) == original and browsers[0].closed


def install_filled_login_browser(monkeypatch, *, outcomes=None, after_login=None):
    """Fixture-only login submission; credential values never leave its DOM."""
    from autojev.services import scenario_rerun

    browsers = install_browser(monkeypatch)
    base_executor = scenario_rerun.PlaywrightExecutor

    class FilledLoginExecutor(base_executor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.page = Observation(
                "https://example.test/", "Login", "Sign in", [
                    {"ref": "login-user", "tag": "input", "name": "Username", "type": "text", "has_value": True},
                    {"ref": "login-password", "tag": "input", "name": "Password", "type": "password", "has_value": True},
                    {"ref": "login-submit", "tag": "button", "name": "Login", "type": "submit"},
                ],
            )
            self.login_outcomes = list(outcomes or [("success", "authenticated")])
            self.login_attempts = []
            self.flow = []

        async def start(self, url):
            self.started.append(url)
            return copy.deepcopy(self.page)

        async def observe(self, label):
            self.flow.append(("observe", label))
            return copy.deepcopy(self.page)

        async def is_login_page(self):
            return ExplorationManager._needs_login(self.page)

        async def attempt_filled_login(self, observation=None):
            assert isinstance(observation, Observation)
            assert ExplorationManager._needs_login(observation)
            self.login_attempts.append(copy.deepcopy(observation))
            self.flow.append(("login", observation.url))
            status, reason = self.login_outcomes.pop(0)
            if status == "success":
                self.page = copy.deepcopy(after_login or observed("e42"))
            return LoginAttempt(status, reason, copy.deepcopy(self.page))

        async def execute(self, decision, credentials):
            self.flow.append(("execute", decision.target))
            return await super().execute(decision, credentials)

    monkeypatch.setattr(scenario_rerun, "PlaywrightExecutor", FilledLoginExecutor)
    return browsers


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmation", ["browser", "application"])
async def test_explicit_continue_submits_filled_login_then_revalidates_before_one_action(
    tmp_path, monkeypatch, confirmation,
):
    service, repository, source_id, _, _ = make_service(tmp_path)
    source = repository.get_exploration(source_id)
    browsers = install_filled_login_browser(monkeypatch)
    new_id = service.submit(source_id, "CT001")
    task = service.tasks[new_id]
    await wait_preparation(service, new_id, 1)
    browser = browsers[0]
    await asyncio.sleep(0.01)
    assert not browser.login_attempts and not browser.actions and not task.done()
    if confirmation == "browser":
        browser.prepared.set()
    else:
        service.resume(new_id)
    await task
    replay = repository.get_exploration(new_id)
    assert len(browser.login_attempts) == 1
    assert replay["status"] == "completed" and replay["scenarios"][0]["status"] == "PASS"
    assert len(browser.actions) == len(replay["actions"]) == 1
    assert browser.actions[0].target == "e42"
    kinds = [kind for kind, _ in browser.flow]
    assert kinds.index("login") < kinds.index("execute")
    assert "observe" in kinds[kinds.index("login") + 1:kinds.index("execute")]
    attempt = service.state_for(new_id)["preparation"]["login_attempt"]
    assert attempt["status"] == "success" and attempt["reason"] == "authenticated"
    assert repository.get_exploration(source_id) == source


@pytest.mark.asyncio
@pytest.mark.parametrize("status,reason", [
    ("rejected", "login_rejected"),
    ("manual_required", "incomplete_credentials"),
    ("manual_required", "unsafe_origin"),
    ("manual_required", "authentication_challenge"),
])
async def test_unsuccessful_filled_login_stays_paused_until_another_explicit_continue(
    tmp_path, monkeypatch, status, reason,
):
    service, repository, source_id, _, _ = make_service(tmp_path)
    browsers = install_filled_login_browser(monkeypatch, outcomes=[
        (status, reason), ("success", "authenticated"),
    ])
    new_id = service.submit(source_id, "CT001")
    task = service.tasks[new_id]
    await wait_preparation(service, new_id, 1)
    service.resume(new_id)
    await wait_preparation(service, new_id, 2)
    retry = service.state_for(new_id)["preparation"]
    assert retry["login_attempt"]["status"] == status
    assert retry["login_attempt"]["reason"] == reason
    assert retry["login_attempt"]["reason_label"]
    waiting = repository.get_exploration(new_id)
    assert waiting["status"] == "waiting_input" and not waiting["actions"] and not waiting["scenarios"]
    await asyncio.sleep(0.01)
    assert len(browsers[0].login_attempts) == 1 and not browsers[0].actions and not task.done()
    service.resume(new_id)
    await task
    replay = repository.get_exploration(new_id)
    assert replay["status"] == "completed" and replay["scenarios"][0]["status"] == "PASS"
    assert len(browsers[0].login_attempts) == 2 and len(browsers[0].actions) == 1


@pytest.mark.asyncio
async def test_manual_login_before_continue_bypasses_filled_login_submission(tmp_path, monkeypatch):
    service, repository, source_id, _, _ = make_service(tmp_path)
    browsers = install_filled_login_browser(monkeypatch)
    new_id = service.submit(source_id, "CT001")
    task = service.tasks[new_id]
    await wait_preparation(service, new_id, 1)
    browsers[0].page = observed("e42")
    await asyncio.sleep(0.01)
    assert not browsers[0].actions and not task.done()
    service.resume(new_id)
    await task
    assert not browsers[0].login_attempts and len(browsers[0].actions) == 1
    assert repository.get_exploration(new_id)["status"] == "completed"


@pytest.mark.asyncio
async def test_successful_login_cannot_bypass_the_original_ct_preconditions(tmp_path, monkeypatch):
    service, repository, source_id, _, _ = make_service(tmp_path)
    wrong_preconditions = observed("e42")
    wrong_preconditions.page_state["cart"] = {"count": 9}
    browsers = install_filled_login_browser(monkeypatch, after_login=wrong_preconditions)
    new_id = service.submit(source_id, "CT001")
    task = service.tasks[new_id]
    await wait_preparation(service, new_id, 1)
    service.resume(new_id)
    await wait_preparation(service, new_id, 2)
    browser = browsers[0]
    assert len(browser.login_attempts) == 1 and not browser.actions and not task.done()
    waiting = repository.get_exploration(new_id)
    assert waiting["status"] == "waiting_input" and not waiting["actions"] and not waiting["scenarios"]
    browser.page = observed("e42")
    service.resume(new_id)
    await task
    assert len(browser.login_attempts) == len(browser.actions) == 1
    assert repository.get_exploration(new_id)["status"] == "completed"
