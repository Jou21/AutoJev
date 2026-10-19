"""The rerun view switch must not move when opening overview/results.

Opt-in real frontend QA: AUTOJEV_FRONTEND_E2E=1 with localhost:3000.
Every API response is an isolated fixture; no user execution is accessed.
"""
import os
from urllib.parse import urlsplit

import pytest
from playwright.async_api import async_playwright, expect


def fixture_run(waiting_print):
    titles = ["Antes da ação", "Aguardar print", "Selecionar opção", "Depois da ação",
              "Opção selecionada", "Ordenação dos produtos", "Erros durante a ação"]
    steps = [{"id": "checkpoint-db-1" if index == 1 else f"source-{index}",
              "kind": "capture_wait" if index == 1 else "observation" if index in {0, 3} else "action" if index == 2 else "assertion",
              "title": title, "state": ("done" if index == 0 else "waiting" if index == 1 else "pending") if waiting_print else "done",
              "label": "Aguardando seu print" if waiting_print and index == 1 else "Captura registrada" if index == 0 else "Concluída",
              "description": "Registro desta tentativa.", **({"checkpoint_id": "db-1"} if index == 1 else {})}
             for index, title in enumerate(titles)]
    return {
        "id": "rerun-header-fixture", "app_url": "https://fixture.invalid/inventory.html",
        "status": "waiting_input" if waiting_print else "completed", "phase": "waiting_input" if waiting_print else "complete",
        "created_at": "2026-10-18T12:00:00Z", "started_at": "2026-10-18T12:00:00Z",
        "finished_at": None if waiting_print else "2026-10-18T12:01:00Z",
        "options": {"mode": "single_ct_rerun", "auxiliary_evidence_mode": "text"},
        "scenarios": [], "actions": [], "events": [], "report_available": not waiting_print,
        "progress": 70 if waiting_print else 100,
        "rerun": {"running": waiting_print, "phase": "waiting_capture" if waiting_print else "finished",
                  "source_exploration_id": "source-fixture", "source_scenario_id": "CT001",
                  "capture_count": 1, "captures_completed": 0 if waiting_print else 1,
                  "capture_checkpoint": {"id": "db-1", "gap": 1, "order": 0, "version": 1} if waiting_print else None,
                  "step_progress": {"version": 1, "steps": steps, "completed": 1 if waiting_print else 7,
                                    "total": 7, "current_id": "checkpoint-db-1" if waiting_print else None}},
    }


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("AUTOJEV_FRONTEND_E2E") != "1", reason="Opt-in local frontend browser test")
@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 900}, {"width": 1920, "height": 1080},
                                     {"width": 390, "height": 844}])
@pytest.mark.parametrize("waiting_print", [False, True], ids=["completed", "waiting-print"])
async def test_rerun_header_and_switch_stay_in_place_across_live_and_overview(tmp_path, viewport, waiting_print):
    data = fixture_run(waiting_print)
    requests = []
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        page = await browser.new_page(viewport=viewport)

        async def isolated_api(route):
            request = route.request
            requests.append((request.method, urlsplit(request.url).path))
            assert request.method in {"GET", "OPTIONS"}, "View switching must not dispatch or change any run"
            path = urlsplit(request.url).path
            if path.endswith("/capture-print/preview"):
                response = {"checkpoint_id": "db-1", "checkpoint_version": 1, "revision": 0,
                            "changed": True, "image": None}
            elif path.endswith("/explorations/rerun-header-fixture"):
                response = data
            else:
                response = [data] if path.endswith("/explorations") else []
            await route.fulfill(status=200, json=response, headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"})

        await page.route("**/api/**", isolated_api)
        try:
            await page.goto("http://localhost:3000/#/exploracoes/rerun-header-fixture/acompanhar")
            await expect(page.locator(".detail-title h1")).to_have_text("Reexecução de CT001")
            await page.evaluate("document.fonts.ready")
            switch = page.get_by_role("navigation", name="Telas da reexecução")
            header = page.locator(".detail-header")
            origin = page.locator(".rerun-origin-details")
            live_box = await switch.bounding_box()
            header_box = await header.bounding_box()
            origin_box = await origin.bounding_box()
            assert live_box and header_box and origin_box
            await expect(origin).not_to_have_attribute("open", "")
            await page.get_by_role("button", name="Visão geral e resultados", exact=True).click()
            await expect(page.locator(".execution-command")).to_be_visible()
            await expect(page.locator(".detail-title h1")).to_have_text("Reexecução de CT001")
            await expect(page.locator(".detail-breadcrumb")).to_be_hidden()
            await expect(page.get_by_role("button", name="Visão geral e resultados", exact=True)).to_have_attribute("aria-current", "page")
            for locator, expected in [(switch, live_box), (header, header_box), (origin, origin_box)]:
                current = await locator.bounding_box()
                assert current
                for dimension in ("x", "y", "width", "height"):
                    assert abs(current[dimension] - expected[dimension]) <= 1, (dimension, expected, current)
            overview = await page.locator(".execution-command").bounding_box()
            assert overview and abs(overview["x"] - header_box["x"]) <= 1
            assert abs(overview["width"] - header_box["width"]) <= 1, "Overview body must remain full-width"
            if waiting_print:
                capture = await page.locator(".rerun-capture").bounding_box()
                assert capture and abs(capture["width"] - header_box["width"]) <= 1
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            await page.screenshot(path=str(tmp_path / "overview-same-header.png"))
            await page.get_by_role("button", name="Acompanhar reexecução", exact=True).click()
            await expect(page.locator(".rerun-live-board")).to_be_visible()
            returned = await switch.bounding_box()
            assert returned and abs(returned["y"] - live_box["y"]) <= 1 and abs(returned["x"] - live_box["x"]) <= 1
            assert all(method in {"GET", "OPTIONS"} for method, _ in requests)
            await page.screenshot(path=str(tmp_path / "live-same-header.png"))
        finally:
            await browser.close()
