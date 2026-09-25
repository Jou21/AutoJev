from __future__ import annotations

import asyncio
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

    # IDs de UI própria injetada no site. Nunca entram em observações,
    # candidatos de ação, texto visível ou screenshots de evidência.
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
        self._user_input_event = asyncio.Event()
        self._intervention_message = ""

    async def start(self, url: str) -> Observation:
        self._validate_url(url)
        self._allowed_origin = self._origin(url)
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
            self._context = await self._browser.new_context(**context_options)
            await self._context.route("**/*", self._guard_navigation)
            self._page = await self._context.new_page()
            await self._page.expose_binding(
                "__autojev_resume",
                lambda source: self._user_input_event.set(),
            )
            self._page.on("console", self._on_console)
            self._page.on("pageerror", self._on_page_error)
            self._page.on("response", self._on_response)
            self._page.on("requestfailed", self._on_request_failed)
            await self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            return await self.observe("initial")
        except BrowserUnavailableError:
            await self.close()
            raise
        except Exception as exc:
            await self.close()
            raise BrowserUnavailableError(f"Browser could not start or navigate: {exc}") from exc

    _SIDEPANEL_CSS = (
        "#autojev-sidepanel{position:fixed !important;z-index:2147483647;top:0;right:0;"
        "width:330px;height:100vh;overflow-y:auto;margin:0;box-sizing:border-box;"
        "padding:28px 22px;color:#e8ecec;background:#111719;border-left:1px solid #765b25;"
        "box-shadow:-8px 0 28px rgba(0,0,0,.28);font:14px ui-sans-serif,system-ui,sans-serif;"
        "display:block !important;visibility:visible !important;opacity:1 !important;}"
        "#autojev-sidepanel .autojev-panel-kicker{color:#eab94e;font-size:10px;font-weight:700;"
        "letter-spacing:.12em;}"
        "#autojev-sidepanel h2{margin:18px 0 10px;font-size:21px;color:#fff;}"
        "#autojev-sidepanel p{color:#aebbb9;line-height:1.55;}"
        "#autojev-sidepanel ul{list-style:none;margin:14px 0 0;padding:0;}"
        "#autojev-sidepanel li{margin:0 0 8px;padding:8px 10px;border-radius:6px;"
        "background:#1a2224;color:#c8d4d2;font-size:12px;line-height:1.45;"
        "border-left:2px solid #eab94e;word-break:break-word;}"
        "#autojev-sidepanel button{width:100%;margin-top:18px;padding:12px 14px;border:0;"
        "border-radius:6px;background:#eab94e;color:#17130b;font-weight:700;cursor:pointer;}"
        "#autojev-sidepanel button:disabled{opacity:.7;cursor:default;}"
        "#autojev-sidepanel .autojev-live-dot{display:inline-block;width:8px;height:8px;"
        "border-radius:50%;background:#4ade80;margin-right:6px;}"
        "#autojev-sidepanel .autojev-usage{display:flex;gap:8px;margin:12px 0 0;color:#eab94e;"
        "font-size:11px;font-weight:700;}"
    )

    async def _ensure_sidepanel(self) -> bool:
        """Cria a barra lateral única (status + ação). Retorna False se impossível."""
        if not self._page or self.headless:
            return False
        try:
            await self._page.evaluate(
                """(css) => {
                  const ID = 'autojev-sidepanel';
                  if (document.getElementById(ID)) return;
                  const panel = document.createElement('aside');
                  panel.id = ID;
                  const kicker = document.createElement('div');
                  kicker.className = 'autojev-panel-kicker';
                  kicker.innerHTML = '<span class="autojev-live-dot"></span><span id="autojev-kicker-text">AUTOJEV · EXPLORANDO</span>';
                  const title = document.createElement('h2');
                  title.id = 'autojev-panel-title';
                  title.textContent = 'Exploração em andamento';
                  const log = document.createElement('ul');
                  log.id = 'autojev-panel-log';
                  const action = document.createElement('div');
                  action.id = 'autojev-panel-action';
                  action.style.display = 'none';
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
                    } catch (e) {}
                    button.disabled = true;
                    button.textContent = 'Retomando…';
                  });
                  action.append(actionText, button);
                  const usage = document.createElement('div');
                  usage.id = 'autojev-usage';
                  usage.className = 'autojev-usage';
                  usage.textContent = 'Tokens: 0 · Custo: —';
                  const style = document.createElement('style');
                  style.textContent = css;
                  panel.append(kicker, title, usage, action, log, style);
                  const mount = () => {
                    if (!document.body) return false;
                    if (!document.getElementById(ID)) document.body.append(panel);
                    return true;
                  };
                  if (!mount()) {
                    new MutationObserver((_, obs) => { if (mount()) obs.disconnect(); })
                      .observe(document.documentElement, { childList: true, subtree: true });
                  }
                }""",
                self._SIDEPANEL_CSS,
            )
            return True
        except Exception:
            return False

    async def show_live_panel(self) -> None:
        await self._ensure_sidepanel()

    async def update_live_panel(self, title: str, line: str) -> None:
        """Atualiza título e acrescenta linha de log (máx. 8). Sem botões."""
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
                      while (log.children.length > 8) log.lastChild.remove();
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
                  const usage = document.getElementById('autojev-usage');
                  if (usage) usage.textContent = `Tokens: ${tokens.toLocaleString('pt-BR')} · Custo: ${costText}`;
                }""",
                [total_tokens, f"${cost:.6f}" if cost_known and cost is not None else "—"],
            )
        except Exception:
            pass

    async def hide_live_panel(self) -> None:
        if not self._page:
            return
        try:
            await self._page.evaluate("document.getElementById('autojev-sidepanel')?.remove()");
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
                  const action = document.getElementById('autojev-panel-action');
                  const text = document.getElementById('autojev-action-text');
                  const button = document.getElementById('autojev-action-button');
                  const kicker = document.getElementById('autojev-kicker-text');
                  if (text) text.textContent = message;
                  if (action) action.style.display = 'block';
                  if (button) { button.disabled = false; button.textContent = 'Continuar exploração'; }
                  if (kicker) kicker.textContent = 'AUTOJEV · AÇÃO NECESSÁRIA';
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
                  document.getElementById('autojev-panel-action')?.style.setProperty('display', 'none');
                  const kicker = document.getElementById('autojev-kicker-text');
                  if (kicker) kicker.textContent = 'AUTOJEV · EXPLORANDO';
                  const button = document.getElementById('autojev-action-button');
                  if (button) { button.disabled = false; button.textContent = 'Continuar exploração'; }
                }"""
            )
        except Exception:
            pass

    def notify_user_input(self) -> None:
        self._user_input_event.set()

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
            is_top_level = request.is_navigation_request() and request.frame.parent_frame is None
            if is_top_level and self._origin(request.url) != self._allowed_origin:
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
        # Esconde a barra do AutoJev para não poluir as evidências.
        panels_hidden = False
        try:
            await self._page.evaluate(
                """() => {
                  document.querySelectorAll(
                    '#autojev-sidepanel,#autojev-intervention-panel,#autojev-live-panel'
                  ).forEach((panel) => {
                    panel.setAttribute('data-autojev-restore', panel.style.display || '');
                    panel.style.display = 'none';
                  });
                }"""
            )
            panels_hidden = True
        except Exception:
            panels_hidden = False
        try:
            await self._page.screenshot(path=str(path), full_page=True, mask=masks, timeout=20_000)
        finally:
            if panels_hidden:
                try:
                    await self._page.evaluate(
                        """() => {
                          document.querySelectorAll('[data-autojev-restore]').forEach((panel) => {
                            panel.style.display = panel.getAttribute('data-autojev-restore') || '';
                            panel.removeAttribute('data-autojev-restore');
                          });
                        }"""
                    )
                except Exception:
                    pass
        return filename

    async def observe(self, screenshot_label: str = "page") -> Observation:
        if not self._page:
            raise BrowserUnavailableError("Browser page is not active")
        snapshot = await self._page.evaluate(
            """() => {
              const PANEL_SELECTOR = '#autojev-sidepanel,#autojev-intervention-panel,#autojev-live-panel';
              const panels = Array.from(document.querySelectorAll(PANEL_SELECTOR));
              const previous = panels.map((panel) => panel.style.display);
              // Esconde a UI do AutoJev de forma síncrona: nem os candidatos
              // nem o texto visível a incluem, e nenhum repaint ocorre no meio.
              panels.forEach((panel) => { panel.style.display = 'none'; });
              let result;
              try {
                const candidates = Array.from(document.querySelectorAll(
                  'a,button,input,select,textarea,[role="button"],[role="link"],[role="checkbox"],[role="radio"],[tabindex]'
                )).filter((el) => {
                  if (el.closest(PANEL_SELECTOR)) return false;
                  const style = getComputedStyle(el);
                  const box = el.getBoundingClientRect();
                  return style.visibility !== 'hidden' && style.display !== 'none' && box.width > 0 && box.height > 0;
                }).slice(0, 100);
                document.querySelectorAll('[data-autojev-ref]').forEach(el => el.removeAttribute('data-autojev-ref'));
              const elements = candidates.map((el, index) => {
                const ref = `e${index}`;
                el.setAttribute('data-autojev-ref', ref);
                return {
                  ref,
                  tag: el.tagName.toLowerCase(),
                  role: el.getAttribute('role') || null,
                  name: (el.getAttribute('aria-label') || el.innerText || el.getAttribute('placeholder') || el.getAttribute('name') || '').trim().slice(0, 300),
                  type: el.getAttribute('type') || null,
                  href: el instanceof HTMLAnchorElement ? el.href : null,
                  enabled: !el.disabled,
                };
              });
              return {
                title: document.title || '',
                text: (document.body?.innerText || '').slice(0, 12000),
                elements,
              };
              } finally {
                panels.forEach((panel, index) => { panel.style.display = previous[index]; });
              }
            }"""
        )
        screenshot = await self._capture(screenshot_label)
        console_errors = list(self._console_buffer)
        network_errors = list(self._network_buffer)
        self._console_buffer.clear()
        self._network_buffer.clear()
        elements = snapshot.get("elements", [])
        for element in elements:
            if isinstance(element, dict):
                element["name"] = self._redact_text(str(element.get("name") or ""))
                if element.get("href"):
                    element["href"] = self._public_url(str(element["href"]))
        return Observation(
            url=self._public_url(self._page.url),
            title=self._redact_text(str(snapshot.get("title", ""))),
            text=self._redact_text(str(snapshot.get("text", ""))),
            interactive_elements=elements,
            console_errors=console_errors,
            network_errors=network_errors,
            screenshot=screenshot,
        )

    def _locator(self, ref: str | None) -> Any:
        if not ref or not ref.startswith("e") or not ref[1:].isdigit():
            raise ValueError("Action requires a valid observed element ref")
        return self._page.locator(f'[data-autojev-ref="{ref}"]')

    async def _enforce_safe_interaction(self, locator: Any) -> None:
        destructive_words = {
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
        }
        try:
            form_text = await locator.evaluate(
                """el => {
                  const form = el.closest('form');
                  if (!form) return '';
                  return `${form.innerText || ''} ${form.getAttribute('aria-label') || ''} ${form.getAttribute('action') || ''}`;
                }"""
            )
            text = " ".join(
                filter(
                    None,
                    [
                        await locator.inner_text(timeout=500),
                        await locator.get_attribute("aria-label", timeout=500),
                        await locator.get_attribute("title", timeout=500),
                        str(form_text or ""),
                    ],
                )
            ).strip().lower()
        except Exception:
            text = ""
        if any(word in text for word in destructive_words):
            raise PermissionError(
                "PolicyBlocked: destructive or transactional clicks are disabled"
            )

    async def execute(
        self, decision: AgentDecision, credentials: dict[str, str]
    ) -> tuple[ActionResult, Observation]:
        if not self._page:
            raise BrowserUnavailableError("Browser page is not active")
        started = utc_now()
        before_url = self._public_url(self._page.url)
        success = False
        message = ""
        recorded_value = decision.value
        self._blocked_navigation_url = None
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
                locator = self._locator(decision.target)
                await self._enforce_safe_interaction(locator)
                await locator.click(timeout=10_000)
            elif action == "fill":
                locator = self._locator(decision.target)
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
                await self._locator(decision.target).select_option(decision.value, timeout=10_000)
            elif action == "press":
                locator = self._locator(decision.target)
                key = decision.key or "Enter"
                if "enter" in key.lower():
                    await self._enforce_safe_interaction(locator)
                await locator.press(key, timeout=10_000)
            elif action == "hover":
                await self._locator(decision.target).hover(timeout=10_000)
            elif action == "go_back":
                await self._page.go_back(wait_until="domcontentloaded", timeout=30_000)
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
                    actual = bool(await self._locator(str(expected)).is_visible(timeout=2_000))
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
