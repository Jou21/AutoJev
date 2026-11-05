"""SSO (OIDC redirect) and proxy-downgrade regressions in real Chromium.

Fixture-only traffic: ``app.fixture.test`` and ``idp.fixture.test`` are served
from memory *behind* the executor's own navigation guard.
"""

import http.server
import json
import threading
from dataclasses import asdict
from urllib.parse import quote

import pytest

from autojev.adapters.playwright_executor import EntryNavigationError, PlaywrightExecutor
from autojev.domain.models import ExplorationOptions

USER = "fixture-sso-user"
PASSWORD = "fixture-sso-password"
APP = "https://app.fixture.test/gcar/"


def sso_url(redirect=APP):
    return (
        "https://idp.fixture.test/auth?client_id=gcar&response_type=code"
        f"&redirect_uri={quote(redirect, safe='')}&state=s"
    )


def app_page(redirect=APP):
    return (
        '<!doctype html><meta charset="utf-8"><title>Shell</title>'
        f"<script>setTimeout(()=>location.href={json.dumps(sso_url(redirect))},300)</script>"
    )


IDP_PAGE = """<!doctype html><meta charset="utf-8"><title>Login sisbr</title>
<form method="post" action="/login">
<label>Login <input name="username" placeholder="Informe seu login"></label>
<label>Senha <input name="password" type="password" placeholder="Informe sua senha"></label>
<input type="submit" value="Logar"><button type="button">Alterar senha</button></form>"""

AUTHENTICATED = (
    '<!doctype html><meta charset="utf-8"><title>App</title>'
    '<h1>Authenticated application</h1><a href="/gcar/painel">Painel</a>'
)


def executor_type(*, redirect=APP):
    class SsoExecutor(PlaywrightExecutor):
        posted: list[str] = []

        async def _guard_navigation(self, route, request):
            class Fixture:
                def __init__(self, inner):
                    self.inner = inner

                def __getattr__(self, name):
                    return getattr(self.inner, name)

                async def continue_(self):
                    url, inner = request.url, self.inner
                    if url.startswith("https://app.fixture.test/gcar") and "code=" in url:
                        await inner.fulfill(content_type="text/html; charset=utf-8", body=AUTHENTICATED)
                    elif url.startswith("https://app.fixture.test/"):
                        await inner.fulfill(content_type="text/html; charset=utf-8", body=app_page(redirect))
                    elif url == "https://idp.fixture.test/login":
                        SsoExecutor.posted.append(request.post_data or "")
                        # A fabricated 302 is not re-routed by Playwright; a JS hop is.
                        await inner.fulfill(
                            content_type="text/html",
                            body=f'<!doctype html><script>location.replace("{APP}?code=abc")</script>',
                        )
                    elif url.startswith("https://idp.fixture.test/"):
                        await inner.fulfill(content_type="text/html; charset=utf-8", body=IDP_PAGE)
                    else:
                        await inner.abort()

            await super()._guard_navigation(Fixture(route), request)

    SsoExecutor.posted = []
    return SsoExecutor


async def test_sso_login_with_saved_credentials_returns_to_the_app(tmp_path):
    executor = executor_type()(tmp_path, ExplorationOptions(), headless=True)
    try:
        observation = await executor.start("https://app.fixture.test/gcar/")
        assert executor._auth_origins == {("https", "idp.fixture.test", 443)}
        assert observation.page_state["authentication_challenge"] is True
        assert await executor.is_login_page()

        result = await executor.attempt_login({"username": USER, "password": PASSWORD}, observation)

        assert result.status == "success", result.reason
        assert executor._origin(executor._page.url) == executor._allowed_origin
        assert not await executor.is_login_page()
        assert "Authenticated application" in result.observation.text
        assert USER not in json.dumps(asdict(result.observation))
        assert len(executor.posted) == 1 and USER in executor.posted[0]
        # Once authenticated the allowance is closed for good.
        executor.protect_authenticated_history()
        assert executor._register_auth_origin(sso_url()) is False
    finally:
        await executor.close()


@pytest.fixture
def http_origin():
    """A real plain-http server: Chromium follows a 301 to it unrouted."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<!doctype html><title>down</title>")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()


def downgrading_executor(tmp_path, http_port, *, persistent):
    """``https://127.0.0.1:8443/gcar`` is answered with a 301 to http, as the proxy does."""

    class Downgrading(PlaywrightExecutor):
        async def _guard_navigation(self, route, request):
            slashed = request.url.endswith("/gcar/")
            if request.url.endswith("/gcar") or (persistent and slashed):
                await route.fulfill(status=301, headers={"location": f"http://127.0.0.1:{http_port}/gcar/"})
            elif slashed:
                await route.fulfill(content_type="text/html", body="<!doctype html><title>ok</title><a href='/x'>x</a>")
            else:
                await route.abort()

    return Downgrading(tmp_path, ExplorationOptions(), headless=True)


async def test_proxy_downgrade_is_retried_with_the_trailing_slash(tmp_path, http_origin):
    executor = downgrading_executor(tmp_path, http_origin, persistent=False)
    try:
        await executor.start("https://127.0.0.1:8443/gcar")
        assert executor.entry_url == "https://127.0.0.1:8443/gcar/"
        assert executor._allowed_origin == ("https", "127.0.0.1", 8443)
        assert executor._page.url == "https://127.0.0.1:8443/gcar/"
    finally:
        await executor.close()


async def test_downgrade_that_persists_fails_with_a_clear_error(tmp_path, http_origin):
    executor = downgrading_executor(tmp_path, http_origin, persistent=True)
    with pytest.raises(EntryNavigationError, match="https para http"):
        await executor.start("https://127.0.0.1:8443/gcar")


async def test_redirect_that_does_not_return_to_the_app_stays_blocked(tmp_path):
    executor = executor_type(redirect="https://evil.fixture.test/")(tmp_path, ExplorationOptions(), headless=True)
    try:
        await executor.start("https://app.fixture.test/gcar/")
        assert executor._auth_origins == set()
        assert executor._blocked_navigation_url.startswith("https://idp.fixture.test/")
        assert executor.render_stalled is True
    finally:
        await executor.close()
