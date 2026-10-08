from copy import deepcopy
from datetime import datetime
import json
import re

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
    assert 'id="case-prev"' in result_section and 'id="case-next"' in result_section
    assert 'id="result-report-actions"' in result_section
    # The progressive enhancement moves this one existing toolbar into the
    # result header; it must not clone controls or canonical result/evidence.
    assert document.count('id="print-report"') == 1
    assert document.count('id="download-report"') == 1
    assert document.count('<details class="scenario"') == 2
    assert document.count('data-status="PASS"') == 1
    assert document.count('data-status="FAIL"') == 1


def test_case_time_sums_distinct_actions_and_starts_at_first_attempt_not_documentation(tmp_path):
    projected = data(render(tmp_path, fixture()))["cases"][0]
    assert projected["duration_seconds"] == 3
    assert projected["offset"] == 1
    assert projected["category"] == "business"
    assert projected["status"] == "PASS"


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
