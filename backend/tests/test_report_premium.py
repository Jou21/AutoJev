from copy import deepcopy
from datetime import datetime
import json
import re
import pytest

from autojev.adapters.report import StandaloneHtmlReport
from autojev.services.scenario_timeline import TIMELINE_VERSION


def fixture():
    return {
        "id": "premium", "status": "completed", "app_url": "https://shop.test?token=private-app",
        "timeline_version": TIMELINE_VERSION,
        "started_at": "2026-10-08T12:00:00Z", "finished_at": "2026-10-08T12:02:00Z",
        "actions": [
            {"step_number": 1, "result": {"started_at": "2026-10-08T12:00:01Z", "finished_at": "2026-10-08T12:00:02Z", "before_url": "https://shop.test/", "after_url": "https://shop.test/cart"}},
            {"step_number": 9, "result": {"started_at": "2026-10-08T12:01:00Z", "finished_at": "2026-10-08T12:01:02Z", "before_url": "https://shop.test/cart", "after_url": "https://shop.test/"}},
        ],
        "scenarios": [
            {"id": "CT001", "title": "Adicionar produto", "status": "PASS", "url": "https://shop.test/cart?token=private-page", "screenshots": ["001.png"], "evidence": {
                "action_started_at": "2026-10-08T12:01:00Z", "action_finished_at": "2026-10-08T12:01:02Z",
                "assertions": [{"kind": "cart_count", "passed": True}],
                "timeline": [{"kind": "action", "step_number": 1}, {"kind": "action", "step_number": 9}, {"kind": "observation", "screenshot": "001.png"}],
            }},
            {"id": "CT002", "title": "Conferir carrinho", "status": "FAIL", "screenshots": ["001.png"], "evidence": {
                "assertions": [{"kind": "cart_count", "passed": False}],
                "timeline": [{"kind": "observation", "screenshot": "001.png"}],
            }},
        ],
    }


def render(tmp_path, run):
    path = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(path))
    return path.read_text()


def data(document):
    return json.loads(re.search(r'<script id="report-data" type="application/json">(.*?)</script>', document, re.S)[1])


def test_named_functional_flows_preserve_case_identity_evidence_and_results(tmp_path):
    run = fixture()
    run["scenarios"][0]["title"] = "Criar cliente"
    run["scenarios"][1].update(title="Editar cliente", status="BLOCKED")
    run["scenarios"] += [
        {"id": "CT003", "title": "Consultar cliente", "status": "FAIL", "evidence": {}},
        {"id": "CT004", "title": "Excluir cliente", "status": "SKIPPED", "evidence": {}},
        {"id": "CT005", "title": "Preencher campo nome", "status": "PASS", "evidence": {"assertions": [{"kind": "field_retains_value", "passed": True}]}},
    ]
    before = deepcopy(run)
    document = render(tmp_path, run)
    payload = data(document)
    assert 'data-report-flows="functional-flows-v1"' in document[:8192]
    assert [case["id"] for case in payload["cases"]] == ["CT001", "CT002", "CT003", "CT004", "CT005"]
    assert [case["status"] for case in payload["cases"]] == ["PASS", "BLOCKED", "FAIL", "SKIPPED", "PASS"]
    assert [[item["name"] for item in case["flow_classification"]["memberships"]] for case in payload["cases"]] == [
        ["Cadastrar cliente"], ["Editar cliente"], ["Consultar cliente"], ["Excluir cliente"], []]
    flows = {flow["name"]: flow for flow in payload["functional_flows"]}
    assert flows["Cadastrar cliente"]["case_ids"] == ["CT001"]
    assert flows["Editar cliente"]["counts"]["BLOCKED"] == 1
    assert flows["Consultar cliente"]["approval_percent"] == 0
    assert flows["Excluir cliente"]["approval_percent"] is None
    assert document.count('<details class="scenario"') == 5
    assert 'id="crud-filter"' not in document
    assert 'data-case-focus="primary"' not in document
    assert 'data-select-flow="unclassified"' in document
    for name in flows:
        assert name in document
    assert 'class="functional-flow-card"' in document
    assert "não representam cobertura" in document
    assert run == before


@pytest.mark.parametrize("scope", ["initial", "consolidated", "round"])
def test_flow_classification_is_present_in_all_export_scopes_and_safe_for_empty_reports(tmp_path, scope):
    run = fixture()
    run["suite_context"] = {"scope": scope, "root_id": "root"}
    document = render(tmp_path, run)
    assert all(case["flow_classification"]["version"] == "functional-flows-v1" for case in data(document)["cases"])
    assert data(document)["functional_flows"]
    assert data(document)["suite_context"]["scope"] == scope
    run["scenarios"] = []
    empty = render(tmp_path, run)
    assert data(empty)["cases"] == []
    assert data(empty)["functional_flows"] == []
    assert "Nenhum fluxo identificado" in empty
    assert 'data-flow-unclassified-count>0</span>' in empty


@pytest.mark.parametrize("scope", ["initial", "consolidated", "round"])
def test_export_context_is_metadata_not_a_banner_above_the_report(tmp_path, scope):
    run = fixture()
    run["suite_context"] = {"root_id": "parent", "scope": scope, "label": "Execução inicial · resultado histórico preservado",
                            "round_name": "Rodada", "generated_at": "2026-10-20T00:01:39Z", "note": "Contexto preservado nos dados."}
    before = deepcopy(run)
    document = render(tmp_path, run)
    assert "suite-report-context" not in document
    assert 'aria-label="Contexto da exportação"' not in document
    assert data(document)["suite_context"] == run["suite_context"]
    assert [(case["id"], case["status"]) for case in data(document)["cases"]] == [("CT001", "PASS"), ("CT002", "FAIL")]
    assert run == before


def test_premium_has_eight_views_offline_assets_and_one_authoritative_card_per_ct(tmp_path):
    run = fixture()
    before = deepcopy(run)
    document = render(tmp_path, run)
    assert 'data-report-layout="premium-v1"' in document[:8192]
    for view in ("overview", "tests", "results", "journey", "evidence", "timeline", "coverage", "settings"):
        assert f'data-report-nav="{view}"' in document
        assert f'class="report-view" data-view="{view}"' in document
    assert document.count('<details class="scenario"') == 2
    assert document.count('data-status="PASS"') == 1
    assert document.count('data-status="FAIL"') == 1
    assert 'id="download-csv"' in document and 'id="download-report"' in document
    assert '<script src=' not in document and 'fonts.googleapis' not in document
    assert "Não há uma lista completa de requisitos" in document
    assert "private-page" not in document and "private-app" not in document
    assert run == before


def test_results_have_a_compact_accessible_header_without_duplicate_report_actions(tmp_path):
    document = render(tmp_path, fixture())
    result_section = document.split('data-view="results" aria-label=', 1)[1].split('data-view="journey"', 1)[0]
    assert 'class="result-navigation"' in result_section
    assert 'class="result-location"' in result_section
    assert re.search(r'<h1\b[^>]*id="result-view-title"[^>]*>Resultado do teste</h1>', result_section)
    assert 'id="case-back"' in result_section
    assert 'class="result-actions"' in result_section
    assert re.search(r'<button\b[^>]*id="case-rerun"[^>]*hidden', result_section)
    assert 'Reexecutar teste</button>' in result_section
    assert re.search(r'<p\b[^>]*id="case-rerun-feedback"[^>]*role="status"[^>]*aria-live="polite"[^>]*hidden', result_section)
    for identifier in ("case-history", "case-history-count", "case-edit", "result-more", "case-prev", "case-next", "case-execution-note", "case-execution-summary", "case-execution-metadata", "case-compare"):
        assert f'id="{identifier}"' not in result_section
    assert 'id="result-report-actions"' in result_section
    # The progressive enhancement moves this one existing toolbar into the
    # result header; it must not clone controls or canonical result/evidence.
    assert document.count('id="print-report"') == 1
    assert document.count('id="download-report"') == 1
    assert document.count('id="case-rerun"') == 1
    assert document.count('<details class="scenario"') == 2
    assert document.count('data-status="PASS"') == 1
    assert document.count('data-status="FAIL"') == 1


def test_case_time_sums_distinct_actions_and_starts_at_first_attempt_not_documentation(tmp_path):
    projected = data(render(tmp_path, fixture()))["cases"][0]
    assert projected["duration_seconds"] == 3
    assert projected["offset"] == 1
    assert projected["category"] == "business"
    assert projected["status"] == "PASS"


def test_step_picker_offers_three_named_choices_without_executable_configuration(tmp_path):
    run = fixture()
    before = deepcopy(run)
    document = render(tmp_path, run)
    picker = document.split('<dialog id="step-picker"', 1)[1].split('</dialog>', 1)[0]
    assert document.count('id="step-picker"') == 1
    assert 'aria-labelledby="step-picker-title"' in picker
    assert 'aria-describedby="step-picker-note"' in picker
    assert 'id="step-picker-close"' in picker
    assert re.findall(r'data-step-choice="([^"]+)"', picker) == ["sws", "sql", "wait_print"]
    for title in ("Executar SWS", "Executar SQL", "Aguardar print"):
        assert f'<strong>{title}</strong>' in picker
    assert 'Escolher SWS · em breve' in picker
    assert 'Executar SQL · em breve' in picker
    assert 'Anexar print e continuar' in picker
    assert 'Ctrl+V' in picker and 'qualquer conteúdo' in picker
    assert 'As pausas para print serão confirmadas na aplicação' in picker
    assert 'SWS e SQL permanecem indisponíveis' in picker
    assert '<input' not in picker and '<form' not in picker
    assert run == before


def test_manual_print_is_presented_as_evidence_not_a_passed_check():
    entry = {"kind": "observation", "capture_phase": "after_action", "checkpoint_id": "db-1"}
    state, outcome, description = StandaloneHtmlReport._step_presentation(entry, "observation", capture_exists=True)
    assert state == "recorded"
    assert outcome == "Print manual registrado"
    assert "não aprova nem reprova" in description
    assert StandaloneHtmlReport._step_presentation(entry, "observation", capture_exists=False)[0] == "info"


def test_uploaded_image_is_not_presented_as_browser_capture_or_pass():
    entry = {"kind": "observation", "image_source": "user_upload", "capture_phase": "after_action", "checkpoint_id": "db-1"}
    state, outcome, description = StandaloneHtmlReport._step_presentation(entry, "observation", capture_exists=True)
    assert state == "recorded"
    assert outcome == "Imagem anexada"
    assert "não foi capturada pelo teste" in description
    assert "não aprova nem reprova" in description


def test_uploaded_external_image_is_embedded_and_identified_without_claiming_a_browser_page(tmp_path):
    from io import BytesIO
    from PIL import Image

    run = fixture()
    filename = "uploaded-external-evidence.webp"
    directory = tmp_path / run["id"] / "screenshots"
    directory.mkdir(parents=True)
    image = BytesIO()
    Image.new("RGB", (24, 18), "#9747ff").save(image, format="WEBP")
    (directory / filename).write_bytes(image.getvalue())
    entry = {"kind": "observation", "title": "Imagem anexada pelo usuário", "image_source": "user_upload",
             "screenshot": filename, "attached_at": "2026-10-16T12:00:00Z", "occurred_at": "2026-10-16T12:00:00Z",
             "checkpoint_id": "db-1", "capture_phase": "after_action"}
    run["scenarios"][0]["evidence"]["timeline"] = [entry]
    run["scenarios"][0]["screenshots"] = [filename]
    before = deepcopy(run)
    document = render(tmp_path, run)
    assert 'data:image/webp;base64,' in document
    assert 'alt="Imagem anexada pelo usuário"' in document
    assert 'data-step-outcome="Imagem anexada"' in document
    gallery = re.search(r'<article class="evidence-card"[^>]*data-filename="uploaded-external-evidence.webp".*?</article>', document).group()
    assert 'Imagem anexada pelo usuário' in gallery
    assert '/cart' not in gallery and 'shop.test' not in gallery
    assert run == before
    assert data(document)["cases"][0]["status"] == "PASS"


def test_unknown_timing_is_not_invented_as_zero(tmp_path):
    run = fixture()
    run["actions"] = []
    projected = data(render(tmp_path, run))["cases"]
    assert projected[0]["duration_seconds"] is None
    assert projected[1]["duration_seconds"] is None


def test_gallery_has_one_card_per_file_and_links_all_associated_cases_without_more_base64(tmp_path):
    shots = tmp_path / "premium" / "screenshots"
    shots.mkdir(parents=True)
    (shots / "001.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    document = render(tmp_path, fixture())
    gallery = document.split('<div class="evidence-grid" id="evidence-grid">', 1)[1].split('</section>', 1)[0]
    assert gallery.count('data-evidence-kind="screenshot"') == 1
    assert 'data-source-shot="001.png"' in gallery
    assert 'data-open-case="0"' in gallery and 'data-open-case="1"' in gallery
    assert 'data:image/png;base64,' not in gallery
    assert 'data:image/png;base64,' in document


def test_json_and_html_escape_application_text_and_script_terminators(tmp_path):
    run = fixture()
    run["scenarios"][0]["title"] = '</script><script>alert("x")</script>'
    document = render(tmp_path, run)
    assert '<script>alert("x")</script>' not in document
    assert '\\u003c/script\\u003e' in document
    assert data(document)["cases"][0]["title"] == run["scenarios"][0]["title"]


def test_observed_journey_preserves_returns_without_claiming_complete_e2e(tmp_path):
    document = render(tmp_path, fixture())
    journey = document.split('data-view="journey" aria-label=', 1)[1].split('data-view="evidence"', 1)[0]
    assert '3 posições de navegação' in journey
    assert 'Não representa um teste end-to-end único' in journey
    assert '100% da aplicação' not in document


def test_canonical_cards_are_rendered_once_and_url_variants_are_distinguishable(tmp_path, monkeypatch):
    run = fixture()
    run["scenarios"][0]["url"] = "https://shop.test/product?id=1&token=private"
    run["scenarios"][1]["url"] = "https://shop.test/product?id=2"
    original = StandaloneHtmlReport._scenario_card
    rendered = []

    def spy(self, exploration_id, scenario):
        rendered.append(scenario["id"])
        return original(self, exploration_id, scenario)

    monkeypatch.setattr(StandaloneHtmlReport, "_scenario_card", spy)
    document = render(tmp_path, run)
    assert rendered == ["CT001", "CT002"]
    assert '<small class="page-variant">?id=1' in document
    assert '<small class="page-variant">?id=2</small>' in document
    assert "token=private" not in document


def test_timeline_end_includes_report_postprocessing_and_rule_counts_explain_their_scope(tmp_path):
    run = fixture()
    run["events"] = [{"type": "report_ready", "created_at": "2026-10-08T12:03:00Z"}]
    document = render(tmp_path, run)
    markers = re.findall(r'data-event-kind="system"><span class="event-time">(.*?)</span>.*?<strong>(.*?)</strong>', document)
    end = datetime.fromisoformat("2026-10-08T12:03:00+00:00").astimezone().strftime("%H:%M:%S")
    assert (end, "Encerramento da execução") in markers
    assert data(document)["duration_seconds"] == 180
    assert "Contagens do resultado atual de cada CT, não de todas as tentativas" in document
    assert "tentativas anteriores permanecem na linha do tempo" in document


def test_rerun_report_explains_provenance_and_non_executable_scope(tmp_path):
    run = fixture()
    run["events"] = [{"type": "scenario_rerun_source", "payload": {
        "source_exploration_id": 'original<script>bad</script>',
        "source_scenario_id": "CT008",
    }}]
    run["scenarios"][0]["evidence"]["rerun_scope"] = {"local_checks": True}
    before = deepcopy(run)
    document = render(tmp_path, run)
    assert 'class="reading-guide rerun-provenance"' in document
    assert "Reexecução individual de CT008" in document
    assert "histórico" in document
    assert "resultado mais recente" in document
    assert "cartões de consulta ao banco não são executados" in document
    assert "cartões de consulta ao banco não foram executados" in document
    assert "original<script>bad</script>" not in document
    assert run == before
