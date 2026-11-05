"""What the period/data searches covered reaches the report and the API summary."""

from autojev.adapters.report import StandaloneHtmlReport
from autojev.services.coverage_ledger import search_coverage


def ev(event_type, **payload):
    return {"type": event_type, "payload": payload}


RUN = [
    ev("action_completed"),
    ev("search_matrix_started", matrix="/c#0"),
    ev("search_matrix_range", matrix="/c#0", label="01/2026", rows=0, empty_state=True),
    ev("search_matrix_range", matrix="/c#0", label="03/2026", rows=3, empty_state=False),
    ev("search_matrix_range", matrix="/c#0", label="04/2026", rows=0, skipped="date_not_accepted"),
    ev("search_matrix_range", matrix="/c#0", label="03/2026", rows=3, replay=True),      # replay is not a new search
    ev("search_matrix_completed", matrix="/c#0", reason="best_range_shown", found_records=True),
    ev("search_variation_result", matrix="/c#0", field="CNPJ", kind="letras", value="abcdef",
       rows=0, validation_errors=1, first_error="CNPJ <inválido>"),
    ev("search_variation_result", matrix="/c#0", field="UF", kind="opção SP", value="SP",
       rows=2, validation_errors=0, first_error=None),
    ev("search_variation_completed", matrix="/c#0", searches=2),
    ev("search_matrix_unsupported", matrix="/n", reason="readonly_date_fields", fields=["Vencimento"]),
]


def test_summary_lists_each_range_and_variation_once():
    summary = search_coverage(RUN)
    assert [r["label"] for r in summary["ranges"]] == ["01/2026", "03/2026", "04/2026"]
    assert summary["months_with_records"] == ["03/2026"] and summary["found_records"] is True
    assert summary["stopped_reason"] == "best_range_shown"
    assert [(v["field"], v["rows"], v["validation_errors"]) for v in summary["variations"]] == [("CNPJ", 0, 1), ("UF", 2, 0)]
    assert summary["variation_searches"] == 2 and summary["variations_with_validation"] == 1
    assert summary["unsupported"] == [{"fields": ["Vencimento"], "reason": "readonly_date_fields"}]


def test_a_run_that_never_searched_has_no_summary():
    assert search_coverage([ev("action_completed"), {"type": "x", "payload": "garbage"}]) is None
    assert search_coverage(None) is None


def test_no_records_anywhere_is_a_stated_result():
    summary = search_coverage([
        ev("search_matrix_range", matrix="/c#0", label="01/2026", rows=0, empty_state=True),
        ev("search_matrix_completed", matrix="/c#0", reason="no_records_found", found_records=False),
    ])
    assert summary["found_records"] is False and summary["months_with_records"] == []
    html = StandaloneHtmlReport._search_coverage_section(summary)
    assert "Nenhum período trouxe registros" in html and "não uma falha" in html


def test_html_section_shows_ranges_data_and_escapes_application_text():
    html = StandaloneHtmlReport._search_coverage_section(search_coverage(RUN))
    assert "Registros encontrados em: 03/2026" in html
    assert "<td>01/2026</td><td>0</td>" in html and "<td>03/2026</td><td>3</td>" in html
    assert "não pesquisado (date_not_accepted)" in html
    assert "letras" in html and "CNPJ &lt;inválido&gt;" in html and "CNPJ <inválido>" not in html
    assert "Campos de data só aceitam calendário (Vencimento)" in html
