"""Explicit Continue submits only an unambiguous, already-filled local login.

Real Chromium uses a disposable loopback server. No saved run, provider,
external site, real credential, or test execution is touched.
"""

import asyncio
import json
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.domain.models import AgentDecision, ExplorationOptions

USER = "disposable-user"
PASSWORD = "  disposable-password  "
LOGIN = """<!doctype html><meta charset="utf-8"><title>Disposable login</title>
<style>input,button{display:block;margin:12px;padding:12px}</style>
<main><h1>Entrar no ambiente local</h1>
<form id="login"><label>Usuário <input name="username" autocomplete="username"></label>
<label>Senha <input name="password" type="password" autocomplete="current-password"></label>
<input type="submit" value="Login"></form></main>
<script>
window.loginSubmissions=0;window.loginInputs=0;
document.querySelector('form').addEventListener('input',()=>window.loginInputs++);
document.querySelector('form').onsubmit=e=>{
 e.preventDefault();window.loginSubmissions++;
 const f=e.target;
 if(f.username.value==='disposable-user' && f.password.value==='  disposable-password  '){
   history.pushState({},'', '/inventory.html');
   document.body.innerHTML='<h1>Produtos locais</h1><a href="/inventory.html">Ver produtos</a>';
 }else{
   const error=document.createElement('p');error.setAttribute('role','alert');
   error.textContent='Invalid login';f.append(error);
 }
};
</script>"""


@pytest.fixture
def login_site():
    state = {"html": LOGIN, "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            state["requests"].append(("GET", self.path))
            content = state["html"].encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_port}/"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


async def start_login(tmp_path, login_site, html=LOGIN):
    login_site["html"] = html
    executor = PlaywrightExecutor(
        tmp_path, ExplorationOptions(capture_screenshots=False), headless=True,
    )
    observation = await executor.start(login_site["url"])
    return executor, observation


async def fill_login(executor, username=USER, password=PASSWORD):
    await executor._page.locator('[name="username"]').fill(username)
    await executor._page.locator('[name="password"]').fill(password)


@pytest.mark.asyncio
async def test_real_continue_click_attempts_filled_login_without_overwriting(tmp_path, login_site):
    executor, observation = await start_login(tmp_path, login_site)
    task = None
    try:
        await fill_login(executor)
        executor.headless = False  # Show the production panel in headless CI.

        async def confirmed_login():
            await executor.wait_for_user_input()
            return await executor.attempt_filled_login(observation)

        task = asyncio.create_task(confirmed_login())
        button = executor._page.get_by_role("button", name="Continuar exploração", exact=True)
        await button.wait_for(state="visible")
        assert await executor._page.evaluate("window.loginSubmissions") == 0
        await button.click()
        result = await asyncio.wait_for(task, 15)
        assert result.status == "success", result.reason
        assert result.observation.url.endswith("/inventory.html")
        assert await executor._page.evaluate("window.loginSubmissions") == 1
        assert await executor._page.evaluate("window.loginInputs") == 2
        assert not executor._automatic_login_attempted
        payload = json.dumps(asdict(result.observation))
        assert USER not in payload and PASSWORD.strip() not in payload
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await executor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("username,password", [("", PASSWORD), (USER, ""), ("", "")])
async def test_empty_fields_are_not_filled_or_submitted(tmp_path, login_site, username, password):
    executor, _ = await start_login(tmp_path, login_site)
    try:
        await fill_login(executor, username, password)
        inputs_before = await executor._page.evaluate("window.loginInputs")
        result = await executor.attempt_filled_login()
        assert (result.status, result.reason) == ("manual_required", "incomplete_credentials")
        assert await executor._page.locator('[name=username]').input_value() == username
        assert await executor._page.locator('[name=password]').input_value() == password
        assert await executor._page.evaluate("window.loginInputs") == inputs_before
        assert await executor._page.evaluate("window.loginSubmissions") == 0
    finally:
        await executor.close()


@pytest.mark.asyncio
async def test_rejected_login_has_no_automatic_retry_but_new_confirmation_can_retry(tmp_path, login_site):
    executor, _ = await start_login(tmp_path, login_site)
    try:
        executor._automatic_login_attempted = True
        await fill_login(executor, password="disposable-wrong-password")
        result = await executor.attempt_filled_login()
        assert (result.status, result.reason) == ("rejected", "login_rejected")
        await asyncio.sleep(0.4)
        assert await executor._page.evaluate("window.loginSubmissions") == 1
        # The user explicitly corrects the field and confirms a second time.
        await executor._page.locator('[name=password]').fill(PASSWORD)
        result = await executor.attempt_filled_login()
        assert result.status == "success", result.reason
        assert await executor._page.evaluate("window.loginSubmissions") == 2
        assert executor._automatic_login_attempted
    finally:
        await executor.close()


@pytest.mark.asyncio
async def test_stale_deep_link_error_does_not_reject_delayed_success(tmp_path, login_site):
    html = LOGIN.replace(
        "history.pushState({},'', '/inventory.html');",
        "setTimeout(()=>{history.pushState({},'', '/inventory.html');",
    ).replace(
        "document.body.innerHTML='<h1>Produtos locais</h1><a href=\"/inventory.html\">Ver produtos</a>';",
        "document.body.innerHTML='<h1>Produtos locais</h1><a href=\"/inventory.html\">Ver produtos</a>';},1700);",
    ).replace("</form>", '<p role="alert">You can only access inventory when you are logged in</p></form>')
    executor, _ = await start_login(tmp_path, login_site, html)
    try:
        await fill_login(executor)
        result = await executor.attempt_filled_login()
        assert result.status == "success", result.reason
        assert await executor._page.evaluate("window.loginSubmissions") == 1
        assert result.observation.url.endswith("/inventory.html")
    finally:
        await executor.close()


@pytest.mark.asyncio
async def test_late_echo_after_rejection_remains_redacted_in_next_observation(tmp_path, login_site):
    executor, _ = await start_login(tmp_path, login_site)
    password = "disposable-wrong-password"
    try:
        await fill_login(executor, password=password)
        result = await executor.attempt_filled_login()
        assert result.status == "rejected"
        await executor._page.evaluate("""values => {
          setTimeout(()=>{
            const echo=document.createElement('p');
            echo.textContent='Rejected credentials: '+values.join(' / ');
            document.body.append(echo);
          },100);
        }""", [USER, password])
        await asyncio.sleep(0.2)
        observed = await executor.observe("later-service-observation")
        payload = json.dumps(asdict(observed))
        assert USER not in payload and password not in payload
        assert "[REDACTED]" in payload
    finally:
        await executor.close()
    assert executor.secret_values == []


@pytest.mark.asyncio
async def test_anonymous_email_field_is_masked_before_pre_login_capture(tmp_path, login_site):
    email = "disposable@example.test"
    html = LOGIN.replace('name="username" autocomplete="username"', 'name="contact" type="email"')
    html = html.replace("f.username.value", "f.contact.value").replace(USER, email)
    executor, _ = await start_login(tmp_path, login_site, html)
    try:
        executor.options.capture_screenshots = True
        await executor._page.locator('[name=contact]').fill(email)
        await executor._page.locator('[name=password]').fill(PASSWORD)
        capture = executor._capture
        refreshed_masks = []

        async def guarded_capture(label, **kwargs):
            if label == "before-user-login":
                refreshed_masks.append(await executor._page.locator('[name=contact]').get_attribute('data-autojev-secret-mask'))
            return await capture(label, **kwargs)

        executor._capture = guarded_capture
        result = await executor.attempt_filled_login()
        assert result.status == "success", result.reason
        assert refreshed_masks == ["true"]
        assert email not in json.dumps(asdict(result.observation))
    finally:
        await executor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("username_type", ["email", "text", "tel"])
async def test_preparation_observe_masks_anonymous_login_but_not_checkout_fields(
    tmp_path, login_site, username_type,
):
    # Observe happens before attempt_filled_login, as it does in the service.
    # Neither field has a sensitive name, autocomplete, or masking attribute.
    html = LOGIN.replace(
        'name="username" autocomplete="username"', f'name="contact" type="{username_type}"',
    ).replace("</main>", '<form id="checkout"><label>Endereço <input name="delivery"></label></form></main>')
    executor, _ = await start_login(tmp_path, login_site, html)
    try:
        executor.options.capture_screenshots = True
        page = executor._page
        await page.locator('[name=contact]').fill("disposable@example.test")
        await page.locator('[name=password]').fill(PASSWORD)
        await page.locator('[name=delivery]').fill("Public fixture address")
        before = await page.evaluate("""() => Array.from(document.querySelectorAll('input')).map(el=>({
          name:el.name,value:el.value,style:el.getAttribute('style'),mask:el.getAttribute('data-autojev-secret-mask'),
        }))""")
        login_box = await page.locator('[name=contact]').bounding_box()
        checkout_box = await page.locator('[name=delivery]').bounding_box()
        observed = await executor.observe("rerun-before")
        after = await page.evaluate("""() => Array.from(document.querySelectorAll('input')).map(el=>({
          name:el.name,value:el.value,style:el.getAttribute('style'),mask:el.getAttribute('data-autojev-secret-mask'),
        }))""")
        assert before == after
        assert executor.secret_values == []
        assert observed.screenshot
        with Image.open(tmp_path / observed.screenshot) as screenshot:
            login_pixel = screenshot.convert("RGB").getpixel((
                int(login_box["x"] + login_box["width"] / 2),
                int(login_box["y"] + login_box["height"] / 2),
            ))
            checkout_pixel = screenshot.convert("RGB").getpixel((
                int(checkout_box["x"] + checkout_box["width"] / 2),
                int(checkout_box["y"] + checkout_box["height"] / 2),
            ))
        # JPEG capture: the mask colour survives within a small lossy margin.
        def near_mask(pixel):
            return max(abs(a - b) for a, b in zip(pixel, (16, 20, 22))) <= 4
        assert near_mask(login_pixel)
        assert not near_mask(checkout_pixel)
    finally:
        await executor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation,reason", [
    ('<input type="password" name="confirm">', "ambiguous_form"),
    ('<input name="secondUsername">', "ambiguous_form"),
    ('<input type="submit" value="Login">', "ambiguous_form"),
    ('<input name="otp" autocomplete="one-time-code">', "authentication_challenge"),
    ('<div class="g-recaptcha" style="width:100px;height:100px">CAPTCHA</div>', "authentication_challenge"),
    ('<input type="checkbox" required>', "additional_required_fields"),
])
async def test_ambiguous_and_challenged_filled_forms_remain_manual(tmp_path, login_site, mutation, reason):
    executor, _ = await start_login(tmp_path, login_site, LOGIN.replace("</form>", mutation + "</form>"))
    try:
        await fill_login(executor)
        result = await executor.attempt_filled_login()
        assert (result.status, result.reason) == ("manual_required", reason)
        assert await executor._page.evaluate("window.loginSubmissions") == 0
        assert await executor._page.evaluate("window.loginInputs") == 2
    finally:
        await executor.close()


@pytest.mark.asyncio
async def test_off_origin_form_action_is_refused_before_submission(tmp_path, login_site):
    html = LOGIN.replace('<form id="login">', '<form id="login" action="http://localhost:1/login">')
    executor, _ = await start_login(tmp_path, login_site, html)
    try:
        await fill_login(executor)
        result = await executor.attempt_filled_login()
        assert (result.status, result.reason) == ("manual_required", "unsafe_form_action")
        assert await executor._page.evaluate("window.loginSubmissions") == 0
    finally:
        await executor.close()


@pytest.mark.asyncio
async def test_wrong_authorized_origin_is_refused_before_observing_or_clicking(tmp_path, login_site):
    executor, _ = await start_login(tmp_path, login_site)
    try:
        await fill_login(executor)
        executor._allowed_origin = ("http", "localhost", 1)
        result = await executor.attempt_filled_login()
        assert (result.status, result.reason) == ("manual_required", "unsafe_origin")
        assert await executor._page.evaluate("window.loginSubmissions") == 0
    finally:
        await executor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [
    "document.querySelector('[name=password]').replaceWith(document.querySelector('[name=password]').cloneNode(true))",
    "document.querySelector('form').action='/unrelated-operation'",
    "document.querySelector('[name=username]').name='paymentRecipient'",
])
async def test_changed_login_controls_fail_closed(tmp_path, login_site, mutation):
    executor, _ = await start_login(tmp_path, login_site)
    try:
        await fill_login(executor)
        resolve = executor._locator
        count = 0

        async def mutate_after_handles(ref):
            nonlocal count
            control = await resolve(ref)
            count += 1
            if count == 3:
                await executor._page.evaluate(mutation)
            return control

        executor._locator = mutate_after_handles
        result = await executor.attempt_filled_login()
        assert (result.status, result.reason) == ("manual_required", "controls_changed")
        assert await executor._page.evaluate("window.loginSubmissions") == 0
    finally:
        await executor.close()


@pytest.mark.asyncio
async def test_native_get_cannot_send_filled_credentials_in_url(tmp_path, login_site):
    html = LOGIN.split("<script>")[0] + "<script>window.loginSubmissions=0;window.loginInputs=0;</script>"
    executor, _ = await start_login(tmp_path, login_site, html)
    try:
        await fill_login(executor)
        result = await asyncio.wait_for(executor.attempt_filled_login(), 15)
        assert result.status == "manual_required"
        assert executor._page.url == login_site["url"]
        assert all(USER not in path and PASSWORD.strip() not in path for _, path in login_site["requests"])
    finally:
        await executor.close()


@pytest.mark.asyncio
async def test_explicit_login_does_not_weaken_generic_action_guard(tmp_path, login_site):
    executor, _ = await start_login(tmp_path, login_site)
    try:
        await fill_login(executor)
        observation = await executor.observe("filled-login")
        login = next(el for el in observation.interactive_elements if el.get("name") == "Login")
        action, _ = await executor.execute(AgentDecision("click", "Fixture", target=login["ref"]), {})
        assert not action.success
        assert "PolicyBlocked" in action.message
        assert await executor._page.evaluate("window.loginSubmissions") == 0
        result = await executor.attempt_filled_login()
        assert result.status == "success", result.reason
        assert await executor._page.evaluate("window.loginSubmissions") == 1
    finally:
        await executor.close()
