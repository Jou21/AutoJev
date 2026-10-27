"""Disposable local pages only: no live user recording or remote application."""
import asyncio
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright, expect

from autojev.adapters.browser_recording_panel import BrowserRecordingPanel, PANEL_URL
from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.domain.models import ExplorationOptions
from autojev.services.planning_assistant import RECORD_SCRIPT
from autojev.services.planning_assistant import PlanningRecorder


@pytest.fixture
async def recording_panel(tmp_path):
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        await context.route("**/*", lambda route: route.abort("blockedbyclient"))
        await context.route("https://shop.fixture.invalid/**", lambda route: route.fulfill(content_type="text/html", body='''
          <label>Nome<input name="customer"></label><label>Senha<input name="password" type="password"></label>
          <button>Adicionar produto</button><a href="/next">Outra página</a>'''))
        steps, finishes = [], []

        async def receive(source, value): steps.append(value["step"])

        await context.expose_binding("__autojevRecordPlanningStep", receive)
        await context.add_init_script(RECORD_SCRIPT)
        page = await context.new_page()
        await page.goto("https://shop.fixture.invalid/")
        executor = PlaywrightExecutor(tmp_path, ExplorationOptions(capture_screenshots=False), headless=False)
        executor._context, executor._page = context, page

        async def finish(): finishes.append(True)

        panel = BrowserRecordingPanel(executor, ("https", "shop.fixture.invalid"),
            lambda: {"status": "finished" if finishes else "recording", "steps": list(steps)}, finish)
        await panel.start()
        try:
            yield panel, page, steps, finishes
        finally:
            await panel.close()
            await browser.close()


@pytest.mark.asyncio
async def test_J_and_recorded_steps_are_live_without_recording_controls_or_passwords(recording_panel):
    panel, page, steps, finishes = recording_panel
    await expect(page.locator(".autojev-rail-mark")).to_have_text("J")
    frame = panel.frames[page][0]
    await expect(frame.locator("h1")).to_have_text("Sua navegação")
    await page.get_by_role("button", name="Adicionar produto", exact=True).click()
    await expect(frame.locator("#count")).to_have_text("1 passo")
    assert steps == ["Clicar em «Adicionar produto»"]
    await page.locator(".autojev-panel-rail").click()
    await page.locator(".autojev-panel-rail").click()
    await page.locator('[name="password"]').fill("PRIVATE-PASSWORD")
    await page.locator('[name="customer"]').fill("PRIVATE-NAME")
    await page.locator('[name="customer"]').blur()
    await panel.refresh()
    assert "PRIVATE-" not in " ".join(steps)
    assert not any("AutoJev" in step or "Finalizar" in step or "password" in step for step in steps)
    assert finishes == []
    await page.screenshot(path=str(panel.executor.artifact_dir / "recording-panel.png"))


@pytest.mark.asyncio
async def test_finish_requires_a_native_click_in_the_owned_isolated_frame(recording_panel):
    panel, page, steps, finishes = recording_panel
    frame, _, nonce = panel.frames[page]
    assert await page.evaluate("document.getElementById('autojev-recording-frame').contentDocument === null") is True
    assert await frame.evaluate("globalThis.origin") == "null"
    assert await frame.evaluate("""async () => {try {await fetch('https://blocked.invalid/');return false;}catch(_){return true;}}""") is True
    command = {"op": "finish", "confirm": True, "nonce": nonce}
    assert await page.evaluate("""async command => {try {await window.__autojev_recording_panel(command);return false;}catch(_){return true;}}""", command) is True
    await frame.locator("#finish").dispatch_event("click")
    assert finishes == []
    await frame.locator("#finish").click()
    async with asyncio.timeout(3):
        while not finishes: await asyncio.sleep(.01)
    assert finishes == [True] and steps == []
    with pytest.raises(ValueError): await panel.command({"page": page, "frame": frame}, command)


@pytest.mark.asyncio
async def test_finish_in_real_panel_closes_only_its_browser_and_preserves_the_review_handoff(tmp_path, monkeypatch):
    from playwright.async_api import Error
    async with async_playwright() as driver:
        closed = []
        class LocalExecutor(PlaywrightExecutor):
            async def start(self, url):
                self._browser = await driver.chromium.launch(headless=True)
                self._context = await self._browser.new_context()
                await self._context.route("**/*", lambda route: route.abort("blockedbyclient"))
                await self._context.route("https://shop.fixture.invalid/**", lambda route: route.fulfill(
                    content_type="text/html", body='<button>Consultar carrinho</button>'))
                self._page = await self._context.new_page()
                await self._page.goto(url)

            async def close(self):
                closed.append(True)
                await self._browser.close()

        monkeypatch.setattr("autojev.services.planning_assistant.PlaywrightExecutor", LocalExecutor)
        recorder = PlanningRecorder(SimpleNamespace(artifact_dir=tmp_path, headless=False, browser="chromium"),
            SimpleNamespace(root=lambda identity: identity, repository=SimpleNamespace(
                get_exploration=lambda _: {"app_url": "https://shop.fixture.invalid/"})))
        try:
            value = await recorder.start("root-fixture")
            session = recorder.sessions[value["id"]]
            page, panel = session["executor"]._page, session["panel"]
            await page.get_by_role("button", name="Consultar carrinho", exact=True).click()
            await panel.refresh()
            try:
                await panel.frames[page][0].locator("#finish").click()
            except Error as error:
                assert "closed" in str(error).lower()
            async with asyncio.timeout(3):
                while recorder.sessions: await asyncio.sleep(.01)
                while session["executor"]._browser.is_connected(): await asyncio.sleep(.01)
            result = recorder.get(value["id"])
            assert result["status"] == "finished" and result["steps"] == ["Clicar em «Consultar carrinho»"]
            assert await recorder.finish(value["id"]) == result
            assert closed == [True] and panel.closed
        finally:
            await recorder.shutdown()


@pytest.mark.asyncio
async def test_navigation_restores_J_and_progress_and_revokes_old_frame_commands(recording_panel):
    panel, page, steps, finishes = recording_panel
    frame, _, nonce = panel.frames[page]
    await page.get_by_role("button", name="Adicionar produto", exact=True).click()
    await page.goto("https://shop.fixture.invalid/next")
    await panel.refresh()
    current = panel.frames[page][0]
    assert current is not frame
    await expect(page.locator(".autojev-rail-mark")).to_have_text("J")
    await expect(current.locator("#steps li")).to_have_count(1)
    with pytest.raises(ValueError):
        await panel.command({"page": page, "frame": frame}, {"op": "finish", "confirm": True, "nonce": nonce})
    assert finishes == []
    # Reloading only the UI frame also revokes commands from its old document.
    nonce = panel.frames[page][2]
    await current.goto(PANEL_URL, wait_until="domcontentloaded")
    await panel.refresh()
    with pytest.raises(ValueError):
        await panel.command({"page": page, "frame": current}, {"op": "finish", "confirm": True, "nonce": nonce})


@pytest.mark.asyncio
async def test_CSP_is_not_weakened_and_recording_keeps_the_main_app_fallback(recording_panel):
    panel, page, steps, finishes = recording_panel
    await page.goto("https://shop.fixture.invalid/restricted")
    await page.set_content('''<meta http-equiv="Content-Security-Policy" content="default-src 'none'; frame-src 'none'; script-src 'none'">Restricted app''')
    await panel.refresh()
    assert page in panel.blocked and page not in panel.frames
    assert await page.locator("#autojev-recording-frame").count() == 0
    assert "aplicação AutoJev" in await page.locator("#autojev-panel-hint").inner_text()
    await panel.refresh()
    assert await page.locator("#autojev-recording-frame").count() == 0
    assert finishes == []


@pytest.mark.asyncio
async def test_second_same_origin_window_gets_its_own_panel(recording_panel):
    panel, page, steps, finishes = recording_panel
    popup = await page.context.new_page()
    await popup.goto("https://shop.fixture.invalid/popup")
    await panel.refresh()
    assert popup in panel.frames
    assert panel.frames[popup][0] is not panel.frames[page][0]
    await expect(popup.locator(".autojev-rail-mark")).to_have_text("J")
    await popup.close()
    await panel.refresh()
    assert finishes == []


@pytest.mark.asyncio
async def test_recording_controls_fit_a_small_window_and_J_can_collapse_and_reopen(recording_panel):
    panel, page, steps, finishes = recording_panel
    await page.set_viewport_size({"width": 390, "height": 700})
    frame = panel.frames[page][0]
    await expect(frame.locator("#finish")).to_be_visible()
    bounds = await page.locator("#autojev-recording-frame").bounding_box()
    assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 390
    assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= 700
    await page.locator(".autojev-panel-rail").click()
    await expect(page.locator("#autojev-sidepanel")).to_have_attribute("data-pinned", "false")
    await page.locator(".autojev-panel-rail").click()
    await expect(page.locator("#autojev-sidepanel")).to_have_attribute("data-pinned", "true")
    assert steps == finishes == []


@pytest.mark.asyncio
async def test_close_waits_for_an_inflight_refresh_without_cancelling_browser_calls():
    panel = BrowserRecordingPanel(SimpleNamespace(), ("https", "shop.test"), lambda: {}, lambda: None)
    entered, release = asyncio.Event(), asyncio.Event()

    async def refresh():
        entered.set()
        await release.wait()

    panel.refresh = refresh
    panel.task = asyncio.create_task(panel._monitor())
    await asyncio.wait_for(entered.wait(), timeout=2)
    closing = asyncio.create_task(panel.close())
    try:
        await asyncio.sleep(0)
        assert panel.closed and not closing.done() and not panel.task.cancelled()
    finally:
        release.set()
        await closing
    assert panel.task.done() and not panel.task.cancelled()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["parent", "nonce", "op", "confirm", "detached", "closed"])
async def test_finish_rejects_untrusted_stale_or_incomplete_commands(invalid):
    page, calls = object(), []
    frame = SimpleNamespace(url=PANEL_URL, is_detached=lambda: invalid == "detached")
    async def finish(): calls.append(True)
    panel = BrowserRecordingPanel(SimpleNamespace(), ("https", "shop.test"), lambda: {}, finish)
    panel.frames[page] = (frame, None, "fixture-nonce")
    command = {"op": "finish", "confirm": True, "nonce": "fixture-nonce"}
    source = {"page": page, "frame": frame}
    if invalid == "parent": source["frame"] = object()
    if invalid == "nonce": command["nonce"] = "old"
    if invalid == "op": command["op"] = "execute"
    if invalid == "confirm": command["confirm"] = False
    if invalid == "closed": await panel.close()
    with pytest.raises(ValueError): await panel.command(source, command)
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["parent", "post", "asset", "url", "closed"])
async def test_virtual_asset_is_served_only_to_the_registered_frame(invalid):
    page, served, aborted = object(), [], []
    frame = SimpleNamespace(page=page)
    panel = BrowserRecordingPanel(SimpleNamespace(), ("https", "shop.test"), lambda: {}, lambda: None)
    panel.frames[page] = (frame, None, "nonce")
    request = SimpleNamespace(url=PANEL_URL, method="GET", frame=frame, is_navigation_request=lambda: True)
    async def abort(code): aborted.append(code)
    async def fulfill(**value): served.append(value)
    if invalid == "parent": request.frame = SimpleNamespace(page=page)
    if invalid == "post": request.method = "POST"
    if invalid == "asset": request.is_navigation_request = lambda: False
    if invalid == "url": request.url += "?token=not-owned"
    if invalid == "closed": await panel.close()
    await panel.route(SimpleNamespace(abort=abort, fulfill=fulfill), request)
    assert aborted == ["blockedbyclient"] and served == []
