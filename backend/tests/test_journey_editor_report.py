from copy import deepcopy
from dataclasses import dataclass, field
from html.parser import HTMLParser
import json
from urllib.parse import urlsplit

import pytest

from autojev.adapters.report import StandaloneHtmlReport
from autojev.adapters.report_premium import _case_editor, _journey
from autojev.services.scenario_timeline import TIMELINE_VERSION


@dataclass
class _Element:
    tag: str
    attrs: dict = field(default_factory=dict)
    content: list = field(default_factory=list)

    @property
    def text(self):
        return "".join(item.text if isinstance(item, _Element) else item for item in self.content)

    @property
    def children(self):
        return [item for item in self.content if isinstance(item, _Element)]

    def find_all(self, tag=None, class_name=None, **attrs):
        found = []
        for child in self.children:
            if (
                (tag is None or child.tag == tag)
                and (class_name is None or class_name in child.attrs.get("class", "").split())
                and all(key in child.attrs and child.attrs[key] == value for key, value in attrs.items())
            ):
                found.append(child)
            found.extend(child.find_all(tag, class_name, **attrs))
        return found

    def one(self, tag=None, class_name=None, **attrs):
        found = self.find_all(tag, class_name, **attrs)
        assert len(found) == 1, (tag, class_name, attrs, len(found))
        return found[0]


class _Document(HTMLParser):
    _VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.root = _Element("document")
        self.stack = [self.root]
        self.feed(document)
        self.close()

    def handle_starttag(self, tag, attrs):
        element = _Element(tag, dict(attrs))
        self.stack[-1].content.append(element)
        if tag not in self._VOID_TAGS:
            self.stack.append(element)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].content.append(_Element(tag, dict(attrs)))

    def handle_endtag(self, tag):
        for position in range(len(self.stack) - 1, 0, -1):
            if self.stack[position].tag == tag:
                del self.stack[position:]
                break

    def handle_data(self, data):
        self.stack[-1].content.append(data)


@pytest.fixture
def report(tmp_path):
    return StandaloneHtmlReport(tmp_path)


def _actions(routes):
    pairs = list(zip(routes, routes[1:])) or [(routes[0], routes[0])]
    return [
        {"step_number": index, "result": {"before_url": before, "after_url": after}}
        for index, (before, after) in enumerate(pairs, 1)
    ]


def _case(index, url, status="PASS", **changes):
    return {"index": index, "id": f"CT{index + 1:03d}", "title": f"Teste {index + 1}", "status": status, "url": url, **changes}


def _render_journey(report, routes, cases=()):
    exploration = {"actions": _actions(routes)}
    original_exploration, original_cases = deepcopy(exploration), deepcopy(cases)
    document = _journey(exploration, cases, report)
    assert exploration == original_exploration
    assert cases == original_cases
    return document, _Document(document).root


def test_journey_map_keeps_native_ordered_items_and_decorative_route_beyond_eight_pages(report):
    routes = [f"https://shop.test/page-{index}" for index in range(1, 12)]
    document, parsed = _render_journey(report, routes)
    page_list = parsed.one("ol", "journey-track")
    assert page_list.attrs["role"] == "list"
    assert page_list.attrs["aria-label"] == "Páginas em ordem da primeira visita"
    assert "style" not in page_list.attrs
    nodes = page_list.children
    assert len(nodes) == 11
    assert all(node.tag == "li" and node.attrs.get("class") == "journey-node" for node in nodes)
    assert [node.attrs["data-journey-position"] for node in nodes] == [str(index) for index in range(1, 12)]
    assert [node.one("span", "journey-number").text for node in nodes] == [f"{index:02d}" for index in range(1, 12)]
    assert [node.one("span", "journey-page-title").text for node in nodes] == [f"/page-{index}" for index in range(1, 12)]
    assert all(node.one("span", "journey-page-title").attrs["role"] == "heading" for node in nodes)
    assert all(node.one("span", "journey-page-title").attrs["aria-level"] == "3" for node in nodes)
    assert all("style" not in node.attrs for node in nodes)
    panel = parsed.one("section", "journey-map-panel")
    route = panel.one("div", "journey-route")
    assert panel.attrs["aria-label"] == "Mapa das páginas em ordem de descoberta"
    assert "11 páginas descobertas" in panel.one("div", "journey-map-meta").text
    canvas = route.one("canvas", "journey-route-canvas")
    assert canvas.attrs["aria-hidden"] == "true" and canvas.attrs["focusable"] == "false"
    assert route.one("span", "journey-terminal-start").text == "INÍCIO"
    assert route.one("span", "journey-terminal-end").text == "FIM"
    assert all(item.attrs["aria-hidden"] == "true" for item in route.find_all("span", "journey-terminal"))
    assert nodes[0].one("span", "journey-stage-label").text == "Primeira página visitada"
    assert nodes[-1].one("span", "journey-stage-label").text == "Última página descoberta"
    assert "journey-list" not in document
    assert "grid-row" not in document and "grid-column" not in document
    assert "--journey-columns" not in document
    assert "Cada cartão representa uma página" in document
    assert "“FIM” indica apenas o fim das páginas descobertas" in document


def test_journey_keeps_returns_in_collapsed_actual_sequence_and_counts_real_visits(report):
    home, cart, checkout = [f"https://shop.test/{path}" for path in ("", "cart", "checkout")]
    routes = [home, cart, home, cart, checkout, home]
    document, parsed = _render_journey(report, routes)
    nodes = parsed.one("ol", "journey-track").children
    assert [node.one("span", "journey-page-title").text for node in nodes] == ["/", "/cart", "/checkout"]
    for node, visits in zip(nodes, (3, 2, 1)):
        assert f'{visits} {"visita" if visits == 1 else "visitas"}' in node.text
    sequence_details = parsed.one("details", "journey-sequence-details")
    assert "open" not in sequence_details.attrs
    assert "6 posições de navegação" in sequence_details.text
    sequence = sequence_details.one("div", "journey-sequence")
    assert [child.text for child in sequence.children if "journey-connector" not in child.attrs.get("class", "").split()] == [
        "/", "/cart", "/", "/cart", "/checkout", "/",
    ]
    assert "Não representa um teste end-to-end único" in document
    assert "nem comprova uma jornada de compra completa" in document


def test_journey_coalesces_unchanged_adjacent_urls_without_losing_later_returns(report):
    home, cart = "https://shop.test/", "https://shop.test/cart"
    _, parsed = _render_journey(report, [home, home, cart, cart, home])
    sequence = parsed.one("div", "journey-sequence")
    assert [child.text for child in sequence.children if "aria-hidden" not in child.attrs] == ["/", "/cart", "/"]
    assert "3 posições de navegação" in parsed.one("details", "journey-sequence-details").text


def test_each_page_picker_lists_every_ct_with_original_status_and_result_and_edit_controls(report):
    products, cart = "https://shop.test/products", "https://shop.test/cart"
    statuses = ["PASS", "FAIL", "BLOCKED", "SKIPPED", "FAIL", "PASS", "BLOCKED"]
    cases = [_case(index, products, status) for index, status in enumerate(statuses)]
    cases.append(_case(7, cart, "SKIPPED"))
    _, parsed = _render_journey(report, [products, cart], cases)
    nodes = parsed.one("ol", "journey-track").children
    pickers = parsed.find_all("section", "journey-case-picker")
    assert [picker.attrs["id"] for picker in pickers] == ["journey-cases-0", "journey-cases-1"]
    for page_index, (node, picker) in enumerate(zip(nodes, pickers)):
        control = node.one("button", **{"data-journey-page": str(page_index)})
        assert control.attrs["aria-controls"] == picker.attrs["id"]
        assert control.attrs["aria-expanded"] == "false"
        assert control.attrs["type"] == "button"
        assert control.attrs["class"] == "journey-page-card"
        assert "Abrir testes desta página" in control.attrs["aria-label"]
        assert control.find_all("button") == []
        assert control.find_all("h3") == []
        assert node.children == [control]
        assert control.one("span", "journey-card-arrow").one("svg").attrs["aria-hidden"] == "true"
        assert "hidden" in picker.attrs
        assert picker.attrs["tabindex"] == "-1"
        assert picker.one("button", **{"data-journey-close": None}).attrs["aria-label"]
    rows = pickers[0].find_all("article", "journey-case-row")
    assert len(rows) == len(statuses)
    assert [row.one("span", "case-id").text for row in rows] == [case["id"] for case in cases[:7]]
    assert [row.one("strong").text for row in rows] == [case["title"] for case in cases[:7]]
    assert [row.one("span", "badge").attrs["class"] for row in rows] == [f"badge {status.lower()}" for status in statuses]
    assert [row.one("span", "badge").text for row in rows] == [
        "Aprovado", "Reprovado", "Bloqueado", "Sem validação", "Reprovado", "Aprovado", "Bloqueado",
    ]
    for index, row in enumerate(rows):
        assert row.one("button", **{"data-open-case": str(index)}).text == "Ver resultado"
        assert row.one("button", **{"data-edit-case": str(index)}).text == "Editar passos"
    assert pickers[1].one("button", **{"data-open-case": "7"})
    assert pickers[1].one("button", **{"data-edit-case": "7"})
    assert "7 testes" in nodes[0].text
    assert "2 reprovados" in nodes[0].text


def test_observed_page_without_associated_ct_has_no_broken_picker_control(report):
    home, observed = "https://shop.test/", "https://shop.test/observed-only"
    _, parsed = _render_journey(report, [home, observed], [_case(0, home)])
    nodes = parsed.one("ol", "journey-track").children
    assert "0 testes" in nodes[1].text
    assert "Sem CT atribuído a esta página." in nodes[1].text
    assert nodes[1].find_all("button") == []
    assert nodes[1].one("div", "journey-page-empty")
    assert nodes[1].find_all("span", "journey-card-arrow") == []
    assert parsed.find_all(id="journey-cases-1") == []
    assert len(parsed.find_all("section", "journey-case-picker")) == 1


def test_single_observed_page_keeps_one_list_item_and_one_case_picker(report):
    url = "https://shop.test/only-page"
    _, parsed = _render_journey(report, [url], [_case(0, url, "FAIL")])
    nodes = parsed.one("ol", "journey-track").children
    assert len(nodes) == 1
    assert nodes[0].attrs["data-journey-position"] == "1"
    assert "style" not in nodes[0].attrs
    assert nodes[0].one("span", "journey-stage-label").text == "Primeira página visitada"
    assert "1 visita" in nodes[0].text
    assert "1 reprovado" in nodes[0].one("span", "journey-failures").text
    assert "1 posições de navegação" in parsed.one("details", "journey-sequence-details").text
    assert len(parsed.find_all("section", "journey-case-picker")) == 1


@pytest.mark.parametrize("exploration", [{}, {"actions": []}, {"actions": [{"result": {}}]}])
def test_no_observed_navigation_uses_historical_fallback_not_inferred_case_routes(report, exploration):
    cases = [_case(0, "https://shop.test/not-observed", "PASS")]
    original = deepcopy(exploration)
    document = _journey(exploration, cases, report)
    parsed = _Document(document).root
    assert "A sequência de navegação não foi preservada nesta execução" in parsed.text
    assert "As páginas dos CTs continuam disponíveis em Regras e cobertura" in parsed.text
    assert parsed.find_all(class_name="journey-track") == []
    assert parsed.find_all(class_name="journey-route-canvas") == []
    assert parsed.find_all(class_name="journey-case-picker") == []
    assert "not-observed" not in document
    assert exploration == original


def test_journey_redacts_secret_queries_and_escapes_titles_ids_routes_and_visible_variants(report):
    path = '/product/<img src=x onerror="route()">'
    raw_urls = [
        f'https://shop.test{path}?id=1&token=private-one#<script>variant</script>',
        f'https://shop.test{path}?id=2&token=private-two#details',
    ]
    public_urls = [report._public_issue_url(url) for url in raw_urls]
    title = 'Produto <img src=x onerror="title()"> & </script>'
    cases = [_case(index, url, "FAIL", id=f'<b>CT{index}</b>', title=title) for index, url in enumerate(public_urls)]
    document, parsed = _render_journey(report, raw_urls, cases)
    nodes = parsed.one("ol", "journey-track").children
    assert len(nodes) == 2
    assert [node.one("span", "journey-page-title").text for node in nodes] == [path, path]
    variants = [node.one("small", "page-variant").text for node in nodes]
    assert variants == [f'?{urlsplit(url).query}#{urlsplit(url).fragment}' for url in public_urls]
    assert variants[0] != variants[1]
    assert "id=1" in variants[0] and "id=2" in variants[1]
    assert all("token=%5BREDACTED%5D" in variant for variant in variants)
    pickers = parsed.find_all("section", "journey-case-picker")
    assert [picker.one("p", "url").text for picker in pickers] == public_urls
    assert [picker.one("span", "case-id").text for picker in pickers] == ['<b>CT0</b>', '<b>CT1</b>']
    assert all(picker.one("strong").text == title for picker in pickers)
    assert "private-one" not in document and "private-two" not in document
    assert "&lt;img" in document and "&quot;" in document and "&amp;" in document
    assert parsed.find_all("img") == []
    assert parsed.find_all("script") == []
    assert parsed.find_all("b") == []


def test_case_editor_is_a_closed_visual_draft_shell_with_a_draggable_database_palette():
    document = _case_editor()
    parsed = _Document(document).root
    dialog = parsed.one("dialog", id="case-editor")
    assert "open" not in dialog.attrs
    assert dialog.attrs["aria-labelledby"] == "case-editor-title"
    assert dialog.one("h2", id="case-editor-title").text == "Editar passos do teste"
    notice = dialog.one("div", "case-editor-notice").text
    assert "Rascunho visual · não executado · não altera este resultado" in notice
    assert "As etapas registradas ficam preservadas" in notice
    assert "não executam consultas nem acessam bancos de dados" in notice
    palette = dialog.one("aside", id="case-editor-palette")
    database = palette.one("button", id="database-step-card")
    assert database.attrs["draggable"] == "true"
    assert database.one("strong").text == "Consulta em banco de dados"
    assert "Somente visual · sem conexão ou execução" in database.text
    assert database.one("svg").attrs["aria-hidden"] == "true"
    assert dialog.one("ol", id="case-editor-steps").content == []
    assert "Etapas originais são somente leitura" in dialog.text
    feedback = dialog.one("p", id="case-editor-feedback")
    assert feedback.attrs["role"] == "status"
    assert feedback.attrs["aria-live"] == "polite"
    assert dialog.one("button", id="case-editor-close")
    assert dialog.one("button", id="case-editor-rerun")
    assert dialog.one("button", id="case-editor-save")
    assert parsed.find_all("script") == []
    assert parsed.find_all("form") == []
    assert parsed.find_all("input") == []
    assert "report-drafts" not in document


def test_generated_report_initializes_separate_empty_draft_data_and_preserves_recorded_history(tmp_path):
    products, cart = "https://shop.test/products", "https://shop.test/cart"
    statuses = ["PASS", "FAIL", "BLOCKED", "SKIPPED"]
    run = {
        "id": "journey-editor", "status": "completed", "app_url": products,
        "timeline_version": TIMELINE_VERSION,
        "actions": _actions([products, cart, products]),
        "scenarios": [
            {
                "id": f"CT{index + 1:03d}", "title": f"Teste histórico {index + 1}",
                "status": status, "url": products,
                "evidence": {"timeline": [{"kind": "action", "step_number": 1, "title": f"Etapa registrada {index + 1}"}]},
            }
            for index, status in enumerate(statuses)
        ],
    }
    original = deepcopy(run)
    output = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(output))
    document = output.read_text()
    parsed = _Document(document).root
    editor = parsed.one("dialog", id="case-editor")
    assert "open" not in editor.attrs
    assert editor.one("ol", id="case-editor-steps").content == []
    drafts = parsed.one("script", id="report-drafts")
    assert drafts.attrs["type"] == "application/json"
    assert json.loads(drafts.text) == {"version": 1, "cases": {}}
    recorded = parsed.one("script", id="report-data")
    assert recorded is not drafts
    payload = json.loads(recorded.text)
    assert [case["status"] for case in payload["cases"]] == statuses
    assert [case["id"] for case in payload["cases"]] == [scenario["id"] for scenario in run["scenarios"]]
    assert [case["title"] for case in payload["cases"]] == [scenario["title"] for scenario in run["scenarios"]]
    assert all("draft" not in case for case in payload["cases"])
    canonical_cards = parsed.find_all("details", "scenario")
    assert len(canonical_cards) == len(statuses)
    assert [card.attrs["data-status"] for card in canonical_cards] == statuses
    assert len(parsed.find_all("article", "journey-case-row")) == len(statuses)
    assert document.count('id="case-editor"') == 1
    assert document.count('id="report-drafts"') == 1
    assert '<script src=' not in document
    assert run == original
