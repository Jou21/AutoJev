"""Host-side gates for the trusted rerun frame, without a real user session."""

import asyncio
from types import SimpleNamespace

import pytest

from autojev.adapters.browser_rerun_panel import BrowserRerunPanel, PANEL_URL
from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.domain.models import ExplorationOptions
from autojev.services.user_image_upload import validate_user_image
from autojev.services.rerun_preparation_presentation import preparation_presentation


LIVE_PAYLOAD = {
    "version": 1, "scenario_id": "CT001", "phase": "waiting_capture", "running": True,
    "checkpoint": {"id": "print-one", "version": 3, "gap": 2, "order": 0},
    "progress": {"steps": [
        {"id": "before", "title": "Antes da ação", "state": "done", "label": "Captura registrada"},
        {"id": "action", "title": "Selecionar opção", "state": "done", "label": "Ação concluída"},
        {"id": "print-one", "title": "Aguardar print", "state": "waiting", "label": "Aguardando seu print"},
        {"id": "after", "title": "Depois da ação", "state": "pending", "label": "Aguardando"},
    ], "completed": 2, "total": 4, "current_id": "print-one", "notice": "Roteiro planejado"},
    "capture_count": 1, "captures_completed": 0, "capture_timeout_seconds": 900,
    "usage": {"total_tokens": 0, "cost": 0, "cost_known": True},
}


@pytest.fixture
async def isolated_browser_panel():
    """Only a disposable, in-memory page and the locally fulfilled UI asset."""
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        calls, signals = [], []
        executor = SimpleNamespace(_page=page, headless=False, notify_user_input=lambda: signals.append(True))

        async def capture(*args):
            calls.append(args)
            return {"accepted": True}

        panel = BrowserRerunPanel(executor, capture)
        preview = {"checkpoint_id": "print-one", "checkpoint_version": 3,
                   "revision": 0, "changed": True, "image": None}

        async def share_preview(checkpoint_id, checkpoint_version, expected_revision, image_data_url):
            # This fixture emulates the service's ephemeral CAS store. Sharing
            # never calls capture or signals a waiter; the real sandbox/frame
            # and nonce gates still run before this handler is reached.
            if ((checkpoint_id, checkpoint_version) != (preview["checkpoint_id"], preview["checkpoint_version"])
                    or expected_revision != preview["revision"]):
                raise ValueError("A prévia mudou em outra janela")
            image = validate_user_image(image_data_url) if image_data_url is not None else None
            preview.update(revision=expected_revision + 1, image=(
                {"data_url": image_data_url, "name": "Print sincronizado", "size": len(image.content)}
                if image is not None else None))
            await panel.set_preview(preview)
            return dict(preview)

        panel.preview_handler = share_preview
        await panel.set_preview(preview)
        await panel.bind_page(page)

        async def intercept(route, request):
            if not await panel.route(route, request):
                await route.abort("blockedbyclient")

        await page.context.route("**/*", intercept)
        await page.set_content("""<main><h1>Disposable tested application</h1></main>
          <aside id="autojev-sidepanel"><button class="autojev-panel-rail">J</button>
          <div class="autojev-panel-surface"></div></aside>""")
        try:
            yield panel, page, calls, signals
        finally:
            panel.close()
            await browser.close()


class FixtureFrame:
    def __init__(self, page):
        self.page = page
        self.url = PANEL_URL
        self.detached = False

    def is_detached(self):
        return self.detached


@pytest.fixture
def panel_gate():
    received = []
    signals = []
    page = object()
    executor = SimpleNamespace(_page=page, headless=False, notify_user_input=lambda: signals.append(True))

    async def capture(checkpoint_id, checkpoint_version, image):
        received.append((checkpoint_id, checkpoint_version, image))
        return {"accepted": True}

    panel = BrowserRerunPanel(executor, capture)
    panel.frame = FixtureFrame(page)
    panel._payload = {
        "phase": "waiting_capture", "running": True,
        "checkpoint": {"id": "print-one", "version": 3},
    }
    return panel, received, signals


def capture_command(panel, **overrides):
    return {
        "op": "capture", "nonce": panel._nonce, "confirm": True,
        "checkpoint_id": "print-one", "checkpoint_version": 3,
        "image_data_url": "data:image/png;base64,cHVibGljLWZpeHR1cmU=", **overrides,
    }


def trusted_source(panel):
    return {"page": panel.executor._page, "frame": panel.frame}


@pytest.mark.asyncio
@pytest.mark.parametrize("source_kind", ["parent", "sibling", "other_page", "missing"])
async def test_capture_rejects_non_owned_frame_before_image_handler(panel_gate, source_kind):
    panel, received, signals = panel_gate
    source = trusted_source(panel)
    if source_kind == "parent":
        source["frame"] = SimpleNamespace(url=PANEL_URL)
    elif source_kind == "sibling":
        source["frame"] = FixtureFrame(panel.executor._page)
    elif source_kind == "other_page":
        source["page"] = object()
    else:
        source = {}
    with pytest.raises(ValueError):
        await panel.command(source, capture_command(panel))
    assert received == signals == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mutate", ["navigated", "detached", "closed", "cancelled", "active"])
async def test_capture_rejects_stale_or_cancelled_document(panel_gate, mutate):
    panel, received, signals = panel_gate
    source = trusted_source(panel)
    command = capture_command(panel)
    if mutate == "navigated":
        panel.frame.url = "https://example.invalid/attacker"
    elif mutate == "detached":
        panel.frame.detached = True
    elif mutate == "closed":
        panel.close()
    elif mutate == "cancelled":
        panel._payload["running"] = False
    else:
        panel._payload["phase"] = "executing"
    with pytest.raises(ValueError):
        await panel.command(source, command)
    assert received == signals == []


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [
    {"nonce": "wrong"}, {"confirm": False}, {"confirm": 1},
    {"checkpoint_id": "print-two"}, {"checkpoint_version": 2},
    {"checkpoint_version": True}, {"checkpoint_version": "3"},
    {"op": "screenshot"}, {"image_data_url": {}},
])
async def test_capture_requires_exact_current_confirmation(panel_gate, override):
    panel, received, signals = panel_gate
    with pytest.raises(ValueError):
        await panel.command(trusted_source(panel), capture_command(panel, **override))
    assert received == signals == []


@pytest.mark.asyncio
async def test_capture_is_explicit_and_one_use(panel_gate):
    panel, received, signals = panel_gate
    assert received == []
    command = capture_command(panel)
    assert await panel.command(trusted_source(panel), command) == {"accepted": True}
    assert received == [("print-one", 3, command["image_data_url"])]
    assert signals == []
    with pytest.raises(ValueError):
        await panel.command(trusted_source(panel), command)
    assert len(received) == 1


@pytest.mark.asyncio
async def test_parallel_confirmation_cannot_upload_twice(panel_gate):
    panel, received, _ = panel_gate
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_capture(*args):
        entered.set()
        await release.wait()
        received.append(args)
        return {"accepted": True}

    panel.capture_handler = delayed_capture
    first = asyncio.create_task(panel.command(trusted_source(panel), capture_command(panel)))
    await entered.wait()
    try:
        with pytest.raises(ValueError):
            await panel.command(trusted_source(panel), capture_command(panel))
    finally:
        release.set()
        await first
    assert len(received) == 1


@pytest.mark.asyncio
async def test_rejected_upload_can_retry_without_consuming_the_checkpoint(panel_gate):
    panel, received, _ = panel_gate

    async def fail_capture(*_args):
        raise ValueError("Imagem inválida")

    original = panel.capture_handler
    panel.capture_handler = fail_capture
    with pytest.raises(ValueError):
        await panel.command(trusted_source(panel), capture_command(panel))
    panel.capture_handler = original
    assert await panel.command(trusted_source(panel), capture_command(panel)) == {"accepted": True}
    assert len(received) == 1


@pytest.mark.asyncio
async def test_resume_signals_only_current_preparation_waiter(panel_gate):
    panel, received, signals = panel_gate
    panel._payload = {"running": True, "phase": "waiting_preparation", "preparation": {"version": 2}}
    command = {"op": "resume", "nonce": panel._nonce, "confirm": True, "preparation_version": 2}
    with pytest.raises(ValueError):
        await panel.command(trusted_source(panel), {**command, "preparation_version": 1})
    assert signals == received == []
    assert await panel.command(trusted_source(panel), command) == {"accepted": True}
    assert signals == [True] and received == []
    with pytest.raises(ValueError):
        await panel.command(trusted_source(panel), command)
    assert signals == [True]


@pytest.mark.asyncio
async def test_versioned_service_resume_is_authoritative_not_generic_browser_signal(panel_gate):
    panel, received, signals = panel_gate
    panel._payload = {"running": True, "phase": "waiting_preparation", "preparation": {"version": 2}}
    command = {"op": "resume", "nonce": panel._nonce, "confirm": True, "preparation_version": 2}
    checked = []

    async def deny_stale(version):
        checked.append(version)
        raise ValueError("A preparação mudou")

    panel.resume_handler = deny_stale
    with pytest.raises(ValueError):
        await panel.command(trusted_source(panel), command)
    assert checked == [2] and signals == received == []

    async def accept_current(version):
        checked.append(version)
        return {"accepted": True}

    panel.resume_handler = accept_current
    assert await panel.command(trusted_source(panel), command) == {"accepted": True}
    assert checked == [2, 2] and signals == received == []
    with pytest.raises(ValueError):
        await panel.command(trusted_source(panel), command)


@pytest.mark.asyncio
async def test_upload_internal_error_does_not_disclose_paths_or_consume_checkpoint(panel_gate):
    panel, received, _ = panel_gate

    async def storage_failed(*_args):
        raise OSError("/private-fixture-path/must-not-leak")

    panel.capture_handler = storage_failed
    with pytest.raises(ValueError) as failure:
        await panel.command(trusted_source(panel), capture_command(panel))
    assert "private-fixture" not in str(failure.value)
    assert received == [] and panel._used == set()


class FixtureRoute:
    def __init__(self):
        self.abort_code = None
        self.fulfilled = None

    async def abort(self, code):
        self.abort_code = code

    async def fulfill(self, **kwargs):
        self.fulfilled = kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["parent", "foreign", "asset", "post", "closed"])
async def test_virtual_panel_route_never_serves_unowned_requests(panel_gate, kind):
    panel, _, _ = panel_gate
    request = SimpleNamespace(
        url=PANEL_URL, method="GET", frame=panel.frame,
        is_navigation_request=lambda: True,
    )
    if kind in {"parent", "foreign"}:
        request.frame = FixtureFrame(panel.executor._page)
    elif kind == "asset":
        request.is_navigation_request = lambda: False
    elif kind == "post":
        request.method = "POST"
    else:
        panel.close()
    route = FixtureRoute()
    assert await panel.route(route, request) is True
    assert route.abort_code == "blockedbyclient"
    assert route.fulfilled is None


@pytest.mark.asyncio
async def test_panel_route_does_not_intercept_application_requests(panel_gate):
    panel, _, _ = panel_gate
    route = FixtureRoute()
    assert await panel.route(route, SimpleNamespace(url="https://fixture.invalid/inventory.html")) is False
    assert route.abort_code is route.fulfilled is None


@pytest.mark.asyncio
async def test_real_frame_is_cross_origin_and_cannot_connect_or_store_data(isolated_browser_panel):
    panel, page, calls, _ = isolated_browser_panel
    assert await panel.update(LIVE_PAYLOAD) is True
    frame = panel.frame
    assert await frame.locator("#steps .step").count() == 4
    assert await frame.locator(".step[data-state=done]").count() == 2
    assert await frame.locator(".step[data-state=waiting]").count() == 1
    assert await frame.locator(".step[data-state=pending]").count() == 1
    assert "Captura registrada" in await frame.locator(".step[data-step-id=before]").inner_text()
    assert await page.evaluate("document.querySelector('iframe').contentDocument === null") is True
    assert await frame.evaluate("globalThis.origin") == "null"
    assert await frame.evaluate("""async () => {
      try { await fetch('https://example.invalid/should-never-be-sent'); return false; }
      catch (_) { return true; }
    }""") is True
    assert await frame.evaluate("""() => {
      try { localStorage.setItem('not-allowed', 'x'); return false; }
      catch (_) { return true; }
    }""") is True
    # Even knowledge of the nonce does not grant the parent application upload
    # authority: Playwright reports the binding's actual source frame.
    assert await page.evaluate("""async command => {
      try { await window.__autojev_rerun_command(command); return false; }
      catch (_) { return true; }
    }""", capture_command(panel)) is True
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reason_code, manual_status", [
    ("authentication_required", None), ("before_url_changed", None),
    ("authentication_required", "ready"), ("before_url_changed", "blocked"),
])
async def test_real_preparation_ui_distinguishes_detected_login_from_other_url_mismatch(
        isolated_browser_panel, reason_code, manual_status):
    from playwright.async_api import expect

    panel, _, calls, signals = isolated_browser_panel
    shared = {"preparation_version": 2, "revision": 0, "checked": False}
    changes = []

    async def share_consent(version, expected_revision, checked):
        assert version == shared["preparation_version"] and expected_revision == shared["revision"]
        shared.update(revision=expected_revision + 1, checked=checked)
        changes.append(dict(shared))
        return dict(shared)

    panel.consent_handler = share_consent
    payload = {**LIVE_PAYLOAD, "phase": "waiting_preparation", "checkpoint": None,
               "preparation": {"version": 2, "reason_code": reason_code,
                               "reason_label": "A página aberta não é a mesma em que o teste original começou.",
                               "expected_url": "https://fixture.invalid/inventory",
                               "current_url": "https://fixture.invalid/", "instruction": "Confira a preparação.",
                               "consent": dict(shared)}}
    if manual_status is not None:
        payload["preparation"]["manual_login"] = {"status": manual_status}
    canonical = preparation_presentation(payload["preparation"], "waiting_preparation")
    assert await panel.update(payload) is True
    frame = panel.frame
    # Legacy clients can omit the presentation object. The panel's safe
    # defaults must match the canonical main-app vocabulary exactly.
    for key, selector in (("kicker", "#preparation-eyebrow"), ("title", "#preparation-heading"),
                          ("instruction", "#preparation-hint"), ("consent_label", "#preparation-consent-label"),
                          ("button_label", "#confirm-preparation"), ("details_label", "#preparation-details-label"),
                          ("note", "#preparation-note"), ("feedback", "#preparation-feedback")):
        assert await frame.locator(selector).inner_text() == canonical[key]
    assert not await frame.locator("#preparation-consent").is_checked()
    assert await frame.locator("#confirm-preparation").is_disabled()
    await frame.locator("#confirm-preparation").dispatch_event("click")
    assert calls == signals == []
    assert changes == []
    # Better wording does not authorize a login attempt: both explicit consent
    # and a native confirmation remain necessary in the trusted frame.
    await frame.locator("#preparation-consent").check()
    await expect(frame.locator("#confirm-preparation")).to_be_enabled()
    assert changes == [{"preparation_version": 2, "revision": 1, "checked": True}]
    assert calls == signals == [], "sharing authorization does not submit login or resume"
    await frame.locator("#confirm-preparation").dispatch_event("click")
    assert calls == signals == []
    await frame.locator("#confirm-preparation").click()
    async with asyncio.timeout(3):
        while not signals:
            await asyncio.sleep(0.01)
    assert signals == [True] and calls == []


@pytest.mark.asyncio
async def test_real_shared_consent_from_other_surface_enables_native_continue_without_second_checkbox(isolated_browser_panel):
    from playwright.async_api import expect

    panel, _, calls, signals = isolated_browser_panel
    preparation = {"version": 2, "reason_code": "authentication_required",
                   "consent": {"preparation_version": 2, "revision": 0, "checked": False}}
    payload = {**LIVE_PAYLOAD, "phase": "waiting_preparation", "checkpoint": None, "preparation": preparation}
    assert await panel.update(payload) is True
    frame = panel.frame
    await expect(frame.locator("#preparation-consent")).not_to_be_checked()
    preparation = {**preparation, "consent": {"preparation_version": 2, "revision": 1, "checked": True}}
    assert await panel.update({**payload, "preparation": preparation}) is True
    await expect(frame.locator("#preparation-consent")).to_be_checked()
    await expect(frame.locator("#confirm-preparation")).to_be_enabled()
    assert signals == calls == []
    await frame.locator("#confirm-preparation").click()
    async with asyncio.timeout(3):
        while not signals:
            await asyncio.sleep(0.01)
    assert signals == [True] and calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["file", "paste", "drop"])
async def test_real_ui_requires_native_confirmation_after_file_preview(isolated_browser_panel, user_print_image, source):
    from playwright.async_api import expect

    panel, _, calls, _ = isolated_browser_panel
    assert await panel.update(LIVE_PAYLOAD) is True
    frame = panel.frame
    if source == "file":
        await frame.locator("#file-input").set_input_files({
            "name": "another-application.png", "mimeType": "image/png", "buffer": user_print_image["bytes"],
        })
    else:
        # Exercise the same clipboard/drop event handlers with a public fixture
        # image. These events may share an ephemeral preview, never authorize
        # its attachment as evidence or resume the test.
        await frame.evaluate("""({data, source}) => {
          const bytes = Uint8Array.from(atob(data.split(',')[1]), c => c.charCodeAt(0));
          const transfer = new DataTransfer();
          transfer.items.add(new File([bytes], 'another-application.png', {type:'image/png'}));
          document.dispatchEvent(source === 'paste'
            ? new ClipboardEvent('paste', {clipboardData:transfer,bubbles:true,cancelable:true})
            : new DragEvent('drop', {dataTransfer:transfer,bubbles:true,cancelable:true}));
        }""", {"data": user_print_image["data_url"], "source": source})
    await expect(frame.locator("#confirm-capture")).to_be_enabled()
    assert "Falta confirmar" in await frame.locator("#capture-status").inner_text()
    assert await frame.locator("#preview").get_attribute("src") == user_print_image["data_url"]
    assert calls == []
    assert panel._preview_snapshot["revision"] == 1
    assert panel._preview_snapshot["image"]["data_url"] == user_print_image["data_url"]
    await frame.locator("#confirm-capture").dispatch_event("click")
    await asyncio.sleep(0.05)
    assert calls == []
    # Repeated host projections must preserve a preview at the same checkpoint.
    assert await panel.update(LIVE_PAYLOAD) is True
    assert await frame.locator("#preview").get_attribute("src") == user_print_image["data_url"]
    await frame.locator("#confirm-capture").click()
    async with asyncio.timeout(3):
        while not calls:
            await asyncio.sleep(0.01)
    assert calls == [("print-one", 3, user_print_image["data_url"])]
    assert await frame.locator("#confirm-capture").is_disabled()
    assert "Salvando" in await frame.locator("#confirm-capture").inner_text()


@pytest.mark.asyncio
async def test_frame_restriction_fails_closed_without_disabling_application_csp(isolated_browser_panel):
    panel, page, calls, _ = isolated_browser_panel
    await page.set_content("""<meta http-equiv="Content-Security-Policy"
      content="default-src 'none'; frame-src 'none'; script-src 'none'">
      <main><h1>Restricted tested application</h1></main>
      <aside id="autojev-sidepanel"><div class="autojev-panel-surface"></div></aside>""")
    assert await panel.update(LIVE_PAYLOAD) is False
    assert panel.frame is None
    assert panel.unavailable is True
    assert await page.locator("#autojev-rerun-frame").count() == 0
    # Polling the same restricted document must not keep inserting or fetching
    # frames. A real navigation may try the optional UI again with fresh gates.
    assert await panel.update(LIVE_PAYLOAD) is False
    assert await page.locator("#autojev-rerun-frame").count() == 0
    await page.evaluate("""() => {
      const script = document.createElement('script');
      script.textContent = 'window.testedApplicationCspWasBypassed = true';
      document.body.append(script);
    }""")
    assert await page.evaluate("window.testedApplicationCspWasBypassed === true") is False
    assert calls == []
    await page.goto("about:blank")
    await page.set_content('<aside id="autojev-sidepanel"><div class="autojev-panel-surface"></div></aside>')
    assert panel.unavailable is False
    assert await panel.update(LIVE_PAYLOAD) is True
    assert await panel.frame.locator("#steps .step").count() == 4


@pytest.mark.asyncio
async def test_iframe_only_reload_revokes_confirmation_and_restores_same_projection(isolated_browser_panel, user_print_image):
    from playwright.async_api import expect

    panel, _, calls, _ = isolated_browser_panel
    assert await panel.update(LIVE_PAYLOAD) is True
    frame = panel.frame
    old_command = capture_command(panel)

    async def stage():
        await frame.locator("#file-input").set_input_files({
            "name": "another-application.png", "mimeType": "image/png", "buffer": user_print_image["bytes"],
        })
        await expect(frame.locator("#confirm-capture")).to_be_enabled()

    await stage()
    await frame.goto(PANEL_URL, wait_until="domcontentloaded")
    assert panel.frame is frame
    assert await panel.update(LIVE_PAYLOAD) is True
    assert await frame.locator("#steps .step").count() == 4
    # A reload revokes the old document's nonce, not the service-owned preview.
    # The same pending checkpoint is restored without any new capture call.
    await expect(frame.locator("#confirm-capture")).to_be_enabled()
    assert await frame.locator("#selected-file").is_visible()
    assert await frame.locator("#preview").get_attribute("src") == user_print_image["data_url"]
    with pytest.raises(ValueError):
        await panel.command(trusted_source(panel), old_command)
    assert calls == []
    await frame.locator("#confirm-capture").dispatch_event("click")
    await asyncio.sleep(0.05)
    assert calls == []
    await frame.locator("#confirm-capture").click()
    async with asyncio.timeout(3):
        while not calls:
            await asyncio.sleep(0.01)
    assert len(calls) == 1
    # The browser Frame object survives its own navigation, but acceptance is
    # one-use per checkpoint, not per document or nonce generation.
    await frame.goto(PANEL_URL, wait_until="domcontentloaded")
    assert await panel.update(LIVE_PAYLOAD) is True
    await stage()
    await frame.locator("#confirm-capture").click()
    await expect(frame.locator("#capture-status")).to_have_attribute("data-tone", "error")
    assert len(calls) == 1


def test_injected_panel_diagnostics_do_not_contaminate_application_evidence(tmp_path):
    executor = PlaywrightExecutor(tmp_path, ExplorationOptions(capture_console_errors=True, capture_network_errors=True))
    executor.configure_rerun_panel(lambda *_args: None)
    page = SimpleNamespace(url="https://fixture.invalid/inventory.html")
    executor._on_console(SimpleNamespace(type="error", text="Panel rendering failed", location={"url": PANEL_URL}), page=page)
    executor._on_console(SimpleNamespace(
        type="error", location={"url": page.url},
        text=f'Refused to frame "{PANEL_URL}" because it violates the following Content Security Policy directive: "frame-src none".',
    ), page=page)
    panel_error = RuntimeError("Panel rendering failed")
    panel_error.stack = f"Error: Panel rendering failed\n  at render ({PANEL_URL}:12:3)"
    executor._on_page_error(panel_error, page=page)
    executor._on_response(SimpleNamespace(url=PANEL_URL, status=500), page=page)
    executor._on_request_failed(SimpleNamespace(url=PANEL_URL, failure="blockedbyclient", method="GET"), page=page)
    assert list(executor._console_buffer) == []
    assert list(executor._network_buffer) == []


def test_real_application_failures_are_still_recorded(tmp_path):
    executor = PlaywrightExecutor(tmp_path, ExplorationOptions(capture_console_errors=True, capture_network_errors=True))
    executor.configure_rerun_panel(lambda *_args: None)
    page = SimpleNamespace(url="https://fixture.invalid/inventory.html")
    executor._on_console(SimpleNamespace(type="error", text="Application console failure", location={"url": page.url}), page=page)
    executor._on_page_error(RuntimeError("Application runtime failure"), page=page)
    executor._on_console(SimpleNamespace(
        type="error", text=f"Application failure; incidental mention of {PANEL_URL}", location={"url": page.url},
    ), page=page)
    incidental_error = RuntimeError(f"Application failure; incidental mention of {PANEL_URL}")
    incidental_error.stack = str(incidental_error) + "\n  at application (https://fixture.invalid/app.js:4:1)"
    executor._on_page_error(incidental_error, page=page)
    executor._on_response(SimpleNamespace(
        url="https://fixture.invalid/api", status=503,
        request=SimpleNamespace(method="GET", resource_type="xhr"),
    ), page=page)
    executor._on_request_failed(SimpleNamespace(url="https://fixture.invalid/api", failure="reset", method="GET"), page=page)
    assert [row["type"] for row in executor._console_buffer] == ["console", "pageerror", "console", "pageerror"]
    assert [row["type"] for row in executor._network_buffer] == ["http", "network"]
    assert all(row["page_url"] == page.url for row in [*executor._console_buffer, *executor._network_buffer])

