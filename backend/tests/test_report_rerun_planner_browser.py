"""Isolated planner QA: only intercepted GETs; never start a user execution."""
import json
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.async_api import async_playwright, expect
from test_report_revision_browser import ORIGIN, connected_page, no_duplicate_ids, rendered_report


async def drag_across_track(page, source, target, screenshot_path=None):
    # Start the native drag before revealing the far destination, so scrolling
    # the track cannot replace the card underneath the initial pointer position.
    await source.scroll_into_view_if_needed()
    start = await source.bounding_box()
    x, y = start["x"] + 24, start["y"] + 24
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x + 12, y + 4, steps=3)
    await target.scroll_into_view_if_needed()
    end = await target.bounding_box()
    await page.mouse.move(end["x"] + end["width"] / 2, end["y"] + 110, steps=8)
    await page.mouse.move(end["x"] + end["width"] / 2, end["y"] + 110)
    if screenshot_path:
        await page.screenshot(path=str(screenshot_path))
    await expect(target).to_have_class(re.compile(r"\bdrag-over\b"))
    highlighted = await target.bounding_box()
    card = await page.locator("#case-editor-steps .case-editor-recorded").first.bounding_box()
    assert highlighted["height"] <= card["height"] + 1
    assert abs(highlighted["y"] - card["y"]) <= 1
    assert abs(highlighted["height"] - end["height"]) <= 1
    insert = await target.locator(".case-editor-insert").bounding_box()
    assert abs((insert["y"] + insert["height"] / 2) - (highlighted["y"] + highlighted["height"] / 2)) <= 1
    await page.mouse.up()


async def open_planner(page):
    await page.evaluate("() => { window.__opened = []; window.open = (...args) => { window.__opened.push(args); return null; }; }")
    await page.locator("#case-rerun").click()
    await expect(page.locator("#case-editor")).to_be_visible()
    assert await page.evaluate("window.__opened") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 1000}, {"width": 390, "height": 844}], ids=["desktop", "mobile"])
async def test_planner_click_drag_reorder_and_explicit_confirmation_preserve_results(tmp_path, viewport):
    _, markup, images = rendered_report(tmp_path)
    requests = []
    state = {"key": "revisions-v2", "markup": markup}
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True, ignore_default_args=["--hide-scrollbars"])
        context, page = await connected_page(browser, markup, state, requests)
        await page.set_viewport_size(viewport)
        page.set_default_timeout(5000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            await expect(page.locator(".case-inline-insert, .case-inline-gap")).to_have_count(0)
            original = page.locator('#scenarios details[data-case-index="0"]')
            count = await original.locator(".timeline-entry").count()
            source = await page.locator("#report-data").text_content()
            await open_planner(page)
            planner = page.locator("#case-editor")
            steps = page.locator("#case-editor-steps")
            await expect(steps.locator(".case-editor-recorded")).to_have_count(count)
            await expect(steps.locator(".case-editor-insert")).to_have_count(count + 1)
            assert await steps.locator(".case-editor-insert").all_text_contents() == ["+"] * (count + 1)
            assert await steps.locator(".case-editor-recorded").evaluate_all(
                "cards => new Set(cards.map(card => Math.round(card.getBoundingClientRect().top))).size === 1")
            assert await steps.locator(".case-editor-recorded").evaluate_all(
                "cards => cards.every(card => !card.draggable)")
            await expect(planner.locator('[data-step-template]')).to_have_count(3)
            await steps.locator(".case-editor-insert").nth(1).click()
            await page.locator('#step-picker [data-step-choice="wait_print"]').click()
            await expect(steps.locator('[data-draft-type="wait_print"]')).to_have_count(1)
            # New templates and existing draft cards can both be dropped on +.
            await drag_across_track(
                page, planner.locator('[data-step-template="sws"]'), steps.locator('[data-editor-gap="0"]'),
                tmp_path / f"rerun-planner-drag-{viewport['width']}.png",
            )
            await expect(steps.locator('[data-draft-type="sws"]')).to_have_count(1)
            await planner.locator('[data-step-template="sql"]').click()
            await steps.locator(".case-editor-insert").last.click()
            sql = steps.locator('[data-draft-type="sql"]')
            await sql.locator(".case-editor-wait-print").check()
            await expect(sql.locator(".case-draft-disabled-action")).to_be_disabled()
            await page.locator("#case-editor-save").click()
            saved = json.loads(await page.locator("#report-drafts").text_content())["cases"]["0"]
            assert [(item["type"], item["gap"]) for item in saved] == [("sws", 0), ("wait_print", 1), ("sql", 2)]
            await drag_across_track(page, steps.locator('[data-draft-type="sws"]'), steps.locator(".case-editor-gap").last)
            if viewport["width"] > 700:
                await steps.hover()
                start_left = await steps.evaluate("el => el.scrollLeft")
                await page.mouse.wheel(0, -80)
                await page.wait_for_function("start => document.getElementById('case-editor-steps').scrollLeft < start - 50", arg=start_left)
            # No handoff occurs until the footer confirmation is explicitly used.
            assert await page.evaluate("window.__opened") == []
            assert await page.locator("#report-data").text_content() == source
            await expect(original.locator(".timeline-entry")).to_have_count(count)
            await expect(original.locator('figure img[src^="data:"]')).to_have_attribute("src", images["latest-rerun"])
            box = await planner.bounding_box()
            assert box and box["x"] >= 0 and box["y"] >= 0
            assert box["x"] + box["width"] <= viewport["width"] + 1
            assert box["y"] + box["height"] <= viewport["height"] + 1
            assert await planner.evaluate("el => el.scrollWidth <= el.clientWidth + 1")
            await page.screenshot(path=str(tmp_path / f"rerun-planner-{viewport['width']}.png"))
            await page.locator("#case-editor-rerun").click()
            opened = await page.evaluate("window.__opened")
            assert len(opened) == 1
            assert opened[0][1:] == ["_blank", "noopener,noreferrer"]
            target = opened[0][0]
            assert target.startswith(ORIGIN + "/#/reexecutar/latest-rerun/CT001?")
            plan = json.loads(parse_qs(target.split("?", 1)[1])["capture_plan"][0])
            assert [(item["gap"], item["order"]) for item in plan["checkpoints"]] == [(1, 0), (2, 1)]
            assert set(plan) == {"version", "checkpoints"}
            assert all(set(item) == {"id", "gap", "order"} for item in plan["checkpoints"])
            await page.locator("#case-editor-close").click()
            await expect(page.locator("#case-rerun")).to_be_focused()
            await expect(page.locator(".case-inline-insert, .case-inline-draft")).to_have_count(0)
            await page.locator("#case-rerun").click()
            await expect(steps.locator(".case-editor-prototype")).to_have_count(3)
            await no_duplicate_ids(page)
            assert not errors
            assert all(method == "GET" for method, _ in requests)
        finally:
            await context.close()
            await browser.close()


@pytest.mark.asyncio
async def test_planner_cancel_export_and_historical_plans_are_isolated(tmp_path):
    _, markup, _ = rendered_report(tmp_path, with_journey=True)
    requests = []
    state = {"key": "revisions-v2", "markup": markup}
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        context, page = await connected_page(browser, markup, state, requests)
        page.set_default_timeout(5000)
        try:
            await open_planner(page)
            await page.locator(".case-editor-insert").first.click()
            await page.locator('#step-picker [data-step-choice="wait_print"]').click()
            await page.locator("#case-editor-close").click()
            await page.locator("#case-rerun").click()
            await expect(page.locator(".case-editor-prototype")).to_have_count(0)
            await page.locator(".case-editor-insert").nth(1).click()
            await page.locator('#step-picker [data-step-choice="wait_print"]').click()
            await page.locator("#case-editor-save").click()
            await page.locator("#case-editor-close").click()
            await page.locator('#case-execution-list [data-execution-version="1"]').click()
            await open_planner(page)
            await expect(page.locator("#case-editor-title")).to_contain_text("Tentativa anterior")
            await expect(page.locator(".case-editor-prototype")).to_have_count(0)
            await page.locator(".case-editor-insert").first.click()
            await page.locator('#step-picker [data-step-choice="wait_print"]').click()
            await page.locator("#case-editor-rerun").click()
            opened = await page.evaluate("window.__opened")
            assert len(opened) == 1 and "/#/reexecutar/old-rerun/CT001?" in opened[0][0]
            plan = json.loads(parse_qs(opened[0][0].split("?", 1)[1])["capture_plan"][0])
            assert [checkpoint["gap"] for checkpoint in plan["checkpoints"]] == [0]
            drafts = json.loads(await page.locator("#report-drafts").text_content())["cases"]
            assert drafts["0"][0]["gap"] == 1
            assert drafts["history:0:1"][0]["gap"] == 0
            await page.locator("#case-editor-close").click()
            async with page.expect_download() as pending:
                await page.locator("#download-report").click()
            export = tmp_path / "planner-offline.html"
            await (await pending.value).save_as(export)
            offline_context = await browser.new_context()
            offline_requests = []
            offline_context.on("request", lambda request: offline_requests.append(request.url))
            offline = await offline_context.new_page()
            offline.set_default_timeout(5000)
            await offline.goto(export.as_uri() + "#view/results/CT147")
            await expect(offline.locator("#case-rerun, .case-inline-insert")).to_have_count(0)
            await offline.locator('[data-report-nav="journey"]').click()
            await offline.locator('[data-journey-page]').first.click()
            await offline.locator('[data-edit-case="0"]:visible').click()
            await expect(offline.locator(".case-editor-prototype")).to_have_count(1)
            assert json.loads(await offline.locator("#report-drafts").text_content())["cases"] == drafts
            await no_duplicate_ids(offline)
            assert not [url for url in offline_requests if urlsplit(url).scheme in {"http", "https"}]
            await offline_context.close()
            assert all(method == "GET" for method, _ in requests)
        finally:
            await context.close()
            await browser.close()


@pytest.mark.asyncio
async def test_running_backend_old_editor_shell_gets_the_new_planner_without_restart(tmp_path):
    _, markup, _ = rendered_report(tmp_path)
    markup = markup.replace('class="case-editor-dialog case-rerun-planner"', 'class="case-editor-dialog"')
    markup = re.sub(r'(<aside id="case-editor-palette"[^>]*>).*?(</aside>)',
                    r'\1<button id="database-step-card" draggable="true">Consulta antiga</button><p>Adicionar aqui</p>\2',
                    markup, count=1, flags=re.DOTALL)
    markup = markup.replace('id="case-editor-save" class="button button-ghost"', 'id="case-editor-save" class="button button-primary"')
    markup = markup.replace('id="case-editor-rerun" class="button button-primary"', 'id="case-editor-rerun" class="button button-ghost"')
    requests = []
    state = {"key": "revisions-v2", "markup": markup}
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        context, page = await connected_page(browser, markup, state, requests)
        page.set_default_timeout(5000)
        try:
            await open_planner(page)
            await expect(page.locator("#case-editor")).to_have_class(re.compile("case-rerun-planner"))
            await expect(page.locator("#database-step-card")).to_have_count(0)
            await expect(page.locator("[data-step-template]")).to_have_count(3)
            await expect(page.locator("#case-editor-rerun")).to_have_class(re.compile("button-primary"))
            await expect(page.locator("#case-editor-save")).to_have_class(re.compile("button-ghost"))
            assert await page.locator("#case-editor-steps").evaluate("el => getComputedStyle(el).display === 'flex'")
            assert await page.locator(".case-editor-insert").all_text_contents() == ["+", "+", "+"]
            assert await page.evaluate("window.__opened") == []
            await no_duplicate_ids(page)
        finally:
            await context.close()
            await browser.close()
