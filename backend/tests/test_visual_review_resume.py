import asyncio
import copy
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from autojev.adapters.repository import SQLiteRepository
from autojev.adapters.openai_compatible import ProviderConnectionError
from autojev.adapters.secrets import SecretCipher
from autojev.config import Settings
from autojev.domain.models import (
    ActionResult, AgentDecision, AssertionResult, ExplorationOptions,
    ExplorationRequest, ModelSelection, Observation, ScenarioStatus,
    TestScenario as StoredScenario,
)
from autojev.main import create_app
from autojev.services.exploration import ExplorationManager
from autojev.services.visual_review_input import build_visual_review_input, restore_visual_review_input
from autojev.services.visual_review_resume import VisualReviewResumeService, visual_review_resume_summary


def settings(tmp_path):
    return Settings(data_dir=tmp_path, database_path=tmp_path / "db.sqlite3",
                    artifact_dir=tmp_path / "artifacts", secret_key="test-passphrase",
                    cors_origins=("http://localhost:3000",), headless=True,
                    browser="chromium", http_timeout_seconds=1,
                    auxiliary_request_interval_seconds=0, auxiliary_tail_timeout_seconds=5)


def inputs():
    observation = Observation("https://example.test/cart", "Cart", "Products Cart",
                              [{"name": "Continue", "role": "button"}], screenshot="after.png")
    decision = AgentDecision("click", "Open cart", target="Cart")
    result = ActionResult("click", True, "2026-10-07T12:00:00+00:00", "2026-10-07T12:00:01+00:00",
                          "https://example.test/items", observation.url,
                          assertions=[AssertionResult("cart_count", 1, 1, True, "Cart contains one item")],
                          screenshot="after.png", evidence={"action_step": 7, "before_screenshot": "before.png",
                                                                "before_text": "Products"})
    return observation, decision, result


def scenario(sequence=1, *, saved=True, status=ScenarioStatus.PASS):
    observation, decision, result = inputs()
    native = ExplorationManager._scenario_from_evidence(observation, decision, result, sequence)
    native.status = status
    native.evidence["visual_assessment"] = {"requested": True, "status": "unavailable", "reason": "auxiliary_request_budget_exhausted"}
    native.evidence["timeline"] = [{"id": "local-history", "kind": "assertion", "title": "Business check"},
                                    {"kind": "observation", "title": "Depois da ação", "screenshot": "after.png"}]
    native.evidence["visual_assessment_history"] = [{"status": "assessed", "reason": "older-evidence"}]
    if saved:
        native.evidence["visual_review_input"] = build_visual_review_input(observation, decision, result, sequence)
    return native


def test_completed_review_state_is_not_eligible_for_another_paid_batch():
    item = {
        "status": "completed", "options": {"auxiliary_evidence_mode": "visual"},
        "scenarios": [{"evidence": {"visual_assessment": {"status": "assessed"}}}],
        "events": [{"type": "visual_review_resume_state", "payload": {
            "running": False, "eligible": True, "pending": 1,
        }}],
    }
    summary = visual_review_resume_summary(item)
    assert summary["pending"] == 0 and summary["eligible"] is False


def make_service(tmp_path, count=1, *, saved=True, status=ScenarioStatus.PASS):
    config = settings(tmp_path)
    repository = SQLiteRepository(config.database_path, SecretCipher(tmp_path, "test-passphrase"))
    repository.initialize()
    run_id = repository.create_exploration(ExplorationRequest("https://example.test",
        ModelSelection("local", "agent"), ModelSelection("local", "aux"),
        ExplorationOptions(auxiliary_evidence_mode="visual")))
    repository.update_exploration(run_id, status="completed", finished_at="2026-10-07T12:00:02+00:00")
    for index in range(1, count + 1):
        repository.add_scenario(run_id, index, scenario(index, saved=saved, status=status))
    client = SimpleNamespace(chat_request_count=0, chat_request_budget=None)
    usage_roles = []

    async def record_usage(_id, clients, *, role):
        assert clients == [client]
        usage_roles.append(role)

    manager = SimpleNamespace(repository=repository, _client_for=lambda _id: client,
                              _record_model_usage=record_usage,
                              _merge_scenario_enrichment=ExplorationManager._merge_scenario_enrichment,
                              _verification_summary=ExplorationManager._verification_summary)
    return VisualReviewResumeService(manager, config), run_id, repository, client, usage_roles


def generator(monkeypatch, reasons, *, cache_after_first=False, block=None):
    calls = []
    cache = []

    class Generator:
        def __init__(self, client, model, screenshot_dir, evidence_mode):
            assert screenshot_dir.name == "screenshots"
            self.client, self.model = client, model
            self.non_retryable_reason = None
            self.cooldown_remaining_seconds = 0

        async def cached_scenario(self, observation, decision, result, sequence):
            if cache_after_first and cache:
                cached = copy.deepcopy(cache[-1])
                cached.evidence["visual_review_provenance"]["source"] = "auxiliary_cache"
                return cached
            return None

        async def create_scenario(self, observation, decision, result, sequence):
            if block:
                block.set()
                await asyncio.sleep(20)
            reason = reasons[min(len(calls), len(reasons) - 1)]
            if reason != "screenshot_not_available":
                assert self.client.chat_request_count < self.client.chat_request_budget
                self.client.chat_request_count += 1
                calls.append(sequence)
            generated = scenario(sequence)
            generated.evidence["visual_review_provenance"] = {
                "source": "auxiliary_llm", "model_input": "text_and_image", "model": self.model}
            generated.evidence["visual_assessment"] = {
                "requested": True, "status": "unavailable" if reason else "assessed",
                "reason": reason, "model": self.model, "findings": [],
                "images": [{"ref": "after", "screenshot": "after.png", "url": observation.url}],
            }
            if not reason:
                cache.append(generated)
            return generated

    monkeypatch.setattr("autojev.services.visual_review_resume.OpenAIAuxiliaryScenarioGenerator", Generator)
    return calls


def test_saved_input_redacts_before_clipping_and_drops_input_values():
    observation, decision, result = inputs()
    secret = "super-secret-password"
    observation.text = "x" * 3495 + secret
    observation.interactive_elements = [{"name": "Password", "type": "password", "value": secret}]
    result.value = secret
    result.message = "Credential was " + secret
    decision.value = secret
    saved = build_visual_review_input(observation, decision, result, 1, [secret])
    serialized = json.dumps(saved)
    assert secret not in serialized
    assert "super" not in serialized
    assert "value" not in saved["observation"]["interactive_elements"][0]
    restored, _, restored_result, limited = restore_visual_review_input({"evidence": {"visual_review_input": saved}}, 1)
    assert not limited and restored.screenshot == "after.png"
    assert restored_result.evidence["visual_assessment"]["requested"] is True


def test_legacy_reconstruction_is_honest_and_excludes_stale_visual_checks():
    stored = asdict(scenario(saved=False))
    stored["evidence"]["assertions"].append({"kind": "visual_model", "passed": False})
    observation, _, result, limited = restore_visual_review_input(stored, 1)
    assert limited and observation.text == "" and observation.interactive_elements == []
    assert observation.screenshot == "after.png"
    assert all(check.kind != "visual_model" for check in result.assertions)
    stored["evidence"]["timeline"] = []
    stored["screenshots"] = ["before-only.png"]
    assert restore_visual_review_input(stored, 1)[0].screenshot is None


@pytest.mark.asyncio
async def test_retry_three_total_attempts_then_preserve_original_ct(tmp_path, monkeypatch):
    service, run_id, repository, client, roles = make_service(tmp_path)
    calls = generator(monkeypatch, ["auxiliary_response_invalid", "auxiliary_timeout", None])
    original = repository.get_exploration(run_id)
    state = service.resume(run_id, max_requests=10, max_attempts=3)
    assert state["running"]
    await service.tasks[run_id]
    stored = repository.get_exploration(run_id)
    reviewed = stored["scenarios"][0]
    assert len(calls) == client.chat_request_count == 3
    assert reviewed["evidence"]["visual_assessment"]["status"] == "assessed"
    assert reviewed["evidence"]["visual_assessment"]["attempts"] == 3
    assert reviewed["status"] == original["scenarios"][0]["status"]
    assert reviewed["title"] == original["scenarios"][0]["title"]
    assert reviewed["evidence"]["assertions"] == original["scenarios"][0]["evidence"]["assertions"]
    assert reviewed["evidence"]["timeline"][0]["id"] == "local-history"
    assert len(reviewed["evidence"]["visual_assessment_history"]) == 2
    assert stored["finished_at"] == original["finished_at"] and stored["status"] == "completed"
    assert roles and set(roles) == {"auxiliary"}
    assert service.state_for(run_id)["completed"] == 1


@pytest.mark.asyncio
async def test_cache_runs_even_after_batch_request_cap_and_never_downgrades_fail(tmp_path, monkeypatch):
    service, run_id, repository, client, _ = make_service(tmp_path, count=2, status=ScenarioStatus.FAIL)
    calls = generator(monkeypatch, [None], cache_after_first=True)
    service.resume(run_id, max_requests=1, max_attempts=3)
    await service.tasks[run_id]
    stored = repository.get_exploration(run_id)
    assert calls == [1] and client.chat_request_count == 1
    assert service.state_for(run_id)["completed"] == 2
    assert all(item["status"] == "FAIL" for item in stored["scenarios"])
    assert stored["scenarios"][1]["evidence"]["visual_assessment"]["provider_requests"] == 0


@pytest.mark.asyncio
async def test_cache_hit_after_an_uncached_budget_blocked_ct_is_not_lost(tmp_path, monkeypatch):
    service, run_id, repository, client, _ = make_service(tmp_path, count=3)
    generator(monkeypatch, [None], cache_after_first=True)
    from autojev.services import visual_review_resume as module
    original_cache = module.OpenAIAuxiliaryScenarioGenerator.cached_scenario

    async def miss_for_second(self, observation, decision, result, sequence):
        return None if sequence == 2 else await original_cache(self, observation, decision, result, sequence)

    monkeypatch.setattr(module.OpenAIAuxiliaryScenarioGenerator, "cached_scenario", miss_for_second)
    service.resume(run_id, max_requests=1)
    await service.tasks[run_id]
    stored = repository.get_exploration(run_id)["scenarios"]
    assert client.chat_request_count == 1
    assert stored[0]["evidence"]["visual_assessment"]["status"] == "assessed"
    assert stored[1]["evidence"]["visual_assessment"]["status"] == "unavailable"
    assert stored[2]["evidence"]["visual_assessment"]["status"] == "assessed"
    assert service.state_for(run_id)["completed"] == 2


@pytest.mark.asyncio
async def test_three_failed_attempts_are_bounded_and_recorded(tmp_path, monkeypatch):
    service, run_id, repository, client, _ = make_service(tmp_path)
    calls = generator(monkeypatch, ["auxiliary_response_invalid"])
    service.resume(run_id, max_requests=10, max_attempts=3)
    await service.tasks[run_id]
    assert len(calls) == client.chat_request_count == 3
    state = service.state_for(run_id)
    assert state["failed"] == state["pending"] == 1
    assert state["reason"] == "completed_with_unavailable_reviews"
    assert repository.get_exploration(run_id)["scenarios"][0]["evidence"]["visual_assessment"]["attempts"] == 3


@pytest.mark.asyncio
async def test_saved_review_keeps_original_sequence_when_ct_ids_have_gaps(tmp_path, monkeypatch):
    service, run_id, repository, _, _ = make_service(tmp_path)
    repository.delete_scenario(run_id, "CT001")
    repository.add_scenario(run_id, 10, scenario(10))
    calls = generator(monkeypatch, [None])
    service.resume(run_id, max_requests=3)
    await service.tasks[run_id]
    assert calls == [10]
    stored = repository.get_exploration(run_id)["scenarios"]
    assert [item["id"] for item in stored] == ["CT010"]


@pytest.mark.asyncio
async def test_batch_exhaustion_does_not_spend_extra_calls_and_preserves_attempts(tmp_path, monkeypatch):
    service, run_id, repository, client, _ = make_service(tmp_path, count=2)
    calls = generator(monkeypatch, ["auxiliary_response_invalid"])
    service.resume(run_id, max_requests=1, max_attempts=3)
    await service.tasks[run_id]
    assert calls == [1] and client.chat_request_count == 1
    state = service.state_for(run_id)
    assert state["reason"] == "auxiliary_request_budget_exhausted"
    assert state["failed"] == 1 and state["pending"] == 2
    assert repository.get_exploration(run_id)["scenarios"][0]["evidence"]["visual_assessment"]["provider_requests"] == 1


@pytest.mark.asyncio
async def test_legacy_and_missing_images_do_not_invent_reviews(tmp_path, monkeypatch):
    service, run_id, repository, client, _ = make_service(tmp_path, saved=False)
    calls = generator(monkeypatch, ["screenshot_not_available"])
    service.resume(run_id)
    await service.tasks[run_id]
    visual = repository.get_exploration(run_id)["scenarios"][0]["evidence"]["visual_assessment"]
    assert not calls and client.chat_request_count == 0
    assert visual["status"] == "unavailable" and visual["context_limited"] is True
    assert "current_dom_and_visible_text_not_preserved" in visual["limitations"]
    assert service.state_for(run_id)["context_limited_count"] == 1


@pytest.mark.asyncio
async def test_duplicate_batch_and_cancel_do_not_cancel_original_exploration(tmp_path, monkeypatch):
    service, run_id, repository, _, _ = make_service(tmp_path)
    started = asyncio.Event()
    generator(monkeypatch, [None], block=started)
    service.resume(run_id)
    await started.wait()
    with pytest.raises(RuntimeError):
        service.resume(run_id)
    state = await service.cancel(run_id)
    assert not state["running"] and state["reason"] == "cancelled"
    assert repository.get_exploration(run_id)["status"] == "completed"


@pytest.mark.asyncio
async def test_cancel_before_coroutine_started_is_durable_and_free(tmp_path, monkeypatch):
    service, run_id, repository, client, _ = make_service(tmp_path)
    calls = generator(monkeypatch, [None])
    service.resume(run_id)
    state = await service.cancel(run_id)
    assert not state["running"] and state["reason"] == "cancelled"
    assert not calls and client.chat_request_count == 0
    assert repository.get_exploration(run_id)["status"] == "completed"


@pytest.mark.asyncio
async def test_restart_marks_durable_running_batch_interrupted(tmp_path):
    service, run_id, repository, _, _ = make_service(tmp_path)
    repository.add_event(run_id, "info", "visual_review_resume_state", "Old process", {
        "running": True, "total": 1, "pending": 1, "requests_used": 1})
    state = service.state_for(run_id)
    assert state["running"] is False and state["reason"] == "backend_restart_interrupted"
    assert visual_review_resume_summary(repository.get_exploration(run_id))["running"] is False


@pytest.mark.asyncio
async def test_startup_recovers_only_the_latest_running_state(tmp_path):
    service, run_id, repository, _, _ = make_service(tmp_path)
    repository.add_event(run_id, "info", "visual_review_resume_state", "Older batch", {"running": True})
    repository.add_event(run_id, "info", "visual_review_resume_state", "Finished batch", {"running": False})
    assert repository.running_visual_review_resume_ids() == []
    repository.add_event(run_id, "info", "visual_review_resume_state", "Unfinished batch", {"running": True})
    assert repository.running_visual_review_resume_ids() == [run_id]
    restarted = VisualReviewResumeService(service.manager, service.settings)
    assert restarted.state_for(run_id)["reason"] == "backend_restart_interrupted"
    assert repository.running_visual_review_resume_ids() == []


def saved_images(service, run_id):
    from PIL import Image
    directory = service.settings.artifact_dir / run_id / "screenshots"
    directory.mkdir(parents=True)
    for name in ("before.png", "after.png"):
        Image.new("RGB", (100, 100), "white").save(directory / name)


@pytest.mark.asyncio
async def test_real_adapter_replay_can_add_grounded_visual_failure_without_rewriting_local_history(tmp_path):
    service, run_id, repository, client, _ = make_service(tmp_path)
    saved_images(service, run_id)

    async def chat_json(*args, **kwargs):
        assert client.chat_request_count < client.chat_request_budget
        client.chat_request_count += 1
        return {"visual_assessment": {"findings": [{
            "category": "clipped_content", "confidence": 0.99,
            "expected": "The cart heading should be fully readable",
            "observed": "The cart heading is visibly cut off at the container edge",
            "evidence": {"image_ref": "after", "page_url": "https://example.test/cart",
                         "region": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.1},
                         "element_label": "Cart heading", "expected_basis": "content_clipped"},
        }]}}

    client.chat_json = chat_json
    old = repository.get_exploration(run_id)["scenarios"][0]
    service.resume(run_id, max_requests=3)
    await service.tasks[run_id]
    new = repository.get_exploration(run_id)["scenarios"][0]
    assert client.chat_request_count == 1
    assert new["status"] == "FAIL" and new["title"] == old["title"]
    assert new["evidence"]["visual_assessment"]["status"] == "assessed"
    assert new["evidence"]["visual_assessment"]["accepted_count"] == 1
    assert new["evidence"]["assertions"][:len(old["evidence"]["assertions"])] == old["evidence"]["assertions"]
    assert new["evidence"]["assertions"][-1]["kind"] == "visual_model"
    assert new["evidence"]["assertions"][-1]["actual"]["confirmed_functional_bug"] is False
    assert new["evidence"]["timeline"][0]["id"] == "local-history"


@pytest.mark.asyncio
async def test_physical_compatibility_requests_count_toward_three_attempt_cap(tmp_path):
    service, run_id, repository, client, _ = make_service(tmp_path)
    saved_images(service, run_id)

    async def chat_json(*args, **kwargs):
        for _ in range(2):
            if client.chat_request_count >= client.chat_request_budget:
                raise ProviderConnectionError("auxiliary_request_budget_exhausted")
            client.chat_request_count += 1
        return {}

    client.chat_json = chat_json
    service.resume(run_id, max_requests=10, max_attempts=3)
    await service.tasks[run_id]
    new = repository.get_exploration(run_id)["scenarios"][0]
    assert client.chat_request_count == 3
    assert new["evidence"]["visual_assessment"]["reason"] == "auxiliary_review_attempts_exhausted"
    assert new["evidence"]["visual_assessment"]["provider_requests"] == 3
    assert new["evidence"]["visual_assessment"]["attempts"] == 2


def test_api_rejects_bad_budgets_nonterminal_text_and_unknown_runs(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        repository = app.state.repository
        run_id = repository.create_exploration(ExplorationRequest("https://example.test",
            ModelSelection("local", "agent"), ModelSelection("local", "aux"), ExplorationOptions()))
        endpoint = f"/api/explorations/{run_id}/visual-reviews/resume"
        for body in ({"max_requests": 0}, {"max_requests": 5001}, {"max_attempts": 0}, {"max_attempts": 6}):
            assert client.post(endpoint, json=body).status_code == 422
        assert client.post(endpoint, json={}).status_code == 409
        repository.update_exploration(run_id, status="completed")
        assert client.post(endpoint, json={}).status_code == 409
        assert client.post("/api/explorations/missing/visual-reviews/resume", json={}).status_code == 404
        assert client.post("/api/explorations/missing/visual-reviews/cancel").status_code == 404
        state = client.get(f"/api/explorations/{run_id}").json()["visual_review_resume"]
        assert not state["running"] and not state["eligible"]
