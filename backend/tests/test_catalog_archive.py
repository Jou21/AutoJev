"""Reversible catalog membership, isolated SQLite; no browser or paid model."""
from copy import deepcopy

import pytest
from test_scenario_rerun_api import runtime as _runtime
from test_test_suites import create_plan, new_manual, round_state

runtime = _runtime


def change(client, root, identity, archived=True, revision=0, **kwargs):
    return client.patch(f"/api/test-suites/{root}/cases/{identity}/catalog-state",
                        json={"archived": archived, "expected_revision": revision}, **kwargs)


def test_archive_original_preserves_results_evidence_and_ids_and_can_be_restored(runtime):
    app, client, root = runtime
    service = app.state.test_suite_service
    original = deepcopy(app.state.repository.get_exploration(root))
    snapshot = service.report_snapshot(root, "initial")["scenarios"]
    response = change(client, root, "CT001")
    assert response.status_code == 200, response.text
    assert response.json()["catalog_revision"] == 1
    case = client.get(f"/api/test-suites/{root}").json()["cases"][0]
    assert case["archived"] is True and case["status"] == "PASS"
    assert case["id"] == "CT001" and case["revision"] == 1
    group = next(value for value in client.get("/api/test-suites").json() if value["id"] == root)
    assert group["case_count"] == 0 and group["archived_count"] == 1 and group["initial_count"] == 1
    assert app.state.repository.get_exploration(root) == original
    assert service.report_snapshot(root, "initial")["scenarios"] == snapshot
    assert service.report_snapshot(root)["scenarios"][0]["id"] == "CT001"
    restored = change(client, root, "CT001", False, 1)
    assert restored.status_code == 200 and restored.json()["catalog_revision"] == 2
    case = client.get(f"/api/test-suites/{root}").json()["cases"][0]
    assert case["archived"] is False and case["status"] == "PASS"
    assert not service.tasks and not app.state.scenario_rerun_service.tasks


def test_archived_manual_is_not_plannable_and_an_existing_draft_is_preserved_but_blocked(runtime):
    app, client, root = runtime
    case = new_manual(client, root)
    plan = create_plan(client, root, [case["id"]])
    before = deepcopy(app.state.test_suite_service.store.get(plan["id"]))
    assert change(client, root, case["id"]).status_code == 200
    assert app.state.test_suite_service.store.get(plan["id"]) == before
    response = client.post(f"/api/test-suites/{root}/plans", json={"name": "Novo", "items": [{"case_id": case["id"]}]})
    assert response.status_code == 409 and "arquivado" in response.json()["detail"]
    validation = client.post(f"/api/test-suites/rounds/{plan['id']}/validate").json()
    assert validation["available"] is False and validation["items"][0]["code"] == "archived"
    assert client.post(f"/api/test-suites/rounds/{plan['id']}/start", json={"confirm": True, "expected_revision": 1}).status_code == 409
    assert app.state.test_suite_service.store.get(plan["id"]) == before
    assert change(client, root, case["id"], False, 1).status_code == 200
    assert client.post(f"/api/test-suites/rounds/{plan['id']}/validate").json()["available"] is True
    newer = new_manual(client, root)
    assert newer["id"] != case["id"]
    assert not app.state.test_suite_service.tasks and not app.state.scenario_rerun_service.tasks


def test_archive_conflicts_unknown_cases_and_untrusted_origins_do_not_change_state(runtime):
    app, client, root = runtime
    assert change(client, root, "CT001", headers={"Origin": "https://untrusted.test"}).status_code == 403
    assert app.state.test_suite_service.store.catalog_states(root) == {}
    assert change(client, root, "CT999").status_code == 404
    assert change(client, root, "CT001").status_code == 200
    assert change(client, root, "CT001", False, 0).status_code == 409
    assert app.state.test_suite_service.store.catalog_states(root)["CT001"]["archived"] is True
    # Another repository instance sees the state, not an in-memory preference.
    from autojev.adapters.execution_plans import ExecutionPlanStore
    assert ExecutionPlanStore(app.state.repository).catalog_states(root)["CT001"]["catalog_revision"] == 1


@pytest.mark.parametrize("body", [{}, {"archived": "true", "expected_revision": 0},
                                 {"archived": True, "expected_revision": True},
                                 {"archived": True, "expected_revision": -1}])
def test_archive_requires_strict_explicit_input(runtime, body):
    app, client, root = runtime
    response = client.patch(f"/api/test-suites/{root}/cases/CT001/catalog-state", json=body)
    assert response.status_code == 422
    assert app.state.test_suite_service.store.catalog_states(root) == {}


def test_active_execution_prevents_archiving_but_finishing_does_not_erase_history(runtime):
    app, client, root = runtime
    case = new_manual(client, root)
    plan = create_plan(client, root, [case["id"]])
    assert client.post(f"/api/test-suites/rounds/{plan['id']}/start", json={"confirm": True, "expected_revision": 1}).status_code == 202
    current = round_state(client, plan["id"], "waiting_manual")
    assert change(client, root, case["id"]).status_code == 409
    assert client.post(f"/api/test-suites/rounds/{plan['id']}/cases/{case['id']}/result", json={"expected_revision": current["revision"], "status": "PASS", "actual_result": "Resultado observado no teste isolado"}).status_code == 200
    before = deepcopy(round_state(client, plan["id"], "completed"))
    assert change(client, root, case["id"]).status_code == 200
    assert client.get(f"/api/test-suites/rounds/{plan['id']}").json() == before
    exported = app.state.test_suite_service.report_snapshot(root, "round", plan["id"])
    assert exported["scenarios"][0]["status"] == "PASS"


def test_archiving_does_not_share_state_between_independent_bases(runtime):
    app, client, root = runtime
    from autojev.domain.models import (
        ExplorationOptions,
        ExplorationRequest,
        ModelSelection,
    )
    from autojev.domain.models import TestScenario as Scenario
    repo = app.state.repository
    other = repo.create_exploration(ExplorationRequest("https://shop.test", ModelSelection("local", "agent"), ModelSelection("local", "aux"), ExplorationOptions()))
    repo.add_scenario(other, 1, Scenario(**repo.get_exploration(root)["scenarios"][0]))
    repo.update_exploration(other, status="completed")
    assert change(client, root, "CT001").status_code == 200
    assert client.get(f"/api/test-suites/{other}").json()["cases"][0]["archived"] is False
    assert change(client, other, "CT002").status_code == 404
