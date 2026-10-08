"""Cost and evidence safety for the live auxiliary queue (no paid calls)."""

import asyncio
from types import SimpleNamespace

import pytest

from autojev.domain.models import ScenarioStatus
from autojev.adapters.openai_compatible import OpenAICompatibleClient, ProviderConnectionError
from autojev.services.exploration import ExplorationManager
from autojev.config import DEFAULT_AUXILIARY_MAX_REQUESTS, Settings
from test_exploration import (
    _LiveScenarioRepository, _live_scenario_evidence, _live_scenario_pipeline,
)


class VisualAuxiliary:
    model = "fixture"
    evidence_mode = "visual"
    response_contract = "visual_findings_v1"
    request_count = 0

    def scenario(self, observation, decision, result, sequence, reason=None, cached=False):
        scenario = ExplorationManager._scenario_from_evidence(observation, decision, result, sequence)
        scenario.evidence["documentation_provenance"] = {"source": "local_evidence"}
        scenario.evidence["visual_review_provenance"] = {
            "source": "local_fallback" if reason else ("auxiliary_cache" if cached else "auxiliary_llm"),
            "reason": reason or "auxiliary_response_accepted", "model_input": "text_and_image",
            "response_contract": self.response_contract,
        }
        scenario.evidence["visual_assessment"] = {
            "requested": True, "status": "unavailable" if reason else "assessed",
            "reason": reason, "findings": [],
            "images": [{"ref": "after", "url": observation.url, "screenshot": "cart.png"}],
        }
        return scenario


def visual_evidence(**kwargs):
    observation, decision, result = _live_scenario_evidence(**kwargs)
    observation.screenshot = "cart.png"
    result.evidence["auxiliary_evidence_mode"] = "visual"
    result.evidence["visual_assessment"] = {"requested": True, "status": "pending"}
    return observation, decision, result


@pytest.mark.asyncio
async def test_exact_cache_is_used_after_paid_budget_exhaustion():
    class Auxiliary(VisualAuxiliary):
        request_count = 120

        async def cached_scenario(self, *args):
            return self.scenario(*args, cached=True)

        async def create_scenario(self, *args):
            raise AssertionError("A cache hit cannot consume a provider request")

    repository = _LiveScenarioRepository()
    auxiliary = Auxiliary()
    pipeline = _live_scenario_pipeline(repository, auxiliary)
    pipeline.request_budget = 120  # A deliberately exhausted explicit cap.
    await pipeline.start()
    await pipeline.capture("cart", *visual_evidence(), sequence=1)
    await asyncio.wait_for(pipeline.finish(), 1)
    assert pipeline.enriched == {"cart"} and not pipeline.skipped
    assert auxiliary.request_count == 120
    assert repository.scenarios[1].evidence["auxiliary_review_metrics"]["provider_requests"] == 0


@pytest.mark.asyncio
async def test_cache_miss_does_not_bypass_exhausted_paid_budget():
    class Auxiliary(VisualAuxiliary):
        request_count = 120

        async def cached_scenario(self, *args):
            return None

        async def create_scenario(self, *args):
            raise AssertionError("A cache miss must still obey the request cap")

    repository = _LiveScenarioRepository()
    pipeline = _live_scenario_pipeline(repository, Auxiliary())
    pipeline.request_budget = 120
    await pipeline.start()
    await pipeline.capture("cart", *visual_evidence(), sequence=1)
    await asyncio.wait_for(pipeline.finish(), 1)
    assert pipeline.skipped == {"cart"}
    assert repository.scenarios[1].evidence["visual_assessment"]["reason"] == "auxiliary_request_budget_exhausted"


@pytest.mark.asyncio
async def test_invalid_response_recovers_on_third_total_attempt():
    class Auxiliary(VisualAuxiliary):
        async def create_scenario(self, *args):
            self.request_count += 1
            return self.scenario(*args, reason="auxiliary_response_invalid" if self.request_count < 3 else None)

    repository = _LiveScenarioRepository()
    auxiliary = Auxiliary()
    pipeline = _live_scenario_pipeline(repository, auxiliary)
    await pipeline.start()
    await pipeline.capture("cart", *visual_evidence(), sequence=1)
    await asyncio.wait_for(pipeline.finish(), 1)
    assert auxiliary.request_count == 3
    assert pipeline.enriched == {"cart"}
    assert repository.scenarios[1].status == ScenarioStatus.PASS
    assert repository.scenarios[1].evidence["auxiliary_review_metrics"]["attempts"] == 3


@pytest.mark.asyncio
async def test_authentication_failure_is_never_retried():
    class Auxiliary(VisualAuxiliary):
        async def create_scenario(self, *args):
            self.request_count += 1
            return self.scenario(*args, reason="auxiliary_authentication_rejected")

    repository = _LiveScenarioRepository()
    auxiliary = Auxiliary()
    pipeline = _live_scenario_pipeline(repository, auxiliary)
    await pipeline.start()
    await pipeline.capture("cart", *visual_evidence(), sequence=1)
    await asyncio.wait_for(pipeline.finish(), 1)
    assert auxiliary.request_count == 1 and pipeline.skipped == {"cart"}


@pytest.mark.asyncio
async def test_debounce_replaces_fast_obsolete_revision_before_any_request():
    class Auxiliary(VisualAuxiliary):
        seen_titles = []

        async def create_scenario(self, observation, *args):
            self.request_count += 1
            self.seen_titles.append(observation.title)
            return self.scenario(observation, *args)

    repository = _LiveScenarioRepository()
    auxiliary = Auxiliary()
    pipeline = _live_scenario_pipeline(repository, auxiliary)
    pipeline.manager.settings = SimpleNamespace(auxiliary_review_debounce_seconds=0.03)
    await pipeline.start()
    await pipeline.capture("cart", *visual_evidence(title="Old"), sequence=1)
    await asyncio.sleep(0)
    await pipeline.capture("cart", *visual_evidence(title="New"), sequence=1)
    await asyncio.wait_for(pipeline.finish(), 1)
    assert auxiliary.request_count == 1 and auxiliary.seen_titles == ["New"]
    assert repository.scenarios[1].evidence["visual_review_input"]["observation"]["title"] == "New"
    assert pipeline.queue._unfinished_tasks == 0


@pytest.mark.asyncio
async def test_cancel_during_settle_never_starts_paid_call():
    class Auxiliary(VisualAuxiliary):
        async def create_scenario(self, *args):
            raise AssertionError("Cancelled evidence must not start another call")

    repository = _LiveScenarioRepository()
    pipeline = _live_scenario_pipeline(repository, Auxiliary())
    pipeline.manager.settings = SimpleNamespace(auxiliary_review_debounce_seconds=0.03)
    await pipeline.start()
    await pipeline.capture("cart", *visual_evidence(), sequence=1)
    await asyncio.sleep(0)
    repository.cancel_requested = True
    await asyncio.wait_for(pipeline.finish(), 1)
    assert pipeline.skipped == {"cart"}


@pytest.mark.asyncio
async def test_format_fallback_cannot_multiply_three_http_attempt_allowance():
    client = OpenAICompatibleClient("https://provider.invalid/v1", None)

    class Auxiliary(VisualAuxiliary):
        async def create_scenario(self, *args):
            # Simulate initial HTTP + explicit format-compatibility fallback.
            # There is deliberately no network traffic in this regression.
            try:
                for _ in range(2):
                    client._claim_chat_request()
                    self.request_count += 1
            except ProviderConnectionError:
                return self.scenario(*args, reason="auxiliary_request_budget_exhausted")
            return self.scenario(*args, reason="auxiliary_response_invalid")

    repository = _LiveScenarioRepository()
    auxiliary = Auxiliary()
    pipeline = _live_scenario_pipeline(repository, auxiliary)
    pipeline.auxiliary_client = client
    pipeline.manager.settings = SimpleNamespace(auxiliary_review_retry_delay_seconds=0)
    client.chat_request_budget = pipeline.request_budget
    await pipeline.start()
    await pipeline.capture("cart", *visual_evidence(), sequence=1)
    await asyncio.wait_for(pipeline.finish(), 1)
    assert client.chat_request_count == auxiliary.request_count == 3
    assert repository.scenarios[1].evidence["visual_assessment"]["reason"] == "auxiliary_review_attempts_exhausted"
    assert client.chat_request_budget == DEFAULT_AUXILIARY_MAX_REQUESTS
    assert pipeline.queue._unfinished_tasks == 0


@pytest.mark.asyncio
async def test_inflight_obsolete_review_is_archived_not_silently_lost():
    started, release = asyncio.Event(), asyncio.Event()

    class Auxiliary(VisualAuxiliary):
        async def create_scenario(self, *args):
            self.request_count += 1
            if self.request_count == 1:
                started.set()
                await release.wait()
            return self.scenario(*args)

    repository = _LiveScenarioRepository()
    auxiliary = Auxiliary()
    pipeline = _live_scenario_pipeline(repository, auxiliary)
    await pipeline.start()
    await pipeline.capture("cart", *visual_evidence(title="Old"), sequence=1)
    await asyncio.wait_for(started.wait(), 1)
    await pipeline.capture("cart", *visual_evidence(title="New"), sequence=1)
    release.set()
    await asyncio.wait_for(pipeline.finish(), 1)
    scenario = repository.scenarios[1]
    assert scenario.status == ScenarioStatus.PASS and scenario.url.endswith("/new")
    assert len(scenario.evidence["visual_assessment_history"]) == 1
    assert scenario.evidence["visual_review_input"]["observation"]["title"] == "New"
    obsolete = [payload for kind, payload in repository.events if kind == "scenario_review_obsolete"]
    assert len(obsolete) == 1 and obsolete[0]["review_provider_requests"] == 1
    assert pipeline.enriched == {"cart"} and auxiliary.request_count == 2


async def drain_live_queue(pipeline):
    await asyncio.wait_for(pipeline.queue.join(), 5)


@pytest.mark.asyncio
async def test_150_cts_with_revisits_receive_final_reviews_with_default_finite_cap():
    class Auxiliary(VisualAuxiliary):
        async def create_scenario(self, *args):
            self.request_count += 1
            return self.scenario(*args)

    repository = _LiveScenarioRepository()
    auxiliary = Auxiliary()
    pipeline = _live_scenario_pipeline(repository, auxiliary)
    assert pipeline.request_budget == DEFAULT_AUXILIARY_MAX_REQUESTS == 300
    await pipeline.start()
    for sequence in range(1, 151):
        await pipeline.capture(str(sequence), *visual_evidence(title="Initial"), sequence=sequence)
    await drain_live_queue(pipeline)
    assert auxiliary.request_count == 150  # Initial reviews really run live.
    for title in ("Revisit 1", "Revisit 2", "Final"):
        for sequence in range(1, 151):
            await pipeline.capture(str(sequence), *visual_evidence(title=title), sequence=sequence)
        await drain_live_queue(pipeline)
        assert auxiliary.request_count == 150
        assert all(s.evidence["visual_assessment"]["status"] != "assessed" for s in repository.scenarios.values())
    await asyncio.wait_for(pipeline.finish(), 10)
    assert auxiliary.request_count == 300
    assert len(pipeline.enriched) == 150 and not pipeline.skipped
    assert not pipeline._deferred_visual_jobs and pipeline.queue._unfinished_tasks == 0
    assert all(s.evidence["visual_review_input"]["observation"]["title"] == "Final" for s in repository.scenarios.values())
    assert all(len(s.evidence["visual_assessment_history"]) == 1 for s in repository.scenarios.values())


@pytest.mark.asyncio
async def test_explicit_120_cap_is_not_raised_to_fit_150_cts():
    class Auxiliary(VisualAuxiliary):
        async def create_scenario(self, *args):
            self.request_count += 1
            return self.scenario(*args)

    repository = _LiveScenarioRepository()
    auxiliary = Auxiliary()
    pipeline = _live_scenario_pipeline(repository, auxiliary)
    pipeline.request_budget = 120
    await pipeline.start()
    for sequence in range(1, 151):
        await pipeline.capture(str(sequence), *visual_evidence(), sequence=sequence)
    await asyncio.wait_for(pipeline.finish(), 5)
    assert auxiliary.request_count == 120
    assert len(pipeline.enriched) == 120 and len(pipeline.skipped) == 30


@pytest.mark.asyncio
async def test_final_review_flush_larger_than_queue_capacity_is_lossless():
    class Auxiliary(VisualAuxiliary):
        async def create_scenario(self, *args):
            self.request_count += 1
            return self.scenario(*args)

    repository = _LiveScenarioRepository()
    auxiliary = Auxiliary()
    pipeline = _live_scenario_pipeline(repository, auxiliary)
    pipeline.request_budget = 600
    await pipeline.start()
    for sequence in range(1, 271):
        await pipeline.capture(str(sequence), *visual_evidence(title="First"), sequence=sequence)
        await drain_live_queue(pipeline)
    for sequence in range(1, 271):
        await pipeline.capture(str(sequence), *visual_evidence(title="Last"), sequence=sequence)
        await drain_live_queue(pipeline)
    assert len(pipeline._deferred_visual_jobs) == 270 > pipeline._QUEUE_LIMIT
    await asyncio.wait_for(pipeline.finish(), 10)
    assert auxiliary.request_count == 540 and len(pipeline.enriched) == 270
    assert pipeline.queue._unfinished_tasks == 0 and not pipeline._deferred_visual_jobs


@pytest.mark.asyncio
async def test_cancelling_deferred_revisits_does_not_make_more_http_requests():
    class Auxiliary(VisualAuxiliary):
        async def create_scenario(self, *args):
            self.request_count += 1
            return self.scenario(*args)

    repository = _LiveScenarioRepository()
    auxiliary = Auxiliary()
    pipeline = _live_scenario_pipeline(repository, auxiliary)
    await pipeline.start()
    await pipeline.capture("cart", *visual_evidence(title="First"), sequence=1)
    await drain_live_queue(pipeline)
    await pipeline.capture("cart", *visual_evidence(title="Last"), sequence=1)
    await drain_live_queue(pipeline)
    assert len(pipeline._deferred_visual_jobs) == 1
    repository.cancel_requested = True
    await asyncio.wait_for(pipeline.finish(cancelled=True), 1)
    assert auxiliary.request_count == 1 and not pipeline._deferred_visual_jobs
    assert repository.scenarios[1].evidence["visual_assessment"]["reason"] == "exploration_cancelled"


@pytest.mark.asyncio
async def test_exact_cached_revisit_is_applied_live_without_another_paid_request():
    class Auxiliary(VisualAuxiliary):
        async def cached_scenario(self, *args):
            return self.scenario(*args, cached=True) if self.request_count else None

        async def create_scenario(self, *args):
            self.request_count += 1
            return self.scenario(*args)

    repository = _LiveScenarioRepository()
    auxiliary = Auxiliary()
    pipeline = _live_scenario_pipeline(repository, auxiliary)
    await pipeline.start()
    await pipeline.capture("cart", *visual_evidence(), sequence=1)
    await drain_live_queue(pipeline)
    await pipeline.capture("cart", *visual_evidence(), sequence=1)
    await drain_live_queue(pipeline)
    assert pipeline.enriched == {"cart"} and not pipeline._deferred_visual_jobs
    assert auxiliary.request_count == 1
    await pipeline.finish()


@pytest.mark.asyncio
async def test_new_ct_review_stays_parallel_while_an_older_revisit_waits_for_final():
    class Auxiliary(VisualAuxiliary):
        titles = []

        async def create_scenario(self, observation, *args):
            self.request_count += 1
            self.titles.append(observation.title)
            return self.scenario(observation, *args)

    repository = _LiveScenarioRepository()
    auxiliary = Auxiliary()
    pipeline = _live_scenario_pipeline(repository, auxiliary)
    await pipeline.start()
    await pipeline.capture("old", *visual_evidence(title="First"), sequence=1)
    await drain_live_queue(pipeline)
    await pipeline.capture("old", *visual_evidence(title="Final"), sequence=1)
    await drain_live_queue(pipeline)
    await pipeline.capture("new", *visual_evidence(title="New CT"), sequence=2)
    await drain_live_queue(pipeline)
    assert auxiliary.titles == ["First", "New CT"]
    assert pipeline.enriched == {"new"}
    await pipeline.finish()
    assert auxiliary.titles == ["First", "New CT", "Final"]
    assert pipeline.enriched == {"new", "old"}


@pytest.mark.asyncio
async def test_removed_and_recreated_ct_is_reviewed_live_not_held_as_a_revisit():
    class Auxiliary(VisualAuxiliary):
        async def create_scenario(self, *args):
            self.request_count += 1
            return self.scenario(*args)

    repository = _LiveScenarioRepository()
    auxiliary = Auxiliary()
    pipeline = _live_scenario_pipeline(repository, auxiliary)
    await pipeline.start()
    await pipeline.capture("cart", *visual_evidence(), sequence=1)
    await drain_live_queue(pipeline)
    await pipeline.remove("cart", 1)
    await pipeline.capture("cart", *visual_evidence(title="New cart"), sequence=2)
    await drain_live_queue(pipeline)
    assert auxiliary.request_count == 2 and pipeline.enriched == {"cart"}
    assert not pipeline._deferred_visual_jobs
    await pipeline.finish()


@pytest.mark.asyncio
async def test_final_tail_prioritizes_unreviewed_ct_before_positive_revisit_with_tight_cap():
    class Auxiliary(VisualAuxiliary):
        titles = []

        async def create_scenario(self, observation, *args):
            self.request_count += 1
            self.titles.append(observation.title)
            return self.scenario(observation, *args)

    repository = _LiveScenarioRepository()
    auxiliary = Auxiliary()
    pipeline = _live_scenario_pipeline(repository, auxiliary)
    pipeline.request_budget = 2
    await pipeline.start()
    await pipeline.capture("old", *visual_evidence(title="First"), sequence=1)
    await drain_live_queue(pipeline)
    await pipeline.capture("old", *visual_evidence(title="Revisit"), sequence=1)
    await drain_live_queue(pipeline)
    await pipeline.capture("new", *visual_evidence(title="New CT"), sequence=2)
    await pipeline.finish()
    assert auxiliary.titles == ["First", "New CT"]
    assert pipeline.enriched == {"new"} and pipeline.skipped == {"old"}
    assert repository.scenarios[1].evidence["visual_assessment"]["status"] == "unavailable"


def test_default_budget_is_finite_and_environment_override_is_respected(monkeypatch):
    monkeypatch.delenv("AUTOJEV_AUXILIARY_MAX_REQUESTS", raising=False)
    assert Settings.from_env().auxiliary_max_requests == 300
    monkeypatch.setenv("AUTOJEV_AUXILIARY_MAX_REQUESTS", "120")
    assert Settings.from_env().auxiliary_max_requests == 120
    monkeypatch.setenv("AUTOJEV_AUXILIARY_MAX_REQUESTS", "0")
    assert Settings.from_env().auxiliary_max_requests == 0  # Never unlimited.
