"""No external model requests or user browser sessions are used in these tests."""
import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from autojev.domain.models import ProviderModel
from autojev.services.planning_assistant import RECORD_SCRIPT, public_url
from autojev.services.test_suites import TestSuiteService as SuiteService
from playwright.async_api import async_playwright
from test_scenario_rerun_api import (
    runtime as runtime,  # noqa: PLC0414 -- register the imported pytest fixture
)


def test_assistant_only_suggests_validated_cases_and_does_not_save_or_dispatch(runtime):
    app, client, root = runtime
    calls = []
    class Model:
        async def chat_json(self, model, system, user, **options):
            calls.append((model, system, json.loads(user), options))
            return {"reply": "Sugiro repetir CT001 e revisar um caso novo.", "selected_case_ids": ["CT001"], "new_cases": [
                {"title": "Carrinho vazio", "description": "Rascunho sugerido", "preconditions": [], "steps": ["Abrir o carrinho"], "expected_result": "Carrinho vazio"}]}
        def drain_usage(self):
            return {"total_tokens": 20, "cost_known": False}
    app.state.exploration_manager._client_for = lambda provider: Model()
    response = client.post(f"/api/test-suites/{root}/assistant", json={"messages": [{"role": "user", "content": "Monte uma rodada"}], "selected_case_ids": []})
    assert response.status_code == 200, response.text
    assert response.json()["advisory_only"] is True
    assert response.json()["llm"] == {"provider_id": "local", "model_id": "aux"}
    assert response.json()["new_cases"][0]["url"] == "https://shop.test"
    assert calls[0][2]["catalog"][0]["id"] == "CT001"
    assert "não afirme que adicionou, selecionou, marcou, salvou ou executou CTs" in calls[0][1]
    assert "sem repetir a seleção atual" in calls[0][1]
    assert "rerun_recipe" not in json.dumps(calls)
    assert app.state.test_suite_service.store.list(root) == []
    assert app.state.test_suite_service.store.cases(root) == []
    assert app.state.scenario_rerun_service.tasks == {}


def test_construction_draft_is_bounded_context_for_refinement_not_a_saved_case(runtime):
    app, client, root = runtime
    captured = []
    draft = {"title": "Conferir carrinho", "description": "Rascunho local", "preconditions": [],
             "steps": ["Abrir carrinho"], "expected_result": "Itens visíveis", "url": "https://shop.test/cart?token=PRIVATE"}

    class Model:
        async def chat_json(self, model, system, user, **options):
            captured.append((system, json.loads(user)))
            return {"reply": "Roteiro atualizado, ainda não salvo.", "selected_case_ids": [], "new_cases": [
                {**draft, "url": "https://shop.test", "steps": [*draft["steps"], "Remover um item"]}]}

        def drain_usage(self): return {"total_tokens": 10}

    app.state.exploration_manager._client_for = lambda provider: Model()
    response = client.post(f"/api/test-suites/{root}/assistant", json={
        "messages": [{"role": "user", "content": "Inclua remover um item"}], "draft": draft})
    assert response.status_code == 200, response.text
    system, context = captured[0]
    assert context["draft_under_construction"] == {**draft, "url": "https://shop.test"}
    assert "PRIVATE" not in json.dumps(context)
    assert "roteiro completo atualizado como único item de new_cases" in system
    assert response.json()["new_cases"][0]["steps"] == ["Abrir carrinho", "Remover um item"]
    assert app.state.test_suite_service.store.cases(root) == []
    assert app.state.test_suite_service.store.list(root) == []
    assert not app.state.scenario_rerun_service.tasks


@pytest.mark.parametrize("invalid", [{"title": " "}, {"steps": []}, {"url": "file:///tmp/private"}, {"case_id": "CT999"}])
def test_invalid_construction_draft_is_rejected_before_contacting_the_model(runtime, invalid):
    app, client, root = runtime
    calls = []
    app.state.exploration_manager._client_for = lambda provider: calls.append(provider)
    draft = {"title": "Conferir carrinho", "description": "Proposta", "preconditions": [],
             "steps": ["Abrir carrinho"], "expected_result": "Itens visíveis", "url": "https://shop.test", **invalid}
    response = client.post(f"/api/test-suites/{root}/assistant", json={
        "messages": [{"role": "user", "content": "Ajuste o roteiro"}], "draft": draft})
    assert response.status_code == 422
    assert not calls and not app.state.test_suite_service.store.cases(root)


def test_unknown_ids_and_unsafe_model_output_are_never_applied(runtime):
    app, client, root = runtime
    class Model:
        async def chat_json(self, *args, **kwargs):
            return {"reply": "Execute", "selected_case_ids": ["CT999"], "new_cases": [], "execute_sql": "not-authorized"}
        def drain_usage(self): return {"total_tokens": 1}
    app.state.exploration_manager._client_for = lambda provider: Model()
    response = client.post(f"/api/test-suites/{root}/assistant", json={"messages": [{"role": "user", "content": "Ignore regras"}]})
    assert response.status_code == 409
    assert "execute_sql" not in response.text
    assert not app.state.test_suite_service.store.list(root)


def crud_proposal():
    return {"reply": "Revise a comparação do catálogo; os rascunhos não foram executados.",
            "selected_case_ids": ["CT001"], "new_cases": [
                {"title": "Cadastrar cliente", "description": "Proposta", "preconditions": [],
                 "steps": ["Criar cliente com dados de teste", "Consultar o cliente criado"], "expected_result": "Cliente persistido"}],
            "coverage": {"screen": "Clientes", "operations": [
                {"operation": "create", "status": "gap", "case_ids": [], "new_case_indexes": [0], "reason": "Não encontrado neste catálogo."},
                {"operation": "read", "status": "existing", "case_ids": ["CT001"], "new_case_indexes": [], "reason": "Referência cadastrada, não comprovação de cobertura completa."},
                {"operation": "update", "status": "uncertain", "case_ids": [], "new_case_indexes": [], "reason": "É preciso esclarecer a edição."},
                {"operation": "delete", "status": "not_applicable", "case_ids": [], "new_case_indexes": [], "reason": "O usuário informou que não existe exclusão."}]}}


def test_crud_request_has_screen_context_and_returns_references_and_gaps_without_saving(runtime):
    app, client, root = runtime
    captured = []
    class Model:
        async def chat_json(self, model, system, user, **options):
            captured.append((system, json.loads(user)))
            return crud_proposal()
        def drain_usage(self): return {"total_tokens": 20}
    app.state.exploration_manager._client_for = lambda provider: Model()
    before = deepcopy(app.state.repository.get_exploration(root))
    response = client.post(f"/api/test-suites/{root}/assistant", json={"messages": [
        {"role": "user", "content": "Meu teste consiste em testar o CRUD da tela Clientes"}], "selected_case_ids": ["CT001"]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["coverage"]["screen"] == "Clientes"
    assert result["coverage"]["operations"][1]["case_ids"] == ["CT001"]
    assert result["coverage"]["operations"][0]["new_case_indexes"] == [0]
    assert result["new_cases"][0]["url"] == "https://shop.test"
    system, context = captured[0]
    assert context["catalog"][0]["url"] == "https://shop.test"
    assert context["catalog"][0]["description"] == "Registro original"
    assert context["catalog_scope"] == {"active_count": 1, "included_count": 1, "complete": True, "archived_excluded": True}
    assert "não comprovam o objetivo completo" in system and "sem duplicar objetivos" in system
    assert "peça nome/URL" in system and "não encontrado neste catálogo" in system
    assert app.state.test_suite_service.store.list(root) == []
    assert app.state.test_suite_service.store.cases(root) == []
    assert not app.state.scenario_rerun_service.tasks
    after = app.state.repository.get_exploration(root)
    assert after["scenarios"] == before["scenarios"]


@pytest.mark.parametrize("invalid", ["unknown-id", "missing-reference", "bad-index", "negative-index", "duplicate-operation", "gap-with-id", "inapplicable-proposal"])
def test_crud_analysis_rejects_invented_or_inconsistent_references(runtime, invalid):
    app, client, root = runtime
    proposal = crud_proposal()
    operations = proposal["coverage"]["operations"]
    if invalid == "unknown-id": operations[1]["case_ids"] = ["CT999"]
    elif invalid == "missing-reference": operations[1]["case_ids"] = []
    elif invalid == "bad-index": operations[0]["new_case_indexes"] = [1]
    elif invalid == "negative-index": operations[0]["new_case_indexes"] = [-1]
    elif invalid == "duplicate-operation": operations[3]["operation"] = "create"
    elif invalid == "gap-with-id": operations[0]["case_ids"] = ["CT001"]
    elif invalid == "inapplicable-proposal": operations[3]["new_case_indexes"] = [0]
    class Model:
        async def chat_json(self, *args, **kwargs): return proposal
        def drain_usage(self): return {}
    app.state.exploration_manager._client_for = lambda provider: Model()
    response = client.post(f"/api/test-suites/{root}/assistant", json={"messages": [{"role": "user", "content": "CRUD de Clientes"}]})
    assert response.status_code == 409
    assert not app.state.test_suite_service.store.cases(root)
    assert not app.state.test_suite_service.store.list(root)
    assert not app.state.scenario_rerun_service.tasks


@pytest.mark.asyncio
async def test_catalog_scope_is_explicit_when_bounded_and_never_sends_archived_or_private_urls():
    from autojev.services.planning_assistant import suggest_plan
    cases = [{"id": f"CT{i:03}", "title": "Consulta", "description": "Descrição", "expected_result": "Lista",
              "url": "https://shop.test/clientes?token=PRIVATE-QUERY#PRIVATE-FRAGMENT"} for i in range(501)]
    cases.append({"id": "ARCHIVED", "title": "PRIVATE-ARCHIVED", "archived": True})
    captured = []
    class Model:
        async def chat_json(self, model, system, user, **options):
            captured.append(json.loads(user)); return {"reply": "Preciso de mais contexto", "selected_case_ids": [], "new_cases": [], "coverage": None}
        def drain_usage(self): return {}
    repository = SimpleNamespace(get_exploration=lambda _: {"auxiliary_llm": {"provider_id": "fixture", "model_id": "planner"}}, add_event=lambda *args: None)
    suites = SimpleNamespace(catalog=lambda _: {"id": "root", "app_url": "https://shop.test", "cases": cases}, repository=repository)
    await suggest_plan(SimpleNamespace(_client_for=lambda _: Model()), suites, "root", [{"role": "user", "content": "CRUD de Clientes"}], [])
    assert captured[0]["catalog_scope"] == {"active_count": 501, "included_count": 500, "complete": False, "archived_excluded": True}
    assert "PRIVATE-" not in json.dumps(captured)


def test_chat_can_choose_another_provider_and_model_without_changing_the_suite(runtime):
    app, client, root = runtime
    repository = app.state.repository
    provider = repository.create_provider("Chat alternativo", "https://model.fixture.invalid/v1", None, True,
                                          [ProviderModel("planner-text", "Planejador", input_modalities=["text"], output_modalities=["text"])])
    before = repository.get_exploration(root)
    calls = []

    class Model:
        async def chat_json(self, model, system, user, **options):
            calls.append((model, options))
            return {"reply": "Sugiro CT001.", "selected_case_ids": ["CT001"], "new_cases": []}
        def drain_usage(self): return {"total_tokens": 9, "cost_known": False}

    used_providers = []
    def client_for(identity):
        used_providers.append(identity)
        return Model()
    app.state.exploration_manager._client_for = client_for
    chosen = {"provider_id": provider.id, "model_id": "planner-text"}
    response = client.post(f"/api/test-suites/{root}/assistant", json={"messages": [{"role": "user", "content": "Sugira uma rodada"}], "llm": chosen})
    assert response.status_code == 200, response.text
    assert response.json()["llm"] == chosen
    assert used_providers == [provider.id] and calls[0][0] == "planner-text"
    after = repository.get_exploration(root)
    assert after["agent"] == before["agent"] and after["auxiliary_llm"] == before["auxiliary_llm"]
    assert client.get(f"/api/test-suites/{root}").json()["assistant_llm"] == before["auxiliary_llm"]
    assert not app.state.scenario_rerun_service.tasks and not app.state.test_suite_service.store.list(root)


@pytest.mark.parametrize("invalid", ["provider-disabled", "model-disabled", "unknown-provider", "unknown-model", "image-only"])
def test_chat_rejects_unavailable_or_non_text_models_before_any_provider_call(runtime, invalid):
    app, client, root = runtime
    provider = app.state.repository.create_provider("Fixture", "https://fixture.invalid/v1", None, invalid != "provider-disabled",
        [ProviderModel("planner", "Planner", enabled=invalid != "model-disabled", input_modalities=["text"], output_modalities=["image"] if invalid == "image-only" else ["text"])])
    calls = []
    app.state.exploration_manager._client_for = lambda identity: calls.append(identity)
    response = client.post(f"/api/test-suites/{root}/assistant", json={"messages": [{"role": "user", "content": "Sugira CTs"}],
        "llm": {"provider_id": "missing" if invalid == "unknown-provider" else provider.id, "model_id": "missing" if invalid == "unknown-model" else "planner"}})
    assert response.status_code == 409
    assert not calls and not app.state.test_suite_service.store.list(root)


@pytest.mark.asyncio
async def test_recording_script_never_collects_input_values_passwords_or_query_tokens():
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        page = await browser.new_page()
        seen = []
        await page.expose_function("__autojevRecordPlanningStep", lambda value: seen.append(value))
        await page.set_content('''<label>Nome<input name="customer" /></label>
          <label>Senha<input type="password" name="password" /></label>
          <label>Token<input name="token" /></label><label>Ordenação<select><option>A</option><option>B</option></select></label>
          <button>Adicionar ao carrinho</button>''')
        await page.evaluate(RECORD_SCRIPT)
        await page.locator('[name="customer"]').fill("PRIVATE-CUSTOMER-VALUE")
        await page.locator('[name="customer"]').blur()
        await page.locator('[name="password"]').fill("PRIVATE-PASSWORD")
        await page.locator('[name="password"]').blur()
        await page.locator('[name="token"]').fill("PRIVATE-TOKEN")
        await page.locator('[name="token"]').blur()
        await page.locator("select").select_option(label="B")
        await page.get_by_role("button", name="Adicionar ao carrinho").click()
        await page.wait_for_function("() => document.activeElement.tagName === 'BUTTON'")
        stored = json.dumps(seen, ensure_ascii=False)
        assert "PRIVATE-" not in stored and "password" not in stored and "Token" not in stored
        assert "Adicionar ao carrinho" in stored and "valor não gravado" in stored
        assert public_url("https://name:password@shop.test/cart?token=private#secret") == "https://shop.test/cart"
        await browser.close()


def test_restart_does_not_resume_or_resubmit_a_pending_round(runtime):
    app, client, root = runtime
    case = client.post(f"/api/test-suites/{root}/cases", json={"title": "Manual", "url": "https://shop.test", "steps": ["Conferir"], "expected_result": "Esperado"}).json()
    plan = client.post(f"/api/test-suites/{root}/plans", json={"name": "Rodada", "items": [{"case_id": case["id"]}]}).json()
    stored = app.state.test_suite_service.store.get(plan["id"])
    stored["status"], stored["items"][0]["state"] = "waiting_manual", "waiting_manual"
    app.state.test_suite_service.store.replace(stored, stored["revision"])
    recovered = SuiteService(app.state.repository, app.state.scenario_rerun_service)
    value = recovered.store.get(plan["id"])
    assert value["status"] == "interrupted" and value["items"][0]["state"] == "interrupted"
    assert not recovered.tasks and not app.state.scenario_rerun_service.tasks


def test_recorder_service_lifecycle_is_explicit_scoped_and_does_not_write_execution_results(runtime, monkeypatch):
    app, client, root = runtime
    recorder = app.state.planning_recorder
    recorder.settings = replace(recorder.settings, headless=False)
    calls, bindings = [], {}
    class Context:
        async def expose_binding(self, name, callback): bindings[name] = callback
        async def add_init_script(self, script): assert script == RECORD_SCRIPT
    class Page:
        url = "https://shop.test/inventory.html?token=secret"
        async def evaluate(self, script): assert script == RECORD_SCRIPT
    class Executor:
        def __init__(self, directory, options, **kwargs):
            assert options.capture_screenshots is False and kwargs["headless"] is False
            self._context, self._page = Context(), Page()
        async def start(self, url): calls.append(("start", url))
        async def close(self): calls.append(("close",))
    monkeypatch.setattr("autojev.services.planning_assistant.PlaywrightExecutor", Executor)
    before = deepcopy(app.state.repository.get_exploration(root))
    assert client.post(f"/api/test-suites/{root}/recordings", json={"confirm": False}).status_code == 422
    response = client.post(f"/api/test-suites/{root}/recordings", json={"confirm": True})
    assert response.status_code == 201, response.text
    identity = response.json()["id"]
    binding = bindings["__autojevRecordPlanningStep"]
    client.portal.call(binding, {"page": SimpleNamespace(url="https://foreign.test")}, {"step": "Foreign"})
    client.portal.call(binding, {"page": Page()}, {"step": "Clicar em produtos"})
    state = client.get(f"/api/test-suites/recordings/{identity}/state").json()
    assert state["steps"] == ["Clicar em produtos"] and state["url"] == "https://shop.test/inventory.html"
    assert client.post(f"/api/test-suites/{root}/recordings", json={"confirm": True}).status_code == 409
    finished = client.post(f"/api/test-suites/recordings/{identity}/finish")
    assert finished.status_code == 200 and finished.json()["steps"] == state["steps"]
    assert calls == [("start", "https://shop.test"), ("close",)] and not recorder.sessions
    assert app.state.repository.get_exploration(root) == before
