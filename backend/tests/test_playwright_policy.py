from pathlib import Path

import pytest
from autojev.adapters.playwright_executor import (
    BrowserUnavailableError,
    PlaywrightExecutor,
)
from autojev.domain.models import AgentDecision, ExplorationOptions


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
    assert "style" not in page.screenshot_options
