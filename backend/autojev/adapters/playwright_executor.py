from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import json
import math
import re
import secrets
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlsplit, urlunsplit

from autojev.domain.models import (
    ActionResult,
    AgentDecision,
    AssertionResult,
    ExplorationOptions,
    Observation,
    utc_now,
)
from autojev.domain.product_identity import (
    declared_product_id, matching_product_indexes, product_ledger_key,
)
from autojev.adapters.browser_rerun_panel import BrowserRerunPanel, is_panel_url


class BrowserUnavailableError(RuntimeError):
    pass


class StaleObservedControlError(ValueError):
    """The observed node changed before any action could be dispatched."""


class _PinnedObservedElement:
    """Keep an action on the checked DOM node, not a reused CSS position."""

    def __init__(self, handle: Any) -> None:
        self._handle = handle

    def __getattr__(self, name: str) -> Any:
        return getattr(self._handle, name)

    async def element_handle(self) -> Any:
        return self._handle

    async def get_attribute(self, name: str, *, timeout: int | None = None) -> Any:
        return await self._handle.get_attribute(name)

    async def inner_text(self, *, timeout: int | None = None) -> str:
        return await self._handle.inner_text()

    async def is_visible(self, *, timeout: int | None = None) -> bool:
        return await self._handle.is_visible()


@dataclass(slots=True)
class LoginAttempt:
    """Non-sensitive authentication outcome; never contains credential values."""

    status: str
    reason: str
    observation: Observation | None = None


class PlaywrightExecutor:
    """The sole component permitted to perform browser actions."""

    # IDs de UI própria injetada no site. Nunca entram no texto ou nos
    # candidatos da observação entregues ao JEV.
    PANEL_SELECTOR = "#autojev-sidepanel,#autojev-intervention-panel,#autojev-live-panel"

    _LOGIN_CHALLENGE_SELECTOR = (
        'iframe[src*="captcha" i],iframe[src*="challenges.cloudflare" i],'
        'iframe[title*="challenge" i],iframe[title*="captcha" i],'
        '.g-recaptcha,.h-captcha,.cf-turnstile,[data-sitekey],'
        'input[autocomplete="one-time-code"],input[name*="otp" i],input[id*="otp" i],'
        'input[autocomplete="webauthn"]'
    )
    _LOGIN_CHALLENGE_TEXT = (
        "captcha|two.factor|multi.factor|\\b2fa\\b|\\bmfa\\b|authenticator|passkey|"
        "security key|sms code|verification code|one.time code|"
        "código de (verificação|segurança)|código (sms|de acesso)|autenticação de dois fatores"
    )
    # Shared across initial observation, post-submit confirmation and the
    # manual-resume poll. A dashboard mentioning "MFA settings" is not a gate.
    _LOGIN_CHALLENGE_JS = """
      const challengeCopy = Array.from(document.querySelectorAll('h1,h2,[role="dialog"],form,main p'))
        .filter(el=>visible(el) && !el.closest('nav,[role="navigation"]'))
        .map(el=>el.innerText || '').join(' ');
      const codeFields = Array.from(document.querySelectorAll('input')).filter(visible).some(el =>
        /^(otp|code|verification[-_]code|security[-_]code|auth[-_]code)$/i.test(el.name || el.id || '')
        || /verification code|security code|código de (verificação|segurança)/i.test(el.getAttribute('aria-label') || el.getAttribute('placeholder') || ''));
      const challengePrompt = /(?:enter|provide|type|verify|authenticate|use your|confirme|digite|informe|insira|verifique|autentique|use sua).{0,60}(?:code|2fa|mfa|passkey|security key|código|chave)|(?:two.factor|multi.factor|autenticação de dois fatores)/i.test(challengeCopy);
      const authenticationChallenge = Array.from(document.querySelectorAll(__LOGIN_CHALLENGE_SELECTOR__)).some(visible)
        || (new RegExp(__LOGIN_CHALLENGE_TEXT__,'i').test(challengeCopy) && (codeFields || challengePrompt));
    """

    def _login_challenge_script(self, script: str) -> str:
        return script.replace("__LOGIN_CHALLENGE_JS__", self._LOGIN_CHALLENGE_JS).replace(
            "__LOGIN_CHALLENGE_SELECTOR__", json.dumps(self._LOGIN_CHALLENGE_SELECTOR)
        ).replace("__LOGIN_CHALLENGE_TEXT__", json.dumps(self._LOGIN_CHALLENGE_TEXT))

    # CSS locators let Playwright mask manual credentials in the screenshot
    # compositor without adding styles, hiding controls or rewriting their DOM.
    # Include the same semantic cues used by sensitiveField in observations;
    # autocomplete and accessible labels also cover unlabeled login/OTP fields.
    _SENSITIVE_FIELD_SELECTOR = ":is(input,textarea,select):is(" + ",".join(
        f'[{attribute}*="{keyword}" i]'
        for attribute in (
            "id", "name", "type", "autocomplete", "data-test", "aria-label",
        )
        for keyword in (
            "password", "passwd", "senha", "token", "credential",
            "authorization", "authentication", "username", "user.name",
            "user-name", "user_name", "user name", "login", "session", "secret",
            "api-key", "api_key", "apikey", "api.key", "api key",
            "one-time", "one_time", "one.time", "one time", "otp",
        )
    ) + ")"

    def __init__(
        self,
        artifact_dir: Path,
        options: ExplorationOptions,
        *,
        headless: bool = True,
        browser_name: str = "chromium",
        secret_values: list[str] | None = None,
    ) -> None:
        self.artifact_dir = artifact_dir
        self.options = options
        self.headless = headless
        self.browser_name = browser_name
        self.secret_values = [value for value in (secret_values or []) if value]
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._console_buffer: deque[dict[str, Any]] = deque(maxlen=500)
        self._network_buffer: deque[dict[str, Any]] = deque(maxlen=500)
        self._pending_app_requests: dict[Any, float] = {}
        self._screenshot_number = 0
        self._allowed_origin: tuple[str, str, int] | None = None
        self._blocked_navigation_url: str | None = None
        self._ref_selectors: dict[str, str] = {}
        self._ref_semantics: dict[str, dict[str, Any]] = {}
        self._observed_node_guard_key = secrets.token_hex(16)
        self._observed_element_handles: list[Any] = []
        self._page_stack: list[Any] = []
        self._pending_pages: deque[Any] = deque(maxlen=20)
        self._user_input_event = asyncio.Event()
        self._intervention_message = ""
        self._protect_authenticated_history = False
        self._authenticated_history_root_url: str | None = None
        self._inactive_event = asyncio.Event()
        self._sidepanel_init_installed = False
        self._rerun_panel: BrowserRerunPanel | None = None
        self._automatic_login_attempted = False
        # Per-run opaque tokens compare field contents without storing PII.
        self._field_token_key = secrets.token_bytes(32)
        self._field_semantics: dict[str, dict[str, Any]] = {}
        self._last_page_state: dict[str, Any] = {}
        self._cart_expectation: dict[str, Any] = {
            "items": [], "items_known": False, "revision": 0,
            "source": "unobserved", "pending_membership": [],
        }
        self._document_response: dict[str, Any] | None = None

    def _observed_node_guard_script(self, script: str) -> str:
        """Internal node/owner identity; never add attributes or export it."""
        helper = """
          const observedNodeGuard = node => {
            const key = Symbol.for(__GUARD_KEY__);
            if (!globalThis[key]) globalThis[key] = {
              nodes: new WeakMap(), next: 0,
              document: Array.from(crypto.getRandomValues(new Uint32Array(4))).join('-'),
            };
            const store = globalThis[key];
            const token = element => {
              if (!element) return null;
              if (!store.nodes.has(element)) {
                store.nodes.set(element, `${store.document}:${++store.next}`);
              }
              return store.nodes.get(element);
            };
            const markers = element => element ?
              ['id','data-testid','data-test','data-qa','data-product-id','data-item-id']
                .filter(attr => element.hasAttribute(attr))
                .map(attr => [attr, element.getAttribute(attr)]) : [];
            const owner = node.parentElement?.closest(
              '[data-product-id],[data-item-id],article,tr,[role="row"],li,[role="listitem"],'
              + '.inventory_item,.cart_item,[data-testid],[data-test],[data-qa],form,[role="form"],dialog,[role="dialog"]'
            ) || node.parentElement;
            const entity = node.parentElement?.closest('[data-product-id],[data-item-id]');
            return {
              node: token(node), markers: markers(node),
              owner: token(owner), owner_markers: markers(owner),
              entity: token(entity), entity_markers: markers(entity),
            };
          };
        """.replace("__GUARD_KEY__", json.dumps(self._observed_node_guard_key))
        return script.replace("__OBSERVED_NODE_GUARD_JS__", helper)

    async def _release_observed_element_handles(self) -> None:
        handles, self._observed_element_handles = self._observed_element_handles, []
        for handle in handles:
            with suppress(Exception):
                await handle.dispose()

    def is_active(self) -> bool:
        """Whether there is still a usable browser page for this run."""

        if self._inactive_event.is_set() or self._page is None:
            return False
        try:
            if self._page.is_closed():
                return False
        except (AttributeError, TypeError):
            # Lightweight page doubles used by unit tests do not implement
            # Playwright's complete lifecycle API.
            pass
        if self._browser is not None:
            try:
                if not self._browser.is_connected():
                    return False
            except (AttributeError, TypeError):
                pass
        return True

    @property
    def inactive_event(self) -> asyncio.Event:
        """Event set as soon as the last page or browser becomes unavailable."""

        return self._inactive_event

    async def wait_until_inactive(self) -> None:
        """Wait until the browser disconnects or its final page is closed."""

        await self._inactive_event.wait()

    def _mark_inactive(self) -> None:
        self._inactive_event.set()
        self._ref_selectors.clear()
        self._ref_semantics.clear()

    def _on_browser_disconnected(self, _browser: Any = None) -> None:
        self._page = None
        self._page_stack = []
        self._pending_pages.clear()
        self._mark_inactive()

    async def _prepare_page(self, page: Any) -> None:
        """Attach AutoJev observation hooks to a newly adopted browser page."""

        try:
            await page.expose_binding(
                "__autojev_resume",
                lambda source: self._user_input_event.set(),
            )
        except Exception:
            # The binding may already exist after a same-document transition.
            pass
        if self._rerun_panel is not None:
            await self._rerun_panel.bind_page(page)
        page.on("console", lambda message: self._on_console(message, page=page))
        page.on("pageerror", lambda error: self._on_page_error(error, page=page))
        page.on("response", lambda response: self._on_response(response, page=page))
        page.on("request", self._on_request_started)
        page.on("requestfinished", self._on_request_settled)
        page.on("requestfailed", self._on_request_settled)
        page.on("requestfailed", lambda request: self._on_request_failed(request, page=page))
        page.on("close", lambda: self._on_page_close(page))

    def _on_context_page(self, page: Any) -> None:
        self._pending_pages.append(page)

    def _on_page_close(self, page: Any) -> None:
        self._page_stack = [item for item in self._page_stack if item is not page]
        if self._page is page:
            open_pages = [item for item in self._page_stack if not item.is_closed()]
            if not open_pages and self._context is not None:
                open_pages = [
                    item
                    for item in getattr(self._context, "pages", [])
                    if item is not page and not item.is_closed()
                ]
            self._page = open_pages[-1] if open_pages else None
            self._ref_selectors.clear()
            self._ref_semantics.clear()
        if self._page is None:
            self._mark_inactive()

    async def start(self, url: str) -> Observation:
        self._validate_url(url)
        self._allowed_origin = self._origin(url)
        self._inactive_event.clear()
        self._authenticated_history_root_url = None
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise BrowserUnavailableError(
                "Playwright is not installed. Run `pip install -r requirements.txt` and `playwright install chromium`."
            ) from exc
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._playwright = await async_playwright().start()
            channel = None
            browser_key = self.browser_name
            if self.browser_name in {"chrome", "msedge"}:
                browser_key = "chromium"
                channel = self.browser_name
            browser_type = getattr(self._playwright, browser_key, None)
            if browser_type is None:
                raise BrowserUnavailableError(f"Unsupported browser: {self.browser_name}")
            launch_options = {"headless": self.headless}
            viewport_width = max(int(self.options.viewport_width or 1440), 1)
            viewport_height = max(int(self.options.viewport_height or 900), 1)
            if self.headless:
                context_options: dict[str, Any] = {
                    "ignore_https_errors": False,
                    "viewport": {"width": viewport_width, "height": viewport_height},
                }
            else:
                # Modo com interface (login manual): viewport LIVRE.
                # Com viewport fixo (ex. 1440x900), maximizar a janela maior
                # que isso deixa faixas brancas — a página fica "reduzida" no
                # canto. Com no_viewport=True o layout acompanha qualquer
                # tamanho de janela/redimensionamento/maximizar.
                # O --window-size só dá o tamanho inicial (~viewport pedido).
                launch_options["args"] = [
                    f"--window-size={viewport_width + 16},{viewport_height + 145}",
                ]
                context_options = {
                    "ignore_https_errors": False,
                    "no_viewport": True,
                }
            if channel:
                launch_options["channel"] = channel
            self._browser = await browser_type.launch(**launch_options)
            self._browser.on("disconnected", self._on_browser_disconnected)
            self._context = await self._browser.new_context(**context_options)
            await self._context.route("**/*", self._guard_navigation)
            self._page = await self._context.new_page()
            self._context.on("page", self._on_context_page)
            self._page_stack = [self._page]
            await self._prepare_page(self._page)
            response = await self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            try:
                await self._page.wait_for_load_state("load", timeout=5_000)
            except Exception:
                pass
            # Give hydrated SPAs one short frame budget to attach click
            # handlers before the first observed refs can be executed.
            await self._page.wait_for_timeout(250)
            initial = await self.observe("initial")
            if response is not None:
                initial.page_state["navigation_response"] = self._navigation_metadata(response)
            return initial
        except BrowserUnavailableError:
            await self.close()
            raise
        except Exception as exc:
            await self.close()
            raise BrowserUnavailableError(f"Browser could not start or navigate: {exc}") from exc

    _SIDEPANEL_CSS = (
        "#autojev-sidepanel{all:initial;position:fixed !important;z-index:2147483647 !important;"
        "top:18px !important;right:18px !important;width:min(356px,calc(100vw - 28px)) !important;"
        "max-height:calc(100vh - 36px) !important;margin:0 !important;display:grid !important;"
        "grid-template-columns:54px minmax(0,1fr) !important;box-sizing:border-box !important;"
        "color:#f7faf9 !important;font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif !important;"
        "transform:translate3d(calc(100% - 54px),0,0);opacity:.42;filter:saturate(.82);"
        "transition:none !important;pointer-events:none !important;contain:layout paint style;}"
        "#autojev-sidepanel,#autojev-sidepanel *{box-sizing:border-box !important;}"
        "#autojev-sidepanel[data-pinned='true'],#autojev-sidepanel[data-mode='attention']{"
        "transform:translate3d(0,0,0);opacity:1;filter:saturate(1);}"
        "#autojev-sidepanel .autojev-panel-rail{position:relative;display:grid;width:54px;height:54px;"
        "min-height:54px;place-items:center;padding:7px;border:1px solid rgba(255,255,255,.16);"
        "border-radius:16px 0 0 16px;background:rgba(10,16,18,.82);pointer-events:auto !important;"
        "box-shadow:inset 0 1px rgba(255,255,255,.04);backdrop-filter:blur(18px) saturate(1.25);"
        "-webkit-backdrop-filter:blur(18px) saturate(1.25);cursor:pointer;}"
        "#autojev-sidepanel .autojev-panel-rail:hover{background:rgba(10,16,18,.96);filter:saturate(1.25);}"
        "#autojev-sidepanel .autojev-rail-mark{display:grid;width:38px;height:38px;place-items:center;"
        "border:1px solid rgba(242,184,75,.5);border-radius:12px;background:linear-gradient(145deg,#2b2415,#17140e);"
        "box-shadow:0 0 26px rgba(242,184,75,.18);color:#f6c85c;font-size:15px;font-weight:850;}"
        "#autojev-sidepanel .autojev-rail-status{position:absolute;right:5px;bottom:5px;width:8px;height:8px;border-radius:50%;"
        "background:#42e5b5;box-shadow:0 0 0 5px rgba(66,229,181,.1),0 0 18px rgba(66,229,181,.65);}"
        "#autojev-sidepanel .autojev-rail-label{display:none;}"
        "#autojev-sidepanel .autojev-panel-surface{min-width:0;max-height:calc(100vh - 36px);"
        "overflow:hidden auto;padding:17px;border:1px solid rgba(255,255,255,.13);border-radius:0 18px 18px 0;"
        "background:linear-gradient(155deg,rgba(18,27,29,.96),rgba(8,13,15,.94));"
        "box-shadow:inset 0 1px rgba(255,255,255,.04);"
        "backdrop-filter:blur(22px) saturate(1.35);-webkit-backdrop-filter:blur(22px) saturate(1.35);"
        "scrollbar-width:thin;scrollbar-color:#3e4b4c transparent;opacity:0;pointer-events:none !important;transition:none !important;}"
        "#autojev-sidepanel[data-pinned='true'] .autojev-panel-surface,"
        "#autojev-sidepanel[data-mode='attention'] .autojev-panel-surface{opacity:1;pointer-events:auto !important;}"
        "#autojev-sidepanel .autojev-panel-header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;}"
        "#autojev-sidepanel .autojev-panel-kicker{display:flex;align-items:center;gap:7px;margin:0;color:#f4c65a;"
        "font-size:9px;font-weight:800;letter-spacing:.13em;text-transform:uppercase;}"
        "#autojev-sidepanel .autojev-live-dot{display:inline-block;width:7px;height:7px;flex:0 0 auto;"
        "border-radius:50%;background:#42e5b5;box-shadow:0 0 12px rgba(66,229,181,.75);}"
        "#autojev-sidepanel .autojev-panel-hint{color:#667473;font-size:8px;line-height:1.35;text-align:right;}"
        "#autojev-sidepanel h2{margin:12px 0 0 !important;color:#fff !important;font-size:18px !important;"
        "font-weight:720 !important;line-height:1.2 !important;letter-spacing:-.025em !important;}"
        "#autojev-sidepanel .autojev-usage{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin:14px 0 0;}"
        "#autojev-sidepanel .autojev-usage-chip{display:flex;min-width:0;flex-direction:column;padding:9px 10px;"
        "border:1px solid rgba(255,255,255,.08);border-radius:10px;background:rgba(255,255,255,.035);}"
        "#autojev-sidepanel .autojev-usage-chip small{color:#748381;font-size:7px;font-weight:750;"
        "letter-spacing:.1em;text-transform:uppercase;}"
        "#autojev-sidepanel .autojev-usage-chip strong{overflow:hidden;margin-top:3px;color:#eef4f2;"
        "font:650 10px ui-monospace,SFMono-Regular,Menlo,monospace;text-overflow:ellipsis;white-space:nowrap;}"
        "#autojev-sidepanel #autojev-panel-action{margin-top:14px;padding:12px;border:1px solid rgba(242,184,75,.38);"
        "border-radius:12px;background:linear-gradient(145deg,rgba(242,184,75,.13),rgba(242,184,75,.055));"
        "box-shadow:0 12px 30px rgba(0,0,0,.16);}"
        "#autojev-sidepanel #autojev-panel-action[hidden]{display:none !important;}"
        "#autojev-sidepanel p{margin:0 !important;color:#c2cecb !important;font-size:11px !important;line-height:1.55 !important;}"
        "#autojev-sidepanel button{all:unset;display:flex !important;width:100% !important;min-height:42px;"
        "align-items:center !important;justify-content:center !important;margin-top:12px !important;padding:0 12px !important;"
        "border-radius:10px !important;background:linear-gradient(135deg,#ffd66e,#f0b33d) !important;"
        "box-shadow:0 8px 24px rgba(242,184,75,.2) !important;color:#19140a !important;"
        "font:800 11px Inter,ui-sans-serif,system-ui,sans-serif !important;cursor:pointer !important;}"
        "#autojev-sidepanel button:hover{filter:brightness(1.06);transform:translateY(-1px);}"
        "#autojev-sidepanel button:focus-visible{outline:2px solid #fff !important;outline-offset:2px !important;}"
        "#autojev-sidepanel button:disabled{opacity:.68;cursor:default !important;transform:none;}"
        "#autojev-sidepanel .autojev-log-heading{display:flex;align-items:center;justify-content:space-between;"
        "margin-top:15px;padding-top:12px;border-top:1px solid rgba(255,255,255,.08);color:#71807e;"
        "font-size:8px;font-weight:750;letter-spacing:.1em;text-transform:uppercase;}"
        "#autojev-sidepanel ul{list-style:none !important;max-height:210px;overflow:hidden auto;margin:8px 0 0 !important;padding:0 !important;}"
        "#autojev-sidepanel li{position:relative;margin:0 0 6px !important;padding:8px 9px 8px 11px !important;"
        "border:1px solid rgba(255,255,255,.065);border-radius:9px;background:rgba(255,255,255,.035);"
        "color:#aebbb8 !important;font-size:9px !important;line-height:1.45 !important;word-break:break-word;}"
        "#autojev-sidepanel li:before{position:absolute;top:9px;bottom:9px;left:0;width:2px;border-radius:2px;"
        "background:#f0b84b;content:'';}"
        "#autojev-sidepanel[data-mode='attention'] .autojev-panel-rail{border-color:rgba(242,184,75,.5);"
        "background:rgba(30,24,12,.92);box-shadow:inset 0 1px rgba(255,255,255,.04);}"
        "#autojev-sidepanel[data-mode='attention'] .autojev-rail-status{background:#f6c655;"
        "box-shadow:0 0 0 5px rgba(246,198,85,.12),0 0 20px rgba(246,198,85,.8);}"
        "#autojev-sidepanel[data-automation='true'] .autojev-panel-rail,"
        "#autojev-sidepanel[data-automation='true'] .autojev-panel-surface,"
        "#autojev-sidepanel[data-automation='true'] .autojev-panel-surface *{pointer-events:none !important;}"
        "#autojev-sidepanel[data-rerun='true']{width:min(526px,calc(100vw - 28px)) !important;}"
        "#autojev-sidepanel[data-rerun='true'] .autojev-panel-surface{padding:0;overflow:hidden;}"
        "#autojev-sidepanel[data-rerun='true'] .autojev-panel-surface > :not(iframe){display:none !important;}"
        "#autojev-rerun-frame{display:none;width:100%;border:0;background:#10191d;}"
        "#autojev-sidepanel[data-rerun='true'] #autojev-rerun-frame{display:block;"
        "height:min(760px,calc(100dvh - 36px));min-height:0;color-scheme:dark;}"
        "@media(max-width:620px){#autojev-sidepanel{top:10px !important;right:10px !important;"
        "width:calc(100vw - 20px) !important;max-height:calc(100vh - 20px) !important;"
        "grid-template-columns:52px minmax(0,1fr) !important;transform:translate3d(calc(100% - 52px),0,0);}"
        "#autojev-sidepanel .autojev-panel-surface{max-height:calc(100vh - 20px);padding:14px;}"
        "#autojev-sidepanel .autojev-panel-rail{width:52px;height:52px;min-height:52px;padding:7px;}}"
        "@media(prefers-reduced-motion:reduce){#autojev-sidepanel{transition:none !important;}}"
    )

    _SIDEPANEL_INSTALLER = r"""(css) => {
      if (window.top !== window) return;
      const ID = 'autojev-sidepanel';
      const current = document.getElementById(ID);
      if (current) {
        const currentStyle = current.querySelector('#autojev-panel-style');
        if (currentStyle && currentStyle.textContent !== css) currentStyle.textContent = css;
        return;
      }

      const panel = document.createElement('aside');
      panel.id = ID;
      panel.dataset.mode = 'exploring';
      const PIN_KEY = 'autojev-panel-open';
      const USAGE_KEY = 'autojev-panel-usage';
      let savedPinned = false;
      try { savedPinned = sessionStorage.getItem(PIN_KEY) === 'true'; } catch (error) {}
      panel.dataset.pinned = savedPinned ? 'true' : 'false';
      panel.setAttribute('aria-label', 'Status da exploração do AutoJev');
      panel.setAttribute('aria-live', 'polite');

      const rail = document.createElement('div');
      rail.className = 'autojev-panel-rail';
      rail.setAttribute('role', 'button');
      rail.setAttribute('tabindex', '0');
      rail.setAttribute('aria-label', 'Expandir ou recolher painel do AutoJev');
      rail.setAttribute('aria-expanded', savedPinned ? 'true' : 'false');
      rail.innerHTML = '<span class="autojev-rail-mark">J</span><span class="autojev-rail-status"></span><span class="autojev-rail-label">AutoJev</span>';
      const togglePinned = () => {
        const next = panel.dataset.pinned !== 'true';
        panel.dataset.pinned = next ? 'true' : 'false';
        rail.setAttribute('aria-expanded', next ? 'true' : 'false');
        const hint = document.getElementById('autojev-panel-hint');
        if (hint && panel.dataset.mode !== 'attention') hint.textContent = next ? 'Clique no J para recolher' : 'Clique no J para abrir';
        try { sessionStorage.setItem(PIN_KEY, next ? 'true' : 'false'); } catch (error) {}
      };
      rail.addEventListener('click', togglePinned);
      rail.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          togglePinned();
        }
      });
      panel.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
          panel.dataset.pinned = 'false';
          rail.setAttribute('aria-expanded', 'false');
          const hint = document.getElementById('autojev-panel-hint');
          if (hint) hint.textContent = 'Clique no J para abrir';
          try { sessionStorage.setItem(PIN_KEY, 'false'); } catch (error) {}
          rail.focus();
        }
      });

      const surface = document.createElement('section');
      surface.className = 'autojev-panel-surface';
      const header = document.createElement('header');
      header.className = 'autojev-panel-header';
      const kicker = document.createElement('div');
      kicker.className = 'autojev-panel-kicker';
      kicker.innerHTML = '<span class="autojev-live-dot"></span><span id="autojev-kicker-text">AUTOJEV · EXPLORANDO</span>';
      const hint = document.createElement('span');
      hint.id = 'autojev-panel-hint';
      hint.className = 'autojev-panel-hint';
      hint.textContent = savedPinned ? 'Clique no J para recolher' : 'Clique no J para abrir';
      header.append(kicker, hint);

      const title = document.createElement('h2');
      title.id = 'autojev-panel-title';
      title.textContent = 'Exploração em andamento';

      const usage = document.createElement('div');
      usage.id = 'autojev-usage';
      usage.className = 'autojev-usage';
      usage.innerHTML = '<span class="autojev-usage-chip"><small>Tokens</small><strong id="autojev-token-value">0</strong></span><span class="autojev-usage-chip"><small>Custo</small><strong id="autojev-cost-value">—</strong></span>';
      try {
        const savedUsage = JSON.parse(sessionStorage.getItem(USAGE_KEY) || 'null');
        if (savedUsage) {
          usage.querySelector('#autojev-token-value').textContent = savedUsage.tokens || '0';
          usage.querySelector('#autojev-cost-value').textContent = savedUsage.cost || '—';
        }
      } catch (error) {}

      const action = document.createElement('div');
      action.id = 'autojev-panel-action';
      action.hidden = true;
      const actionText = document.createElement('p');
      actionText.id = 'autojev-action-text';
      const button = document.createElement('button');
      button.id = 'autojev-action-button';
      button.type = 'button';
      button.textContent = 'Continuar exploração';
      button.addEventListener('click', () => {
        if (button.disabled) return;
        try {
          const fn = window.__autojev_resume;
          if (typeof fn === 'function') fn();
        } catch (error) {}
        button.disabled = true;
        button.textContent = 'Retomando…';
      });
      action.append(actionText, button);

      const logHeading = document.createElement('div');
      logHeading.className = 'autojev-log-heading';
      logHeading.innerHTML = '<span>Atividade</span><span>ao vivo</span>';
      const log = document.createElement('ul');
      log.id = 'autojev-panel-log';

      const style = document.createElement('style');
      style.id = 'autojev-panel-style';
      style.textContent = css;
      surface.append(header, title, usage, action, logHeading, log);
      panel.append(rail, surface, style);

      const mount = () => {
        if (!document.body) return false;
        if (!document.getElementById(ID)) document.body.append(panel);
        return true;
      };
      if (!mount()) document.addEventListener('DOMContentLoaded', mount, { once: true });
    }"""

    async def _install_sidepanel_bootstrap(self) -> None:
        """Instala a UI antes de futuras navegações para não haver sumiço/pisca."""

        if self.headless or not self._context or self._sidepanel_init_installed:
            return
        script = f"({self._SIDEPANEL_INSTALLER})({json.dumps(self._SIDEPANEL_CSS)});"
        try:
            await self._context.add_init_script(script=script)
            self._sidepanel_init_installed = True
        except Exception:
            # A injeção imediata abaixo ainda mantém o teste funcional.
            pass

    async def _ensure_sidepanel(self) -> bool:
        """Cria o painel único (status + ação). Retorna False se impossível."""
        if not self._page or self.headless:
            return False
        await self._install_sidepanel_bootstrap()
        try:
            await self._page.evaluate(
                self._SIDEPANEL_INSTALLER,
                self._SIDEPANEL_CSS,
            )
            return True
        except Exception:
            return False

    async def show_live_panel(self) -> None:
        await self._ensure_sidepanel()

    def configure_rerun_panel(self, capture_handler: Any) -> None:
        """Enable the isolated map/upload UI for this one rerun only."""
        if self._rerun_panel is not None:
            self._rerun_panel.close()
        self._rerun_panel = BrowserRerunPanel(self, capture_handler)

    def configure_rerun_resume(self, resume_handler: Any) -> None:
        if self._rerun_panel is not None:
            self._rerun_panel.resume_handler = resume_handler

    async def update_rerun_panel(self, payload: dict[str, Any]) -> bool:
        if self._rerun_panel is None or not await self._ensure_sidepanel():
            return False
        try:
            return await self._rerun_panel.update(payload)
        except Exception:
            # Some sites forbid embedded frames. Keep their CSP intact and
            # retain the main AutoJev application as a confirmation fallback.
            return False

    async def _set_sidepanel_automation(self, active: bool) -> None:
        """Mantém o overlay fora do hit testing enquanto o Playwright atua."""

        if not self._page or self.headless:
            return
        try:
            await self._page.evaluate(
                """(active) => {
                  const panel = document.getElementById('autojev-sidepanel');
                  if (!panel) return;
                  panel.dataset.automation = active ? 'true' : 'false';
                  const hitTargets = [
                    panel.querySelector('.autojev-panel-rail'),
                    panel.querySelector('.autojev-panel-surface'),
                  ].filter(Boolean);
                  for (const target of hitTargets) {
                    if (active) {
                      target.style.setProperty('pointer-events', 'none', 'important');
                    } else {
                      target.style.removeProperty('pointer-events');
                    }
                  }
                }""",
                active,
            )
        except Exception:
            pass

    async def update_live_panel(self, title: str, line: str) -> None:
        """Atualiza título e acrescenta uma linha ao histórico compacto."""
        if not self._page or self.headless:
            return
        if not await self._ensure_sidepanel():
            return
        try:
            await self._page.evaluate(
                """([title, line]) => {
                  const panel = document.getElementById('autojev-sidepanel');
                  if (!panel) return;
                  const titleEl = document.getElementById('autojev-panel-title');
                  if (titleEl && title) titleEl.textContent = title;
                  if (line) {
                    const log = document.getElementById('autojev-panel-log');
                    if (log) {
                      const li = document.createElement('li');
                      li.textContent = line;
                      log.prepend(li);
                      while (log.children.length > 6) log.lastChild.remove();
                    }
                  }
                }""",
                [str(title or "")[:200], str(line or "")[:500]],
            )
        except Exception:
            pass

    async def update_usage_panel(
        self, total_tokens: int, cost: float | None, cost_known: bool
    ) -> None:
        if not self._page or self.headless:
            return
        try:
            await self._page.evaluate(
                """([tokens, costText]) => {
                  const panel = document.getElementById('autojev-sidepanel');
                  const previous = Number(panel?.dataset.usageTokens || '0');
                  // JEV and the auxiliary LLM account concurrently. A slower
                  // DOM update carrying an older snapshot must not make the
                  // live counter move backwards.
                  if (Number(tokens) < previous) return;
                  if (panel) panel.dataset.usageTokens = String(tokens);
                  const tokenValue = document.getElementById('autojev-token-value');
                  const costValue = document.getElementById('autojev-cost-value');
                  const tokenText = tokens.toLocaleString('pt-BR');
                  if (tokenValue) tokenValue.textContent = tokenText;
                  if (costValue) costValue.textContent = costText;
                  try {
                    sessionStorage.setItem(
                      'autojev-panel-usage',
                      JSON.stringify({ tokens: tokenText, cost: costText })
                    );
                  } catch (error) {}
                }""",
                [total_tokens, f"${cost:.6f}" if cost_known and cost is not None else "—"],
            )
        except Exception:
            pass

    async def hide_live_panel(self) -> None:
        if not self._page:
            return
        try:
            await self._page.evaluate(
                "document.getElementById('autojev-sidepanel')?.remove()"
            )
        except Exception:
            pass

    async def show_intervention_panel(self, message: str, *, title: str = "Faça login para continuar",
                                      button_label: str = "Continuar exploração", button_disabled: bool = False) -> None:
        """Mostra a seção de ação (botão Continuar) mantendo o log visível."""
        self._intervention_message = message
        if not self._page or self.headless:
            return
        if not await self._ensure_sidepanel():
            return
        try:
            await self._page.evaluate(
                """([message, panelTitle, buttonLabel, buttonDisabled]) => {
                  const panel = document.getElementById('autojev-sidepanel');
                  const action = document.getElementById('autojev-panel-action');
                  const text = document.getElementById('autojev-action-text');
                  const button = document.getElementById('autojev-action-button');
                  const kicker = document.getElementById('autojev-kicker-text');
                  const hint = document.getElementById('autojev-panel-hint');
                  if (text) text.textContent = message;
                  if (action) action.hidden = false;
                  if (button) {
                    button.disabled = buttonDisabled; button.textContent = buttonLabel;
                  }
                  if (kicker) kicker.textContent = 'AUTOJEV · AÇÃO NECESSÁRIA';
                  if (hint) hint.textContent = 'Aguardando você';
                  if (panel) {
                    panel.dataset.mode = 'attention';
                    panel.setAttribute('aria-live', 'assertive');
                  }
                  const title = document.getElementById('autojev-panel-title');
                  if (title) title.textContent = panelTitle;
                }""",
                [message, title, button_label, button_disabled],
            )
        except Exception:
            pass

    async def hide_intervention_panel(self) -> None:
        """Esconde só a seção de ação; o log de status continua visível."""
        if not self._page:
            return
        try:
            await self._page.evaluate(
                """() => {
                  const panel = document.getElementById('autojev-sidepanel');
                  const action = document.getElementById('autojev-panel-action');
                  if (action) action.hidden = true;
                  const kicker = document.getElementById('autojev-kicker-text');
                  if (kicker) kicker.textContent = 'AUTOJEV · EXPLORANDO';
                  const hint = document.getElementById('autojev-panel-hint');
                  let savedPinned = false;
                  try { savedPinned = sessionStorage.getItem('autojev-panel-open') === 'true'; } catch (error) {}
                  if (panel?.dataset.rerun === 'true') savedPinned = panel.dataset.pinned === 'true';
                  if (hint) hint.textContent = savedPinned ? 'Clique no J para recolher' : 'Clique no J para abrir';
                  if (panel) {
                    panel.dataset.mode = 'exploring';
                    panel.dataset.pinned = savedPinned ? 'true' : 'false';
                    panel.setAttribute('aria-live', 'polite');
                  }
                  const rail = panel?.querySelector('.autojev-panel-rail');
                  if (rail) rail.setAttribute('aria-expanded', savedPinned ? 'true' : 'false');
                  const button = document.getElementById('autojev-action-button');
                  if (button) {
                    button.disabled = false; button.textContent = 'Continuar exploração';
                  }
                }"""
            )
        except Exception:
            pass

    def notify_user_input(self) -> None:
        self._user_input_event.set()

    async def wait_for_capture_confirmation(self, message: str) -> None:
        """Restore guidance; uploads use the isolated frame or trusted app.

        The generic parent-page resume binding can never consume a print.
        This waiter is cancelled only after the service accepts an image.
        """
        while True:
            guidance = ("Este site restringe o painel integrado. Volte à aplicação AutoJev "
                        "para acompanhar o mapa, colar ou escolher seu print e confirmar o envio."
                        if self._rerun_panel is not None and self._rerun_panel.unavailable else message)
            await self.show_intervention_panel(guidance, title="Aguardando seu print",
                button_label="Anexe na aplicação AutoJev", button_disabled=True)
            # Restore passive guidance after a full navigation, even when the
            # bootstrap has already recreated a hidden default panel.
            await asyncio.sleep(1.0)

    def protect_authenticated_history(self) -> None:
        """Keep coverage backtracking inside the authenticated application."""

        self._protect_authenticated_history = True
        self._authenticated_history_root_url = (
            self._history_url_identity(self._page.url) if self._page else None
        )

    @staticmethod
    def _history_url_identity(value: str) -> str:
        """Canonical URL identity used only for the authenticated history floor."""

        parsed = urlsplit(value)
        query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
        return urlunsplit(
            (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                parsed.path or "/",
                query,
                parsed.fragment,
            )
        )

    async def is_login_page(self) -> bool:
        """Checagem leve (sem screenshot) para detectar tela de login."""
        if not self._page:
            return False
        try:
            return bool(
                await self._page.evaluate(
                    self._login_challenge_script("""() => {
                      const panel = '#autojev-sidepanel,#autojev-intervention-panel,#autojev-live-panel';
                      const visible = field => {
                        if (field.closest(panel)) return false;
                        const box = field.getBoundingClientRect(), style = getComputedStyle(field);
                        return style.display !== 'none' && style.visibility !== 'hidden' && box.width > 0 && box.height > 0;
                      };
                      const fields = document.querySelectorAll(
                        'input[type="password"],input[name="password" i],input[name="passwd" i],input[name="senha" i],input[id="password" i],input[id="senha" i]'
                      );
                      __LOGIN_CHALLENGE_JS__
                      return Array.from(fields).some(visible) || authenticationChallenge;
                    }""")
                )
            )
        except Exception:
            return True

    async def attempt_login(
        self, credentials: dict[str, str], observation: Observation,
    ) -> LoginAttempt:
        """Attempt one unambiguous same-origin login, without involving an LLM.

        This narrowly scoped path is separate from exploratory fill/click:
        authentication is explicitly authorized by the supplied credentials,
        while the general destructive-interaction policy remains unchanged.
        Captchas, additional factors, changed nodes and ambiguous forms fail
        closed to manual intervention. Even a rejected login is never retried.
        """
        if self._automatic_login_attempted:
            return LoginAttempt("manual_required", "already_attempted")
        self._automatic_login_attempted = True
        return await self._attempt_guarded_login(credentials, observation)

    async def attempt_filled_login(self, observation: Observation | None = None) -> LoginAttempt:
        """Submit an already-filled login only after an explicit Continue click.

        This is not automatic exploration: the caller must obtain a new user
        confirmation for each call. It never fills fields, guesses credentials,
        presses Enter, or consumes the separate automatic-login attempt.
        """
        if not self.is_active():
            raise BrowserUnavailableError("Browser page is not active")
        if self._origin(self._page.url) != self._allowed_origin:
            return LoginAttempt("manual_required", "unsafe_origin")
        # Mask even anonymous/email username controls before the refresh can
        # capture a bitmap. This reads no values and changes no input contents.
        await self._page.evaluate("""panel => {
          const visible = el => {
            if (el.closest(panel)) return false;
            const b=el.getBoundingClientRect(),s=getComputedStyle(el);
            return b.width>0 && b.height>0 && s.visibility!=='hidden' && s.display!=='none';
          };
          for (const password of Array.from(document.querySelectorAll('input[type="password"]')).filter(visible)) {
            password.setAttribute('data-autojev-secret-mask','true');
            const scope=password.form || password.closest('[role="form"]');
            if (!scope) continue;
            for (const field of Array.from(document.querySelectorAll('input')).filter(el=>visible(el)
              && (el.form || el.closest('[role="form"]'))===scope && ['text','email','tel'].includes(el.type))) {
              field.setAttribute('data-autojev-secret-mask','true');
            }
          }
        }""", self.PANEL_SELECTOR)
        # Login forms can rerender while the person types. Refresh node guards
        # before resolving the single, visible submit; never reuse old refs.
        observation = await self.observe("before-user-login")
        return await self._attempt_guarded_login(None, observation)

    async def _attempt_guarded_login(
        self, credentials: dict[str, str] | None, observation: Observation,
    ) -> LoginAttempt:
        filled_login = credentials is None
        username, password = "", ""
        if credentials is not None:
            username, password = credentials.get("username"), credentials.get("password")
            if not username or not password:
                return LoginAttempt("manual_required", "incomplete_credentials")
        if not self.is_active():
            raise BrowserUnavailableError("Browser page is not active")
        if not filled_login:
            for value in (username, password):
                if value not in self.secret_values:
                    self.secret_values.append(value)
        # A caller may provide credentials after the first observation. Keep
        # stale-ref comparison on the same redacted representation in that case.
        for semantics in self._ref_semantics.values():
            semantics["name"] = self._redact_text(str(semantics.get("name") or ""))
            if semantics.get("href"):
                semantics["href"] = self._public_url(semantics["href"])
        page = self._page
        handles: list[Any] = []
        request_guard_installed = False
        blocked_request = False
        submit_started = False
        self._blocked_navigation_url = None

        async def guard_authentication_request(route: Any, request: Any) -> None:
            nonlocal blocked_request
            # Match complete structured values, not short substrings such as a
            # username "a" in an asset path. Ordinary CDN/telemetry traffic is
            # not an authentication failure and keeps the existing policy.
            decoded_url = unquote(request.url)
            parsed_url = urlsplit(request.url)
            query_pairs = parse_qsl(parsed_url.query)
            secret_in_url = any(
                any(value == content and (len(value) >= 8 or re.search(r"user|email|login|pass|senha|credential|token", key, re.I)) for key, content in query_pairs)
                or value in [unquote(segment) for segment in parsed_url.path.split("/") if segment]
                or (len(value) >= 8 and value in decoded_url)
                for value in (username, password)
            )
            body = request.post_data or ""
            body_values = [value for _, value in parse_qsl(body)]
            try:
                def string_values(value: Any) -> list[str]:
                    if isinstance(value, str):
                        return [value]
                    if isinstance(value, dict):
                        return [s for child in value.values() for s in string_values(child)]
                    if isinstance(value, list):
                        return [s for child in value for s in string_values(child)]
                    return []
                body_values.extend(string_values(json.loads(body)))
            except (ValueError, TypeError):
                pass
            secret_in_body = any(
                value in body_values or (len(value) >= 8 and value in body)
                or bool(body and re.search(rf"(?<!\w){re.escape(value)}(?!\w)", body))
                for value in (username, password)
            )
            # Authorization headers and opaque body formats cannot bypass the
            # short-secret checks (e.g. multipart/plain-text password "1234").
            authorization = request.headers.get("authorization", "")
            if (
                secret_in_url
                or ((secret_in_body or authorization) and self._origin(request.url) != self._allowed_origin)
            ):
                blocked_request = True
                await route.abort("blockedbyclient")
                return
            await route.fallback()

        try:
            if self._origin(page.url) != self._allowed_origin:
                return LoginAttempt("manual_required", "unsafe_origin")
            plan = await page.evaluate(
                self._login_challenge_script("""({selectors, panel}) => {
                  const visible = el => {
                    if (!el || el.closest(panel)) return false;
                    const b = el.getBoundingClientRect(), s = getComputedStyle(el);
                    return b.width > 0 && b.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                  };
                  __LOGIN_CHALLENGE_JS__
                  if (authenticationChallenge) return {reason:'authentication_challenge'};
                  const passwords = Array.from(document.querySelectorAll('input[type="password"]')).filter(visible);
                  if (passwords.length !== 1) return {reason:'ambiguous_form'};
                  const password = passwords[0], scope = password.form || password.closest('[role="form"]');
                  if (!scope || scope.closest(panel)) return {reason:'ambiguous_form'};
                  const fields = Array.from(document.querySelectorAll('input')).filter(el => visible(el) && (el.form || el.closest('[role="form"]')) === scope);
                  const usernames = fields.filter(el => ['text','email','tel'].includes(el.type));
                  // Registration, password changes and extra required fields
                  // are not guessed from position or handed credentials.
                  if (usernames.length !== 1 || fields.some(el => !['text','email','tel','password','submit','button','checkbox','hidden'].includes(el.type))) return {reason:'ambiguous_form'};
                  const username = usernames[0];
                  const usernameHint = [username.id,username.name,username.getAttribute('aria-label'),username.getAttribute('placeholder'),
                    ...Array.from(username.labels || []).map(label=>label.innerText)].filter(Boolean).join(' ')
                    .replace(/([a-z])([A-Z])/g,'$1 $2').normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/[_-]/g,' ');
                  if (username.type !== 'email' && !/^(username|email)$/i.test(username.autocomplete)
                    && !/\\b(user(name)?|usuario|e ?mail|login|account|conta|telefone|phone|identifier)\\b/i.test(usernameHint)) return {reason:'ambiguous_username'};
                  const submits = Array.from(document.querySelectorAll('button,input[type="submit"],input[type="button"],[role="button"]')).filter(el => {
                    if (!visible(el) || (el.form || el.closest('[role="form"]')) !== scope) return false;
                    const label = (el.getAttribute('aria-label') || el.innerText || el.value || '').trim();
                    return /^(log[ -]?in|sign[ -]?in|sign on|entrar|acessar|autenticar|iniciar sessão)(\\b|$)/i.test(label);
                  });
                  if (submits.length !== 1 || [username,password,submits[0]].some(el => el.disabled || el.readOnly || el.getAttribute('aria-disabled') === 'true')) return {reason:'ambiguous_form'};
                  const submit = submits[0];
                  if (fields.some(el => el.required && el !== username && el !== password && el.type !== 'hidden')) return {reason:'additional_required_fields'};
                  const nativeForm = scope instanceof HTMLFormElement;
                  const action = submit.getAttribute('formaction') || (nativeForm ? scope.action : location.href);
                  const method = submit.getAttribute('formmethod') || (nativeForm ? scope.method : 'post');
                  if (new URL(action,location.href).origin !== location.origin || !['get','post'].includes(method.toLowerCase())) return {reason:'unsafe_form_action'};
                  const ref = node => Object.keys(selectors).find(key => document.querySelector(selectors[key]) === node);
                  const refs = [username,password,submit].map(ref);
                  const identity = [username,password,submit].map(el => ({
                    attributes:['id','name','type','autocomplete','aria-label','placeholder','formaction','formmethod']
                      .map(attribute=>el.getAttribute(attribute)),
                    action:el.form?.action || null, method:el.form?.method || null,
                  }));
                  return refs.every(Boolean) ? {refs,identity} : {reason:'unobserved_controls'};
                }"""),
                {"selectors": self._ref_selectors, "panel": self.PANEL_SELECTOR},
            )
            if plan.get("reason"):
                return LoginAttempt("manual_required", plan["reason"])
            for ref in plan["refs"]:
                handles.append(await (await self._locator(ref)).element_handle())
            username_field, password_field, submit = handles
            control_identity = plan["identity"]

            async def controls_unchanged() -> bool:
                if self._page is not page or self._origin(page.url) != self._allowed_origin:
                    return False
                return bool(await page.evaluate(
                    self._login_challenge_script("""({controls:[username,password,submit],expected}) => {
                      const scope = password?.form || password?.closest('[role="form"]');
                      if (!scope || ![username,password,submit].every(el => el?.isConnected && !el.disabled && !el.readOnly && el.getAttribute('aria-disabled') !== 'true' && (el.form || el.closest('[role="form"]')) === scope)) return false;
                      const identity=[username,password,submit].map(el=>({
                        attributes:['id','name','type','autocomplete','aria-label','placeholder','formaction','formmethod'].map(attribute=>el.getAttribute(attribute)),
                        action:el.form?.action || null, method:el.form?.method || null,
                      }));
                      if (JSON.stringify(identity)!==JSON.stringify(expected)) return false;
                      if (!['text','email','tel'].includes(username.type) || password.type !== 'password') return false;
                      const visible = el => {
                        const b=el.getBoundingClientRect(),s=getComputedStyle(el);
                        return b.width>0 && b.height>0 && s.visibility!=='hidden' && s.display!=='none';
                      };
                      if (![username,password,submit].every(visible)) return false;
                      const fields = Array.from(document.querySelectorAll('input')).filter(el=>visible(el) && (el.form || el.closest('[role="form"]'))===scope);
                      if (fields.filter(el=>el.type==='password').length!==1 || fields.filter(el=>['text','email','tel'].includes(el.type)).length!==1) return false;
                      if (fields.some(el=>el.required && el!==username && el!==password && el.type!=='hidden')) return false;
                      __LOGIN_CHALLENGE_JS__
                      if (authenticationChallenge) return false;
                      const label=(submit.getAttribute('aria-label') || submit.innerText || submit.value || '').trim();
                      if (!/^(log[ -]?in|sign[ -]?in|sign on|entrar|acessar|autenticar|iniciar sessão)(\\b|$)/i.test(label)) return false;
                      const action = submit.getAttribute('formaction') || (scope instanceof HTMLFormElement ? scope.action : location.href);
                      return new URL(action,location.href).origin === location.origin;
                    }"""), {"controls": handles, "expected": control_identity},
                ))

            if not await controls_unchanged():
                return LoginAttempt("manual_required", "controls_changed")
            if filled_login:
                # Values remain private/volatile redaction material for this
                # browser run, not credentials, events or CT evidence. Retain
                # the masks after return: a delayed rejection may echo input.
                username, password = await page.evaluate(
                    "controls => controls.slice(0,2).map(el => el.value)", handles,
                )
                if not username or not password:
                    return LoginAttempt("manual_required", "incomplete_credentials")
                for value in (username, password):
                    if value not in self.secret_values:
                        self.secret_values.append(value)
            # Mask before filling (email/anonymous inputs may lack the semantic
            # keywords used by the ordinary screenshot mask).
            for field in (username_field, password_field):
                await field.evaluate("el => el.setAttribute('data-autojev-secret-mask','true')")
            await page.route("**/*", guard_authentication_request)
            request_guard_installed = True
            # Some SPA forms default to GET but prevent native submission in
            # their own handler. Preserve that handler, blocking only a native
            # GET that would put credentials into the URL.
            await page.evaluate(
                """password => {
                  window.__autojevLoginSubmitGuard = event => {
                    const form = password.form;
                    if (event.target !== form) return;
                    const method = event.submitter?.getAttribute('formmethod') || form.method;
                    if (method.toLowerCase() === 'get') event.preventDefault();
                  };
                  window.addEventListener('submit',window.__autojevLoginSubmitGuard);
                }""", password_field,
            )
            if not filled_login:
                await username_field.fill(username, timeout=3_000)
                if not await controls_unchanged():
                    return LoginAttempt("manual_required", "controls_changed")
                await password_field.fill(password, timeout=3_000)
            if not await controls_unchanged():
                return LoginAttempt("manual_required", "controls_changed")
            if filled_login and not await page.evaluate(
                "({controls,values}) => controls.slice(0,2).every((el,i) => el.value === values[i])",
                {"controls": handles, "values": [username, password]},
            ):
                return LoginAttempt("manual_required", "controls_changed")

            async def probe() -> dict[str, Any]:
                return await page.evaluate(
                    self._observed_node_guard_script(self._login_challenge_script("""panel => {
                      __OBSERVED_NODE_GUARD_JS__
                      const visible = el => {
                        if (el.closest(panel)) return false;
                        const b=el.getBoundingClientRect(),s=getComputedStyle(el);
                        return b.width>0 && b.height>0 && s.visibility!=='hidden' && s.display!=='none';
                      };
                      const text=Array.from(document.body?.children || []).filter(el=>!el.matches(panel)).map(el=>el.innerText || '').join(' ').trim();
                      __LOGIN_CHALLENGE_JS__
                      const passwordVisible = Array.from(document.querySelectorAll('input[type="password"]')).some(visible);
                      const errors = Array.from(document.querySelectorAll('[role="alert"],[data-test="error"],.error-message,.error_message')).filter(el =>
                        visible(el) && el.textContent.trim() && (passwordVisible || /invalid (login|credentials|password)|login failed|senha inv[áa]lida|credenciais inv[áa]lidas/i.test(el.textContent)));
                      const busy = Array.from(document.querySelectorAll('[aria-busy="true"],[role="progressbar"]')).some(visible)
                        || /^(loading|carregando|aguarde|signing in|redirecionando)[. …]*$/i.test(text);
                      return {ready:document.readyState!=='loading' && text.length>0 && !busy, challenge:authenticationChallenge, rejected:errors.length>0,
                        rejection_signature:JSON.stringify(errors.map(el=>[observedNodeGuard(el),el.textContent.trim()])),
                        signature:JSON.stringify([location.href,document.title,text])};
                    }""")), self.PANEL_SELECTOR,
                )

            # A denied deep-link can leave a stale login error visible. Only a
            # new/changed post-submit error is evidence of this attempt failing.
            baseline_rejection = (await probe())["rejection_signature"]
            if not await controls_unchanged():
                return LoginAttempt("manual_required", "controls_changed")
            submit_started = True
            await submit.click(timeout=4_000)
            deadline = time.monotonic() + 8
            await self._wait_for_dom_settle()
            clear_reads = 0
            previous_signature = None
            while time.monotonic() < deadline:
                if not self.is_active():
                    raise BrowserUnavailableError("Browser page is not active")
                state = await probe()
                if blocked_request or self._blocked_navigation_url or self._origin(page.url) != self._allowed_origin:
                    return LoginAttempt("manual_required", "unsafe_authentication_request")
                if state["challenge"]:
                    return LoginAttempt("manual_required", "authentication_challenge")
                if state["rejected"] and state["rejection_signature"] != baseline_rejection:
                    return LoginAttempt("rejected", "login_rejected")
                if not state["rejected"]:
                    baseline_rejection = state["rejection_signature"]
                stable = state["signature"] == previous_signature
                previous_signature = state["signature"]
                clear_reads = clear_reads + 1 if stable and state["ready"] and not self._pending_app_requests and not await self.is_login_page() else 0
                if clear_reads >= 3:
                    observed = await self.observe("after-user-login" if filled_login else "after-automatic-login")
                    verified = await probe()
                    if (
                        verified["ready"] and not verified["challenge"] and not verified["rejected"]
                        and verified["signature"] == previous_signature
                        and not self._pending_app_requests and not await self.is_login_page()
                        and not blocked_request and self._origin(page.url) == self._allowed_origin
                    ):
                        return LoginAttempt("success", "authenticated", observed)
                    clear_reads = 0
                await asyncio.sleep(0.25)
            return LoginAttempt("manual_required", "login_not_confirmed")
        except BrowserUnavailableError:
            raise
        except Exception:
            # Playwright errors can embed typed values. Return only a fixed
            # reason code, never its raw exception or a credential-bearing CT.
            return LoginAttempt("manual_required", "login_not_confirmed" if submit_started else "controls_changed")
        finally:
            if request_guard_installed:
                try:
                    await page.unroute("**/*", guard_authentication_request)
                    await page.evaluate("() => {window.removeEventListener('submit',window.__autojevLoginSubmitGuard); delete window.__autojevLoginSubmitGuard;}")
                except Exception:
                    pass
            for handle in handles:
                if handle:
                    with suppress(Exception):
                        await handle.dispose()

    async def wait_for_user_input(self) -> None:
        # Mantém a barra lateral sempre visível durante a espera:
        # reinjeta se navegação/JS do site a remover.
        self._user_input_event.clear()
        await self.show_intervention_panel(self._intervention_message or "Preencha usuário e senha na página.")
        while not self._user_input_event.is_set():
            try:
                await asyncio.wait_for(self._user_input_event.wait(), timeout=1.0)
                break
            except asyncio.TimeoutError:
                pass
            if self.headless or not self._page:
                continue
            try:
                exists = await self._page.evaluate(
                    "!!document.getElementById('autojev-sidepanel') && !document.getElementById('autojev-panel-action')?.hidden"
                )
            except Exception:
                exists = True
            if not exists and not self._user_input_event.is_set():
                await self.show_intervention_panel(self._intervention_message or "Preencha usuário e senha na página.")
        self._user_input_event.clear()
        await self.hide_intervention_panel()

    async def capture_print(self, label: str) -> dict[str, Any]:
        """One user-requested masked bitmap, with no action or assertion run."""
        if not self._page or not self.is_active():
            raise BrowserUnavailableError("Browser closed before manual capture")
        url = self._page.url
        if self._allowed_origin and self._origin(url) != self._allowed_origin:
            raise BrowserUnavailableError("Manual capture is outside the authorized origin")
        screenshot = await self._capture(label)
        if not screenshot:
            raise RuntimeError("Manual screenshot could not be saved")
        return {"screenshot": screenshot, "url": self._public_url(url), "captured_at": utc_now()}

    @staticmethod
    def _validate_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Only absolute http(s) URLs are allowed")
        if parsed.username or parsed.password:
            raise ValueError("Credentials in URLs are not allowed")

    @staticmethod
    def _origin(url: str) -> tuple[str, str, int]:
        parsed = urlparse(url)
        default_port = 443 if parsed.scheme.lower() == "https" else 80
        return (parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port or default_port)

    async def _guard_navigation(self, route: Any, request: Any) -> None:
        if self._rerun_panel is not None and await self._rerun_panel.route(route, request):
            return
        try:
            is_top_level = True
            if request.is_navigation_request():
                try:
                    is_top_level = request.frame.parent_frame is None
                except Exception:
                    # A popup's first navigation may arrive before its Frame is
                    # exposed. Treat that specific unknown navigation as
                    # top-level; normal iframe requests have a usable parent.
                    is_top_level = True
            if (
                request.is_navigation_request()
                and is_top_level
                and self._origin(request.url) != self._allowed_origin
            ):
                self._blocked_navigation_url = self._public_url(request.url)
                await route.abort("blockedbyclient")
                return
        except Exception:
            # Malformed navigation targets fail closed; ordinary assets continue.
            if request.is_navigation_request():
                await route.abort("blockedbyclient")
                return
        await route.continue_()

    def _redact_text(self, value: str) -> str:
        for secret in self.secret_values:
            value = value.replace(secret, "[REDACTED]")
        return value

    def _field_value_token(self, value: str) -> str:
        return hmac.new(
            self._field_token_key, value.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def _navigation_metadata(self, response: Any) -> dict[str, Any]:
        return {
            "url": self._public_url(str(response.url)),
            "status_code": response.status,
        }

    def _sanitize_page_state(self, state: Any) -> dict[str, Any]:
        """Raw field contents never leave the executor in public observations."""
        if not isinstance(state, dict):
            return {}
        self._field_semantics = {}
        safe_synthetic = {
            "", "AutoJev QA exploratory input", "AutoJev QA",
            "autojev.qa@example.com", "11999999999", "1", "https://example.com",
        }
        private_values: list[str] = []
        for item in state.get("fields", []):
            if not isinstance(item, dict):
                continue
            raw = str(item.pop("_raw_value", ""))
            sensitive = bool(item.get("sensitive"))
            if raw and (sensitive or raw not in safe_synthetic):
                private_values.append(raw)
            if not sensitive:
                item["value_token"] = self._field_value_token(raw)
                if raw in safe_synthetic and raw not in self.secret_values:
                    item["value"] = raw
            else:
                item.pop("value", None)
                item.pop("value_token", None)
            ref = str(item.get("ref") or "")
            if ref:
                self._field_semantics[ref] = {
                    "identity": item.get("identity"), "sensitive": sensitive,
                }
        def scrub(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    key: item if key == "value_token" and isinstance(item, str) and re.fullmatch(r"[a-f0-9]{64}", item)
                    else scrub(item)
                    for key, item in value.items() if not key.startswith("_")
                }
            if isinstance(value, list):
                return [scrub(item) for item in value]
            if isinstance(value, str):
                for private in private_values:
                    if len(private) >= 3:
                        value = value.replace(private, "[FIELD VALUE]")
                    elif value == private:
                        value = "[FIELD VALUE]"
                return self._redact_text(value)
            return value
        sanitized = scrub(state)
        for item in [*sanitized.get("products", []), *sanitized.get("cart", {}).get("items", []),
                     *(sanitized.get("checkout") or {}).get("items", [])]:
            if item.get("image_src"):
                item["image_src"] = self._public_url(item["image_src"])
        detail = sanitized.get("product_detail")
        if isinstance(detail, dict) and detail.get("image_src"):
            detail["image_src"] = self._public_url(detail["image_src"])
        return sanitized

    @staticmethod
    def _cart_product_fact(product: dict[str, Any]) -> dict[str, Any]:
        return {
            key: product.get(key) for key in ("id", "identity_source", "name", "price", "quantity")
            if product.get(key) is not None
        }

    @staticmethod
    def _cart_product_key(product: dict[str, Any]) -> str:
        return product_ledger_key(product)

    def _observe_cart_expectation(self, state: dict[str, Any]) -> None:
        """Keep the user's action intent separate from possibly faulty UI data.

        Initial observed contents establish a baseline, never prove the app
        correct. Subsequent observations must not overwrite an expectation when
        a wrong item/count is found; that would silently approve the defect.
        """
        expectation = self._cart_expectation
        cart = state.get("cart") or {}
        checkout = state.get("checkout") or {}
        if checkout.get("stage") == "complete" and checkout.get("confirmed") and cart.get("count") == 0:
            # Some shops retain a cart after ordering. Only an actually
            # observed empty badge confirms this post-checkout reset; the
            # confirmation heading alone must not invent a universal rule.
            expectation.update(items=[], items_known=True, source="confirmed_checkout", pending_membership=[])
        elif not expectation["items_known"]:
            if cart.get("count") == 0 and not expectation["pending_membership"]:
                expectation.update(items=[], items_known=True, source="observed_empty_cart")
            elif cart.get("items_known") and not expectation["pending_membership"]:
                expectation.update(
                    items=[self._cart_product_fact(item) for item in cart.get("items", [])],
                    items_known=True, source="initial_cart_baseline",
                )
            elif state.get("products") and not expectation["pending_membership"]:
                selected = [item for item in state["products"] if item.get("cart_action") == "remove"]
                if isinstance(cart.get("count"), int) and len(selected) == cart["count"]:
                    expectation.update(
                        items=[self._cart_product_fact(item) for item in selected],
                        items_known=True, source="initial_product_controls",
                    )
        state["cart_expectation"] = copy.deepcopy(expectation)

    def _remember_cart_action(self, decision: AgentDecision) -> dict[str, Any] | None:
        if decision.action != "click":
            return None
        label = str(self._ref_semantics.get(str(decision.target), {}).get("name") or "").strip()
        hostname = (urlsplit(self._page.url).hostname or "").casefold() if self._page else ""
        demo_reset = label.casefold() == "reset app state" and (
            hostname in {"saucedemo.com", "www.saucedemo.com", "localhost", "127.0.0.1", "::1"}
            or hostname.endswith(".test")
        )
        explicit_cart_clear = re.fullmatch(r"clear cart|empty cart|limpar carrinho|esvaziar carrinho", label, re.I) is not None
        if demo_reset or explicit_cart_clear:
            self._cart_expectation.update(
                items=[], items_known=True, pending_membership=[], source="explicit_cart_reset",
                revision=self._cart_expectation["revision"] + 1,
            )
            return {"kind": "explicit_cart_reset", "expected_empty": True, "control": label}
        state = self._last_page_state
        products = [*state.get("products", []), *(state.get("cart") or {}).get("items", [])]
        if isinstance(state.get("product_detail"), dict):
            products.append(state["product_detail"])
        product = next((item for item in products if decision.target in (item.get("action_refs") or [])), None)
        if not product or product.get("cart_action") not in {"add", "remove"}:
            return None
        fact = self._cart_product_fact(product)
        key = self._cart_product_key(fact)
        if not key:
            return None
        expectation = self._cart_expectation
        adding = product["cart_action"] == "add"
        collection_key = "items" if expectation["items_known"] else "pending_membership"
        recorded = expectation[collection_key]
        matching = matching_product_indexes(fact, recorded)
        if len(matching) > 1:
            # A name-only cart control cannot identify which declared SKU it
            # removed. Retain the established intent rather than deleting all
            # homonymous items or turning the ambiguity into later approvals.
            return {
                "kind": "product_cart_action", "operation": "add" if adding else "remove",
                "product": fact, "outcome": "identity_ambiguous",
                "ledger_update": "previous_baseline_preserved",
            }
        if matching:
            previous_fact = recorded[matching[0]]
            if declared_product_id(fact) is None and declared_product_id(previous_fact) is not None:
                fact.update(id=previous_fact["id"], identity_source=previous_fact.get("identity_source"))
        if expectation["items_known"]:
            items = [item for index, item in enumerate(recorded) if index not in matching]
            if adding:
                items.append(fact)
            expectation["items"] = items
        else:
            pending = [item for index, item in enumerate(recorded) if index not in matching]
            pending.append({**fact, "present": adding})
            expectation["pending_membership"] = pending
        expectation["revision"] += 1
        expectation["source"] = "product_cart_intent"
        return {
            "kind": "product_cart_action", "operation": "add" if adding else "remove",
            "product": fact, "outcome": "effect_not_yet_verified",
        }

    def _retain_cart_baseline_for_noop(
        self,
        intent: dict[str, Any],
        previous_expectation: dict[str, Any],
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> bool:
        """Do not turn a proven failed Add/Remove into future cart loss.

        This is deliberately not synchronization with the application's cart.
        Only an unchanged badge AND unchanged control for the same product
        prove this dispatched action had no observable membership effect.
        Partial/unknown effects and successful-looking wrong-product actions
        retain the intended ledger so later composition/price/loss checks still
        detect defects. The current action's business assertions still FAIL.
        """
        if intent.get("kind") != "product_cart_action":
            return False
        prior_cart, current_cart = before.get("cart") or {}, after.get("cart") or {}
        prior_count, current_count = prior_cart.get("count"), current_cart.get("count")
        if (
            not isinstance(prior_count, int) or isinstance(prior_count, bool)
            or not isinstance(current_count, int) or isinstance(current_count, bool)
            or prior_count < 0 or current_count != prior_count
        ):
            return False

        def products(state: dict[str, Any]) -> list[dict[str, Any]]:
            values = [*state.get("products", []), *(state.get("cart") or {}).get("items", [])]
            if isinstance(state.get("product_detail"), dict):
                values.append(state["product_detail"])
            return values

        key = self._cart_product_key(intent.get("product") or {})
        prior_products, current_products = products(before), products(after)
        def ambiguous_keys(values: list[dict[str, Any]]) -> bool:
            groups: dict[str, list[dict[str, Any]]] = {}
            for item in values:
                groups.setdefault(self._cart_product_key(item), []).append(item)
            return any(len(group) > 1 and (
                any(declared_product_id(item) is None for item in group)
                or len({item.get("cart_action") for item in group}) > 1
            ) for group in groups.values())
        if ambiguous_keys(prior_products) or ambiguous_keys(current_products):
            # Duplicate name-derived keys cannot prove the selected entity
            # remained unchanged; do not let dictionary collapse hide a swap.
            # Repeated facts for one declared entity on catalog/detail surfaces
            # are harmless only when their observed controls agree.
            return False
        prior = next((item for item in prior_products if self._cart_product_key(item) == key), None)
        current = next((item for item in current_products if self._cart_product_key(item) == key), None)
        if not prior or not current or prior.get("cart_action") not in {"add", "remove"}:
            return False
        if current.get("cart_action") != prior["cart_action"]:
            return False
        # A constant count alone cannot exclude replacing another product.
        # Retain intent if any other observed membership/control changed.
        prior_controls = {self._cart_product_key(item): item.get("cart_action") for item in prior_products}
        current_controls = {self._cart_product_key(item): item.get("cart_action") for item in current_products}
        if prior_controls != current_controls:
            return False
        if prior_cart.get("items_known") and current_cart.get("items_known"):
            prior_items = sorted((self._cart_product_key(item), str(item.get("quantity", 1))) for item in prior_cart.get("items", []))
            current_items = sorted((self._cart_product_key(item), str(item.get("quantity", 1))) for item in current_cart.get("items", []))
            if prior_items != current_items:
                return False

        self._cart_expectation = copy.deepcopy(previous_expectation)
        after["cart_expectation"] = copy.deepcopy(previous_expectation)
        self._last_page_state["cart_expectation"] = copy.deepcopy(previous_expectation)
        intent.update(
            outcome="observed_no_op", ledger_update="previous_baseline_preserved",
            reason="cart_count_and_product_control_unchanged",
        )
        return True

    def _public_url(self, value: str) -> str:
        """Remove URL credentials, sensitive query values and fragments before egress."""

        sensitive = {
            "access_token",
            "auth",
            "authorization",
            "code",
            "credential",
            "jwt",
            "key",
            "magic",
            "password",
            "secret",
            "session",
            "sig",
            "signature",
            "token",
        }
        try:
            parsed = urlsplit(value)
            host = parsed.hostname or ""
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            netloc = f"{host}:{parsed.port}" if parsed.port else host
            query = []
            for key, item in parse_qsl(parsed.query, keep_blank_values=True):
                lowered = key.lower().replace("-", "_")
                safe_value = "[REDACTED]" if lowered in sensitive else self._redact_text(item)
                query.append((key, safe_value))
            fragment = self._redact_text(parsed.fragment)
            fragment_pairs = parse_qsl(fragment.lstrip("?/"), keep_blank_values=True)
            if len(fragment) > 200 or any(
                key.lower().replace("-", "_") in sensitive
                for key, _ in fragment_pairs
            ):
                fragment = "[REDACTED]"
            result = urlunsplit(
                (parsed.scheme, netloc, parsed.path, urlencode(query), fragment)
            )
            return self._redact_text(result)
        except (TypeError, ValueError):
            return self._redact_text(value.split("#", 1)[0])

    def _event_page_context(self, page: Any = None) -> dict[str, str]:
        """Capture the current page at callback time, not request initiation.

        Buffers can be collected after a subsequent navigation. Keep this
        factual page context separate from the later observation's URL.
        """

        source_page = page if page is not None else self._page
        page_url = source_page.url if source_page else ""
        if not page_url:
            return {}
        return {
            "page_url": self._public_url(page_url)[:2000],
            "page_url_source": "event",
        }

    def _on_console(self, message: Any, *, page: Any = None) -> None:
        text = str(getattr(message, "text", ""))
        panel_csp_diagnostic = (self._rerun_panel is not None
            and "https://autojev-panel.invalid" in text and "Content Security Policy" in text
            and text.startswith(("Refused to frame", "Refused to load", "Framing '")))
        if (is_panel_url((getattr(message, "location", {}) or {}).get("url", ""))
                or panel_csp_diagnostic):
            return
        if self.options.capture_console_errors and message.type == "error":
            page_context = self._event_page_context(page)
            self._console_buffer.append(
                {
                    "type": "console",
                    "message": self._redact_text(message.text)[:2000],
                    "url": page_context.get("page_url", ""),
                    "occurred_at": utc_now(),
                    **page_context,
                }
            )

    def _on_page_error(self, error: Exception, *, page: Any = None) -> None:
        if any("https://autojev-panel.invalid/" in line
               for line in str(getattr(error, "stack", "")).splitlines()[1:]):
            return
        if self.options.capture_console_errors:
            page_context = self._event_page_context(page)
            self._console_buffer.append(
                {
                    "type": "pageerror",
                    "message": self._redact_text(str(error))[:2000],
                    "url": page_context.get("page_url", ""),
                    "occurred_at": utc_now(),
                    **page_context,
                }
            )

    def _on_response(self, response: Any, *, page: Any = None) -> None:
        if is_panel_url(getattr(response, "url", "")):
            return
        # Remember the main document independently from error capture. A SPA
        # may hydrate after a 404 response; the planner still needs to know
        # that replaying the URL with GET is not equivalent to its UI route.
        try:
            if response.request.resource_type == "document" and response.frame == self._page.main_frame:
                self._document_response = self._navigation_metadata(response)
        except (AttributeError, TypeError):
            pass
        if self.options.capture_network_errors and response.status >= 400:
            self._network_buffer.append(
                {
                    "type": "http",
                    "method": response.request.method,
                    "url": self._public_url(response.url)[:2000],
                    "status_code": response.status,
                    "message": f"{response.request.method} returned HTTP {response.status}",
                    "occurred_at": utc_now(),
                    **self._event_page_context(page),
                }
            )

    def _on_request_failed(self, request: Any, *, page: Any = None) -> None:
        if is_panel_url(getattr(request, "url", "")):
            return
        if self.options.capture_network_errors:
            failure = request.failure or "Request failed"
            self._network_buffer.append(
                {
                    "type": "network",
                    "method": request.method,
                    "url": self._public_url(request.url)[:2000],
                    "status_code": None,
                    "message": self._redact_text(str(failure))[:2000],
                    "occurred_at": utc_now(),
                    **self._event_page_context(page),
                }
            )

    async def _capture(self, label: str, *, viewport_only: bool = False) -> str | None:
        if not self.options.capture_screenshots or not self._page:
            return None
        self._screenshot_number += 1
        safe_label = "".join(char if char.isalnum() else "-" for char in label.lower()).strip("-")
        filename = f"{self._screenshot_number:03d}-{safe_label[:60] or 'page'}.png"
        path = self.artifact_dir / filename
        masks = []
        if self.secret_values:
            try:
                await self._page.evaluate(
                    """(secrets) => {
                      for (const el of document.querySelectorAll('input,textarea,select')) {
                        if (secrets.includes(el.value)) el.setAttribute('data-autojev-secret-mask', 'true');
                      }
                    }""",
                    self.secret_values,
                )
            except Exception:
                pass
        marked = self._page.locator('[data-autojev-secret-mask="true"]')
        if await marked.count():
            masks.append(marked)
        sensitive = self._page.locator(self._SENSITIVE_FIELD_SELECTOR)
        if await sensitive.count():
            masks.append(sensitive)
        # Preparation checks also capture the page before the explicit login
        # helper runs. Mask anonymous username/email controls in a visible
        # password form here, in the screenshot compositor, without reading
        # values or changing DOM contents/styles. Ordinary checkout fields in
        # forms without a password remain visible.
        login_fields = self._page.locator(
            ':is(form,[role="form"]):has(input[type="password"]:visible) '
            ':is(input:not([type]),input[type="text"],input[type="email"],input[type="tel"]):visible'
        )
        if await login_fields.count():
            masks.append(login_fields)
        for secret in self.secret_values:
            try:
                text_locator = self._page.get_by_text(secret, exact=False)
                if await text_locator.count():
                    masks.append(text_locator)
            except Exception:
                continue
        # Do not inject screenshot-only visibility styles. Chromium briefly
        # paints those styles in the live window and makes the panel flash.
        # In headed runs the stable status panel may therefore appear in the
        # evidence image; that is preferable to mutating the user's viewport.
        screenshot_options: dict[str, Any] = {}
        if viewport_only:
            # Geometry for action previews is recorded in CSS pixels. Do not
            # let a retina/device scale factor resize only the bitmap.
            screenshot_options["scale"] = "css"
        await self._page.screenshot(
            path=str(path),
            full_page=self.headless and not viewport_only,
            mask=masks,
            mask_color="#101416",
            # Playwright's default caret='hide' temporarily writes inline
            # styles to every editable field, which can trigger application
            # mutation observers. Sensitive contents are already masked.
            caret="initial",
            timeout=20_000,
            **screenshot_options,
        )
        return filename

    async def _capture_action_preview(
        self, locator: Any, decision: AgentDecision,
    ) -> dict[str, Any] | None:
        """Save a masked, unannotated viewport and its checked action target.

        The report draws its marker over these coordinates, never over the
        tested application's DOM or into the evidence image. This optional
        local evidence must not prevent an otherwise valid browser action.
        """
        if not self.options.capture_screenshots or self._page is None:
            return None
        try:
            page = self._page
            await locator.scroll_into_view_if_needed(timeout=2_000)
            viewport_script = "() => ({width: innerWidth, height: innerHeight})"
            viewport = await page.evaluate(viewport_script)
            before = await locator.bounding_box()
            if not isinstance(viewport, dict) or not isinstance(before, dict):
                return None
            image = {key: viewport.get(key) for key in ("width", "height")}
            box = {key: before.get(key) for key in ("x", "y", "width", "height")}
            for value in (*image.values(), *box.values()):
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    return None
            if min(image.values()) <= 0 or box["width"] <= 0 or box["height"] <= 0:
                return None
            # Do not invent a click point for an offscreen/oversized control.
            # A preview without trusted geometry is better omitted entirely.
            if (
                box["x"] < 0 or box["y"] < 0
                or box["x"] + box["width"] > image["width"]
                or box["y"] + box["height"] > image["height"]
            ):
                return None
            source_url = page.url
            captured_at = utc_now()
            screenshot = await self._capture(
                f"before-{decision.action}-target", viewport_only=True,
            )
            if not screenshot or self._page is not page or page.url != source_url:
                return None
            after = await locator.bounding_box()
            after_viewport = await page.evaluate(viewport_script)
            if not isinstance(after, dict) or after_viewport != viewport:
                return None
            for key, previous in box.items():
                current = after.get(key)
                if (
                    isinstance(current, bool) or not isinstance(current, (int, float))
                    or not math.isfinite(current) or abs(current - previous) > 1
                ):
                    return None
            return {
                "version": "action-target-v1",
                "screenshot": screenshot,
                "captured_at": captured_at,
                "url": self._public_url(source_url),
                "action": decision.action,
                "target": decision.target,
                "coordinate_space": "screenshot-css-pixels",
                "image": image,
                "box": box,
            }
        except Exception:
            # Disappearing controls, unsupported test doubles or screenshot
            # failures are evidence limitations, not action/verdict failures.
            return None

    async def _select_label_snapshot(
        self, locator: Any, requested_value: str,
    ) -> dict[str, Any] | None:
        """Read display labels, never export option values or change the DOM.

        Native select popups are browser/OS UI, not screenshot evidence. These
        labels describe the checked control without pretending its menu opened.
        Comparing the private value happens inside this read-only DOM query.
        """
        try:
            snapshot = await locator.evaluate(
                """(el, {requested, sensitiveSelector}) => {
                  if (!(el instanceof HTMLSelectElement) || !el.isConnected
                    || el.matches(sensitiveSelector)
                    || el.hasAttribute('data-autojev-secret-mask')) return null;
                  const options = Array.from(el.options).slice(0, 1000);
                  const requestedOption = options.find(option => option.value === requested);
                  const selected = Array.from(el.selectedOptions);
                  return {
                    requested_label: requestedOption?.label ?? null,
                    selected_label: selected.slice(0, 20).map(option => option.label).join(', '),
                    matches: selected.length === 1 && selected[0].value === requested,
                  };
                }""",
                {"requested": requested_value, "sensitiveSelector": self._SENSITIVE_FIELD_SELECTOR},
            )
            if not isinstance(snapshot, dict) or not isinstance(snapshot.get("matches"), bool):
                return None

            def label(value: Any) -> str | None:
                if not isinstance(value, str):
                    return None
                value = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", self._redact_text(value))
                value = " ".join(value.split())[:240].strip()
                return value or None

            return {
                "requested_label": label(snapshot.get("requested_label")),
                "selected_label": label(snapshot.get("selected_label")),
                "matches": snapshot["matches"],
            }
        except Exception:
            return None

    async def observe(self, screenshot_label: str = "page") -> Observation:
        if not self.is_active():
            raise BrowserUnavailableError("Browser page is not active")
        await self._release_observed_element_handles()
        snapshot = await self._page.evaluate(
            self._observed_node_guard_script(self._login_challenge_script("""() => {
              __OBSERVED_NODE_GUARD_JS__
              const PANEL_SELECTOR = '#autojev-sidepanel,#autojev-intervention-panel,#autojev-live-panel';
              // Nunca altera a renderização do painel durante a observação.
              // A UI do AutoJev é excluída apenas da cópia lógica entregue ao
              // JEV, evitando o flash de visibility:hidden a cada etapa.
              const isVisible = (el) => {
                const box = el.getBoundingClientRect();
                if (el.closest('[aria-hidden="true"]') || box.width <= 0 || box.height <= 0) {
                  return false;
                }
                // Opacity is not inherited in computed styles: a child can
                // report opacity 1 while an opacity:0 ancestor makes it fully
                // invisible. Walk the rendered ancestry so hidden drawers and
                // transition shells never become executable coverage.
                for (let owner = el; owner; owner = owner.parentElement) {
                  const style = getComputedStyle(owner);
                  if (style.visibility === 'hidden'
                    || style.visibility === 'collapse'
                    || style.display === 'none'
                    || Number.parseFloat(style.opacity || '1') <= 0) {
                    return false;
                  }
                }
                return true;
              };
              // Distinguish controls that Playwright can safely target from
              // elements that merely remain mounted off-canvas or underneath
              // a drawer. Controls below the fold stay reachable because
              // Playwright can scroll them into view; horizontally displaced
              // controls remain eligible only when a real scroll surface can
              // bring them back. Fixed/transformed off-canvas drawers cannot.
              const interactionState = (el, box) => {
                const inHorizontalViewport = box.right > 0 && box.left < window.innerWidth;
                const inVerticalViewport = box.bottom > 0 && box.top < window.innerHeight;
                const scrollableAncestor = (axis) => {
                  for (let owner = el.parentElement; owner && owner !== document.body; owner = owner.parentElement) {
                    const style = getComputedStyle(owner);
                    const overflow = axis === 'x' ? style.overflowX : style.overflowY;
                    const hasRange = axis === 'x'
                      ? owner.scrollWidth > owner.clientWidth + 1
                      : owner.scrollHeight > owner.clientHeight + 1;
                    if (hasRange && ['auto', 'scroll', 'overlay'].includes(overflow)) return true;
                  }
                  return false;
                };
                const fixedOwner = (() => {
                  for (let owner = el; owner; owner = owner.parentElement) {
                    if (getComputedStyle(owner).position === 'fixed') return true;
                  }
                  return false;
                })();
                const root = document.scrollingElement || document.documentElement;
                const documentX = box.left + window.scrollX;
                const documentY = box.top + window.scrollY;
                const horizontallyReachable = inHorizontalViewport || scrollableAncestor('x') || (
                  !fixedOwner
                  && root.scrollWidth > window.innerWidth + 1
                  && documentX + box.width > 0
                  && documentX < root.scrollWidth
                );
                const verticallyReachable = inVerticalViewport || scrollableAncestor('y') || (
                  !fixedOwner
                  && root.scrollHeight > window.innerHeight + 1
                  && documentY + box.height > 0
                  && documentY < root.scrollHeight
                );
                if (!horizontallyReachable || !verticallyReachable) {
                  return { inHorizontalViewport, inVerticalViewport, interactable: false };
                }
                if (!inHorizontalViewport || !inVerticalViewport) {
                  return { inHorizontalViewport, inVerticalViewport, interactable: true };
                }
                // Playwright clicks the center of the visible box. A free
                // corner is not enough: it previously approved buttons whose
                // center was intercepted by the site's open burger menu.
                const x = (Math.max(0, box.left) + Math.min(window.innerWidth - 1, box.right)) / 2;
                const y = (Math.max(0, box.top) + Math.min(window.innerHeight - 1, box.bottom)) / 2;
                const hit = document.elementsFromPoint(x, y).find((candidate) => (
                  !candidate.closest(PANEL_SELECTOR)
                  && getComputedStyle(candidate).pointerEvents !== 'none'
                ));
                const canReceivePointer = Boolean(hit && (hit === el || el.contains(hit)));
                return {
                  inHorizontalViewport,
                  inVerticalViewport,
                  interactable: canReceivePointer,
                  obstruction: canReceivePointer ? null : hit,
                };
              };
                const visibleDialogs = Array.from(document.querySelectorAll(
                  'dialog[open],[role="dialog"]'
                )).filter(isVisible);
                const modalDialog = [...visibleDialogs].reverse().find((dialog) => {
                  try { return dialog.matches(':modal'); } catch { return false; }
                });
                const activeDialog = modalDialog
                  || visibleDialogs[visibleDialogs.length - 1]
                  || null;
                const overlaySelector = 'dialog[open],[role="dialog"],[aria-modal="true"],[role="menu"],.bm-menu-wrap,[data-testid="drawer"],[data-test="drawer"]';
                const overlays = Array.from(document.querySelectorAll(overlaySelector)).filter((node) => {
                  const box = node.getBoundingClientRect();
                  return !node.closest(PANEL_SELECTOR) && isVisible(node)
                    && box.right > 0 && box.left < window.innerWidth
                    && box.bottom > 0 && box.top < window.innerHeight;
                });
                const overlayFor = (node) => overlays.find((owner) => owner.contains(node)) || null;
                const candidates = Array.from(document.querySelectorAll(
                  'a,button,input,select,textarea,[aria-haspopup],[role="button"],[role="link"],[role="menuitem"],[role="checkbox"],[role="radio"],[tabindex]'
                )).filter((el) => {
                  if (el.closest(PANEL_SELECTOR)) return false;
                  if (activeDialog && !activeDialog.contains(el)) return false;
                  return isVisible(el);
                }).slice(0, 300);
              const selectorFor = (node) => {
                const parts = [];
                let current = node;
                while (current && current.nodeType === Node.ELEMENT_NODE) {
                  const tag = current.tagName.toLowerCase();
                  const parent = current.parentElement;
                  if (!parent) {
                    parts.unshift(tag);
                    break;
                  }
                  const siblings = Array.from(parent.children).filter(
                    (item) => item.tagName === current.tagName
                  );
                  parts.unshift(`${tag}:nth-of-type(${siblings.indexOf(current) + 1})`);
                  current = parent;
                }
                return parts.join(' > ');
              };
              // A marker is an identity only when unique in the observed DOM.
              // Repeated data-test values are common in product rows; never
              // collapse their controls merely because those markers match.
              const identityAttributes = ['id', 'data-testid', 'data-test', 'data-qa'];
              const ownerAttributes = [...identityAttributes, 'data-product-id', 'data-item-id'];
              const markerCounts = new Map();
              for (const node of document.querySelectorAll(ownerAttributes.map(attr => `[${attr}]`).join(','))) {
                for (const attr of ownerAttributes) {
                  const value = node.getAttribute(attr);
                  if (!value || value.length > 200) continue;
                  const key = JSON.stringify([attr, value]);
                  markerCounts.set(key, (markerCounts.get(key) || 0) + 1);
                }
              }
              const uniqueMarker = (node, attributes) => {
                for (const attr of attributes) {
                  const value = node.getAttribute(attr);
                  if (!value || value.length > 200) continue;
                  const key = JSON.stringify([attr, value]);
                  if (markerCounts.get(key) === 1) return [node.tagName.toLowerCase(), attr, value];
                }
                return null;
              };
              const identityLabel = node => (node.getAttribute('aria-label') || node.innerText
                || (node instanceof HTMLInputElement && ['submit','button','reset'].includes(node.type) ? node.value : '')
                || node.getAttribute('placeholder') || node.getAttribute('name') || '').trim().slice(0, 300);
              const controlSignature = node => JSON.stringify([
                node.tagName.toLowerCase(), node.getAttribute('role') || '', node.getAttribute('type') || '',
                identityLabel(node).toLowerCase().replace(/\\s+/g, ' '),
                node instanceof HTMLAnchorElement ? node.href : '', node.getAttribute('aria-haspopup') || '',
              ]);
              const signatures = new Map(candidates.map(node => [node, controlSignature(node)]));
              const allControls = Array.from(document.querySelectorAll(
                'a,button,input,select,textarea,[aria-haspopup],[role="button"],[role="link"],[role="menuitem"],[role="checkbox"],[role="radio"],[tabindex]'
              ));
              const allSignatures = new Map(allControls.map(node => [node, controlSignature(node)]));
              const anchorSignatures = new Map();
              // Include hidden/disabled duplicates: uniqueness is a DOM fact,
              // not permission to alias another control when it becomes live.
              for (const anchor of document.querySelectorAll('a[href]')) {
                const signature = controlSignature(anchor);
                anchorSignatures.set(signature, (anchorSignatures.get(signature) || 0) + 1);
              }
              const semanticIdentity = node => {
                let entity = null;
                for (let parent = node.parentElement, depth = 0;
                     parent && depth < 8 && !['BODY','HTML'].includes(parent.tagName);
                     parent = parent.parentElement, depth += 1) {
                  if (!parent.hasAttribute('data-product-id') && !parent.hasAttribute('data-item-id')) continue;
                  entity = uniqueMarker(parent, ['data-product-id', 'data-item-id']);
                  // A reused edit/toggle component must not ignore its entity.
                  // Ambiguous declared entities keep textual context rather
                  // than inheriting coverage through a globally unique button.
                  if (!entity) return null;
                  break;
                }
                const own = uniqueMarker(node, identityAttributes);
                if (own) return `control:${JSON.stringify(own)}${entity ? `:entity:${JSON.stringify(entity)}` : ''}`;
                // A unique, declared entity/scope can anchor an unmarked
                // control only when its semantic signature is unique there.
                // Homonymous siblings retain textual row context otherwise.
                for (let parent = node.parentElement, depth = 0;
                     parent && depth < 8 && !['BODY','HTML'].includes(parent.tagName);
                     parent = parent.parentElement, depth += 1) {
                  const owner = uniqueMarker(parent, ownerAttributes);
                  if (!owner) continue;
                  const matches = allControls.filter(candidate => parent.contains(candidate)
                    && allSignatures.get(candidate) === signatures.get(node));
                  if (matches.length === 1) return `owner:${JSON.stringify(owner)}`;
                }
                // A native, unique global navigation anchor can have no test
                // marker while its main/header text contains a changing cart.
                // Do not extend this to anonymous cards, form actions, rows,
                // dialogs, onclick controls or placeholder/hash anchors.
                if (node instanceof HTMLAnchorElement
                    && node.parentElement?.matches('main,header,footer,nav,[role="navigation"]')
                    && !node.closest('article,tr,[role="row"],li,[role="listitem"],form,[role="form"],[role="dialog"],[data-product-id],[data-item-id]')
                    && !node.hasAttribute('onclick') && typeof node.onclick !== 'function'
                    && !node.hasAttribute('download')
                    && (node.getAttribute('href') || '').trim()
                    && !(node.getAttribute('href') || '').trim().startsWith('#')
                    && anchorSignatures.get(signatures.get(node)) === 1) {
                  let destination;
                  try { destination = new URL(node.href); } catch { return null; }
                  if (['http:','https:'].includes(destination.protocol)
                      && destination.origin === location.origin) {
                    // The full name/href/role/type remain in the fingerprint.
                    // Avoid copying credential-bearing URLs into metadata.
                    return 'navigation:unique-declared-href';
                  }
                }
                return null;
              };
              const elements = candidates.map((el, index) => {
                const ref = `e${index}`;
                const box = el.getBoundingClientRect();
                const interaction = interactionState(el, box);
                const overlay = overlayFor(el);
                const obstructingOverlay = interaction.obstruction ? overlayFor(interaction.obstruction) : null;
                const controlLabel = (el.getAttribute('aria-label') || el.innerText || el.getAttribute('title') || '').trim();
                const dismissCopy = /^(?:close(?: menu| dialog| drawer)?|dismiss|fechar(?: menu| janela)?|cancel|cancelar)$/i.test(controlLabel);
                const destructiveDismiss = /discard|delete|erase|excluir|apagar|descartar|subscription|booking|cancel order|cancelar pedido/i.test(`${controlLabel} ${overlay?.innerText || ''}`);
                const hasEnteredData = Boolean(overlay && Array.from(overlay.querySelectorAll('input,textarea,select'))
                  .some((field) => !['hidden','submit','button','reset'].includes(field.type) && field.value));
                const enabled = !el.matches(':disabled')
                  && el.getAttribute('aria-disabled') !== 'true'
                  && !el.closest('[inert]');
                const ariaHiddenOwner = el.closest('[aria-hidden]');
                const navigationOwner = el.closest('nav,[role="navigation"],aside');
                const explicitContext = el.closest(
                  'tr,[role="row"],li,[role="listitem"],form,[role="dialog"],nav,[role="navigation"],aside'
                );
                // Modern component libraries often render cards as plain divs.
                // Prefer a nearby test-marked container, excluding the control
                // itself, so repeated labels such as "Add to cart" retain the
                // product/card identity that makes each action distinct.
                const dataContext = el.parentElement?.closest(
                  '[data-testid],[data-test],[data-qa]'
                ) || null;
                let boundedContext = null;
                if (!explicitContext && !dataContext) {
                  let parent = el.parentElement;
                  for (let depth = 0; parent && depth < 5; depth += 1, parent = parent.parentElement) {
                    if (['BODY', 'HTML', 'MAIN'].includes(parent.tagName)) continue;
                    const text = (parent.innerText || '').trim().replace(/\\s+/g, ' ');
                    const controls = parent.querySelectorAll(
                      'a,button,input,select,textarea,[aria-haspopup],[role="button"],[role="link"],[role="menuitem"]'
                    ).length;
                    if (text.length > 0 && text.length <= 500 && controls <= 12) {
                      boundedContext = parent;
                      break;
                    }
                  }
                }
                const contextNode = explicitContext || dataContext || boundedContext || el.closest('section,main');
                const contextText = contextNode
                  ? (contextNode.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 120)
                  : '';
                const contextMarker = contextNode
                  ? ['data-testid', 'data-test', 'data-qa']
                    .map((name) => contextNode.getAttribute(name) ? `${name}=${contextNode.getAttribute(name)}` : '')
                    .filter(Boolean)
                    .join(' ')
                  : '';
                return {
                  ref,
                  tag: el.tagName.toLowerCase(),
                  role: el.getAttribute('role') || null,
                  name: (el.getAttribute('aria-label') || el.innerText
                    || (el instanceof HTMLInputElement && ['submit','button','reset'].includes(el.type) ? el.value : '')
                    || el.getAttribute('placeholder') || el.getAttribute('name') || '').trim().slice(0, 300),
                  type: el.getAttribute('type') || null,
                  form: (el.form || el.closest('form,[role="form"]'))
                    ? selectorFor(el.form || el.closest('form,[role="form"]')) : null,
                  href: el instanceof HTMLAnchorElement ? el.href : null,
                  raw_href: el instanceof HTMLAnchorElement ? el.getAttribute('href') : null,
                  semantic_identity: semanticIdentity(el),
                  _node_guard: observedNodeGuard(el),
                  navigation_owner: navigationOwner ? `${navigationOwner.tagName.toLowerCase()} ${['id','data-testid','data-test','data-qa','aria-label'].map(attr => navigationOwner.getAttribute(attr) ? `${attr}=${navigationOwner.getAttribute(attr)}` : '').filter(Boolean).join(' ') || selectorFor(navigationOwner)}` : null,
                  enabled,
                  read_only: Boolean(el.readOnly),
                  required: Boolean(el.required || el.getAttribute('aria-required') === 'true'),
                  has_value: el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement
                    ? Boolean(el.value)
                    : null,
                  checked: el instanceof HTMLInputElement && ['checkbox', 'radio'].includes(el.type)
                    ? el.checked
                    : null,
                  selected_index: el instanceof HTMLSelectElement ? el.selectedIndex : null,
                  multiple: el instanceof HTMLSelectElement ? el.multiple : null,
                  selected_values: el instanceof HTMLSelectElement
                    ? Array.from(el.selectedOptions).slice(0, 100).map((option) => option.value)
                    : null,
                  aria_haspopup: el.getAttribute('aria-haspopup'),
                  aria_expanded: el.getAttribute('aria-expanded'),
                  aria_hidden: ariaHiddenOwner?.getAttribute('aria-hidden') || null,
                  aria_pressed: el.getAttribute('aria-pressed'),
                  aria_selected: el.getAttribute('aria-selected'),
                  aria_current: el.getAttribute('aria-current'),
                  options: el instanceof HTMLSelectElement
                    ? Array.from(el.options).slice(0, 100).map((option) => ({
                        value: option.value,
                        label: (option.label || option.textContent || '').trim().slice(0, 200),
                        disabled: option.disabled,
                        selected: option.selected,
                      }))
                    : null,
                  document_top: Math.round(box.top + window.scrollY),
                  in_viewport: interaction.inHorizontalViewport && interaction.inVerticalViewport,
                  in_horizontal_viewport: interaction.inHorizontalViewport,
                  interactable: enabled && interaction.interactable,
                  in_overlay: Boolean(overlay),
                  overlay_group: overlay ? selectorFor(overlay) : null,
                  obstructed_by_overlay: Boolean(obstructingOverlay && !overlay),
                  overlay_dismiss: Boolean(overlay && dismissCopy && !destructiveDismiss && !hasEnteredData),
                  in_navigation: Boolean(el.closest('nav,[role="navigation"],aside')),
                  context: contextNode
                    ? `${contextNode.tagName.toLowerCase()} ${contextMarker} ${contextNode.getAttribute('aria-label') || contextNode.getAttribute('role') || ''} ${contextText}`.trim().slice(0, 160)
                    : null,
                  _selector: selectorFor(el),
                };
              });
              const refFor = (node) => {
                const index = candidates.indexOf(node);
                return index >= 0 ? `e${index}` : null;
              };
              const eligible = (node) => Boolean(node && isVisible(node)
                && !node.closest(PANEL_SELECTOR)
                && (!activeDialog || activeDialog.contains(node)));
              const identityFor = (node) => {
                for (const attr of ['id', 'data-testid', 'data-test', 'data-qa', 'name']) {
                  const value = node.getAttribute(attr);
                  if (value) return `${node.tagName.toLowerCase()}:${attr}=${value}`;
                }
                return selectorFor(node);
              };
              const formNodes = Array.from(document.querySelectorAll('form,[role="form"]'))
                .filter(eligible);
              const formFor = (node) => node.form || node.closest('form,[role="form"]');
              const sensitiveField = (node) => node.type === 'password'
                || node.getAttribute('data-autojev-secret-mask') === 'true'
                || /password|passwd|senha|token|credential|authorization|authentication|username|user.name|login|session|secret|api.?key|one.time|\\botp\\b/.test(
                  [node.id, node.name, node.type, node.autocomplete,
                    node.getAttribute('data-test'), node.getAttribute('aria-label')]
                    .filter(Boolean).join(' ').toLowerCase());
              const fields = candidates.filter((node) => node.matches('input,textarea,select')
                && !['submit', 'button', 'hidden', 'reset'].includes(node.type)).map((node) => {
                  const formNode = formFor(node);
                  const form = formNode ? identityFor(formNode) : null;
                  const marker = node.getAttribute('data-test') || node.id || node.name || '';
                  // These three declared checkout fields belong to a required
                  // step even when the app implements validation in JavaScript.
                  const checkoutRequired = Boolean(node.closest('.checkout_info,[data-test="checkout-info"]'))
                    && ['firstName', 'lastName', 'postalCode', 'first-name', 'last-name', 'postal-code'].includes(marker);
                  return {
                    identity: `${form || 'page'}::${identityFor(node)}`,
                    ref: refFor(node), form,
                    label: (node.getAttribute('aria-label') || Array.from(node.labels || []).map((label) => label.innerText).join(' ')
                      || node.getAttribute('placeholder') || node.name || '').trim().slice(0, 200),
                    type: node.type || node.tagName.toLowerCase(),
                    required: Boolean(node.required || node.getAttribute('aria-required') === 'true' || checkoutRequired),
                    sensitive: sensitiveField(node),
                    has_value: Boolean(node.value),
                    enabled: !node.matches(':disabled') && node.getAttribute('aria-disabled') !== 'true',
                    read_only: Boolean(node.readOnly),
                    valid: node.validity ? node.validity.valid : node.getAttribute('aria-invalid') !== 'true',
                    validation_message: node.validationMessage || '',
                    selected_values: node instanceof HTMLSelectElement
                      ? Array.from(node.selectedOptions).map((option) => option.value) : null,
                    _raw_value: node.value || '',
                  };
                });
              const errorNodes = Array.from(document.querySelectorAll(
                '[role="alert"],[aria-live="assertive"],[data-test="error"],.error-message,.error_message,[data-testid="validation-error"]'
              )).filter(eligible);
              const errorText = (node) => (node.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 500);
              // Inline validation controls are transient: a later valid
              // submission removes them. Mark only explicit harmless buttons
              // inside an observed form-owned error, never a modal cancel or
              // a submit/reset that could discard entered data.
              elements.forEach((element, index) => {
                const control = candidates[index];
                const formNode = formFor(control);
                const label = (control.getAttribute('aria-label') || control.innerText || '').trim();
                const error = errorNodes.find(node => node.contains(control) && errorText(node));
                if (control.tagName === 'BUTTON' && control.type === 'button'
                    && formNode && error && formNode.contains(error)
                    && /^(?:dismiss(?: error)?|close(?: error| message)?|fechar(?: erro| mensagem)?)$/i.test(label)) {
                  element.validation_dismiss = true;
                  element.validation_form = identityFor(formNode);
                }
              });
              const isFormSubmit = (control) => {
                const label = (control.innerText || control.getAttribute('aria-label') || control.value || '').trim();
                // A button defaults to type=submit even when the site's handler
                // cancels or navigates away. Those actions must not be tested as
                // submissions requiring validation of the form's empty fields.
                if (/^(cancel|cancelar|voltar|back|close|fechar|retornar)\\b/i.test(label)) return false;
                return control.type === 'submit'
                  || /^(continue|continuar|next|próximo|enviar|salvar|save|submit)$/i.test(label);
              };
              const forms = formNodes.map((node) => ({
                id: identityFor(node),
                field_ids: fields.filter((field) => field.form === identityFor(node)).map((field) => field.identity),
                submit_refs: candidates.filter((control) => formFor(control) === node && isFormSubmit(control)).map(refFor),
                validation_errors: errorNodes.filter((error) => node.contains(error)).map(errorText).filter(Boolean),
              }));
              const firstText = (node, selector) => {
                const child = node.querySelector(selector);
                return child && eligible(child) ? (child.innerText || child.getAttribute('content') || '').trim().slice(0, 300) : '';
              };
              const productName = '[data-test="inventory-item-name"],.inventory_item_name,.inventory_details_name,[data-testid="product-name"],[itemprop="name"],.product-name';
              const productPrice = '[data-test="inventory-item-price"],.inventory_item_price,.inventory_details_price,[data-testid="product-price"],[itemprop="price"],.product-price';
              const productInfo = (node) => {
                const name = firstText(node, productName);
                const entityOwner = node.closest('[data-product-id],[data-item-id]');
                const productId = entityOwner && entityOwner.getAttribute('data-product-id');
                const itemId = entityOwner && entityOwner.getAttribute('data-item-id');
                const image = Array.from(node.querySelectorAll('img')).find(eligible);
                const controls = candidates.filter((control) => node.contains(control));
                const cartControls = controls.filter((control) => /add.*cart|adicionar.*carrinho|remove|remover/i.test(
                  `${control.innerText || ''} ${control.getAttribute('aria-label') || ''}`));
                const cartControl = cartControls[0];
                const cartLabel = cartControl ? `${cartControl.innerText || ''} ${cartControl.getAttribute('aria-label') || ''}` : '';
                return {
                  id: productId || itemId || (name ? `name:${name}` : identityFor(node)),
                  identity_source: productId ? 'declared_product_id'
                    : itemId ? 'declared_item_id'
                    : name ? 'observed_name_id' : 'dom_structure',
                  name, price: firstText(node, productPrice),
                  quantity: firstText(node, '.cart_quantity,[data-test="item-quantity"],[data-testid="quantity"],[itemprop="quantity"]') || '1',
                  description: firstText(node, '[data-test="inventory-item-desc"],.inventory_item_desc,.inventory_details_desc,[itemprop="description"],[data-testid="product-description"]'),
                  image_alt: image ? (image.alt || '').slice(0, 300) : null,
                  image_src: image ? (image.currentSrc || image.src || '') : null,
                  image_loaded: image ? Boolean(image.complete && image.naturalWidth > 0) : null,
                  image_complete: image ? image.complete : null,
                  natural_width: image ? image.naturalWidth : null,
                  action_refs: cartControls.map(refFor),
                  link_refs: controls.filter((control) => control.tagName === 'A' || control.getAttribute('role') === 'link').map(refFor),
                  cart_action: /add.*cart|adicionar.*carrinho/i.test(cartLabel) ? 'add'
                    : /remove|remover/i.test(cartLabel) ? 'remove' : null,
                };
              };
              const detailNode = Array.from(document.querySelectorAll(
                '.inventory_details,[data-test="inventory-details"],[data-testid="product-detail"]'
              )).find((node) => eligible(node)
                && !node.matches('header,nav,[role="banner"],[role="navigation"]')
                && Boolean(firstText(node, productName)));
              const productNodes = Array.from(document.querySelectorAll(
                '.inventory_item,[data-test="inventory-item"],[data-testid="product-card"],[data-product-id],[data-item-id],[itemtype$="/Product"]'
              )).filter((node) => eligible(node)
                && !node.closest('.cart_item,[data-test="cart-item"],[data-testid="cart-item"],.cart_list,[data-test="cart-list"],[data-testid="cart-items"]')
                // A declared entity may wrap a product detail rather than a
                // catalog card. Report that physical surface only as detail,
                // so one Add/Remove is not mistaken for two mounted controls.
                && (!detailNode || !(node === detailNode || node.contains(detailNode) || detailNode.contains(node))));
              const products = productNodes.filter((node) => !productNodes.some((other) => other !== node && other.contains(node)))
                .map(productInfo).filter((item) => item.name);
              const cartNodes = Array.from(document.querySelectorAll(
                '.cart_item,[data-test="cart-item"],[data-testid="cart-item"],:is(.cart_list,[data-test="cart-list"],[data-testid="cart-items"]) :is(.inventory_item,[data-test="inventory-item"],[data-testid="product-card"],[data-product-id],[data-item-id],[itemtype$="/Product"])'
              )).filter(eligible).filter((node, _, nodes) => !nodes.some(other => other !== node && other.contains(node)));
              const cartList = Array.from(document.querySelectorAll(
                '.cart_list,[data-test="cart-list"],[data-testid="cart-items"]'
              )).find((node) => {
                if (eligible(node)) return true;
                // An explicitly rendered empty list often has zero height.
                // That is an observable empty cart, not unknown membership.
                if (node.closest(PANEL_SELECTOR) || (activeDialog && !activeDialog.contains(node))
                  || node.closest('[aria-hidden="true"]') || node.getBoundingClientRect().width <= 0) return false;
                for (let owner=node; owner; owner=owner.parentElement) {
                  const style=getComputedStyle(owner);
                  if (style.display==='none' || ['hidden','collapse'].includes(style.visibility)
                    || Number.parseFloat(style.opacity || '1') <= 0) return false;
                }
                return true;
              });
              const cartLink = Array.from(document.querySelectorAll(
                '.shopping_cart_link,[data-test="shopping-cart-link"],[data-testid="cart-link"]'
              )).find(eligible);
              const cartBadge = Array.from(document.querySelectorAll(
                '.shopping_cart_badge,[data-test="shopping-cart-badge"],[data-testid="cart-count"],[aria-label="Cart count"]'
              )).find(eligible);
              const badgeText = cartBadge ? (cartBadge.innerText || '').trim() : '';
              const reviewNode = Array.from(document.querySelectorAll(
                '.summary_info,[data-test="checkout-summary-container"],[data-testid="checkout-review"]'
              )).find(eligible);
              const confirmationNode = Array.from(document.querySelectorAll(
                '.checkout_complete_container,[data-test="checkout-complete-container"],[data-testid="order-confirmation"]'
              )).find(eligible);
              const completionText = confirmationNode ? firstText(confirmationNode,
                '.complete-header,[data-test="complete-header"],[data-testid="confirmation-heading"],h1,h2'
              ) : '';
              const reviewItems = cartNodes.map(productInfo).filter((item) => item.name);
              const optionalTotal = (selector) => reviewNode ? firstText(reviewNode, selector) || null : null;
              const page_state = {
                fields, forms, products,
                product_detail: detailNode ? productInfo(detailNode) : null,
                cart: {
                  count: /^\\d+$/.test(badgeText) ? Number(badgeText) : cartLink ? 0 : null,
                  items: cartNodes.map(productInfo).filter((item) => item.name),
                  items_known: Boolean(cartList),
                  link_refs: cartLink ? [refFor(cartLink)].filter(Boolean) : [],
                },
                checkout: reviewNode ? {
                  stage: 'review', items: reviewItems, items_known: Boolean(cartList),
                  subtotal: optionalTotal('.summary_subtotal_label,[data-test="subtotal-label"],[data-testid="subtotal"]'),
                  tax: optionalTotal('.summary_tax_label,[data-test="tax-label"],[data-testid="tax"]'),
                  shipping: optionalTotal('[data-test="shipping-total"],[data-testid="shipping-total"]'),
                  discount: optionalTotal('[data-test="discount-total"],[data-testid="discount-total"]'),
                  total: optionalTotal('.summary_total_label,[data-test="total-label"],[data-testid="total"]'),
                  finish_refs: candidates.filter((control) => /^(finish|place order|complete order|finalizar pedido|confirmar pedido)$/i.test(
                    (control.innerText || control.value || control.getAttribute('aria-label') || '').trim()
                  )).map(refFor),
                  arithmetic_scope: 'declared_components_only',
                } : confirmationNode ? {
                  stage: 'complete', confirmation_text: completionText,
                  confirmed: /thank you for your order|order (?:placed|confirmed|complete)|pedido (?:confirmado|concluído)|obrigad[oa].{0,40}pedido/i.test(completionText),
                } : null,
                validation_errors: errorNodes.map(errorText).filter(Boolean),
              };
              const textRoot = activeDialog || document.body;
              const textParts = [];
              let textLength = 0;
              const walker = document.createTreeWalker(textRoot, NodeFilter.SHOW_TEXT);
              let textNode;
              while ((textNode = walker.nextNode()) && textLength < 12000) {
                const parent = textNode.parentElement;
                const value = (textNode.nodeValue || '').trim();
                if (!parent || !value || parent.closest(PANEL_SELECTOR)) continue;
                if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE'].includes(parent.tagName)) continue;
                if (!isVisible(parent)) continue;
                textParts.push(value);
                textLength += value.length + 1;
              }
              const visible = eligible;
              __LOGIN_CHALLENGE_JS__
              page_state.authentication_challenge = authenticationChallenge;
              return {
                title: document.title || '',
                text: textParts.join(' ').replace(/\\s+/g, ' ').slice(0, 12000),
                elements,
                page_state,
              };
            }""")),
        )
        screenshot = await self._capture(screenshot_label)
        console_errors = list(self._console_buffer)
        network_errors = list(self._network_buffer)
        self._console_buffer.clear()
        self._network_buffer.clear()
        elements = snapshot.get("elements", [])
        self._ref_selectors = {}
        self._ref_semantics = {}
        for element in elements:
            if isinstance(element, dict):
                selector = element.pop("_selector", None)
                node_guard = element.pop("_node_guard", None)
                if element.get("ref") and isinstance(selector, str) and selector:
                    self._ref_selectors[str(element["ref"])] = selector
                element["name"] = self._redact_text(str(element.get("name") or ""))
                if element.get("context"):
                    element["context"] = self._redact_text(
                        str(element.get("context") or "")
                    )[:160]
                # An <a> without href exposes an empty DOM property. Normalize
                # that exactly like the pre-action stale check (None), otherwise
                # a valid button-like link is rejected as if it had rerendered.
                element["href"] = (
                    self._public_url(str(element["href"]))
                    if element.get("href")
                    else None
                )
                if isinstance(element.get("raw_href"), str):
                    raw_href = element["raw_href"]
                    # Preserve the bare '#' anchor distinction without leaking
                    # credential-bearing URL fragments into model inputs.
                    element["raw_href"] = raw_href if raw_href in {"", "#"} else self._public_url(raw_href)
                if element.get("navigation_owner"):
                    element["navigation_owner"] = self._redact_text(str(element["navigation_owner"]))[:300]
                if element.get("semantic_identity"):
                    element["semantic_identity"] = self._redact_text(str(element["semantic_identity"]))
                if element.get("ref"):
                    self._ref_semantics[str(element["ref"])] = {
                        "tag": str(element.get("tag") or "").lower(),
                        "role": element.get("role"),
                        "type": element.get("type"),
                        "name": element["name"],
                        "href": element.get("href"),
                        "aria_haspopup": element.get("aria_haspopup"),
                    }
                    if isinstance(node_guard, dict):
                        self._ref_semantics[str(element["ref"])]["_node_guard"] = node_guard
        page_state = self._sanitize_page_state(snapshot.get("page_state", {}))
        if self._document_response:
            page_state["document_response"] = dict(self._document_response)
        self._observe_cart_expectation(page_state)
        self._last_page_state = copy.deepcopy(page_state)
        field_by_ref = {item.get("ref"): item for item in page_state.get("fields", [])}
        for element in elements:
            field = field_by_ref.get(element.get("ref"))
            if field:
                element["required"] = bool(field.get("required"))
        # Cart removal is reversible setup, not deletion of application data.
        # Grant this narrow semantic hint only for observed named products;
        # the executor independently checks the live DOM again before clicking.
        products = [*page_state.get("products", []), *(page_state.get("cart") or {}).get("items", [])]
        if isinstance(page_state.get("product_detail"), dict):
            products.append(page_state["product_detail"])
        removal_refs = {
            ref for product in products if product.get("name") and product.get("cart_action") == "remove"
            for ref in product.get("action_refs", [])
        }
        for element in elements:
            if element.get("ref") in removal_refs and re.fullmatch(
                r"(?:remove(?: from cart)?|remover(?: do carrinho)?)", str(element.get("name") or ""), re.IGNORECASE,
            ):
                element["reversible_cart_action"] = "remove"
        return Observation(
            url=self._public_url(self._page.url),
            title=self._redact_text(str(snapshot.get("title", ""))),
            text=self._redact_text(str(snapshot.get("text", ""))),
            interactive_elements=elements,
            console_errors=console_errors,
            network_errors=network_errors,
            screenshot=screenshot,
            page_state=page_state,
        )

    async def _locator(self, ref: str | None) -> Any:
        if not ref or not ref.startswith("e") or not ref[1:].isdigit():
            raise ValueError("Action requires a valid observed element ref")
        selector = self._ref_selectors.get(ref)
        if not selector:
            raise ValueError("Observed element is no longer attached to the page")
        locator = self._page.locator(selector)
        if await locator.count() != 1:
            raise StaleObservedControlError("Observed element is no longer uniquely addressable; re-observation is required")
        observed = self._ref_semantics.get(ref)
        handle = None
        if observed and observed.get("_node_guard"):
            # Pin the resolved node: a later DOM reorder must not make click()
            # re-resolve the positional CSS selector onto another product.
            handle = await locator.element_handle(timeout=2_000)
            if handle is None:
                raise StaleObservedControlError("Observed element changed; re-observation is required")
            self._observed_element_handles.append(handle)
        if observed:
            actual = await (handle or locator).evaluate(
                self._observed_node_guard_script("""el => {
                  __OBSERVED_NODE_GUARD_JS__
                  return {
                  tag: el.tagName.toLowerCase(),
                  role: el.getAttribute('role'),
                  type: el.getAttribute('type'),
                  name: (el.getAttribute('aria-label') || el.innerText
                    || (el instanceof HTMLInputElement && ['submit','button','reset'].includes(el.type) ? el.value : '')
                    || el.getAttribute('placeholder') || el.getAttribute('name') || '').trim().slice(0, 300),
                  href: el instanceof HTMLAnchorElement ? el.href : null,
                  aria_haspopup: el.getAttribute('aria-haspopup'),
                  _node_guard: observedNodeGuard(el),
                }; }""")
            )
            actual_guard = actual.get("_node_guard")
            actual = {
                "tag": str(actual.get("tag") or "").lower(),
                "role": actual.get("role"),
                "type": actual.get("type"),
                "name": self._redact_text(str(actual.get("name") or "")),
                "href": self._public_url(str(actual["href"]))
                if actual.get("href")
                else None,
                "aria_haspopup": actual.get("aria_haspopup"),
            }
            if observed.get("_node_guard"):
                actual["_node_guard"] = actual_guard
            if actual != observed:
                raise StaleObservedControlError(
                    "Observed element changed after the decision; re-observation is required"
                )
        return _PinnedObservedElement(handle) if handle is not None else locator

    def _on_request_started(self, request: Any) -> None:
        # Only application API work is relevant to business-result settling.
        # Third-party telemetry/assets and requests from before this action
        # must not keep the explorer waiting for global network idleness.
        try:
            if (
                request.resource_type in {"fetch", "xhr"}
                and self._origin(request.url) == self._allowed_origin
                and len(self._pending_app_requests) < 500
            ):
                self._pending_app_requests[request] = time.monotonic()
        except (AttributeError, TypeError, ValueError):
            return

    def _on_request_settled(self, request: Any) -> None:
        self._pending_app_requests.pop(request, None)

    async def _wait_for_action_requests(
        self, since: float, timeout_ms: int = 5_000,
    ) -> bool:
        """Wait a bounded window for API effects initiated by this action."""
        deadline = time.monotonic() + timeout_ms / 1000
        while any(started >= since for started in self._pending_app_requests.values()):
            if not self.is_active():
                return False
            if time.monotonic() >= deadline:
                return False
            await self._page.wait_for_timeout(50)
        return True

    async def _wait_for_dom_settle(self, timeout_ms: int = 1_200) -> None:
        """Wait for a short stable DOM window after an action, bounded by timeout."""

        if not self._page:
            return
        stable_samples = 0
        previous: str | None = None
        samples = max(timeout_ms // 150, 1)
        for _ in range(samples):
            if not self._page or self._page.is_closed():
                return
            try:
                signature = await self._page.evaluate(
                    """() => JSON.stringify({
                      url: location.href,
                      title: document.title,
                      text: (document.body?.innerText || '').slice(0, 4000),
                      controls: Array.from(document.querySelectorAll(
                        'a,button,input,select,textarea,[aria-haspopup],[role="button"],[role="link"],[role="menuitem"],[role="checkbox"],[role="radio"],[tabindex]'
                      )).slice(0, 400).map((el) => [
                        el.tagName,
                        el.getAttribute('role'),
                        el.getAttribute('aria-expanded'),
                        el.getAttribute('aria-selected'),
                        el instanceof HTMLSelectElement ? el.selectedIndex : null,
                        el instanceof HTMLInputElement && ['checkbox', 'radio'].includes(el.type) ? el.checked : null,
                      ]),
                    })"""
                )
            except Exception:
                return
            if signature == previous:
                stable_samples += 1
                if stable_samples >= 3:
                    return
            else:
                stable_samples = 0
                previous = signature
            await self._page.wait_for_timeout(150)

    async def _enforce_safe_interaction(self, locator: Any) -> None:
        # Finishing a checkout can create a real order even when the button's
        # innocent label is simply "Finish". Demo/reserved local test origins
        # are explicitly safe; ordinary production origins fail closed.
        try:
            order_submission = await locator.evaluate("""el => {
              const label = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
              return /^(place order|complete order|confirm order|finalizar pedido|confirmar pedido)$/i.test(label)
                || (/^finish$/i.test(label) && Boolean(document.querySelector(
                  '.summary_info,[data-test="checkout-summary-container"],[data-testid="checkout-review"]'
                )));
            }""")
        except Exception:
            order_submission = False
        if order_submission is True:
            hostname = (urlsplit(self._page.url).hostname or "").casefold() if self._page else ""
            sandbox = hostname in {"saucedemo.com", "www.saucedemo.com", "localhost", "127.0.0.1", "::1"} or hostname.endswith(".test")
            if not sandbox:
                raise PermissionError("PolicyBlocked: submitting a real order requires explicit sandbox authorization")
        destructive_words = {
            "account security",
            "admin role",
            "bank",
            "banking",
            "card",
            "cartao",
            "cartão",
            "change password",
            "delete",
            "remove",
            "erase",
            "purchase",
            "buy now",
            "pay now",
            "excluir",
            "remover",
            "apagar",
            "comprar",
            "pagar",
            "confirmar pagamento",
            "permissao",
            "permissão",
            "permission",
            "pix",
            "role",
            "security",
            "senha",
            "withdraw",
            "saque",
        }
        destructive_phrases = (
            r"\bcancel\s+(?:(?:the|your|an?)\s+)?(?:subscription|order|booking)\b",
            r"\bcancelar\s+(?:(?:a|o|sua|seu)\s+)?(?:assinatura|pedido|reserva)\b",
        )
        try:
            form_text = await locator.evaluate(
                """el => {
                  const context = el.closest('form,[role="dialog"]');
                  if (!context) return '';
                  return `${context.innerText || ''} ${context.getAttribute('aria-label') || ''} ${context.getAttribute('action') || ''}`;
                }"""
            )
            text = " ".join(
                filter(
                    None,
                    [
                        await locator.inner_text(timeout=500),
                        await locator.get_attribute("aria-label", timeout=500),
                        await locator.get_attribute("title", timeout=500),
                        await locator.get_attribute("placeholder", timeout=500),
                        await locator.get_attribute("name", timeout=500),
                        str(form_text or ""),
                    ],
                )
            ).strip().lower()
        except Exception:
            text = ""
        if re.search(r"\b(?:remove|remover)\b", text):
            cart_removal = await locator.evaluate(
                """el => {
                  const label = (el.getAttribute('aria-label') || el.innerText || '').trim();
                  if (!/^(remove( from cart)?|remover( do carrinho)?)$/i.test(label)) return false;
                  const card = el.closest('.inventory_item,.inventory_details,.cart_item,[data-test="inventory-item"],[data-test="inventory-details"],[data-test="cart-item"],[data-testid="product-card"],[data-testid="product-detail"],[data-testid="cart-item"],[data-product-id],[itemtype$="/Product"]');
                  const name = card?.querySelector('[data-test="inventory-item-name"],.inventory_item_name,.inventory_details_name,[data-testid="product-name"],[itemprop="name"],.product-name');
                  return Boolean(name && name.textContent.trim());
                }"""
            )
            if cart_removal is True:
                destructive_words -= {"remove", "remover"}
        has_destructive_word = any(
            re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text)
            for word in destructive_words
        )
        has_destructive_phrase = any(
            re.search(pattern, text) for pattern in destructive_phrases
        )
        if has_destructive_word or has_destructive_phrase:
            raise PermissionError(
                "PolicyBlocked: destructive or transactional clicks are disabled"
            )

    async def execute(
        self, decision: AgentDecision, credentials: dict[str, str]
    ) -> tuple[ActionResult, Observation]:
        if not self.is_active():
            raise BrowserUnavailableError("Browser page is not active")
        started = utc_now()
        before_url = self._public_url(self._page.url)
        success = False
        message = ""
        recorded_value = decision.value
        execution_evidence: dict[str, Any] = {}
        select_preview_context: tuple[Any, str, Any, dict[str, Any]] | None = None
        action_started_monotonic = time.monotonic()
        self._blocked_navigation_url = None
        await self._set_sidepanel_automation(True)
        try:
            action = decision.action
            if action == "navigate":
                if not decision.url:
                    raise ValueError("navigate requires url")
                self._validate_url(decision.url)
                if self._origin(decision.url) != self._allowed_origin:
                    raise PermissionError("PolicyBlocked: cross-origin navigation is disabled")
                response = await self._page.goto(decision.url, wait_until="domcontentloaded", timeout=30_000)
                if response is not None:
                    execution_evidence["navigation_response"] = self._navigation_metadata(response)
            elif action == "click":
                locator = await self._locator(decision.target)
                await self._enforce_safe_interaction(locator)
                pages_before = set(self._context.pages)
                self._pending_pages.clear()
                opens_new_tab = (
                    await locator.get_attribute("target", timeout=500) == "_blank"
                )
                preview = await self._capture_action_preview(locator, decision)
                if preview is not None:
                    execution_evidence["action_preview"] = preview
                await locator.click(timeout=10_000)
                # Same-origin links that open a new tab are part of the tested
                # application. Adopt the tab so observations and JEV decisions
                # continue there; the page stack lets go_back close it and
                # resume the exact parent state. External popups remain blocked.
                await self._page.wait_for_timeout(100)
                if opens_new_tab and not self._pending_pages:
                    # target=_blank is explicit; allow a small event window for
                    # a delayed popup without slowing ordinary clicks.
                    for _ in range(4):
                        await self._page.wait_for_timeout(100)
                        if self._pending_pages:
                            break
                new_pages = [
                    page
                    for page in [*self._pending_pages, *self._context.pages]
                    if page not in pages_before and not page.is_closed()
                ]
                if new_pages:
                    popup = new_pages[-1]
                    # A newly created target starts at about:blank before its
                    # first navigation is committed. Waiting only for the load
                    # state can resolve against that transient document, so
                    # first wait explicitly for a real URL.
                    for _ in range(50):
                        if popup.url != "about:blank":
                            break
                        await popup.wait_for_timeout(100)
                    try:
                        await popup.wait_for_load_state("domcontentloaded", timeout=5_000)
                    except Exception:
                        pass
                    if (
                        self._blocked_navigation_url
                        or popup.url == "about:blank"
                        or self._origin(popup.url) != self._allowed_origin
                    ):
                        await popup.close()
                        raise PermissionError(
                            "PolicyBlocked: cross-origin or unresolved popup navigation is disabled"
                        )
                    await self._prepare_page(popup)
                    self._page = popup
                    self._page_stack.append(popup)
            elif action == "fill":
                locator = await self._locator(decision.target)
                await self._enforce_safe_interaction(locator)
                if decision.credential_key:
                    if decision.credential_key not in credentials:
                        raise KeyError(f"Credential alias {decision.credential_key!r} is unavailable")
                    fill_value = credentials[decision.credential_key]
                    recorded_value = f"[credential:{decision.credential_key}]"
                elif decision.value is not None:
                    fill_value = decision.value
                else:
                    raise ValueError("fill requires value or credential_key")
                field = self._field_semantics.get(str(decision.target))
                if field and not field.get("sensitive") and not decision.credential_key:
                    execution_evidence["business_input"] = {
                        "identity": field.get("identity"),
                        "expected_token": self._field_value_token(fill_value),
                    }
                preview = await self._capture_action_preview(locator, decision)
                if preview is not None:
                    execution_evidence["action_preview"] = preview
                await locator.fill(fill_value, timeout=10_000)
                if decision.credential_key:
                    await locator.evaluate(
                        "el => el.setAttribute('data-autojev-secret-mask', 'true')"
                    )
            elif action == "select":
                if decision.value is None:
                    raise ValueError("select requires value")
                locator = await self._locator(decision.target)
                await self._enforce_safe_interaction(locator)
                field = self._field_semantics.get(str(decision.target))
                if field and not field.get("sensitive"):
                    execution_evidence["business_input"] = {
                        "identity": field.get("identity"),
                        "expected_token": self._field_value_token(decision.value),
                    }
                preview = await self._capture_action_preview(locator, decision)
                if preview is not None:
                    execution_evidence["action_preview"] = preview
                    # Retain the checked node, not a selector that could later
                    # resolve to a replacement control. Optional label reads
                    # must never prevent dispatch of the authorized action.
                    if not (field or {}).get("sensitive") and not any(
                        secret in decision.value for secret in self.secret_values
                    ):
                        try:
                            label_target = (
                                await locator.element_handle()
                                if hasattr(locator, "element_handle") else locator
                            )
                            snapshot = await self._select_label_snapshot(label_target, decision.value)
                            if snapshot is not None:
                                selection: dict[str, Any] = {
                                    "version": "select-labels-v1", "status": "unverified",
                                }
                                for source, dest in (
                                    ("requested_label", "requested_label"),
                                    ("selected_label", "before_label"),
                                ):
                                    if snapshot[source]:
                                        selection[dest] = snapshot[source]
                                preview["selection"] = selection
                                select_preview_context = (
                                    self._page, self._page.url, label_target, selection,
                                )
                        except Exception:
                            pass
                await locator.select_option(decision.value, timeout=10_000)
            elif action == "press":
                locator = await self._locator(decision.target)
                key = decision.key or "Enter"
                if "enter" in key.lower():
                    await self._enforce_safe_interaction(locator)
                preview = await self._capture_action_preview(locator, decision)
                if preview is not None:
                    execution_evidence["action_preview"] = preview
                await locator.press(key, timeout=10_000)
            elif action == "hover":
                locator = await self._locator(decision.target)
                preview = await self._capture_action_preview(locator, decision)
                if preview is not None:
                    execution_evidence["action_preview"] = preview
                await locator.hover(timeout=10_000)
            elif action == "go_back":
                if (
                    self._protect_authenticated_history
                    and self._authenticated_history_root_url is not None
                    and self._history_url_identity(self._page.url)
                    == self._authenticated_history_root_url
                ):
                    # Do not briefly expose the login document and then move
                    # forward again. Apart from the visible flash, that round
                    # trip can run page startup effects and invalidate the
                    # authenticated DOM state the planner is trying to keep.
                    raise PermissionError(
                        "PolicyBlocked: browser back would cross the authenticated session boundary"
                    )
                before_back_url = self._page.url
                response = await self._page.go_back(
                    wait_until="domcontentloaded", timeout=30_000
                )
                if (
                    response is None
                    and self._page.url == before_back_url
                    and len(self._page_stack) > 1
                ):
                    popup = self._page_stack.pop()
                    self._page = self._page_stack[-1]
                    await popup.close()
                if (
                    self._protect_authenticated_history
                    and self._page
                    and await self.is_login_page()
                ):
                    # The previous browser-history entry can still be the
                    # authentication form even though the coverage parent is an
                    # authenticated DOM state. Restore the forward entry and
                    # report a policy failure so the planner can use its known
                    # route/replay strategy instead of asking for login again.
                    try:
                        await self._page.go_forward(
                            wait_until="domcontentloaded", timeout=30_000
                        )
                    except Exception:
                        pass
                    raise PermissionError(
                        "PolicyBlocked: browser back would cross the authenticated session boundary"
                    )
            elif action == "reload":
                await self._page.reload(wait_until="domcontentloaded", timeout=30_000)
            elif action == "wait":
                milliseconds = min(max(int(decision.value or "500"), 0), 5_000)
                await self._page.wait_for_timeout(milliseconds)
            elif action == "screenshot":
                pass
            else:
                raise ValueError(f"Unsupported executor action: {action}")
            try:
                await self._page.wait_for_load_state("domcontentloaded", timeout=3_000)
            except Exception:
                pass
            # SPA/API updates often land after the action promise resolves.
            # Observe only after a short stable DOM window so late controls are
            # not missed and refs do not point into a transient render.
            if action not in {"wait", "screenshot"}:
                requests_settled = await self._wait_for_action_requests(action_started_monotonic)
                if not requests_settled:
                    execution_evidence["business_settle"] = {
                        "status": "api_wait_timed_out",
                        "timeout_ms": 5_000,
                        "message": "Application API work remained pending at observation time.",
                    }
                await self._wait_for_dom_settle()
            if self._origin(self._page.url) != self._allowed_origin:
                raise PermissionError("PolicyBlocked: action navigated to another origin")
            if self._blocked_navigation_url:
                raise PermissionError(
                    "PolicyBlocked: cross-origin navigation was blocked before request"
                )
            if select_preview_context is not None:
                selection_page, selection_url, label_target, selection = select_preview_context
                if self._page is selection_page and self._page.url == selection_url:
                    snapshot = await self._select_label_snapshot(label_target, decision.value)
                    if snapshot is not None:
                        if snapshot["selected_label"]:
                            selection["after_label"] = snapshot["selected_label"]
                        selection["status"] = "confirmed" if snapshot["matches"] else "mismatch"
            success = True
            message = f"Playwright executed {action} successfully"
            if execution_evidence.get("business_settle"):
                message += "; resultado de negócio indisponível: a API ainda estava pendente após a espera limitada"
        except Exception as exc:
            if isinstance(exc, StaleObservedControlError):
                execution_evidence["action_dispatch"] = {
                    "status": "not_executed", "reason": "stale_observed_control",
                    "retryable": True,
                }
            message = self._redact_text(f"{exc.__class__.__name__}: {exc}")[:2000]
        finally:
            await self._set_sidepanel_automation(False)

        assertion_results = await self._evaluate_assertions(decision.assertions)
        cart_intent = None
        previous_cart_expectation = None
        previous_cart_state = None
        if success and decision.action == "click" and not execution_evidence.get("business_settle"):
            previous_cart_expectation = copy.deepcopy(self._cart_expectation)
            previous_cart_state = copy.deepcopy(self._last_page_state)
            cart_intent = self._remember_cart_action(decision)
            if cart_intent:
                execution_evidence["business_cart_intent"] = cart_intent
        observation = await self.observe(decision.action)
        if cart_intent and previous_cart_expectation is not None and previous_cart_state is not None:
            self._retain_cart_baseline_for_noop(
                cart_intent, previous_cart_expectation, previous_cart_state, observation.page_state,
            )
        result = ActionResult(
            action=decision.action,
            success=success,
            started_at=started,
            finished_at=utc_now(),
            before_url=before_url,
            after_url=self._public_url(self._page.url),
            target=decision.target,
            value=recorded_value,
            message=message,
            assertions=assertion_results,
            screenshot=observation.screenshot,
            evidence=execution_evidence,
        )
        return result, observation

    async def _evaluate_assertions(
        self, assertions: list[dict[str, Any]]
    ) -> list[AssertionResult]:
        results: list[AssertionResult] = []
        for assertion in assertions:
            kind = assertion["kind"]
            expected = assertion["expected"]
            actual: Any = None
            passed = False
            try:
                if kind == "url_contains":
                    actual = self._public_url(self._page.url)
                    passed = str(expected) in actual
                elif kind == "url_equals":
                    actual = self._public_url(self._page.url)
                    passed = actual == str(expected)
                elif kind == "title_contains":
                    actual = await self._page.title()
                    passed = str(expected).lower() in actual.lower()
                elif kind == "text_contains":
                    actual = (await self._page.locator("body").inner_text())[:12000]
                    actual = self._redact_text(actual)
                    passed = str(expected).lower() in actual.lower()
                elif kind == "element_visible":
                    locator = await self._locator(str(expected))
                    actual = bool(await locator.is_visible(timeout=2_000))
                    passed = actual
            except Exception as exc:
                actual = self._redact_text(f"{exc.__class__.__name__}: {exc}")[:500]
                passed = False
            results.append(
                AssertionResult(
                    kind=kind,
                    expected=expected,
                    actual=actual,
                    passed=passed,
                    message="Observed" if passed else "Expected evidence was not observed",
                )
            )
        return results

    async def close(self) -> None:
        if self._rerun_panel is not None:
            self._rerun_panel.close()
        await self._release_observed_element_handles()
        for resource in (self._context, self._browser):
            if resource:
                try:
                    await resource.close()
                except Exception:
                    pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self._page = self._context = self._browser = self._playwright = None
        self._page_stack = []
        self._pending_pages.clear()
        self._ref_selectors.clear()
        self._ref_semantics.clear()
        self._authenticated_history_root_url = None
        self._mark_inactive()
        self._sidepanel_init_installed = False
        self.secret_values.clear()
