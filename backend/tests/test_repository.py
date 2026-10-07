import sqlite3

from autojev.adapters.repository import SQLiteRepository
from autojev.adapters.secrets import SecretCipher
from autojev.domain.models import (
    ExplorationOptions,
    ExplorationRequest,
    ExplorationStatus,
    ModelSelection,
    ProviderModel,
    ScenarioStatus,
    utc_now,
)
from autojev.domain.models import (
    TestScenario as DomainScenario,
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


def test_provider_model_capabilities_round_trip(tmp_path):
    repo = repository(tmp_path)
    provider = repo.create_provider(
        "Vision provider",
        "http://localhost:4000",
        None,
        True,
        [
            ProviderModel(
                "vision-model",
                "Vision model",
                input_modalities=["TEXT", "image", "image"],
            )
        ],
    )

    assert provider.models[0].input_modalities == ["text", "image"]
    assert provider.models[0].supports_vision is True
    assert provider.models[0].capabilities_known is True


def test_provider_model_capabilities_migrate_legacy_database(tmp_path):
    database_path = tmp_path / "legacy-models.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE providers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                base_url TEXT NOT NULL,
                api_key_encrypted TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE provider_models (
                provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
                model_id TEXT NOT NULL,
                label TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(provider_id, model_id)
            );
            """
        )
        connection.execute(
            "INSERT INTO providers VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("provider", "Legacy", "http://localhost:4000", None, 1, "now", "now"),
        )
        connection.execute(
            "INSERT INTO provider_models VALUES (?, ?, ?, ?)",
            ("provider", "legacy-model", "Legacy model", 1),
        )

    repo = SQLiteRepository(database_path, SecretCipher(tmp_path, "legacy-model-key"))
    repo.initialize()

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(provider_models)")
        }
    assert {
        "input_modalities_json",
        "supports_vision",
        "capabilities_known",
    } <= columns
    model = repo.get_provider("provider").models[0]
    assert model.input_modalities == []
    assert model.supports_vision is False
    assert model.capabilities_known is False


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
        connection.execute(
            """INSERT INTO explorations (
                id, app_url, agent_provider_id, agent_model_id,
                auxiliary_provider_id, auxiliary_model_id, options_json,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("legacy-run", "https://app.test", "provider", "same-model",
             "provider", "same-model", "{}", "completed", "now"),
        )

    repo = SQLiteRepository(database_path, SecretCipher(tmp_path, "legacy-key"))
    repo.initialize()
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(explorations)")
        }
    assert {"prompt_tokens", "completion_tokens", "total_tokens", "cost", "cost_known", "usage_by_role_json", "cost_complete"} <= columns
    legacy = repo.get_exploration("legacy-run", include_details=False)["usage"]
    assert legacy["by_role"] == {}
    assert "cost_complete" not in legacy

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
        cost_complete=True,
        usage_by_role={
            "jev": {"total_tokens": 12, "cost": 0.001, "cost_known": True},
            "auxiliary": {"total_tokens": 7, "cost": 0.000234, "cost_known": True},
        },
    )
    usage = repo.get_exploration(run_id, include_details=False)["usage"]
    assert usage == {
        "prompt_tokens": 12,
        "completion_tokens": 7,
        "total_tokens": 19,
        "cost": 0.001234,
        "cost_known": True,
        "cost_complete": True,
        "by_role": {
            "jev": {"total_tokens": 12, "cost": 0.001, "cost_known": True},
            "auxiliary": {"total_tokens": 7, "cost": 0.000234, "cost_known": True},
        },
    }


def test_new_run_proves_zero_cost_for_both_roles_without_calls(tmp_path):
    repo = repository(tmp_path)
    run_id = repo.create_exploration(ExplorationRequest(
        app_url="https://app.test",
        agent=ModelSelection("provider", "same-model"),
        auxiliary_llm=ModelSelection("provider", "same-model"),
        options=ExplorationOptions(),
    ))
    usage = repo.get_exploration(run_id, include_details=False)["usage"]
    assert usage["cost"] == 0
    assert usage["cost_known"] is True
    assert usage["cost_complete"] is True
    assert set(usage["by_role"]) == {"jev", "auxiliary"}
    for bucket in usage["by_role"].values():
        assert bucket["cost"] == 0
        assert bucket["cost_known"] is True
        assert bucket["cost_complete"] is True
        assert bucket["usage_records"] == 0


def test_restart_fails_every_nonterminal_exploration_phase(tmp_path):
    repo = repository(tmp_path)
    provider = repo.create_provider(
        "P", "http://localhost:4000", None, True, [ProviderModel("m", "M")]
    )
    run_ids = []
    for phase in (
        ExplorationStatus.QUEUED,
        ExplorationStatus.RUNNING,
        ExplorationStatus.WAITING_INPUT,
        ExplorationStatus.POSTPROCESSING,
    ):
        run_id = repo.create_exploration(
            ExplorationRequest(
                app_url="http://localhost:3000",
                agent=ModelSelection(provider.id, "m"),
                auxiliary_llm=ModelSelection(provider.id, "m"),
                options=ExplorationOptions(max_steps=3),
            )
        )
        repo.update_exploration(run_id, status=phase)
        run_ids.append(run_id)

    repo.initialize()

    for run_id in run_ids:
        item = repo.get_exploration(run_id, include_details=False)
        assert item["status"] == ExplorationStatus.FAILED
        assert "Backend restarted" in item["error"]


def test_cancel_request_only_marks_a_still_active_exploration(tmp_path):
    repo = repository(tmp_path)
    provider = repo.create_provider(
        "P", "http://localhost:4000", None, True, [ProviderModel("m", "M")]
    )

    def create_run():
        return repo.create_exploration(
            ExplorationRequest(
                app_url="http://localhost:3000",
                agent=ModelSelection(provider.id, "m"),
                auxiliary_llm=ModelSelection(provider.id, "m"),
                options=ExplorationOptions(max_steps=3),
            )
        )

    active_id = create_run()
    repo.update_exploration(active_id, status=ExplorationStatus.POSTPROCESSING)
    repo.request_cancel(active_id)
    assert repo.is_cancel_requested(active_id) is True

    completed_id = create_run()
    repo.update_exploration(completed_id, status=ExplorationStatus.COMPLETED)
    repo.request_cancel(completed_id)
    assert repo.is_cancel_requested(completed_id) is False


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
    repo.add_event(
        run_id,
        "warning",
        "coverage_complete_with_restrictions",
        "Reachable coverage exhausted with restrictions",
        {"candidates_blocked": 3},
    )

    result = repo.get_exploration(run_id)
    assert result["options"]["max_steps"] == 3
    assert result["scenarios"][0]["status"] == "PASS"
    assert result["summary"]["pass"] == 1
    assert result["summary"]["total_scenarios"] == 1

    history_item = repo.list_explorations()[0]
    assert history_item["summary"]["pass"] == 1
    assert history_item["summary"]["fail"] == 0
    assert history_item["summary"]["total_scenarios"] == 1
    assert history_item["completion_event"] == {
        "type": "coverage_complete_with_restrictions",
        "message": "Reachable coverage exhausted with restrictions",
        "payload": {"candidates_blocked": 3},
    }


def test_delete_provisional_scenario_keeps_global_browser_evidence(tmp_path):
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
        title="Provisional click failure",
        description="Retryable evidence",
        preconditions=[],
        steps=["Click"],
        expected_result="Control responds",
        actual_result="Locator became stale",
        status=ScenarioStatus.FAIL,
        url="http://localhost:3000",
        created_at=utc_now(),
    )
    repo.add_scenario(run_id, 1, scenario)
    repo.add_screenshot(run_id, "global.png", "Browser evidence")
    repo.add_screenshot(run_id, "draft.png", "Draft evidence", "CT001")

    repo.delete_scenario(run_id, "CT001")

    result = repo.get_exploration(run_id)
    assert result["scenarios"] == []
    assert [item["path"] for item in result["screenshots"]] == ["global.png"]
