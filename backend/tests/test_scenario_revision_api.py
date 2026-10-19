"""HTTP projection refreshes reports without changing saved CT evidence."""
import base64
import json
import re
from io import BytesIO

import pytest
from autojev.adapters.openai_compatible import OpenAICompatibleClient
from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.adapters.report import StandaloneHtmlReport
from autojev.main import create_app
from fastapi.testclient import TestClient
from PIL import Image
from test_api import settings
from test_scenario_revisions import case, run


def report_data(markup):
    return json.loads(re.search(r'<script id="report-data" type="application/json">(.*?)</script>',
                                markup, re.DOTALL).group(1))


def image_bytes(color):
    buffer = BytesIO()
    Image.new("RGB", (2, 2), color).save(buffer, format="PNG")
    return buffer.getvalue()


def save_original(app, identity, *, image=None):
    artifact = app.state.settings.artifact_dir / identity
    if image:
        shots = artifact / "screenshots"
        shots.mkdir(parents=True, exist_ok=True)
        (shots / "same-name.png").write_bytes(image)
    path = artifact / "report.html"
    app.state.repository.update_exploration(identity, report_path=str(path))
    StandaloneHtmlReport(app.state.settings.artifact_dir).generate(app.state.repository.get_exploration(identity), str(path))
    return path


@pytest.fixture
def api(tmp_path, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Reading a report must not start a browser or call a model")

    monkeypatch.setattr(PlaywrightExecutor, "start", forbidden)
    monkeypatch.setattr(OpenAICompatibleClient, "chat_json", forbidden)
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        yield app, client
        assert not app.state.exploration_manager.tasks
        assert not app.state.scenario_rerun_service.tasks


def test_root_detail_shows_latest_stable_ct_and_history_but_durable_records_stay_original(api):
    app, client = api
    repo = app.state.repository
    root = run(repo, cases=[case("CT042"), case("CT043", status="FAIL")])
    latest = run(repo, 2, parent=(root, "CT042"), cases=[case(status="BLOCKED")], status="partial")
    repo.add_action(latest, 1, {"action": "select"}, {"evidence": {"rerun_recipe": {"private": "not public"}}})
    original = repo.get_exploration(root)
    replay = repo.get_exploration(latest)
    response = client.get(f"/api/explorations/{root}")
    assert response.status_code == 200
    body = response.json()
    selected, untouched = body["scenarios"]
    assert selected["id"] == "CT042" and selected["status"] == "BLOCKED"
    assert selected["execution_revision"]["scenario_id"] == "CT001"
    assert selected["evidence_exploration_id"] == latest
    assert [row["status"] for row in selected["execution_history"]] == ["PASS", "BLOCKED"]
    assert untouched["id"] == "CT043" and untouched["status"] == "FAIL"
    assert body["summary"]["total_scenarios"] == 2 and body["summary"]["blocked"] == 1
    assert "rerun_recipe" not in response.text and "not public" not in response.text
    assert repo.get_exploration(root) == original
    assert repo.get_exploration(latest) == replay
    freshness = client.get(f"/api/explorations/{root}/scenario-revisions")
    assert freshness.json() == {"key": body["scenario_revisions_key"]}
    assert "no-store" in freshness.headers["cache-control"]


def test_report_refresh_uses_latest_owner_images_and_invalidates_without_rewriting_source_html(api):
    app, client = api
    repo = app.state.repository
    root = run(repo, cases=[case("CT042")])
    original_image, latest_image = image_bytes("red"), image_bytes("blue")
    original_html = save_original(app, root, image=original_image)
    saved_bytes = original_html.read_bytes()
    first = client.get(f"/api/explorations/{root}/report")
    assert first.status_code == 200
    original_key = report_data(first.text)["scenario_revisions_key"]
    latest = run(repo, 2, parent=(root, "CT042"), cases=[case(status="FAIL")])
    save_original(app, latest, image=latest_image)
    originals = {identity: repo.get_exploration(identity) for identity in (root, latest)}
    response = client.get(f"/api/explorations/{root}/report")
    assert response.status_code == 200 and "no-store" in response.headers["cache-control"]
    body = report_data(response.text)
    current = body["cases"][0]
    assert current["id"] == "CT042" and current["status"] == "FAIL"
    assert current["execution_revision"]["exploration_id"] == latest
    assert current["rerun_source_exploration_id"] == latest and current["rerun_source_scenario_id"] == "CT001"
    assert body["scenario_revisions_key"] != original_key
    assert base64.b64encode(latest_image).decode() in response.text
    assert base64.b64encode(original_image).decode() in response.text
    assert 'id="case-history-archive"' in response.text
    assert original_html.read_bytes() == saved_bytes
    assert all(repo.get_exploration(identity) == snapshot for identity, snapshot in originals.items())
    # Same verdict, changed evidence: counts remain equal, the cached HTML must
    # nevertheless refresh and retain the immutable source artifact.
    previous = response.content
    repo.add_scenario(latest, 1, case(status="FAIL", screenshot="different-image.png"))
    next_response = client.get(f"/api/explorations/{root}/report")
    next_data = report_data(next_response.text)
    assert next_response.content != previous
    assert next_data["scenario_revisions_key"] != body["scenario_revisions_key"]
    assert next_data["cases"][0]["status"] == "FAIL"
    assert original_html.read_bytes() == saved_bytes
    token = client.get(f"/api/explorations/{root}/scenario-revisions")
    assert token.json()["key"] == next_data["scenario_revisions_key"]


@pytest.mark.parametrize("with_state", [False, True])
def test_rerun_live_detail_stays_on_its_execution_but_report_can_show_only_its_descendants(api, with_state):
    app, client = api
    repo = app.state.repository
    root = run(repo, cases=[case("CT147")])
    first = run(repo, 2, parent=(root, "CT147"), cases=[case(status="FAIL")])
    descendant = run(repo, 3, parent=(first, "CT001"), cases=[case(status="BLOCKED")], status="partial")
    sibling = run(repo, 4, parent=(root, "CT147"), cases=[case(status="PASS")])
    save_original(app, first)
    if with_state:
        repo.add_event(first, "info", "scenario_rerun_state", "Finished reexecution", {
            "source_exploration_id": root, "source_scenario_id": "CT147", "exploration_id": first,
            "running": False, "phase": "finished", "scope": {"local_checks": True},
        })
    live = client.get(f"/api/explorations/{first}")
    assert live.status_code == 200 and live.json()["scenarios"][0]["status"] == "FAIL"
    if with_state:
        assert live.json()["rerun"]["source_exploration_id"] == root
        assert live.json()["rerun"]["report_exploration_id"] == root
        assert live.json()["rerun"]["report_scenario_id"] == "CT147"
    report = client.get(f"/api/explorations/{first}/report")
    assert report.status_code == 200
    body = report_data(report.text)
    selected = body["cases"][0]
    assert selected["status"] == "BLOCKED" and selected["execution_revision"]["exploration_id"] == descendant
    assert [row["exploration_id"] for row in selected["execution_history"]] == [first, descendant]
    assert sibling not in [row["exploration_id"] for row in selected["execution_history"]]
    assert client.get(f"/api/explorations/{first}/scenario-revisions").json()["key"] == body["scenario_revisions_key"]


def test_new_attempt_without_result_changes_history_token_not_current_test(api):
    app, client = api
    repo = app.state.repository
    root = run(repo)
    initial = client.get(f"/api/explorations/{root}/scenario-revisions").json()["key"]
    interrupted = run(repo, 2, parent=(root, "CT001"), status="cancelled", cases=[])
    next_key = client.get(f"/api/explorations/{root}/scenario-revisions").json()["key"]
    assert initial != next_key
    selected = client.get(f"/api/explorations/{root}").json()["scenarios"][0]
    assert selected["status"] == "PASS" and selected["execution_revision"]["exploration_id"] == root
    assert selected["execution_history"][-1]["exploration_id"] == interrupted
    assert selected["execution_history"][-1]["has_result"] is False
    assert client.get("/api/explorations/no-such-run/scenario-revisions").status_code == 404


def test_nested_rerun_detail_points_primary_report_at_root_without_changing_own_result(api):
    app, client = api
    repo = app.state.repository
    root = run(repo, cases=[case("CT147")])
    first = run(repo, 2, parent=(root, "CT147"), cases=[case(status="FAIL")])
    nested = run(repo, 3, parent=(first, "CT001"), cases=[case(status="BLOCKED")], status="partial")
    repo.add_event(nested, "info", "scenario_rerun_state", "Finished reexecution", {
        "source_exploration_id": first, "source_scenario_id": "CT001", "exploration_id": nested,
        "running": False, "phase": "finished", "scope": {"local_checks": True},
    })
    raw = repo.get_exploration(nested)
    response = client.get(f"/api/explorations/{nested}")
    assert response.status_code == 200
    body = response.json()
    assert body["scenarios"][0]["status"] == "BLOCKED" and body["scenarios"][0]["id"] == "CT001"
    assert body["rerun"]["source_exploration_id"] == first
    assert body["rerun"]["report_exploration_id"] == root and body["rerun"]["report_scenario_id"] == "CT147"
    assert repo.get_exploration(nested) == raw


@pytest.mark.parametrize("invalid", ["malformed", "cycle"])
def test_bad_rerun_ancestry_keeps_primary_report_link_on_own_execution(api, invalid):
    app, client = api
    repo = app.state.repository
    root = run(repo, cases=[case("CT147")])
    child = run(repo, 2, parent=(root, "CT147"))
    if invalid == "cycle":
        repo.add_event(root, "info", "scenario_rerun_source", "Cyclic link", {
            "source_exploration_id": child, "source_scenario_id": "CT001",
        })
    else:
        repo.add_event(child, "info", "scenario_rerun_source", "Conflicting link", {
            "source_exploration_id": root, "source_scenario_id": "CT999",
        })
    repo.add_event(child, "info", "scenario_rerun_state", "Finished reexecution", {
        "source_exploration_id": root, "source_scenario_id": "CT147", "exploration_id": child,
        "running": False, "phase": "finished", "scope": {"local_checks": True},
    })
    response = client.get(f"/api/explorations/{child}")
    assert response.status_code == 200
    assert response.json()["rerun"]["report_exploration_id"] == child
    assert response.json()["rerun"]["report_scenario_id"] == "CT001"
