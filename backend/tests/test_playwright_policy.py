from pathlib import Path

import pytest

from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.domain.models import ExplorationOptions


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
            assert "(activeDialog || document.body)?.innerText" in script
            assert "[data-testid],[data-test],[data-qa]" in script
            assert "setProperty('visibility', 'hidden', 'important')" in script
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
    assert "[data-automation='true'] *{pointer-events:none !important" in executor._SIDEPANEL_CSS
    assert "[data-automation='true'] .autojev-panel-surface{opacity:0" not in executor._SIDEPANEL_CSS
    assert "box-shadow:-4px 18px 48px" not in executor._SIDEPANEL_CSS
    assert "box-shadow:-10px 12px 34px" not in executor._SIDEPANEL_CSS
    assert "height:100vh" not in executor._SIDEPANEL_CSS
    assert "action.hidden = true" in executor._SIDEPANEL_INSTALLER
    assert "sessionStorage.setItem(PIN_KEY" in executor._SIDEPANEL_INSTALLER
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


async def test_go_back_restores_forward_page_at_authenticated_boundary(
    tmp_path: Path,
):
    class Page:
        url = "https://example.test/inventory"
        went_forward = False

        async def go_back(self, **_kwargs):
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

    from autojev.domain.models import AgentDecision

    result, observation = await executor.execute(
        AgentDecision(action="go_back", rationale="Return to coverage parent"), {}
    )

    assert result.success is False
    assert "authenticated session boundary" in result.message
    assert page.went_forward is True
    assert observation.url == "https://example.test/inventory"


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


async def test_capture_hides_overlay_with_screenshot_only_style(tmp_path: Path):
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

    filename = await executor._capture("sem overlay")

    assert filename == "001-sem-overlay.png"
    assert page.screenshot_options["full_page"] is False
    assert page.screenshot_options["mask"] == []
    assert "#autojev-sidepanel" in page.screenshot_options["style"]
    assert "visibility:hidden !important" in page.screenshot_options["style"]
