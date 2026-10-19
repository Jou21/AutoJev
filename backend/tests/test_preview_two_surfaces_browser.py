"""Opt-in real frontend + isolated backend + browser-panel integration.

Run with AUTOJEV_FRONTEND_E2E=1 and a frontend on localhost:3000. All API
requests are redirected to a temporary database; no user run is executed.
"""
import asyncio
import os
import socket
from urllib.parse import urlsplit

import pytest

from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.config import Settings
from autojev.domain.models import ExplorationOptions, ExplorationRequest, ModelSelection
from autojev.main import create_app


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("AUTOJEV_FRONTEND_E2E") != "1", reason="Opt-in local frontend browser test")
async def test_real_frontend_and_floating_panel_share_selection_without_attaching(tmp_path, user_print_image):
    import uvicorn
    from playwright.async_api import async_playwright, expect

    settings = Settings(data_dir=tmp_path, database_path=tmp_path / "fixture.sqlite3",
                        artifact_dir=tmp_path / "artifacts", secret_key="public-fixture",
                        cors_origins=("http://localhost:3000",), headless=True,
                        browser="chromium", http_timeout_seconds=1)
    app = create_app(settings)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    fixture_origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    executor = None
    stream = None
    keep_alive = None
    try:
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(0.02)
        repo = app.state.repository
        service = app.state.scenario_rerun_service
        request = ExplorationRequest("https://fixture.invalid/inventory", ModelSelection("fixture", "fixture"),
                                     ModelSelection("fixture", "fixture"), ExplorationOptions(mode="single_ct_rerun"))
        source_id = repo.create_exploration(request)
        repo.update_exploration(source_id, status="completed")
        run_id = repo.create_exploration(request)
        repo.update_exploration(run_id, status="waiting_input")
        checkpoint = {"id": "db-1", "version": 1, "gap": 1, "order": 0}
        service.states[run_id] = {"running": True, "phase": "waiting_capture",
            "source_exploration_id": source_id, "source_scenario_id": "CT001",
            "capture_checkpoint": checkpoint, "capture_count": 1, "captures_completed": 0,
            "capture_action_index": 1, "step_plan": {"version": 1, "steps": [
                {"id": "checkpoint-db-1", "title": "Aguardar print", "kind": "capture_wait", "checkpoint_id": "db-1"},
            ]}}
        service.capture_events[run_id] = asyncio.Event()
        keep_alive = asyncio.create_task(asyncio.Event().wait())
        service.tasks[run_id] = keep_alive
        forbidden_captures = []

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(viewport={"width": 1440, "height": 900})

            async def isolated_api(route):
                original = urlsplit(route.request.url)
                if "/api/" not in original.path:
                    await route.continue_()
                    return
                if route.request.method not in {"GET", "OPTIONS"}:
                    assert original.path == f"/api/explorations/{run_id}/capture-print/preview", "No execution/attachment authorized"
                response = await route.fetch(url=fixture_origin + original.path + ("?" + original.query if original.query else ""))
                await route.fulfill(response=response)

            await context.route("**/api/**", isolated_api)
            main = await context.new_page()
            await main.goto(f"http://localhost:3000/#/exploracoes/{run_id}/acompanhar")
            await main.get_by_role("heading", name="Adicione o print que você quiser", exact=True).wait_for()
            floating = await context.new_page()
            await floating.set_content("<main><h1>Aplicação descartável</h1></main>")
            executor = PlaywrightExecutor(tmp_path / "unused", ExplorationOptions(capture_screenshots=False), headless=False)
            executor._page, executor._context = floating, context

            async def forbidden_capture(*args):
                forbidden_captures.append(args)
                raise AssertionError("No final confirmation was authorized in this test")

            async def preview(*args):
                return await service.preview_from_browser_panel(run_id, *args)

            executor.configure_rerun_panel(forbidden_capture)
            executor.configure_rerun_preview(preview)
            await executor._prepare_page(floating)
            await floating.route("**/*", executor._guard_navigation)
            stream = asyncio.create_task(service._stream_browser_panel(run_id, executor, []))
            async with asyncio.timeout(10):
                while executor._rerun_panel.frame is None:
                    await asyncio.sleep(0.05)
            frame = executor._rerun_panel.frame
            await frame.locator("#capture").wait_for()
            public_file = {"name": "public-sync-fixture.png", "mimeType": "image/png", "buffer": user_print_image["bytes"]}
            # Paste/file choice in the floating browser becomes visible in the
            # actual React uploader behind it, before any final attachment.
            await frame.locator("#file-input").set_input_files(public_file)
            await expect(main.locator(".rerun-print-preview img")).to_have_attribute("src", user_print_image["data_url"], timeout=10000)
            assert not service.capture_events[run_id].is_set()
            assert forbidden_captures == []
            # The J remains a functioning toggle while waiting and polling.
            rail = floating.locator(".autojev-panel-rail")
            await rail.click()
            assert await rail.get_attribute("aria-expanded") == "false"
            await main.get_by_role("button", name="Remover imagem escolhida", exact=True).click()
            await expect(frame.locator("#selected-file")).to_be_hidden(timeout=10000)
            await rail.click()
            # Selecting in the main app shares back to the floating panel.
            await main.locator(".rerun-print-input").set_input_files(public_file)
            await expect(frame.locator("#preview")).to_have_attribute("src", user_print_image["data_url"], timeout=10000)
            await expect(frame.locator("#confirm-capture")).to_be_enabled()
            assert not service.capture_events[run_id].is_set()
            assert repo.get_exploration(run_id)["actions"] == []
            assert not any(event["type"] == "manual_print_attached" for event in repo.get_exploration(run_id)["events"])
            assert forbidden_captures == []
            await main.screenshot(path=str(tmp_path / "main-shared-preview.png"))
            await floating.screenshot(path=str(tmp_path / "floating-shared-preview.png"))
            stream.cancel()
            await asyncio.gather(stream, return_exceptions=True)
            stream = None
            await executor.close()
            executor = None
            await browser.close()
    finally:
        if stream is not None:
            stream.cancel()
            await asyncio.gather(stream, return_exceptions=True)
        if executor is not None:
            await executor.close()
        if keep_alive is not None:
            keep_alive.cancel()
            await asyncio.gather(keep_alive, return_exceptions=True)
        server.should_exit = True
        await asyncio.wait_for(server_task, 10)
        listener.close()
