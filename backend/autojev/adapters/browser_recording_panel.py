"""Read-only recording progress and an isolated, explicit finish control."""
from __future__ import annotations

import asyncio
import secrets
from pathlib import Path
from urllib.parse import urlsplit

PANEL_URL = "https://autojev-panel.invalid/recording.html"
FRAME_ID = "autojev-recording-frame"
PANEL_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
)
ASSET = Path(__file__).with_name("browser_recording_panel.html")


class BrowserRecordingPanel:
    def __init__(self, executor, origin, snapshot, finish):
        self.executor, self.origin = executor, origin
        self.snapshot, self.finish = snapshot, finish
        self.frames, self.pages, self.blocked = {}, set(), set()
        self.closed = False
        self.finishing = False
        self.task = None
        self.lock = asyncio.Lock()
        self.stopped = asyncio.Event()

    async def start(self):
        await self.executor._context.expose_binding("__autojev_recording_panel", self.command)
        await self.executor._context.route("https://autojev-panel.invalid/**", self.route)
        await self.executor.show_live_panel()
        await self.refresh()
        self.task = asyncio.create_task(self._monitor())

    async def route(self, route, request):
        try:
            entry = self.frames.get(request.frame.page)
            owned = (not self.closed and request.url == PANEL_URL and request.method == "GET"
                     and request.is_navigation_request() and entry is not None and request.frame is entry[0])
        except Exception:
            owned = False
        if not owned:
            await route.abort("blockedbyclient")
        else:
            await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=ASSET.read_text(encoding="utf-8"),
                                headers={"Content-Security-Policy": PANEL_CSP, "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    async def command(self, source, command):
        try:
            entry = self.frames.get(source.get("page"))
            trusted = (not self.closed and not self.finishing and entry is not None
                       and source.get("frame") is entry[0] and entry[0].url == PANEL_URL
                       and not entry[0].is_detached() and isinstance(command, dict)
                       and command.get("op") == "finish" and command.get("confirm") is True
                       and secrets.compare_digest(str(command.get("nonce", "")), entry[2]))
        except Exception:
            trusted = False
        if not trusted:
            raise ValueError("Finalize a gravação pelo painel atual do AutoJev.")
        self.finishing = True
        try:
            await self.finish()
            return {"accepted": True}
        except Exception:
            self.finishing = False
            raise ValueError("Não foi possível finalizar. Tente pela aplicação AutoJev.") from None

    def _navigation(self, page, frame):
        if frame is page.main_frame:
            self.frames.pop(page, None)
            self.blocked.discard(page)
        elif (entry := self.frames.get(page)) and frame is entry[0]:
            # A frame reload revokes the previous document's command nonce.
            self.frames[page] = (entry[0], entry[1], secrets.token_urlsafe(32))

    async def refresh(self):
        async with self.lock:
            if self.closed:
                return
            snapshot = self.snapshot()
            for page in list(self.pages):
                if page.is_closed():
                    self.frames.pop(page, None)
                    self.blocked.discard(page)
                    self.pages.discard(page)
            for page in list(self.executor._context.pages):
                if self.closed:
                    break
                try:
                    location = urlsplit(page.url)
                    if page.is_closed() or (location.scheme, location.netloc) != self.origin:
                        continue
                    if page not in self.pages:
                        page.on("framenavigated", lambda frame, page=page: self._navigation(page, frame))
                        self.pages.add(page)
                    await page.evaluate(self.executor._SIDEPANEL_INSTALLER, self.executor._SIDEPANEL_CSS)
                    await page.evaluate("""data => {
                      const panel = document.getElementById('autojev-sidepanel');
                      if (!panel) return;
                      panel.setAttribute('aria-label', 'Gravação da navegação AutoJev');
                      panel.querySelector('#autojev-kicker-text').textContent = 'AUTOJEV · GRAVANDO';
                      panel.querySelector('#autojev-panel-title').textContent = `Gravação · ${data.steps.length} passos`;
                      panel.querySelector('#autojev-usage').hidden = true;
                      const log = panel.querySelector('#autojev-panel-log');
                      log.replaceChildren(...data.steps.slice(-6).map(step => { const li = document.createElement('li'); li.textContent = step; return li; }));
                    }""", snapshot)
                    if page in self.blocked:
                        continue
                    entry = self.frames.get(page)
                    if not entry or entry[0].is_detached() or not await entry[1].evaluate("el => el.isConnected"):
                        entry = await self._ensure_frame(page)
                    if entry:
                        await entry[0].evaluate("data => window.__autojevRecordingPanel?.update(data)", {**snapshot, "nonce": entry[2]})
                except Exception:
                    # Navigation/CSP/closed windows must not interrupt recording.
                    continue

    async def _ensure_frame(self, page):
        handle = await page.evaluate_handle("""() => {
          const panel = document.getElementById('autojev-sidepanel');
          const surface = panel?.querySelector('.autojev-panel-surface');
          if (!surface) return null;
          panel.querySelector('#autojev-recording-frame')?.remove();
          if (!panel.querySelector('#autojev-recording-style')) {
            const style = document.createElement('style'); style.id = 'autojev-recording-style';
            style.textContent = `#autojev-sidepanel[data-recording=true]{width:min(480px,calc(100vw - 28px))!important}
              #autojev-sidepanel[data-recording=true] .autojev-panel-surface{padding:0;overflow:hidden}
              #autojev-sidepanel[data-recording=true] .autojev-panel-surface>:not(iframe){display:none!important}
              #autojev-recording-frame{display:block;width:100%;height:min(520px,calc(100vh - 40px));border:0;background:#11191c}`;
            panel.append(style);
          }
          const frame = document.createElement('iframe'); frame.id = 'autojev-recording-frame';
          frame.title = 'Gravação da navegação AutoJev'; frame.setAttribute('sandbox', 'allow-scripts');
          frame.setAttribute('referrerpolicy', 'no-referrer'); surface.append(frame); return frame;
        }""")
        element = handle.as_element()
        if element is None:
            await handle.dispose()
            return None
        frame = await element.content_frame()
        if frame is None:
            await element.dispose()
            return None
        self.frames[page] = (frame, element, secrets.token_urlsafe(32))
        try:
            await frame.goto(PANEL_URL, wait_until="domcontentloaded", timeout=5000)
            await frame.evaluate("document.getElementById('autojev-sidepanel')?.remove()")
        except Exception:
            self.frames.pop(page, None)
            self.blocked.add(page)
            await element.evaluate("el => el.remove()")
            await page.evaluate("""() => {
              const panel = document.getElementById('autojev-sidepanel');
              delete panel.dataset.recording;
              panel.querySelector('#autojev-panel-hint').textContent = 'Finalize e revise pela aplicação AutoJev';
            }""")
            return None
        await element.evaluate("""el => {
          const panel = el.closest('#autojev-sidepanel'); panel.dataset.recording = 'true';
          const expanded = panel.dataset.collapsed !== 'true'; panel.dataset.pinned = String(expanded);
          panel.querySelector('.autojev-panel-rail').setAttribute('aria-expanded', String(expanded));
        }""")
        return self.frames.get(page)

    async def _monitor(self):
        while not self.closed:
            try:
                await asyncio.wait_for(self.stopped.wait(), timeout=.75)
            except asyncio.TimeoutError:
                await self.refresh()

    async def close(self):
        self.closed = True
        self.stopped.set()
        if self.task and self.task is not asyncio.current_task():
            # Let an in-flight Playwright call settle before closing its browser.
            # Cancelling it can leave an unobserved transport error at shutdown.
            await self.task
        self.frames.clear()
