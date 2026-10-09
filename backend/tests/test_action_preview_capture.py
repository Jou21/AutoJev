from __future__ import annotations

import base64
import json
import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from autojev.adapters.playwright_executor import PlaywrightExecutor, StaleObservedControlError
from autojev.domain.models import AgentDecision, ExplorationOptions, Observation


class EmptyLocator:
    async def count(self):
        return 0


class PreviewPage:
    url = "https://example.test/products?token=private-token&name=fixture-secret"

    def __init__(self, events):
        self.events = events
        self.viewport = {"width": 800, "height": 600}
        self.screenshot_options = None
        self.fail_screenshot = False

    async def evaluate(self, script, *_args):
        assert "innerWidth" in script and "innerHeight" in script
        self.events.append("viewport")
        return self.viewport.copy()

    def locator(self, _selector):
        return EmptyLocator()

    async def screenshot(self, **kwargs):
        self.events.append("capture")
        self.screenshot_options = kwargs
        if self.fail_screenshot:
            raise RuntimeError("Screenshot unavailable")

    async def wait_for_timeout(self, _milliseconds):
        pass

    async def wait_for_load_state(self, *_args, **_kwargs):
        pass


class PreviewTarget:
    def __init__(self, events):
        self.events = events
        self.box = {"x": 612.5, "y": 54.25, "width": 160, "height": 32}
        self.after_box = None
        self.measurements = 0
        self.fail_action = False

    async def scroll_into_view_if_needed(self, **_kwargs):
        self.events.append("scroll")

    async def bounding_box(self):
        self.events.append("box")
        self.measurements += 1
        if self.measurements > 1 and self.after_box is not None:
            return self.after_box.copy()
        return self.box.copy() if self.box is not None else None

    async def get_attribute(self, *_args, **_kwargs):
        return None

    async def evaluate(self, *_args):
        pass

    async def _act(self, name):
        self.events.append(name)
        if self.fail_action:
            raise RuntimeError("Action failed")

    async def click(self, **_kwargs):
        await self._act("click")

    async def fill(self, _value, **_kwargs):
        await self._act("fill")

    async def select_option(self, _value, **_kwargs):
        await self._act("select")

    async def press(self, _key, **_kwargs):
        await self._act("press")

    async def hover(self, **_kwargs):
        await self._act("hover")


class SelectPreviewTarget(PreviewTarget):
    def __init__(self, events):
        super().__init__(events)
        self.selected_label = "Name (A to Z)"
        self.requested_label = "Name (Z to A)"
        self.matches = False
        self.unavailable = False
        self.fail_read = False

    async def evaluate(self, script, *_args):
        if "HTMLSelectElement" not in script:
            return None
        self.events.append("select-labels")
        if self.fail_read:
            raise RuntimeError("Optional label read failed")
        if self.unavailable:
            return None
        return {
            "requested_label": self.requested_label,
            "selected_label": self.selected_label,
            "matches": self.matches,
        }

    async def select_option(self, value, **_kwargs):
        await self._act("select")
        self.selected_label = self.requested_label
        self.matches = True


def preview_fixture(tmp_path: Path):
    events = []
    executor = PlaywrightExecutor(tmp_path, ExplorationOptions())
    executor._page = PreviewPage(events)
    target = PreviewTarget(events)
    decision = AgentDecision(
        action="select", target="e2", value="fixture-secret", rationale="fixture-secret",
    )
    return executor, target, decision, events


def stub_execution(monkeypatch, executor, target, events):
    executor._context = SimpleNamespace(pages=[executor._page])
    executor._allowed_origin = executor._origin(executor._page.url)
    monkeypatch.setattr(executor, "_locator", AsyncMock(return_value=target))

    async def authorize(_locator):
        events.append("authorize")

    monkeypatch.setattr(executor, "_enforce_safe_interaction", authorize)
    monkeypatch.setattr(executor, "_set_sidepanel_automation", AsyncMock())
    monkeypatch.setattr(executor, "_wait_for_action_requests", AsyncMock(return_value=True))
    monkeypatch.setattr(executor, "_wait_for_dom_settle", AsyncMock())
    monkeypatch.setattr(executor, "_evaluate_assertions", AsyncMock(return_value=[]))
    monkeypatch.setattr(executor, "_remember_cart_action", lambda _decision: None)
    observation = Observation(
        url="https://example.test/products", title="Products", text="Products",
        interactive_elements=[], screenshot="after.png",
    )
    monkeypatch.setattr(executor, "observe", AsyncMock(return_value=observation))


async def test_preview_records_css_viewport_target_before_action_and_redacts_url(tmp_path):
    executor, target, decision, events = preview_fixture(tmp_path)
    executor.secret_values = ["fixture-secret"]
    preview = await executor._capture_action_preview(target, decision)

    assert preview is not None
    assert preview["version"] == "action-target-v1"
    assert preview["coordinate_space"] == "screenshot-css-pixels"
    assert preview["image"] == {"width": 800, "height": 600}
    assert preview["box"] == target.box
    assert preview["target"] == "e2"
    assert preview["action"] == "select"
    assert preview["captured_at"].endswith("+00:00")
    assert preview["screenshot"] == "001-before-select-target.png"
    assert "private-token" not in preview["url"]
    assert "fixture-secret" not in json.dumps(preview)
    assert events == ["scroll", "viewport", "box", "capture", "box", "viewport"]
    assert executor._page.screenshot_options["full_page"] is False
    assert executor._page.screenshot_options["scale"] == "css"
    assert executor._page.screenshot_options["caret"] == "initial"
    assert "style" not in executor._page.screenshot_options
    assert "value" not in preview and "rationale" not in preview
    assert set(preview) == {
        "version", "coordinate_space", "image", "box", "target", "action",
        "captured_at", "screenshot", "url",
    }


@pytest.mark.parametrize("headless,viewport_only,full_page", [
    (True, False, True), (True, True, False),
    (False, False, False), (False, True, False),
])
async def test_viewport_capture_preserves_existing_capture_modes(
    tmp_path, headless, viewport_only, full_page,
):
    executor, _target, _decision, _events = preview_fixture(tmp_path)
    executor.headless = headless
    await executor._capture("fixture", viewport_only=viewport_only)
    options = executor._page.screenshot_options
    assert options["full_page"] is full_page
    if viewport_only:
        assert options["scale"] == "css"
    else:
        assert "scale" not in options


@pytest.mark.parametrize("patch", [
    {"x": -1}, {"y": -1}, {"width": 0}, {"height": -5},
    {"x": float("nan")}, {"height": float("inf")}, {"x": True},
    {"width": "160"}, {"x": 799}, {"y": 599}, {"width": 801},
])
async def test_preview_never_claims_invalid_or_offscreen_geometry(tmp_path, patch):
    executor, target, decision, events = preview_fixture(tmp_path)
    target.box.update(patch)
    assert await executor._capture_action_preview(target, decision) is None
    assert "capture" not in events


@pytest.mark.parametrize("viewport", [
    {"width": 0, "height": 600}, {"width": 800, "height": float("nan")},
    {"width": True, "height": 600}, {"width": "800", "height": 600},
])
async def test_preview_rejects_untrusted_viewport_dimensions(tmp_path, viewport):
    executor, target, decision, events = preview_fixture(tmp_path)
    executor._page.viewport = viewport
    assert await executor._capture_action_preview(target, decision) is None
    assert "capture" not in events


@pytest.mark.parametrize("changed_property", ["x", "y", "width", "height"])
async def test_preview_omits_target_that_moves_during_screenshot(tmp_path, changed_property):
    executor, target, decision, events = preview_fixture(tmp_path)
    target.after_box = {**target.box, changed_property: target.box[changed_property] + 8}
    assert await executor._capture_action_preview(target, decision) is None
    assert "capture" in events


@pytest.mark.parametrize("change", ["url", "viewport", "page"])
async def test_preview_omits_changed_document_or_viewport(tmp_path, monkeypatch, change):
    executor, target, decision, _events = preview_fixture(tmp_path)
    page = executor._page
    screenshot = page.screenshot

    async def changing_screenshot(**kwargs):
        await screenshot(**kwargs)
        if change == "url":
            page.url = "https://example.test/another-page"
        elif change == "viewport":
            page.viewport["width"] = 640
        else:
            executor._page = PreviewPage([])

    monkeypatch.setattr(page, "screenshot", changing_screenshot)
    assert await executor._capture_action_preview(target, decision) is None


async def test_preview_disabled_does_not_scroll_or_measure(tmp_path):
    executor, target, decision, events = preview_fixture(tmp_path)
    executor.options.capture_screenshots = False
    assert await executor._capture_action_preview(target, decision) is None
    assert events == []


async def test_preview_missing_test_double_apis_is_optional(tmp_path):
    executor, _target, decision, _events = preview_fixture(tmp_path)
    assert await executor._capture_action_preview(SimpleNamespace(), decision) is None


@pytest.mark.parametrize("action,value,key", [
    ("click", None, None), ("select", "za", None), ("fill", "ordinary", None),
    ("press", None, "Enter"), ("hover", None, None),
])
async def test_execute_keeps_preview_on_action_before_dispatch(
    tmp_path, monkeypatch, action, value, key,
):
    executor, target, _decision, events = preview_fixture(tmp_path)
    stub_execution(monkeypatch, executor, target, events)
    decision = AgentDecision(action=action, target="e2", value=value, key=key, rationale="fixture")
    result, _observation = await executor.execute(decision, {})

    assert result.success
    assert result.evidence["action_preview"]["action"] == action
    assert result.evidence["action_preview"]["target"] == "e2"
    assert result.screenshot == "after.png"
    assert events.index("capture") < events.index(action)
    if action != "hover":
        assert events.index("authorize") < events.index("capture")


@pytest.mark.parametrize("action,value,key", [
    ("click", None, None), ("select", "za", None), ("fill", "ordinary", None),
    ("press", None, "Enter"),
])
async def test_blocked_actions_never_capture_preview_or_scroll(
    tmp_path, monkeypatch, action, value, key,
):
    executor, target, _decision, events = preview_fixture(tmp_path)
    stub_execution(monkeypatch, executor, target, events)
    monkeypatch.setattr(executor, "_enforce_safe_interaction", AsyncMock(
        side_effect=PermissionError("PolicyBlocked: fixture"),
    ))
    result, _observation = await executor.execute(
        AgentDecision(action=action, target="e2", value=value, key=key, rationale="fixture"), {},
    )
    assert not result.success
    assert "action_preview" not in result.evidence
    assert "capture" not in events and "scroll" not in events and action not in events


@pytest.mark.parametrize("decision", [
    AgentDecision(action="fill", target="e2", rationale="Missing input"),
    AgentDecision(action="fill", target="e2", credential_key="missing", rationale="Missing alias"),
    AgentDecision(action="select", target="e2", rationale="Missing selection"),
])
async def test_invalid_input_never_captures_preview(tmp_path, monkeypatch, decision):
    executor, target, _decision, events = preview_fixture(tmp_path)
    stub_execution(monkeypatch, executor, target, events)
    result, _observation = await executor.execute(decision, {})
    assert not result.success
    assert "action_preview" not in result.evidence
    assert "capture" not in events and "scroll" not in events


async def test_screenshot_failure_does_not_change_action_verdict(tmp_path, monkeypatch):
    executor, target, decision, events = preview_fixture(tmp_path)
    stub_execution(monkeypatch, executor, target, events)
    executor._page.fail_screenshot = True
    result, _observation = await executor.execute(decision, {})
    assert result.success
    assert "action_preview" not in result.evidence
    assert "select" in events


async def test_failed_action_still_retains_its_before_target_capture(tmp_path, monkeypatch):
    executor, target, decision, events = preview_fixture(tmp_path)
    stub_execution(monkeypatch, executor, target, events)
    target.fail_action = True
    result, _observation = await executor.execute(decision, {})
    assert not result.success
    assert result.evidence["action_preview"]["action"] == "select"
    assert events.index("capture") < events.index("select")


async def test_stale_node_guard_precedes_preview(tmp_path, monkeypatch):
    executor, target, decision, events = preview_fixture(tmp_path)
    stub_execution(monkeypatch, executor, target, events)
    monkeypatch.setattr(executor, "_locator", AsyncMock(side_effect=StaleObservedControlError("stale")))
    result, _observation = await executor.execute(decision, {})
    assert not result.success
    assert "action_preview" not in result.evidence
    assert result.evidence["action_dispatch"]["status"] == "not_executed"
    assert "capture" not in events and "scroll" not in events


async def test_credential_input_value_is_never_added_to_preview(tmp_path, monkeypatch):
    executor, target, _decision, events = preview_fixture(tmp_path)
    stub_execution(monkeypatch, executor, target, events)
    decision = AgentDecision(action="fill", target="e2", credential_key="password", rationale="fixture")
    result, _observation = await executor.execute(decision, {"password": "fixture-credential"})
    assert result.success
    assert "fixture-credential" not in json.dumps(result.evidence["action_preview"])
    assert result.value == "[credential:password]"


async def test_select_preview_records_labels_not_values_and_observed_result(tmp_path, monkeypatch):
    executor, _target, _decision, events = preview_fixture(tmp_path)
    target = SelectPreviewTarget(events)
    stub_execution(monkeypatch, executor, target, events)
    decision = AgentDecision(action="select", target="e2", value="za", rationale="fixture")
    result, _observation = await executor.execute(decision, {})

    assert result.success
    selection = result.evidence["action_preview"]["selection"]
    assert selection == {
        "version": "select-labels-v1", "status": "confirmed",
        "requested_label": "Name (Z to A)", "before_label": "Name (A to Z)",
        "after_label": "Name (Z to A)",
    }
    assert "za" not in selection.values()
    assert events.index("select-labels") < events.index("select") < len(events) - 1


async def test_select_preview_mismatch_does_not_change_automation_verdict(tmp_path, monkeypatch):
    executor, _target, _decision, events = preview_fixture(tmp_path)
    target = SelectPreviewTarget(events)
    stub_execution(monkeypatch, executor, target, events)

    async def app_reverted_selection():
        target.selected_label = "Name (A to Z)"
        target.matches = False

    monkeypatch.setattr(executor, "_wait_for_dom_settle", app_reverted_selection)
    result, _observation = await executor.execute(
        AgentDecision(action="select", target="e2", value="za", rationale="fixture"), {},
    )
    assert result.success
    selection = result.evidence["action_preview"]["selection"]
    assert selection["status"] == "mismatch"
    assert selection["requested_label"] == "Name (Z to A)"
    assert selection["after_label"] == "Name (A to Z)"


@pytest.mark.parametrize("change", ["page", "url", "detached", "read_failure"])
async def test_select_preview_does_not_claim_result_from_changed_control(
    tmp_path, monkeypatch, change,
):
    executor, _target, _decision, events = preview_fixture(tmp_path)
    target = SelectPreviewTarget(events)
    stub_execution(monkeypatch, executor, target, events)

    async def change_after_dispatch():
        if change == "page":
            executor._page = PreviewPage(events)
        elif change == "url":
            executor._page.url = "https://example.test/other-page"
        elif change == "detached":
            target.unavailable = True
        else:
            target.fail_read = True

    monkeypatch.setattr(executor, "_wait_for_dom_settle", change_after_dispatch)
    result, _observation = await executor.execute(
        AgentDecision(action="select", target="e2", value="za", rationale="fixture"), {},
    )
    assert result.success
    selection = result.evidence["action_preview"]["selection"]
    assert selection["status"] == "unverified"
    assert "after_label" not in selection


async def test_failed_select_retains_intent_without_claiming_observed_selection(tmp_path, monkeypatch):
    executor, _target, _decision, events = preview_fixture(tmp_path)
    target = SelectPreviewTarget(events)
    target.fail_action = True
    stub_execution(monkeypatch, executor, target, events)
    result, _observation = await executor.execute(
        AgentDecision(action="select", target="e2", value="za", rationale="fixture"), {},
    )
    assert not result.success
    assert result.evidence["action_preview"]["selection"] == {
        "version": "select-labels-v1", "status": "unverified",
        "requested_label": "Name (Z to A)", "before_label": "Name (A to Z)",
    }


@pytest.mark.parametrize("source", ["field_semantics", "dom", "secret_value", "read_failure"])
async def test_sensitive_or_unavailable_select_labels_are_optional(tmp_path, monkeypatch, source):
    executor, _target, _decision, events = preview_fixture(tmp_path)
    target = SelectPreviewTarget(events)
    stub_execution(monkeypatch, executor, target, events)
    if source == "field_semantics":
        executor._field_semantics["e2"] = {"sensitive": True}
    elif source == "dom":
        target.unavailable = True
    elif source == "secret_value":
        executor.secret_values = ["za"]
    else:
        target.fail_read = True
    result, _observation = await executor.execute(
        AgentDecision(action="select", target="e2", value="za", rationale="fixture"), {},
    )
    assert result.success
    assert "selection" not in result.evidence["action_preview"]


async def test_select_labels_redact_secrets_strip_controls_and_bound_length(tmp_path, monkeypatch):
    executor, _target, _decision, events = preview_fixture(tmp_path)
    target = SelectPreviewTarget(events)
    target.requested_label = "  Option\x00 fixture-private " + "x" * 300
    target.selected_label = "\n\t\x7f"
    executor.secret_values = ["fixture-private"]
    stub_execution(monkeypatch, executor, target, events)
    result, _observation = await executor.execute(
        AgentDecision(action="select", target="e2", value="option-code", rationale="fixture"), {},
    )
    assert result.success
    selection = result.evidence["action_preview"]["selection"]
    assert selection["requested_label"].startswith("Option [REDACTED]")
    assert selection["after_label"] == selection["requested_label"]
    assert len(selection["requested_label"]) == 240
    assert "before_label" not in selection
    assert "fixture-private" not in json.dumps(selection)
    assert "option-code" not in json.dumps(selection)
    assert all(ord(char) >= 32 for char in selection["requested_label"])


async def test_real_select_labels_use_visible_option_label_and_checked_native_node(tmp_path):
    playwright_api = pytest.importorskip("playwright.async_api")
    playwright = await playwright_api.async_playwright().start()
    browser = None
    try:
        try:
            browser = await playwright.chromium.launch(headless=True)
        except playwright_api.Error as exc:
            pytest.skip(f"Chromium unavailable: {exc}")
        page = await browser.new_page()
        await page.set_content("""
            <select id="sort">
              <option value="az">Name (A to Z)</option>
              <option value="za" label="Name (Z to A)">Other text</option>
            </select>
            <select name="session-token"><option value="secret-code">Private label</option></select>
        """)
        executor = PlaywrightExecutor(tmp_path, ExplorationOptions())
        executor._page = page
        target = await page.locator("#sort").element_handle()
        before = await executor._select_label_snapshot(target, "za")
        assert before == {
            "requested_label": "Name (Z to A)", "selected_label": "Name (A to Z)",
            "matches": False,
        }
        await target.select_option("za")
        assert await executor._select_label_snapshot(target, "za") == {
            "requested_label": "Name (Z to A)", "selected_label": "Name (Z to A)",
            "matches": True,
        }
        # Equal display labels cannot confirm the wrong underlying option.
        await page.locator("#sort option[value=az]").evaluate(
            "option => option.label = 'Name (Z to A)'"
        )
        await target.select_option("az")
        assert await executor._select_label_snapshot(target, "za") == {
            "requested_label": "Name (Z to A)", "selected_label": "Name (Z to A)",
            "matches": False,
        }
        assert await executor._select_label_snapshot(page.locator("[name=session-token]"), "secret-code") is None
        await page.locator("#sort").evaluate("el => el.replaceWith(el.cloneNode(true))")
        assert await executor._select_label_snapshot(target, "za") is None
        assert "za" not in before.values()
    finally:
        if browser is not None:
            await browser.close()
        await playwright.stop()


async def test_real_preview_is_css_sized_scrolls_and_never_mutates_application(tmp_path):
    playwright_api = pytest.importorskip("playwright.async_api")
    playwright = await playwright_api.async_playwright().start()
    browser = None
    try:
        try:
            browser = await playwright.chromium.launch(headless=True)
        except playwright_api.Error as exc:
            pytest.skip(f"Chromium unavailable: {exc}")
        page = await browser.new_page(
            viewport={"width": 640, "height": 360}, device_scale_factor=2,
        )
        await page.set_content("""
            <style>body{margin:0}.spacer{height:800px}
            button{display:block;width:200px;height:48px;background:#2dd4bf}</style>
            <div class="spacer"></div><input type="password" value="fixture-private">
            <button id="action">Selecionar opção</button><div style="height:800px"></div>
        """)
        await page.evaluate("""() => {
            window.fixtureMutations=[];
            new MutationObserver(changes=>window.fixtureMutations.push(...changes.map(
                change=>({type:change.type,attribute:change.attributeName})
            ))).observe(document.body,{subtree:true,attributes:true,childList:true});
        }""")
        executor = PlaywrightExecutor(tmp_path, ExplorationOptions())
        executor._page = page
        locator = page.locator("#action")
        assert (await locator.bounding_box())["y"] > 360
        preview = await executor._capture_action_preview(
            locator, AgentDecision(action="click", target="e0", rationale="fixture"),
        )
        assert preview is not None
        assert preview["box"] == await locator.bounding_box()
        assert preview["box"]["y"] >= 0
        assert preview["box"]["y"] + preview["box"]["height"] <= 360
        assert preview["image"] == {"width": 640, "height": 360}
        # The PNG itself is viewport-sized in CSS pixels, not 2x device pixels
        # or the tall full document normally captured in headless runs.
        data = (tmp_path / preview["screenshot"]).read_bytes()
        assert struct.unpack(">II", data[16:24]) == (640, 360)
        password_box = await page.locator('input').bounding_box()
        coordinates = {
            "password": [
                int(password_box["x"] + password_box["width"] / 2),
                int(password_box["y"] + password_box["height"] / 2),
            ],
            "target": [int(preview["box"]["x"] + 10), int(preview["box"]["y"] + 10)],
        }
        pixels = await page.evaluate("""async ({data,coordinates}) => {
            const image=new Image();image.src=`data:image/png;base64,${data}`;await image.decode();
            const canvas=document.createElement('canvas');canvas.width=image.width;canvas.height=image.height;
            const context=canvas.getContext('2d');context.drawImage(image,0,0);
            return Object.fromEntries(Object.entries(coordinates).map(([name,[x,y]])=>
                [name,Array.from(context.getImageData(x,y,1,1).data)]));
        }""", {"data": base64.b64encode(data).decode(), "coordinates": coordinates})
        assert pixels["password"] == [16, 20, 22, 255]
        assert pixels["target"] == [45, 212, 191, 255]
        assert await page.evaluate("window.fixtureMutations") == []
        assert await page.locator('input').input_value() == "fixture-private"
        assert not await page.locator('[data-autojev-action-marker]').count()
    finally:
        if browser is not None:
            await browser.close()
        await playwright.stop()
