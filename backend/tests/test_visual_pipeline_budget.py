"""Cost and evidence safety for the live auxiliary queue (no paid calls)."""

import asyncio
from types import SimpleNamespace

import pytest

from autojev.domain.models import ScenarioStatus
from autojev.adapters.openai_compatible import OpenAICompatibleClient, ProviderConnectionError
from autojev.services.exploration import ExplorationManager
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
    assert client.chat_request_budget == 120
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
