"""The offline report shows one latest CT and preserves earlier owned evidence."""
import base64
import json
import re
from copy import deepcopy
from html.parser import HTMLParser

from autojev.adapters.report import StandaloneHtmlReport
from autojev.adapters.report_premium import _history_card
from autojev.services.scenario_timeline import TIMELINE_VERSION


class Markup(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.tags = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def scenario(owner, title, status, elapsed=7):
    return {
        "id": "CT147", "title": title, "status": status,
        "url": "https://shop.test/inventory.html",
        "evidence_exploration_id": owner,
        "screenshots": ["same.png"],
        "evidence": {
            "action_started_at": "2026-10-18T12:00:00Z",
            "action_finished_at": f"2026-10-18T12:00:{elapsed:02d}Z",
            "assertions": [{"kind": "selection_matches", "passed": status == "PASS"}],
            "timeline": [
                {"kind": "observation", "title": "Antes da ação", "screenshot": "same.png"},
                {"kind": "action", "title": "Selecionar opção", "step_number": 1},
            ],
            "rerun_recipe": {"selector": "private-selector-not-in-report"},
        },
    }


def revision(version, owner, snapshot, *, current=False, run_status="completed"):
    return {
        "version": version, "exploration_id": owner,
        "scenario_id": "CT147" if version == 0 else "CT001",
        "root_exploration_id": "root", "root_scenario_id": "CT147",
        "recorded_at": "2026-10-18T12:00:07Z", "run_status": run_status,
        "current": current, "status": snapshot.get("status") if snapshot else None,
        "has_result": snapshot is not None, "scenario": snapshot,
        "actions": [{"step_number": 1, "result": {
            "started_at": "2026-10-18T12:00:00Z", "finished_at": "2026-10-18T12:00:07Z",
        }}],
    }


def fixture():
    original = scenario("root", "Primeira execução", "PASS")
    older = scenario("old-rerun", "Tentativa anterior", "FAIL")
    current = scenario("latest-rerun", "Resultado mais recente", "PASS")
    revisions = [
        revision(0, "root", original), revision(1, "old-rerun", older),
        revision(2, "latest-rerun", deepcopy(current), current=True),
        revision(3, "canceled-rerun", None, run_status="canceled"),
    ]
    current["execution_revision"] = {
        key: value for key, value in revisions[2].items()
        if key not in {"current", "status", "has_result", "scenario", "actions"}
    }
    current["execution_actions"] = revisions[2]["actions"]
    current["execution_history"] = revisions
    untouched = scenario("root", "Outro CT não reexecutado", "FAIL", elapsed=1)
    untouched["id"] = "CT148"
    return {
        "id": "root", "status": "completed", "app_url": "https://shop.test",
        "timeline_version": TIMELINE_VERSION, "scenario_revisions_key": "revisions-v2",
        "started_at": "2026-10-14T12:00:00Z", "finished_at": "2026-10-14T12:01:00Z",
        "summary": {"total_scenarios": 2, "pass": 1, "fail": 1},
        "scenarios": [current, untouched],
        "actions": [{"step_number": 1, "result": {
            "started_at": "2026-10-14T12:00:00Z", "finished_at": "2026-10-14T12:00:01Z",
        }}],
    }


def render(tmp_path, run):
    for owner in ("root", "old-rerun", "latest-rerun"):
        directory = tmp_path / owner / "screenshots"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "same.png").write_bytes((owner + "-image").encode())
    output = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(output))
    document = output.read_text()
    payload = json.loads(re.search(r'<script id="report-data" type="application/json">(.*?)</script>', document, re.DOTALL)[1])
    return document, payload, Markup(document)


def test_current_ct_uses_latest_owner_duration_and_actual_rerun_source(tmp_path):
    run = fixture()
    before = deepcopy(run)
    document, payload, markup = render(tmp_path, run)
    assert run == before
    assert len(payload["cases"]) == 2
    case = payload["cases"][0]
    assert case["id"] == "CT147" and case["title"] == "Resultado mais recente"
    assert case["duration_seconds"] == 7
    assert case["offset"] is None
    assert case["rerun_source_exploration_id"] == "latest-rerun"
    assert case["rerun_source_scenario_id"] == "CT001"
    assert case["execution_revision"]["root_scenario_id"] == "CT147"
    assert payload["scenario_revisions_key"] == "revisions-v2"
    assert "Reexecução 2" in document
    assert len([attrs for tag, attrs in markup.tags if tag == "tr" and "data-open-case-row" in attrs]) == 2


def test_histories_embed_all_offline_evidence_without_extra_current_cts(tmp_path):
    document, payload, markup = render(tmp_path, fixture())
    primary = [attrs for tag, attrs in markup.tags if tag == "details" and "data-case-index" in attrs]
    archived = [attrs for tag, attrs in markup.tags if tag == "details" and "data-history-case-index" in attrs]
    assert len(primary) == 2
    assert [(item["data-history-case-index"], item["data-history-version"]) for item in archived] == [("0", "0"), ("0", "1")]
    assert '<div id="case-history-archive" hidden aria-hidden="true">' in document
    assert '</section><div id="case-history-archive"' in document
    history = payload["cases"][0]["execution_history"]
    assert [item["version"] for item in history] == [0, 1, 2, 3]
    assert history[2]["current"] is True
    assert history[2]["started_at"] == "2026-10-18T12:00:00+00:00"
    assert history[2]["steps_count"] == len(fixture()["scenarios"][0]["evidence"]["timeline"])
    assert history[3]["has_result"] is False and history[3]["run_status"] == "canceled"
    assert all("scenario" not in item and "actions" not in item for item in history)
    assert "private-selector-not-in-report" not in document
    for owner in ("root", "old-rerun", "latest-rerun"):
        encoded = base64.b64encode((owner + "-image").encode()).decode()
        assert f"data:image/png;base64,{encoded}" in document
    ids = [attrs["id"] for _, attrs in markup.tags if "id" in attrs]
    assert len(ids) == len(set(ids))
    assert any(item.endswith("--history-0-v0") for item in ids)
    assert any(item.endswith("--history-0-v1") for item in ids)


def test_gallery_identifies_capture_by_owner_and_filename_not_filename_alone(tmp_path):
    _, _, markup = render(tmp_path, fixture())
    images = [attrs for tag, attrs in markup.tags if tag == "img" and "data-source-case" in attrs]
    # The filename is identical, but these are distinct actual recordings.
    assert [(image["data-source-case"], image["data-source-shot"]) for image in images] == [("0", "same.png"), ("1", "same.png")]


def test_revision_without_owner_actions_does_not_borrow_original_step_duration(tmp_path):
    run = fixture()
    del run["scenarios"][0]["execution_actions"]
    _, payload, _ = render(tmp_path, run)
    assert payload["cases"][0]["duration_seconds"] == 7


def test_execution_date_uses_its_first_recorded_evidence_and_keeps_action_duration(tmp_path):
    run = fixture()
    run["scenarios"][0]["execution_history"][1]["scenario"]["evidence"]["timeline"][0]["occurred_at"] = "2026-10-18T11:59:20Z"
    _, payload, _ = render(tmp_path, run)
    older = payload["cases"][0]["execution_history"][1]
    assert older["started_at"] == "2026-10-18T11:59:20+00:00"
    assert older["duration_seconds"] == 7


def test_history_dom_ids_and_internal_accessibility_references_are_namespaced():
    original = ('<details class="scenario" data-id="untouched"><label for="input">Nome</label>'
                '<input id="input" aria-labelledby="title other" aria-describedby="help">'
                '<h3 id="title">Título</h3><p id="help">Ajuda</p>'
                '<a href="#input" aria-controls="input help">Abrir</a></details>')
    card = _history_card(original, 3, 4)
    assert 'data-id="untouched"' in card
    assert 'id="input--history-3-v4"' in card
    assert 'for="input--history-3-v4"' in card
    assert 'aria-labelledby="title--history-3-v4 other"' in card
    assert 'aria-describedby="help--history-3-v4"' in card
    assert 'href="#input--history-3-v4"' in card
    assert 'aria-controls="input--history-3-v4 help--history-3-v4"' in card
    assert 'data-history-case-index="3" data-history-version="4"' in card


def test_history_modal_controls_exist_exactly_once(tmp_path):
    _, _, markup = render(tmp_path, fixture())
    ids = [attrs["id"] for _, attrs in markup.tags if "id" in attrs]
    for identifier in ("case-history-dialog", "case-history-title", "case-history-list", "case-history-detail", "case-history-note", "case-history-close"):
        assert ids.count(identifier) == 1
    assert not {"case-history", "case-history-count", "case-execution-summary", "case-execution-metadata", "case-compare"}.intersection(ids)


def test_original_coverage_never_links_to_foreign_action_with_same_step_number(tmp_path):
    report = StandaloneHtmlReport(tmp_path)
    foreign = scenario("latest-rerun", "Resultado mais recente", "PASS")
    assert report._action_anchor(1, [foreign], exploration_id="root") is None
    original = scenario("root", "Ação da execução original", "FAIL")
    original["id"] = "CT148"
    assert report._action_anchor(1, [foreign, original], exploration_id="root") == report._timeline_entry_id(original, 1)
