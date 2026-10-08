from copy import deepcopy

import pytest

from autojev.adapters.report import StandaloneHtmlReport
from autojev.services.report_localization import (
    REPORT_LOCALE_VERSION, localize_report_presentation, report_text, report_title,
)
from autojev.services.scenario_timeline import TIMELINE_VERSION


RATIONALE = (
    "TypeSafe JEV selected select for Sort products — option Name (Z to A) "
    "to maximize safe untested coverage (QA focus: resilience observability; "
    "move probability: 0.43; choice confidence: 0.40; observed-issue probability: 0.96)."
)


@pytest.mark.parametrize(("source", "translated"), [
    ("Resilience Observability: Sort products — option Name (Z to A)",
     "Resiliência e observabilidade: Sort products — option Name (Z to A)"),
    ("Click: Sauce Labs Backpack", "Clicar: Sauce Labs Backpack"),
    ("Select: Name (A to Z)", "Selecionar opção: Name (A to Z)"),
    ("Explore fill on Swag Labs", "Explorar: preencher em «Swag Labs»"),
    ("Executar select", "Selecionar opção"),
    ("Ação: go_back", "Voltar"),
    ("product_sort_order", "Ordenação dos produtos"),
    ("Exploratory scenario 12", "Cenário exploratório 12"),
])
def test_owned_titles_are_localized_but_original_control_names_are_retained(source, translated):
    assert report_title(source) == translated


def test_jev_rationale_translates_metrics_without_changing_their_values():
    text = report_text(RATIONALE)
    assert "ação selecionar opção" in text
    assert "«Sort products — option Name (Z to A)»" in text
    assert "foco de qualidade: Resiliência e observabilidade" in text
    assert "probabilidade da ação: 0.43" in text
    assert "confiança na escolha: 0.40" in text
    assert "probabilidade de problema observado: 0.96" in text
    assert "cobertura insegura" in report_text(RATIONALE.replace("safe untested", "unsafe and untested"))


def test_documentation_wrappers_and_execution_messages_are_localized():
    assert report_text("Execute select on e2") == "Selecionar opção em e2"
    assert report_text("Execute click") == "Clicar"
    assert report_text("Confirmada: The selected option must match the requested value.") == (
        "Confirmada: A opção selecionada deve corresponder ao valor solicitado."
    )
    assert report_text("Playwright executed click successfully; no new route or interactive-state change was observed") == (
        "O Playwright executou a ação clicar com sucesso; não foi observada uma nova rota nem mudança no estado dos controles"
    )
    assert report_text("The browser is on https://shop.test/select?Name=Remove") == (
        "O navegador está em https://shop.test/select?Name=Remove"
    )
    assert "formulário convencional" in report_text(
        "AutoJev's deterministic invocation policy selected the next safe, untried fill without a JEV call (conventional_form)."
    )


def saved_run():
    assertion = {"kind": "selection_matches", "passed": True,
                 "message": "The selected option must match the requested value.",
                 "expected": {"selected": "Name (Z to A)"}, "actual": {"selected": "Name (Z to A)"}}
    signal = {"type": "http", "message": "POST returned HTTP 401", "status_code": 401,
              "url": "https://telemetry.test/select", "evidence_channels": ["network"]}
    return {
        "id": "portuguese", "timeline_version": TIMELINE_VERSION, "status": "completed",
        "app_url": "https://shop.test/select", "usage": {"cost": 0.01, "cost_known": True},
        "summary": {"total_scenarios": 1, "pass": 1},
        "events": [{"type": "coverage_complete_with_restrictions",
                    "message": "Reachable coverage exhausted; blocked controls were documented as restrictions"}],
        "scenarios": [{"id": "CT001", "status": "PASS", "url": "https://shop.test/select",
            "title": "Resilience Observability: Sort products — option Name (Z to A)",
            "description": RATIONALE, "steps": ["Execute select on e2"],
            "preconditions": ["The browser is on https://shop.test/select"],
            "expected_result": assertion["message"], "actual_result": "Confirmada: " + assertion["message"],
            "screenshots": ["002-after-user-input.png"],
            "evidence": {"action": "select", "assertions": [deepcopy(assertion)], "technical_signals": [signal],
                "timeline": [
                    {"id": "a1", "kind": "action", "title": "Executar select", "message": RATIONALE, "target": "e2", "step_number": 1},
                    {"id": "a2", "kind": "observation", "title": "Estado após a ação", "message": "Playwright executed select successfully", "screenshot": "002-after-user-input.png"},
                    {"id": "a3", "kind": "assertion", "title": "selection_matches", "message": assertion["message"], "assertion": deepcopy(assertion)},
                    {"id": "a4", "kind": "signal", "message": signal["message"], "signal": deepcopy(signal)},
                ]},
        }],
    }


def test_projection_handles_versioned_timelines_is_idempotent_and_never_changes_raw_evidence():
    run = saved_run()
    before = deepcopy(run)
    projected = localize_report_presentation(run)
    assert run == before
    assert projected["report_locale"] == REPORT_LOCALE_VERSION
    assert localize_report_presentation(projected) == projected
    scenario = projected["scenarios"][0]
    assert scenario["status"] == "PASS"
    assert scenario["screenshots"] == before["scenarios"][0]["screenshots"]
    evidence = scenario["evidence"]
    assert evidence["technical_signals"] == before["scenarios"][0]["evidence"]["technical_signals"]
    assert evidence["assertions"][0]["kind"] == "selection_matches"
    assert evidence["assertions"][0]["actual"] == {"selected": "Name (Z to A)"}
    assert evidence["timeline"][1]["message"].startswith("O Playwright executou")
    assert [entry["id"] for entry in evidence["timeline"]] == ["a1", "a2", "a3", "a4"]
    for original in ("TimeoutError: select: Target closed", "POST returned HTTP 401", "<script>select</script>", "Remove"):
        assert report_text(original) == original


def test_existing_english_report_renders_in_portuguese_with_internal_filters_and_raw_diagnostics(tmp_path):
    run = saved_run()
    before = deepcopy(run)
    path = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(path))
    html = path.read_text()
    assert run == before
    assert 'data-report-locale="pt-BR-v1"' in html[:8192]
    assert "Relatório de testes exploratórios" in html
    assert "Resiliência e observabilidade: Sort products — option Name (Z to A)" in html
    assert "O TypeSafe JEV escolheu" in html
    assert "O Playwright executou a ação selecionar opção com sucesso" in html
    assert "A opção selecionada deve corresponder ao valor solicitado." in html
    assert "A cobertura alcançável foi esgotada" in html
    assert 'data-status="PASS"' in html and '>APROVADO</span>' in html
    assert 'PASS: "Aprovados", FAIL: "Reprovados"' in html
    assert 'BLOCKED: "Bloqueados", SKIPPED: "Sem validação"' in html
    assert "POST returned HTTP 401" in html
    assert "Exploratory Test Report" not in html and "Playwright executed" not in html
    assert "TypeSafe JEV selected" not in html


def test_owned_expected_prose_has_a_localized_display_without_changing_evidence(tmp_path):
    run = saved_run()
    assertion = run["scenarios"][0]["evidence"]["timeline"][2]["assertion"]
    assertion["expected"] = "0 attributable application runtime, HTTP or network failures"
    before = deepcopy(run)
    projected = localize_report_presentation(run)
    translated = projected["scenarios"][0]["evidence"]["timeline"][2]["assertion"]
    assert translated["expected"] == assertion["expected"]
    assert translated["expected_display"] == "Nenhuma falha de execução, HTTP ou rede atribuível à aplicação"
    assert "expected_display" not in run["scenarios"][0]["evidence"]["assertions"][0]
    assert run == before
    path = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(path))
    assert translated["expected_display"] in path.read_text()
