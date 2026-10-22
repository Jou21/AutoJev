"""Opt-in frontend QA; every API call is intercepted, including all mutations."""
import asyncio
import os
from urllib.parse import urlsplit

import pytest
from playwright.async_api import async_playwright, expect


def fixture_suite(count=2):
    def case(index):
        return dict(id=f"CT{index:03}", title="Ordenar produtos" if index == 1 else "Conferir carrinho", description="Caso registrado",
                    preconditions=["Estar autenticado"], steps=["Abrir a página", "Conferir o resultado"], expected_result="Resultado esperado",
                    url="https://fixture.invalid/inventory.html", mode="automatic", revision=1, status="FAIL" if index == 1 else "PASS",
                    source_exploration_id="suite-fixture", source_scenario_id=f"CT{index:03}", recorded_at="2026-10-19T12:00:00Z",
                    recorded_steps=[{"title": "Antes da ação", "kind": "observation"}, {"title": "Selecionar opção", "kind": "action"}])
    return dict(id="suite-fixture", app_url="https://fixture.invalid", created_at="2026-10-14T12:00:00Z", initial_count=count,
                cases=[case(index) for index in range(1, count + 1)], rounds=[], discoveries=[], assistant_model="fixture-model",
                assistant_llm={"provider_id": "fixture-provider", "model_id": "fixture-model"})


def fixture_models():
    return [{"id": "fixture-model", "provider_id": "fixture-provider", "provider_name": "Provedor do conjunto", "label": "Modelo do conjunto", "enabled": True},
            {"id": "alternate-model", "provider_id": "alternate-provider", "provider_name": "Outro provedor", "label": "Modelo alternativo", "enabled": True},
            {"id": "fixture-model", "provider_id": "alternate-provider", "provider_name": "Outro provedor", "label": "Mesmo ID em outro provedor", "enabled": True},
            {"id": "disabled-model", "provider_id": "fixture-provider", "provider_name": "Provedor do conjunto", "label": "Desativado", "enabled": False},
            {"id": "image-model", "provider_id": "fixture-provider", "provider_name": "Provedor do conjunto", "label": "Somente imagem", "output_modalities": ["image"]}]


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("AUTOJEV_FRONTEND_E2E") != "1", reason="Opt-in frontend QA with intercepted API")
@pytest.mark.parametrize("width", [1440, 390], ids=["desktop", "mobile"])
async def test_suite_planner_chat_authoring_and_explicit_confirmation(tmp_path, width):
    suite, plans, calls, errors = fixture_suite(), {}, [], []
    now = "2026-10-19T12:00:00Z"
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": width, "height": 1000})
        page.set_default_timeout(8000)
        page.on("pageerror", lambda error: errors.append(str(error)))

        async def api(route):
            request = route.request
            path = urlsplit(request.url).path.removeprefix("/api")
            if request.method == "OPTIONS":
                await route.fulfill(status=204, headers={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Allow-Methods": "GET,POST,PUT"})
                return
            calls.append((request.method, path))
            body = request.post_data_json if request.method in {"POST", "PUT"} else None
            response = []
            if path == "/models":
                response = fixture_models()
            elif path == "/test-suites":
                response = [{"id": suite["id"], "app_url": suite["app_url"], "created_at": now, "latest_activity": now, "initial_count": 2, "case_count": len(suite["cases"]), "active_count": 0,
                             "initial_status": "completed", "rounds": [], "legacy_runs": [{"id": "old-child", "count": 1, "status": "completed", "created_at": now}]}]
            elif path == "/test-suites/suite-fixture":
                response = suite
            elif path.endswith("/assistant"):
                assert body["llm"] == {"provider_id": "fixture-provider", "model_id": "fixture-model"}
                response = {"reply": "Sugiro repetir a ordenação e o carrinho. Revise também um caso novo para o carrinho vazio.", "selected_case_ids": ["CT001", "CT002"],
                            "new_cases": [{"title": "Carrinho vazio", "description": "Rascunho proposto", "steps": ["Abrir o carrinho", "Conferir que não há produtos"], "preconditions": [], "expected_result": "Carrinho vazio", "url": suite["app_url"]}],
                            "usage": {"total_tokens": 42, "cost_known": False}, "advisory_only": True}
            elif path == "/test-suites/suite-fixture/cases" and request.method == "POST":
                response = dict(body, id="CT003", mode="manual", revision=1)
                suite["cases"].append(response)
            elif path == "/test-suites/suite-fixture/plans" and request.method == "POST":
                items = []
                for selected in body["items"]:
                    case = next(case for case in suite["cases"] if case["id"] == selected["case_id"])
                    items.append(dict(selected, mode=case["mode"], definition={key: case[key] for key in ["title", "description", "steps", "preconditions", "expected_result", "url"]}, state="queued"))
                response = dict(id="plan-fixture", root_id=suite["id"], name=body["name"], items=items, status="draft", revision=1, created_at=now, updated_at=now, count=len(items), finished_count=0)
                plans[response["id"]] = response
                suite["rounds"] = [response]
            elif path == "/test-suites/rounds/plan-fixture/validate":
                response = {"revision": 1, "available": True, "effects": ["Cada CT exige preparação; nenhuma ação começa antes de confirmar."], "items": [{"case_id": item["case_id"], "available": True} for item in plans["plan-fixture"]["items"]]}
            elif path == "/test-suites/rounds/plan-fixture/start":
                assert body == {"confirm": True, "expected_revision": 1}
                plans["plan-fixture"]["status"] = "running"
                plans["plan-fixture"]["revision"] = 2
                response = plans["plan-fixture"]
            elif path == "/test-suites/rounds/plan-fixture":
                response = plans["plan-fixture"]
            elif request.method not in {"GET"}:
                raise AssertionError("Unexpected mutation: " + path)
            await route.fulfill(status=200, json=response, headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"})

        await page.route("**/api/**", api)
        try:
            await page.goto("http://localhost:3000/#/exploracoes")
            await expect(page.get_by_role("heading", name="Conjuntos de testes", exact=True)).to_be_visible()
            await expect(page.locator(".suite-group")).to_have_count(1)
            await page.get_by_role("button", name="Mostrar rodadas de fixture.invalid").click()
            await expect(page.get_by_role("button", name="Execução vinculada", exact=False)).to_be_visible()
            await page.get_by_role("button", name="Abrir conjunto", exact=False).click()
            await expect(page.locator(".suite-case-row")).to_have_count(2)
            await page.get_by_role("button", name="Planejar rodada", exact=True).click()
            await page.get_by_role("checkbox", name="Incluir CT001", exact=True).check()
            await page.get_by_role("textbox", name="Mensagem para o assistente", exact=True).fill("Quero repetir os CTs do carrinho e criar um teste novo")
            await page.get_by_role("button", name="Enviar", exact=True).click()
            await expect(page.locator(".suite-chat-message[data-role='assistant']")).to_contain_text("Sugiro repetir")
            assert not [path for method, path in calls if method == "POST" and path.endswith(("/cases", "/plans", "/start"))]
            await page.get_by_role("button", name="Adicionar 1 CT sugerido", exact=True).click()
            await expect(page.locator(".suite-selected-list > div")).to_have_count(2)
            await page.get_by_role("button", name="Carrinho vazio", exact=False).click()
            await expect(page.locator(".suite-case-form input").first).to_have_value("Carrinho vazio")
            await page.get_by_role("button", name="Salvar CT e adicionar à rodada", exact=False).click()
            await expect(page.locator(".suite-selected-list > div")).to_have_count(3)
            await page.get_by_role("textbox", name="Nome da rodada", exact=True).fill("Correções e carrinho vazio")
            await page.get_by_role("button", name="Revisar rodada", exact=True).click()
            execute = page.get_by_role("button", name="Confirmar e executar 3 CTs", exact=False)
            await expect(execute).to_be_disabled()
            assert not [path for method, path in calls if path.endswith("/start")]
            await page.screenshot(path=str(tmp_path / f"suite-planner-{width}.png"), full_page=True)
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            await page.get_by_role("checkbox", name="Revisei os CTs e autorizo", exact=False).check()
            await execute.click()
            await expect(page.locator(".suite-round-detail")).to_be_visible()
            await expect(page.locator(".suite-round-case")).to_have_count(3)
            await expect(page.locator(".suite-round-detail")).to_contain_text("Os outros CTs do conjunto não foram reexecutados aqui")
            assert [path for method, path in calls if path.endswith("/start")] == ["/test-suites/rounds/plan-fixture/start"]
            await page.screenshot(path=str(tmp_path / f"suite-round-{width}.png"), full_page=True)
            assert not errors
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("AUTOJEV_FRONTEND_E2E") != "1", reason="Opt-in frontend QA with intercepted API")
@pytest.mark.parametrize("width", [1440, 390], ids=["desktop", "mobile"])
async def test_direct_chat_addition_marks_checkbox_and_queue_without_execution(tmp_path, width):
    suite, calls, errors = fixture_suite(4), [], []
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": width, "height": 1000})
        page.set_default_timeout(8000)
        page.on("pageerror", lambda error: errors.append(str(error)))

        async def api(route):
            request = route.request
            path = urlsplit(request.url).path.removeprefix("/api")
            headers = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Allow-Methods": "GET,POST,PUT"}
            if request.method == "OPTIONS":
                await route.fulfill(status=204, headers=headers)
                return
            body = request.post_data_json if request.method in {"POST", "PUT"} else None
            calls.append((request.method, path, body))
            response = []
            if path == "/models":
                response = fixture_models()
            elif path == "/test-suites/suite-fixture":
                response = suite
            elif path.endswith("/assistant"):
                response = {"reply": "Sugiro CT003; revise antes de adicionar.", "selected_case_ids": ["CT001", "CT003", "CT003", "CT004"],
                            "new_cases": [], "usage": {"total_tokens": 42, "cost_known": False}}
            elif path.endswith("/plans") and request.method == "POST":
                response = {**body, "id": "draft-fixture", "revision": 1, "status": "draft"}
            elif request.method != "GET":
                await route.fulfill(status=500, json={"detail": "Unexpected mutation"}, headers=headers)
                return
            await route.fulfill(status=200, json=response, headers=headers)

        await page.route("**/api/**", api)
        try:
            await page.goto("http://localhost:3000/#/conjuntos/suite-fixture?ct=CT001")
            await expect(page.get_by_role("checkbox", name="Incluir CT001", exact=True)).to_be_checked()
            await page.get_by_role("button", name="Adicionar print na posição 1", exact=True).click()

            async def send(text):
                await page.get_by_role("textbox", name="Mensagem para o assistente", exact=True).fill(text)
                await page.get_by_role("button", name="Enviar", exact=True).click()

            await send("adicione o ct004 também na reexecução")
            await expect(page.get_by_role("checkbox", name="Incluir CT004", exact=True)).to_be_checked()
            await expect(page.locator(".suite-selected-list > div")).to_have_count(2)
            await expect(page.locator(".suite-selected-list > div").last).to_contain_text("CT004")
            await expect(page.locator(".suite-chat-message[data-role='assistant']").last).to_contain_text("CT004 foi adicionado ao rascunho")
            assert not [call for call in calls if call[0] != "GET"]

            await send("pode incluir CT4?")
            await expect(page.locator(".suite-chat-message[data-role='assistant']").last).to_contain_text("já estava selecionado")
            await expect(page.locator(".suite-selected-list > div")).to_have_count(2)
            await send("adicione CT003 e CT999")
            await expect(page.locator(".suite-chat-message[data-role='assistant']").last).to_contain_text("Nenhum CT foi adicionado")
            await expect(page.get_by_role("checkbox", name="Incluir CT003", exact=True)).not_to_be_checked()
            assert not [call for call in calls if call[0] != "GET"]

            await send("Quais CTs você sugere?")
            await expect(page.get_by_role("button", name="Adicionar 1 CT sugerido", exact=True)).to_be_visible()
            await expect(page.get_by_role("checkbox", name="Incluir CT003", exact=True)).not_to_be_checked()
            await page.get_by_role("button", name="Adicionar 1 CT sugerido", exact=True).click()
            await expect(page.get_by_role("checkbox", name="Incluir CT003", exact=True)).to_be_checked()
            await expect(page.locator(".suite-selected-list > div")).to_have_count(3)
            await expect(page.get_by_role("button", name="Adicionar 1 CT sugerido", exact=True)).to_have_count(0)
            assert not [call for call in calls if call[0] != "GET" and not call[1].endswith("/assistant")]
            await page.get_by_role("button", name="Guardar planejamento", exact=True).click()
            await expect(page.get_by_text("RASCUNHO SALVO", exact=False)).to_be_visible()
            saved = next(body for method, path, body in calls if path.endswith("/plans"))
            assert [item["case_id"] for item in saved["items"]] == ["CT001", "CT004", "CT003"]
            assert saved["items"][0]["capture_plan"]["checkpoints"][0]["gap"] == 1
            assert not [call for call in calls if call[1].endswith(("/start", "/cases", "/discover"))]
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            await page.screenshot(path=str(tmp_path / f"direct-chat-{width}.png"), full_page=True)
            assert not errors
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("AUTOJEV_FRONTEND_E2E") != "1", reason="Opt-in frontend QA with intercepted API")
@pytest.mark.parametrize("width", [1440, 390], ids=["desktop", "mobile"])
async def test_premium_chat_model_picker_keyboard_and_response_attribution(tmp_path, width):
    suite, calls, errors = fixture_suite(4), [], []
    received, release = asyncio.Event(), asyncio.Event()
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": width, "height": 1000})
        page.set_default_timeout(8000)
        page.on("pageerror", lambda error: errors.append(str(error)))

        async def api(route):
            request = route.request
            path = urlsplit(request.url).path.removeprefix("/api")
            headers = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Allow-Methods": "GET,POST,PUT"}
            if request.method == "OPTIONS":
                await route.fulfill(status=204, headers=headers)
                return
            response = []
            if path == "/models": response = fixture_models()
            elif path == "/test-suites/suite-fixture": response = suite
            elif path.endswith("/assistant"):
                body = request.post_data_json
                calls.append(body)
                received.set()
                await release.wait()
                response = {"reply": "**Plano sugerido**\n" + "Revise o CT `CT004` antes de adicionar.\n" * 12 + "<img src=x onerror=alert(1)>",
                            "selected_case_ids": ["CT004"], "new_cases": [], "usage": {"total_tokens": 42, "cost_known": False}, "llm": body["llm"]}
            elif request.method != "GET":
                raise AssertionError("Unexpected mutation: " + path)
            await route.fulfill(status=200, json=response, headers=headers)

        await page.route("**/api/**", api)
        try:
            await page.goto("http://localhost:3000/#/conjuntos/suite-fixture?ct=CT001")
            trigger = page.get_by_role("button", name="Modelo de IA do chat: Modelo do conjunto", exact=True)
            await expect(trigger).to_be_enabled()
            await expect(page.locator(".planning-chat-header")).to_have_count(0)
            await expect(page.get_by_text("Copiloto de testes", exact=True)).to_have_count(0)
            await expect(page.get_by_text("Ideias que viram bons testes", exact=True)).to_have_count(0)
            await expect(page.get_by_text("Só planejamento", exact=True)).to_have_count(0)
            await expect(page.locator(".suite-chat-compose .planning-chat-model-trigger")).to_have_count(1)
            await page.locator(".suite-chat").screenshot(path=str(tmp_path / f"premium-chat-welcome-{width}.png"))
            assert await page.evaluate("""() => {
                const chat = document.querySelector('.suite-chat-compose').getBoundingClientRect();
                const footer = document.querySelector('.suite-plan-footer').getBoundingClientRect();
                return chat.right <= footer.left || chat.left >= footer.right || chat.bottom <= footer.top || chat.top >= footer.bottom;
            }""")
            composer = page.get_by_role("textbox", name="Mensagem para o assistente", exact=True)
            await composer.fill("Rascunho que não deve ser enviado ao buscar um modelo")
            await trigger.click()
            search = page.get_by_role("textbox", name="Buscar modelos de IA", exact=True)
            await expect(search).to_be_focused()
            assert await page.evaluate("""() => {
                const menu = document.querySelector('.planning-chat-model-menu').getBoundingClientRect();
                const trigger = document.querySelector('.planning-chat-model-trigger').getBoundingClientRect();
                const chat = document.querySelector('.suite-chat').getBoundingClientRect();
                return menu.bottom <= trigger.top + 2 && menu.top >= chat.top && menu.left >= chat.left && menu.right <= chat.right;
            }""")
            await page.locator(".suite-chat").screenshot(path=str(tmp_path / f"premium-chat-picker-{width}.png"))
            await expect(page.locator(".planning-chat-model-options")).not_to_contain_text("Desativado")
            await expect(page.locator(".planning-chat-model-options")).not_to_contain_text("Somente imagem")
            await search.fill("alternativo")
            await search.press("Enter")
            await expect(composer).to_have_value("Rascunho que não deve ser enviado ao buscar um modelo")
            assert not calls
            await search.press("ArrowDown")
            await page.keyboard.press("Enter")
            await expect(page.get_by_role("dialog", name="Escolher modelo de IA", exact=True)).to_have_count(0)
            await expect(page.get_by_role("button", name="Modelo de IA do chat: Modelo alternativo", exact=True)).to_be_visible()

            await composer.fill("Sugira uma rodada")
            await composer.press("Shift+Enter")
            await composer.press("b")
            await expect(composer).to_have_value("Sugira uma rodada\nb")
            await composer.press("Enter")
            await asyncio.wait_for(received.wait(), timeout=8)
            await expect(page.get_by_role("status")).to_contain_text("Pensando na sua rodada")
            await expect(page.get_by_role("button", name="Enviar", exact=True)).to_be_disabled()
            await expect(page.get_by_role("button", name="Modelo de IA do chat: Modelo alternativo", exact=True)).to_be_disabled()
            assert calls[0]["llm"] == {"provider_id": "alternate-provider", "model_id": "alternate-model"}
            assert calls[0]["messages"] == [{"role": "user", "content": "Sugira uma rodada\nb"}]
            release.set()
            await expect(page.locator(".planning-chat-message-byline")).to_contain_text("Modelo alternativo")
            await expect(page.locator(".suite-chat-message strong").filter(has_text="Plano sugerido")).to_be_visible()
            await expect(page.locator(".suite-chat-message img")).to_have_count(0)
            await expect(page.get_by_role("button", name="Adicionar 1 CT sugerido", exact=True)).to_be_visible()
            await expect(page.get_by_role("checkbox", name="Incluir CT004", exact=True)).not_to_be_checked()
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            await page.locator(".suite-chat-messages").evaluate("node => { node.scrollTop = 0; }")
            await page.locator(".suite-chat").screenshot(path=str(tmp_path / f"premium-chat-answer-{width}.png"))
            await page.get_by_role("button", name="Assistente", exact=True).click()
            await page.get_by_role("button", name="Assistente", exact=True).click()
            await expect(page.get_by_role("button", name="Modelo de IA do chat: Modelo alternativo", exact=True)).to_be_enabled()
            await page.get_by_role("button", name="Modelo de IA do chat: Modelo alternativo", exact=True).click()
            await page.get_by_role("textbox", name="Buscar modelos de IA", exact=True).press("Escape")
            await expect(page.get_by_role("dialog", name="Escolher modelo de IA", exact=True)).to_have_count(0)
            assert not errors
        finally:
            release.set()
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("AUTOJEV_FRONTEND_E2E") != "1", reason="Opt-in frontend QA with intercepted API")
@pytest.mark.parametrize("width", [1440, 390], ids=["desktop", "mobile"])
async def test_print_cards_match_original_cards_and_have_yellow_slots_on_both_sides(tmp_path, width):
    suite, mutations, errors = fixture_suite(), [], []
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": width, "height": 1000})
        page.set_default_timeout(8000)
        page.on("pageerror", lambda error: errors.append(str(error)))

        async def api(route):
            request = route.request
            path = urlsplit(request.url).path.removeprefix("/api")
            headers = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Allow-Methods": "GET,POST,PUT"}
            if request.method == "OPTIONS":
                await route.fulfill(status=204, headers=headers)
                return
            response = []
            if path == "/models": response = fixture_models()
            elif path == "/test-suites/suite-fixture": response = suite
            elif path.endswith("/plans") and request.method == "POST":
                mutations.append((path, request.post_data_json))
                response = {**request.post_data_json, "id": "print-plan", "revision": 1, "status": "draft"}
            elif request.method != "GET": raise AssertionError("Unexpected mutation: " + path)
            await route.fulfill(status=200, json=response, headers=headers)

        await page.route("**/api/**", api)
        try:
            await page.goto("http://localhost:3000/#/conjuntos/suite-fixture?ct=CT001")
            await page.get_by_role("button", name="Assistente", exact=True).click()
            await page.get_by_role("button", name="Adicionar print na posição 1", exact=True).click()
            await expect(page.locator(".suite-print-card")).to_have_count(1)
            assert await page.evaluate("""() => {
                const reference = document.querySelector('.suite-source-step:not(.suite-print-card)').getBoundingClientRect();
                const pause = document.querySelector('.suite-print-card');
                const bounds = pause.getBoundingClientRect();
                return Math.abs(reference.width - bounds.width) < 1 && Math.abs(reference.height - bounds.height) < 1
                    && getComputedStyle(pause).borderTopStyle === 'solid'
                    && pause.previousElementSibling.tagName === 'BUTTON' && pause.nextElementSibling.tagName === 'BUTTON'
                    && [...document.querySelectorAll('.suite-gap > button')].every(button => getComputedStyle(button).color === 'rgb(242, 196, 91)');
            }""")
            await page.locator(".suite-case-plan").screenshot(path=str(tmp_path / f"print-card-{width}.png"))
            await page.get_by_role("button", name="Adicionar print após pausa db-1", exact=True).click()
            await page.get_by_role("button", name="Adicionar print após pausa db-1", exact=True).click()
            assert await page.locator(".suite-print-card").evaluate_all("cards => cards.map(card => card.dataset.checkpointId)") == ["db-1", "db-3", "db-2"]
            await page.get_by_role("button", name="Adicionar print na posição 1", exact=True).click()
            target = page.get_by_role("button", name="Adicionar print após pausa db-3", exact=True)
            await page.locator(".suite-print-palette").drag_to(target)
            await expect(page.locator(".suite-print-card")).to_have_count(5)
            assert await page.locator(".suite-print-card").evaluate_all("cards => cards.map(card => card.dataset.checkpointId)") == ["db-4", "db-1", "db-3", "db-5", "db-2"]
            await page.locator('[data-checkpoint-id="db-3"]').get_by_role("button", name="Remover pausa", exact=True).click()
            await expect(page.locator(".suite-print-card")).to_have_count(4)
            assert await page.locator(".suite-print-card").evaluate_all("cards => cards.every(card => card.previousElementSibling.tagName === 'BUTTON' && card.nextElementSibling.tagName === 'BUTTON')")
            assert not mutations
            await page.get_by_role("button", name="Guardar planejamento", exact=True).click()
            await expect(page.get_by_text("RASCUNHO SALVO", exact=False)).to_be_visible()
            points = mutations[0][1]["items"][0]["capture_plan"]["checkpoints"]
            assert [point["id"] for point in points] == ["db-4", "db-1", "db-5", "db-2"]
            assert all(point["gap"] == 1 for point in points)
            assert [point["order"] for point in points] == [0, 1, 3, 4]
            assert len(mutations) == 1 and not errors
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("AUTOJEV_FRONTEND_E2E") != "1", reason="Opt-in frontend QA with intercepted API")
@pytest.mark.parametrize("width", [1440, 390], ids=["desktop", "mobile"])
async def test_expanded_suite_history_is_one_newest_first_timeline(tmp_path, width):
    group = {"id": "suite-fixture", "app_url": "https://fixture.invalid", "created_at": "2026-10-14T19:22:00Z",
             "latest_activity": "2026-10-19T23:53:00Z", "initial_count": 151, "case_count": 151, "active_count": 0,
             "initial_status": "completed", "rounds": [
                 {"id": "older-round", "name": "Rodada antiga", "count": 1, "status": "completed", "created_at": "2026-10-16T12:00:00Z"},
                 {"id": "new-round", "name": "Nova rodada", "count": 2, "status": "completed", "created_at": "2026-10-19T23:53:00Z"}],
             "legacy_runs": [
                 {"id": "older-child", "count": 1, "status": "partial", "created_at": "2026-10-15T12:00:00Z"},
                 {"id": "newer-child", "count": 1, "status": "completed", "created_at": "2026-10-18T15:22:00Z"}]}
    errors, mutations = [], []
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": width, "height": 1000})
        page.set_default_timeout(8000)
        page.on("pageerror", lambda error: errors.append(str(error)))

        async def api(route):
            request = route.request
            path = urlsplit(request.url).path.removeprefix("/api")
            if request.method != "GET": mutations.append((request.method, path))
            response = [group] if path == "/test-suites" else []
            await route.fulfill(status=200, json=response, headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"})

        await page.route("**/api/**", api)
        try:
            await page.goto("http://localhost:3000/#/exploracoes")
            await page.get_by_role("button", name="Mostrar rodadas de fixture.invalid", exact=True).click()
            rows = page.locator(".suite-child-list > button")
            await expect(rows).to_have_count(5)
            assert await rows.evaluate_all("rows => rows.map(row => row.dataset.historyId)") == ["new-round", "newer-child", "older-round", "older-child", "suite-fixture"]
            await expect(rows.first).to_contain_text("Nova rodada")
            await expect(rows.last).to_contain_text("Execução inicial")
            await page.get_by_role("textbox", name="Buscar conjuntos", exact=True).fill("fixture")
            await expect(rows.first).to_contain_text("Nova rodada")
            group["legacy_runs"].append({"id": "latest-child", "count": 1, "status": "completed", "created_at": "2026-10-19T23:54:00Z"})
            await page.get_by_role("button", name="Atualizar", exact=True).click()
            await expect(rows).to_have_count(6)
            assert await rows.evaluate_all("rows => rows.map(row => row.dataset.historyId)") == ["latest-child", "new-round", "newer-child", "older-round", "older-child", "suite-fixture"]
            await page.screenshot(path=str(tmp_path / f"history-newest-first-{width}.png"), full_page=True)
            assert not mutations and not errors
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        finally:
            await browser.close()
