"""The midpoint indicators compare product pairs without inventing verdicts."""

from copy import deepcopy
from html.parser import HTMLParser
import json

import pytest

from autojev.adapters.report import StandaloneHtmlReport


class Node:
    def __init__(self, tag, attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []

    def has_class(self, name):
        return name in self.attrs.get("class", "").split()

    def find_all(self, name):
        found = []
        for child in self.children:
            if isinstance(child, Node):
                if child.has_class(name):
                    found.append(child)
                found.extend(child.find_all(name))
        return found

    def text(self):
        return "".join(child.text() if isinstance(child, Node) else child for child in self.children)


class SortDocument(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.root = Node("document")
        self.stack = [self.root]
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for position in range(len(self.stack) - 1, 0, -1):
            if self.stack[position].tag == tag:
                del self.stack[position:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def assertion(wanted=None, found=None, *, passed=True, by="name", values=None):
    wanted = ["name:Casaco", "name:Bolsa", "name:Abajur"] if wanted is None else wanted
    found = wanted[:] if found is None else found
    return {
        "kind": "product_sort_order",
        "passed": passed,
        "expected": {"by": by, "descending": True, "products": wanted},
        "actual": {"products": found, "values": [] if values is None else values},
    }


def render(check):
    original = deepcopy(check)
    markup = StandaloneHtmlReport._assertion_detail(check)
    assert check == original
    return markup, SortDocument(markup).root


def pairs(root):
    return root.find_all("sort-pair")


def test_found_heading_follows_its_cards_and_indicators_are_between_rows():
    _, root = render(assertion())
    scroll = root.find_all("sort-comparison-scroll")[0]
    sections = [child for child in scroll.children if isinstance(child, Node)]
    assert len(sections) == 3
    expected, indicators, actual = sections
    assert expected.has_class("sort-row") and expected.attrs["data-side"] == "expected"
    assert indicators.tag == "ol" and indicators.has_class("sort-pairs")
    assert actual.has_class("sort-row") and actual.attrs["data-side"] == "actual"
    for row, wanted_order in ((expected, ["sort-row-heading", "sort-products"]), (actual, ["sort-products", "sort-row-heading"])):
        children = [child for child in row.children if isinstance(child, Node)]
        assert len(children) == 2
        assert all(child.has_class(name) for child, name in zip(children, wanted_order))
    assert "Ordem esperada" in expected.find_all("sort-row-heading")[0].text()
    found_heading = actual.find_all("sort-row-heading")[0].text()
    assert "Ordem encontrada" in found_heading and "3 produtos" in found_heading


def test_each_pair_has_one_accessible_svg_without_duplicate_card_icons():
    _, root = render(assertion())
    assert len(pairs(root)) == 3
    for pair in pairs(root):
        assert pair.tag == "li"
        label = pair.attrs.get("aria-label", "")
        assert len(label.strip()) > 12
        assert "{" not in label and "product_sort_order" not in label
        icons = pair.find_all("sort-pair-icon")
        assert len(icons) == 1 and icons[0].tag == "svg"
        assert icons[0].attrs.get("aria-hidden") == "true"
    assert root.find_all("sort-card-icon") == []
    assert len(root.find_all("sort-state-icon")) == 1


@pytest.mark.parametrize("check,expected_states,result", [
    (assertion(), ["match"] * 3, "pass"),
    (assertion(found=["name:Abajur", "name:Bolsa", "name:Casaco"], passed=False), ["different", "match", "different"], "fail"),
    (assertion(["name:A", "name:B"], ["name:B", "name:A"], by="price", values=[9.99, 9.99]), ["equivalent"] * 2, "pass"),
    (assertion(["id:A", "id:B"], ["id:B", "id:A"], by="name"), ["equivalent"] * 2, "pass"),
    (assertion(found=["name:Casaco"], passed=False), ["match", "missing", "missing"], "fail"),
    (assertion(["name:A", "name:B"], ["name:A", "name:B", "name:Extra"], passed=False), ["match", "match", "missing"], "fail"),
    (assertion(found=["name:Casaco", "name:Bolsa", "name:Bolsa"], passed=False), ["match", "match", "different"], "fail"),
    (assertion(["name:A", "name:A", "name:B"], ["name:A", "name:B", "name:A"], passed=False), ["match", "different", "different"], "fail"),
    (assertion(passed=False, by="price", values=[1, 3, 2]), ["match"] * 3, "fail"),
    (assertion(found=["name:Casaco", "name:Bolsa", "name:Outro"]), ["match", "match", "different"], "pass"),
    (assertion(["name:A", "name:B"], ["name:B", "name:A"], by="unknown"), ["different"] * 2, "pass"),
    (assertion([], ["name:A", "name:B"], passed=False), ["missing"] * 2, "fail"),
    (assertion([], []), [], "pass"),
])
def test_pair_states_follow_recorded_positions_and_preserve_verdict(check, expected_states, result):
    _, root = render(check)
    assert [pair.attrs["data-state"] for pair in pairs(root)] == expected_states
    assert root.find_all("sort-verification")[0].attrs["data-result"] == result


@pytest.mark.parametrize("passed", [None, "true", 1])
def test_unknown_result_has_no_green_confirmation_even_when_products_match(passed):
    _, root = render(assertion(passed=passed))
    assert [pair.attrs["data-state"] for pair in pairs(root)] == ["unverified"] * 3
    assert root.find_all("sort-verification")[0].attrs["data-result"] == "unknown"


@pytest.mark.parametrize("invalid", [None, "not a sequence", ["name:Casaco", None], [{}], [""]])
def test_malformed_sequence_is_not_shifted_or_reconstructed(invalid):
    check = assertion()
    check["actual"]["products"] = invalid
    _, root = render(check)
    assert [pair.attrs["data-state"] for pair in pairs(root)] == ["unverified"] * 3
    assert root.find_all("sort-row")[1].find_all("sort-product") == []


def test_both_missing_sequences_have_no_artificial_pair_or_product():
    check = assertion()
    check["expected"]["products"] = None
    check["actual"]["products"] = None
    _, root = render(check)
    assert pairs(root) == [] and root.find_all("sort-product") == []


def test_card_names_and_original_technical_json_are_preserved():
    payload = '<img src=x onerror="alert(1)">'
    check = assertion(["name:" + payload, "id:SKU-42", "Legacy name"], ["Legacy name", "id:SKU-42", "name:" + payload], passed=False)
    markup, root = render(check)
    rows = root.find_all("sort-row")
    assert [name.text() for name in rows[0].find_all("sort-product-name")] == [payload, "Produto · ID SKU-42", "Legacy name"]
    assert [name.text() for name in rows[1].find_all("sort-product-name")] == ["Legacy name", "Produto · ID SKU-42", payload]
    assert "<img" not in markup and "&lt;img" in markup
    technical = root.find_all("sort-technical")[0]
    assert "open" not in technical.attrs
    raw = next(child for child in technical.children if isinstance(child, Node) and child.tag == "pre")
    assert json.loads(raw.text()) == check


def test_match_indicator_does_not_override_overall_failed_sort_values():
    _, root = render(assertion(passed=False, by="price", values=[1, 3, 2]))
    assert [pair.attrs["data-state"] for pair in pairs(root)] == ["match"] * 3
    assert root.find_all("sort-verification")[0].attrs["data-result"] == "fail"
    assert "verificação dos valores de ordenação não passou" in root.text()
