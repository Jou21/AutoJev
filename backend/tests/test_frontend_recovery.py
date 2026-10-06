"""Local UI smoke test, with all API/model traffic replaced by fixtures.

Runs when the existing localhost frontend is available. Does not start servers,
read provider secrets, change app history, or make any external/model request.
"""

import base64
from urllib.parse import urlsplit

import httpx
import pytest
from playwright.async_api import async_playwright


async def test_screenshot_error_can_be_retried_and_visual_gaps_are_visible(tmp_path):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get("http://localhost:3000/")
            if response.status_code != 200:
                pytest.skip(f"Local frontend returned HTTP {response.status_code}")
            vite_client = await client.get("http://localhost:3000/@vite/client")
    except httpx.HTTPError as exc:
        pytest.skip(f"Local frontend unavailable: {type(exc).__name__}")
    assert '"enabled":false,"unhandledErrors":false,"logLevels":[]' in vite_client.text

    run = {
        "id": "ui-fixture", "app_url": "https://shop.fixture.test/",
        "status": "completed", "phase": "complete", "overall_progress": 100,
        "report_available": True, "updated_at": "2026-10-02T22:00:00Z",
        "created_at": "2026-10-02T21:00:00Z", "finished_at": "2026-10-02T22:00:00Z",
        "options": {"auxiliary_evidence_mode": "visual"},
        "screenshot_url": "/api/explorations/ui-fixture/screenshots/after.png",
        "counters": {"actions": 2, "scenarios": 2},
        "scenarios": [{"id": f"CT{i:03d}", "title": "Fixture cart check", "status": "PASS",
                       "evidence": {"visual_assessment": {"requested": True, "status": "unavailable", "reason": reason}}}
                      for i, reason in enumerate(["auxiliary_rate_limited", "visual_assessment_not_returned"], 1)],
    }
    shots, unexpected = [], []
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1sAAAAASUVORK5CYII=")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={"width": 1024, "height": 768})

            async def serve(route):
                parsed = urlsplit(route.request.url)
                if "/api/" in parsed.path:
                    assert route.request.method == "GET"
                    if "/screenshots/" in parsed.path:
                        shots.append(route.request.url)
                        if len(shots) == 1:
                            await route.fulfill(status=503, body="Fixture transient image error")
                        else:
                            await route.fulfill(content_type="image/png", body=png)
                    else:
                        import json
                        data = (run if parsed.path.endswith("/ui-fixture") else [run] if parsed.path.endswith("/explorations") else [])
                        await route.fulfill(content_type="application/json", body=json.dumps(data))
                elif parsed.hostname == "localhost" and parsed.port == 3000:
                    await route.continue_()
                else:
                    unexpected.append(route.request.url)
                    await route.abort()

            await page.route("**/*", serve)
            await page.goto("http://localhost:3000/#/exploracoes/ui-fixture")
            await page.get_by_text("0 de 2 CTs receberam uma análise visual válida", exact=False).wait_for()
            await page.get_by_text("Análise visual não realizada", exact=True).wait_for()
            await page.get_by_text("Não foi possível carregar a captura", exact=True).wait_for()
            assert len(shots) == 1
            capture = tmp_path / "frontend-recovery.png"
            await page.screenshot(path=str(capture), full_page=True)
            print(f"UI evidence: {capture}")
            await page.get_by_role("button", name="Tentar novamente", exact=True).click()
            image = page.get_by_role("img", name="Último screenshot de shop.fixture.test", exact=True)
            await image.wait_for()
            await page.wait_for_function("document.querySelector('.browser-canvas img')?.naturalWidth === 1")
            assert len(shots) == 2 and "retry-1" in shots[1]
            assert await page.get_by_text("Não foi possível carregar a captura", exact=True).count() == 0
            # A fresh capture must not inherit the previous request's failure.
            run["updated_at"] = "2026-10-02T22:01:00Z"
            run["screenshot_url"] = "/api/explorations/ui-fixture/screenshots/new.png"
            await page.get_by_role("button", name="Atualizar", exact=True).click()
            await page.wait_for_function("document.querySelector('.browser-canvas img')?.src.includes('/new.png')")
            assert await page.get_by_text("Não foi possível carregar a captura", exact=True).count() == 0
            assert not unexpected
            assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        finally:
            await browser.close()


async def test_application_credentials_are_sent_even_when_section_is_collapsed(tmp_path):
    import json

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            if (await client.get("http://localhost:3000/")).status_code != 200:
                pytest.skip("Local frontend unavailable")
    except httpx.HTTPError:
        pytest.skip("Local frontend unavailable")

    models = [{"id": "fixture-model", "model_id": "fixture-model", "name": "Fixture model",
               "enabled": True, "input_modalities": ["text"], "capabilities_known": True}]
    provider = {"id": "fixture-provider", "name": "Fixture provider", "enabled": True,
                "base_url": "http://fixture.invalid", "has_api_key": False, "models": models}
    submitted, unexpected = [], []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={"width": 1024, "height": 768})

            async def serve(route):
                parsed = urlsplit(route.request.url)
                if "/api/" in parsed.path:
                    if route.request.method == "POST":
                        assert parsed.path.endswith("/explorations")
                        submitted.append(route.request.post_data_json)
                        # Keep the form mounted for assertions; no real run.
                        await route.fulfill(status=503, content_type="application/json", body='{"detail":"Fixture-only submission recorded"}')
                    else:
                        data = [provider] if parsed.path.endswith('/providers') else models if parsed.path.endswith('/models') else []
                        await route.fulfill(content_type="application/json", body=json.dumps(data))
                elif parsed.hostname == "localhost" and parsed.port == 3000:
                    await route.continue_()
                else:
                    unexpected.append(route.request.url)
                    await route.abort()

            await page.route("**/*", serve)
            await page.goto("http://localhost:3000/#/nova-exploracao")
            await page.get_by_label("URL da aplicação").fill("http://login.fixture.test/")
            section = page.get_by_role("button", name="Credenciais da aplicação", exact=False)
            await section.click()
            await page.get_by_label("Usuário ou e-mail", exact=True).fill("fixture-login-user")
            await page.get_by_role("button", name="Iniciar exploração", exact=True).click()
            await page.get_by_text("Informe usuário e senha juntos, ou deixe ambos vazios para fazer login manualmente.", exact=True).wait_for()
            assert submitted == []
            await page.get_by_label("Senha", exact=True).fill("  fixture-password-with-spaces  ")
            await section.click()
            assert await page.get_by_label("Senha", exact=True).count() == 0
            await page.get_by_text("Credenciais configuradas · login automático, mesmo com esta seção recolhida.", exact=True).wait_for()
            await page.get_by_role("button", name="Iniciar exploração", exact=True).click()
            await page.get_by_text("Fixture-only submission recorded", exact=True).wait_for()
            assert len(submitted) == 1
            assert submitted[0]["credentials"] == {"username": "fixture-login-user", "password": "  fixture-password-with-spaces  "}
            assert not unexpected
            assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            capture = tmp_path / "credentials-collapsed.png"
            await page.screenshot(path=str(capture), full_page=True)
            print(f"UI evidence: {capture}")
        finally:
            await browser.close()
