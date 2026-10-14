"""CT summaries and step colors describe recorded evidence, not new verdicts."""

from copy import deepcopy
from html.parser import HTMLParser
from unittest.mock import patch

import pytest

from autojev.adapters.report import StandaloneHtmlReport


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


class SummaryParser(HTMLParser):
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, document):
        super().__init__()
        self.root = Node()
        self.stack = [self.root]
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for position in range(len(self.stack) - 1, 0, -1):
            if self.stack[position].tag == tag:
                del self.stack[position:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def one(node, name):
    matches = node.find_all(name)
    assert len(matches) == 1, name
    return matches[0]


def without_documentation(node):
    if node.has_class("case-documentation"):
        return ""
    return "".join(without_documentation(child) if isinstance(child, Node) else child for child in node.children)


@pytest.fixture
def report(tmp_path):
    screenshots = tmp_path / "run" / "screenshots"
    screenshots.mkdir(parents=True)
    (screenshots / "before.png").write_bytes(b"\x89PNG\r\n\x1a\nbefore")
    return StandaloneHtmlReport(tmp_path)


def selection(requested="Name (Z to A)"):
    return {
        "version": "select-labels-v1", "status": "confirmed",
        "requested_label": requested, "before_label": "Name (A to Z)",
        "after_label": requested,
    }


def action(**changes):
    return {
        "kind": "action", "title": "Ação: select", "target": "e2",
        "step_number": 1, "success": True,
        "occurred_at": "2026-10-09T12:00:01Z", "url": "https://shop.test/products",
        "action_preview": {
            "version": "action-target-v1", "coordinate_space": "screenshot-css-pixels",
            "action": "select", "target": "e2", "screenshot": "before.png",
            "captured_at": "2026-10-09T12:00:00Z", "url": "https://shop.test/products",
            "image": {"width": 1440, "height": 900},
            "box": {"x": 1100, "y": 80, "width": 190, "height": 32},
            "selection": selection(),
        },
        **changes,
    }


def check(kind="selection_matches", passed=True):
    return {
        "kind": kind, "passed": passed,
        "expected": {"option_matches": True}, "actual": {"option_matches": True},
        "message": "Observed",
    }


def assertion(assertion_value=None, **changes):
    return {
        "kind": "assertion", "title": "Verificação observada", "step_number": 1,
        "occurred_at": "2026-10-09T12:00:02Z", "url": "https://shop.test/products",
        "assertion": check() if assertion_value is None else assertion_value,
        **changes,
    }


def scenario(**changes):
    checks = [
        check("selection_matches"), check("observable_state_change"),
        {"kind": "no_new_browser_errors", "passed": True,
         "expected": "0 attributable application runtime, HTTP or network failures",
         "actual": {"console_errors": 0, "network_errors": 0}, "message": "Observed"},
    ]
    return {
        "id": "CT001", "title": "Ordenação dos produtos", "status": "PASS",
        "description": "Original description retained only as documentation.",
        "url": "https://shop.test/products", "steps": ["Selecionar uma opção"],
        "expected_result": "Resultado documentado", "actual_result": "Resultado registrado",
        "evidence": {
            "action": "select", "action_succeeded": True, "assertions": checks,
            "timeline": [action(), *[assertion(deepcopy(item)) for item in checks]],
        },
        **changes,
    }


def card(report, value):
    original = deepcopy(value)
    document = report._scenario_card("run", value)
    assert value == original
    root = SummaryParser(document).root
    overview = one(root, "ct-overview")
    assert overview.tag == "section"
    assert one(overview, "ct-overview-result").text().strip()
    assert one(overview, "ct-summary-action").text().strip()
    assert one(overview, "ct-summary-scope").text().strip()
    return document, root, overview


def timeline(report, entries):
    value = {"id": "CT001", "evidence": {"timeline": entries}}
    original = deepcopy(value)
    document = report._timeline("run", value)
    assert value == original
    nodes = SummaryParser(document).root.find_all("timeline-entry")
    assert len(nodes) == len(entries)
    for node in nodes:
        assert node.tag == "li"
        assert node.attrs.get("data-step-outcome", "").strip()
        assert node.attrs.get("data-step-description", "").strip()
    return document, nodes


def test_pass_summary_has_three_recorded_checks_and_a_human_action(report):
    value = scenario()
    _, _, overview = card(report, value)
    assert overview.attrs["data-result"] == "PASS"
    checks = overview.find_all("ct-summary-check")
    assert len(checks) == 3  # The same checks also occur in the timeline.
    assert all(item.tag == "li" and item.attrs["data-state"] == "pass" for item in checks)
    summary_action = one(overview, "ct-summary-action").text()
    assert "selecion" in summary_action.lower()
    assert "Name (Z to A)" in summary_action
    for raw_kind in ("selection_matches", "observable_state_change", "no_new_browser_errors"):
        assert raw_kind not in overview.text()
    assert "option_matches" not in overview.text()
    assert "{" not in overview.text()


@pytest.mark.parametrize("passed", [None, 0, 1, "true", "false", "", [], {}])
def test_unknown_summary_check_never_coerces_a_non_boolean_result(report, passed):
    value = scenario()
    value["evidence"]["assertions"] = [check(passed=passed)]
    value["evidence"]["timeline"] = [action(), assertion(check(passed=passed))]
    _, _, overview = card(report, value)
    checks = overview.find_all("ct-summary-check")
    assert len(checks) == 1
    assert checks[0].attrs["data-state"] == "unknown"
    assert overview.attrs["data-result"] == "PASS"  # Preserve the recorded CT result.


def test_missing_assertion_verdict_remains_unknown(report):
    value = scenario()
    unknown = check()
    del unknown["passed"]
    value["evidence"]["assertions"] = [unknown]
    value["evidence"]["timeline"] = [assertion(deepcopy(unknown))]
    _, _, overview = card(report, value)
    assert one(overview, "ct-summary-check").attrs["data-state"] == "unknown"


def test_failed_visual_result_is_not_overridden_by_passing_local_checks(report):
    value = scenario(status="FAIL")
    value["evidence"].update(
        verdict={"kind": "visual_failure", "label": "Indício visual", "explanation": "Revisão necessária"},
        visual_assessment={"requested": True, "status": "assessed", "accepted_count": 1},
    )
    _, root, overview = card(report, value)
    assert overview.attrs["data-result"] == "FAIL"
    assert all(item.attrs["data-state"] == "pass" for item in overview.find_all("ct-summary-check"))
    headline = one(overview, "ct-overview-result").text().lower()
    assert any(word in headline for word in ("reprov", "falh", "visual"))
    visual = one(root, "ct-summary-visual")
    assert any(word in visual.text().lower() for word in ("visual", "imagens", "capturas"))
    assert visual.attrs.get("data-state") != "pass"
    assert not visual.has_class("ct-summary-check")
    assert "não" in visual.text().lower()
    assert "funcional" in visual.text().lower()


@pytest.mark.parametrize("status", ["PASS", "FAIL", "BLOCKED", "SKIPPED"])
def test_preserved_status_is_independent_of_checks_and_action_success(report, status):
    value = scenario(status=status)
    value["evidence"]["assertions"] = [check(passed=False)]
    value["evidence"]["timeline"] = [action(success=False), assertion(check(passed=False))]
    _, root, overview = card(report, value)
    assert one(root, "scenario").attrs["data-status"] == status
    assert overview.attrs["data-result"] == status
    assert one(overview, "ct-summary-check").attrs["data-state"] == "fail"


def test_invalid_status_keeps_the_existing_skipped_fallback(report):
    _, root, overview = card(report, scenario(status="not-a-result"))
    assert one(root, "scenario").attrs["data-status"] == "SKIPPED"
    assert overview.attrs["data-result"] == "SKIPPED"


def test_no_assertions_does_not_invent_a_business_check_or_global_approval(report):
    value = scenario()
    value["evidence"]["assertions"] = []
    value["evidence"]["timeline"] = [action()]
    _, _, overview = card(report, value)
    assert overview.find_all("ct-summary-check") == []
    assert any(word in overview.text().lower() for word in ("nenhuma", "sem verifica", "não há"))
    scope = one(overview, "ct-summary-scope").text().lower()
    assert any(word in scope for word in ("não", "apenas", "limit", "somente"))


def test_timeline_only_assertions_fill_the_summary_without_mutating_the_source(report):
    value = scenario()
    del value["evidence"]["assertions"]
    _, _, overview = card(report, value)
    checks = overview.find_all("ct-summary-check")
    assert len(checks) == 3
    assert [item.attrs["data-state"] for item in checks] == ["pass"] * 3
    assert "não tem uma regra de negócio confirmada" not in one(overview, "ct-summary-scope").text()
    assert "assertions" not in value["evidence"]


def test_explicit_empty_assertions_remains_authoritative_over_timeline_checks(report):
    value = scenario()
    value["evidence"]["assertions"] = []
    _, _, overview = card(report, value)
    assert overview.find_all("ct-summary-check") == []
    assert "Nenhuma conferência" in overview.text()
    assert value["evidence"]["assertions"] == []
    assert len([item for item in value["evidence"]["timeline"] if item["kind"] == "assertion"]) == 3


def test_original_description_and_verdict_documentation_are_collapsed(report):
    value = scenario()
    value["evidence"]["verdict"] = {
        "kind": "technical_failure", "label": "Historical verdict label",
        "explanation": "Historical verdict explanation", "historical": True,
    }
    _, root, _ = card(report, value)
    details = one(root, "ct-summary-details")
    assert details.tag == "details" and details.has_class("case-documentation")
    assert "open" not in details.attrs
    for text in (value["description"], "Historical verdict label", "Historical verdict explanation"):
        assert text in details.text()
        assert text not in without_documentation(root)


@pytest.mark.parametrize("field,bad_value", [
    ("url", "https://shop.test/unrelated"), ("target", "e99"),
    ("version", "other-preview-v1"), ("action", "click"),
])
def test_summary_does_not_borrow_requested_option_from_unrelated_preview(report, field, bad_value):
    value = scenario()
    value["evidence"]["timeline"][0]["action_preview"][field] = bad_value
    _, _, overview = card(report, value)
    assert "Name (Z to A)" not in one(overview, "ct-summary-action").text()


def test_multiple_action_attempts_do_not_pick_a_scenario_wide_requested_label(report):
    value = scenario()
    first = action()
    second = action(step_number=2, target="e3")
    second["action_preview"].update(target="e3", selection=selection("Price (low to high)"))
    value["evidence"]["timeline"] = [first, assertion(), second, assertion()]
    value["evidence"]["requested_label"] = "Unowned option"
    _, _, overview = card(report, value)
    text = one(overview, "ct-summary-action").text()
    for label in ("Name (Z to A)", "Price (low to high)", "Unowned option"):
        assert label not in text


@pytest.mark.parametrize("requested", [None, True, 1, "", "unsafe\nlabel", "x" * 241])
def test_invalid_requested_labels_are_not_presented_as_the_action(report, requested):
    value = scenario()
    value["evidence"]["timeline"][0]["action_preview"]["selection"] = selection(requested)
    _, _, overview = card(report, value)
    text = one(overview, "ct-summary-action").text()
    assert "unsafe" not in text and "x" * 241 not in text
    assert text.strip() not in {"True", "1"}


def test_summary_escapes_recorded_option_description_and_outcome(report):
    payload = '<script>alert("summary")</script>'
    value = scenario(description=payload)
    value["evidence"]["timeline"][0]["action_preview"]["selection"] = selection(payload)
    document, root, overview = card(report, value)
    assert "<script" not in document and "&lt;script" in document
    assert payload in one(overview, "ct-summary-action").text()
    assert payload in one(root, "ct-summary-details").text()
    assert all(child.tag != "script" for child in root.children if isinstance(child, Node))


@pytest.mark.parametrize("passed,state", [(True, "pass"), (False, "fail"), (None, "unknown"), (1, "unknown"), (0, "unknown"), ("true", "unknown"), ("false", "unknown")])
def test_assertion_timeline_color_uses_only_its_strict_recorded_boolean(report, passed, state):
    entry = assertion(check(passed=passed), success=True)
    _, nodes = timeline(report, [entry])
    assert nodes[0].attrs["data-step-state"] == state
    assert "selection_matches" not in nodes[0].attrs["data-step-description"]
    assert "opção" in nodes[0].attrs["data-step-description"].lower()


@pytest.mark.parametrize("success,state", [(True, "pass"), (False, "fail"), (None, "unknown"), (1, "unknown"), (0, "unknown"), ("true", "unknown"), ("false", "unknown")])
def test_action_timeline_color_uses_only_its_strict_execution_boolean(report, success, state):
    entry = action(success=success, assertion=check(passed=True))
    _, nodes = timeline(report, [entry])
    assert nodes[0].attrs["data-step-state"] == state
    description = nodes[0].attrs["data-step-description"].lower()
    assert "ação" in description
    assert "selection_matches" not in description and "{" not in description


@pytest.mark.parametrize("screenshot,state", [("before.png", "recorded"), ("missing.png", "info"), (None, "info"), ("", "info")])
def test_observation_is_recorded_only_when_its_capture_exists_and_never_passes(report, screenshot, state):
    entry = {"kind": "observation", "title": "Estado anterior", "screenshot": screenshot, "success": True}
    _, nodes = timeline(report, [entry])
    assert nodes[0].attrs["data-step-state"] == state
    assert nodes[0].attrs["data-step-state"] != "pass"


def test_observation_state_does_not_read_and_encode_its_capture_twice(report):
    entry = {"kind": "observation", "title": "Captura", "screenshot": "before.png"}
    with patch.object(report, "_image_data", wraps=report._image_data) as image_data:
        document, nodes = timeline(report, [entry])
    assert nodes[0].attrs["data-step-state"] == "recorded"
    image_data.assert_called_once_with("run", "before.png")
    assert document.count("data:image/png;base64,") == 1


@pytest.mark.parametrize("classification", ["external_telemetry", "application_error", "unclassified"])
def test_browser_signal_is_a_warning_not_a_failed_functional_step(report, classification):
    entry = {
        "kind": "signal", "title": "Sinal técnico", "success": False,
        "signal": {"classification": classification, "message": "HTTP 401", "status_code": 401},
    }
    _, nodes = timeline(report, [entry])
    assert nodes[0].attrs["data-step-state"] == "warning"
    assert nodes[0].attrs["data-step-state"] != "fail"


def test_timeline_outcomes_are_readable_portuguese_not_internal_state_codes(report):
    entries = [
        action(), action(success=False), assertion(check(passed=None)),
        {"kind": "observation", "title": "Captura", "screenshot": "before.png"},
        {"kind": "observation", "title": "Observação sem captura"},
        {"kind": "signal", "title": "Sinal técnico"},
    ]
    _, nodes = timeline(report, entries)
    assert [node.attrs["data-step-state"] for node in nodes] == ["pass", "fail", "unknown", "recorded", "info", "warning"]
    for node in nodes:
        outcome = node.attrs["data-step-outcome"]
        assert outcome.lower() not in {"pass", "fail", "unknown", "recorded", "info", "warning", "true", "false"}
        assert "{" not in outcome
