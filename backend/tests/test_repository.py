import sqlite3

from autojev.adapters.repository import SQLiteRepository
from autojev.adapters.secrets import SecretCipher
from autojev.domain.models import (
    ExplorationOptions,
    ExplorationRequest,
    ModelSelection,
    ProviderModel,
    ScenarioStatus,
    TestScenario as DomainScenario,
    utc_now,
)


def repository(tmp_path):
    result = SQLiteRepository(tmp_path / "db.sqlite3", SecretCipher(tmp_path, "test-key"))
    result.initialize()
    return result


def test_provider_secret_is_write_only(tmp_path):
    repo = repository(tmp_path)
    provider = repo.create_provider(
        "Local LiteLLM",
        "http://localhost:4000/",
        "sk-do-not-return",
        True,
        [ProviderModel("jev", "JEV")],
    )

    assert provider.base_url == "http://localhost:4000"
    assert provider.has_api_key is True
    assert not hasattr(provider, "api_key")
    assert repo.get_provider_secret(provider.id) == "sk-do-not-return"
    raw = (tmp_path / "db.sqlite3").read_bytes()
    assert b"sk-do-not-return" not in raw


def test_usage_columns_migrate_legacy_database_and_round_trip(tmp_path):
    database_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """CREATE TABLE explorations (
                id TEXT PRIMARY KEY,
                app_url TEXT NOT NULL,
                agent_provider_id TEXT NOT NULL,
                agent_model_id TEXT NOT NULL,
                auxiliary_provider_id TEXT NOT NULL,
                auxiliary_model_id TEXT NOT NULL,
                options_json TEXT NOT NULL,
                status TEXT NOT NULL,
                current_url TEXT,
                steps_completed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                report_path TEXT
            )"""
        )

    repo = SQLiteRepository(database_path, SecretCipher(tmp_path, "legacy-key"))
    repo.initialize()
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(explorations)")
        }
    assert {"prompt_tokens", "completion_tokens", "total_tokens", "cost", "cost_known"} <= columns

    provider = repo.create_provider("P", "http://localhost:4000", None, True, [ProviderModel("m", "M")])
    run_id = repo.create_exploration(
        ExplorationRequest(
            app_url="http://localhost:3000",
            agent=ModelSelection(provider.id, "m"),
            auxiliary_llm=ModelSelection(provider.id, "m"),
            options=ExplorationOptions(max_steps=3),
        )
    )
    repo.update_exploration(
        run_id,
        prompt_tokens=12,
        completion_tokens=7,
        total_tokens=19,
        cost=0.001234,
        cost_known=True,
    )
    usage = repo.get_exploration(run_id, include_details=False)["usage"]
    assert usage == {
        "prompt_tokens": 12,
        "completion_tokens": 7,
        "total_tokens": 19,
        "cost": 0.001234,
        "cost_known": True,
    }


def test_exploration_details_and_summary(tmp_path):
    repo = repository(tmp_path)
    provider = repo.create_provider(
        "P", "http://localhost:4000", None, True, [ProviderModel("m", "M")]
    )
    run_id = repo.create_exploration(
        ExplorationRequest(
            app_url="http://localhost:3000",
            agent=ModelSelection(provider.id, "m"),
            auxiliary_llm=ModelSelection(provider.id, "m"),
            options=ExplorationOptions(max_steps=3),
        )
    )
    scenario = DomainScenario(
        id="CT001",
        title="Visible dashboard",
        description="Check dashboard",
        preconditions=[],
        steps=["Open dashboard"],
        expected_result="Dashboard visible",
        actual_result="Dashboard visible",
        status=ScenarioStatus.PASS,
        url="http://localhost:3000/dashboard",
        created_at=utc_now(),
    )
    repo.add_scenario(run_id, 1, scenario)

    result = repo.get_exploration(run_id)
    assert result["options"]["max_steps"] == 3
    assert result["scenarios"][0]["status"] == "PASS"
    assert result["summary"]["pass"] == 1
    assert result["summary"]["total_scenarios"] == 1

    history_item = repo.list_explorations()[0]
    assert history_item["summary"]["pass"] == 1
    assert history_item["summary"]["fail"] == 0
    assert history_item["summary"]["total_scenarios"] == 1
