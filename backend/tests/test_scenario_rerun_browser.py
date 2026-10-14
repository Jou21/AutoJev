"""Chromium regression for an individual replay behind a manual login gate.

This deliberately uses a loopback-only fixture and an isolated database. No
provider clients, real credentials, browser storage inspection or external
sites are involved. The page's login form is operated like a user's browser.
"""

import asyncio
import copy
import hashlib
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from autojev.adapters.playwright_executor import PlaywrightExecutor
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
            # service itself receives no credentials and performs no login.
            fields = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            if self.path == "/login" and fields == {"username": ["fixture-user"], "password": ["fixture-password"]}:
                self._redirect("/inventory.html", signed_in=True)
            else:
                self._redirect("/login")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/inventory.html"
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


@pytest.mark.asyncio
async def test_login_redirect_and_premature_resume_keep_browser_open_until_explicit_retry(
    tmp_path, monkeypatch, protected_inventory,
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
            browsers.append(self)

        async def execute(self, replay_decision, credentials):
            assert credentials == {}, "The replay service must not log in or forward credentials"
            self.dispatched.append(copy.deepcopy(replay_decision))
            replay_result, replay_after = await super().execute(replay_decision, credentials)
            self.fixture_changes = await self._page.locator("#change-count").inner_text()
            return replay_result, replay_after

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
        new_id = service.submit(source_id, "CT001")
        task = service.tasks[new_id]
        first = await wait_preparation(service, new_id, 1)
        browser = browsers[0]
        assert first["preparation"]["expected_url"] == protected_inventory
        assert first["preparation"]["current_url"].endswith("/login")
        assert browser._page.url.endswith("/login")

        # Reproduce the reported mistake: confirmation before logging in must
        # show an actionable preparation warning, not terminate the attempt.
        service.resume(new_id)
        second = await wait_preparation(service, new_id, 2)
        assert second["preparation"]["attempts"] == 1
        assert second["preparation"]["reason_code"] == "before_url_changed"
        assert second["preparation"]["reason_label"]
        waiting = repository.get_exploration(new_id)
        assert waiting["status"] == "waiting_input"
        assert waiting["steps_completed"] == 0
        assert waiting["actions"] == waiting["scenarios"] == []
        assert waiting["report_available"] is False
        assert not (settings.artifact_dir / new_id / "report.html").exists()
        assert browser.dispatched == [] and not browser.was_closed
        assert not browser._page.is_closed() and not task.done()

        await login_through_fixture_ui(browser, protected_inventory)
        # Navigating to the correct page is preparation, never authorization to
        # dispatch. Yield to the service so any automatic bypass is observable.
        await asyncio.sleep(0.1)
        assert browser.dispatched == [] and not task.done()
        assert service.states[new_id]["phase"] == "waiting_preparation"
        assert repository.get_exploration(new_id)["steps_completed"] == 0

        service.resume(new_id)
        await asyncio.wait_for(task, timeout=30)
        replay = repository.get_exploration(new_id)
        assert replay["status"] == "completed"
        assert replay["steps_completed"] == 1
        assert len(replay["actions"]) == len(replay["scenarios"]) == len(browser.dispatched) == 1
        assert browser.fixture_changes == "Alterações: 1"
        assert browser.was_closed
        assert replay["scenarios"][0]["status"] == "PASS"
        replay_assertions = replay["scenarios"][0]["evidence"]["assertions"]
        original_assertions = original["scenarios"][0]["evidence"]["assertions"]
        assert [(item["kind"], item["expected"]) for item in replay_assertions] == [
            (item["kind"], item["expected"]) for item in original_assertions
        ]
        assert all(item["passed"] is True for item in replay_assertions)
        assert replay["scenarios"][0]["evidence"]["missing_original_checks"] == []
        assert replay["usage"]["total_tokens"] == 0
        assert replay["report_available"] is True
        assert (settings.artifact_dir / new_id / "report.html").is_file()
        assert repository.get_exploration(source_id) == original
        assert hashlib.sha256(source_report.read_bytes()).hexdigest() == original_report_digest
    finally:
        await service.shutdown()
