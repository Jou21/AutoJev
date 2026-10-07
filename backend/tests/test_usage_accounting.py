import asyncio

import httpx
import pytest

from autojev.adapters.openai_compatible import OpenAICompatibleClient, ProviderConnectionError
from autojev.adapters.repository import SQLiteRepository
from autojev.adapters.secrets import SecretCipher
from autojev.domain.models import ExplorationOptions, ExplorationRequest, ModelSelection
from autojev.services.exploration import ExplorationManager


@pytest.fixture
def ledger(tmp_path):
    repository = SQLiteRepository(tmp_path / "usage.sqlite3", SecretCipher(tmp_path, "usage-tests"))
    repository.initialize()
    run_id = repository.create_exploration(ExplorationRequest(
        app_url="https://app.test",
        agent=ModelSelection("provider", "same-model"),
        auxiliary_llm=ModelSelection("provider", "same-model"),
        options=ExplorationOptions(),
    ))
    manager = object.__new__(ExplorationManager)
    manager.repository = repository
    manager.usage_locks = {}
    return manager, repository, run_id


def client_usage(*records):
    client = OpenAICompatibleClient("https://provider.test/v1", None)
    for record in records:
        client._record_usage({"usage": record})
    return client


@pytest.mark.asyncio
async def test_same_model_has_separate_explicit_roles_and_idempotent_drains(ledger):
    manager, repository, run_id = ledger
    agent = client_usage({"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.002})
    auxiliary = client_usage({"prompt_tokens": 30, "completion_tokens": 5, "cost": 0.003})
    await asyncio.gather(
        manager._record_model_usage(run_id, [agent], None, "jev"),
        manager._record_model_usage(run_id, [auxiliary], None, "auxiliary"),
    )
    usage = repository.get_exploration(run_id, include_details=False)["usage"]
    assert usage["prompt_tokens"] == 40
    assert usage["completion_tokens"] == 7
    assert usage["total_tokens"] == 47
    assert usage["cost"] == pytest.approx(0.005)
    assert usage["cost_complete"] is True
    assert usage["by_role"]["jev"]["cost"] == 0.002
    assert usage["by_role"]["auxiliary"]["cost"] == 0.003
    assert usage["by_role"]["jev"]["total_tokens"] == 12
    assert usage["by_role"]["auxiliary"]["total_tokens"] == 35
    await manager._record_model_usage(run_id, [agent], None, "jev")
    await manager._record_model_usage(run_id, [auxiliary], None, "auxiliary")
    assert repository.get_exploration(run_id, include_details=False)["usage"] == usage


@pytest.mark.asyncio
async def test_parallel_updates_cannot_overwrite_the_other_role(ledger):
    manager, repository, run_id = ledger

    async def record(index):
        client = client_usage({"prompt_tokens": index + 1, "cost": 0.001})
        await asyncio.sleep(0)
        await manager._record_model_usage(run_id, [client], None, "jev" if index % 2 else "auxiliary")

    await asyncio.gather(*(record(index) for index in range(30)))
    usage = repository.get_exploration(run_id, include_details=False)["usage"]
    assert usage["total_tokens"] == sum(range(1, 31))
    assert usage["cost"] == pytest.approx(0.03)
    assert usage["by_role"]["jev"]["cost"] == pytest.approx(0.015)
    assert usage["by_role"]["auxiliary"]["cost"] == pytest.approx(0.015)
    assert sum(bucket["total_tokens"] for bucket in usage["by_role"].values()) == usage["total_tokens"]


@pytest.mark.asyncio
async def test_unused_role_and_provider_reported_zero_are_known_zero(ledger):
    manager, repository, run_id = ledger
    await manager._record_model_usage(
        run_id, [client_usage({"total_tokens": 21, "cost": 0})], None, "auxiliary"
    )
    usage = repository.get_exploration(run_id, include_details=False)["usage"]
    assert usage["cost"] == 0
    assert usage["cost_known"] is True
    assert usage["cost_complete"] is True
    assert usage["by_role"]["jev"]["usage_records"] == 0
    assert usage["by_role"]["jev"]["cost_known"] is True
    assert usage["by_role"]["auxiliary"]["usage_records"] == 1
    assert usage["by_role"]["auxiliary"]["cost_known"] is True


@pytest.mark.asyncio
async def test_missing_price_does_not_inherit_initial_zero_as_a_known_cost(ledger):
    manager, repository, run_id = ledger
    await manager._record_model_usage(
        run_id, [client_usage({"total_tokens": 21})], None, "auxiliary"
    )
    usage = repository.get_exploration(run_id, include_details=False)["usage"]
    assert usage["cost"] is None
    assert usage["cost_known"] is False
    assert usage["cost_complete"] is False
    assert usage["by_role"]["auxiliary"]["cost"] is None
    assert usage["by_role"]["auxiliary"]["cost_known"] is False
    assert usage["by_role"]["auxiliary"]["cost_complete"] is False
    assert usage["by_role"]["jev"]["cost"] == 0
    assert usage["by_role"]["jev"]["cost_complete"] is True


@pytest.mark.asyncio
async def test_mixed_prices_keep_reported_subtotal_and_mark_it_partial(ledger):
    manager, repository, run_id = ledger
    client = client_usage({"total_tokens": 20, "cost": 0.01}, {"total_tokens": 10})
    await manager._record_model_usage(run_id, [client], None, "jev")
    await manager._record_model_usage(
        run_id, [client_usage({"total_tokens": 5, "cost": 0.02})], None, "auxiliary"
    )
    usage = repository.get_exploration(run_id, include_details=False)["usage"]
    assert usage["cost"] == pytest.approx(0.03)
    assert usage["cost_known"] is True
    assert usage["cost_complete"] is False
    assert usage["by_role"]["jev"]["cost"] == 0.01
    assert usage["by_role"]["jev"]["cost_complete"] is False
    assert usage["by_role"]["auxiliary"]["cost_complete"] is True


@pytest.mark.asyncio
async def test_legacy_aggregate_is_not_assigned_to_a_role_or_lost_on_later_drains(ledger):
    manager, repository, run_id = ledger
    repository.update_exploration(
        run_id, total_tokens=100, cost=0.1, cost_known=True,
        cost_complete=None, usage_by_role={},
    )
    legacy = repository.get_exploration(run_id, include_details=False)["usage"]
    assert legacy["by_role"] == {}
    assert "cost_complete" not in legacy
    for role in ("jev", "auxiliary"):
        await manager._record_model_usage(
            run_id, [client_usage({"total_tokens": 1})], None, role
        )
    usage = repository.get_exploration(run_id, include_details=False)["usage"]
    assert usage["cost"] == 0.1
    assert usage["cost_known"] is True
    assert usage["cost_complete"] is False
    assert usage["total_tokens"] == 102
    assert all(bucket["cost"] is None for bucket in usage["by_role"].values())
    assert all(bucket["cost_complete"] is False for bucket in usage["by_role"].values())


@pytest.mark.asyncio
async def test_role_is_required_before_usage_is_drained(ledger):
    manager, _, run_id = ledger
    client = client_usage({"total_tokens": 1, "cost": 0.001})
    with pytest.raises(ValueError, match="explicit"):
        await manager._record_model_usage(run_id, [client])
    assert client.drain_usage()["total_tokens"] == 1


@pytest.mark.parametrize("cost", [None, "invalid", "nan", "inf", -0.1, True, 10**1000])
def test_provider_invalid_or_absent_cost_is_not_a_known_zero(cost):
    client = client_usage({"total_tokens": 12, "cost": cost})
    usage = client.drain_usage()
    assert usage["total_tokens"] == 12
    assert usage["cost_known"] is False
    assert usage["cost_complete"] is False
    assert usage["cost_reported_records"] == 0
    assert usage["usage_records"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["get_exploration", "update_exploration"])
async def test_failed_usage_persistence_is_retried_without_losing_or_duplicating_cost(
    monkeypatch, ledger, operation
):
    manager, repository, run_id = ledger
    original = getattr(repository, operation)
    failures = 1

    def transient_failure(*args, **kwargs):
        nonlocal failures
        if failures:
            failures -= 1
            raise RuntimeError("database temporarily unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(repository, operation, transient_failure)
    agent = client_usage({"total_tokens": 100, "cost": 0.01})
    with pytest.raises(RuntimeError, match="temporarily"):
        await manager._record_model_usage(run_id, [agent], None, "jev")
    assert manager.pending_model_usage[run_id]["jev"]["total_tokens"] == 100
    # The other role can persist independently of JEV's retained buffer.
    await manager._record_model_usage(
        run_id, [client_usage({"total_tokens": 20, "cost": 0.002})], None, "auxiliary"
    )
    assert manager.pending_model_usage[run_id]["jev"]["total_tokens"] == 100
    await manager._record_model_usage(run_id, [agent], None, "jev")
    assert manager.pending_model_usage == {}
    usage = repository.get_exploration(run_id, include_details=False)["usage"]
    assert usage["total_tokens"] == 120
    assert usage["cost"] == pytest.approx(0.012)
    assert usage["by_role"]["jev"]["total_tokens"] == 100
    assert usage["by_role"]["auxiliary"]["total_tokens"] == 20
    await manager._record_model_usage(run_id, [agent], None, "jev")
    assert repository.get_exploration(run_id, include_details=False)["usage"] == usage


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["chat_json", "decisions"])
async def test_provider_timeout_is_persisted_as_uncertain_billing(monkeypatch, ledger, method):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            raise httpx.ReadTimeout("provider timeout")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: Client())
    client = OpenAICompatibleClient("https://provider.test/v1", None)
    with pytest.raises(ProviderConnectionError):
        if method == "chat_json":
            await client.chat_json("same-model", "system", "user")
        else:
            await client.decisions("same-model", {}, {})
    manager, repository, run_id = ledger
    role = "jev" if method == "decisions" else "auxiliary"
    await manager._record_model_usage(run_id, [client], None, role)
    usage = repository.get_exploration(run_id, include_details=False)["usage"]
    assert usage["cost_known"] is False
    assert usage["cost_complete"] is False
    assert usage["by_role"][role]["usage_records"] == 1


@pytest.mark.asyncio
async def test_cancelled_request_drains_missing_price_once(monkeypatch, ledger):
    started = asyncio.Event()

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            started.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: Client())
    client = OpenAICompatibleClient("https://provider.test/v1", None)
    task = asyncio.create_task(client.chat_json("same-model", "system", "user"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    manager, repository, run_id = ledger
    await manager._record_model_usage(run_id, [client], None, "auxiliary")
    once = repository.get_exploration(run_id, include_details=False)["usage"]
    await manager._record_model_usage(run_id, [client], None, "auxiliary")
    assert repository.get_exploration(run_id, include_details=False)["usage"] == once
    assert once["by_role"]["auxiliary"]["usage_records"] == 1
    assert once["cost_complete"] is False
