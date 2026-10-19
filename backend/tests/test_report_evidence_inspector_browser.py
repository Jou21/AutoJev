"""Evidence viewer QA in isolated reports; never execute a real user test."""
import base64
import re

import pytest
from autojev.adapters.report import StandaloneHtmlReport
from playwright.async_api import async_playwright, expect
from test_report_execution_history import fixture
from test_report_revision_browser import connected_page, no_duplicate_ids, rendered_report


def inspector_report(tmp_path):
    source, _, images = rendered_report(tmp_path)
    run = fixture()
    timeline = run["scenarios"][0]["evidence"]["timeline"]
    timeline[0].update({"occurred_at": "2026-10-18T15:22:24Z", "url": "https://shop.test/inventory.html?token=private-token",
                        "message": "Estado inicial registrado antes de continuar o roteiro."})
    timeline[1].update({"screenshot": "same.png", "occurred_at": "2026-10-18T15:22:25Z", "url": timeline[0]["url"],
                        "action_preview": {"version": "action-target-v1", "screenshot": "same.png", "url": timeline[0]["url"]}})
    timeline.append({"kind": "observation", "title": "Imagem anexada pelo usuário", "image_source": "user_upload",
                     "screenshot": "same.png", "attached_at": "2026-10-18T15:23:00Z"})
    StandaloneHtmlReport(tmp_path).generate(run, str(source))
    return source, source.read_text(), images


@pytest.mark.asyncio
async def test_zoomed_image_can_be_panned_with_real_mouse_without_opening_preview(tmp_path):
    _, markup, images = inspector_report(tmp_path)
    requests = []
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        context, page = await connected_page(browser, markup, {"key": "revisions-v2", "markup": markup}, requests)
        page.set_default_timeout(5000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            await page.set_viewport_size({"width": 1440, "height": 900})
            viewer = page.locator('#scenarios details[data-case-index="0"] .step-evidence-layout:visible')
            viewport = viewer.locator(".step-evidence-viewport")
            for _ in range(4):
                await viewer.get_by_role("button", name="Aumentar zoom", exact=True).click()
            await expect(viewer.locator(".evidence-zoom-reset")).to_have_text("200%")
            await expect(viewport).to_have_attribute("data-pan-enabled", "true")
            assert await viewport.evaluate("el => el.scrollWidth > el.clientWidth + 100 && el.scrollHeight > el.clientHeight + 50")
            box = await viewport.bounding_box()
            start_x, start_y = box["x"] + box["width"] * .7, box["y"] + box["height"] * .65
            await page.mouse.move(start_x, start_y)
            await page.mouse.down(button="left")
            await expect(viewport).to_have_class(re.compile(r"\bis-panning\b"))
            assert await viewer.locator(".step-preview-button").evaluate("el => getComputedStyle(el).cursor") == "grabbing"
            await page.mouse.move(start_x - 140, start_y - 90, steps=10)
            assert await viewport.evaluate("el => el.scrollLeft >= 135 && el.scrollTop >= 85")
            await page.screenshot(path=str(tmp_path / "evidence-panning-active.png"))
            await page.mouse.up(button="left")
            assert not await viewport.evaluate("el => el.classList.contains('is-panning')")
            await expect(page.locator("#evidence-dialog")).not_to_be_visible()
            assert await viewer.locator(".step-preview-button").evaluate("el => getComputedStyle(el).cursor") == "grab"
            # Pointer capture keeps tracking when the mouse leaves the image.
            await page.mouse.move(start_x, start_y)
            await page.mouse.down(button="left")
            await page.mouse.move(box["x"] - 60, box["y"] - 50, steps=10)
            await page.mouse.up(button="left")
            assert not await viewport.evaluate("el => el.classList.contains('is-panning')")
            await expect(page.locator("#evidence-dialog")).not_to_be_visible()
            before = await viewport.evaluate("el => ({left: el.scrollLeft, top: el.scrollTop})")
            await page.mouse.move(start_x - 100, start_y - 70)
            await page.mouse.down(button="left")
            await page.mouse.move(start_x, start_y, steps=8)
            await page.mouse.up(button="left")
            after = await viewport.evaluate("el => ({left: el.scrollLeft, top: el.scrollTop})")
            assert after["left"] < before["left"] and after["top"] < before["top"]
            await expect(page.locator("#evidence-dialog")).not_to_be_visible()
            # A real click and a keyboard activation still open the preview.
            await page.mouse.click(start_x, start_y)
            await expect(page.locator("#evidence-dialog")).to_be_visible()
            await page.locator("#close-evidence").click()
            await viewer.locator(".step-preview-button").press("Enter")
            await expect(page.locator("#evidence-dialog")).to_be_visible()
            await page.locator("#close-evidence").click()
            await viewer.get_by_role("button", name="Restaurar zoom para 100%", exact=True).click()
            await expect(viewport).to_have_attribute("data-pan-enabled", "false")
            assert await viewport.evaluate("el => el.scrollLeft === 0 && el.scrollTop === 0")
            await expect(viewer.locator("img")).to_have_attribute("src", images["latest-rerun"])
            await no_duplicate_ids(page)
            assert not errors
            assert all(method == "GET" for method, _ in requests)
        finally:
            await context.close()
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("viewport", [{"width": 1920, "height": 1080}, {"width": 1440, "height": 1000}, {"width": 390, "height": 844}], ids=["wide", "desktop", "mobile"])
async def test_evidence_viewer_zoom_metadata_download_and_owned_history(tmp_path, viewport):
    _, markup, images = inspector_report(tmp_path)
    requests = []
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        context, page = await connected_page(browser, markup, {"key": "revisions-v2", "markup": markup}, requests)
        page.set_default_timeout(5000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            await page.set_viewport_size(viewport)
            stage = page.locator('#scenarios details[data-case-index="0"] .case-step-stage')
            viewer = stage.locator(".step-evidence-layout:visible")
            field = lambda name: viewer.locator(f'[data-evidence-field="{name}"] dd')
            await expect(viewer).to_be_visible()
            await expect(viewer.get_by_role("button", name="HTML indisponível", exact=False)).to_be_disabled()
            await expect(stage.locator(".case-step-outcome")).to_have_text("Captura registrada")
            await expect(stage.locator(".case-step-outcome")).to_have_attribute("data-step-state", "recorded")
            await expect(field("type")).to_have_text("Captura de tela")
            await expect(field("filename").locator(".step-evidence-filename")).to_have_text("same.png")
            await expect(field("resolution")).to_have_text("64 × 40 px")
            await expect(field("date")).to_contain_text("18/10/2026")
            assert "private-token" not in await field("url").text_content()
            assert (await field("url").locator("a").get_attribute("href")).startswith("https://shop.test/inventory.html")
            image_bytes = base64.b64decode(images["latest-rerun"].split(",", 1)[1])
            await expect(field("size")).to_have_text(f"{len(image_bytes)} B")
            await expect(viewer.locator("img")).to_have_attribute("src", images["latest-rerun"])
            viewport_element = viewer.locator(".step-evidence-viewport")
            await viewer.get_by_role("button", name="Aumentar zoom", exact=True).click()
            await viewer.get_by_role("button", name="Aumentar zoom", exact=True).click()
            await expect(viewer.locator(".evidence-zoom-reset")).to_have_text("150%")
            assert await viewport_element.evaluate("el => el.scrollWidth > el.clientWidth + 20")
            await viewer.get_by_role("button", name="Restaurar zoom para 100%", exact=True).click()
            await expect(viewer.locator(".evidence-zoom-reset")).to_have_text("100%")
            await page.evaluate("""() => {
              window.__copied = [];
              Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async value => { window.__copied.push(value); }}});
            }""")
            await viewer.get_by_role("button", name="Copiar nome do arquivo", exact=True).click()
            assert await page.evaluate("window.__copied") == ["same.png"]
            expand = viewer.get_by_role("button", name="Ampliar evidência", exact=True)
            await expand.click()
            await expect(page.locator("#evidence-dialog")).to_be_visible()
            await expect(page.locator("#evidence-preview")).to_have_attribute("src", images["latest-rerun"])
            await page.locator("#close-evidence").click()
            await expect(expand).to_be_focused()
            async with page.expect_download() as pending:
                await viewer.get_by_role("link", name="Baixar imagem", exact=True).click()
            downloaded = tmp_path / "evidence.png"
            await (await pending.value).save_as(downloaded)
            assert downloaded.read_bytes() == image_bytes
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            viewer_box = await viewer.locator(".step-evidence-viewer").bounding_box()
            info_box = await viewer.locator(".step-evidence-info").bounding_box()
            if viewport["width"] == 1920:
                assert info_box["x"] > viewer_box["x"] + viewer_box["width"]
                assert abs(info_box["y"] - viewer_box["y"]) <= 1
            if viewport["width"] == 390:
                assert info_box["y"] >= viewer_box["y"] + viewer_box["height"]
            await stage.locator(".case-step-content").evaluate("el => el.scrollTop = 0")
            await page.screenshot(path=str(tmp_path / f"evidence-inspector-{viewport['width']}.png"), full_page=True)
            await stage.locator(".case-step-next").click()
            await expect(viewer.locator(".step-evidence-choice option")).to_have_count(2)
            await expect(stage.locator(".case-step-outcome")).to_have_attribute("data-step-state", "unknown")
            await viewer.locator(".step-evidence-choice").select_option("1")
            await expect(field("type")).to_have_text("Captura de tela")
            await expect(viewer.locator("figure:visible")).to_have_count(1)
            await page.emulate_media(media="print")
            assert await viewer.locator("figure").evaluate_all("figures => figures.every(figure => getComputedStyle(figure).display !== 'none')")
            await page.emulate_media(media="screen")
            await expect(viewer.locator("figure:visible")).to_have_count(1)
            await stage.locator(".case-step-next").click()
            await expect(field("type")).to_have_text("Imagem do usuário")
            await expect(stage.locator(".case-step-outcome")).to_have_text("Imagem anexada")
            await page.locator('#case-execution-list [data-execution-version="1"]').click()
            history_viewer = page.locator("#case-selected-execution .step-evidence-layout:visible")
            await expect(history_viewer.locator("img")).to_have_attribute("src", images["old-rerun"])
            await expect(history_viewer.locator('[data-evidence-field="date"] dd')).to_have_text("Não registrada")
            async with page.expect_download() as pending:
                await page.locator("#download-report").click()
            exported = tmp_path / "inspector-offline.html"
            await (await pending.value).save_as(exported)
            saved_markup = exported.read_text()
            assert 'class="step-evidence-layout"' not in saved_markup
            for image in images.values():
                assert saved_markup.count(image) == markup.count(image)
            offline_context = await browser.new_context(viewport=viewport)
            offline_requests = []
            offline_context.on("request", lambda request: offline_requests.append(request.url))
            offline = await offline_context.new_page()
            await offline.goto(exported.as_uri() + "#view/results/CT147?execution=1")
            await expect(offline.locator("#case-selected-execution .step-evidence-download")).to_have_attribute("href", images["old-rerun"])
            assert not [url for url in offline_requests if url.startswith(("http:", "https:"))]
            await no_duplicate_ids(offline)
            await offline_context.close()
            await no_duplicate_ids(page)
            assert not errors
            assert all(method == "GET" for method, _ in requests)
        finally:
            await context.close()
            await browser.close()
