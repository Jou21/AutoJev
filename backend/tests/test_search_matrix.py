"""Period searches are run over many ranges: 12 months, then earlier years."""

from datetime import date

import pytest

from autojev.domain.models import Observation
from autojev.services.coverage import CoveragePlanner
from autojev.services.search_matrix import (
    FieldVariations,
    SearchMatrix,
    detect_date_pairs,
    month_ranges,
    readonly_date_fields,
    variants_for,
)

TODAY = date(2026, 11, 1)
URL = "https://app.test/gcar/conciliacao"


class FakeForm:
    """A date-range search form: typed values in, a table (or none) out."""

    EXTRA = [
        {"ref": "e4", "tag": "select", "label": "UF", "name": "UF", "document_top": 60, "enabled": True,
         "interactable": True, "options": [{"value": "", "label": "Todos"}, {"value": "SP", "label": "SP"},
                                           {"value": "RJ", "label": "RJ"}]},
        {"ref": "e5", "tag": "input", "type": "text", "label": "CNPJ", "name": "CNPJ", "document_top": 80,
         "enabled": True, "interactable": True},
    ]

    def __init__(self, records, *, iso=False, accepts=True, extra=False, latency=0, blur_enables=False,
                 required_gate=False, toast=False, covered=False, specific_option=False):
        self.records, self.iso, self.accepts = records, iso, accepts
        # required_gate: UF is required and the button stays disabled until it is chosen.
        # toast: "no records" is announced in an alert region, like the real screen.
        # covered: a toast sits on top of the extra fields, so they are not clickable right now.
        self.required_gate, self.toast, self.covered = required_gate, toast, covered
        # specific_option: "Todos" is selectable (a non-empty value) but the search
        # only enables once a specific UF is chosen.
        self.specific_option = specific_option
        # Like the real screens: results arrive after `latency` waits (a spinner
        # shows meanwhile) and the button only enables once a field is left.
        self.latency, self.blur_enables = latency, blur_enables
        self.pending, self.blurred = 0, not blur_enables
        self.shown_rows, self.shown_empty = 0, True
        self.extra = {item["ref"]: "" for item in self.EXTRA} if extra else {}
        if extra and specific_option:
            self.extra["e4"] = "ALL"
        self.errors = []
        self.keys = []
        self.values = {"e1": "", "e2": ""}
        self.rows = 0
        self.empty = True
        self.log = []

    def fmt(self, day):
        return day.isoformat() if self.iso else day.strftime("%d/%m/%Y")

    def _extra_element(self, item):
        element = dict(item)
        if self.required_gate and element["ref"] == "e4":
            element["required"] = True
            element["selected_values"] = [self.extra.get("e4", "")]
        if self.covered:
            element["interactable"] = False
        if self.specific_option and element["ref"] == "e4":
            element["options"] = [{"value": "ALL", "label": "Todos"}, {"value": "SP", "label": "SP"},
                                  {"value": "RJ", "label": "RJ"}]
            element["selected_values"] = [self.extra.get("e4", "")]
        return element

    def observation(self):
        def field(ref, label, top):
            return {"ref": ref, "tag": "input", "type": "date" if self.iso else "text",
                    "placeholder": None if self.iso else "01/01/2026", "label": label,
                    "name": label, "document_top": top, "enabled": True, "interactable": True}

        enabled = all(self.values.values()) and self.blurred and (not self.required_gate or bool(self.extra.get("e4")))
        if self.specific_option:
            enabled = enabled and self.extra.get("e4") not in ("", "ALL", None)
        return Observation(
            url=URL, title="Conciliação", text="",
            interactive_elements=[
                field("e1", "Data de início*", 100), field("e2", "Data de término*", 100),
                {"ref": "e3", "tag": "button", "name": "PESQUISAR", "document_top": 200,
                 "enabled": enabled, "interactable": enabled},
                *(self._extra_element(item) for item in self.EXTRA if item["ref"] in self.extra),
            ],
            page_state={
                "fields": [{"ref": ref, "value": value} for ref, value in self.values.items() if value],
                "results": {"rows": self.shown_rows, "empty_state": self.shown_empty},
                "validation_errors": list(self.errors) if not self.pending else [],
                "busy": self.pending > 0,
            },
        )

    def apply(self, decision):
        self.log.append((decision.action, decision.target, decision.value))
        self.keys.append(decision.key)
        if decision.action == "wait":
            if self.pending:
                self.pending -= 1
                if not self.pending:
                    self.shown_rows, self.shown_empty = self.rows, self.empty
            return
        if decision.action == "press":
            self.blurred = True
            return
        if decision.action == "fill" and self.blur_enables:
            self.blurred = decision.key == "Tab"      # leaving the field is what validates it
        if decision.target in self.extra:
            self.extra[decision.target] = decision.value
        elif decision.action == "fill":
            self.values[decision.target] = decision.value if self.accepts else ""
        elif decision.action == "click":
            assert decision.target == "e3"
            start, end = self.values["e1"], self.values["e2"]
            def parse(text):
                if self.iso:
                    return date.fromisoformat(text)
                digits = text.replace("/", "")                  # a mask may echo 01012026
                return date(int(digits[4:]), int(digits[2:4]), int(digits[:2]))

            first, last = parse(start), parse(end)
            assert first <= last, f"inverted range typed: {start} .. {end}"
            self.rows = self.records.get(f"{first.month:02d}/{first.year}", 0)
            cnpj = self.extra.get("e5", "")
            digits = cnpj.replace(".", "").replace("/", "").replace("-", "")
            self.errors = ["CNPJ inválido"] if cnpj and not digits.isdigit() else []
            if self.extra.get("e4") not in ("", "ALL", None):
                self.rows = self.rows // 2          # a UF filter narrows the table
            if self.errors:
                self.rows = 0
            self.empty = self.rows == 0
            if self.empty and self.toast and not self.errors:
                self.errors = ["Não foram encontrados registros de acordo com a pesquisa realizada."]
            if self.latency:
                self.pending = self.latency          # the old table stays up, with a spinner
            else:
                self.shown_rows, self.shown_empty = self.rows, self.empty


def run_variations(form):
    """Date matrix first (as the planner does), then the field variations."""

    planner = CoveragePlanner("https://app.test/gcar/home", None)
    planner.search_today = TODAY
    for _ in range(2000):
        step = planner.next_search_action(form.observation())
        if step is None:
            break
        form.apply(step[0])
    return planner


def run(form, *, matrix=None):
    matrix = matrix or SearchMatrix(key="r#0", today=TODAY)
    for _ in range(400):
        observation = form.observation()
        pairs = detect_date_pairs(observation)
        assert len(pairs) == 1
        step = matrix.step(observation, pairs[0])
        if step is None:
            break
        form.apply(step[0])
    assert matrix.done, "matrix never finished"
    return matrix


def searched(matrix):
    return [r["label"] for r in matrix.records if not r["replay"]]


# --- detection -------------------------------------------------------------------

def test_date_fields_are_paired_with_the_search_button():
    form = FakeForm({})
    [pair] = detect_date_pairs(form.observation())
    assert (pair.start["ref"], pair.end["ref"], pair.button["ref"], pair.iso) == ("e1", "e2", "e3", False)
    [iso_pair] = detect_date_pairs(FakeForm({}, iso=True).observation())
    assert iso_pair.iso is True


def test_a_lone_date_field_or_no_dates_is_not_a_period_search():
    only_one = FakeForm({}).observation()
    only_one.interactive_elements.pop(1)
    assert detect_date_pairs(only_one) == []
    assert detect_date_pairs(Observation(url=URL, title="", text="", interactive_elements=[])) == []


def test_ranges_are_whole_months_this_year_then_earlier_years():
    ranges = month_ranges(TODAY, max_years_back=1)
    assert [r["label"] for r in ranges[:12]] == [f"{m:02d}/2026" for m in range(1, 13)]
    assert ranges[1]["end"] == date(2026, 2, 28) and ranges[11]["end"] == date(2026, 12, 31)
    assert ranges[12]["label"] == "01/2025" and ranges[12]["earlier_year"] and not ranges[11]["earlier_year"]
    assert month_ranges(date(2024, 5, 1), 0)[1]["end"] == date(2024, 2, 29)     # leap year


# --- the machine -------------------------------------------------------------------

def labels_two_years():
    return [f"{m:02d}/2026" for m in range(1, 13)] + [f"{m:02d}/2025" for m in range(1, 13)]


def test_all_24_months_are_searched_even_after_records_appear():
    form = FakeForm({"03/2026": 5, "08/2026": 9})
    matrix = run(form)
    assert searched(matrix) == labels_two_years()
    assert [r["rows"] for r in matrix.records[:12]] == [0, 0, 5, 0, 0, 0, 0, 9, 0, 0, 0, 0]
    assert matrix.stopped_reason == "best_range_shown"
    # The richest month is searched again so its table is on screen afterwards.
    assert matrix.records[-1]["replay"] and matrix.records[-1]["label"] == "08/2026"
    assert form.rows == 9
    assert [e["type"] for e in matrix.events].count("search_matrix_range") == 25


def test_no_replay_when_the_last_search_already_shows_the_records():
    form = FakeForm({"12/2025": 4})                          # the very last month searched
    matrix = run(form)
    assert matrix.stopped_reason == "records_on_screen" and not matrix.records[-1]["replay"]
    assert form.rows == 4


def test_last_year_is_searched_too_and_its_best_month_is_shown():
    form = FakeForm({"09/2025": 3, "06/2025": 8})            # nothing in 2026
    matrix = run(form)
    assert searched(matrix) == labels_two_years()
    assert matrix.records[-1]["replay"] and matrix.records[-1]["label"] == "06/2025"
    assert matrix.stopped_reason == "best_range_shown" and form.rows == 8


def test_no_records_in_24_months_is_a_stated_result():
    form = FakeForm({})
    matrix = run(form)
    assert len(searched(matrix)) == 24
    assert matrix.stopped_reason == "no_records_found"
    done = [e for e in matrix.events if e["type"] == "search_matrix_completed"][0]
    assert done["found_records"] is False and done["best"] is None


def test_the_range_never_inverts_while_dates_are_typed():
    form = FakeForm({"02/2026": 1, "11/2025": 2})   # FakeForm.apply asserts start <= end on every search
    run(form)
    fills = [(target, value) for action, target, value in form.log if action == "fill"]
    assert len(fills) >= 24                                      # it typed both dates for every month
    # Forward month: end is typed before start so start <= end holds at every moment.
    assert [t for t, _ in fills[:2]] == ["e2", "e1"]


def test_iso_date_inputs_are_filled_in_iso():
    form = FakeForm({"01/2026": 2}, iso=True)
    run(form)
    values = [value for action, _, value in form.log if action == "fill"]
    assert values[:2] == ["2026-01-31", "2026-01-01"]


def test_a_form_that_rejects_the_dates_stops_instead_of_walking_every_month():
    form = FakeForm({"03/2026": 5}, accepts=False)
    matrix = run(form)
    assert matrix.stopped_reason == "range_not_accepted"
    assert len(form.log) < 150


def test_a_masked_field_that_rewrites_the_date_is_still_accepted():
    form = FakeForm({"01/2026": 6})
    original = form.apply

    def reformat(decision):
        original(decision)
        if decision.action == "fill":          # the app echoes it as digits only: 01012026
            form.values[decision.target] = decision.value.replace("/", "")

    form.apply = reformat
    matrix = run(form)
    assert searched(matrix)[0] == "01/2026" and matrix.records[0]["rows"] == 6
    assert len(searched(matrix)) == 24                         # nothing was skipped as "not accepted"


# --- planner wiring ----------------------------------------------------------------

def test_planner_runs_the_matrix_only_on_the_requested_screen():
    planner = CoveragePlanner("https://app.test/gcar/home", "Conciliação de Títulos")
    planner.search_today = TODAY
    observation = FakeForm({}).observation()
    assert planner.next_search_action(observation) is None      # focus not reached yet
    assert not planner.has_search_work(observation)
    planner.focus_reached = True
    assert planner.has_search_work(observation)
    decision, reason = planner.next_search_action(observation)
    assert decision.action == "fill" and reason == "search_matrix_fill"
    assert planner.drain_search_events()[0]["type"] == "search_matrix_started"
    assert planner.drain_search_events() == []


# --- other test data (field variations) -----------------------------------------------

def test_every_field_gets_valid_boundary_and_invalid_values():
    values = dict(variants_for(dict(FakeForm.EXTRA[1]))).values()
    assert {"11.222.333/0001-81", "111.111.111-11", "abcdef"} <= set(values)
    assert list(dict(variants_for(FakeForm.EXTRA[0])).values()) == ["SP", "RJ"]    # real options, not the blank one
    money = [v for _, v in variants_for({"tag": "input", "label": "Valor R$"})]
    assert {"100,00", "0,00", "-1,00", "abc"} <= set(money)
    text = [v for _, v in variants_for({"tag": "input", "label": "Transação"})]
    assert any(len(v) >= 100 for v in text) and any("<" in v for v in text)         # long text, special characters
    assert variants_for({"tag": "input", "type": "date", "label": "Vencimento"})[0][0] == "data válida"


def test_after_the_months_each_other_field_is_varied_one_at_a_time_and_cleared():
    form = FakeForm({"03/2026": 8}, extra=True)
    planner = run_variations(form)
    assert planner.search_matrices["/gcar/conciliacao#0"].done
    assert planner.search_variations["/gcar/conciliacao#0"].done

    start = next(i for i, entry in enumerate(form.log) if entry[1] in {"e4", "e5"})
    after_months = form.log[start:]
    sets = [(target, value) for action, target, value in after_months
            if target in {"e4", "e5"} and action in {"fill", "select"}]
    assert [v for t, v in sets if t == "e4"] == ["SP", "RJ", ""]                    # each option, then cleared
    assert [v for t, v in sets if t == "e5"] == [
        "529.982.247-25", "11.222.333/0001-81", "111.111.111-11", "123", "abcdef", "",
    ]
    # One search per variant, then the best range is searched again so its table is on screen.
    assert sum(1 for action, *_ in after_months if action == "click") == 2 + 5 + 1
    assert form.extra == {"e4": "", "e5": ""} and form.rows == 8


def test_variation_results_record_rows_and_validation_messages():
    form = FakeForm({"03/2026": 8}, extra=True)
    events = run_variations(form).drain_search_events()
    by = {(e["field"], e["kind"]): e for e in events if e["type"] == "search_variation_result"}
    assert by[("UF", "opção SP")]["rows"] == 4                                      # narrowed by the filter
    assert by[("CNPJ", "letras")]["validation_errors"] == 1
    assert by[("CNPJ", "letras")]["first_error"] == "CNPJ inválido" and by[("CNPJ", "letras")]["rows"] == 0
    assert by[("CNPJ", "CNPJ válido")]["validation_errors"] == 0
    done = [e for e in events if e["type"] == "search_variation_completed"][0]
    assert done["fields"] == 2 and done["searches"] == 7 and done["with_validation"] == 1


def test_no_records_anywhere_still_varies_the_fields_but_has_nothing_to_restore():
    planner = run_variations(FakeForm({}, extra=True))
    variations = planner.search_variations["/gcar/conciliacao#0"]
    assert variations.done and not variations.restore_best_range and len(variations.results) == 7


def test_a_screen_without_other_fields_has_no_variations():
    variations = run_variations(FakeForm({"03/2026": 1})).search_variations["/gcar/conciliacao#0"]
    assert variations.done and variations.plan == []


def test_search_button_disabled_by_a_variation_is_recorded_not_looped():
    form = FakeForm({"03/2026": 1}, extra=True)
    planner = CoveragePlanner("https://app.test/gcar/home", None)
    planner.search_today = TODAY
    original = form.observation

    def observation():
        current = original()
        if "abc" in form.extra.get("e5", ""):
            next(e for e in current.interactive_elements if e["ref"] == "e3")["enabled"] = False
        return current

    for _ in range(2000):
        step = planner.next_search_action(observation())
        if step is None:
            break
        form.apply(step[0])
    skipped = [e for e in planner.drain_search_events() if e.get("skipped") == "search_disabled"]
    assert skipped and skipped[0]["kind"] == "letras"


# --- dates only a calendar can fill -----------------------------------------------------

def test_readonly_date_fields_are_reported_instead_of_silently_skipped():
    observation = FakeForm({}).observation()
    for element in observation.interactive_elements:
        if element["tag"] == "input":
            element["read_only"] = True
    assert readonly_date_fields(observation) == ["Data de início*", "Data de término*"]
    assert detect_date_pairs(observation) == []

    planner = CoveragePlanner("https://app.test/gcar/home", None)
    assert planner.next_search_action(observation) is None
    events = planner.drain_search_events()
    assert events[0]["type"] == "search_matrix_unsupported" and events[0]["reason"] == "readonly_date_fields"
    planner.next_search_action(observation)
    assert planner.drain_search_events() == []                                      # said once per screen


# --- what the live screens taught us -----------------------------------------------------

def test_slow_results_are_awaited_instead_of_read_as_empty():
    form = FakeForm({"03/2026": 5, "08/2026": 7}, latency=3)
    matrix = run(form)
    by_label = {r["label"]: r["rows"] for r in matrix.records if not r["replay"]}
    assert by_label["03/2026"] == 5 and by_label["08/2026"] == 7                 # not the previous table, not 0
    assert sum(1 for entry in form.log if entry[0] == "wait") >= 3 * 24


def test_no_fixed_pause_when_the_page_is_not_loading():
    form = FakeForm({})
    run(form, matrix=SearchMatrix(key="r#0", today=TODAY, ranges=month_ranges(TODAY, 0)[:2]))
    assert not any(entry[0] == "wait" for entry in form.log)          # an empty answer is taken as is


def test_the_last_date_leaves_the_field_in_the_same_action():
    form = FakeForm({"03/2026": 4}, blur_enables=True)
    matrix = run(form)
    assert not any(r.get("skipped") for r in matrix.records)
    assert [r["rows"] for r in matrix.records if not r["replay"]][2] == 4
    assert [entry[0] for entry in form.log[:3]] == ["fill", "fill", "click"]         # end, start(+Tab), search
    assert form.keys[:3] == [None, "Tab", None]
    assert not any(entry[0] == "press" for entry in form.log)                       # no extra Tab action


def test_a_button_that_enables_late_is_waited_for_not_skipped():
    form = FakeForm({"01/2026": 2})
    original = form.observation
    state = {"polls": 0}

    def observation():
        current = original()
        button = next(e for e in current.interactive_elements if e["ref"] == "e3")
        if state["polls"] < 2:                       # validation lags two reads behind
            button["enabled"] = False
        state["polls"] += 1
        return current

    matrix = SearchMatrix(key="r#0", today=TODAY, ranges=month_ranges(TODAY, 0)[:1])
    for _ in range(100):
        pair = detect_date_pairs(observation())[0]
        step = matrix.step(observation(), pair)
        if step is None:
            break
        form.apply(step[0])
    assert matrix.records[0]["rows"] == 2 and not matrix.records[0].get("skipped")


def test_variations_also_leave_the_field_and_wait_for_the_table():
    form = FakeForm({"03/2026": 8}, extra=True, latency=2, blur_enables=True)
    planner = run_variations(form)
    variations = planner.search_variations["/gcar/conciliacao#0"]
    assert variations.done and len(variations.results) == 7
    by = {(r["field"], r["kind"]): r for r in variations.results}
    assert by[("UF", "opção SP")]["rows"] == 4 and by[("CNPJ", "letras")]["validation_errors"] == 1
    typed = [i for i, entry in enumerate(form.log) if entry[1] == "e5" and entry[0] == "fill" and entry[2]]
    assert typed and all(form.keys[i] == "Tab" for i in typed)                      # Tab with the typing itself


# --- speed: only the search is a full, evidenced action -------------------------------------

def decisions_of(form, *, matrix=None):
    matrix = matrix or SearchMatrix(key="r#0", today=TODAY, ranges=month_ranges(TODAY, 0)[:2])
    seen = []
    for _ in range(100):
        observation = form.observation()
        step = matrix.step(observation, detect_date_pairs(observation)[0])
        if step is None:
            break
        seen.append(step[0])
        form.apply(step[0])
    return seen


def test_typing_and_waiting_are_fast_steps_but_the_search_keeps_its_evidence():
    seen = decisions_of(FakeForm({"01/2026": 3}, latency=2))
    inputs = [d for d in seen if d.action in {"fill", "wait"}]
    searches = [d for d in seen if d.action == "click"]
    assert inputs and all(d.fast for d in inputs)
    assert searches and not any(d.fast for d in searches)            # screenshot, preview, DOM settle stay


def test_variation_inputs_are_fast_too_and_their_search_is_not():
    form = FakeForm({"03/2026": 8}, extra=True)
    planner = run_variations(form)
    assert planner.search_variations["/gcar/conciliacao#0"].done
    # Re-run capturing the decisions of the variation phase.
    form = FakeForm({"03/2026": 8}, extra=True)
    planner = CoveragePlanner("https://app.test/gcar/home", None)
    planner.search_today = TODAY
    taken = []
    for _ in range(2000):
        step = planner.next_search_action(form.observation())
        if step is None:
            break
        taken.append(step)
        form.apply(step[0])
    variation = [(d, reason) for d, reason in taken if reason.startswith("search_variation")]
    assert variation
    for decision, reason in variation:
        assert decision.fast == (decision.action != "click"), (decision.action, reason)


def test_only_the_search_click_of_the_matrix_becomes_a_test_case():
    from autojev.domain.models import ActionResult, AssertionResult
    from autojev.services.exploration import ExplorationManager

    def result(action):
        return ActionResult(
            action=action, success=True, started_at="t", finished_at="t", before_url="u", after_url="u",
            assertions=[AssertionResult("observable_state_change", True, True, True, "ok")],
        )

    policy = ExplorationManager._scenario_capture_policy
    assert policy(decision_source="search_matrix", is_restoration_action=False, result=result("fill")) \
        == (False, "search_matrix_input_step")
    assert policy(decision_source="search_matrix", is_restoration_action=False, result=result("wait")) \
        == (False, "search_matrix_input_step")
    assert policy(decision_source="search_matrix", is_restoration_action=False, result=result("click")) == (True, None)


# --- what the second live run taught us ---------------------------------------------------------

def test_a_required_field_that_keeps_the_search_disabled_is_filled_once():
    form = FakeForm({"03/2026": 4}, extra=True, required_gate=True)
    matrix = run(form)
    assert not any(r.get("skipped") for r in matrix.records)
    assert [r["rows"] for r in matrix.records if not r["replay"]][2] == 2          # the UF filter narrows 4 to 2
    chosen = [entry for entry in form.log if entry[1] == "e4" and entry[2]]
    assert chosen == [("select", "e4", "SP")]                           # chosen once, kept for the other months


def test_when_nothing_enables_the_search_the_report_says_what_was_in_the_way():
    form = FakeForm({}, extra=True, required_gate=True)
    for item in form.EXTRA:
        item["options"] = [{"value": "", "label": "Selecione"}]       # nothing valid to choose
    try:
        matrix = run(form)
    finally:
        form.EXTRA[0]["options"] = [{"value": "", "label": "Todos"}, {"value": "SP", "label": "SP"}, {"value": "RJ", "label": "RJ"}]
    assert matrix.stopped_reason == "range_not_accepted"
    skipped = [e for e in matrix.events if e["type"] == "search_matrix_range" and e.get("skipped") == "search_disabled"]
    assert skipped and skipped[0]["label"] == "01/2026"


def test_the_no_records_toast_is_an_empty_answer_not_a_validation_error():
    form = FakeForm({"03/2026": 8}, extra=True, toast=True)
    events = run_variations(form).drain_search_events()
    month = next(e for e in events if e["type"] == "search_matrix_range" and e["label"] == "01/2026")
    assert month["rows"] == 0 and month["empty_state"] is True
    variation = [e for e in events if e["type"] == "search_variation_result"]
    assert variation and all(not (e["validation_errors"] and "registros" in (e["first_error"] or "")) for e in variation)
    done = [e for e in events if e["type"] == "search_variation_completed"][0]
    assert done["with_validation"] == 1                                   # only the real "CNPJ inválido"


def test_fields_covered_by_a_toast_are_still_planned_and_varied():
    form = FakeForm({"03/2026": 8}, extra=True, covered=True)
    planner = run_variations(form)
    variations = planner.search_variations["/gcar/conciliacao#0"]
    assert [p["title"] for p in variations.plan] == ["UF", "CNPJ"] and len(variations.results) == 7


def test_a_loading_page_is_waited_for_before_the_fields_are_planned():
    form = FakeForm({"03/2026": 8}, extra=True)
    observation = form.observation()
    pair = detect_date_pairs(observation)[0]
    variations = FieldVariations(key="r#0", restore_best_range=True)

    observation.page_state["busy"] = True
    decision, reason = variations.step(observation, pair)
    assert decision.action == "wait" and decision.fast and variations.plan == []       # nothing planned while loading

    observation.page_state["busy"] = False
    decision, reason = variations.step(observation, pair)
    assert reason == "search_variation_set" and [p["title"] for p in variations.plan] == ["UF", "CNPJ"]


# --- a select whose "Todos" does not enable the search ---------------------------------------

def test_other_select_options_are_tried_until_the_search_enables():
    form = FakeForm({"03/2026": 8}, extra=True, specific_option=True)
    matrix = run(form)
    assert not any(r.get("skipped") for r in matrix.records)             # no month given up on
    assert [r["rows"] for r in matrix.records if not r["replay"]][2] == 4  # UF=SP narrows 8 to 4
    unlocked = [e for e in matrix.events if e["type"] == "search_matrix_unlocked"]
    assert len(unlocked) == 1 and unlocked[0]["field"] == "UF" and unlocked[0]["tried"] == 1
    # What the defect report needs: the default the page offered and the choice that worked.
    assert unlocked[0]["default"] == "Todos" and unlocked[0]["option"] == "SP"
    assert unlocked[0]["button"] == "PESQUISAR" and unlocked[0]["messages"] == []
    chosen = [entry for entry in form.log if entry[1] == "e4"]
    assert chosen == [("select", "e4", "SP")]                            # tried once, kept for every month


def test_it_stops_trying_when_no_option_enables_the_search():
    form = FakeForm({}, extra=True, specific_option=True)
    original = form._extra_element

    def only_todos(item):
        element = original(item)
        if element["ref"] == "e4":
            element["options"] = [{"value": "ALL", "label": "Todos"}]        # nothing else to choose
        return element

    form._extra_element = only_todos
    matrix = run(form)
    assert matrix.stopped_reason == "range_not_accepted"
    assert not [e for e in matrix.events if e["type"] == "search_matrix_unlocked"]


def test_a_field_with_no_label_is_named_and_typed_by_its_form_control():
    element = {"tag": "input", "type": "text", "field_id": "valorTransacao"}
    assert [k for k, _ in variants_for(element)][:2] == ["valor comum", "zero"]          # classified as an amount
    from autojev.services.search_matrix import _field_title
    assert _field_title(element) == "valorTransacao"
    assert _field_title({"tag": "input"}) == "campo"


def test_an_alert_already_on_screen_before_the_search_is_flagged_as_carried_over():
    form = FakeForm({"03/2026": 8}, extra=True)
    original_observation, original_apply = form.observation, form.apply
    armed = {"on": False}

    def apply(decision):
        original_apply(decision)
        if decision.action == "click" and form.extra.get("e5"):
            armed["on"] = True                                          # the first search with a CNPJ raises it...
        if decision.target == "e5" and decision.value == "":
            armed["on"] = False                                         # ...and it stays up until the field is cleared

    def observation():
        current = original_observation()
        if armed["on"]:
            current.page_state["validation_errors"] = ["× Infinite or NaN"]
        return current

    form.apply, form.observation = apply, observation
    planner = CoveragePlanner("https://app.test/gcar/home", None)
    planner.search_today = TODAY
    for _ in range(2000):
        step = planner.next_search_action(observation())
        if step is None:
            break
        form.apply(step[0])
    cnpj = [r for r in planner.search_variations["/gcar/conciliacao#0"].results if r["field"] == "CNPJ"]
    assert cnpj[0]["validation_errors"] == 1 and "carried_over" not in cnpj[0]      # new when first shown
    assert all(r.get("carried_over") for r in cnpj[1:5])                             # then it is the same alert
