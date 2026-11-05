"""A mask that ignores the single ``input`` event of fill() still gets the value."""

from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.domain.models import AgentDecision, ExplorationOptions

PAGE = """<!doctype html><meta charset="utf-8"><title>mask</title>
<label for="d">Data</label><input id="d" placeholder="01/01/2026">
<label for="p">Plain</label><input id="p">
<label for="v">Validates on blur</label><input id="v">
<p id="state">pristine</p>
<script>
  // A custom mask: it only trusts values that arrive through key presses.
  let typing = false;
  const field = document.getElementById('d');
  field.addEventListener('keydown', () => { typing = true; });
  field.addEventListener('input', () => { if (!typing) field.value = ''; typing = false; });
  document.getElementById('v').addEventListener('change', () => { document.getElementById('state').textContent = 'validated'; });
</script>"""


def executor_for(tmp_path):
    class Executor(PlaywrightExecutor):
        async def _guard_navigation(self, route, request):
            if request.url.startswith("https://mask.fixture.test/"):
                await route.fulfill(content_type="text/html; charset=utf-8", body=PAGE)
            else:
                await route.abort()

    return Executor(tmp_path, ExplorationOptions(), headless=True)


async def test_fill_falls_back_to_typing_when_the_mask_drops_the_value(tmp_path):
    executor = executor_for(tmp_path)
    try:
        observation = await executor.start("https://mask.fixture.test/")
        date = next(e for e in observation.interactive_elements if e["tag"] == "input" and e["label"] == "Data")
        result, _ = await executor.execute(
            AgentDecision("fill", "x", target=date["ref"], value="15/03/2026"), None
        )
        assert result.success
        assert await executor._page.locator("#d").input_value() == "15/03/2026"
    finally:
        await executor.close()


async def test_a_field_that_accepts_fill_is_not_retyped(tmp_path):
    executor = executor_for(tmp_path)
    try:
        observation = await executor.start("https://mask.fixture.test/")
        plain = next(e for e in observation.interactive_elements if e["tag"] == "input" and e["label"] == "Plain")
        calls = []
        original = executor._page.keyboard.type

        async def spy(*args, **kwargs):
            calls.append(args)
            return await original(*args, **kwargs)

        executor._page.keyboard.type = spy
        await executor.execute(AgentDecision("fill", "x", target=plain["ref"], value="abc"), None)
        assert await executor._page.locator("#p").input_value() == "abc" and calls == []
    finally:
        await executor.close()


def test_mask_punctuation_does_not_count_as_a_difference():
    same = PlaywrightExecutor._same_characters
    assert same("15032026", "15/03/2026") and same("529.982.247-25", "52998224725")
    assert not same("", "15/03/2026") and not same("15/03", "15/03/2026")


async def test_fill_can_leave_the_field_in_the_same_action(tmp_path):
    executor = executor_for(tmp_path)
    try:
        observation = await executor.start("https://mask.fixture.test/")
        field = next(e for e in observation.interactive_elements if e["tag"] == "input" and e["label"] == "Validates on blur")
        result, _ = await executor.execute(AgentDecision("fill", "x", target=field["ref"], value="abc"), None)
        assert result.success and await executor._page.locator("#state").inner_text() == "pristine"   # typed, not left

        again, _ = await executor.execute(AgentDecision("fill", "x", target=field["ref"], value="abcd", key="Tab"), None)
        assert again.success and await executor._page.locator("#state").inner_text() == "validated"   # Tab fired change
    finally:
        await executor.close()


async def test_a_fast_fill_skips_the_preview_the_settle_and_the_screenshot(tmp_path):
    executor = executor_for(tmp_path)
    try:
        observation = await executor.start("https://mask.fixture.test/")
        field = next(e for e in observation.interactive_elements if e["tag"] == "input" and e["label"] == "Plain")

        slow, slow_after = await executor.execute(AgentDecision("fill", "x", target=field["ref"], value="a"), None)
        assert slow.screenshot and "action_preview" in slow.evidence and slow.evidence["phase_ms"].get("dom_settle", 0) >= 0
        assert slow_after.screenshot

        observation = await executor.observe("again", capture_screenshot=False)
        field = next(e for e in observation.interactive_elements if e["tag"] == "input" and e["label"] == "Plain")
        fast, fast_after = await executor.execute(
            AgentDecision("fill", "x", target=field["ref"], value="b", fast=True), None
        )
        assert fast.success and await executor._page.locator("#p").input_value() == "b"
        assert fast.screenshot is None and fast_after.screenshot is None
        assert "action_preview" not in fast.evidence
        assert "dom_settle" not in fast.evidence["phase_ms"] and "api_wait" not in fast.evidence["phase_ms"]
        # The observation still describes the page, so the planner can go on.
        assert any(e["label"] == "Plain" for e in fast_after.interactive_elements if e.get("label"))
    finally:
        await executor.close()
