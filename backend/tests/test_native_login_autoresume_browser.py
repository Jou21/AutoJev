"""Real native-site login + passive watcher + trusted floating map integration.

All requests are intercepted public fixtures; the user's CTs are never run.
"""
import asyncio
from copy import deepcopy
from dataclasses import asdict

import pytest
from playwright.async_api import async_playwright, expect

import autojev.services.scenario_rerun as rerun_module
from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.domain.models import AgentDecision, ExplorationOptions, ExplorationRequest, ModelSelection, utc_now
from autojev.services.business_checks import check_business_outcomes
from autojev.services.exploration import ExplorationManager
from autojev.services.rerun_recipe import build_rerun_recipe
from test_scenario_rerun import action_result, capture_plan, make_service


ORIGIN = "https://example.test"
TARGET = ORIGIN + "/inventory.html"
LOGIN = """<!doctype html><title>Login</title><style>main{width:300px;margin:50px}</style><main>
<form action="/inventory.html" onsubmit="event.preventDefault(); location.href='/inventory.html'">
<label>Username <input name="user" autocomplete="username"></label>
<label>Password <input type="password" name="password" autocomplete="current-password"></label>
<button type="submit">Login</button></form></main>"""
INVENTORY = """<!doctype html><title>Products</title><style>main{width:300px;margin:50px}</style>
<main><h1>Products</h1><label>Sort products <select id="sort" aria-label="Sort products">
<option value="az">Name (A to Z)</option><option value="za">Name (Z to A)</option>
</select></label></main>"""


async def wait_until(predicate, timeout=8):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.025)


@pytest.mark.asyncio
@pytest.mark.parametrize("authorize_before_login", [True, False])
async def test_native_login_resumes_only_with_consent_then_still_waits_for_print(
    tmp_path, monkeypatch, user_print_image, authorize_before_login,
):
    service, repo, _, _, _ = make_service(tmp_path)
    source_id = repo.create_exploration(ExplorationRequest(
        TARGET, ModelSelection("fixture", "fixture"), ModelSelection("fixture", "fixture"), ExplorationOptions()))
    repo.update_exploration(source_id, status="completed")
    service.manual_login_poll_seconds = 0.05
    service.intervention_timeout_seconds = 15
    instances = []
    task = None
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        async def fixture_route(route):
            if route.request.url == "https://autojev-panel.invalid/rerun.html" and instances:
                await instances[0]._guard_navigation(route, route.request)
                return
            assert route.request.url.startswith(ORIGIN + "/"), "No external page access"
            await route.fulfill(status=200, content_type="text/html",
                                body=INVENTORY if "/inventory.html" in route.request.url else LOGIN)

        await context.route("**/*", fixture_route)
        await page.goto(TARGET)
        probe = PlaywrightExecutor(tmp_path / "source", ExplorationOptions(capture_screenshots=False))
        probe._page, probe._browser = page, browser
        before = await probe.observe_preparation()
        before.screenshot = "fixture-before.png"
        target = next(control for control in before.interactive_elements if control.get("tag") == "select")
        decision = AgentDecision("select", "Change sorting", target=target["ref"], value="za")
        recipe = build_rerun_recipe(before, decision, TARGET)
        assert recipe["enabled"]
        await page.locator("#sort").select_option("za")
        after = await probe.observe_preparation()
        after.screenshot = "fixture-after.png"
        result = action_result(before, after, decision)
        result.started_at = result.finished_at = utc_now()
        result.assertions.extend(check_business_outcomes(before, decision, result, after))
        ExplorationManager._append_observable_state_evidence(result, decision, after, state_changed=True,
                                                             capture_console_errors=True, capture_network_errors=True)
        # Keep this fixture scoped to portable local selection/error checks.
        # A synthetic source fallback observable_state_change isn't an actual
        # saved executor assertion and is outside this auth regression.
        result.assertions = [check for check in result.assertions if check.kind != "observable_state_change"]
        result.evidence.update(action_step=1, before_screenshot=before.screenshot, before_observed_at=before.captured_at,
                               rerun_recipe=recipe)
        scenario = ExplorationManager._scenario_from_evidence(after, decision, result, 1)
        repo.add_action(source_id, 1, asdict(decision), result.to_dict())
        repo.add_scenario(source_id, 1, scenario)
        original = deepcopy(repo.get_exploration(source_id))

        class FixtureExecutor(PlaywrightExecutor):
            def __init__(self, artifact_dir, options, **kwargs):
                super().__init__(artifact_dir, options, **kwargs)
                self._page, self._browser, self._context = page, browser, context
                self.actions = []
                self.login_submissions = 0
                instances.append(self)

            async def start(self, url):
                assert url == TARGET
                self._allowed_origin = self._origin(TARGET)
                await self._prepare_page(page)
                await page.goto(ORIGIN + "/")
                return await self.observe("initial")

            async def attempt_filled_login(self, observation=None):
                self.login_submissions += 1
                raise AssertionError("A native site login must never cause AutoJev to submit again")

            async def execute(self, decision, credentials):
                self.actions.append(deepcopy(decision))
                return await super().execute(decision, credentials)

        monkeypatch.setattr(rerun_module, "PlaywrightExecutor", FixtureExecutor)
        run_id = service.submit(source_id, "CT001", capture_plan=capture_plan(("db-1", 1, 0)))
        task = service.tasks[run_id]
        try:
            await wait_until(lambda: service.states[run_id].get("phase") == "waiting_preparation")
            executor = instances[0]
            def live_frame():
                candidate = executor._rerun_panel.frame
                return candidate is not None and not candidate.is_detached() and candidate.url == "https://autojev-panel.invalid/rerun.html"

            await wait_until(live_frame)
            frame = executor._rerun_panel.frame
            await frame.locator("#preparation").wait_for()
            version = service.states[run_id]["preparation"]["version"]
            if authorize_before_login:
                # A real checkbox click from the registered isolated frame.
                await frame.locator("#preparation-consent").check()
                await expect(frame.locator("#confirm-preparation")).to_be_enabled()
                await asyncio.sleep(0.12)
                assert service.preparation_consent_for(run_id, version)["checked"]
                assert executor.actions == [] and executor.login_submissions == 0
            await page.get_by_label("Username").fill("public-fixture-user")
            await page.get_by_label("Password").fill("public-fixture-password")
            await page.get_by_role("button", name="Login", exact=True).click()
            await page.wait_for_url(TARGET)
            if not authorize_before_login:
                await wait_until(lambda: (service.states[run_id].get("preparation", {}).get("manual_login") or {}).get("status") == "ready")
                assert service.states[run_id]["phase"] == "waiting_preparation"
                assert service.state_for(run_id)["preparation"]["presentation"]["title"] == "Login realizado. Autorize para continuar"
                await wait_until(live_frame)
                frame = executor._rerun_panel.frame
                await expect(frame.locator("#preparation-heading")).to_have_text("Login realizado. Autorize para continuar", timeout=5000)
                await frame.locator("#preparation-consent").check()

            # No Continue click. The backend detects the user's login, checks
            # original guards, completes the before card, then awaits the image.
            await wait_until(lambda: service.states[run_id].get("phase") == "waiting_capture")
            await wait_until(live_frame)
            frame = executor._rerun_panel.frame
            await expect(frame.locator("#capture")).to_be_visible(timeout=5000)
            await expect(frame.locator("#completed")).to_have_text("1")
            await expect(frame.locator('[data-step-id="source-0"]')).to_have_attribute("data-state", "done")
            assert executor.actions == [] and executor.login_submissions == 0
            assert not service.capture_events[run_id].is_set()
            checkpoint = service.states[run_id]["capture_checkpoint"]
            service.capture_print(run_id, checkpoint["id"], checkpoint["version"], user_print_image["data_url"])
            await asyncio.wait_for(task, 15)
            saved = repo.get_exploration(run_id)
            assert len(executor.actions) == len(saved["actions"]) == len(saved["scenarios"]) == 1
            assert saved["status"] == "completed"
            assert executor.login_submissions == 0
            assert not any(event["type"] == "confirmed_login_started" for event in saved["events"])
            assert len(saved["scenarios"][0]["evidence"]["manual_captures"]) == 1
            assert repo.get_exploration(source_id) == original
        finally:
            if task is not None and not task.done():
                service.cancel(run_id)
                await asyncio.gather(task, return_exceptions=True)
            await context.close()
            await browser.close()
