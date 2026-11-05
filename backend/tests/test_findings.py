"""Defects stated from what the run saw, written as a tester files them.

The browser fixture copies the real "Nova Conciliação" layout (from a snapshot
of the application): result titles with nothing under them on the left, two
search blocks with h6 titles in an aside on the right, a breadcrumb whose last
link points at the new screen itself, and no Voltar/Cancelar anywhere.
"""

from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.domain.models import AgentDecision, ExplorationOptions, Observation
from autojev.services.coverage_ledger import functional_findings
from autojev.services.findings import FindingDetector, dead_end, empty_result_titles, search_findings

ORIGIN = "https://gcar.fixture.test"
CHROME = """<!doctype html><meta charset="utf-8"><title>gcar</title>
<style>body{margin:0;font:14px sans-serif}.row{display:flex}.main{flex:1;padding:16px;min-height:600px}
aside{width:360px;padding:16px}h6{font-size:18px;margin:12px 0}</style>
<header><h6>Início</h6><ol><li><a href="/gcar/">Início</a></li>
<li><a href="__CRUMB__">Conciliação de Títulos Pagos em Cartório</a></li></ol>__HEADER__</header>"""

LISTING = CHROME.replace("__CRUMB__", "/gcar/conciliacao").replace(
    "__HEADER__", '<a id="nova" href="/gcar/conciliacao/nova">add Nova Conciliação</a>'
) + """<div class="row"><div class="main"><h6>Sem Conteudo</h6><p>Faça uma pesquisa para apresentar resultados.</p></div>
<aside><h6>Conciliação de Títulos Pagos em Cartório</h6>
 <label for="uf0">UF</label><select id="uf0"><option>Todos</option></select>
 <label for="i0">Data de início*</label><input id="i0" placeholder="01/01/2026">
 <label for="f0">Data de término*</label><input id="f0" placeholder="01/01/2026">
 <button>search PESQUISAR</button></aside></div>"""

NOVA = CHROME.replace("__CRUMB__", "/gcar/conciliacao/nova").replace("__HEADER__", "") + """
<div class="row"><div class="main"><div><h6>Arquivo Recebido</h6></div><div><h6>Recurso Recebido</h6></div></div>
<aside>
 <div><h6>Arquivo Recebido</h6><div>
  <div><span>UF*</span><select id="uf" required><option value="0">Todos</option><option value="1">AC</option></select></div>
  <div><span>Data de início*</span><input id="ai" placeholder="01/01/2026"></div>
  <div><span>Data de término*</span><input id="af" placeholder="01/01/2026"></div>
  <button id="ap" disabled>search PESQUISAR</button></div></div>
 <div><h6>Recurso Recebido</h6><div>
  <div><span>Data de início*</span><input id="ri" placeholder="01/01/2026"></div>
  <div><span>Data de término*</span><input id="rf" placeholder="01/01/2026"></div>
  <div><span>CNPJ</span><input id="cnpj"></div>
  <button id="rp">search PESQUISAR</button></div></div>
</aside></div>"""


def gcar_executor(tmp_path):
    class Executor(PlaywrightExecutor):
        async def _guard_navigation(self, route, request):
            if request.url.startswith(ORIGIN):
                page = NOVA if request.url.rstrip("/").endswith("/nova") else LISTING
                await route.fulfill(content_type="text/html; charset=utf-8", body=page)
            else:
                await route.abort()

    return Executor(tmp_path, ExplorationOptions(), headless=True)


async def test_the_real_screen_defects_are_found_from_real_observations(tmp_path):
    listing_run, nova_run = gcar_executor(tmp_path / "a"), gcar_executor(tmp_path / "b")
    try:
        listing = await listing_run.start(f"{ORIGIN}/gcar/conciliacao")
        nova = await nova_run.start(f"{ORIGIN}/gcar/conciliacao/nova")
    finally:
        await listing_run.close()
        await nova_run.close()

    # "Sem Conteudo — Faça uma pesquisa" is the right empty state: nothing to report.
    assert listing.page_state["results"]["empty_sections"] == []
    assert empty_result_titles(listing) is None
    # The new screen shows the result titles over a blank area; the aside titles have their forms.
    assert nova.page_state["results"]["empty_sections"] == ["Arquivo Recebido", "Recurso Recebido"]
    titles = empty_result_titles(nova)
    assert titles and titles["kind"] == "empty_result_titles"
    assert "«Arquivo Recebido», «Recurso Recebido»" in titles["actual"]

    # Each field and button knows which block it belongs to.
    by_id = {e.get("field_id") or e["name"]: e for e in nova.interactive_elements}
    assert by_id["uf"]["section"] == "Arquivo Recebido"
    assert by_id["cnpj"]["section"] == "Recurso Recebido"
    buttons = [e for e in nova.interactive_elements if e["tag"] == "button"]
    assert [b["section"] for b in buttons] == ["Arquivo Recebido", "Recurso Recebido"]

    # "+ Nova Conciliação" leads to a screen with no way back; its breadcrumb points at itself.
    trigger = next(e for e in listing.interactive_elements if "Nova Conciliação" in e["name"])
    finding = dead_end(listing, AgentDecision(action="click", target=trigger["ref"], rationale="t"), nova)
    assert finding and finding["kind"] == "dead_end" and finding["severity"] == "média"
    assert finding["steps"] == ["Acessar /gcar/conciliacao", f"Clicar em «{trigger['name']}»"]
    assert "aponta para a própria tela nova" in finding["actual"]
    assert "Conciliação de Títulos Pagos em Cartório" in finding["actual"]


def observation(url, elements, results=None, **state):
    return Observation(
        url=url, title="t", text="", interactive_elements=elements,
        page_state={"results": results or {"rows": 0, "empty_state": False}, **state},
    )


BUTTON = {"ref": "e1", "tag": "a", "name": "add Nova Conciliação", "href": f"{ORIGIN}/gcar/nova"}
CLICK = AgentDecision(action="click", target="e1", rationale="t")
BEFORE = observation(f"{ORIGIN}/gcar/lista", [BUTTON])


def test_a_way_back_means_no_dead_end():
    for way_back in (
        {"ref": "e2", "tag": "button", "name": "Voltar"},
        {"ref": "e2", "tag": "button", "name": "arrow_back"},
        {"ref": "e2", "tag": "button", "name": "Cancelar"},
        {"ref": "e2", "tag": "a", "name": "Lista", "href": f"{ORIGIN}/gcar/lista/", "in_navigation": True},
    ):
        assert dead_end(BEFORE, CLICK, observation(f"{ORIGIN}/gcar/nova", [way_back])) is None, way_back


def test_no_dead_end_for_menu_navigation_same_route_or_a_control_that_stays():
    after = observation(f"{ORIGIN}/gcar/nova", [])
    menu = observation(BEFORE.url, [{**BUTTON, "in_navigation": True}])
    assert dead_end(menu, CLICK, after) is None                                   # the global menu leaves on purpose
    assert dead_end(BEFORE, CLICK, observation(BEFORE.url + "?x=1", [])) is None  # same screen
    assert dead_end(BEFORE, CLICK, observation(after.url, [dict(BUTTON)])) is None
    assert dead_end(BEFORE, AgentDecision(action="fill", target="e1", value="x", rationale="t"), after) is None
    # A global menu "Sair" is not the screen's way back.
    logout = observation(after.url, [{"ref": "e9", "tag": "a", "name": "Sair", "in_navigation": True}])
    assert dead_end(BEFORE, CLICK, logout)["kind"] == "dead_end"


def test_empty_titles_only_count_on_a_search_screen_without_rows():
    search = {"ref": "e3", "tag": "button", "name": "PESQUISAR"}
    titles = {"rows": 0, "empty_sections": ["Arquivo Recebido"]}
    assert empty_result_titles(observation(BEFORE.url, [search], titles))
    assert empty_result_titles(observation(BEFORE.url, [], titles)) is None              # no search here
    assert empty_result_titles(observation(BEFORE.url, [search], {**titles, "rows": 3})) is None
    assert empty_result_titles(observation(BEFORE.url, [search], titles, busy=True)) is None


def test_each_finding_is_reported_once_per_run():
    detector = FindingDetector()
    after = observation(f"{ORIGIN}/gcar/nova", [{"ref": "e3", "tag": "button", "name": "Pesquisar"}],
                        {"rows": 0, "empty_sections": ["Recurso Recebido"]})
    first = detector.after_action(BEFORE, CLICK, after)
    assert sorted(f["kind"] for f in first) == ["dead_end", "empty_result_titles"]
    assert detector.after_action(BEFORE, CLICK, after) == [] and detector.observe(after) == []


def event(event_type, **payload):
    return {"type": event_type, "payload": payload}


MATRIX = "/gcar/nova#0"


def test_search_events_state_the_blocked_default_and_the_missing_test_data():
    events = [
        event("search_matrix_started", matrix=MATRIX, section="Arquivo Recebido", planned_ranges=24),
        event("search_matrix_unlocked", matrix=MATRIX, field="UF", tried=1, option="AC", section="Arquivo Recebido",
              default="Todos", required=True, button="search PESQUISAR", messages=[], screenshot="082-click.jpg"),
        event("search_matrix_completed", matrix=MATRIX, searches=24, found_records=False, section="Arquivo Recebido"),
        event("search_variation_result", matrix=MATRIX, field="UF", kind="opção AC", rows=0),
        event("search_matrix_completed", matrix="/gcar/nova#1", searches=24, found_records=False, section="Recurso Recebido"),
    ]
    blocked, no_data = search_findings(events)
    assert blocked["kind"] == "search_blocked_by_default" and blocked["screenshot"] == "082-click.jpg"
    assert blocked["title"] == "Pesquisa de «Arquivo Recebido» bloqueada com o valor padrão de «UF»"
    assert "«UF» = «Todos»" in blocked["actual"] and "«UF» = «AC»" in blocked["actual"]
    assert "sem mensagem" in blocked["actual"] and "obrigatório" in blocked["expected"]
    assert blocked["steps"][1] == "Em «Arquivo Recebido», preencher as datas de início e de término"
    assert no_data["kind"] == "no_test_data" and no_data["severity"] == "impedimento"
    assert "Nenhuma das 49 pesquisas" in no_data["actual"]
    assert "«Arquivo Recebido»" in no_data["actual"] and "«Recurso Recebido»" in no_data["actual"]


def test_records_anywhere_mean_no_missing_data_finding():
    events = [
        event("search_matrix_completed", matrix=MATRIX, searches=24, found_records=False),
        event("search_matrix_completed", matrix="/gcar/nova#1", searches=24, found_records=True),
    ]
    assert search_findings(events) == []


def test_the_ledger_lists_screen_and_search_findings_most_severe_first():
    screen = {"id": "abc", "kind": "empty_result_titles", "severity": "baixa", "title": "Títulos"}
    events = [
        event("functional_finding", **screen),
        event("functional_finding", **screen),                 # the same finding recorded twice
        event("search_matrix_unlocked", matrix=MATRIX, field="UF", tried=1, option="AC", default="Todos"),
        event("search_matrix_completed", matrix=MATRIX, searches=24, found_records=False),
    ]
    assert [f["kind"] for f in functional_findings(events)] == [
        "search_blocked_by_default", "empty_result_titles", "no_test_data",
    ]
