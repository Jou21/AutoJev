"""Exercise the real preparation button through a local login redirect.

No external site, provider, saved run or user credentials are involved. These
tests use Chromium's real exposed binding, rather than calling service.resume
or invoking window.__autojev_resume directly.
"""

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.domain.models import ExplorationOptions


@pytest.fixture
def preparation_site():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path == "/login":
                html = """<!doctype html><html lang="pt-BR"><body>
                <main><h1>Entrar no ambiente local</h1>
                <form method="post" action="/login">
                  <label>Usuário <input name="username"></label>
                  <label>Senha <input name="password" type="password"></label>
                  <button type="submit">Entrar</button>
                </form></main></body></html>"""
            elif self.path == "/inventory.html" and "fixture_session=yes" in self.headers.get("Cookie", ""):
                html = """<!doctype html><html lang="pt-BR"><body>
                <main><h1>Produtos locais</h1>
                <label>Ordenar <select><option>A a Z</option><option>Z a A</option></select></label>
                </main></body></html>"""
            else:
                self.send_response(303)
                self.send_header("Location", "/login")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            content = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_POST(self):
            # Deliberately public, arbitrary values belonging only to this
            # disposable loopback form. AutoJev never performs this login.
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(303)
            self.send_header("Set-Cookie", "fixture_session=yes; Path=/; HttpOnly; SameSite=Lax")
            self.send_header("Location", "/inventory.html")
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/inventory.html"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


async def visible_preparation_button(executor):
    button = executor._page.get_by_role("button", name="Continuar exploração", exact=True)
    await button.wait_for(state="visible", timeout=5_000)
    assert await executor._page.locator("#autojev-sidepanel").count() == 1
    assert await executor._page.locator("#autojev-action-button").count() == 1
    assert await button.is_enabled()
    return button


@pytest.mark.asyncio
@pytest.mark.parametrize("reload_after_login", [False, True])
async def test_real_continue_button_survives_premature_confirmation_and_login_navigation(
    tmp_path, preparation_site, reload_after_login,
):
    executor = PlaywrightExecutor(
        tmp_path / "screenshots", ExplorationOptions(capture_screenshots=False), headless=True,
    )
    waits = []
    try:
        redirected = await executor.start(preparation_site)
        assert redirected.url.endswith("/login")
        # The real browser stays headless in CI; only enable the production
        # panel injection path after launch, as other real-browser tests do.
        executor.headless = False
        executor._intervention_message = "Faça login na página e confirme quando estiver pronto."

        first_wait = asyncio.create_task(executor.wait_for_user_input())
        waits.append(first_wait)
        first_button = await visible_preparation_button(executor)
        await first_button.click()
        await asyncio.wait_for(asyncio.shield(first_wait), timeout=3)
        # The low-level waiter only reports confirmation. The service then
        # calls attempt_filled_login; waiting alone never submits a form.
        assert executor._page.url.endswith("/login")

        executor._intervention_message = "A preparação ainda não está pronta. Entre primeiro e confirme novamente."
        second_wait = asyncio.create_task(executor.wait_for_user_input())
        waits.append(second_wait)
        await visible_preparation_button(executor)
        page = executor._page
        await page.get_by_label("Usuário").fill("fixture-user")
        await page.get_by_label("Senha").fill("fixture-password")
        await page.get_by_role("button", name="Entrar", exact=True).click()
        await page.wait_for_url(preparation_site)
        if reload_after_login:
            await page.reload()

        button = await visible_preparation_button(executor)
        # Arriving at the correct page alone is not confirmation. A second
        # explicit click, on the newly installed real DOM button, is required.
        assert not second_wait.done()
        assert await page.locator("#autojev-action-text").inner_text() == executor._intervention_message
        await button.click()
        await asyncio.wait_for(asyncio.shield(second_wait), timeout=3)
        assert page.url == preparation_site
        assert await page.locator("#autojev-panel-action").is_hidden()
    finally:
        for wait in waits:
            if not wait.done():
                wait.cancel()
        await asyncio.gather(*waits, return_exceptions=True)
        await executor.close()
