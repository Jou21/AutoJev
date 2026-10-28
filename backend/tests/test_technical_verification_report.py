"""Readable technical comparisons must never invent counters or a new verdict."""

from copy import deepcopy
from html.parser import HTMLParser
import json

import pytest

from autojev.adapters.report import StandaloneHtmlReport
from autojev.services.scenario_timeline import TIMELINE_VERSION


EXPECTED_ZERO = "0 attributable application runtime, HTTP or network failures"
EXPECTED_ZERO_PT = "Nenhuma falha de execução, HTTP ou rede atribuível à aplicação"
CHANNELS = ("console_errors", "network_errors")


class Node:
    def __init__(self, tag="root", attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []

    def has_class(self, name):
        return name in self.attrs.get("class", "").split()

    def find_all(self, name):
        found = [self] if self.has_class(name) else []
        for child in self.children:
            if isinstance(child, Node):
                found.extend(child.find_all(name))
        return found

    def text(self):
        return "".join(child.text() if isinstance(child, Node) else child for child in self.children)


class TechnicalParser(HTMLParser):
    def __init__(self, document):
        super().__init__()
        self.root = Node()
        self.stack = [self.root]
        self.feed(document)

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


def check(*, console=0, network=0, expected=EXPECTED_ZERO, passed=True, kind="no_new_browser_errors"):
    return {
        "kind": kind, "passed": passed, "expected": deepcopy(expected),
        "actual": {"console_errors": console, "network_errors": network},
        "message": "Observed",
    }


def render(assertion):
    before = json.dumps(assertion, ensure_ascii=False)
    document = StandaloneHtmlReport._assertion_detail(assertion)
    assert json.dumps(assertion, ensure_ascii=False) == before
    parser = TechnicalParser(document)
    sections = parser.root.find_all("technical-verification")
    assert len(sections) == 1
    return document, sections[0]


def channels(section):
    return {node.attrs["data-channel"]: node for node in section.find_all("technical-channel")}


def count(channel, side):
    values = [node for node in channel.find_all("technical-value") if node.attrs.get("data-side") == side]
    assert len(values) == 1
    counts = values[0].find_all("technical-value-count")
    assert len(counts) == 1
    return counts[0].text().strip()


def visible_text(node):
    if node.tag == "details" and node.has_class("technical-raw"):
        return ""
    return "".join(visible_text(child) if isinstance(child, Node) else child for child in node.children)


def raw_json(section):
    details = section.find_all("technical-raw")
    assert len(details) == 1
    assert details[0].tag == "details" and "open" not in details[0].attrs
    pre_nodes = []

    def visit(node):
        if node.tag == "pre":
            pre_nodes.append(node)
        for child in node.children:
            if isinstance(child, Node):
                visit(child)

    visit(details[0])
    assert len(pre_nodes) == 1
    return json.loads(pre_nodes[0].text())


@pytest.mark.parametrize("kind", ["no_new_browser_errors", "no_new_application_errors"])
def test_two_zero_channels_are_readable_matches_with_closed_original_data(kind):
    assertion = check(kind=kind)
    document, section = render(assertion)
    assert section.tag == "section" and section.attrs["data-result"] == "pass"
    items = channels(section)
    assert list(items) == list(CHANNELS)
    for channel in items.values():
        assert channel.tag == "article" and channel.attrs["data-state"] == "match"
        assert count(channel, "expected") == count(channel, "actual") == "0"
        comparisons = channel.find_all("technical-comparison")
        assert len(comparisons) == 1
        icons = comparisons[0].find_all("technical-pair-icon")
        assert len(icons) == 1 and icons[0].tag == "svg"
        pair = comparisons[0].find_all("technical-pair")
        assert len(pair) == 1 and pair[0].attrs["role"] == "img"
        assert "Zero esperado e zero registrado" in pair[0].attrs["aria-label"]
        assert "não é o resultado da verificação inteira" in pair[0].attrs["aria-label"]
        comparison_html = document[document.index('class="technical-comparison"'):]
        assert comparison_html.index('data-side="expected"') < comparison_html.index('technical-pair-icon') < comparison_html.index('data-side="actual"')
    assert "console_errors" not in visible_text(section)
    assert "network_errors" not in visible_text(section)
    assert "{" not in visible_text(section)
    assert raw_json(section) == assertion


@pytest.mark.parametrize("console,network,states", [
    (0, 2, ("match", "different")),
    (1, 0, ("different", "match")),
    (1, 2, ("different", "different")),
])
def test_failed_check_compares_each_channel_without_changing_the_overall_verdict(console, network, states):
    _, section = render(check(console=console, network=network, passed=False))
    assert section.attrs["data-result"] == "fail"
    items = channels(section)
    assert [items[channel].attrs["data-state"] for channel in CHANNELS] == list(states)
    assert [count(items[channel], "actual") for channel in CHANNELS] == [str(console), str(network)]
    assert raw_json(section)["passed"] is False


@pytest.mark.parametrize("passed", [None, 1, "true"])
def test_equal_counters_never_infer_a_strict_boolean_verdict(passed):
    _, section = render(check(passed=passed))
    assert section.attrs["data-result"] == "unknown"
    assert [item.attrs["data-state"] for item in channels(section).values()] == ["unverified"] * 2


def test_failed_verdict_with_zero_counters_is_preserved_and_not_called_a_match():
    _, section = render(check(passed=False))
    assert section.attrs["data-result"] == "fail"
    assert [item.attrs["data-state"] for item in channels(section).values()] == ["unverified"] * 2
    assert raw_json(section)["passed"] is False


@pytest.mark.parametrize("expected", [
    EXPECTED_ZERO, EXPECTED_ZERO_PT, "0 new console or network errors", 0, "0",
    {"console_errors": 0, "network_errors": 0},
])
def test_only_recognized_zero_expectations_can_render_a_confirmed_pair(expected):
    _, section = render(check(expected=expected))
    for item in channels(section).values():
        assert item.attrs["data-state"] == "match"
        assert count(item, "expected") == "0"


@pytest.mark.parametrize("expected", [None, True, "no errors hopefully", "zero", [], {"errors": 0}, {"console_errors": True, "network_errors": "0"}])
def test_unrecognized_expectation_does_not_invent_a_zero_target(expected):
    _, section = render(check(expected=expected))
    for item in channels(section).values():
        assert item.attrs["data-state"] == "unverified"
        assert count(item, "expected") != "0"
        assert count(item, "actual") == "0"


def test_partially_recorded_expected_counts_do_not_fill_the_other_channel():
    _, section = render(check(expected={"console_errors": 0}))
    items = channels(section)
    assert items["console_errors"].attrs["data-state"] == "match"
    assert count(items["console_errors"], "expected") == "0"
    assert items["network_errors"].attrs["data-state"] == "unverified"
    assert count(items["network_errors"], "expected") != "0"


@pytest.mark.parametrize("invalid", [None, True, False, -1, 0.0, "0", [], {}, float("inf"), float("nan")])
def test_invalid_actual_count_is_not_coerced_filtered_or_replaced_with_zero(invalid):
    assertion = check(console=invalid)
    _, section = render(assertion)
    items = channels(section)
    assert items["console_errors"].attrs["data-state"] == "unverified"
    assert count(items["console_errors"], "actual") != "0"
    assert items["network_errors"].attrs["data-state"] == "match"
    assert count(items["network_errors"], "actual") == "0"


@pytest.mark.parametrize("actual", [None, 0, "0", [], "2 console errors", {"errors": 2}])
def test_legacy_scalar_or_unrecognized_actual_is_not_split_into_invented_channel_counts(actual):
    assertion = check()
    assertion["actual"] = actual
    _, section = render(assertion)
    for item in channels(section).values():
        assert item.attrs["data-state"] == "unverified"
        assert count(item, "actual") != "0"
    assert raw_json(section)["actual"] == actual


def test_absent_actual_channel_remains_unknown_while_present_channel_can_match():
    assertion = check()
    del assertion["actual"]["network_errors"]
    _, section = render(assertion)
    items = channels(section)
    assert items["console_errors"].attrs["data-state"] == "match"
    assert items["network_errors"].attrs["data-state"] == "unverified"
    assert count(items["network_errors"], "actual") != "0"
    assert "network_errors" not in raw_json(section)["actual"]


def test_contradictory_pass_with_positive_counter_preserves_original_result_without_claiming_no_errors():
    _, section = render(check(console=1, passed=True))
    assert section.attrs["data-result"] == "pass"
    assert channels(section)["console_errors"].attrs["data-state"] == "different"
    assert raw_json(section)["passed"] is True
    headings = []

    def headings_in(node):
        if node.tag in {"h3", "h4"}:
            headings.append(node.text().lower())
        for child in node.children:
            if isinstance(child, Node):
                headings_in(child)

    headings_in(section)
    assert headings
    assert not any("nenhuma falha" in title or "nenhum erro" in title or "sem falhas" in title for title in headings)
    assert "preservado" in visible_text(section).lower()


def test_untrusted_original_data_is_escaped_and_only_present_in_collapsed_details():
    payload = '<img src=x onerror="alert(1)">'
    assertion = check(expected=payload)
    assertion["actual"] = {"console_errors": payload, "network_errors": 0}
    assertion["message"] = payload
    document, section = render(assertion)
    assert "<img" not in document and "&lt;img" in document
    assert payload not in visible_text(section)
    assert raw_json(section) == assertion


def test_visual_technical_checks_are_in_full_report_without_changing_saved_evidence(tmp_path):
    scenarios = []
    assertions = [check(), check(network=2, passed=False), check(passed=None)]
    del assertions[2]["actual"]["console_errors"]
    for number, assertion in enumerate(assertions, start=1):
        scenarios.append({
            "id": f"CT{number:03d}", "status": ["PASS", "FAIL", "SKIPPED"][number - 1],
            "url": "https://shop.test/products", "title": "Ausência de novas falhas técnicas da aplicação",
            "evidence": {"assertions": [deepcopy(assertion)], "timeline": [{
                "id": f"technical-{number}", "kind": "assertion",
                "title": "Ausência de novas falhas técnicas da aplicação",
                "occurred_at": "2026-10-09T12:00:00Z", "url": "https://shop.test/products",
                "assertion": deepcopy(assertion),
            }]},
        })
    run = {
        "id": "technical-cards", "status": "completed", "app_url": "https://shop.test/products",
        "timeline_version": TIMELINE_VERSION, "scenarios": scenarios,
        "summary": {"total_scenarios": 3, "pass": 1, "fail": 1, "skipped": 1},
    }
    before = deepcopy(run)
    path = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(path))
    document = path.read_text()
    sections = TechnicalParser(document).root.find_all("technical-verification")
    assert len(sections) == 3
    assert [section.attrs["data-result"] for section in sections] == ["pass", "fail", "unknown"]
    assert 'data-status="FAIL"' in document and 'data-status="PASS"' in document
    assert run == before


def test_failed_technical_check_names_the_request_that_tripped_it():
    signal = {
        "type": "http", "label": "Falha HTTP da aplicação", "method": "GET", "status_code": 404,
        "url": "https://www.saucedemo.com/inventory.html", "affects_verdict": True,
        "occurrence_id": "signal-1",
    }
    unrelated = {**signal, "occurrence_id": "signal-2", "affects_verdict": False, "url": "https://ads.example/x"}
    entries = [
        {"kind": "signal", "signal": signal},
        {"kind": "signal", "signal": unrelated},
        {"kind": "assertion", "assertion": check(network=1, passed=False)},
    ]
    found = StandaloneHtmlReport._failing_signals_for_assertion(entries, 2)
    assert found == [signal]

    document = StandaloneHtmlReport._assertion_detail(entries[2]["assertion"], signals=found)
    assert "O que reprovou esta verificação" in document
    assert "GET · HTTP 404" in document
    assert "https://www.saucedemo.com/inventory.html" in document
    assert "ads.example" not in document


def test_signals_before_a_previous_technical_check_are_not_attributed_to_the_next_one():
    earlier = {"type": "http", "affects_verdict": True, "occurrence_id": "a", "url": "https://a.test/"}
    entries = [
        {"kind": "signal", "signal": earlier},
        {"kind": "assertion", "assertion": check(network=1, passed=False)},
        {"kind": "assertion", "assertion": check(passed=True)},
    ]
    assert StandaloneHtmlReport._failing_signals_for_assertion(entries, 2) == []


def test_passing_technical_check_lists_no_occurrences():
    document = StandaloneHtmlReport._assertion_detail(check(), signals=[{"affects_verdict": True, "url": "https://a.test/"}])
    assert "technical-occurrences" not in document


def test_failing_signal_step_no_longer_claims_it_cannot_fail_the_test():
    entry = {"kind": "signal", "signal": {"affects_verdict": True}}
    _, outcome, description = StandaloneHtmlReport._step_presentation(entry, "signal")
    assert outcome == "Falha técnica registrada" and "reprova a verificação técnica" in description
    _, outcome, description = StandaloneHtmlReport._step_presentation({"kind": "signal", "signal": {"affects_verdict": False}}, "signal")
    assert outcome == "Ocorrência registrada" and "não reprova o teste" in description


def test_www_and_bare_domain_are_the_same_application_origin():
    from autojev.services.technical_signals import classify_signal

    issue = {"type": "http", "status_code": 404, "url": "https://www.saucedemo.com/inventory.html", "message": "GET returned HTTP 404"}
    for app_url in ("https://saucedemo.com/", "https://www.saucedemo.com/"):
        assert classify_signal(issue, app_url)["affects_verdict"] is True
    external = classify_signal({**issue, "url": "https://www.other.com/x"}, "https://saucedemo.com/")
    assert external["affects_verdict"] is False and external["classification"] == "external_dependency"


def test_before_and_after_captures_name_the_action_they_frame():
    entries = [
        {"kind": "observation", "title": "Antes da ação"},
        {"kind": "action", "title": "Navegar"},
        {"kind": "observation", "title": "Depois da ação"},
        {"kind": "assertion", "title": "Ausência de novas falhas técnicas da aplicação"},
    ]
    title = StandaloneHtmlReport._action_aware_title
    assert title(entries, 0) == "Antes de navegar"
    assert title(entries, 2) == "Depois de navegar"
    assert title(entries, 1) is None and title(entries, 3) is None


def test_capture_titles_do_not_cross_into_another_attempt_or_invent_an_action():
    title = StandaloneHtmlReport._action_aware_title
    assert title([{"kind": "observation", "title": "Antes da ação"}], 0) is None
    two_attempts = [
        {"kind": "observation", "title": "Antes da ação"},
        {"kind": "observation", "title": "Antes da ação"},
        {"kind": "action", "title": "Clicar"},
    ]
    assert title(two_attempts, 0) is None and title(two_attempts, 1) == "Antes de clicar"
    assert title([{"kind": "action", "title": ""}, {"kind": "observation", "title": "Depois da ação"}], 1) is None
