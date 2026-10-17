"""Trusted, ephemeral rerun controls inside an untrusted tested page.

The parent page receives no upload token. Only the registered sandboxed frame
can use the binding; the asset is fulfilled locally, never fetched online.
"""
from __future__ import annotations

import asyncio
import copy
import json
import secrets
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit


PANEL_URL = "https://autojev-panel.invalid/rerun.html"
PANEL_HOST = "autojev-panel.invalid"
PANEL_FRAME_ID = "autojev-rerun-frame"
PANEL_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data: blob:; connect-src 'none'; frame-src 'none'; object-src 'none'; "
    "base-uri 'none'; form-action 'none'"
)
_ASSET = Path(__file__).with_name("browser_rerun_panel_assets") / "panel.html"


def is_panel_url(url: str) -> bool:
    try:
        return urlsplit(url).hostname == PANEL_HOST
    except (TypeError, ValueError):
        return False


class BrowserRerunPanel:
    def __init__(self, executor: Any, capture_handler: Callable[..., Awaitable[dict]]) -> None:
        self.executor = executor
        self.capture_handler = capture_handler
        self.resume_handler: Callable[..., Awaitable[dict]] | None = None
        self.frame: Any = None
        self._element: Any = None
        self._page: Any = None
        self._payload: dict[str, Any] = {}
        self._nonce = secrets.token_urlsafe(32)
        self._key: tuple = ()
        self._delivered: str | None = None
        self._busy = False
        self._used: set[tuple] = set()
        self._lock = asyncio.Lock()
        self._closed = False
        self._bound_pages: set[Any] = set()
        self._document_version = 0
        self._blocked_document: tuple | None = None

    async def bind_page(self, page: Any) -> None:
        if page in self._bound_pages:
            return
        await page.expose_binding("__autojev_rerun_command", self.command)
        self._bound_pages.add(page)
        page.on("framenavigated", lambda frame: self._on_navigation(page, frame))

    def _on_navigation(self, page: Any, frame: Any) -> None:
        if frame is page.main_frame:
            self._document_version += 1
        elif frame is self.frame:
            self._nonce = secrets.token_urlsafe(32)
            self._delivered = None

    @property
    def unavailable(self) -> bool:
        return self._blocked_document == (self.executor._page, self._document_version)

    async def route(self, route: Any, request: Any) -> bool:
        if not is_panel_url(request.url):
            return False
        try:
            owned = (not self._closed and request.url == PANEL_URL
                     and request.is_navigation_request() and request.method == "GET"
                     and request.frame is self.frame and self.frame is not None
                     and request.frame.page is self.executor._page)
        except Exception:
            owned = False
        if not owned:
            await route.abort("blockedbyclient")
        else:
            await route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                headers={"Content-Security-Policy": PANEL_CSP,
                                         "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
                                body=_ASSET.read_text(encoding="utf-8"))
        return True

    def close(self) -> None:
        self._closed = True
        self._nonce = secrets.token_urlsafe(32)
        self._payload = {}
        self.frame = self._page = self._element = None
        self._delivered = None

    async def ensure(self) -> bool:
        page = self.executor._page
        if self._closed or page is None or self.executor.headless:
            return False
        if self._blocked_document == (page, self._document_version):
            return False
        if self._page is page and self.frame is not None:
            try:
                if (not self.frame.is_detached() and self.frame.url == PANEL_URL
                        and await self._element.evaluate("el => el.isConnected")):
                    return True
            except Exception:
                pass
        # Revoke the previous document before creating the next one. Nothing
        # stored in the tested DOM is an authentication credential.
        self.frame = None
        self._nonce = secrets.token_urlsafe(32)
        self._delivered = None
        self._page = page
        handle = await page.evaluate_handle("""() => {
          const panel = document.getElementById('autojev-sidepanel');
          const surface = panel?.querySelector('.autojev-panel-surface');
          if (!surface) return null;
          surface.querySelector('#autojev-rerun-frame')?.remove();
          const frame = document.createElement('iframe');
          frame.id = 'autojev-rerun-frame';
          frame.title = 'Mapa e prints da reexecução AutoJev';
          frame.setAttribute('sandbox', 'allow-scripts');
          frame.setAttribute('referrerpolicy', 'no-referrer');
          surface.append(frame);
          return frame;
        }""")
        element = handle.as_element()
        if element is None:
            await handle.dispose()
            return False
        self._element = element
        self.frame = await element.content_frame()
        if self.frame is None:
            return False
        # Do not bypass the application's CSP: a site that forbids frames
        # retains the trusted main-app uploader instead of a weakened policy.
        try:
            await self.frame.goto(PANEL_URL, wait_until="domcontentloaded", timeout=5000)
        except Exception:
            self.frame = None
            self._blocked_document = (page, self._document_version)
            await element.evaluate("el => el.remove()")
            await page.evaluate("""() => {
              const panel = document.getElementById('autojev-sidepanel');
              if (!panel) return;
              delete panel.dataset.rerun;
              const text = panel.querySelector('#autojev-action-text');
              if (text) text.textContent = 'Este site restringe o painel integrado. '
                + 'Use a aplicação AutoJev para acompanhar o mapa e anexar seu print.';
            }""")
            return False
        await element.evaluate("""el => {
          const panel = el.closest('#autojev-sidepanel');
          panel.dataset.rerun = 'true';
          panel.dataset.pinned = 'true';
          panel.querySelector('.autojev-panel-rail')?.setAttribute('aria-expanded', 'true');
        }""")
        return True

    async def update(self, payload: dict[str, Any]) -> bool:
        async with self._lock:
            if self._closed:
                return False
            # Only internal service projections reach this method. Deep copy
            # prevents a concurrent phase transition mutating the command gate.
            self._payload = copy.deepcopy(payload)
            checkpoint = payload.get("checkpoint") or {}
            preparation = payload.get("preparation") or {}
            key = (payload.get("phase"), checkpoint.get("id"), checkpoint.get("version"),
                   preparation.get("version"))
            if key != self._key:
                self._key = key
                self._nonce = secrets.token_urlsafe(32)
            if not await self.ensure():
                return False
            data = {**self._payload, "nonce": self._nonce}
            serial = json.dumps(data, ensure_ascii=False, sort_keys=True)
            if serial != self._delivered:
                updated = await self.frame.evaluate("""data => {
                  if (!window.__autojevRerunPanel?.update) return false;
                  window.__autojevRerunPanel.update(data);
                  return true;
                }""", data)
                if not updated:
                    return False
                self._delivered = serial
            return True

    async def command(self, source: dict[str, Any], command: Any) -> dict[str, Any]:
        """Fail closed before parsing images or signalling either waiter."""
        try:
            trusted = (not self._closed and self.frame is not None
                       and source.get("page") is self.executor._page
                       and source.get("frame") is self.frame
                       and self.frame.url == PANEL_URL and not self.frame.is_detached()
                       and isinstance(command, dict)
                       and secrets.compare_digest(str(command.get("nonce", "")), self._nonce)
                       and command.get("confirm") is True
                       and self._payload.get("running") is True
                       and not self._busy)
        except Exception:
            trusted = False
        if not trusted:
            raise ValueError("Esta confirmação não está mais disponível. Confira a etapa atual.")
        phase = self._payload.get("phase")
        if command.get("op") == "capture":
            checkpoint = self._payload.get("checkpoint") or {}
            if (phase != "waiting_capture" or command.get("checkpoint_id") != checkpoint.get("id")
                    or type(command.get("checkpoint_version")) is not int
                    or command["checkpoint_version"] != checkpoint.get("version")):
                raise ValueError("Este print pertence a outra etapa. Confira a etapa atual.")
            key = ("capture", checkpoint.get("id"), checkpoint.get("version"))
            image = command.get("image_data_url")
            if not isinstance(image, str) or len(image) > 14_000_000:
                raise ValueError("Anexe uma imagem PNG, JPEG ou WebP de até 10 MB.")
        elif command.get("op") == "resume":
            preparation = self._payload.get("preparation") or {}
            if (phase != "waiting_preparation" or type(command.get("preparation_version")) is not int
                    or command["preparation_version"] != preparation.get("version")):
                raise ValueError("A preparação mudou. Confira a etapa atual.")
            key = ("resume", preparation.get("version"))
        else:
            raise ValueError("Ação não disponível neste painel.")
        if key in self._used:
            raise ValueError("A confirmação já foi recebida. Aguarde a atualização.")
        self._busy = True
        try:
            if command["op"] == "capture":
                try:
                    result = await self.capture_handler(checkpoint["id"], checkpoint["version"], image)
                except ValueError:
                    raise
                except Exception as exc:
                    raise ValueError("Não foi possível salvar o print. Tente novamente.") from exc
                if result.get("accepted") is not True:
                    raise ValueError("Não foi possível anexar o print. Tente novamente.")
            else:
                if self.resume_handler is not None:
                    result = await self.resume_handler(preparation["version"])
                    if result.get("accepted") is not True:
                        raise ValueError("Não foi possível continuar. Confira a preparação atual.")
                else:
                    self.executor.notify_user_input()
                    result = {"accepted": True}
            self._used.add(key)
            return result
        finally:
            self._busy = False
