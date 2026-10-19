"""Real Chromium checks for the J rail and ephemeral shared preview bridge."""
import copy

import pytest
from playwright.async_api import expect

from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.domain.models import ExplorationOptions


PAYLOAD = {
    "version": 1, "phase": "waiting_capture", "running": True,
    "checkpoint": {"id": "db-1", "version": 1, "gap": 1, "order": 0},
    "preparation": {"version": 1}, "capture_count": 1, "captures_completed": 0,
    "progress": {"completed": 1, "total": 3, "steps": [
        {"id": "source-0", "title": "Antes da ação", "state": "done", "label": "Estado registrado"},
        {"id": "checkpoint-db-1", "title": "Aguardar print", "state": "waiting", "label": "Aguardando seu print"},
        {"id": "source-1", "title": "Selecionar opção", "state": "pending", "label": "Aguardando"},
    ]},
}


@pytest.fixture
async def rerun_ui(tmp_path):
    from playwright.async_api import async_playwright
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()
        executor = PlaywrightExecutor(tmp_path, ExplorationOptions(capture_screenshots=False), headless=False)
        executor._page, executor._context = page, context
        captures, stages = [], []
        preview = {"checkpoint_id": "db-1", "checkpoint_version": 1, "revision": 0,
                   "changed": True, "image": None}

        async def capture(*args):
            captures.append(args)
            return {"accepted": True}

        async def stage(checkpoint_id, checkpoint_version, expected_revision, image):
            assert checkpoint_id == "db-1" and checkpoint_version == 1
            if expected_revision != preview["revision"]:
                raise ValueError("A prévia foi atualizada")
            stages.append(image)
            preview.update(revision=preview["revision"] + 1,
                           image={"data_url": image, "name": "Print sincronizado", "size": 100} if image else None)
            return copy.deepcopy(preview)

        executor.configure_rerun_panel(capture)
        executor.configure_rerun_preview(stage)
        await executor._prepare_page(page)
        await context.route("**/*", executor._guard_navigation)
        await page.set_content('<main><h1>Aplicação descartável para teste local</h1></main>')
        await executor.update_rerun_preview(preview)
        assert await executor.update_rerun_panel(PAYLOAD)
        await executor.show_intervention_panel("Aguardando sua imagem")
        try:
            yield executor, page, captures, stages, preview
        finally:
            await executor.close()
            await browser.close()


async def collapsed(page):
    return await page.locator(".autojev-panel-surface").evaluate("el => getComputedStyle(el).opacity === '0'")


@pytest.mark.asyncio
async def test_j_minimizes_attention_and_stays_collapsed_during_polling_and_frame_recreation(rerun_ui):
    executor, page, captures, stages, _ = rerun_ui
    rail = page.locator(".autojev-panel-rail")
    assert not await collapsed(page)
    await rail.click()
    assert await collapsed(page)
    assert await rail.get_attribute("aria-expanded") == "false"
    for _ in range(3):
        await executor.show_intervention_panel("Aguardando sua imagem")
        assert await executor.update_rerun_panel(PAYLOAD)
        assert await collapsed(page)
    # The optional frame may be recreated without overriding the user's J.
    await page.locator("#autojev-rerun-frame").evaluate("el => el.remove()")
    assert await executor.update_rerun_panel(PAYLOAD)
    assert await collapsed(page)
    box = await rail.bounding_box()
    assert box and 0 <= box["x"] < 1280 and box["width"] > 0
    await rail.click()
    assert not await collapsed(page)
    assert await rail.get_attribute("aria-expanded") == "true"
    await rail.focus()
    await page.keyboard.press("Space")
    assert await collapsed(page)
    await page.keyboard.press("Enter")
    assert not await collapsed(page)
    assert captures == stages == []


@pytest.mark.asyncio
async def test_panel_staging_shares_preview_but_only_confirmation_attaches(rerun_ui, user_print_image):
    executor, page, captures, stages, preview = rerun_ui
    frame = executor._rerun_panel.frame
    await frame.locator("#file-input").set_input_files({"name": "public-test-image.png",
        "mimeType": "image/png", "buffer": user_print_image["bytes"]})
    await expect(frame.locator("#confirm-capture")).to_be_enabled()
    assert stages == [user_print_image["data_url"]]
    assert preview["revision"] == 1 and preview["image"]["data_url"] == user_print_image["data_url"]
    assert captures == []
    # Folding the panel must neither destroy the frame nor its selected image.
    await page.locator(".autojev-panel-rail").click()
    assert await collapsed(page)
    await executor.update_rerun_preview(preview)
    assert await executor.update_rerun_panel(PAYLOAD)
    await page.locator(".autojev-panel-rail").click()
    assert await frame.locator("#preview").get_attribute("src") == user_print_image["data_url"]
    await frame.locator("#confirm-capture").click()
    assert captures == [("db-1", 1, user_print_image["data_url"])]


@pytest.mark.asyncio
async def test_main_app_preview_and_removal_arrive_without_echo_or_automatic_attach(rerun_ui, user_print_image):
    executor, _page, captures, stages, preview = rerun_ui
    frame = executor._rerun_panel.frame
    preview.update(revision=1, image={"data_url": user_print_image["data_url"],
                                    "name": "Print de outra tela", "size": len(user_print_image["bytes"])})
    await executor.update_rerun_preview(preview)
    await executor.update_rerun_panel(PAYLOAD)
    assert await frame.locator("#preview").get_attribute("src") == user_print_image["data_url"]
    assert not await frame.locator("#confirm-capture").is_disabled()
    assert captures == stages == []
    preview.update(revision=2, image=None)
    await executor.update_rerun_preview(preview)
    await executor.update_rerun_panel(PAYLOAD)
    assert await frame.locator("#selected-file").is_hidden()
    assert await frame.locator("#confirm-capture").is_disabled()
    assert captures == stages == []


@pytest.mark.asyncio
async def test_parent_cannot_stage_or_clear_the_shared_preview(rerun_ui, user_print_image):
    executor, page, captures, stages, _ = rerun_ui
    command = {"op": "preview", "nonce": executor._rerun_panel._nonce,
               "checkpoint_id": "db-1", "checkpoint_version": 1,
               "expected_revision": 0, "image_data_url": user_print_image["data_url"]}
    assert await page.evaluate("""async command => {
      try { await window.__autojev_rerun_command(command); return false; }
      catch (_) { return true; }
    }""", command)
    assert captures == stages == []
