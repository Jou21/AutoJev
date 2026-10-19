"""Real Chromium QA of isolated offline reports and connected revision refresh.

All HTTP responses are intercepted fixtures. No user server, CT or login is
accessed; the only writes are temporary HTML, PNG and download test artifacts.
"""
import asyncio
import base64
import json
from urllib.parse import urlsplit

import pytest
from autojev.adapters.report import StandaloneHtmlReport
from PIL import Image
from playwright.async_api import async_playwright, expect
from test_report_execution_history import fixture

ORIGIN = "http://report-host.test"


def rendered_report(tmp_path, *, key="revisions-v2", title="Resultado mais recente"):
    run = fixture()
    run["scenario_revisions_key"] = key
    run["scenarios"][0]["title"] = title
    colors = {"root": "#d54a4a", "old-rerun": "#de9f22", "latest-rerun": "#27b67b"}
    images = {}
    for owner, color in colors.items():
        directory = tmp_path / owner / "screenshots"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "same.png"
        Image.new("RGB", (64, 40), color=color).save(path, format="PNG")
        images[owner] = "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()
    output = tmp_path / f"report-{key}.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(output))
    return output, output.read_text(), images


async def no_duplicate_ids(page):
    assert await page.evaluate("""() => {
      const ids = Array.from(document.querySelectorAll('[id]'), el => el.id);
      return ids.length === new Set(ids).size;
    }""")


@pytest.mark.asyncio
async def test_offline_current_history_owner_images_and_download_remain_complete(tmp_path):
    source, _, images = rendered_report(tmp_path)
    requests = []
    errors = []
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        context.on("request", lambda request: requests.append(request.url))
        page = await context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            await page.goto(source.as_uri() + "#view/tests")
            await expect(page.locator('#case-rows tr:not([hidden])')).to_have_count(2)
            # Open from the result cell, not just the title/name hit target.
            await page.locator('#case-rows tr[data-case-index="0"] td').nth(2).click()
            await expect(page.locator("body")).to_have_attribute("data-view", "results")
            await expect(page.locator("#case-rerun, #case-rerun-feedback")).to_have_count(0)
            await expect(page.locator("#case-title")).to_have_text("Resultado mais recente")
            current = page.locator('#scenarios details[data-case-index="0"]')
            current_image = current.locator('figure img[src^="data:"]')
            await expect(current_image).to_have_attribute("src", images["latest-rerun"])
            await expect(current_image).to_have_js_property("naturalWidth", 64)
            await page.locator("#case-history").click()
            dialog = page.locator("#case-history-dialog")
            await expect(dialog).to_be_visible()
            await expect(page.locator("#case-history-title")).to_have_text("Histórico de CT147")
            await expect(page.locator("#case-history-list button")).to_have_count(4)
            await page.get_by_role("button", name="Execução #1", exact=False).click()
            detail = page.locator("#case-history-detail")
            await expect(detail).to_contain_text("Primeira execução")
            await expect(detail.locator('figure img[src^="data:"]')).to_have_attribute("src", images["root"])
            await expect(detail.locator('figure img[src^="data:"]')).to_have_js_property("naturalWidth", 64)
            await no_duplicate_ids(page)
            await page.get_by_role("button", name="Execução #2", exact=False).click()
            await expect(detail).to_contain_text("Tentativa anterior")
            await expect(detail.locator('figure img[src^="data:"]')).to_have_attribute("src", images["old-rerun"])
            await no_duplicate_ids(page)
            await page.get_by_role("button", name="Execução #4", exact=False).click()
            await expect(detail).to_contain_text("não tem um resultado de teste registrado")
            await expect(detail.locator(".scenario")).to_have_count(0)
            await page.get_by_role("button", name="Execução #3 · Último resultado", exact=False).click()
            await expect(detail).to_contain_text("versão mostrada na lista de testes")
            await page.get_by_role("button", name="Ver resultado atual", exact=True).click()
            await expect(dialog).not_to_be_visible()
            await expect(page.locator("#case-title")).to_have_text("Resultado mais recente")
            await expect(current_image).to_have_attribute("src", images["latest-rerun"])
            await expect(page.locator("#scenarios details.scenario[data-case-index]")).to_have_count(2)
            await no_duplicate_ids(page)
            await page.screenshot(path=str(tmp_path / "latest-and-history-browser.png"))

            async with page.expect_download() as pending:
                await page.locator("#download-report").click()
            download = await pending.value
            exported_path = tmp_path / "downloaded-report.html"
            await download.save_as(exported_path)
            exported = exported_path.read_text()
            assert 'id="case-history-archive"' in exported
            assert 'id="report-app-runtime"' not in exported
            for image in images.values():
                assert image in exported
            saved = await context.new_page()
            await saved.goto(exported_path.as_uri() + "#view/results/CT147")
            await expect(saved.locator("#case-title")).to_have_text("Resultado mais recente")
            await saved.locator("#case-history").click()
            await saved.get_by_role("button", name="Execução #1", exact=False).click()
            await expect(saved.locator('#case-history-detail figure img[src^="data:"]')).to_have_attribute("src", images["root"])
            await no_duplicate_ids(saved)
            assert not [url for url in requests if urlsplit(url).scheme in {"http", "https"}]
            assert not errors
        finally:
            await context.close()
            await browser.close()


async def connected_page(browser, markup, state, requests):
    context = await browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
    page = await context.new_page()
    runtime = {"app_url": ORIGIN, "source_exploration_id": "root", "api_url": ORIGIN + "/api"}
    injected = markup.replace("<head>", '<head><script id="report-app-runtime" type="application/json">' + json.dumps(runtime) + "</script>", 1)

    async def isolate(route):
        request = route.request
        parsed = urlsplit(request.url)
        requests.append((request.method, parsed.path))
        assert request.method == "GET", "Reports must not execute or mutate a test"
        if parsed.path == "/":
            await route.fulfill(status=200, content_type="text/html", body="<!doctype html><title>Isolated report host</title>")
        elif parsed.path == "/api/explorations/root/scenario-revisions":
            await route.fulfill(status=200, json={"key": state["key"]}, headers={"Cache-Control": "no-store"})
        elif parsed.path == "/api/explorations/root/report":
            if state.get("report_requested") is not None:
                state["report_requested"].set()
            if state.get("report_release") is not None:
                await state["report_release"].wait()
            await route.fulfill(status=200, content_type="text/html", body=state["markup"], headers={"Cache-Control": "no-store"})
        else:
            raise AssertionError("Unexpected external request: " + parsed.path)

    await context.route("**/*", isolate)
    await page.goto(ORIGIN + "/")
    await page.evaluate("""markup => {
      const report = URL.createObjectURL(new Blob([markup], {type: 'text/html'}));
      location.assign(report + '#view/results/CT147');
    }""", injected)
    await expect(page.locator("#case-title")).to_have_text("Resultado mais recente")
    assert page.url.startswith("blob:" + ORIGIN + "/")
    return context, page


@pytest.mark.asyncio
async def test_execution_timeline_switches_owned_evidence_without_changing_latest_results(tmp_path):
    _, markup, images = rendered_report(tmp_path)
    requests = []
    state = {"key": "revisions-v2", "markup": markup}
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        context, page = await connected_page(browser, markup, state, requests)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            await expect(page.locator("#case-execution-list button")).to_have_count(4)
            await expect(page.locator('#case-execution-list [aria-pressed="true"]')).to_have_attribute("data-execution-version", "2")
            await expect(page.locator("#case-execution-metadata")).to_contain_text("Último resultado")
            await expect(page.locator('#case-execution-list [data-execution-version="3"]')).to_contain_text("Mais recente")
            await page.locator('#case-execution-list [data-execution-version="0"]').click()
            await expect(page.locator("#case-title")).to_have_text("Primeira execução")
            await expect(page.locator("#case-selected-execution")).to_be_visible()
            await expect(page.locator("#scenarios")).not_to_be_visible()
            await expect(page.locator('#case-selected-execution figure img[src^="data:"]')).to_have_attribute("src", images["root"])
            await expect(page.locator("#case-selected-execution .case-inline-insert")).to_have_count(0)
            await expect(page.locator("#case-edit")).to_be_disabled()
            assert page.url.endswith("?execution=0")
            await no_duplicate_ids(page)
            await page.locator('#case-execution-list [data-execution-version="1"]').click()
            await expect(page.locator("#case-title")).to_have_text("Tentativa anterior")
            await expect(page.locator("#result-case-status")).to_have_text("Reprovado")
            await expect(page.locator('#case-selected-execution figure img[src^="data:"]')).to_have_attribute("src", images["old-rerun"])
            await page.evaluate("window.dispatchEvent(new Event('beforeprint'))")
            await expect(page.locator("#scenarios")).to_have_css("display", "none")
            await page.evaluate("window.dispatchEvent(new Event('afterprint'))")
            state["key"] = "newer-result"
            await page.evaluate("window.dispatchEvent(new Event('focus'))")
            await expect(page.get_by_role("button", name="Atualizar resultados", exact=True)).to_be_attached()
            await expect(page.locator("#case-title")).to_have_text("Tentativa anterior")
            assert ("GET", "/api/explorations/root/report") not in requests
            state["key"] = "revisions-v2"
            async with page.expect_download() as pending:
                await page.locator("#download-report").click()
            exported_path = tmp_path / "timeline-offline.html"
            await (await pending.value).save_as(exported_path)
            offline = await browser.new_context()
            offline_requests = []
            offline.on("request", lambda request: offline_requests.append(request.url))
            saved = await offline.new_page()
            await saved.goto(exported_path.as_uri() + "#view/results/CT147?execution=1")
            await expect(saved.locator("#case-title")).to_have_text("Tentativa anterior")
            await expect(saved.locator("#case-rerun")).to_have_count(0)
            await expect(saved.locator('#case-selected-execution figure img[src^="data:"]')).to_have_attribute("src", images["old-rerun"])
            await no_duplicate_ids(saved)
            assert not [url for url in offline_requests if urlsplit(url).scheme in {"http", "https"}]
            await offline.close()
            await page.evaluate("() => { window.__opened = []; window.open = (...args) => { window.__opened.push(args); return null; }; }")
            await page.locator("#case-rerun").click()
            assert await page.evaluate("window.__opened") == [[ORIGIN + "/#/reexecutar/old-rerun/CT001", "_blank", "noopener,noreferrer"]]
            await page.locator("#case-compare").click()
            await expect(page.locator(".execution-comparison")).to_contain_text("Último resultado")
            await no_duplicate_ids(page)
            await page.locator("#case-history-close").click()
            await page.locator('#case-execution-list [data-execution-version="3"]').click()
            await expect(page.locator("#case-selected-execution")).to_contain_text("Cancelada")
            await expect(page.locator("#case-rerun")).to_be_disabled()
            await expect(page.locator("#case-selected-execution .timeline-entry")).to_have_count(0)
            await page.get_by_role("button", name="Ver último resultado disponível", exact=True).click()
            await expect(page.locator("#case-title")).to_have_text("Resultado mais recente")
            await expect(page.locator("#case-edit")).to_be_enabled()
            await expect(page.locator('#scenarios details[data-case-index="0"] figure img[src^="data:"]')).to_have_attribute("src", images["latest-rerun"])
            await expect(page.locator("#case-history-archive details[data-history-case-index]")).to_have_count(2)
            await page.locator("#result-more summary").click()
            await page.locator("#case-next").click()
            await expect(page.locator("#case-title")).to_have_text("Outro CT não reexecutado")
            await expect(page.locator("#case-execution-list button")).to_have_count(1)
            await no_duplicate_ids(page)
            assert not errors
            assert all(method == "GET" for method, _ in requests)
        finally:
            await context.close()
            await browser.close()


@pytest.mark.asyncio
async def test_execution_timeline_expands_all_attempts_and_keeps_an_older_selection(tmp_path):
    from test_report_execution_history import revision

    run = fixture()
    run["scenarios"][0]["execution_history"].extend([
        revision(4, "no-result-4", None, run_status="cancelled"),
        revision(5, "no-result-5", None, run_status="cancelled"),
    ])
    source, _, _ = rendered_report(tmp_path)
    StandaloneHtmlReport(tmp_path).generate(run, str(source))
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        try:
            await page.goto(source.as_uri() + "#view/results/CT147")
            await expect(page.locator("#case-execution-list button")).to_have_count(4)
            await page.locator("#case-executions-all").click()
            await expect(page.locator("#case-execution-list button")).to_have_count(6)
            await page.locator('#case-execution-list [data-execution-version="0"]').focus()
            await page.keyboard.press("Enter")
            await expect(page.locator("#case-title")).to_have_text("Primeira execução")
            await expect(page.locator('#case-execution-list [data-execution-version="0"]')).to_be_focused()
            await page.locator("#case-executions-all").click()
            await expect(page.locator("#case-execution-list button")).to_have_count(4)
            await expect(page.locator('#case-execution-list [data-execution-version="0"]')).to_have_attribute("aria-pressed", "true")
            await no_duplicate_ids(page)
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_connected_focus_refreshes_latest_report_in_same_blob_and_hash(tmp_path):
    _, original, _ = rendered_report(tmp_path)
    _, newer, _ = rendered_report(tmp_path, key="revisions-v3", title="Mais novo resultado após reexecução")
    state = {"key": "revisions-v2", "markup": newer}
    requests = []
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        context, page = await connected_page(browser, original, state, requests)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            location = page.url
            # Same-verdict evidence/title changes must also trigger refresh.
            state["key"] = "revisions-v3"
            await page.evaluate("window.dispatchEvent(new Event('focus'))")
            await expect(page.locator("#case-title")).to_have_text("Mais novo resultado após reexecução")
            assert page.url == location
            assert ("GET", "/api/explorations/root/scenario-revisions") in requests
            assert ("GET", "/api/explorations/root/report") in requests
            await no_duplicate_ids(page)
            await page.evaluate("""() => {
              window.__opened = [];
              window.open = (...args) => { window.__opened.push(args); return null; };
            }""")
            await expect(page.locator("#case-rerun")).to_be_visible()
            await page.locator("#case-rerun").click()
            opened = await page.evaluate("window.__opened")
            assert opened == [[ORIGIN + "/#/reexecutar/latest-rerun/CT001", "_blank", "noopener,noreferrer"]]
            async with page.expect_download() as pending:
                await page.locator("#download-report").click()
            exported_path = tmp_path / "connected-download.html"
            await (await pending.value).save_as(exported_path)
            exported = exported_path.read_text()
            assert 'id="report-app-runtime"' not in exported
            assert 'id="case-rerun"' not in exported
            assert 'id="case-rerun-feedback"' not in exported
            await expect(page.locator("#case-rerun")).to_be_visible()
            assert 'id="case-history-archive"' in exported
            assert "revisions-v3" in exported and "Mais novo resultado após reexecução" in exported
            assert not errors
        finally:
            await context.close()
            await browser.close()


@pytest.mark.asyncio
async def test_connected_open_history_keeps_snapshot_until_explicit_refresh(tmp_path):
    _, original, _ = rendered_report(tmp_path)
    _, newer, _ = rendered_report(tmp_path, key="revisions-v3", title="Mais novo resultado após reexecução")
    state = {"key": "revisions-v2", "markup": newer}
    requests = []
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        context, page = await connected_page(browser, original, state, requests)
        try:
            location = page.url
            await page.locator("#case-history").click()
            await page.get_by_role("button", name="Execução #1", exact=False).click()
            state["key"] = "revisions-v3"
            await page.evaluate("window.dispatchEvent(new Event('focus'))")
            await expect(page.get_by_role("button", name="Atualizar resultados", exact=True)).to_be_attached()
            await expect(page.locator("#case-history-dialog")).to_be_visible()
            await expect(page.locator("#case-history-detail")).to_contain_text("Primeira execução")
            await expect(page.locator("#case-title")).to_have_text("Resultado mais recente")
            assert ("GET", "/api/explorations/root/report") not in requests
            # The existing version stays fully readable before the user agrees.
            await page.locator("#case-history-close").click()
            await page.get_by_role("button", name="Atualizar resultados", exact=True).click()
            await expect(page.locator("#case-title")).to_have_text("Mais novo resultado após reexecução")
            assert page.url == location
            await no_duplicate_ids(page)
        finally:
            await context.close()
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}], ids=["desktop", "mobile"])
async def test_history_dialog_layout_stays_inside_viewport(tmp_path, viewport):
    source, _, _ = rendered_report(tmp_path)
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        page = await browser.new_page(viewport=viewport)
        try:
            await page.goto(source.as_uri() + "#view/results/CT147")
            await page.locator("#case-history").click()
            dialog = page.locator("#case-history-dialog")
            await expect(dialog).to_be_visible()
            for version in ("Execução #1", "Execução #2"):
                await page.get_by_role("button", name=version, exact=False).click()
                box = await dialog.bounding_box()
                assert box and box["x"] >= 0 and box["y"] >= 0
                assert box["x"] + box["width"] <= viewport["width"] + 1
                assert box["y"] + box["height"] <= viewport["height"] + 1
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert await dialog.evaluate("el => el.scrollWidth <= el.clientWidth + 1")
                assert await page.locator("#case-history-detail").evaluate("el => el.scrollWidth <= el.clientWidth + 1")
                await no_duplicate_ids(page)
                version_id = "original" if version == "Execução #1" else "rerun-1"
                await page.screenshot(path=str(tmp_path / f"history-{viewport['width']}-{version_id}.png"))
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_connected_saved_draft_blocks_auto_refresh_and_cancel_preserves_it(tmp_path):
    _, original, _ = rendered_report(tmp_path)
    _, newer, _ = rendered_report(tmp_path, key="revisions-v3", title="Mais novo resultado após reexecução")
    state = {"key": "revisions-v2", "markup": newer}
    requests = []
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        context, page = await connected_page(browser, original, state, requests)
        try:
            await page.locator(".case-inline-insert").first.click()
            await page.locator('#step-picker [data-step-choice="wait_print"]').click()
            before = await page.locator("#report-drafts").text_content()
            assert json.loads(before)["cases"]["0"][0]["type"] == "wait_print"
            state["key"] = "revisions-v3"
            await page.evaluate("window.dispatchEvent(new Event('focus'))")
            update = page.get_by_role("button", name="Atualizar resultados", exact=True)
            await expect(update).to_be_visible()
            await expect(page.locator("#case-title")).to_have_text("Resultado mais recente")
            assert ("GET", "/api/explorations/root/report") not in requests
            confirmations = []

            async def decline(dialog):
                confirmations.append(dialog.message)
                await dialog.dismiss()

            page.once("dialog", decline)
            await update.click()
            await expect(update).to_be_enabled()
            assert len(confirmations) == 1 and "descarta os passos extras" in confirmations[0]
            assert await page.locator("#report-drafts").text_content() == before
            await expect(page.locator("#case-title")).to_have_text("Resultado mais recente")
            assert ("GET", "/api/explorations/root/report") not in requests
        finally:
            await context.close()
            await browser.close()


@pytest.mark.asyncio
async def test_history_opened_during_report_fetch_is_not_discarded(tmp_path):
    _, original, _ = rendered_report(tmp_path)
    _, newer, _ = rendered_report(tmp_path, key="revisions-v3", title="Mais novo resultado após reexecução")
    requested, release = asyncio.Event(), asyncio.Event()
    state = {"key": "revisions-v2", "markup": newer, "report_requested": requested, "report_release": release}
    requests = []
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        context, page = await connected_page(browser, original, state, requests)
        try:
            location = page.url
            state["key"] = "revisions-v3"
            await page.evaluate("window.dispatchEvent(new Event('focus'))")
            async with asyncio.timeout(5):
                await requested.wait()
            await page.locator("#case-history").click()
            await page.get_by_role("button", name="Execução #1", exact=False).click()
            release.set()
            await expect(page.get_by_role("button", name="Atualizar resultados", exact=True)).to_be_attached()
            await expect(page.locator("#case-history-dialog")).to_be_visible()
            await expect(page.locator("#case-history-detail")).to_contain_text("Primeira execução")
            await expect(page.locator("#case-title")).to_have_text("Resultado mais recente")
            assert page.url == location
            assert json.loads(await page.locator("#report-data").text_content())["scenario_revisions_key"] == "revisions-v2"
            await no_duplicate_ids(page)
        finally:
            release.set()
            await context.close()
            await browser.close()
