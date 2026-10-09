"""Didactic selection checks must remain grounded in the same action attempt."""

from copy import deepcopy
from html.parser import HTMLParser
import json

import pytest

from autojev.adapters.report import StandaloneHtmlReport


class ComparisonParser(HTMLParser):
    def __init__(self, document):
        super().__init__()
        self.result = None
        self.values = []
        self.technical = []
        self.visible_text = []
        self.in_value = False
        self.in_technical = False
        self.technical_is_closed = False
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        if "selection-verification" in classes:
            self.result = attrs["data-result"]
        if "selection-choice-value" in classes:
            self.in_value = True
        if "selection-technical" in classes:
            self.in_technical = True
            self.technical_is_closed = "open" not in attrs

    def handle_endtag(self, tag):
        if tag == "strong":
            self.in_value = False
        if tag == "details":
            self.in_technical = False

    def handle_data(self, data):
        if self.in_value:
            self.values.append(data)
        if self.in_technical:
            self.technical.append(data)
        else:
            self.visible_text.append(data)


def labels(status="confirmed", requested="Name (Z to A)", actual="Name (Z to A)"):
    return {
        "version": "select-labels-v1", "status": status,
        "requested_label": requested, "before_label": "Name (A to Z)",
        "after_label": actual,
    }


def action(step=1, **changes):
    entry = {
        "kind": "action", "step_number": step, "target": "e2",
        "occurred_at": "2026-10-09T12:00:00Z", "url": "https://shop.test/products",
        "action_preview": {
            "version": "action-target-v1", "action": "select", "target": "e2",
            "captured_at": "2026-10-09T12:00:01Z", "url": "https://shop.test/products",
            "selection": labels(),
        },
    }
    entry.update(changes)
    return entry


def assertion(passed=True, **changes):
    entry = {
        "kind": "assertion", "occurred_at": "2026-10-09T12:00:02Z",
        "url": "https://shop.test/products", "title": "Opção selecionada",
        "assertion": {
            "kind": "selection_matches", "passed": passed,
            "expected": {"option_matches": True}, "actual": {"option_matches": passed},
            "message": "The selected option must match the requested value.",
        },
    }
    entry.update(changes)
    return entry


def comparison(entries):
    original = deepcopy(entries)
    selection = StandaloneHtmlReport._selection_for_assertion(entries, len(entries) - 1)
    document = StandaloneHtmlReport._assertion_detail(entries[-1]["assertion"], selection=selection)
    assert entries == original
    return document, ComparisonParser(document)


def test_pass_is_two_readable_named_cards_with_technical_values_closed():
    document, parsed = comparison([action(), assertion()])
    assert parsed.result == "pass"
    assert parsed.values == ["Name (Z to A)", "Name (Z to A)"]
    assert "A opção correta foi selecionada" in parsed.visible_text
    assert "Opção esperada" in parsed.visible_text
    assert "Opção encontrada" in parsed.visible_text
    assert "option_matches" not in "".join(parsed.visible_text)
    assert parsed.technical_is_closed
    assert '"option_matches": true' in "".join(parsed.technical)
    assert document.count('class="selection-choice-icon"') == 2


def test_fail_explains_actual_option_and_keeps_recorded_failure():
    first = action()
    first["action_preview"]["selection"] = labels("mismatch", actual="Name (A to Z)")
    _, parsed = comparison([first, assertion(False)])
    assert parsed.result == "fail"
    assert parsed.values == ["Name (Z to A)", "Name (A to Z)"]
    assert "A seleção não correspondeu ao esperado" in parsed.visible_text
    assert '"option_matches": false' in "".join(parsed.technical)


def test_localized_expected_description_remains_in_closed_technical_details():
    check = assertion()
    check["assertion"].update(
        expected="Original recorded description",
        expected_display="Descrição em português <preservada>",
    )
    document, parsed = comparison([action(), check])
    assert parsed.technical_is_closed
    assert "Descrição em português <preservada>" in "".join(parsed.technical)
    assert "Descrição em português" not in "".join(parsed.visible_text)
    assert '"expected": "Original recorded description"' in "".join(parsed.technical)
    assert "&lt;preservada&gt;" in document


def test_identical_display_names_cannot_turn_a_failed_value_check_green():
    first = action()
    first["action_preview"]["selection"] = labels("mismatch")
    _, parsed = comparison([first, assertion(False)])
    assert parsed.result == "fail"
    assert parsed.values == ["Name (Z to A)", "Name (Z to A)"]
    assert "valores internos não passou" in "".join(parsed.visible_text)


@pytest.mark.parametrize("passed,preview_status", [(True, "mismatch"), (False, "confirmed")])
def test_conflicting_snapshot_does_not_claim_its_label_was_the_checked_result(passed, preview_status):
    first = action()
    first["action_preview"]["selection"] = labels(preview_status, actual="Snapshot label")
    _, parsed = comparison([first, assertion(passed)])
    assert parsed.result == ("pass" if passed else "fail")
    assert "Snapshot label" not in parsed.values
    assert "instantes diferentes" in "".join(parsed.visible_text)


@pytest.mark.parametrize("passed", [True, False, None, "true", 1])
def test_missing_labels_still_explains_result_without_showing_raw_json(passed):
    _, parsed = comparison([action(action_preview=None), assertion(passed)])
    assert parsed.result == ("pass" if passed is True else "fail" if passed is False else "unknown")
    assert "option_matches" not in "".join(parsed.visible_text)
    assert "nomes das opções não foram registrados" in "".join(parsed.visible_text)
    assert parsed.technical_is_closed


def test_unknown_assertion_is_not_promoted_by_confirmed_snapshot():
    _, parsed = comparison([action(), assertion(None)])
    assert parsed.result == "unknown"
    assert "Os nomes exibidos, sozinhos" in "".join(parsed.visible_text)


def test_historical_requested_name_is_shown_twice_when_value_match_confirms_it():
    first = action()
    first["action_preview"]["selection"] = labels("unverified", actual="")
    _, parsed = comparison([first, assertion(True)])
    assert parsed.values == ["Name (Z to A)", "Name (Z to A)"]
    assert parsed.result == "pass"
    assert "Opção solicitada confirmada no navegador" in parsed.visible_text
    assert "nomes das opções não foram registrados" not in "".join(parsed.visible_text)


@pytest.mark.parametrize("key,value", [
    ("actual", {"option_matches": False}), ("actual", {"option_matches": "true"}),
    ("actual", {"option_matches": 1}), ("actual", {}), ("actual", None),
    ("expected", {"option_matches": False}), ("expected", {"option_matches": "true"}),
    ("expected", {"option_matches": 1}), ("expected", {}), ("expected", None),
])
def test_historical_name_fallback_requires_explicit_true_value_match(key, value):
    first = action()
    first["action_preview"]["selection"] = labels("unverified", actual="")
    check = assertion(True)
    check["assertion"][key] = value
    _, parsed = comparison([first, check])
    assert parsed.values == ["Name (Z to A)", "Corresponde à opção esperada"]


@pytest.mark.parametrize("passed", [False, None, "true", 1])
def test_historical_requested_name_is_not_copied_for_failed_or_unknown_checks(passed):
    first = action()
    first["action_preview"]["selection"] = labels("unverified", actual="")
    check = assertion(passed)
    check["assertion"]["actual"] = {"option_matches": True}
    _, parsed = comparison([first, check])
    assert parsed.values[0] == "Name (Z to A)"
    assert parsed.values[1] != "Name (Z to A)"


def test_historical_name_fallback_does_not_borrow_from_another_page():
    first = action()
    first["action_preview"]["selection"] = labels("unverified", actual="")
    _, parsed = comparison([first, assertion(url="https://shop.test/other")])
    assert "Name (Z to A)" not in parsed.values


def test_unknown_capture_with_a_label_is_not_replaced_by_the_requested_name():
    first = action()
    first["action_preview"]["selection"] = labels("unverified", actual="Snapshot label")
    _, parsed = comparison([first, assertion(True)])
    assert parsed.values == ["Name (Z to A)", "Corresponde à opção esperada"]
    assert "instantes diferentes" in "".join(parsed.visible_text)


def test_two_attempts_keep_their_own_choices_not_latest_scenario_preview():
    first = action()
    second = action(2, occurred_at="2026-10-09T12:00:03Z")
    second["action_preview"].update(captured_at="2026-10-09T12:00:04Z", selection=labels(requested="Menor preço", actual="Menor preço"))
    entries = [first, assertion(), second, assertion(occurred_at="2026-10-09T12:00:05Z")]
    assert StandaloneHtmlReport._selection_for_assertion(entries, 1)["requested_label"] == "Name (Z to A)"
    assert StandaloneHtmlReport._selection_for_assertion(entries, 3)["requested_label"] == "Menor preço"


@pytest.mark.parametrize("changes", [
    {"url": "https://shop.test/other"}, {"url": None}, {"target": "another-control"},
    {"occurred_at": "2026-10-09T11:59:59Z"}, {"occurred_at": None},
    {"step_number": 2}, {"step_number": True}, {"step_number": 0},
    {"step_number": "1"}, {"step_number": 1, "action_step": 2},
])
def test_different_page_time_target_or_invalid_step_does_not_borrow_labels(changes):
    assert StandaloneHtmlReport._selection_for_assertion([action(), assertion(**changes)], 1) is None


def test_explicit_unique_step_can_link_only_the_immediately_preceding_action():
    entries = [action(), assertion(step_number=1)]
    assert StandaloneHtmlReport._selection_for_assertion(entries, 1) == labels()
    entries.insert(1, action(2, action_preview=None))
    assert StandaloneHtmlReport._selection_for_assertion(entries, 2) is None


def test_nearest_action_without_dropdown_metadata_blocks_search_for_older_option():
    entries = [action(), action(2, action_preview=None), assertion()]
    assert StandaloneHtmlReport._selection_for_assertion(entries, 2) is None


@pytest.mark.parametrize("same_step", [True, False])
def test_duplicate_actions_are_ambiguous(same_step):
    entries = [action(), action(1 if same_step else 2), assertion(**({"step_number": 1} if same_step else {}))]
    assert StandaloneHtmlReport._selection_for_assertion(entries, 2) is None


@pytest.mark.parametrize("change", ["version", "action", "url", "target", "captured_at", "selection"])
def test_invalid_preview_cannot_supply_display_labels(change):
    first = action()
    first["action_preview"][change] = {
        "version": "another-version", "action": "click", "url": "https://shop.test/other",
        "target": "other", "captured_at": "2026-10-09T12:00:03Z", "selection": {"requested_label": "Untrusted"},
    }[change]
    assert StandaloneHtmlReport._selection_for_assertion([first, assertion()], 1) is None


def test_selection_names_and_technical_data_are_escaped_not_interpreted_as_html():
    malicious = '<img src=x onerror="alert(1)">'
    first = action()
    first["action_preview"]["selection"] = labels(requested=malicious, actual=malicious)
    check = assertion()
    check["assertion"]["actual"] = {"unsafe": "</pre><script>boom</script>"}
    document, parsed = comparison([first, check])
    assert parsed.values == [malicious, malicious]
    assert "<img" not in document and "<script>" not in document
    assert "&lt;img" in document and "&lt;script&gt;" in document
    assert json.loads("".join(parsed.technical).replace("Ver dados técnicos", ""))["actual"] == check["assertion"]["actual"]


def test_non_selection_assertions_keep_existing_presentation():
    document = StandaloneHtmlReport._assertion_detail({"kind": "text_contains", "passed": True, "expected": "Produtos"})
    assert "selection-verification" not in document
    assert "Esperado" in document and "Produtos" in document
