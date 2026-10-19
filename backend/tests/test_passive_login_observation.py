"""Passive auth polling reads current DOM without submitting or making evidence."""
from copy import deepcopy
from dataclasses import asdict

import pytest
from playwright.async_api import async_playwright

from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.domain.models import AgentDecision, ExplorationOptions
from autojev.services.exploration import ExplorationManager
from autojev.services.rerun_recipe import build_rerun_recipe, resolve_rerun_decision


TARGET = "https://passive-fixture.test/inventory.html"
LOGIN = """<!doctype html><title>Login</title><main>
<form onsubmit="event.preventDefault(); window.fixtureSubmissions++;
history.pushState({}, '', '/inventory.html'); document.body.innerHTML = window.fixtureInventory;
document.title = 'Products'">
<label>Username <input autocomplete="username" name="user" placeholder="Username"></label>
<label>Password <input autocomplete="current-password" name="password" type="password"></label>
<button type="submit">Login</button></form></main>"""
INVENTORY = """<main><h1>Products</h1><label>Sort products
<select id="sort" aria-label="Sort products">
<option value="az">Name (A to Z)</option><option value="za">Name (Z to A)</option>
</select></label></main>"""


@pytest.fixture
async def passive_page(tmp_path):
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        await page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=LOGIN))
        await page.goto("https://passive-fixture.test/")
        await page.evaluate("html => { window.fixtureInventory = html; window.fixtureSubmissions = 0; }", INVENTORY)
        executor = PlaywrightExecutor(tmp_path, ExplorationOptions(capture_screenshots=True))
        executor._browser, executor._page = browser, page
        captured = []

        async def record_capture(label, **kwargs):
            captured.append(label)
            return "regular-evidence.png"

        executor._capture = record_capture
        try:
            yield executor, page, captured
        finally:
            await executor.close()
            await browser.close()


@pytest.mark.asyncio
async def test_passive_login_probe_preserves_technical_signals_and_does_not_submit(passive_page):
    executor, page, captured = passive_page
    executor._console_buffer.append({"type": "error", "text": "public fixture console error"})
    executor._network_buffer.append({"url": "https://passive-fixture.test/api/products", "status": 500})
    console, network = deepcopy(executor._console_buffer), deepcopy(executor._network_buffer)
    await page.get_by_placeholder("Username").fill("public-fixture-user")
    await page.locator('input[type="password"]').fill("public-fixture-password")
    for _ in range(3):
        observation = await executor.observe_preparation()
        assert ExplorationManager._needs_login(observation)
        assert observation.screenshot is None
        assert observation.console_errors == observation.network_errors == []
        assert "public-fixture-password" not in str(asdict(observation))
    assert await page.evaluate("window.fixtureSubmissions") == 0
    assert not captured
    assert executor._console_buffer == console and executor._network_buffer == network

    regular = await executor.observe("regular")
    assert regular.screenshot == "regular-evidence.png" and captured == ["regular"]
    assert regular.console_errors == list(console) and regular.network_errors == list(network)
    assert not executor._console_buffer and not executor._network_buffer


@pytest.mark.asyncio
async def test_native_login_navigation_is_detected_without_old_warning_or_stale_handles(passive_page):
    executor, page, captured = passive_page
    before_login = await executor.observe_preparation()
    assert ExplorationManager._needs_login(before_login)
    # This is the user's own native site click, not the AutoJev Continue CTA.
    await page.get_by_role("button", name="Login", exact=True).click()
    after_login = await executor.observe_preparation()
    assert after_login.url == TARGET
    assert not ExplorationManager._needs_login(after_login)
    assert await page.evaluate("window.fixtureSubmissions") == 1 and not captured
    select = next(control for control in after_login.interactive_elements if control.get("tag") == "select")
    recipe = build_rerun_recipe(after_login, AgentDecision("select", "Change sorting", target=select["ref"], value="za"), TARGET)
    assert recipe["enabled"]
    # A regular fresh observation rebinds the exact control after polling.
    fresh = await executor.observe("rerun_before")
    decision = resolve_rerun_decision(recipe, fresh, TARGET)
    assert await (await executor._locator(decision.target)).get_attribute("id") == "sort"
    assert captured == ["rerun_before"]
