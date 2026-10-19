"""Fullscreen evidence viewer checks; all reports and requests are isolated."""
import base64

import pytest
from autojev.adapters.report import StandaloneHtmlReport
from PIL import Image, ImageDraw
from playwright.async_api import async_playwright, expect
from test_report_execution_history import fixture
from test_report_revision_browser import connected_page, no_duplicate_ids, rendered_report


def lightbox_report(tmp_path):
    source, _, images = rendered_report(tmp_path)
    portrait = tmp_path / "latest-rerun" / "screenshots" / "portrait.png"
    picture = Image.new("RGB", (720, 1600), "#f1f4f8")
    draw = ImageDraw.Draw(picture)
    for row in range(8):
        draw.rounded_rectangle((40, 30 + row * 195, 680, 200 + row * 195), radius=12, fill="#c5d9e8" if row % 2 else "#94b2ca")
    picture.save(portrait, format="PNG")
    run = fixture()
    run["scenarios"][0]["evidence"]["timeline"].append({"kind": "observation", "title": "Depois da ação", "screenshot": "portrait.png"})
    StandaloneHtmlReport(tmp_path).generate(run, str(source))
    images["portrait"] = "data:image/png;base64," + base64.b64encode(portrait.read_bytes()).decode()
    return source.read_text(), images


async def assert_fitted_image(page):
    image = page.locator("#evidence-preview")
    await expect(image).to_be_visible()
    geometry = await image.evaluate("""image => {
      const viewport = image.closest('.evidence-lightbox-viewport');
      const box = image.getBoundingClientRect(), area = viewport.getBoundingClientRect();
      return {x: box.x, y: box.y, width: box.width, height: box.height, naturalWidth: image.naturalWidth,
        naturalHeight: image.naturalHeight, left: area.x, top: area.y, availableWidth: viewport.clientWidth, availableHeight: viewport.clientHeight};
    }""")
    gutter = 10 if geometry["availableWidth"] < 600 else 58
    padding = 10 if geometry["availableWidth"] < 600 else 20
    available_width = geometry["availableWidth"] - gutter * 2
    available_height = geometry["availableHeight"] - padding * 2
    assert abs(geometry["width"] / geometry["height"] - geometry["naturalWidth"] / geometry["naturalHeight"]) < .01
    assert geometry["width"] <= available_width + 1 and geometry["height"] <= available_height + 1
    assert max(geometry["width"] / available_width, geometry["height"] / available_height) >= .99
    assert geometry["x"] >= geometry["left"] and geometry["y"] >= geometry["top"]
    assert await page.locator(".evidence-lightbox-viewport").evaluate("el => el.scrollWidth <= el.clientWidth + 1 && el.scrollHeight <= el.clientHeight + 1")


@pytest.mark.asyncio
@pytest.mark.parametrize("viewport", [{"width": 1920, "height": 1080}, {"width": 1440, "height": 900}, {"width": 390, "height": 844}], ids=["wide", "desktop", "mobile"])
async def test_lightbox_fills_browser_and_fits_landscape_portrait_and_resize(tmp_path, viewport):
    markup, images = lightbox_report(tmp_path)
    requests = []
    state = {"key": "revisions-v2", "markup": markup}
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        context, page = await connected_page(browser, markup, state, requests)
        page.set_default_timeout(5000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            await page.set_viewport_size(viewport)
            trigger = page.locator('#scenarios details[data-case-index="0"] .step-evidence-layout:visible').get_by_role("button", name="Ampliar evidência", exact=True)
            original_hash = page.url
            await page.locator('#scenarios details[data-case-index="0"] .step-evidence-viewport:visible').hover()
            await trigger.click()
            dialog = page.locator("#evidence-dialog")
            await expect(dialog).to_be_visible()
            await expect(page.locator("#evidence-preview")).to_have_js_property("naturalWidth", 64)
            box = await dialog.bounding_box()
            assert abs(box["x"]) <= 1 and abs(box["y"]) <= 1
            assert abs(box["width"] - viewport["width"]) <= 1 and abs(box["height"] - viewport["height"]) <= 1
            await assert_fitted_image(page)
            await expect(dialog.locator(".evidence-lightbox-counter")).to_have_text("1 de 2")
            await expect(dialog.get_by_role("button", name="Imagem anterior", exact=True)).to_be_disabled()
            await page.screenshot(path=str(tmp_path / f"fullscreen-landscape-{viewport['width']}.png"))
            await dialog.get_by_role("button", name="Próxima imagem", exact=True).click()
            await expect(page.locator("#evidence-preview")).to_have_js_property("naturalHeight", 1600)
            await expect(dialog.locator(".evidence-lightbox-counter")).to_have_text("2 de 2")
            await expect(page.locator("#download-evidence")).to_have_attribute("download", "portrait.png")
            await assert_fitted_image(page)
            await page.screenshot(path=str(tmp_path / f"fullscreen-portrait-{viewport['width']}.png"))
            resized = {"width": 844, "height": 390} if viewport["width"] < 600 else {"width": 1280, "height": 720}
            await page.set_viewport_size(resized)
            await page.wait_for_function("() => { const image = document.getElementById('evidence-preview'); return image.getBoundingClientRect().height < innerHeight - 70; }")
            await assert_fitted_image(page)
            await page.keyboard.press("ArrowLeft")
            await expect(dialog.locator(".evidence-lightbox-counter")).to_have_text("1 de 2")
            await expect(page.locator("#evidence-preview")).to_have_attribute("src", images["latest-rerun"])
            assert page.url == original_hash
            await page.keyboard.press("Escape")
            await expect(dialog).not_to_be_visible()
            await expect(trigger).to_be_focused()
            assert not await page.locator("body").evaluate("el => el.classList.contains('evidence-lightbox-open')")
            # Historical captures never enter the current execution's gallery.
            await page.locator('#case-execution-list [data-execution-version="1"]').click()
            await page.locator("#case-selected-execution .step-evidence-viewport:visible").hover()
            await page.locator("#case-selected-execution .step-evidence-layout:visible").get_by_role("button", name="Ampliar evidência", exact=True).click()
            await expect(dialog.locator(".evidence-lightbox-counter")).to_have_text("1 de 1")
            await expect(page.locator("#evidence-preview")).to_have_attribute("src", images["old-rerun"])
            await expect(dialog.locator(".lightbox-next")).not_to_be_visible()
            async with page.expect_download() as pending:
                await page.locator("#download-evidence").click()
            saved = tmp_path / "original.png"
            await (await pending.value).save_as(saved)
            assert saved.read_bytes() == base64.b64decode(images["old-rerun"].split(",", 1)[1])
            await page.locator("#close-evidence").click()
            await no_duplicate_ids(page)
            assert not errors
            assert all(method == "GET" for method, _ in requests)
        finally:
            await context.close()
            await browser.close()


@pytest.mark.asyncio
async def test_lightbox_zoom_pan_keyboard_and_offline_export_preserve_original_bytes(tmp_path):
    markup, images = lightbox_report(tmp_path)
    requests = []
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        context, page = await connected_page(browser, markup, {"key": "revisions-v2", "markup": markup}, requests)
        page.set_default_timeout(5000)
        try:
            await page.locator('#scenarios details[data-case-index="0"] .step-evidence-viewport:visible').hover()
            await page.locator('#scenarios details[data-case-index="0"] .step-evidence-layout:visible').get_by_role("button", name="Ampliar evidência", exact=True).click()
            dialog = page.locator("#evidence-dialog")
            for _ in range(4):
                await dialog.get_by_role("button", name="Aumentar zoom da tela cheia", exact=True).click()
            await expect(dialog.locator(".lightbox-zoom-value")).to_have_text("200%")
            viewport = dialog.locator(".evidence-lightbox-viewport")
            await expect(viewport).to_have_attribute("data-pan-enabled", "true")
            box = await viewport.bounding_box()
            x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            before = await viewport.evaluate("el => ({x: el.scrollLeft, y: el.scrollTop})")
            await page.mouse.move(x, y)
            await page.mouse.down()
            await page.mouse.move(x - 100, y - 80, steps=8)
            await page.mouse.up()
            after = await viewport.evaluate("el => ({x: el.scrollLeft, y: el.scrollTop})")
            assert after["x"] > before["x"] + 90 and after["y"] > before["y"] + 70
            await expect(dialog).to_be_visible()
            await dialog.get_by_role("button", name="Ajustar imagem à tela", exact=True).click()
            await expect(viewport).to_have_attribute("data-pan-enabled", "false")
            await assert_fitted_image(page)
            await page.keyboard.press("ArrowRight")
            await expect(dialog.locator(".evidence-lightbox-counter")).to_have_text("2 de 2")
            await page.keyboard.press("+")
            await expect(dialog.locator(".lightbox-zoom-value")).to_have_text("125%")
            await page.keyboard.press("0")
            await expect(dialog.locator(".lightbox-zoom-value")).to_have_text("100%")
            await page.keyboard.press("Escape")
            async with page.expect_download() as pending:
                await page.locator("#download-report").click()
            exported = tmp_path / "fullscreen-offline.html"
            await (await pending.value).save_as(exported)
            saved_markup = exported.read_text()
            assert 'class="evidence-lightbox-surface"' not in saved_markup
            for image in images.values():
                assert saved_markup.count(image) == markup.count(image)
            offline_context = await browser.new_context(viewport={"width": 1440, "height": 900})
            offline_requests = []
            offline_context.on("request", lambda request: offline_requests.append(request.url))
            offline = await offline_context.new_page()
            await offline.goto(exported.as_uri() + "#view/results/CT147")
            await offline.locator('#scenarios details[data-case-index="0"] .step-evidence-viewport:visible').hover()
            await offline.locator('#scenarios details[data-case-index="0"] .step-evidence-layout:visible').get_by_role("button", name="Ampliar evidência", exact=True).click()
            await expect(offline.locator("#evidence-dialog")).to_have_css("width", "1440px")
            await expect(offline.locator(".evidence-lightbox-counter")).to_have_text("1 de 2")
            assert not [url for url in offline_requests if url.startswith(("http:", "https:"))]
            await no_duplicate_ids(offline)
            await offline_context.close()
        finally:
            await context.close()
            await browser.close()


@pytest.mark.asyncio
async def test_open_lightbox_is_not_discarded_by_background_result_refresh(tmp_path):
    markup, images = lightbox_report(tmp_path)
    _, newer, _ = rendered_report(tmp_path, key="revisions-v3", title="Novo resultado recebido")
    requests = []
    state = {"key": "revisions-v2", "markup": newer}
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        context, page = await connected_page(browser, markup, state, requests)
        page.set_default_timeout(5000)
        try:
            await page.locator('#scenarios details[data-case-index="0"] .step-evidence-viewport:visible').hover()
            await page.locator('#scenarios details[data-case-index="0"] .step-evidence-layout:visible').get_by_role("button", name="Ampliar evidência", exact=True).click()
            state["key"] = "revisions-v3"
            await page.evaluate("window.dispatchEvent(new Event('focus'))")
            await expect(page.locator(".report-revision-refresh")).to_be_attached()
            await expect(page.locator("#evidence-dialog")).to_be_visible()
            await expect(page.locator("#evidence-preview")).to_have_attribute("src", images["latest-rerun"])
            assert ("GET", "/api/explorations/root/report") not in requests
            await page.locator("#close-evidence").click()
            await page.get_by_role("button", name="Atualizar resultados", exact=True).click()
            await expect(page.locator("#case-title")).to_have_text("Novo resultado recebido")
            await no_duplicate_ids(page)
            assert all(method == "GET" for method, _ in requests)
        finally:
            await context.close()
            await browser.close()
