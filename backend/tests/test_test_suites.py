"""Suite/round contracts using isolated SQLite and no real browser or model."""
from copy import deepcopy

import pytest

from test_scenario_rerun_api import runtime
from autojev.domain.models import ExplorationRequest, ExplorationOptions, ModelSelection, TestScenario as StoredScenario, ScenarioStatus


def new_manual(client, root, title="Conferir carrinho vazio"):
    response = client.post(f"/api/test-suites/{root}/cases", json={"title": title, "url": "https://shop.test", "description": "Manual",
        "steps": ["Abrir o carrinho", "Conferir o estado vazio"], "preconditions": [], "expected_result": "Carrinho vazio"})
    assert response.status_code == 201, response.text
    return response.json()


def create_plan(client, root, ids):
    response = client.post(f"/api/test-suites/{root}/plans", json={"name": "Regressão do carrinho", "items": [{"case_id": value} for value in ids]})
    assert response.status_code == 201, response.text
    return response.json()


def test_catalog_flows_match_export_and_are_read_only_for_manual_and_original_cts(runtime):
    from autojev.services.functional_flows import classify_case_flows
    from test_report_premium import data
    app, client, root = runtime
    created = new_manual(client, root, "Criar cliente")
    complementary = new_manual(client, root, "Preencher campo nome")
    # Keep this fixture's steps about field interaction rather than its default cart check.
    store = app.state.test_suite_service.store
    for item in [created, complementary]:
        value = {key: item[key] for key in ("title", "description", "preconditions", "steps", "expected_result", "url")}
        value["steps"] = ["Preencher campo nome"]
        store.save_case(root, value, [], item["id"], item["revision"])
    before = deepcopy(app.state.repository.get_exploration(root))
    stored = deepcopy(store.cases(root))
    response = client.get(f"/api/test-suites/{root}")
    assert response.status_code == 200, response.text
    catalog = {case["id"]: case for case in response.json()["cases"]}
    assert catalog["CT001"]["flow_classification"] == classify_case_flows(before["scenarios"][0])
    assert catalog[created["id"]]["flow_classification"]["memberships"][0]["name"] == "Cadastrar cliente"
    assert catalog[complementary["id"]]["flow_classification"]["memberships"] == []
    document = client.get(f"/api/test-suites/{root}/report?scope=consolidated")
    assert document.status_code == 200, document.text
    exported = {case["id"]: case for case in data(document.text)["cases"]}
    for identity in catalog:
        assert catalog[identity]["flow_classification"] == exported[identity]["flow_classification"]
    assert {flow["name"] for flow in response.json()["functional_flows"]} == {flow["name"] for flow in data(document.text)["functional_flows"]}
    assert app.state.repository.get_exploration(root) == before
    assert store.cases(root) == stored
    assert app.state.scenario_rerun_service.tasks == {}


def round_state(client, identity, status=None):
    for _ in range(80):
        response = client.get("/api/test-suites/rounds/" + identity)
        assert response.status_code == 200, response.text
        value = response.json()
        if status is None or value["status"] == status:
            return value
        import time
        time.sleep(.01)
    raise AssertionError(value)


def test_legacy_reruns_group_by_parent_not_host_and_original_records_are_unchanged(runtime):
    app, client, root = runtime
    repo = app.state.repository
    request = ExplorationRequest("https://shop.test", ModelSelection("local", "agent"), ModelSelection("local", "aux"), ExplorationOptions())
    independent = repo.create_exploration(request)
    child = repo.create_exploration(request)
    repo.add_scenario(child, 1, StoredScenario(**repo.get_exploration(root)["scenarios"][0]))
    repo.add_event(child, "info", "scenario_rerun_source", "Stored link", {"source_exploration_id": root, "source_scenario_id": "CT001"})
    repo.update_exploration(child, status="completed")
    original = deepcopy(repo.get_exploration(root))
    response = client.get("/api/test-suites")
    assert response.status_code == 200, response.text
    groups = {item["id"]: item for item in response.json()}
    assert set(groups) == {root, independent}
    assert groups[root]["initial_count"] == 1
    assert [value["id"] for value in groups[root]["legacy_runs"]] == [child]
    assert repo.get_exploration(root) == original


def test_persistent_draft_validation_versions_and_no_implicit_browser_work(runtime):
    app, client, root = runtime
    first, second = new_manual(client, root), new_manual(client, root, "Segundo CT")
    assert [first["id"], second["id"]] == ["CT002", "CT003"]
    original = deepcopy(app.state.repository.get_exploration(root))
    plan = create_plan(client, root, [first["id"], second["id"]])
    assert plan["status"] == "draft" and plan["revision"] == 1
    assert app.state.scenario_rerun_service.tasks == {}
    validation = client.post(f"/api/test-suites/rounds/{plan['id']}/validate").json()
    assert validation["available"] is True
    edited = client.put(f"/api/test-suites/{root}/plans/{plan['id']}", json={"expected_revision": 1, "name": "Nova ordem", "items": [{"case_id": second["id"]}, {"case_id": first["id"]}]})
    assert edited.status_code == 200 and edited.json()["revision"] == 2
    stale = client.put(f"/api/test-suites/{root}/plans/{plan['id']}", json={"expected_revision": 1, "name": "Obsoleto", "items": [{"case_id": first["id"]}]})
    assert stale.status_code == 409
    assert client.post(f"/api/test-suites/rounds/{plan['id']}/start", json={"confirm": True, "expected_revision": 1}).status_code == 409
    assert app.state.repository.get_exploration(root) == original


def test_manual_round_is_sequential_explicit_idempotent_and_preserves_snapshots(runtime):
    app, client, root = runtime
    first, second = new_manual(client, root), new_manual(client, root, "Outro CT")
    plan = create_plan(client, root, [first["id"], second["id"]])
    start = f"/api/test-suites/rounds/{plan['id']}/start"
    assert client.post(start, json={"confirm": False, "expected_revision": 1}).status_code == 422
    assert client.post(start, json={"confirm": True, "expected_revision": 1}).status_code == 202
    current = round_state(client, plan["id"], "waiting_manual")
    assert [item["state"] for item in current["items"]] == ["waiting_manual", "queued"]
    assert client.post(start, json={"confirm": True, "expected_revision": 1}).json()["id"] == plan["id"]
    assert len(app.state.test_suite_service.store.list(root)) == 1
    result = f"/api/test-suites/rounds/{plan['id']}/cases/{first['id']}/result"
    assert client.post(result, json={"expected_revision": current["revision"], "status": "PASS", "actual_result": "Carrinho vazio conferido"}).status_code == 200
    current = round_state(client, plan["id"], "waiting_manual")
    assert current["items"][0]["result"]["status"] == "PASS" and current["items"][1]["state"] == "waiting_manual"
    assert client.post(result, json={"expected_revision": current["revision"], "status": "FAIL", "actual_result": "Não pode alterar resultado"}).status_code == 409
    assert client.post(f"/api/test-suites/rounds/{plan['id']}/stop").status_code == 200
    final = round_state(client, plan["id"], "cancelled")
    assert final["items"][0]["result"]["status"] == "PASS"
    assert final["items"][1]["result"] is None and final["items"][1]["state"] == "cancelled"
    assert app.state.scenario_rerun_service.tasks == {}
    initial = app.state.test_suite_service.report_snapshot(root, "initial")
    consolidated = app.state.test_suite_service.report_snapshot(root)
    selected = app.state.test_suite_service.report_snapshot(root, "round", plan["id"])
    assert len(initial["scenarios"]) == 1 and len(consolidated["scenarios"]) == 3
    assert [case["id"] for case in selected["scenarios"]] == [first["id"], second["id"]]
    assert selected["scenarios"][1]["status"] == "SKIPPED"
    assert "Não executado" in selected["scenarios"][1]["actual_result"]


def test_auto_round_uses_individual_executor_once_and_retains_real_evidence_owner(runtime):
    app, client, root = runtime
    service = app.state.scenario_rerun_service
    service.preflight = lambda run, case: {"available": True, "timeline_count": 2, "action_index": 1}
    calls = []
    def submit(run, case, **options):
        calls.append((run, case, options))
        repo = app.state.repository
        child = repo.create_exploration(ExplorationRequest("https://shop.test", ModelSelection("local", "agent"), ModelSelection("local", "aux"), ExplorationOptions()))
        repo.add_scenario(child, 1, StoredScenario(**repo.get_exploration(root)["scenarios"][0]))
        repo.add_event(child, "info", "scenario_rerun_source", "Link", {"source_exploration_id": run, "source_scenario_id": case})
        repo.update_exploration(child, status="completed")
        return child
    service.submit = submit
    original = deepcopy(app.state.repository.get_exploration(root))
    plan = create_plan(client, root, ["CT001"])
    assert client.post(f"/api/test-suites/rounds/{plan['id']}/start", json={"confirm": True, "expected_revision": 1}).status_code == 202
    final = round_state(client, plan["id"], "completed")
    child = final["items"][0]["execution_id"]
    assert calls == [(root, "CT001", {"capture_plan": None})]
    assert final["items"][0]["result"]["evidence_exploration_id"] == child
    assert app.state.test_suite_service.store.execution_context(child) == {"root_id": root, "round_id": plan["id"], "case_id": "CT001"}
    assert client.post(f"/api/test-suites/rounds/{plan['id']}/start", json={"confirm": True, "expected_revision": 1}).status_code == 202
    assert len(calls) == 1
    assert app.state.repository.get_exploration(root) == original


@pytest.mark.parametrize("suffix,body", [("plans", {"name": "Duplicado", "items": [{"case_id": "CT001"}, {"case_id": "CT001"}]}),
    ("plans", {"name": "Inexistente", "items": [{"case_id": "CT999"}]}),
    ("cases", {"title": "Sem dados"})])
def test_invalid_or_unknown_planning_inputs_never_dispatch(runtime, suffix, body):
    app, client, root = runtime
    assert client.post(f"/api/test-suites/{root}/{suffix}", json=body).status_code in {409, 422}
    assert not app.state.scenario_rerun_service.tasks


def test_recording_and_discovery_and_assistant_require_trusted_application_origin(runtime):
    app, client, root = runtime
    for suffix, body in [("recordings", {"confirm": True}), ("discover", {"confirm": True, "focus": "Carrinho"}),
                         ("assistant", {"messages": [{"role": "user", "content": "Quero novos CTs"}]})]:
        response = client.post(f"/api/test-suites/{root}/{suffix}", json=body, headers={"Origin": "https://untrusted.test"})
        assert response.status_code == 403
    assert not app.state.planning_recorder.sessions


def test_export_is_self_contained_scope_labelled_and_does_not_modify_initial_report(runtime):
    app, client, root = runtime
    case = new_manual(client, root)
    plan = create_plan(client, root, [case["id"]])
    before = deepcopy(app.state.repository.get_exploration(root))
    response = client.get(f"/api/test-suites/{root}/report?scope=round&round_id={plan['id']}")
    assert response.status_code == 200, response.text
    assert "Rodada selecionada" in response.text and "Regressão do carrinho" in response.text
    assert "Não executado nesta rodada" in response.text
    assert 'class="suite-report-context"' not in response.text
    assert '"scope":"round"' in response.text or '"scope": "round"' in response.text
    assert app.state.repository.get_exploration(root) == before


def test_older_case_version_stays_frozen_and_cannot_approve_the_new_version(runtime):
    app, client, root = runtime
    case = new_manual(client, root)
    plan = create_plan(client, root, [case["id"]])
    payload = {key: case[key] for key in ("title", "description", "steps", "preconditions", "expected_result", "url")}
    payload.update(title="Objetivo alterado", expected_result="Novo resultado esperado", expected_revision=1)
    assert client.put(f"/api/test-suites/{root}/cases/{case['id']}", json=payload).status_code == 200
    assert app.state.test_suite_service.store.get(plan["id"])["items"][0]["definition"]["title"] == case["title"]
    assert client.post(f"/api/test-suites/rounds/{plan['id']}/start", json={"confirm": True, "expected_revision": 1}).status_code == 202
    current = round_state(client, plan["id"], "waiting_manual")
    assert client.post(f"/api/test-suites/rounds/{plan['id']}/cases/{case['id']}/result", json={"expected_revision": current["revision"], "status": "PASS", "actual_result": "Versão original conferida"}).status_code == 200
    round_state(client, plan["id"], "completed")
    catalog = client.get(f"/api/test-suites/{root}").json()
    assert catalog["cases"][-1]["revision"] == 2 and catalog["cases"][-1]["status"] is None
    assert app.state.test_suite_service.report_snapshot(root)["scenarios"][-1]["status"] == "SKIPPED"


def test_one_live_round_limit_never_creates_a_second_execution(runtime):
    app, client, root = runtime
    case = new_manual(client, root)
    first, second = create_plan(client, root, [case["id"]]), create_plan(client, root, [case["id"]])
    assert client.post(f"/api/test-suites/rounds/{first['id']}/start", json={"confirm": True, "expected_revision": 1}).status_code == 202
    round_state(client, first["id"], "waiting_manual")
    assert client.post(f"/api/test-suites/rounds/{second['id']}/start", json={"confirm": True, "expected_revision": 1}).status_code == 409
    assert app.state.test_suite_service.store.get(second["id"])["status"] == "draft"
    assert not app.state.scenario_rerun_service.tasks


def test_report_handoff_resolves_stable_parent_ct_and_rejects_a_different_ct(runtime):
    app, client, root = runtime
    repo = app.state.repository
    original = deepcopy(repo.get_exploration(root)["scenarios"][0])
    original["id"], original["title"] = "CT147", "CT pai correto"
    repo.add_scenario(root, 2, StoredScenario(**original))
    child = repo.create_exploration(ExplorationRequest("https://shop.test", ModelSelection("local", "agent"), ModelSelection("local", "aux"), ExplorationOptions()))
    original["id"] = "CT001"
    repo.add_scenario(child, 1, StoredScenario(**original))
    repo.add_event(child, "info", "scenario_rerun_source", "Stored source", {"source_exploration_id": root, "source_scenario_id": "CT147"})
    repo.update_exploration(child, status="completed")
    response = client.get(f"/api/test-suites/{child}/case-context/CT001")
    assert response.status_code == 200, response.text
    assert response.json()["root_id"] == root and response.json()["case_id"] == "CT147"
    assert response.json()["reference"]["title"] == "CT pai correto"
    bad = client.post(f"/api/test-suites/{root}/plans", json={"name": "CT errado", "items": [{"case_id": "CT001", "source_exploration_id": child, "source_scenario_id": "CT001"}]})
    assert bad.status_code == 409


def test_discovery_is_explicit_bounded_grouped_and_imports_cases_once(runtime):
    app, client, root = runtime
    calls = []
    app.state.exploration_manager._client_for = lambda provider: object()
    app.state.exploration_manager.submit = lambda identity: calls.append(identity)
    assert client.post(f"/api/test-suites/{root}/discover", json={"confirm": False, "focus": "Carrinho"}).status_code == 422
    response = client.post(f"/api/test-suites/{root}/discover", json={"confirm": True, "focus": "Carrinho", "max_steps": 10})
    assert response.status_code == 202, response.text
    child = response.json()["id"]
    assert calls == [child]
    stored = app.state.repository.get_exploration(child)
    assert stored["options"]["max_steps"] == 10 and stored["options"]["max_duration_seconds"] == 300
    app.state.repository.add_scenario(child, 1, StoredScenario(**app.state.repository.get_exploration(root)["scenarios"][0]))
    assert client.post(f"/api/test-suites/{root}/import", json={"session_id": child, "case_id": "CT001"}).status_code == 409
    app.state.repository.update_exploration(child, status="completed")
    first = client.post(f"/api/test-suites/{root}/import", json={"session_id": child, "case_id": "CT001"})
    second = client.post(f"/api/test-suites/{root}/import", json={"session_id": child, "case_id": "CT001"})
    assert first.status_code == 201 and second.json()["id"] == first.json()["id"] == "CT002"
    catalog = client.get(f"/api/test-suites/{root}").json()
    assert len(catalog["cases"]) == 2 and catalog["cases"][-1]["mode"] == "automatic"
    assert len(client.get("/api/test-suites").json()) == 1


def test_all_151_original_cases_fit_in_one_saved_round_without_starting_anything(runtime):
    app, client, root = runtime
    repo = app.state.repository
    template = repo.get_exploration(root)["scenarios"][0]
    for index in range(2, 152):
        case = deepcopy(template)
        case["id"] = f"CT{index:03}"
        repo.add_scenario(root, index, StoredScenario(**case))
    before = deepcopy(repo.get_exploration(root))
    plan = create_plan(client, root, [f"CT{index:03}" for index in range(1, 152)])
    assert len(plan["items"]) == 151 and plan["status"] == "draft"
    assert not app.state.scenario_rerun_service.tasks and not app.state.test_suite_service.tasks
    assert repo.get_exploration(root) == before


def test_suite_queries_have_indexes_for_root_lists_and_execution_context(runtime):
    app, _, root = runtime
    with app.state.repository._connect() as connection:
        names = {row["name"] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type = 'index'")}
        assert {"idx_execution_plans_root", "idx_suite_sessions_root", "idx_one_live_execution_round"} <= names
        plans = list(connection.execute("EXPLAIN QUERY PLAN SELECT document_json FROM execution_plans WHERE root_id = ? ORDER BY created_at, id", (root,)))
        events = list(connection.execute("EXPLAIN QUERY PLAN SELECT payload_json FROM exploration_events WHERE exploration_id = ? AND type = 'execution_round_item' ORDER BY id DESC LIMIT 1", (root,)))
        assert any("idx_execution_plans_root" in row["detail"] for row in plans)
        assert any("idx_exploration_events" in row["detail"] for row in events)
