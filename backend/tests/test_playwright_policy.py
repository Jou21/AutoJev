import base64
from pathlib import Path

import pytest
from autojev.adapters.playwright_executor import (
    BrowserUnavailableError,
    PlaywrightExecutor,
)
from autojev.domain.models import AgentDecision, ExplorationOptions
from autojev.services.coverage import CoveragePlanner


def test_public_browser_urls_redact_secrets_and_preserve_spa_routes(tmp_path: Path):
    executor = PlaywrightExecutor(
        tmp_path, ExplorationOptions(), secret_values=["known-secret"]
    )

    public = executor._public_url(
        "https://example.test/app?q=ok&token=unknown&name=known-secret#/dashboard"
    )
    assert "unknown" not in public
    assert "known-secret" not in public
    assert "q=ok" in public
    assert public.endswith("#/dashboard")


async def test_navigation_guard_allows_same_origin_popup_without_frame_access(
    tmp_path: Path,
):
    class Route:
        continued = False
        aborted = False

        async def continue_(self):
            self.continued = True

        async def abort(self, _reason):
            self.aborted = True

    class Request:
        url = "https://example.test/child"

        @staticmethod
        def is_navigation_request():
            return True

        @property
        def frame(self):
            raise AssertionError("popup frame is not available yet")

    executor = PlaywrightExecutor(tmp_path, ExplorationOptions())
    executor._allowed_origin = executor._origin("https://example.test/")
    route = Route()

    await executor._guard_navigation(route, Request())

    assert route.continued is True
    assert route.aborted is False


async def test_navigation_guard_allows_cross_origin_iframe_without_blocking_page(
    tmp_path: Path,
):
    class Route:
        continued = False
        aborted = False

        async def continue_(self):
            self.continued = True

        async def abort(self, _reason):
            self.aborted = True

    class Frame:
        parent_frame = object()

    class Request:
        url = "https://embedded.example/frame"
        frame = Frame()

        @staticmethod
        def is_navigation_request():
            return True

    executor = PlaywrightExecutor(tmp_path, ExplorationOptions())
    executor._allowed_origin = executor._origin("https://example.test/")
    route = Route()

    await executor._guard_navigation(route, Request())

    assert route.continued is True
    assert route.aborted is False
    assert executor._blocked_navigation_url is None


@pytest.mark.parametrize(
    "context",
    [
        "Cancel subscription",
        "Cancel your order",
        "Cancel the booking",
        "Cancelar assinatura",
        "Cancelar o pedido",
        "Cancelar a reserva",
    ],
)
async def test_safe_interaction_blocks_contextual_cancellation(
    tmp_path: Path, context: str
):
    class Locator:
        async def evaluate(self, _script):
            return context

        async def inner_text(self, **_kwargs):
            return "Confirm"

        async def get_attribute(self, _name, **_kwargs):
            return None

    executor = PlaywrightExecutor(tmp_path, ExplorationOptions())

    with pytest.raises(PermissionError, match="PolicyBlocked"):
        await executor._enforce_safe_interaction(Locator())


async def test_safe_interaction_allows_generic_modal_cancel(tmp_path: Path):
    class Locator:
        async def evaluate(self, script):
            assert "form,[role=\"dialog\"]" in script
            assert "section" not in script
            return "Deseja fechar esta janela?"

        async def inner_text(self, **_kwargs):
            return "Cancelar"

        async def get_attribute(self, _name, **_kwargs):
            return None

    executor = PlaywrightExecutor(tmp_path, ExplorationOptions())

    await executor._enforce_safe_interaction(Locator())


async def test_observe_restricts_candidates_and_text_to_active_dialog(tmp_path: Path):
    class Page:
        url = "https://example.test/orders"

        async def evaluate(self, script):
            assert "dialog[open],[role=\"dialog\"]" in script
            assert "activeDialog && !activeDialog.contains(el)" in script
            assert "const textRoot = activeDialog || document.body" in script
            assert "parent.closest(PANEL_SELECTOR)" in script
            assert "[data-testid],[data-test],[data-qa]" in script
            assert "elementsFromPoint" in script
            assert "!candidate.closest(PANEL_SELECTOR)" in script
            assert "in_horizontal_viewport" in script
            assert "interactable" in script
            assert "closest('[aria-hidden=\"true\"]')" in script
            assert "for (let owner = el; owner; owner = owner.parentElement)" in script
            assert "el.matches(':disabled')" in script
            assert "el.closest('[inert]')" in script
            assert "setProperty('visibility', 'hidden', 'important')" not in script
            assert "panel.style.display = 'none'" not in script
            return {
                "title": "Orders",
                "text": "Confirm order dialog",
                "elements": [
                    {
                        "ref": "e0",
                        "tag": "button",
                        "role": None,
                        "name": "Close",
                        "type": None,
                        "href": None,
                        "enabled": True,
                        "context": "dialog Confirm order Close",
                        "_selector": "div:nth-of-type(1) > button:nth-of-type(1)",
                    }
                ],
            }

    executor = PlaywrightExecutor(
        tmp_path, ExplorationOptions(capture_screenshots=False)
    )
    executor._page = Page()

    observed = await executor.observe()

    assert observed.text == "Confirm order dialog"
    assert [element["name"] for element in observed.interactive_elements] == [
        "Close"
    ]
    assert executor._ref_selectors == {
        "e0": "div:nth-of-type(1) > button:nth-of-type(1)"
    }


async def test_real_dom_observation_rejects_transparent_and_disabled_controls(
    tmp_path: Path,
):
    playwright_api = pytest.importorskip("playwright.async_api")
    playwright = await playwright_api.async_playwright().start()
    browser = None
    try:
        try:
            browser = await playwright.chromium.launch(headless=True)
        except Exception as exc:
            pytest.skip(f"Chromium is unavailable: {exc}")
        page = await browser.new_page()
        await page.set_content(
            """
            <button>Visible action</button>
            <div style="opacity:0"><button>Transparent parent</button></div>
            <fieldset disabled><button>Fieldset disabled</button></fieldset>
            <div inert><button>Inert action</button></div>
            """
        )
        executor = PlaywrightExecutor(
            tmp_path, ExplorationOptions(capture_screenshots=False)
        )
        executor._browser = browser
        executor._page = page

        observed = await executor.observe()
        elements = {item["name"]: item for item in observed.interactive_elements}

        assert "Transparent parent" not in elements
        assert elements["Visible action"]["enabled"] is True
        assert elements["Visible action"]["interactable"] is True
        assert elements["Fieldset disabled"]["enabled"] is False
        assert elements["Fieldset disabled"]["interactable"] is False
        assert elements["Inert action"]["enabled"] is False
        assert elements["Inert action"]["interactable"] is False

        await page.set_content(
            """
            <style>
              body { margin: 0; min-height: 2400px; }
              .cover { position: fixed; inset: 0 0 auto 0; height: 80px;
                       z-index: 10; background: white; }
              #top { position: absolute; top: 10px; }
              #below { position: absolute; top: 1400px; }
            </style>
            <div class="cover"></div>
            <button id="top">Top button</button>
            <button id="below">Below button</button>
            """
        )
        before_scroll = await executor.observe()
        await page.evaluate("window.scrollTo(0, 1300)")
        after_scroll = await executor.observe()
        planner = CoveragePlanner("https://app.test/scroll")

        before_controls = {
            item["name"]: item["interactable"]
            for item in before_scroll.interactive_elements
        }
        after_controls = {
            item["name"]: item["interactable"]
            for item in after_scroll.interactive_elements
        }
        assert before_controls == {"Top button": False, "Below button": True}
        assert after_controls == {"Top button": True, "Below button": True}
        assert planner.effect_key(before_scroll) == planner.effect_key(after_scroll)
        assert planner.state_key(before_scroll) == planner.state_key(after_scroll)

        await page.set_viewport_size({"width": 800, "height": 600})
        await page.set_content(
            """
            <button>Open Menu</button>
            <aside style="position:fixed;top:-220px;right:0;width:180px;height:180px">
              <button>Close Menu</button>
            </aside>
            <div style="width:120px;overflow-x:auto;margin-top:100px">
              <div style="width:1200px">
                <button style="margin-left:1000px">Carousel action</button>
              </div>
            </div>
            """
        )
        surfaces = await executor.observe()
        surface_elements = {
            item["name"]: item for item in surfaces.interactive_elements
        }
        surface_planner = CoveragePlanner("https://app.test/surfaces")
        surface_names = {
            item["name"]
            for item in surface_planner.context(surfaces)["current_untried"]
        }

        assert surface_elements["Close Menu"]["interactable"] is False
        assert surface_elements["Carousel action"]["interactable"] is True
        assert "Open Menu" in surface_names
        assert "Close Menu" not in surface_names
        assert "Carousel action" in surface_names
    finally:
        if browser is not None:
            await browser.close()
        await playwright.stop()


async def test_observe_rejects_center_occlusion_even_when_button_corner_is_free(tmp_path: Path):
    from playwright.async_api import async_playwright
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={"width": 800, "height": 600})
            await page.set_content("""
              <style>body{margin:0}#background{position:absolute;left:20px;top:200px;width:220px;height:40px}
              .bm-menu-wrap{position:fixed;left:80px;top:0;width:100px;height:600px;background:white;z-index:20}</style>
              <button id="background">Continue Shopping</button>
              <div class="bm-menu-wrap"><nav><button onclick="this.closest('.bm-menu-wrap').remove()">Close Menu</button></nav></div>
            """)
            executor = PlaywrightExecutor(tmp_path, ExplorationOptions(capture_screenshots=False))
            executor._page, executor._browser = page, browser
            before = await executor.observe()
            elements = {item["name"]: item for item in before.interactive_elements}
            assert elements["Continue Shopping"]["interactable"] is False
            assert elements["Continue Shopping"]["obstructed_by_overlay"] is True
            assert elements["Close Menu"]["in_overlay"] is True
            assert elements["Close Menu"]["overlay_dismiss"] is True
            await page.get_by_role("button", name="Close Menu").click()
            after = await executor.observe()
            background = next(item for item in after.interactive_elements if item["name"] == "Continue Shopping")
            assert background["interactable"] is True
            assert background["obstructed_by_overlay"] is False
        finally:
            await browser.close()


async def test_overlay_dismiss_never_discards_entered_form_data(tmp_path: Path):
    from playwright.async_api import async_playwright
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            executor = PlaywrightExecutor(tmp_path, ExplorationOptions(capture_screenshots=False))
            executor._page, executor._browser = page, browser
            await page.set_content('<section role="dialog"><input value="draft"><button>Cancel</button></section>')
            observation = await executor.observe()
            close = next(item for item in observation.interactive_elements if item["name"] == "Cancel")
            assert close["in_overlay"] is True
            assert close["overlay_dismiss"] is False
            await page.locator("input").fill("")
            observation = await executor.observe()
            close = next(item for item in observation.interactive_elements if item["name"] == "Cancel")
            assert close["overlay_dismiss"] is True
        finally:
            await browser.close()


async def test_navigation_owner_and_bare_href_survive_menu_content_changes(tmp_path: Path):
    from playwright.async_api import async_playwright
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content('<nav id="menu" aria-label="Site menu"><a href="#">Dynamic Catalog</a></nav>')
            executor = PlaywrightExecutor(tmp_path, ExplorationOptions(capture_screenshots=False))
            executor._page, executor._browser = page, browser
            before = (await executor.observe()).interactive_elements[0]
            await page.evaluate("document.querySelector('nav').insertAdjacentHTML('beforeend','<a href=\"#\">Lazy Load</a>')")
            after = (await executor.observe()).interactive_elements[0]
            assert before["raw_href"] == after["raw_href"] == "#"
            assert before["navigation_owner"] == after["navigation_owner"] == "nav id=menu aria-label=Site menu"
            assert before["context"] != after["context"]
        finally:
            await browser.close()


@pytest.mark.parametrize("host,blocked", [("production.example", True), ("www.saucedemo.com", False), ("shop.test", False)])
async def test_finish_checkout_is_only_automatic_on_known_sandbox_origins(tmp_path: Path, host, blocked):
    class Page:
        url = f"https://{host}/review"
    class Locator:
        async def evaluate(self, script):
            if "order|complete order" in script:
                return True
            return ""
        async def inner_text(self, **_kwargs):
            return "Finish"
        async def get_attribute(self, _attribute, **_kwargs):
            return None
    executor = PlaywrightExecutor(tmp_path, ExplorationOptions())
    executor._page = Page()
    if blocked:
        with pytest.raises(PermissionError, match="real order"):
            await executor._enforce_safe_interaction(Locator())
    else:
        await executor._enforce_safe_interaction(Locator())


def test_sidepanel_is_stable_click_controlled_and_expands_on_attention(tmp_path: Path):
    executor = PlaywrightExecutor(tmp_path, ExplorationOptions(), headless=False)

    assert "position:fixed !important" in executor._SIDEPANEL_CSS
    assert "translate3d(calc(100% - 54px),0,0)" in executor._SIDEPANEL_CSS
    assert "opacity:.42" in executor._SIDEPANEL_CSS
    assert "#autojev-sidepanel:hover" not in executor._SIDEPANEL_CSS
    assert "transition:none !important" in executor._SIDEPANEL_CSS
    assert "[data-mode='attention']" in executor._SIDEPANEL_CSS
    automation_rule = (
        "#autojev-sidepanel[data-automation='true'] .autojev-panel-rail,"
        "#autojev-sidepanel[data-automation='true'] .autojev-panel-surface,"
        "#autojev-sidepanel[data-automation='true'] .autojev-panel-surface *"
        "{pointer-events:none !important;}"
    )
    assert automation_rule in executor._SIDEPANEL_CSS
    assert executor._SIDEPANEL_CSS.index(automation_rule) > executor._SIDEPANEL_CSS.index(
        "pointer-events:auto !important"
    )
    assert "[data-automation='true'] .autojev-panel-surface{opacity:0" not in executor._SIDEPANEL_CSS
    assert "box-shadow:-4px 18px 48px" not in executor._SIDEPANEL_CSS
    assert "box-shadow:-10px 12px 34px" not in executor._SIDEPANEL_CSS
    assert "box-shadow:-12px 18px 55px" not in executor._SIDEPANEL_CSS
    assert "height:100vh" not in executor._SIDEPANEL_CSS
    assert "action.hidden = true" in executor._SIDEPANEL_INSTALLER
    assert "sessionStorage.setItem(PIN_KEY" in executor._SIDEPANEL_INSTALLER
    assert "autojev-panel-usage" in executor._SIDEPANEL_INSTALLER
    assert "savedUsage.tokens" in executor._SIDEPANEL_INSTALLER
    assert "Clique no J para abrir" in executor._SIDEPANEL_INSTALLER
    assert "window.top !== window" in executor._SIDEPANEL_INSTALLER


async def test_sidepanel_bootstrap_is_installed_only_once(tmp_path: Path):
    class Context:
        def __init__(self):
            self.scripts: list[str] = []

        async def add_init_script(self, *, script):
            self.scripts.append(script)

    executor = PlaywrightExecutor(tmp_path, ExplorationOptions(), headless=False)
    context = Context()
    executor._context = context

    await executor._install_sidepanel_bootstrap()
    await executor._install_sidepanel_bootstrap()

    assert len(context.scripts) == 1
    assert "autojev-sidepanel" in context.scripts[0]
    assert "DOMContentLoaded" in context.scripts[0]


async def test_automation_makes_panel_hit_targets_click_through(tmp_path: Path):
    class Page:
        def __init__(self):
            self.script = ""
            self.active = None

        async def evaluate(self, script, active):
            self.script = script
            self.active = active

    executor = PlaywrightExecutor(
        tmp_path, ExplorationOptions(capture_screenshots=False), headless=False
    )
    page = Page()
    executor._page = page

    await executor._set_sidepanel_automation(True)

    assert page.active is True
    assert "autojev-panel-rail" in page.script
    assert "autojev-panel-surface" in page.script
    assert "setProperty('pointer-events', 'none', 'important')" in page.script
    assert "removeProperty('pointer-events')" in page.script


async def test_observe_normalizes_empty_anchor_href_like_stale_check(tmp_path: Path):
    class Locator:
        async def count(self):
            return 1

        async def evaluate(self, _script):
            return {
                "tag": "a",
                "role": "button",
                "type": None,
                "name": "Cart, 1 items",
                "href": None,
                "aria_haspopup": None,
            }

    class Page:
        url = "https://example.test/inventory"

        async def evaluate(self, _script):
            return {
                "title": "Inventory",
                "text": "Cart",
                "elements": [
                    {
                        "ref": "e0",
                        "tag": "a",
                        "role": "button",
                        "name": "Cart, 1 items",
                        "type": None,
                        "href": "",
                        "enabled": True,
                        "context": "header Cart",
                        "aria_haspopup": None,
                        "_selector": "a:nth-of-type(1)",
                    }
                ],
            }

        def locator(self, _selector):
            return Locator()

    executor = PlaywrightExecutor(
        tmp_path, ExplorationOptions(capture_screenshots=False)
    )
    executor._page = Page()

    observed = await executor.observe()
    await executor._locator("e0")

    assert observed.interactive_elements[0]["href"] is None
    assert executor._ref_semantics["e0"]["href"] is None


async def test_go_back_is_blocked_before_leaving_authenticated_root(
    tmp_path: Path,
):
    class Page:
        url = "https://example.test/inventory?b=2&a=1"
        went_back = False
        went_forward = False

        async def go_back(self, **_kwargs):
            self.went_back = True
            self.url = "https://example.test/login"
            return object()

        async def go_forward(self, **_kwargs):
            self.url = "https://example.test/inventory"
            self.went_forward = True
            return object()

        async def evaluate(self, script):
            if "input[type=\"password\"]" in script:
                return self.url.endswith("/login")
            return {"title": "Inventory", "text": "Products", "elements": []}

    executor = PlaywrightExecutor(
        tmp_path, ExplorationOptions(capture_screenshots=False)
    )
    page = Page()
    executor._page = page
    executor._allowed_origin = executor._origin(page.url)
    executor.protect_authenticated_history()
    # Query ordering must not move the authenticated history floor.
    page.url = "https://example.test/inventory?a=1&b=2"

    result, observation = await executor.execute(
        AgentDecision(action="go_back", rationale="Return to coverage parent"), {}
    )

    assert result.success is False
    assert "authenticated session boundary" in result.message
    assert page.went_back is False
    assert page.went_forward is False
    assert observation.url == "https://example.test/inventory?a=1&b=2"


async def test_go_back_restores_forward_page_when_unexpected_login_is_reached(
    tmp_path: Path,
):
    class Page:
        url = "https://example.test/inventory"
        went_back = False
        went_forward = False

        async def go_back(self, **_kwargs):
            self.went_back = True
            self.url = "https://example.test/login"
            return object()

        async def go_forward(self, **_kwargs):
            self.url = "https://example.test/details"
            self.went_forward = True
            return object()

        async def evaluate(self, script):
            if "input[type=\"password\"]" in script:
                return self.url.endswith("/login")
            return {"title": "Details", "text": "Product", "elements": []}

    executor = PlaywrightExecutor(
        tmp_path, ExplorationOptions(capture_screenshots=False)
    )
    page = Page()
    executor._page = page
    executor._allowed_origin = executor._origin(page.url)
    executor.protect_authenticated_history()
    # The executor is away from the protected root, so it retains the
    # post-navigation login detection as a defense-in-depth fallback.
    page.url = "https://example.test/details"

    result, observation = await executor.execute(
        AgentDecision(action="go_back", rationale="Return to coverage parent"), {}
    )

    assert result.success is False
    assert "authenticated session boundary" in result.message
    assert page.went_back is True
    assert page.went_forward is True
    assert observation.url == "https://example.test/details"


async def test_closing_last_page_marks_executor_inactive_and_fails_fast(
    tmp_path: Path,
):
    class Page:
        url = "https://example.test/inventory"

        @staticmethod
        def is_closed():
            return True

    executor = PlaywrightExecutor(tmp_path, ExplorationOptions())
    page = Page()
    executor._page = page
    executor._page_stack = [page]

    executor._on_page_close(page)

    assert executor.is_active() is False
    assert executor.inactive_event.is_set() is True
    await executor.wait_until_inactive()
    with pytest.raises(BrowserUnavailableError, match="not active"):
        await executor.observe()
    with pytest.raises(BrowserUnavailableError, match="not active"):
        await executor.execute(AgentDecision(action="wait", rationale="wait"), {})


def test_browser_disconnect_marks_executor_inactive(tmp_path: Path):
    class Page:
        url = "https://example.test/inventory"

        @staticmethod
        def is_closed():
            return False

    executor = PlaywrightExecutor(tmp_path, ExplorationOptions())
    page = Page()
    executor._page = page
    executor._page_stack = [page]

    assert executor.is_active() is True

    executor._on_browser_disconnected()

    assert executor.is_active() is False
    assert executor.inactive_event.is_set() is True
    assert executor._page is None


@pytest.mark.parametrize(
    "actual",
    [
        {
            "tag": "button",
            "role": None,
            "type": None,
            "name": "Excluir conta",
            "href": None,
        },
        {
            "tag": "a",
            "role": None,
            "type": None,
            "name": "Continuar",
            "href": None,
        },
        {
            "tag": "button",
            "role": None,
            "type": None,
            "name": "Continuar",
            "href": "https://example.test/changed",
        },
    ],
)
async def test_locator_rejects_stale_semantic_ref(tmp_path: Path, actual: dict):
    class Locator:
        async def count(self):
            return 1

        async def evaluate(self, _script):
            return actual

    class Page:
        def locator(self, selector):
            assert selector == "button:nth-of-type(1)"
            return Locator()

    executor = PlaywrightExecutor(tmp_path, ExplorationOptions())
    executor._page = Page()
    executor._ref_selectors = {"e0": "button:nth-of-type(1)"}
    executor._ref_semantics = {
        "e0": {
            "tag": "button",
            "role": None,
            "type": None,
            "name": "Continuar",
            "href": None,
        }
    }

    with pytest.raises(ValueError, match="re-observation"):
        await executor._locator("e0")


async def test_capture_never_mutates_live_panel_visibility(tmp_path: Path):
    class EmptyLocator:
        async def count(self):
            return 0

    class Page:
        screenshot_options = None

        def locator(self, _selector):
            return EmptyLocator()

        async def screenshot(self, **kwargs):
            self.screenshot_options = kwargs

    executor = PlaywrightExecutor(
        tmp_path, ExplorationOptions(capture_screenshots=True), headless=False
    )
    page = Page()
    executor._page = page

    filename = await executor._capture("painel estavel")

    assert filename == "001-painel-estavel.png"
    assert page.screenshot_options["full_page"] is False
    assert page.screenshot_options["mask"] == []
    assert page.screenshot_options["caret"] == "initial"
    assert "style" not in page.screenshot_options


async def test_real_capture_masks_manual_credentials_without_changing_panel_or_inputs(
    tmp_path: Path,
):
    playwright_api = pytest.importorskip("playwright.async_api")
    playwright = await playwright_api.async_playwright().start()
    browser = None
    try:
        try:
            browser = await playwright.chromium.launch(headless=True)
        except playwright_api.Error as exc:
            pytest.skip(f"Chromium is unavailable: {exc}")
        page = await browser.new_page(viewport={"width": 800, "height": 600})
        await page.set_content(
            """
            <style>
              input,textarea,select {display:block;width:240px;height:24px;margin:8px;}
              #autojev-sidepanel {position:fixed;right:12px;top:12px;
                width:100px;height:100px;background:rgb(20,184,166);}
            </style>
            <input id="username" value="fixture-user">
            <input id="password" type="password" value="fixture-password">
            <input id="access" aria-label="API key" value="fixture-api-key">
            <input id="code" autocomplete="one-time-code" value="123456">
            <textarea id="note" name="session_token">fixture-session</textarea>
            <select id="choice" aria-label="Secret choice"><option>fixture-private</option></select>
            <input id="ordinary" placeholder="Product quantity" value="2">
            <aside id="autojev-sidepanel">J</aside>
            """
        )
        # Record mutations, not just the final style: a transient hide/show
        # would still flash even if its final computed state were identical.
        await page.evaluate(
            """() => {
              window.fixtureMutations = [];
              new MutationObserver((changes) => window.fixtureMutations.push(
                ...changes.map((change) => ({type:change.type,attribute:change.attributeName}))
              )).observe(document.body, {subtree:true,attributes:true,childList:true});
            }"""
        )
        executor = PlaywrightExecutor(
            tmp_path, ExplorationOptions(capture_screenshots=True), headless=False
        )
        executor._page = page
        assert executor.secret_values == []  # no credential vault / external key
        sensitive_ids = await page.locator(
            executor._SENSITIVE_FIELD_SELECTOR
        ).evaluate_all("nodes => nodes.map(node => node.id)")
        assert set(sensitive_ids) == {
            "username", "password", "access", "code", "note", "choice",
        }

        first = await executor._capture("manual credentials")
        second = await executor._capture("manual credentials again")

        assert first and second
        assert await page.evaluate("window.fixtureMutations") == []
        assert await page.locator("#username").input_value() == "fixture-user"
        assert await page.locator("#access").input_value() == "fixture-api-key"
        assert await page.locator("#ordinary").input_value() == "2"
        assert await page.locator("#autojev-sidepanel").is_visible()

        coordinates = {}
        for element_id in [*sensitive_ids, "ordinary", "autojev-sidepanel"]:
            box = await page.locator(f"#{element_id}").bounding_box()
            coordinates[element_id] = [
                int(box["x"] + box["width"] / 2),
                int(box["y"] + box["height"] / 2),
            ]
        # Inspect actual screenshot pixels with an off-DOM canvas. The masking
        # must protect both repeated captures without painting over the panel.
        for filename in (first, second):
            pixels = await page.evaluate(
                """async ({data,coordinates}) => {
                  const image = new Image();
                  image.src = `data:image/png;base64,${data}`;
                  await image.decode();
                  const canvas = document.createElement('canvas');
                  canvas.width=image.width; canvas.height=image.height;
                  const context=canvas.getContext('2d'); context.drawImage(image,0,0);
                  return Object.fromEntries(Object.entries(coordinates).map(([id,[x,y]]) =>
                    [id,Array.from(context.getImageData(x,y,1,1).data)]));
                }""",
                {
                    "data": base64.b64encode((tmp_path / filename).read_bytes()).decode(),
                    "coordinates": coordinates,
                },
            )
            assert all(
                pixels[element_id] == [16, 20, 22, 255]
                for element_id in sensitive_ids
            )
            assert pixels["ordinary"] != [16, 20, 22, 255]
            assert pixels["autojev-sidepanel"] == [20, 184, 166, 255]
    finally:
        if browser is not None:
            await browser.close()
        await playwright.stop()
