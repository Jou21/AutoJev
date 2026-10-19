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


def rendered_report(tmp_path, *, key="revisions-v2", title="Resultado mais recente", with_journey=False):
    run = fixture()
    run["scenario_revisions_key"] = key
    run["scenarios"][0]["title"] = title
    if with_journey:
        run["actions"][0]["result"]["before_url"] = run["scenarios"][0]["url"]
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
            await expect(page.locator("#case-execution-summary, #case-compare")).to_have_count(0)
            await expect(page.locator("#case-execution-list button")).to_have_count(4)
            await page.locator('#case-execution-list [data-execution-version="0"]').click()
            detail = page.locator("#case-selected-execution")
            await expect(detail).to_contain_text("Primeira execução")
            await expect(detail.locator('figure img[src^="data:"]')).to_have_attribute("src", images["root"])
            await expect(detail.locator('figure img[src^="data:"]')).to_have_js_property("naturalWidth", 64)
            await no_duplicate_ids(page)
            await page.locator('#case-execution-list [data-execution-version="1"]').click()
            await expect(detail).to_contain_text("Tentativa anterior")
            await expect(detail.locator('figure img[src^="data:"]')).to_have_attribute("src", images["old-rerun"])
            await no_duplicate_ids(page)
            await page.locator('#case-execution-list [data-execution-version="3"]').click()
            await expect(detail).to_contain_text("não tem passos ou evidências de um resultado concluído")
            await expect(detail.locator(".scenario")).to_have_count(0)
            await page.locator('#case-execution-list [data-execution-version="2"]').click()
            await expect(detail).not_to_be_visible()
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
            await saved.locator('#case-execution-list [data-execution-version="0"]').click()
            await expect(saved.locator('#case-selected-execution figure img[src^="data:"]')).to_have_attribute("src", images["root"])
            await no_duplicate_ids(saved)
            assert not [url for url in requests if urlsplit(url).scheme in {"http", "https"}]
            assert not errors
        finally:
            await context.close()
            await browser.close()


async def connected_page(browser, markup, state, requests, *, expected_title="Resultado mais recente"):
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
    await expect(page.locator("#case-title")).to_have_text(expected_title)
    assert page.url.startswith("blob:" + ORIGIN + "/")
    return context, page


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [1440, 390], ids=["desktop", "mobile"])
async def test_summary_is_an_unnumbered_list_item_for_current_and_previous_executions(tmp_path, width):
    source, _, _ = rendered_report(tmp_path)
    errors = []
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": width, "height": 1000})
        page = await context.new_page()
        page.set_default_timeout(5000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            await page.goto(source.as_uri() + "#view/results/CT147")
            for version, status in [(2, "Aprovado"), (1, "Reprovado")]:
                await page.locator(f'#case-execution-list [data-execution-version="{version}"]').click()
                workspace = page.locator(".case-workspace:visible")
                sidebar = workspace.locator(".case-step-sidebar")
                steps = sidebar.locator(".case-step-list > .case-step-button")
                summary = steps.first
                await expect(steps).to_have_count(3)
                await expect(sidebar.locator(".case-step-sidebar-header button")).to_have_count(0)
                await expect(summary).to_have_attribute("data-step-kind", "summary")
                await expect(summary.locator(".step-label")).to_have_text("Resumo do teste")
                await expect(summary.locator(".step-number")).to_have_count(0)
                assert await steps.locator(".step-number").all_text_contents() == ["01", "02"]
                await expect(sidebar.locator(".case-step-progress")).to_have_text("2 / 2")
                await summary.click()
                await expect(summary).to_have_attribute("aria-current", "step")
                await expect(workspace.locator(".case-summary-panel")).to_be_visible()
                await expect(workspace.locator(".timeline-entry:visible")).to_have_count(0)
                await expect(workspace.locator(".case-step-outcome")).to_have_text(status)
                assert await summary.evaluate("""el => {
                  const box = el.getBoundingClientRect();
                  const list = el.parentElement.getBoundingClientRect();
                  const label = el.querySelector('.step-label').getBoundingClientRect();
                  const icon = el.querySelector('.step-status-icon').getBoundingClientRect();
                  return box.width >= list.width - 20 && box.height >= 52 &&
                    label.width > 50 && icon.width === icon.height &&
                    Math.abs(icon.y + icon.height / 2 - box.y - box.height / 2) <= 1 &&
                    getComputedStyle(el, '::after').display === 'none';
                }""")
                await sidebar.screenshot(path=str(tmp_path / f"summary-step-{width}-v{version}.png"))
                await page.keyboard.press("ArrowDown")
                await expect(steps.nth(1)).to_be_focused()
                await expect(steps.nth(1)).to_have_attribute("aria-current", "step")
                await expect(workspace.locator(".case-summary-panel")).not_to_be_visible()
                await page.keyboard.press("Home")
                await expect(summary).to_be_focused()
                await expect(workspace.locator(".case-summary-panel")).to_be_visible()
                await no_duplicate_ids(page)
            assert not errors
        finally:
            await context.close()
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [1440, 390], ids=["desktop", "mobile"])
async def test_result_header_has_only_primary_actions_and_a_status_tag_next_to_title(tmp_path, width):
    title = "Comportamento funcional: Sort products — option Name (Z to A)"
    _, markup, _ = rendered_report(tmp_path, title=title)
    requests = []
    state = {"key": "revisions-v2", "markup": markup}
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        context, page = await connected_page(browser, markup, state, requests, expected_title=title)
        page.set_default_timeout(5000)
        try:
            await page.set_viewport_size({"width": width, "height": 1000})
            await expect(page.locator("#case-history, #case-edit, #result-more")).to_have_count(0)
            assert await page.locator(".result-navigation button:visible").evaluate_all(
                "buttons => buttons.map(button => button.id)") == ["case-back", "case-rerun", "download-report"]
            tag = page.locator(".result-heading #result-case-status")
            await expect(tag).to_have_text("Aprovado")
            assert await tag.evaluate("el => el.tagName === 'SPAN' && parseFloat(getComputedStyle(el).borderRadius) >= 28")
            heading_box = await page.locator("#case-title").bounding_box()
            tag_box = await tag.bounding_box()
            assert tag_box["height"] >= 28
            assert tag_box["x"] >= 0 and tag_box["x"] + tag_box["width"] <= width
            assert await page.locator(".result-navigation").evaluate("el => el.scrollWidth <= el.clientWidth + 1")
            if width > 900:
                assert 0 < tag_box["x"] - (heading_box["x"] + heading_box["width"]) <= 15
                assert abs(tag_box["y"] + tag_box["height"] / 2 - heading_box["y"] - heading_box["height"] / 2) <= 1
            else:
                assert tag_box["y"] >= heading_box["y"] + heading_box["height"]
                assert abs(tag_box["x"] - heading_box["x"]) <= 1
            await page.locator(".result-navigation").screenshot(path=str(tmp_path / f"result-header-{width}.png"))
            await expect(page.locator("#case-execution-summary, #case-execution-metadata, #case-compare")).to_have_count(0)
            history_box = await page.locator("#case-execution-history").bounding_box()
            workspace_box = await page.locator("#scenarios .case-workspace").first.bounding_box()
            assert 0 <= workspace_box["y"] - history_box["y"] - history_box["height"] <= 16
            await page.screenshot(path=str(tmp_path / f"results-without-summary-{width}.png"))
            await page.locator('#case-execution-list [data-execution-version="1"]').click()
            await expect(tag).to_have_text("Reprovado")
            await expect(tag).to_have_class("badge fail")
            await no_duplicate_ids(page)
            assert all(method == "GET" for method, _ in requests)
        finally:
            await context.close()
            await browser.close()


@pytest.mark.asyncio
async def test_execution_timeline_switches_owned_evidence_without_changing_latest_results(tmp_path):
    _, markup, images = rendered_report(tmp_path)
    # Reproduce a long-running backend holding its old template while it loads
    # current CSS/JS: the obsolete button must leave both the blob and exports.
    markup = markup.replace('<div class="execution-history-track">',
                            '<div class="execution-history-track"><button id="case-executions-all">Ver todas →</button>', 1)
    markup = markup.replace('<div id="result-report-actions"',
                            '<button id="case-history">Histórico</button><button id="case-edit">Editar passos</button>'
                            '<details id="result-more"><summary>Mais</summary></details><div id="result-report-actions"', 1)
    markup = markup.replace('<section id="scenarios">',
                            '<section id="case-execution-summary"><div id="case-execution-metadata"></div>'
                            '<button id="case-compare">Comparar execução</button></section><section id="scenarios">', 1)
    requests = []
    state = {"key": "revisions-v2", "markup": markup}
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        context, page = await connected_page(browser, markup, state, requests)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            await expect(page.locator("#case-execution-list button")).to_have_count(4)
            await expect(page.locator("#case-executions-all")).to_have_count(0)
            await expect(page.locator("#case-history, #case-edit, #result-more")).to_have_count(0)
            await expect(page.locator('#case-execution-list [aria-pressed="true"]')).to_have_attribute("data-execution-version", "2")
            await expect(page.locator("#case-execution-summary, #case-execution-metadata, #case-compare")).to_have_count(0)
            await expect(page.locator('#case-execution-list [data-execution-version="3"]')).to_contain_text("Mais recente")
            await page.locator('#case-execution-list [data-execution-version="0"]').click()
            await expect(page.locator("#case-title")).to_have_text("Primeira execução")
            await expect(page.locator("#case-selected-execution")).to_be_visible()
            await expect(page.locator("#scenarios")).not_to_be_visible()
            await expect(page.locator('#case-selected-execution figure img[src^="data:"]')).to_have_attribute("src", images["root"])
            await expect(page.locator("#case-selected-execution .case-inline-insert")).to_have_count(0)
            await expect(page.locator("#case-selected-execution .case-editor-prototype")).to_have_count(0)
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
            for identifier in ("case-executions-all", "case-history", "case-edit", "result-more", "case-execution-summary", "case-execution-metadata", "case-compare"):
                assert f'id="{identifier}"' not in exported_path.read_text()
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
            await expect(page.locator("#case-editor")).to_be_visible()
            assert await page.evaluate("window.__opened") == []
            await page.locator("#case-editor-rerun").click()
            assert await page.evaluate("window.__opened") == [[ORIGIN + "/#/reexecutar/old-rerun/CT001", "_blank", "noopener,noreferrer"]]
            await page.locator("#case-editor-close").click()
            await page.locator('#case-execution-list [data-execution-version="3"]').click()
            await expect(page.locator("#case-selected-execution")).to_contain_text("Cancelada")
            await expect(page.locator("#case-rerun")).to_be_disabled()
            await expect(page.locator("#case-selected-execution .timeline-entry")).to_have_count(0)
            await page.get_by_role("button", name="Ver último resultado disponível", exact=True).click()
            await expect(page.locator("#case-title")).to_have_text("Resultado mais recente")
            await expect(page.locator("#case-rerun")).to_be_enabled()
            await expect(page.locator('#scenarios details[data-case-index="0"] figure img[src^="data:"]')).to_have_attribute("src", images["latest-rerun"])
            await expect(page.locator("#case-history-archive details[data-history-case-index]")).to_have_count(2)
            await page.locator("#case-back").click()
            await page.locator('#case-rows tr[data-case-index="1"] td').nth(2).click()
            await expect(page.locator("#case-title")).to_have_text("Outro CT não reexecutado")
            await expect(page.locator("#case-execution-list button")).to_have_count(1)
            await expect(page.locator(".execution-history-track")).to_have_attribute("data-more-executions", "false")
            await no_duplicate_ids(page)
            assert not errors
            assert all(method == "GET" for method, _ in requests)
        finally:
            await context.close()
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 1000}, {"width": 390, "height": 844}], ids=["desktop", "mobile"])
async def test_execution_timeline_scrolls_all_attempts_and_keeps_an_older_selection(tmp_path, viewport):
    from test_report_execution_history import revision

    run = fixture()
    run["scenarios"][0]["execution_history"].extend(
        revision(version, f"no-result-{version}", None, run_status="cancelled")
        for version in range(4, 25)
    )
    source, _, _ = rendered_report(tmp_path)
    StandaloneHtmlReport(tmp_path).generate(run, str(source))
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True, ignore_default_args=["--hide-scrollbars"])
        page = await browser.new_page(viewport=viewport)
        page.set_default_timeout(5000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            await page.goto(source.as_uri() + "#view/results/CT147")
            track = page.locator("#case-execution-list")
            edge = page.locator(".execution-history-track")
            await expect(track.locator("button")).to_have_count(25)
            await expect(page.locator("#case-executions-all")).to_have_count(0)
            assert await track.locator("button").evaluate_all(
                "buttons => buttons.map(button => Number(button.dataset.executionVersion))"
            ) == list(range(24, -1, -1))
            assert await track.evaluate("el => el.scrollWidth > el.clientWidth")
            assert await track.evaluate("el => el.scrollHeight <= el.clientHeight + 1")
            assert await track.locator("li").evaluate_all(
                "items => new Set(items.map(item => Math.round(item.getBoundingClientRect().top))).size === 1"
            )
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert await track.evaluate("""el => {
              const selected = el.querySelector('[aria-pressed="true"]').getBoundingClientRect();
              const bounds = el.getBoundingClientRect();
              return selected.left >= bounds.left - 1 && selected.right <= bounds.right + 1;
            }""")
            await track.evaluate("el => el.scrollTo({left: 0, behavior: 'instant'})")
            await expect(edge).to_have_attribute("data-previous-executions", "false")
            await expect(edge).to_have_attribute("data-scrollable", "true")
            assert await edge.evaluate("el => getComputedStyle(el, '::before').opacity === '1'")
            await expect(edge).to_have_attribute("data-more-executions", "true")
            assert await edge.evaluate("el => getComputedStyle(el, '::after').opacity === '1'")
            assert await edge.evaluate("el => getComputedStyle(el, '::after').backdropFilter === 'blur(3px)'")
            assert await edge.evaluate("el => getComputedStyle(el, '::after').pointerEvents === 'none'")
            await page.locator("#case-execution-history").screenshot(path=str(tmp_path / f"execution-blur-{viewport['width']}.png"))

            # The blur stays fixed in a reserved edge gutter. At tiny offsets
            # only the part of the card actually entering that gutter is affected.
            async def left_edge_geometry():
                return await edge.evaluate("""el => {
                  const style = getComputedStyle(el, '::before');
                  const first = el.querySelector('.execution-card').getBoundingClientRect();
                  const bounds = el.getBoundingClientRect();
                  return {width: parseFloat(style.width), left: style.left, opacity: style.opacity,
                    cardLeft: first.left - bounds.left};
                }""")

            initial_edge = await left_edge_geometry()
            assert initial_edge["cardLeft"] >= initial_edge["width"]
            for offset in (3, 10, 20):
                await track.evaluate("(el, offset) => el.scrollLeft = offset", offset)
                current_edge = await left_edge_geometry()
                assert {key: current_edge[key] for key in ("width", "left", "opacity")} == {
                    key: initial_edge[key] for key in ("width", "left", "opacity")
                }
                assert abs(current_edge["cardLeft"] - (initial_edge["cardLeft"] - offset)) <= 1
                assert max(0, current_edge["width"] - current_edge["cardLeft"]) <= offset + 1
                if offset == 3:
                    assert current_edge["cardLeft"] >= current_edge["width"] - 1
                    await page.locator("#case-execution-history").screenshot(path=str(tmp_path / f"execution-tiny-scroll-{viewport['width']}.png"))
            await track.evaluate("el => el.scrollLeft = 0")

            # A normal mouse emits vertical deltas, including small wheel ticks.
            # Handle the heading and cards, without scrolling the whole page.
            await page.locator("#case-execution-history-title").hover()
            page_top = await page.evaluate("scrollY")
            await page.mouse.wheel(0, 100)
            await page.wait_for_function("document.getElementById('case-execution-list').scrollLeft >= 99")
            assert await page.evaluate("scrollY") == page_top
            await page.mouse.wheel(0, -60)
            await page.wait_for_function("Math.abs(document.getElementById('case-execution-list').scrollLeft - 40) <= 1")
            await track.locator("button").first.hover()
            card_start = await track.evaluate("el => el.scrollLeft")
            await page.mouse.wheel(0, 60)
            await page.wait_for_function("target => Math.abs(document.getElementById('case-execution-list').scrollLeft - target) <= 1", arg=card_start + 60)
            assert await page.evaluate("scrollY") == page_top
            await track.evaluate("el => el.scrollLeft = 100")
            await expect(edge).to_have_attribute("data-previous-executions", "true")
            assert await edge.evaluate("el => getComputedStyle(el, '::before').opacity === '1'")
            assert await edge.evaluate("""el => {
              const left = getComputedStyle(el, '::before'), right = getComputedStyle(el, '::after');
              return left.backdropFilter === right.backdropFilter && left.width === right.width && left.pointerEvents === 'none';
            }""")
            await page.locator("#case-execution-history").screenshot(path=str(tmp_path / f"execution-both-blurs-{viewport['width']}.png"))

            async def wheel_event(options, target=None):
                return await (target or page.locator("#case-execution-history")).evaluate("""(region, options) => {
                  const list = document.getElementById('case-execution-list');
                  const event = new WheelEvent('wheel', {bubbles: true, cancelable: true, ...options});
                  region.dispatchEvent(event);
                  return {prevented: event.defaultPrevented, left: list.scrollLeft};
                }""", options)

            # Leave horizontal gestures and zoom/Shift shortcuts to the browser.
            for options in [
                {"deltaY": 100, "ctrlKey": True}, {"deltaY": 100, "metaKey": True},
                {"deltaY": 100, "shiftKey": True}, {"deltaX": 100},
                {"deltaX": 100, "deltaY": 10}, {"deltaY": 100, "cancelable": False},
            ]:
                assert await wheel_event(options) == {"prevented": False, "left": 100}
            assert await wheel_event({"deltaY": 3, "deltaMode": 1}) == {"prevented": True, "left": 148}
            page_step = await track.evaluate("el => el.clientWidth")
            assert await wheel_event({"deltaY": 1, "deltaMode": 2}) == {"prevented": True, "left": 148 + page_step}
            # Events outside the history and at its edges must not be trapped.
            before = await track.evaluate("el => el.scrollLeft")
            assert await wheel_event({"deltaY": 100}, page.locator("#case-title")) == {"prevented": False, "left": before}
            await track.evaluate("el => el.scrollLeft = 0")
            assert await wheel_event({"deltaY": -100}) == {"prevented": False, "left": 0}
            await expect(edge).to_have_attribute("data-previous-executions", "false")
            await track.evaluate("el => el.scrollLeft = el.scrollWidth")
            end = await track.evaluate("el => el.scrollLeft")
            assert await wheel_event({"deltaY": 100}) == {"prevented": False, "left": end}
            await track.evaluate("el => el.scrollLeft = 0")
            # Native horizontal input still works independently of the mapping.
            await track.hover()
            await page.mouse.wheel(1500, 0)
            await page.wait_for_function("document.getElementById('case-execution-list').scrollLeft > 100")
            await page.locator('#case-execution-list [data-execution-version="0"]').focus()
            await page.keyboard.press("Enter")
            await expect(page.locator("#case-title")).to_have_text("Primeira execução")
            await expect(page.locator('#case-execution-list [data-execution-version="0"]')).to_be_focused()
            await expect(track.locator("button")).to_have_count(25)
            await expect(page.locator('#case-execution-list [data-execution-version="0"]')).to_have_attribute("aria-pressed", "true")
            await expect(edge).to_have_attribute("data-more-executions", "false")
            await expect(edge).to_have_attribute("data-previous-executions", "true")
            assert await edge.evaluate("""el => {
              const last = el.querySelector('.execution-history-item:last-child .execution-card').getBoundingClientRect();
              const bounds = el.getBoundingClientRect();
              const width = parseFloat(getComputedStyle(el, '::after').width);
              return last.right <= bounds.right - width + 1;
            }""")
            assert page.url.endswith("?execution=0")
            await page.screenshot(path=str(tmp_path / f"execution-scroll-{viewport['width']}.png"))
            await page.reload()
            await expect(page.locator("#case-title")).to_have_text("Primeira execução")
            assert await track.evaluate("""el => {
              const selected = el.querySelector('[aria-pressed="true"]').getBoundingClientRect();
              const bounds = el.getBoundingClientRect();
              return el.scrollLeft > 0 && selected.left >= bounds.left - 1 && selected.right <= bounds.right + 1;
            }""")
            await no_duplicate_ids(page)
            await page.goto(source.as_uri() + "#view/results/CT148")
            await expect(track.locator("button")).to_have_count(1)
            assert await wheel_event({"deltaY": 100}) == {"prevented": False, "left": 0}
            await expect(edge).to_have_attribute("data-previous-executions", "false")
            await expect(edge).to_have_attribute("data-scrollable", "false")
            assert await edge.evaluate("el => getComputedStyle(el, '::before').opacity === '0'")
            assert not errors
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
            await expect(page.locator("#case-editor")).to_be_visible()
            assert await page.evaluate("window.__opened") == []
            await page.locator("#case-editor-rerun").click()
            opened = await page.evaluate("window.__opened")
            assert opened == [[ORIGIN + "/#/reexecutar/latest-rerun/CT001", "_blank", "noopener,noreferrer"]]
            await page.locator("#case-editor-close").click()
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
            await page.locator('#case-execution-list [data-execution-version="0"]').click()
            location = page.url
            state["key"] = "revisions-v3"
            await page.evaluate("window.dispatchEvent(new Event('focus'))")
            await expect(page.get_by_role("button", name="Atualizar resultados", exact=True)).to_be_attached()
            await expect(page.locator("#case-selected-execution")).to_be_visible()
            await expect(page.locator("#case-title")).to_have_text("Primeira execução")
            assert ("GET", "/api/explorations/root/report") not in requests
            # The existing version stays fully readable before the user agrees.
            await page.get_by_role("button", name="Atualizar resultados", exact=True).click()
            await expect(page.locator("#case-title")).to_have_text("Primeira execução")
            await expect(page.locator('#case-rows tr[data-case-index="0"]')).to_contain_text("Mais novo resultado após reexecução")
            assert page.url == location
            await no_duplicate_ids(page)
        finally:
            await context.close()
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}], ids=["desktop", "mobile"])
async def test_selected_history_layout_stays_inside_viewport(tmp_path, viewport):
    source, _, _ = rendered_report(tmp_path)
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        page = await browser.new_page(viewport=viewport)
        try:
            await page.goto(source.as_uri() + "#view/results/CT147")
            for version in (0, 1):
                await page.locator(f'#case-execution-list [data-execution-version="{version}"]').click()
                workspace = page.locator("#case-selected-execution .case-workspace")
                await expect(workspace).to_be_visible()
                box = await workspace.bounding_box()
                assert box and box["x"] >= 0
                assert box["x"] + box["width"] <= viewport["width"] + 1
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert await workspace.evaluate("el => el.scrollWidth <= el.clientWidth + 1")
                await expect(page.locator("#case-execution-summary, #case-compare")).to_have_count(0)
                await no_duplicate_ids(page)
                version_id = "original" if version == 0 else "rerun-1"
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
            await page.locator("#case-rerun").click()
            await page.locator(".case-editor-insert").first.click()
            await page.locator('#step-picker [data-step-choice="wait_print"]').click()
            await page.locator("#case-editor-save").click()
            await page.locator("#case-editor-close").click()
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
            state["key"] = "revisions-v3"
            await page.evaluate("window.dispatchEvent(new Event('focus'))")
            async with asyncio.timeout(5):
                await requested.wait()
            await page.locator('#case-execution-list [data-execution-version="0"]').click()
            location = page.url
            release.set()
            await expect(page.get_by_role("button", name="Atualizar resultados", exact=True)).to_be_attached()
            await expect(page.locator("#case-selected-execution")).to_be_visible()
            await expect(page.locator("#case-title")).to_have_text("Primeira execução")
            assert page.url == location
            assert json.loads(await page.locator("#report-data").text_content())["scenario_revisions_key"] == "revisions-v2"
            await no_duplicate_ids(page)
        finally:
            release.set()
            await context.close()
            await browser.close()
