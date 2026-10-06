"""A positional ref must never execute against another homonymous owner."""

from contextlib import asynccontextmanager

import pytest
from playwright.async_api import async_playwright

from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.domain.models import AgentDecision, ExplorationOptions
from autojev.services.action_dispatch import is_stale_undispatched_action
from autojev.services.exploration import ExplorationManager


@asynccontextmanager
async def observed_page(tmp_path, html):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.route("**/*", lambda route: route.fulfill(
                content_type="text/html", body=html))
            await page.goto("https://owner.fixture.test/catalog")
            executor = PlaywrightExecutor(
                tmp_path, ExplorationOptions(capture_screenshots=False))
            executor._browser, executor._page, executor._context = (
                browser, page, page.context)
            executor._allowed_origin = executor._origin(page.url)
            yield executor, page
        finally:
            await browser.close()


def products(*, declared=True):
    marker_a = 'data-product-id="sku-a"' if declared else ''
    marker_b = 'data-product-id="sku-b"' if declared else ''
    return f'''<script>window.clicked=[]</script><main>
      <article {marker_a}><h2>Desk Lamp</h2><button data-test="toggle"
        onclick="clicked.push('sku-a');this.textContent='Remove'">Add to cart</button></article>
      <article {marker_b}><h2>Desk Lamp</h2><button data-test="toggle"
        onclick="clicked.push('sku-b');this.textContent='Remove'">Add to cart</button></article>
    </main>'''


@pytest.mark.parametrize("declared", [True, False])
async def test_reordered_homonymous_owner_rejected_before_native_click(tmp_path, declared):
    async with observed_page(tmp_path, products(declared=declared)) as (executor, page):
        before = await executor.observe()
        original = before.interactive_elements[0]
        await page.evaluate("() => document.querySelector('main').append(document.querySelector('article'))")
        result, refreshed = await executor.execute(
            AgentDecision(action="click", target=original["ref"], rationale="Add first product"), {})
        assert not result.success
        assert "re-observation is required" in result.message
        assert result.evidence["action_dispatch"] == {
            "status": "not_executed", "reason": "stale_observed_control", "retryable": True,
        }
        assert is_stale_undispatched_action(result)
        assert await page.evaluate("window.clicked") == []
        assert await page.locator("button").all_text_contents() == ["Add to cart", "Add to cart"]
        assert refreshed.interactive_elements
        # One fresh observation is enough to target the original, now second,
        # node. Do not approve or execute its homonymous neighbour instead.
        fresh_ref = refreshed.interactive_elements[1]["ref"]
        recovered, _ = await executor.execute(
            AgentDecision(action="click", target=fresh_ref, rationale="Add original product"), {})
        assert recovered.success
        assert await page.evaluate("window.clicked") == ["sku-a"]


async def test_pinned_action_survives_reorder_after_guard(tmp_path):
    async with observed_page(tmp_path, products()) as (executor, page):
        original = (await executor.observe()).interactive_elements[0]
        checked = await executor._locator(original["ref"])
        await page.evaluate("() => document.querySelector('main').append(document.querySelector('article'))")
        await checked.click(timeout=1_000)
        assert await page.evaluate("window.clicked") == ["sku-a"]
        assert await page.locator('[data-product-id="sku-b"] button').inner_text() == "Add to cart"
        assert await page.locator("[data-autojev-ref]").count() == 0
        handles = list(executor._observed_element_handles)
        await executor.observe()
        assert executor._observed_element_handles == []
        assert not page.is_closed()
        with pytest.raises(Exception, match="disposed|closed"):
            await handles[0].evaluate("node => node.tagName")


async def test_reused_node_cannot_transfer_to_another_declared_entity(tmp_path):
    html = '<article data-item-id="order-a"><button id="edit">Edit</button></article>'
    async with observed_page(tmp_path, html) as (executor, page):
        original = (await executor.observe()).interactive_elements[0]
        await page.locator("article").evaluate("node => node.setAttribute('data-item-id','order-b')")
        with pytest.raises(ValueError, match="re-observation"):
            await executor._locator(original["ref"])


async def test_same_control_moved_to_another_owner_is_not_same_observation(tmp_path):
    html = '<main><article><button id="edit">Edit</button></article><article><h2>Other</h2></article></main>'
    async with observed_page(tmp_path, html) as (executor, page):
        original = (await executor.observe()).interactive_elements[0]
        # Keep the selector pointing to a button while changing its ownership.
        # The actual node token remains unchanged; the owner token must differ.
        await page.evaluate("""() => {
          const first=document.querySelector('article'),second=first.nextElementSibling;
          second.append(first.querySelector('button'));
          first.replaceWith(second);
        }""")
        with pytest.raises(ValueError, match="re-observation"):
            await executor._locator(original["ref"])


async def test_identical_replacement_requires_refresh(tmp_path):
    html = '<main><button id="toggle">Add to cart</button></main>'
    async with observed_page(tmp_path, html) as (executor, page):
        original = (await executor.observe()).interactive_elements[0]
        await page.locator("button").evaluate("node => node.replaceWith(node.cloneNode(true))")
        with pytest.raises(ValueError, match="re-observation"):
            await executor._locator(original["ref"])
        refreshed = (await executor.observe()).interactive_elements[0]
        await executor._locator(refreshed["ref"])


async def test_identical_document_reload_does_not_reuse_node_tokens(tmp_path):
    html = '<main><button id="toggle">Add to cart</button></main>'
    async with observed_page(tmp_path, html) as (executor, page):
        original = (await executor.observe()).interactive_elements[0]
        await page.reload()
        with pytest.raises(ValueError, match="re-observation"):
            await executor._locator(original["ref"])


async def test_feedback_text_change_does_not_invalidate_same_control(tmp_path):
    html = '<form><p id="feedback">Enter details</p><button type="button" id="dismiss">Dismiss error</button></form>'
    async with observed_page(tmp_path, html) as (executor, page):
        original = (await executor.observe()).interactive_elements[0]
        await page.locator("#feedback").evaluate("node => node.textContent='Error: postal code is required'")
        await executor._locator(original["ref"])


async def test_internal_guard_is_not_exported_to_observation(tmp_path):
    html = '<article data-item-id="private-owner"><button id="edit">Edit</button></article>'
    async with observed_page(tmp_path, html) as (executor, page):
        executor.secret_values.append("private-owner")
        observation = await executor.observe()
        control = observation.interactive_elements[0]
        assert "_node_guard" not in control
        assert "_node_guard" in executor._ref_semantics[control["ref"]]
        assert executor._observed_node_guard_key not in repr(observation)
        assert "private-owner" not in repr(observation)
        assert await page.locator("[data-autojev-ref]").count() == 0


async def test_runtime_error_after_native_dispatch_remains_execution_failure(tmp_path):
    async with observed_page(tmp_path, products()) as (executor, page):
        before = await executor.observe()
        action = AgentDecision(action="click", target=before.interactive_elements[0]["ref"], rationale="Add product")
        async def failed_settle(*_args, **_kwargs):
            raise RuntimeError("stale reference message after an actual dispatch")
        executor._wait_for_dom_settle = failed_settle
        result, after = await executor.execute(action, {})
        assert await page.evaluate("window.clicked") == ["sku-a"]
        assert await page.locator('[data-product-id="sku-a"] button').inner_text() == "Remove"
        assert result.success is False
        assert "action_dispatch" not in result.evidence
        assert not is_stale_undispatched_action(result)
        assert ExplorationManager._scenario_capture_policy(
            decision_source="jev", is_restoration_action=False, result=result) == (True, None)
        assert ExplorationManager._scenario_from_evidence(after, action, result, 1).status.value == "FAIL"
