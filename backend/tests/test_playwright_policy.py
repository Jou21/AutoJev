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
