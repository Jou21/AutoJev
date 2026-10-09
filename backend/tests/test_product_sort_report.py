"""Visual product ordering is a projection of evidence, not another verdict."""

from copy import deepcopy
from html.parser import HTMLParser
import json

import pytest

from autojev.adapters.report import StandaloneHtmlReport
from autojev.services.scenario_timeline import TIMELINE_VERSION


class SortParser(HTMLParser):
    def __init__(self, document):
        super().__init__()
        self.result = None
        self.rows = {}
        self.side = None
        self.card = None
        self.in_name = False
        self.technical_is_closed = False
        self.in_technical = False
        self.technical = []
        self.visible = []
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        if "sort-verification" in classes:
            self.result = attrs["data-result"]
        if "sort-row" in classes:
            self.side = attrs["data-side"]
            self.rows[self.side] = []
        if "sort-product" in classes:
            self.card = {"state": attrs["data-state"], "name": ""}
            self.rows[self.side].append(self.card)
        if "sort-product-name" in classes:
            self.in_name = True
        if "sort-technical" in classes:
            self.in_technical = True
            self.technical_is_closed = "open" not in attrs

    def handle_endtag(self, tag):
        if tag == "strong":
            self.in_name = False
        if tag == "details":
            self.in_technical = False

    def handle_data(self, data):
        if self.in_name:
            self.card["name"] += data
        (self.technical if self.in_technical else self.visible).append(data)


def check(wanted=None, found=None, *, passed=True, by="name", descending=True, values=None):
    wanted = ["name:Casaco", "name:Bolsa", "name:Abajur"] if wanted is None else wanted
    found = wanted[:] if found is None else found
    return {
        "kind": "product_sort_order", "passed": passed,
        "expected": {"by": by, "descending": descending, "products": wanted},
        "actual": {"products": found, "values": values or []},
        "message": "The visible products must preserve membership and follow the declared sort order.",
    }


def render(assertion):
    before = deepcopy(assertion)
    document = StandaloneHtmlReport._assertion_detail(assertion)
    assert assertion == before
    return document, SortParser(document)


def names(parsed, side):
    return [item["name"] for item in parsed.rows[side]]


def states(parsed, side):
    return [item["state"] for item in parsed.rows[side]]


def test_matching_sequences_use_two_numbered_rows_and_closed_original_json():
    assertion = check()
    document, parsed = render(assertion)
    assert parsed.result == "pass"
    assert list(parsed.rows) == ["expected", "actual"]
    assert names(parsed, "expected") == names(parsed, "actual") == ["Casaco", "Bolsa", "Abajur"]
    assert states(parsed, "actual") == ["match"] * 3
    assert document.count('aria-label="Posição 1"') == 2
    assert "Ordem esperada" in parsed.visible and "Ordem encontrada" in parsed.visible
    assert "products" not in "".join(parsed.visible)
    assert parsed.technical_is_closed
    assert json.loads("".join(parsed.technical).replace("Ver dados técnicos", "")) == {
        key: assertion[key] for key in ("kind", "expected", "actual", "passed")
    }
    assert 'style="--sort-columns: 3"' in document
    assert 'tabindex="0"' in document


@pytest.mark.parametrize("by,descending,label", [
    ("name", False, "Nome: de A a Z"), ("name", True, "Nome: de Z a A"),
    ("price", False, "Preço: do menor para o maior"), ("price", True, "Preço: do maior para o menor"),
    ("unknown", True, "Critério não registrado"), ("name", 1, "Critério não registrado"),
    ({"untrusted": "name"}, True, "Critério não registrado"),
])
def test_sort_criterion_is_readable_and_strictly_typed(by, descending, label):
    _, parsed = render(check(by=by, descending=descending))
    assert label in parsed.visible


def test_wrong_order_is_not_resorted_for_display_and_differences_stay_failed():
    _, parsed = render(check(found=["name:Abajur", "name:Bolsa", "name:Casaco"], passed=False))
    assert parsed.result == "fail"
    assert names(parsed, "actual") == ["Abajur", "Bolsa", "Casaco"]
    assert states(parsed, "actual") == ["different", "match", "different"]
    assert "Mesmo produto nesta posição" in parsed.visible


def test_equal_price_reorder_pass_is_equivalent_not_red():
    _, parsed = render(check(["sku:a", "sku:b"], ["sku:b", "sku:a"], by="price", descending=False, values=[9.99, 9.99]))
    assert parsed.result == "pass"
    assert names(parsed, "actual") == ["sku:b", "sku:a"]
    assert states(parsed, "actual") == ["equivalent", "equivalent"]
    assert "não reprovam o teste" in "".join(parsed.visible)
    assert "Preço observado: 9,99" in parsed.visible
    assert "$" not in "".join(parsed.visible)


def test_same_sequence_with_failed_values_never_becomes_a_pass():
    _, parsed = render(check(passed=False, by="price", values=[1, 3, 2]))
    assert parsed.result == "fail"
    assert "verificação dos valores de ordenação não passou" in "".join(parsed.visible)


@pytest.mark.parametrize("found,actual_names,actual_states", [
    (["name:Casaco"], ["Casaco", "Não encontrado", "Não encontrado"], ["match", "missing", "missing"]),
    (["name:Casaco", "name:Bolsa", "name:Abajur", "name:Extra"], ["Casaco", "Bolsa", "Abajur", "Extra"], ["match", "match", "match", "different"]),
    (["name:Casaco", "name:Bolsa", "name:Bolsa"], ["Casaco", "Bolsa", "Bolsa"], ["match", "match", "different"]),
])
def test_missing_extra_and_duplicate_products_keep_their_recorded_positions(found, actual_names, actual_states):
    _, parsed = render(check(found=found, passed=False))
    assert names(parsed, "actual") == actual_names
    assert states(parsed, "actual") == actual_states
    assert len(parsed.rows["actual"]) == len(parsed.rows["expected"])


@pytest.mark.parametrize("passed", [None, "true", 1])
def test_sequence_equality_does_not_infer_a_boolean_result(passed):
    _, parsed = render(check(passed=passed))
    assert parsed.result == "unknown"
    assert states(parsed, "actual") == ["unverified"] * 3


@pytest.mark.parametrize("invalid", [None, "not a list", ["name:Casaco", None], [True], [{}], [""]])
def test_incomplete_sequences_are_not_filtered_or_reconstructed(invalid):
    assertion = check()
    assertion["actual"]["products"] = invalid
    _, parsed = render(assertion)
    assert names(parsed, "actual") == []
    assert states(parsed, "expected") == ["unverified"] * 3
    assert "não foram reconstruídos" in "".join(parsed.visible)


def test_no_products_does_not_create_fake_product_cards():
    _, parsed = render(check([], []))
    assert names(parsed, "actual") == names(parsed, "expected") == []
    assert "Nenhum produto registrado." in parsed.visible


def test_product_labels_are_escaped_and_only_known_name_prefix_is_removed():
    payload = '<img src=x onerror="alert(1)">'
    document, parsed = render(check(["name:" + payload, "id:SKU-42", "Legacy product", "sku:other"]))
    assert names(parsed, "actual") == [payload, "Produto · ID SKU-42", "Legacy product", "sku:other"]
    assert "<img" not in document and "&lt;img" in document


@pytest.mark.parametrize("value", [True, None, float("nan"), float("inf"), "9.99"])
def test_price_display_does_not_invent_invalid_or_untyped_values(value):
    _, parsed = render(check(["name:Casaco"], by="price", values=[value]))
    assert "Preço observado" not in "".join(parsed.visible)


def test_contradictory_pass_cannot_call_membership_changes_a_tie():
    _, parsed = render(check(found=["name:Casaco", "name:Bolsa", "name:Outro"], passed=True))
    assert parsed.result == "pass"
    assert "equivalent" not in states(parsed, "actual")
    assert "As listas registradas têm produtos diferentes" in "".join(parsed.visible)


def test_visual_sorting_is_included_in_complete_report_and_preserves_evidence(tmp_path):
    scenarios = []
    for number, assertion in enumerate([
        check(), check(found=["name:Abajur", "name:Bolsa", "name:Casaco"], passed=False),
        check(["name:Produto A", "name:Produto B"], ["name:Produto B", "name:Produto A"], by="price", descending=False, values=[9.99, 9.99]),
    ], start=1):
        scenarios.append({
            "id": f"CT{number:03d}", "status": "PASS" if assertion["passed"] else "FAIL",
            "url": "https://shop.test/products", "title": "Ordenação dos produtos",
            "evidence": {"assertions": [deepcopy(assertion)], "timeline": [{
                "id": f"sort-{number}", "kind": "assertion", "title": "Ordenação dos produtos",
                "occurred_at": "2026-10-09T12:00:00Z", "url": "https://shop.test/products",
                "assertion": deepcopy(assertion),
            }]},
        })
    run = {"id": "sort-cards", "status": "completed", "app_url": "https://shop.test/products",
           "timeline_version": TIMELINE_VERSION, "scenarios": scenarios,
           "summary": {"total_scenarios": 3, "pass": 2, "fail": 1}}
    before = deepcopy(run)
    path = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(path))
    document = path.read_text()
    assert document.count('class="assertion-detail sort-verification"') == 3
    assert 'data-status="FAIL"' in document and 'data-status="PASS"' in document
    assert run == before
