"""Opt-in real React + isolated API regression for pre-print live progress.

Use AUTOJEV_FRONTEND_E2E=1 with the frontend running on localhost:3000.
Only temporary fixture data and disposable pages are used; no CT is executed.
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
from autojev.services.rerun_step_progress import build_step_plan, project_step_progress


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("AUTOJEV_FRONTEND_E2E") != "1", reason="Opt-in local frontend browser test")
async def test_both_live_maps_complete_only_the_recorded_before_step_while_waiting_for_print(tmp_path):
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
        fixture_url = "https://fixture.invalid/inventory"
        request = ExplorationRequest(fixture_url, ModelSelection("fixture", "fixture"),
                                     ModelSelection("fixture", "fixture"), ExplorationOptions(mode="single_ct_rerun"))
        source_id = repo.create_exploration(request)
        repo.update_exploration(source_id, status="completed")
        run_id = repo.create_exploration(request)
        repo.update_exploration(run_id, status="waiting_input")
        source_steps = [
            {"kind": "observation", "title": "Antes da ação", "screenshot": "old-before.png"},
            {"kind": "action", "title": "Selecionar opção", "success": True},
            {"kind": "observation", "title": "Depois da ação", "screenshot": "old-after.png"},
            {"kind": "assertion", "assertion": {"kind": "selection_matches", "expected": {"option_matches": True}, "passed": True}},
            {"kind": "assertion", "assertion": {"kind": "product_sort_order", "expected": {"by": "name"}, "passed": True}},
            {"kind": "assertion", "assertion": {"kind": "no_new_browser_errors", "expected": "No new errors", "passed": True}},
        ]
        checkpoint = {"id": "db-1", "version": 1, "gap": 1, "order": 0}
        plan = build_step_plan(source_steps, {"version": 1, "checkpoints": [checkpoint]})
        service.states[run_id] = {"running": True, "phase": "waiting_capture",
            "source_exploration_id": source_id, "source_scenario_id": "CT001",
            "capture_checkpoint": checkpoint, "capture_count": 1, "captures_completed": 0,
            "capture_action_index": 1, "step_plan": plan}
        service.capture_events[run_id] = asyncio.Event()
        keep_alive = asyncio.create_task(asyncio.Event().wait())
        service.tasks[run_id] = keep_alive
        forbidden_captures = []

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(viewport={"width": 1440, "height": 900})

            async def isolated_api(route):
                original = urlsplit(route.request.url)
                assert route.request.method in {"GET", "OPTIONS"}, "No execution, preview write or attachment authorized"
                response = await route.fetch(url=fixture_origin + original.path + ("?" + original.query if original.query else ""))
                await route.fulfill(response=response)

            await context.route("**/api/**", isolated_api)
            main = await context.new_page()
            await main.goto(f"http://localhost:3000/#/exploracoes/{run_id}/acompanhar")
            await main.get_by_role("heading", name="Adicione o print que você quiser", exact=True).wait_for()
            main_board = main.locator(".rerun-live-board")
            await expect(main_board.locator(".rerun-board-counter strong")).to_have_text("0 / 7")

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
            await expect(frame.locator("#completed")).to_have_text("0")
            await expect(frame.locator("#total")).to_have_text(" / 7")

            # Store only a real current-run observation. A waiting checkpoint
            # and all source success flags cannot complete any card by themselves.
            entry = {"id": "rerun-source-0-before", "source_step_id": "source-0",
                     "kind": "observation", "title": "Antes da ação", "occurred_at": "2026-10-18T12:00:00Z",
                     "url": fixture_url, "screenshot": None,
                     "message": "Estado inicial registrado antes de continuar o roteiro."}
            repo.add_event(run_id, "info", "rerun_before_observation", entry["message"],
                           {"scenario_id": "CT001", "entry": entry})
            current = repo.get_exploration(run_id)
            projected = project_step_progress(current, service.states[run_id], plan)
            assert projected["completed"] == 1 and projected["total"] == 7
            assert [row["state"] for row in projected["steps"]] == ["done", "waiting", "pending", "pending", "pending", "pending", "pending"]

            # Both actual UIs receive the projection through their normal live
            # refresh paths, rather than an injected hard-coded frontend payload.
            await expect(main_board.locator(".rerun-board-counter strong")).to_have_text("1 / 7", timeout=10000)
            main_before = main_board.locator('[data-step-id="source-0"]')
            main_wait = main_board.locator('[data-step-id="checkpoint-db-1"]')
            main_action = main_board.locator('[data-step-id="source-1"]')
            await expect(main_before).to_have_attribute("data-step-state", "done")
            await expect(main_before.locator(".rerun-board-state-symbol svg.lucide-check")).to_have_count(1)
            await expect(main_before.locator(".rerun-board-state-label")).to_have_text("Estado registrado")
            await expect(main_wait).to_have_attribute("data-step-state", "waiting")
            await expect(main_action).to_have_attribute("data-step-state", "pending")
            await expect(main_action.locator(".rerun-board-state-symbol svg.lucide-check")).to_have_count(0)
            await expect(main_board.locator('[data-step-state="done"].rerun-board-cell')).to_have_count(1)

            await expect(frame.locator("#completed")).to_have_text("1", timeout=10000)
            await expect(frame.locator("#total")).to_have_text(" / 7")
            frame_before = frame.locator('[data-step-id="source-0"]')
            frame_wait = frame.locator('[data-step-id="checkpoint-db-1"]')
            frame_action = frame.locator('[data-step-id="source-1"]')
            await expect(frame_before).to_have_attribute("data-state", "done")
            await expect(frame_before.locator(".step-icon")).to_have_text("✓")
            await expect(frame_before.locator(".step-label")).to_have_text("Estado registrado")
            await expect(frame_wait).to_have_attribute("data-state", "waiting")
            await expect(frame_action).to_have_attribute("data-state", "pending")
            await expect(frame.locator('.step[data-state="done"]')).to_have_count(1)
            # The completed card is actually green; the pending action is not
            # merely carrying a different hidden machine-readable status.
            assert await main_before.locator(".rerun-board-state-symbol").evaluate("el => getComputedStyle(el).color") == "rgb(123, 229, 173)"
            assert await frame_before.locator(".step-icon").evaluate("el => getComputedStyle(el).color") == "rgb(111, 213, 170)"
            assert await frame_before.evaluate("el => getComputedStyle(el).backgroundColor") != await frame_action.evaluate("el => getComputedStyle(el).backgroundColor")

            saved = repo.get_exploration(run_id)
            assert saved["actions"] == []
            assert len([event for event in saved["events"] if event["type"] == "rerun_before_observation"]) == 1
            assert not any(event["type"] == "manual_print_attached" for event in saved["events"])
            assert not service.capture_events[run_id].is_set()
            assert forbidden_captures == []
            await main.screenshot(path=str(tmp_path / "before-observation-main-1-of-7.png"))
            await floating.screenshot(path=str(tmp_path / "before-observation-floating-1-of-7.png"))
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
