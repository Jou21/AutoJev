"""The CT roteiro follows saved slots without falsifying attachment chronology."""

import base64
from copy import deepcopy
import re

import pytest

from autojev.adapters.report import StandaloneHtmlReport
from autojev.services.scenario_timeline import TIMELINE_VERSION


def snapshot(gap=2):
    originals = [
        {"id": "before", "kind": "observation", "title": "Antes da ação"},
        {"id": "action", "kind": "action", "title": "Selecionar opção", "step_number": 1, "success": True},
        {"id": "after", "kind": "observation", "title": "Depois da ação"},
        {"id": "option", "kind": "assertion", "title": "Opção selecionada", "assertion": {"kind": "selection_matches", "passed": True}},
        {"id": "sort", "kind": "assertion", "title": "Ordenação dos produtos", "assertion": {"kind": "product_sort_order", "passed": True}},
        {"id": "errors", "kind": "assertion", "title": "Erros durante a ação", "assertion": {"kind": "no_new_browser_errors", "passed": True}},
    ]
    for index, entry in enumerate(originals):
        entry["occurred_at"] = f"2026-10-16T10:00:0{index}Z"
    uploaded = {
        "id": "capture-db-1", "kind": "observation", "title": "Imagem anexada pelo usuário",
        "image_source": "user_upload", "source": "user_upload", "screenshot": "user-print.png",
        "occurred_at": "2026-10-16T10:05:00Z", "attached_at": "2026-10-16T10:05:00Z",
        "planned_gap": gap, "planned_order": 0, "checkpoint_id": "db-1",
    }
    return {
        "id": "position", "status": "completed", "app_url": "https://shop.test",
        "timeline_version": TIMELINE_VERSION,
        "scenarios": [{"id": "CT001", "title": "Selecionar opção", "status": "PASS",
                       "screenshots": ["user-print.png"], "evidence": {"timeline": [*originals, uploaded]}}],
    }


def render(tmp_path, run):
    directory = tmp_path / run["id"] / "screenshots"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "user-print.png").write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
    report = StandaloneHtmlReport(tmp_path)
    path = tmp_path / "report.html"
    report.generate(run, str(path))
    return report, path.read_text()


def native_steps(document):
    return re.findall(r'<li id="(timeline-[^"]+)" class="timeline-entry ([^"]+)"[^>]*>.*?<h3>(.*?)</h3>', document, re.S)


@pytest.mark.parametrize("gap", range(7))
def test_report_embeds_image_at_each_selected_slot_with_real_timestamp_and_no_mutation(tmp_path, gap):
    run = snapshot(gap)
    unchanged = deepcopy(run)
    _report, document = render(tmp_path, run)
    steps = native_steps(document)
    assert len(steps) == 7
    assert steps[gap][2] == "Imagem anexada pelo usuário"
    assert [title for _id, _kind, title in steps if title != "Imagem anexada pelo usuário"] == [
        "Antes de selecionar opção", "Selecionar opção", "Depois de selecionar opção", "Opção selecionada", "Ordenação dos produtos", "Erros durante a ação",
    ]
    assert 'data-step-order="planned"' in document
    assert 'data-image-source="user_upload"' in document
    assert '<time datetime="2026-10-16T10:05:00Z">Anexado em 16/10/2026 10:05:00' in document
    assert document.count('alt="Imagem anexada pelo usuário"') == 1
    assert document.count('data:image/png;base64,') == 1
    assert document.count('data-status="PASS"') == 1
    assert run == unchanged
    assert run["scenarios"][0]["evidence"]["timeline"][-1]["id"] == "capture-db-1"


def test_action_deep_link_matches_displayed_action_after_a_pre_action_attachment(tmp_path):
    run = snapshot(1)
    report, document = render(tmp_path, run)
    anchor = report._action_anchor(1, run["scenarios"])
    action_id, kind, title = native_steps(document)[2]
    assert (kind, title) == ("action", "Selecionar opção")
    assert anchor == action_id
    assert anchor.endswith("-2")


def test_report_multiple_attachments_share_saved_gap_order_and_never_duplicate(tmp_path):
    run = snapshot(2)
    timeline = run["scenarios"][0]["evidence"]["timeline"]
    second = {**timeline[-1], "id": "capture-db-2", "title": "Segundo anexo", "planned_order": 1,
              "attached_at": "2026-10-16T10:04:00Z", "occurred_at": "2026-10-16T10:04:00Z"}
    timeline.insert(6, second)
    _report, document = render(tmp_path, run)
    assert [title for _id, _kind, title in native_steps(document)][1:5] == [
        "Selecionar opção", "Imagem anexada pelo usuário", "Segundo anexo", "Depois de selecionar opção",
    ]


def test_unresolvable_slot_is_explicit_not_clamped_to_a_different_step(tmp_path):
    run = snapshot(500)
    _report, document = render(tmp_path, run)
    assert native_steps(document)[-1][2] == "Imagem anexada pelo usuário"
    assert 'sem posição atribuída e com seu horário real' in document
    assert 'data-step-order="planned"' not in document


def test_unowned_execution_context_is_not_reordered_as_a_ct_roteiro(tmp_path):
    run = snapshot(2)
    report, _document = render(tmp_path, run)
    context = {"id": "context", "evidence": deepcopy(run["scenarios"][0]["evidence"])}
    raw = deepcopy(context)
    document = report._timeline(run["id"], context, observation=True)
    assert document.index('<h3>Depois da ação</h3>') < document.index('<h3>Imagem anexada pelo usuário</h3>')
    assert 'data-step-order="planned"' not in document
    assert context == raw
