"""Fast case browsing is distinct from a self-contained offline export."""
import base64
from copy import deepcopy
from pathlib import Path
import re

import pytest

from test_scenario_rerun_api import runtime
from test_report_premium import data
from test_test_suites import create_plan, new_manual, round_state
from autojev.domain.models import ExplorationRequest, ExplorationOptions, ModelSelection, TestScenario as StoredScenario

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")


def with_shot(app, owner, identity="CT001", filename="evidence.png"):
    repo = app.state.repository
    original = next(case for case in repo.get_exploration(owner)["scenarios"] if case["id"] == identity)
    directory = app.state.settings.artifact_dir / owner / "screenshots"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_bytes(PNG)
    repo.add_screenshot(owner, filename, "Captura", identity)
    value = {**original, "screenshots": [filename], "evidence": {**original["evidence"], "timeline": [
        {"kind": "observation", "screenshot": filename, "title": "Antes da ação", "occurred_at": "2026-10-14T12:00:00Z"},
        {"kind": "action", "step_number": 1, "title": "Selecionar opção"},
    ]}}
    repo.add_scenario(owner, int(identity[2:]), StoredScenario(**value))


def test_preview_filters_before_rendering_and_never_reads_image_bytes(runtime, monkeypatch):
    app, client, root = runtime
    repo = app.state.repository
    other = deepcopy(repo.get_exploration(root)["scenarios"][0])
    other.update(id="CT002", title="Unrelated test must not be rendered")
    repo.add_scenario(root, 2, StoredScenario(**other))
    with_shot(app, root)
    with_shot(app, root, "CT002", "unrelated.png")
    before = deepcopy(repo.get_exploration(root))

    def no_image_bytes(path):
        raise AssertionError("Preview must not read/encode captures: " + str(path))
    monkeypatch.setattr(Path, "read_bytes", no_image_bytes)
    class BusyExport:
        def __enter__(self):
            raise AssertionError("Fast browsing cannot wait for the offline-export lock")
        def __exit__(self, *args):
            pass
    app.state.test_suite_service.report_gate = BusyExport()
    response = client.get(f"/api/test-suites/{root}/report?case_id=CT001&preview=true")
    assert response.status_code == 200, response.text
    payload = data(response.text)
    assert [case["id"] for case in payload["cases"]] == ["CT001"]
    assert payload["suite_context"]["focused_case_id"] == "CT001"
    assert "unrelated.png" not in response.text
    assert other["title"] not in response.text
    assert payload["online_preview"]["image_sizes"] == {f"http://testserver/api/explorations/{root}/screenshots/evidence.png": len(PNG)}
    sources = re.findall(r'<img[^>]*\bsrc="([^"]+)"', response.text)
    assert sources and all(source.startswith(f"http://testserver/api/explorations/{root}/screenshots/") for source in sources)
    assert '<img loading="lazy"' in response.text
    assert len(response.content) < 700_000
    assert repo.get_exploration(root) == before
    assert not app.state.scenario_rerun_service.tasks


def test_offline_download_still_embeds_selected_evidence_and_normal_export_keeps_all_cts(runtime):
    app, client, root = runtime
    with_shot(app, root)
    new_manual(client, root, "Outro CT não executado")
    preview = client.get(f"/api/test-suites/{root}/report?scope=consolidated&case_id=CT001&preview=true")
    export_path = data(preview.text)["online_preview"]["export_path"]
    offline = client.get("/api" + export_path)
    assert offline.status_code == 200
    assert "online_preview" not in data(offline.text)
    assert [case["id"] for case in data(offline.text)["cases"]] == ["CT001"]
    assert re.findall(r'<img[^>]*\bsrc="([^"]+)"', offline.text)[0].startswith("data:image/png;base64,")
    all_cases = client.get(f"/api/test-suites/{root}/report")
    assert len(data(all_cases.text)["cases"]) == 2
    assert "online_preview" not in data(all_cases.text)
    assert not list((app.state.settings.artifact_dir / root).glob("tmp*.html")), "Temporary streams are removed"


def test_focused_preview_keeps_real_history_owners_and_stable_parent_identity(runtime):
    app, client, root = runtime
    repo = app.state.repository
    original = deepcopy(repo.get_exploration(root)["scenarios"][0])
    original["id"], original["title"] = "CT147", "Original parent case"
    repo.add_scenario(root, 147, StoredScenario(**original))
    with_shot(app, root, "CT147", "parent.png")
    child = repo.create_exploration(ExplorationRequest("https://shop.test", ModelSelection("local", "agent"), ModelSelection("local", "aux"), ExplorationOptions()))
    original.update(id="CT001", title="Latest child case", status="FAIL")
    repo.add_scenario(child, 1, StoredScenario(**original))
    with_shot(app, child, "CT001", "child.png")
    repo.add_event(child, "info", "scenario_rerun_source", "Source", {"source_exploration_id": root, "source_scenario_id": "CT147"})
    repo.update_exploration(child, status="completed")
    before = deepcopy(repo.get_exploration(root))
    response = client.get(f"/api/test-suites/{root}/report?case_id=CT147&preview=true")
    assert response.status_code == 200, response.text
    payload = data(response.text)
    case, = payload["cases"]
    assert case["id"] == "CT147" and case["status"] == "FAIL"
    assert len(case["execution_history"]) == 2
    assert case["execution_revision"]["exploration_id"] == child
    images = payload["online_preview"]["image_sizes"]
    assert f"http://testserver/api/explorations/{root}/screenshots/parent.png" in images
    assert f"http://testserver/api/explorations/{child}/screenshots/child.png" in images
    assert repo.get_exploration(root) == before


def test_selected_manual_round_uses_only_its_own_result(runtime):
    app, client, root = runtime
    item = new_manual(client, root, "Conferência manual")
    plan = create_plan(client, root, [item["id"]])
    assert client.post(f"/api/test-suites/rounds/{plan['id']}/start", json={"confirm": True, "expected_revision": 1}).status_code == 202
    current = round_state(client, plan["id"], "waiting_manual")
    assert client.post(f"/api/test-suites/rounds/{plan['id']}/cases/{item['id']}/result", json={"expected_revision": current["revision"], "status": "BLOCKED", "actual_result": "Acesso não disponível"}).status_code == 200
    round_state(client, plan["id"], "completed")
    response = client.get(f"/api/test-suites/{root}/report?scope=round&round_id={plan['id']}&case_id={item['id']}&preview=true")
    assert response.status_code == 200
    case, = data(response.text)["cases"]
    assert case["id"] == item["id"] and case["status"] == "BLOCKED"
    assert client.get(f"/api/test-suites/{root}/report?scope=round&round_id={plan['id']}&case_id=CT001&preview=true").status_code == 404


@pytest.mark.parametrize("query,status", [("preview=true", 422), ("case_id=unknown&preview=true", 404), ("case_id=&preview=true", 422), ("scope=initial&case_id=unknown", 404)])
def test_invalid_focus_never_falls_back_to_the_full_suite(runtime, query, status):
    app, client, root = runtime
    before = app.state.repository.get_exploration(root)
    response = client.get(f"/api/test-suites/{root}/report?{query}")
    assert response.status_code == status
    assert app.state.repository.get_exploration(root) == before
    assert not app.state.scenario_rerun_service.tasks
