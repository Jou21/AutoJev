from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from autojev.config import Settings
from autojev.domain.models import (
    ExplorationOptions, ExplorationRequest, ModelSelection, ScenarioStatus,
    TestScenario as StoredScenario,
)
from autojev.main import create_app
from autojev.services.rerun_projection import without_rerun_recipes


@pytest.fixture
def runtime(tmp_path: Path):
    settings = Settings(tmp_path, tmp_path / "db.sqlite3", tmp_path / "artifacts",
                        "test-passphrase", ("http://localhost:3000",), True, "chromium", 1)
    app = create_app(settings)
    with TestClient(app) as client:
        repository = app.state.repository
        source = repository.create_exploration(ExplorationRequest(
            "https://shop.test", ModelSelection("local", "agent"),
            ModelSelection("local", "aux"), ExplorationOptions(),
        ))
        repository.add_scenario(source, 1, StoredScenario(
            "CT001", "Selecionar opção", "Registro original", [], ["Selecionar"],
            "Opção aplicada", "Opção aplicada", ScenarioStatus.PASS,
            "https://shop.test", "2026-10-14T12:00:00Z",
            evidence={"action_step": 1, "assertions": [{"kind": "selection_matches", "passed": True}]},
        ))
        repository.update_exploration(source, status="completed")
        yield app, client, source


def path(source):
    return f"/api/explorations/{source}/scenarios/CT001/rerun"


def test_historical_preflight_is_read_only_and_explains_missing_recipe(runtime):
    app, client, source = runtime
    before = app.state.repository.get_exploration(source)
    response = client.get(path(source))
    assert response.status_code == 200
    assert response.json()["available"] is False
    assert response.json()["code"] == "missing_recipe"
    assert response.json()["manual_preparation_required"] is True
    assert "banco de dados" in " ".join(response.json()["effects"])
    assert app.state.repository.get_exploration(source) == before
    assert app.state.scenario_rerun_service.tasks == {}
    assert client.post(path(source), json={"confirm": True}).status_code == 409
    assert app.state.repository.get_exploration(source) == before


@pytest.mark.parametrize("body", [{}, {"confirm": False}, {"confirm": 1}, {"confirm": "true"},
                                   {"confirm": True, "sql": "DROP TABLE anything"},
                                   {"confirm": True, "steps": [{"type": "database"}]}])
def test_rerun_requires_strict_explicit_consent_and_never_accepts_custom_execution(body, runtime):
    app, client, source = runtime
    assert client.post(path(source), json=body).status_code == 422
    assert app.state.scenario_rerun_service.tasks == {}


@pytest.mark.parametrize("origin", ["null", "https://unrelated.test", "http://localhost:3000.evil.test"])
def test_untrusted_browser_origin_cannot_start_a_rerun(origin, runtime):
    app, client, source = runtime
    assert client.post(path(source), json={"confirm": True}, headers={"Origin": origin}).status_code == 403
    assert app.state.scenario_rerun_service.tasks == {}


def test_confirmed_start_and_existing_controls_delegate_to_rerun_service_without_old_history_changes(runtime):
    app, client, source = runtime
    repository = app.state.repository
    before = repository.get_exploration(source)
    child = repository.create_exploration(ExplorationRequest(
        "https://shop.test", ModelSelection("local", "agent"), ModelSelection("local", "aux"),
        ExplorationOptions(mode="single_ct_rerun"),
    ))
    calls = []
    service = app.state.scenario_rerun_service
    service.submit = lambda run, case: calls.append(("submit", run, case)) or child
    service.state_for = lambda run: {"source_exploration_id": source, "source_scenario_id": "CT001"} if run == child else {}
    service.resume = lambda run: calls.append(("resume", run))
    service.cancel = lambda run: calls.append(("cancel", run))
    response = client.post(path(source), json={"confirm": True}, headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 202
    assert response.json()["id"] == child and child != source
    assert response.json()["rerun"]["source_scenario_id"] == "CT001"
    assert client.post(f"/api/explorations/{child}/resume").status_code == 202
    assert client.post(f"/api/explorations/{child}/cancel").status_code == 202
    assert calls == [("submit", source, "CT001"), ("resume", child), ("cancel", child)]
    assert repository.get_exploration(source) == before


def test_recipes_are_private_copies_not_api_or_exported_evidence(runtime, tmp_path):
    app, client, source = runtime
    repository = app.state.repository
    recipe = {"version": "ct-rerun-v1", "private_sentinel": "private-replay-metadata-do-not-export"}
    repository.add_action(source, 1, {"action": "select", "target": "e0"}, {
        "before_url": "https://shop.test", "after_url": "https://shop.test",
        "evidence": {"rerun_recipe": recipe},
    })
    before = repository.get_exploration(source)
    projected = client.get(f"/api/explorations/{source}")
    assert projected.status_code == 200
    assert "rerun_recipe" not in projected.text
    assert recipe["private_sentinel"] not in projected.text
    output = app.state.settings.artifact_dir / source / "report.html"
    app.state.exploration_manager.report_renderer.generate(before, str(output))
    repository.update_exploration(source, report_path=str(output))
    original_html = output.read_text()
    response = client.get(f"/api/explorations/{source}/report")
    assert response.status_code == 200
    assert recipe["private_sentinel"] not in response.text
    assert '"run_id"' in response.text
    assert output.read_text() == original_html
    assert repository.get_exploration(source)["actions"] == before["actions"]
    value = {"nested": [{"rerun_recipe": recipe, "passed": True}], "status": "PASS"}
    unchanged = deepcopy(value)
    assert without_rerun_recipes(value) == {"nested": [{"passed": True}], "status": "PASS"}
    assert value == unchanged


def test_unknown_source_or_case_is_not_a_new_execution(runtime):
    _, client, source = runtime
    assert client.get(path("missing-source")).status_code == 404
    assert client.get(path(source).replace("CT001", "CT999")).status_code == 404


def test_historical_preparation_block_has_human_reason_without_rewriting_history(runtime):
    app, client, source = runtime
    repository = app.state.repository
    repository.add_event(source, "info", "scenario_rerun_state", "Estado preservado", {
        "source_exploration_id": "original-run", "source_scenario_id": "CT001",
        "running": False, "phase": "finished", "reason": "before_url_changed",
    })
    repository.update_exploration(source, status="partial", error="before_url_changed")
    before = repository.get_exploration(source)
    response = client.get(f"/api/explorations/{source}")
    assert response.status_code == 200
    assert response.json()["rerun"]["reason_label"] == (
        "A página aberta não é a mesma em que o teste original começou."
    )
    assert response.json()["rerun"]["reason"] == "before_url_changed"
    assert repository.get_exploration(source) == before
