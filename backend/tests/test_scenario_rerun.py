import asyncio
import copy
import json
import time
from dataclasses import asdict
from types import SimpleNamespace

import pytest

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


def observed(ref="e1", *, selected="az"):
    element = {"ref": ref, "tag": "select", "role": "combobox", "name": "Sort products",
               "enabled": True, "has_value": False, "selected_values": [selected],
               "selected_index": 0 if selected == "az" else 1,
               "options": [{"value": "az", "label": "Name (A to Z)"},
                           {"value": "za", "label": "Name (Z to A)"}]}
    return Observation(URL, "Products", "Sort products", [element], screenshot=f"{selected}.png",
                       page_state={"fields": [{"ref": ref, "id": "sort", "identity": "sort",
                                                "name": "Sort products", "type": "select-one",
                                                "has_value": False, "selected_values": [selected]}]})


def action_result(before, after, decision):
    result = ActionResult(decision.action, True, "2026-10-14T12:00:00+00:00", "2026-10-14T12:00:01+00:00",
                          before.url, after.url, target=decision.target, screenshot=after.screenshot)
    for assertion in decision.assertions:
        result.assertions.append(AssertionResult(assertion["kind"], assertion["expected"],
                                                assertion["expected"], True, "Observed"))
    return result


def make_service(tmp_path):
    settings = Settings(data_dir=tmp_path, database_path=tmp_path / "db.sqlite3",
                        artifact_dir=tmp_path / "artifacts", secret_key="test",
                        cors_origins=(), headless=True, browser="chromium", http_timeout_seconds=1)
    repository = SQLiteRepository(settings.database_path, SecretCipher(tmp_path, "test"))
    repository.initialize()
    source_id = repository.create_exploration(ExplorationRequest(URL,
        ModelSelection("unused", "unused-agent"), ModelSelection("unused", "unused-aux"), ExplorationOptions()))
    repository.update_exploration(source_id, status="completed", finished_at="2026-10-14T12:00:02+00:00")
    before, after = observed(), observed(selected="za")
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

        async def show_intervention_panel(self, message):
            self.preparation_message = message

        async def hide_intervention_panel(self):
            pass

        async def wait_for_user_input(self):
            await self.prepared.wait()
            self.prepared.clear()

        async def wait_until_inactive(self):
            await self.inactive.wait()

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
    service.intervention_timeout_seconds = 0.15
    page = observed("e42")
    page.url = "https://example.test/"
    browsers = install_browser(monkeypatch, before=page)
    new_id = service.submit(source_id, "CT001")
    task = service.tasks[new_id]
    await wait_preparation(service, new_id, 1)
    started = time.monotonic()
    await asyncio.sleep(0.10)
    service.resume(new_id)
    await wait_preparation(service, new_id, 2)
    service.resume(new_id)
    await wait_preparation(service, new_id, 3)
    await task
    assert time.monotonic() - started < 0.22
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
async def test_visual_checks_never_reuse_the_original_visual_approval(tmp_path, monkeypatch):
    service, repository, source_id, scenario, _ = make_service(tmp_path)
    scenario.evidence["assertions"].append({"kind": "visual_model", "expected": "No overlap", "passed": True})
    scenario.evidence["visual_assessment"] = {"requested": True, "status": "assessed"}
    replace_scenario(repository, source_id, scenario)
    assert service.preflight(source_id, "CT001")["scope"]["missing_visual_checks"] is True
    install_browser(monkeypatch)
    new_id = service.submit(source_id, "CT001")
    await wait_phase(service, new_id, "waiting_preparation")
    service.resume(new_id)
    await service.tasks[new_id]
    result = repository.get_exploration(new_id)["scenarios"][0]
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
