"""Report-only loopback fixtures: no execution API or production data access."""
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.async_api import async_playwright, expect
import test_report_revision_browser as reports
from test_report_premium import fixture, render


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [1440, 390])
async def test_tests_screen_reuses_history_and_hands_selected_sources_to_planner(tmp_path, monkeypatch, width):
    monkeypatch.setattr(reports, "ORIGIN", "http://127.0.0.1:18769")
    _, markup, _ = reports.rendered_report(tmp_path)
    drafts = {"version": 1, "cases": {
        "0": [{"id": "db-1", "type": "wait_print", "gap": 1, "order": 0}],
        "history:0:1": [{"id": "db-2", "type": "wait_print", "gap": 0, "order": 0}],
    }}
    markup = markup.replace('{"version":1,"cases":{}}</script>', json.dumps(drafts) + '</script>')
    requests = []
    async with async_playwright() as driver:
        browser = await driver.chromium.launch()
        context, page = await reports.connected_page(browser, markup, {"key": "revisions-v2", "markup": markup}, requests)
        try:
            await page.set_viewport_size({"width": width, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.evaluate("() => { window.__history = document.getElementById('case-execution-history'); window.__opened = []; window.open = (...args) => { window.__opened.push(args); return null; }; }")
            await page.locator('[data-report-nav="tests"]').click()
            await expect(page.locator('#tests-execution-history #case-execution-list button')).to_have_count(4)
            assert await page.evaluate("window.__history === document.getElementById('case-execution-history')")
            await page.locator('#case-execution-list [data-execution-version="1"]').click()
            await expect(page.locator('body')).to_have_attribute('data-view', 'results')
            await expect(page.locator('#case-title')).to_have_text('Tentativa anterior')
            await page.locator('#case-back').click()
            await expect(page.locator('#case-execution-list [data-execution-version="1"]')).to_have_attribute('aria-pressed', 'true')
            await page.locator('#tests-rerun').click()
            await expect(page.locator('#case-editor')).not_to_be_visible()
            opened = await page.evaluate('window.__opened')
            batch = json.loads(parse_qs(urlsplit(opened[0][0]).fragment.split('?', 1)[1])['itens'][0])
            assert '/#/planejar-lote/root?' in opened[0][0]
            assert [(item['source'], item['ct']) for item in batch] == [('old-rerun', 'CT001'), ('root', 'CT148')]
            assert batch[0]['capture_plan']['checkpoints'] == [{'id': 'db-2', 'gap': 0, 'order': 0}]
            await page.locator('#case-rows tr[data-case-index="0"] td').first.click()
            await page.locator('#case-rerun').click()
            target = (await page.evaluate('window.__opened'))[-1][0]
            params = parse_qs(target.split('?', 1)[1])
            assert '/#/conjuntos/root?' in target
            assert {key: params[key] for key in ['tab', 'ct', 'source', 'source_ct']} == {'tab': ['planner'], 'ct': ['CT147'], 'source': ['old-rerun'], 'source_ct': ['CT001']}
            assert json.loads(params['capture_plan'][0]) == batch[0]['capture_plan']
            await reports.no_duplicate_ids(page)
            assert not errors
            assert all(method == 'GET' for method, _ in requests)
        finally:
            await context.close()
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('width', [1440, 390])
async def test_flow_dropdown_counts_filter_intersection_reset_and_responsive_layout(tmp_path, width):
    run = fixture()
    run['scenarios'][0]['title'] = 'Criar cliente'
    run['scenarios'][1]['title'] = 'Preencher campo nome'
    document = render(tmp_path, run)
    async with async_playwright() as driver:
        browser = await driver.chromium.launch()
        page = await browser.new_page(viewport={'width': width, 'height': 900})
        try:
            await page.goto('file://' + str(tmp_path / 'report.html') + '#view/tests')
            await expect(page.locator('.functional-flow-card, .functional-flows-panel')).to_have_count(0)
            flows = page.locator('#flow-filter')
            await expect(flows.locator('option[value="all"]')).to_have_text('Todos os fluxos (2)')
            await expect(flows.locator('option[value="unclassified"]')).to_have_text('Não classificados (1)')
            identity = await flows.locator('option').nth(2).get_attribute('value')
            await flows.select_option(identity)
            await expect(page.locator('#case-rows tr:not([hidden])')).to_have_count(1)
            await page.locator('#filters [data-status="FAIL"]').click()
            await expect(flows.locator('option').nth(2)).to_have_text('Cadastrar cliente (0)')
            await expect(page.locator('#case-rows tr:not([hidden])')).to_have_count(0)
            await flows.select_option('unclassified')
            await expect(page.locator('#case-rows tr:not([hidden])')).to_have_count(1)
            await page.locator('#clear-filters').click()
            await expect(flows).to_have_value('all')
            await expect(page.locator('#case-rows tr:not([hidden])')).to_have_count(2)
            await page.locator('#search').fill('nome')
            await expect(flows.locator('option[value="all"]')).to_have_text('Todos os fluxos (1)')
            box = await flows.bounding_box()
            assert 0 <= box['x'] and box['x'] + box['width'] <= width
            assert await page.locator('.report-view[data-view="tests"]').evaluate("el => getComputedStyle(el).display !== 'grid'")
            assert document.count('id="flow-filter"') == 1
        finally:
            await browser.close()
