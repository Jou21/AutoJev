"""Focused run through a hover-flyout toolbar like Sisbr's (real Chromium, fake LLMs).

Regression: the menu entries are plain ``div`` with ``cursor:pointer`` and the
submenu links are ``<a>`` without ``href`` shown only while the item is hovered.
The explorer saw none of it and ended with ``coverage_scope_not_found``.
"""

import asyncio
import json

from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.adapters.report import StandaloneHtmlReport
from autojev.adapters.repository import SQLiteRepository
from autojev.adapters.secrets import EphemeralCredentialVault, SecretCipher
from autojev.config import Settings
from autojev.domain.models import (
    AgentDecision,
    ExplorationOptions,
    ExplorationRequest,
    ModelSelection,
    ProviderModel,
)
from autojev.services.exploration import ExplorationManager
from autojev.services.frontier_advice import FrontierAdvisor

FOCUS = "Painel de Operações > Conciliação de Títulos Pagos em Cartório"

SISBR = """<!doctype html><meta charset="utf-8"><title>Gestão Catorária - Sisbr 3.0</title>
<style>
 aside{width:103px;font:13px sans-serif;position:fixed;top:60px;left:0}
 .ss-toolbar-item{display:block;width:103px;height:80px;cursor:pointer;position:relative}
 .ss-toolbar-menu{display:none;position:absolute;left:103px;top:0;width:307px;height:400px;background:#fff}
 .ss-toolbar-item:hover .ss-toolbar-menu{display:block}
 .ss-toolbar-menu-title{cursor:pointer}
 .ss-toolbar-menu-item a{display:block;height:48px;cursor:pointer}
 .chckbx{opacity:0;position:absolute}
 main{margin-left:430px;font:14px sans-serif}
</style>
<header><a href="/gcar/home">GESTÃO CARTORÁRIA</a>
 <span id="star" style="cursor:pointer" onclick="window.starClicks=(window.starClicks||0)+1">star</span></header>
<aside class="ss-toolbar">
 <div><div class="ss-toolbar-item"><i class="mdi"></i>Liberações
  <div class="ss-toolbar-menu"><h6 class="ss-toolbar-menu-title">Liberações</h6>
   <div><div class="ss-toolbar-menu-item"><a>Liberação de Títulos</a><input type="checkbox" class="chckbx"></div></div></div></div></div>
 <div><div class="ss-toolbar-item"><i class="mdi"></i>Painel de Operações
  <div class="ss-toolbar-menu"><h6 class="ss-toolbar-menu-title">Painel de Operações</h6>
   <div><div class="ss-toolbar-menu-item"><a>Repasse Financeiro</a><input type="checkbox" class="chckbx"></div></div>
   <div><div class="ss-toolbar-menu-item"><a>Conciliação de Títulos Pagos em Cartório</a><input type="checkbox" class="chckbx"></div></div>
   <div><div class="ss-toolbar-menu-item"><a>Resumo</a><input type="checkbox" class="chckbx"></div></div></div></div></div>
 <div><div class="ss-toolbar-item"><i class="mdi"></i>Processamento
  <div class="ss-toolbar-menu"><h6 class="ss-toolbar-menu-title">Processamento</h6>
   <div><div class="ss-toolbar-menu-item"><a>Reprocessar Lote</a><input type="checkbox" class="chckbx"></div></div></div></div></div>
</aside>
<main id="screen"><h1>Início</h1></main>
<script>
 const screen = document.getElementById('screen');
 document.querySelectorAll('.ss-toolbar-menu-item a').forEach(a => a.addEventListener('click', () => {
   const name = a.textContent.trim();
   history.pushState({}, '', '/gcar/' + name.toLowerCase().normalize('NFD').replace(/[^a-z]+/g, '-'));
   screen.innerHTML = '<h1>' + name + '</h1>' + (name.startsWith('Concilia')
     ? '<label>Período <input id="periodo" placeholder="Período"></label> <button id="pesq">Pesquisar</button>' : '');
 }));
</script>"""


class FixtureClient:
    def __init__(self, repository, run_id):
        self.repository, self.run_id = repository, run_id

    async def chat_json(self, *_args, **_kwargs):
        await asyncio.sleep(0.01)
        return {"title": "Documented local contract", "visual_assessment": {"findings": []}}

    def drain_usage(self):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost": 0, "cost_known": False}


class AdvisorClient:
    """Stands in for the auxiliary model: points at menu entries by name."""

    def __init__(self, wanted):
        self.wanted, self.calls = wanted, []

    async def chat_json(self, model, system, content, **kwargs):
        self.calls.append(json.loads(content if isinstance(content, str) else content[0]["text"]))
        controls = self.calls[-1]["controls"]
        picks = [c for c in controls if c["name"] in self.wanted]
        return {
            "focus_status": "on_path",
            "suggestions": [
                {"ref": c["ref"], "priority": 1, "why": "abre o submenu"} for c in picks
            ] + [{"ref": "e999", "priority": 1, "why": "inventado"}],
            "unexplored_notes": ["Há campos abaixo da dobra do painel lateral"],
        }

    def drain_usage(self):
        return {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10, "cost": 0.0001, "cost_known": True}


class Trace(list):
    """Executed steps as (action, ok, url); ``fills`` keeps what was typed where."""

    def __init__(self):
        super().__init__()
        self.fills = []
        self.searches = []
        self.rows = []


async def run_focused(tmp_path, monkeypatch, advisor_client=None, page_html=None, focus=FOCUS):
    repo = SQLiteRepository(tmp_path / "fixture.sqlite3", SecretCipher(tmp_path, "fixture"))
    repo.initialize()
    provider = repo.create_provider(
        "Fixture only", "http://fixture.invalid", None, True,
        [ProviderModel("fixture-agent", "Agent"), ProviderModel("fixture-aux", "Aux")],
    )
    selection = lambda model: ModelSelection(provider.id, model)
    run_id = repo.create_exploration(ExplorationRequest(
        "https://sisbr.fixture.test/gcar/home",
        selection("fixture-agent"), selection("fixture-aux"),
        ExplorationOptions(focus_area=focus, auxiliary_evidence_mode="text", generate_test_cases=False),
    ))
    settings = Settings(
        tmp_path, tmp_path / "fixture.sqlite3", tmp_path / "artifacts", None, (), True, "chromium", 1,
        auxiliary_max_requests=50,
    )
    clicked, star_clicks = Trace(), []

    class FixtureExecutor(PlaywrightExecutor):
        async def _prepare_page(self, page):
            await super()._prepare_page(page)

            async def serve(route):
                if route.request.url.startswith("https://sisbr.fixture.test/"):
                    await route.fulfill(content_type="text/html; charset=utf-8", body=page_html or SISBR)
                else:
                    await route.abort()

            await page.route("**/*", serve)

        async def execute(self, decision, credentials):
            result, after = await super().execute(decision, credentials)
            clicked.append((decision.action, result.success, after.url))
            if decision.action in {"fill", "select"}:
                clicked.fills.append((decision.action, decision.value, result.success, after.url))
            star_clicks.append(await self._page.evaluate("window.starClicks || 0"))
            clicked.searches.append(await self._page.evaluate("window.searches || 0"))
            clicked.rows.append(await self._page.evaluate("document.querySelectorAll('#resultados tr').length"))
            return result, after

    class FixtureAgent:
        def __init__(self, *_args):
            pass

        async def decide(self, _observation, _history, _credentials, coverage):
            candidates = coverage.get("current_untried", [])
            if not candidates:
                return AgentDecision("finish", "No local fixture candidate")
            candidate = candidates[0]
            return AgentDecision(
                candidate["suggested_action"], "Fixture decision",
                target=candidate["ref"], value=candidate.get("suggested_value"),
            )

    manager = ExplorationManager(
        repo, EphemeralCredentialVault(), StandaloneHtmlReport(settings.artifact_dir), settings,
    )
    monkeypatch.setattr(manager, "_client_for", lambda _id: FixtureClient(repo, run_id))
    if advisor_client is not None:
        monkeypatch.setattr(
            manager, "_make_frontier_advisor",
            lambda item, options, auxiliary, secrets: (
                FrontierAdvisor(advisor_client, "fixture-aux", max_requests=options.frontier_advice_max_requests),
                advisor_client,
            ),
        )
    monkeypatch.setattr("autojev.services.exploration.PlaywrightExecutor", FixtureExecutor)
    monkeypatch.setattr("autojev.services.exploration.OpenAIJevAgent", FixtureAgent)

    await asyncio.wait_for(manager._run(run_id), timeout=480)
    return repo.get_exploration(run_id), clicked, star_clicks


def clicks_before_the_focus(clicked, star_clicks):
    """Star clicks recorded up to (and including) the step that reached the focus."""
    reached = next(i for i, (_, ok, url) in enumerate(clicked) if ok and "concilia" in url)
    return star_clicks[: reached + 1]


async def test_focused_run_opens_the_hover_menu_and_reaches_the_requested_screen(tmp_path, monkeypatch):
    run, clicked, star_clicks = await run_focused(tmp_path, monkeypatch)

    event_types = [event["type"] for event in run["events"]]
    assert "coverage_scope_not_found" not in event_types, (run["status"], run["error"], clicked)
    assert any("concilia" in url for _, ok, url in clicked if ok), clicked
    assert run["status"] == "completed", (run["status"], run["error"], clicked)
    # The entry screen's own widgets (header star) are not the requested scope:
    # nothing there is touched on the way to the focus.
    assert max(clicks_before_the_focus(clicked, star_clicks)) == 0
    assert "frontier_advice" not in event_types          # no advisor without an auxiliary HTTP client


async def test_advisor_points_at_openings_and_the_run_still_reaches_the_screen(tmp_path, monkeypatch):
    advisor_client = AdvisorClient({"Painel de Operações"})
    run, clicked, star_clicks = await run_focused(tmp_path, monkeypatch, advisor_client)

    advice = [event for event in run["events"] if event["type"] == "frontier_advice"]
    assert advice, [event["type"] for event in run["events"]]
    first = advice[0]["payload"]
    assert first["trigger"] == "first_screen" and first["focus_status"] == "on_path"
    assert [item["name"] for item in first["suggestions"]] == ["Painel de Operações"]
    assert first["ignored_suggestions"] == 1                 # the invented ref was dropped
    assert first["unexplored_notes"] == ["Há campos abaixo da dobra do painel lateral"]
    assert first["requests_used"] == 1 and first["screenshot_sent"] is False
    # The home screen was consulted once, then only screens an action opened.
    assert advisor_client.calls[0]["screen"]["page"].endswith("/gcar/home")
    assert len(advisor_client.calls) == len({json.dumps(call["screen"]) for call in advisor_client.calls})
    assert any("concilia" in url for _, ok, url in clicked if ok), clicked
    assert max(clicks_before_the_focus(clicked, star_clicks)) == 0
    assert run["status"] == "completed", (run["status"], run["error"])
    # Its tokens/cost are accounted like the other auxiliary calls.
    assert run["usage"]["by_role"]["auxiliary"]["total_tokens"] >= 10
