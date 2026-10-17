"""Chromium regression for an individual replay behind a manual login gate.

This deliberately uses a loopback-only fixture and an isolated database. No
provider clients, real credentials, browser storage inspection or external
sites are involved. The page's login form is operated like a user's browser.
"""

import asyncio
import base64
import copy
import hashlib
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.adapters.browser_rerun_panel import PANEL_URL
from autojev.adapters.report import StandaloneHtmlReport
from autojev.adapters.repository import SQLiteRepository
from autojev.adapters.secrets import EphemeralCredentialVault, SecretCipher
from autojev.config import Settings
from autojev.domain.models import (
    AgentDecision, ExplorationOptions, ExplorationRequest, ModelSelection, utc_now,
)
from autojev.services.business_checks import check_business_outcomes
from autojev.services.exploration import ExplorationManager
from autojev.services.rerun_recipe import build_rerun_recipe, recipe_effect_digest
from autojev.services.scenario_rerun import ScenarioRerunService


LOGIN_HTML = """<!doctype html><html lang="pt-BR"><head><title>Entrar</title></head>
<body><main><h1>Entrar no ambiente de teste</h1>
<form method="post" action="/login">
  <label>Usuário <input name="username" autocomplete="username"></label>
  <label>Senha <input name="password" type="password" autocomplete="current-password"></label>
  <button type="submit">Entrar</button>
</form></main></body></html>"""

INVENTORY_HTML = """<!doctype html><html lang="pt-BR"><head><title>Produtos</title></head>
<body><main><h1>Produtos</h1>
<label for="sort">Ordenar produtos</label>
<select id="sort" name="sort" data-testid="sort-products">
  <option value="az">Nome de A a Z</option>
  <option value="za">Nome de Z a A</option>
</select>
<p id="selected-option">Nome de A a Z</p>
<p id="change-count">Alterações: 0</p>
</main><script>
let changes = 0;
document.getElementById('sort').addEventListener('change', event => {
  document.getElementById('selected-option').textContent = event.target.selectedOptions[0].label;
  document.getElementById('change-count').textContent = 'Alterações: ' + (++changes);
});
</script></body></html>"""


@pytest.fixture
def protected_inventory():
    login_submissions = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def _html(self, html):
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _redirect(self, destination, *, signed_in=False):
            self.send_response(303)
            self.send_header("Location", destination)
            if signed_in:
                self.send_header("Set-Cookie", "fixture_session=public-test-session; Path=/; HttpOnly; SameSite=Lax")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/login":
                self._html(LOGIN_HTML)
            elif path == "/inventory.html":
                if "fixture_session=public-test-session" in self.headers.get("Cookie", ""):
                    self._html(INVENTORY_HTML)
                else:
                    self._redirect("/login")
            else:
                self.send_response(204)
                self.end_headers()

        def do_POST(self):
            # These constants belong solely to this public test fixture. The
            # service receives no credentials. An explicit Continue may submit
            # the fields already filled in the browser, without forwarding them.
            login_submissions.append(self.path)
            fields = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            if self.path == "/login" and fields == {"username": ["fixture-user"], "password": ["fixture-password"]}:
                self._redirect("/inventory.html", signed_in=True)
            else:
                self._redirect("/login")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        class FixtureInventoryUrl(str):
            pass

        fixture_url = FixtureInventoryUrl(f"http://127.0.0.1:{server.server_port}/inventory.html")
        fixture_url.login_submissions = login_submissions
        yield fixture_url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


async def login_through_fixture_ui(executor, inventory_url):
    page = executor._page
    await page.get_by_label("Usuário").fill("fixture-user")
    await page.get_by_label("Senha").fill("fixture-password")
    await page.get_by_role("button", name="Entrar", exact=True).click()
    await page.wait_for_url(inventory_url)
    await page.get_by_label("Ordenar produtos").wait_for()


async def wait_preparation(service, exploration_id, version):
    async with asyncio.timeout(20):
        while True:
            state = service.states[exploration_id]
            if state.get("phase") == "waiting_preparation" and state.get("preparation", {}).get("version") == version:
                return copy.deepcopy(state)
            if state.get("phase") == "finished":
                raise AssertionError(f"Preparation finished prematurely: {state}")
            await asyncio.sleep(0.01)


async def wait_capture(service, exploration_id):
    async with asyncio.timeout(20):
        while True:
            state = service.state_for(exploration_id)
            if state.get("phase") == "waiting_capture":
                return copy.deepcopy(state["capture_checkpoint"])
            if state.get("phase") == "finished":
                raise AssertionError(f"Capture finished prematurely: {state}")
            await asyncio.sleep(0.01)


async def wait_panel_frame(browser, section="capture", preparation_version=None):
    """The live projection restores a new isolated document after navigation."""
    async with asyncio.timeout(10):
        while True:
            panel = browser._rerun_panel
            frame = panel.frame if panel else None
            if preparation_version is not None and (
                not panel or panel._payload.get("phase") != "waiting_preparation"
                or (panel._payload.get("preparation") or {}).get("version") != preparation_version
            ):
                await asyncio.sleep(0.05)
                continue
            if frame is not None and not frame.is_detached() and frame.url == PANEL_URL:
                try:
                    if (await frame.locator(f"#{section}").is_visible()
                            and (section != "preparation" or "Conferindo" not in await frame.locator("#confirm-preparation").inner_text())):
                        return frame
                except Exception:
                    pass
            await asyncio.sleep(0.05)


@pytest.mark.asyncio
@pytest.mark.parametrize("print_gap", [None, 0, 3])
@pytest.mark.parametrize("login_via_continue", [False, True], ids=["site-login", "continue-login"])
async def test_login_redirect_and_premature_resume_keep_browser_open_until_explicit_retry(
    tmp_path, monkeypatch, protected_inventory, user_print_image, print_gap, login_via_continue,
):
    settings = Settings(
        data_dir=tmp_path, database_path=tmp_path / "db.sqlite3",
        artifact_dir=tmp_path / "artifacts", secret_key="test",
        cors_origins=(), headless=True, browser="chromium", http_timeout_seconds=1,
    )
    repository = SQLiteRepository(settings.database_path, SecretCipher(tmp_path, "test"))
    repository.initialize()
    renderer = StandaloneHtmlReport(settings.artifact_dir)
    options = ExplorationOptions(auxiliary_evidence_mode="text", max_steps=1)
    source_id = repository.create_exploration(ExplorationRequest(
        protected_inventory, ModelSelection("unused", "unused-agent"),
        ModelSelection("unused", "unused-auxiliary"), options,
    ))
    source_browser = PlaywrightExecutor(
        settings.artifact_dir / source_id / "screenshots", options, headless=True,
    )
    try:
        redirected = await source_browser.start(protected_inventory)
        assert redirected.url.endswith("/login")
        await login_through_fixture_ui(source_browser, protected_inventory)
        before = await source_browser.observe("source_before")
        target = next(element for element in before.interactive_elements if element.get("tag") == "select")
        decision = AgentDecision("select", "Selecionar Nome de Z a A", target=target["ref"], value="za")
        recipe = build_rerun_recipe(before, decision, protected_inventory)
        assert recipe.get("enabled") is True, recipe
        result, after = await source_browser.execute(decision, {})
        result.assertions.extend(check_business_outcomes(before, decision, result, after))
        ExplorationManager._append_observable_state_evidence(
            result, decision, after,
            state_changed=recipe_effect_digest(before) != recipe_effect_digest(after),
            capture_console_errors=True, capture_network_errors=True, new_issues=[],
        )
        result.evidence.update(
            action_step=1, before_screenshot=before.screenshot,
            before_observed_at=before.captured_at,
        )
        scenario = ExplorationManager._scenario_from_evidence(after, decision, result, 1)
        assert str(scenario.status) == "PASS"
        assert [assertion.kind for assertion in result.assertions] == ["selection_matches", "no_new_browser_errors"]
        # An optional auxiliary review is metadata, not an additional original
        # assertion. The local replay must neither borrow it nor invent a
        # missing visual_model check when all saved local checks are repeated.
        scenario.evidence["visual_assessment"] = {
            "requested": True, "status": "assessed", "summary": "Original-only auxiliary review",
        }
        saved_result = result.to_dict()
        saved_result["evidence"]["rerun_recipe"] = recipe
        repository.add_action(source_id, 1, asdict(decision), saved_result)
        repository.add_scenario(source_id, 1, scenario)
        repository.update_exploration(
            source_id, status="completed", started_at=result.started_at,
            finished_at=utc_now(), steps_completed=1, current_url=after.url,
        )
        source_report = settings.artifact_dir / source_id / "report.html"
        renderer.generate(repository.get_exploration(source_id), str(source_report))
        repository.update_exploration(source_id, report_path=str(source_report))
    finally:
        await source_browser.close()

    original = repository.get_exploration(source_id)
    original_report_digest = hashlib.sha256(source_report.read_bytes()).hexdigest()
    assert protected_inventory.login_submissions == ["/login"]
    browsers = []

    class HeadlessFixtureExecutor(PlaywrightExecutor):
        def __init__(self, *args, **kwargs):
            # Production remains headed/manual. CI drives the same real browser
            # implementation headlessly, only for this local regression.
            kwargs["headless"] = True
            super().__init__(*args, **kwargs)
            self.dispatched = []
            self.fixture_changes = None
            self.was_closed = False
            self.confirmed_login_calls = 0
            browsers.append(self)

        async def start(self, url):
            observation = await super().start(url)
            if print_gap is not None or login_via_continue:
                # CI launches without a desktop, then enables the exact headed
                # intervention-panel path. This leaves the real Chromium
                # process headless while exercising the isolated browser UI.
                # Capture authority remains in the host, outside the tested DOM.
                self.headless = False
            return observation

        async def attempt_filled_login(self, observation=None):
            self.confirmed_login_calls += 1
            return await super().attempt_filled_login(observation)

        async def execute(self, replay_decision, credentials):
            assert credentials == {}, "The CT action must not receive or forward login credentials"
            self.dispatched.append(copy.deepcopy(replay_decision))
            replay_result, replay_after = await super().execute(replay_decision, credentials)
            self.fixture_changes = await self._page.locator("#change-count").inner_text()
            return replay_result, replay_after

        async def capture_print(self, _label):
            raise AssertionError("Attaching an arbitrary image must not capture the tested browser")

        async def close(self):
            await super().close()
            self.was_closed = True

    def no_models(*_args, **_kwargs):
        raise AssertionError("A single-CT rerun must never invoke models")

    monkeypatch.setattr("autojev.services.scenario_rerun.PlaywrightExecutor", HeadlessFixtureExecutor)
    manager = SimpleNamespace(
        repository=repository, credential_vault=EphemeralCredentialVault(),
        report_renderer=renderer, semaphore=asyncio.Semaphore(1), _client_for=no_models,
    )
    service = ScenarioRerunService(manager, settings)
    service.intervention_timeout_seconds = 40
    try:
        capture_plan = None if print_gap is None else {
            "version": 1, "checkpoints": [{"id": "db-1", "gap": print_gap, "order": 0}],
        }
        new_id = service.submit(source_id, "CT001", capture_plan=capture_plan)
        task = service.tasks[new_id]
        first = await wait_preparation(service, new_id, 1)
        browser = browsers[0]
        assert first["preparation"]["expected_url"] == protected_inventory
        assert first["preparation"]["current_url"].endswith("/login")
        assert browser._page.url.endswith("/login")
        assert browser.confirmed_login_calls == 0
        assert protected_inventory.login_submissions == ["/login"]

        # An empty login cannot submit. Confirmation must instead show an
        # actionable preparation warning, without terminating the attempt.
        service.resume(new_id)
        second = await wait_preparation(service, new_id, 2)
        assert second["preparation"]["attempts"] == 1
        assert second["preparation"]["reason_code"] == "before_url_changed"
        assert second["preparation"]["reason_label"]
        assert second["preparation"]["login_attempt"]["status"] == "manual_required"
        assert second["preparation"]["login_attempt"]["reason"] == "incomplete_credentials"
        assert browser.confirmed_login_calls == 1
        assert protected_inventory.login_submissions == ["/login"]
        waiting = repository.get_exploration(new_id)
        assert waiting["status"] == "waiting_input"
        assert waiting["steps_completed"] == 0
        assert waiting["actions"] == waiting["scenarios"] == []
        assert waiting["report_available"] is False
        assert not (settings.artifact_dir / new_id / "report.html").exists()
        assert browser.dispatched == [] and not browser.was_closed
        assert not browser._page.is_closed() and not task.done()

        if login_via_continue:
            # Reproduce the original yellow-button workflow: only fill the
            # fields; never click the fixture site's Entrar button.
            await browser._page.get_by_label("Usuário").fill("fixture-user")
            await browser._page.get_by_label("Senha").fill("fixture-password")
            assert browser._page.url.endswith("/login")
            assert protected_inventory.login_submissions == ["/login"]
        else:
            await login_through_fixture_ui(browser, protected_inventory)
        # Filling or manually navigating is preparation, never authorization to
        # dispatch. Yield so any automatic submission/bypass is observable.
        await asyncio.sleep(0.1)
        assert browser.confirmed_login_calls == 1
        assert len(protected_inventory.login_submissions) == (1 if login_via_continue else 2)
        assert browser.dispatched == [] and not task.done()
        assert service.states[new_id]["phase"] == "waiting_preparation"
        assert repository.get_exploration(new_id)["steps_completed"] == 0

        if login_via_continue:
            preparation_frame = await wait_panel_frame(browser, "preparation", preparation_version=2)
            continue_button = preparation_frame.locator("#confirm-preparation")
            assert await continue_button.is_disabled()
            await preparation_frame.locator("#preparation-consent").check()
            await continue_button.dispatch_event("click")
            await asyncio.sleep(0.05)
            assert service.states[new_id]["phase"] == "waiting_preparation"
            assert browser.confirmed_login_calls == 1
            await continue_button.click()
        else:
            service.resume(new_id)
        if print_gap is not None:
            checkpoint = await wait_capture(service, new_id)
            assert checkpoint["id"] == "db-1" and checkpoint["gap"] == print_gap
            expected_actions = 0 if print_gap == 0 else 1
            assert len(browser.dispatched) == expected_actions
            assert repository.get_exploration(new_id)["status"] == "waiting_input"
            assert not browser.was_closed and not task.done()
            capture_frame = await wait_panel_frame(browser)
            capture_button = capture_frame.locator("#confirm-capture")
            assert await capture_button.is_disabled()
            assert await capture_frame.locator("#steps .step").count() >= 4
            # The tested page cannot inspect the trusted frame, even though it
            # can see the non-secret iframe element and the binding's name.
            assert await browser._page.evaluate("document.querySelector('#autojev-rerun-frame').contentDocument === null")
            assert await browser._page.evaluate("typeof window.__autojev_capture_print") == "undefined"
            # Both a normal Continue request and elapsed time leave this real
            # Chromium session waiting. The tested page cannot authorize its
            # own attachment through a DOM event or an exposed host binding.
            service.resume(new_id)
            parent_rejected = await browser._page.evaluate("""async () => {
              try {
                await window.__autojev_rerun_command({op:'capture',nonce:'not-a-capability',confirm:true,
                  checkpoint_id:'db-1',checkpoint_version:1,image_data_url:'invalid'});
                return false;
              } catch (_) { return true; }
            }""")
            assert parent_rejected is True
            await browser._page.evaluate("window.__autojev_resume?.()")
            await asyncio.sleep(0.2)
            assert service.state_for(new_id)["phase"] == "waiting_capture"
            assert service.state_for(new_id)["captures_completed"] == 0
            assert len(browser.dispatched) == expected_actions and not task.done()
            # Staging an arbitrary file is not authorization to advance. A
            # synthetic DOM click inside the trusted document also cannot send.
            await capture_frame.locator("#file-input").set_input_files({
                "name": "evidence-from-another-app.png", "mimeType": "image/png",
                "buffer": user_print_image["bytes"],
            })
            await capture_frame.wait_for_function("document.querySelector('#confirm-capture').disabled === false")
            assert "Falta confirmar" in await capture_frame.locator("#capture-status").inner_text()
            await capture_button.dispatch_event("click")
            await asyncio.sleep(0.1)
            assert service.state_for(new_id)["phase"] == "waiting_capture"
            assert not task.done() and len(browser.dispatched) == expected_actions
            # A full navigation revokes the old frame and restores a fresh UI
            # without silently uploading the unconfirmed, ephemeral preview.
            await browser._page.reload()
            replacement_frame = await wait_panel_frame(browser)
            assert replacement_frame is not capture_frame
            assert await replacement_frame.locator("#confirm-capture").is_disabled()
            assert await replacement_frame.locator("#selected-file").is_hidden()
            assert await browser._page.evaluate("typeof window.__autojev_capture_print") == "undefined"
            await browser._page.evaluate("window.__autojev_resume?.()")
            await asyncio.sleep(0.1)
            assert service.state_for(new_id)["phase"] == "waiting_capture"
            assert not task.done() and len(browser.dispatched) == expected_actions
            # Invalid payloads leave the planned point pending. Uploading via
            # the trusted iframe uses the same service acceptance gate as the
            # origin-checked AutoJev app, never a browser screenshot operation.
            with pytest.raises(ValueError):
                service.capture_print(new_id, checkpoint["id"], checkpoint["version"], "")
            await asyncio.sleep(0.1)
            assert service.state_for(new_id)["phase"] == "waiting_capture"
            assert not task.done() and len(browser.dispatched) == expected_actions
            await replacement_frame.locator("#file-input").set_input_files({
                "name": "evidence-from-another-app.png", "mimeType": "image/png",
                "buffer": user_print_image["bytes"],
            })
            await replacement_frame.wait_for_function("document.querySelector('#confirm-capture').disabled === false")
            assert service.state_for(new_id)["captures_completed"] == 0
            await replacement_frame.locator("#confirm-capture").click()
        await asyncio.wait_for(task, timeout=30)
        replay = repository.get_exploration(new_id)
        assert replay["status"] == "completed"
        assert replay["steps_completed"] == 1
        assert len(replay["actions"]) == len(replay["scenarios"]) == len(browser.dispatched) == 1
        assert browser.fixture_changes == "Alterações: 1"
        assert protected_inventory.login_submissions == ["/login", "/login"]
        assert browser.confirmed_login_calls == (2 if login_via_continue else 1)
        confirmed_logins = [event for event in replay["events"] if event["type"] == "confirmed_login_result"]
        assert sum(event["payload"]["status"] == "success" for event in confirmed_logins) == int(login_via_continue)
        assert browser.was_closed
        assert replay["scenarios"][0]["status"] == "PASS"
        replay_assertions = replay["scenarios"][0]["evidence"]["assertions"]
        original_assertions = original["scenarios"][0]["evidence"]["assertions"]
        assert [(item["kind"], item["expected"]) for item in replay_assertions] == [
            (item["kind"], item["expected"]) for item in original_assertions
        ]
        assert all(item["passed"] is True for item in replay_assertions)
        assert replay["scenarios"][0]["evidence"]["missing_original_checks"] == []
        assert replay["scenarios"][0]["evidence"]["visual_assessment"] == {
            "requested": False, "status": "disabled", "reason": "single_ct_rerun_local_only",
        }
        assert not replay.get("error")
        replay_scope = replay["scenarios"][0]["evidence"]["rerun_scope"]
        assert replay_scope["missing_visual_checks"] is False
        assert replay_scope["visual_review_omitted"] is True and replay_scope["visual_review"] is False
        assert replay["usage"]["total_tokens"] == 0
        assert replay["report_available"] is True
        assert (settings.artifact_dir / new_id / "report.html").is_file()
        if print_gap is not None:
            manual = replay["scenarios"][0]["evidence"]["manual_captures"]
            assert len(manual) == 1
            captured = manual[0]
            assert captured["checkpoint_id"] == "db-1" and captured["planned_gap"] == print_gap
            assert captured["kind"] == "observation" and "passed" not in captured
            assert captured["source"] == captured["image_source"] == "user_upload"
            assert "url" not in captured and "page_url" not in captured
            assert captured["attachment"]["sha256"] == user_print_image["sha256"]
            assert (captured["attachment"]["width"], captured["attachment"]["height"]) == (240, 120)
            screenshot = next(shot for shot in replay["screenshots"] if shot["path"] == captured["screenshot"])
            assert screenshot["scenario_id"] == "CT001"
            path = Path(captured["screenshot"])
            if not path.is_absolute():
                path = settings.artifact_dir / new_id / "screenshots" / path
            assert path.is_file() and path.read_bytes() == user_print_image["bytes"]
            html = (settings.artifact_dir / new_id / "report.html").read_text()
            assert "Imagem anexada pelo usuário" in html
            assert user_print_image["data_url"] in html
            assert base64.b64encode(user_print_image["bytes"]).decode("ascii") in html
            assert 'data-step-outcome="Imagem anexada"' in html
            assert 'alt="Imagem anexada pelo usuário"' in html
            assert "não aprova nem reprova" in html
            assert service.state_for(new_id)["captures_completed"] == 1
        assert repository.get_exploration(source_id) == original
        assert hashlib.sha256(source_report.read_bytes()).hexdigest() == original_report_digest
    finally:
        await service.shutdown()
