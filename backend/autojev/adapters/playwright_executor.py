from __future__ import annotations

import asyncio
import json
import re
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

from autojev.domain.models import (
    ActionResult,
    AgentDecision,
    AssertionResult,
    ExplorationOptions,
    Observation,
    utc_now,
)


class BrowserUnavailableError(RuntimeError):
    pass


class PlaywrightExecutor:
    """The sole component permitted to perform browser actions."""

    # IDs de UI própria injetada no site. Nunca entram no texto ou nos
    # candidatos da observação entregues ao JEV.
    PANEL_SELECTOR = "#autojev-sidepanel,#autojev-intervention-panel,#autojev-live-panel"

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
        self._screenshot_number = 0
        self._allowed_origin: tuple[str, str, int] | None = None
        self._blocked_navigation_url: str | None = None
        self._ref_selectors: dict[str, str] = {}
        self._ref_semantics: dict[str, dict[str, Any]] = {}
        self._page_stack: list[Any] = []
        self._pending_pages: deque[Any] = deque(maxlen=20)
        self._user_input_event = asyncio.Event()
        self._intervention_message = ""
        self._protect_authenticated_history = False
        self._authenticated_history_root_url: str | None = None
        self._inactive_event = asyncio.Event()
        self._sidepanel_init_installed = False

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
        page.on("console", self._on_console)
        page.on("pageerror", self._on_page_error)
        page.on("response", self._on_response)
        page.on("requestfailed", self._on_request_failed)
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
            await self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            try:
                await self._page.wait_for_load_state("load", timeout=5_000)
            except Exception:
                pass
            # Give hydrated SPAs one short frame budget to attach click
            # handlers before the first observed refs can be executed.
            await self._page.wait_for_timeout(250)
            return await self.observe("initial")
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

    async def show_intervention_panel(self, message: str) -> None:
        """Mostra a seção de ação (botão Continuar) mantendo o log visível."""
        if not self._page or self.headless:
            return
        self._intervention_message = message
        if not await self._ensure_sidepanel():
            return
        try:
            await self._page.evaluate(
                """(message) => {
                  const panel = document.getElementById('autojev-sidepanel');
                  const action = document.getElementById('autojev-panel-action');
                  const text = document.getElementById('autojev-action-text');
                  const button = document.getElementById('autojev-action-button');
                  const kicker = document.getElementById('autojev-kicker-text');
                  const hint = document.getElementById('autojev-panel-hint');
                  if (text) text.textContent = message;
                  if (action) action.hidden = false;
                  if (button) { button.disabled = false; button.textContent = 'Continuar exploração'; }
                  if (kicker) kicker.textContent = 'AUTOJEV · AÇÃO NECESSÁRIA';
                  if (hint) hint.textContent = 'Aguardando você';
                  if (panel) {
                    panel.dataset.mode = 'attention';
                    panel.setAttribute('aria-live', 'assertive');
                  }
                  const title = document.getElementById('autojev-panel-title');
                  if (title) title.textContent = 'Faça login para continuar';
                }""",
                message,
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
                  if (hint) hint.textContent = savedPinned ? 'Clique no J para recolher' : 'Clique no J para abrir';
                  if (panel) {
                    panel.dataset.mode = 'exploring';
                    panel.dataset.pinned = savedPinned ? 'true' : 'false';
                    panel.setAttribute('aria-live', 'polite');
                  }
                  const rail = panel?.querySelector('.autojev-panel-rail');
                  if (rail) rail.setAttribute('aria-expanded', savedPinned ? 'true' : 'false');
                  const button = document.getElementById('autojev-action-button');
                  if (button) { button.disabled = false; button.textContent = 'Continuar exploração'; }
                }"""
            )
        except Exception:
            pass

    def notify_user_input(self) -> None:
        self._user_input_event.set()

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
                    """() => {
                      const field = document.querySelector(
                        'input[type="password"], input[name*="pass" i], input[name*="senha" i], input[id*="pass" i]'
                      );
                      if (!field) return false;
                      const box = field.getBoundingClientRect();
                      const style = getComputedStyle(field);
                      return style.display !== 'none' && style.visibility !== 'hidden' && box.width > 0 && box.height > 0;
                    }"""
                )
            )
        except Exception:
            return True

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
                    "!!document.getElementById('autojev-sidepanel')"
                )
            except Exception:
                exists = True
            if not exists and not self._user_input_event.is_set():
                await self.show_intervention_panel(self._intervention_message or "Preencha usuário e senha na página.")
        self._user_input_event.clear()
        await self.hide_intervention_panel()

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

    def _on_console(self, message: Any) -> None:
        if self.options.capture_console_errors and message.type == "error":
            self._console_buffer.append(
                {
                    "type": "console",
                    "message": self._redact_text(message.text)[:2000],
                    "url": self._public_url(self._page.url) if self._page else "",
                    "occurred_at": utc_now(),
                }
            )

    def _on_page_error(self, error: Exception) -> None:
        if self.options.capture_console_errors:
            self._console_buffer.append(
                {
                    "type": "pageerror",
                    "message": self._redact_text(str(error))[:2000],
                    "url": self._public_url(self._page.url) if self._page else "",
                    "occurred_at": utc_now(),
                }
            )

    def _on_response(self, response: Any) -> None:
        if self.options.capture_network_errors and response.status >= 400:
            self._network_buffer.append(
                {
                    "type": "http",
                    "method": response.request.method,
                    "url": self._public_url(response.url)[:2000],
                    "status_code": response.status,
                    "message": f"{response.request.method} returned HTTP {response.status}",
                    "occurred_at": utc_now(),
                }
            )

    def _on_request_failed(self, request: Any) -> None:
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
                }
            )

    async def _capture(self, label: str) -> str | None:
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
        await self._page.screenshot(
            path=str(path),
            full_page=self.headless,
            mask=masks,
            mask_color="#101416",
            timeout=20_000,
        )
        return filename

    async def observe(self, screenshot_label: str = "page") -> Observation:
        if not self.is_active():
            raise BrowserUnavailableError("Browser page is not active")
        snapshot = await self._page.evaluate(
            """() => {
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
                const inset = 2;
                const points = [
                  [box.left + box.width / 2, box.top + box.height / 2],
                  [box.left + inset, box.top + inset],
                  [box.right - inset, box.top + inset],
                  [box.left + inset, box.bottom - inset],
                  [box.right - inset, box.bottom - inset],
                ];
                const canReceivePointer = points.some(([rawX, rawY]) => {
                  const x = Math.max(0, Math.min(window.innerWidth - 1, rawX));
                  const y = Math.max(0, Math.min(window.innerHeight - 1, rawY));
                  const hit = document.elementsFromPoint(x, y).find((candidate) => (
                    !candidate.closest(PANEL_SELECTOR)
                    && getComputedStyle(candidate).pointerEvents !== 'none'
                  ));
                  return Boolean(hit && (hit === el || el.contains(hit)));
                });
                return {
                  inHorizontalViewport,
                  inVerticalViewport,
                  interactable: canReceivePointer,
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
              const elements = candidates.map((el, index) => {
                const ref = `e${index}`;
                const box = el.getBoundingClientRect();
                const interaction = interactionState(el, box);
                const enabled = !el.matches(':disabled')
                  && el.getAttribute('aria-disabled') !== 'true'
                  && !el.closest('[inert]');
                const ariaHiddenOwner = el.closest('[aria-hidden]');
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
                  name: (el.getAttribute('aria-label') || el.innerText || el.getAttribute('placeholder') || el.getAttribute('name') || '').trim().slice(0, 300),
                  type: el.getAttribute('type') || null,
                  href: el instanceof HTMLAnchorElement ? el.href : null,
                  enabled,
                  read_only: Boolean(el.readOnly),
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
                  in_navigation: Boolean(el.closest('nav,[role="navigation"],aside')),
                  context: contextNode
                    ? `${contextNode.tagName.toLowerCase()} ${contextMarker} ${contextNode.getAttribute('aria-label') || contextNode.getAttribute('role') || ''} ${contextText}`.trim().slice(0, 160)
                    : null,
                  _selector: selectorFor(el),
                };
              });
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
              return {
                title: document.title || '',
                text: textParts.join(' ').replace(/\\s+/g, ' ').slice(0, 12000),
                elements,
              };
            }"""
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
                if element.get("ref"):
                    self._ref_semantics[str(element["ref"])] = {
                        "tag": str(element.get("tag") or "").lower(),
                        "role": element.get("role"),
                        "type": element.get("type"),
                        "name": element["name"],
                        "href": element.get("href"),
                        "aria_haspopup": element.get("aria_haspopup"),
                    }
        return Observation(
            url=self._public_url(self._page.url),
            title=self._redact_text(str(snapshot.get("title", ""))),
            text=self._redact_text(str(snapshot.get("text", ""))),
            interactive_elements=elements,
            console_errors=console_errors,
            network_errors=network_errors,
            screenshot=screenshot,
        )

    async def _locator(self, ref: str | None) -> Any:
        if not ref or not ref.startswith("e") or not ref[1:].isdigit():
            raise ValueError("Action requires a valid observed element ref")
        selector = self._ref_selectors.get(ref)
        if not selector:
            raise ValueError("Observed element is no longer attached to the page")
        locator = self._page.locator(selector)
        if await locator.count() != 1:
            raise ValueError("Observed element is no longer uniquely addressable")
        observed = self._ref_semantics.get(ref)
        if observed:
            actual = await locator.evaluate(
                """el => ({
                  tag: el.tagName.toLowerCase(),
                  role: el.getAttribute('role'),
                  type: el.getAttribute('type'),
                  name: (el.getAttribute('aria-label') || el.innerText || el.getAttribute('placeholder') || el.getAttribute('name') || '').trim().slice(0, 300),
                  href: el instanceof HTMLAnchorElement ? el.href : null,
                  aria_haspopup: el.getAttribute('aria-haspopup'),
                })"""
            )
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
            if actual != observed:
                raise ValueError(
                    "Observed element changed after the decision; re-observation is required"
                )
        return locator

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
                await self._page.goto(decision.url, wait_until="domcontentloaded", timeout=30_000)
            elif action == "click":
                locator = await self._locator(decision.target)
                await self._enforce_safe_interaction(locator)
                pages_before = set(self._context.pages)
                self._pending_pages.clear()
                opens_new_tab = (
                    await locator.get_attribute("target", timeout=500) == "_blank"
                )
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
                await locator.select_option(decision.value, timeout=10_000)
            elif action == "press":
                locator = await self._locator(decision.target)
                key = decision.key or "Enter"
                if "enter" in key.lower():
                    await self._enforce_safe_interaction(locator)
                await locator.press(key, timeout=10_000)
            elif action == "hover":
                locator = await self._locator(decision.target)
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
                await self._wait_for_dom_settle()
            if self._origin(self._page.url) != self._allowed_origin:
                raise PermissionError("PolicyBlocked: action navigated to another origin")
            if self._blocked_navigation_url:
                raise PermissionError(
                    "PolicyBlocked: cross-origin navigation was blocked before request"
                )
            success = True
            message = f"Playwright executed {action} successfully"
        except Exception as exc:
            message = self._redact_text(f"{exc.__class__.__name__}: {exc}")[:2000]
        finally:
            await self._set_sidepanel_automation(False)

        assertion_results = await self._evaluate_assertions(decision.assertions)
        observation = await self.observe(decision.action)
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
