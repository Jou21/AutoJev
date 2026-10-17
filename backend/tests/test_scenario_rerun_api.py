from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from autojev.config import Settings
from autojev.domain.models import (
    ExplorationOptions, ExplorationRequest, ModelSelection, ScenarioStatus,
    TestScenario as StoredScenario,
)
from autojev.main import create_app
from autojev.services.rerun_projection import without_rerun_recipes


@pytest.fixture
def runtime(tmp_path: Path):
    settings = Settings(tmp_path, tmp_path / "db.sqlite3", tmp_path / "artifacts",
                        "test-passphrase", ("http://localhost:3000",), True, "chromium", 1)
    app = create_app(settings)
    with TestClient(app) as client:
        repository = app.state.repository
        source = repository.create_exploration(ExplorationRequest(
            "https://shop.test", ModelSelection("local", "agent"),
            ModelSelection("local", "aux"), ExplorationOptions(),
        ))
        repository.add_scenario(source, 1, StoredScenario(
            "CT001", "Selecionar opção", "Registro original", [], ["Selecionar"],
            "Opção aplicada", "Opção aplicada", ScenarioStatus.PASS,
            "https://shop.test", "2026-10-14T12:00:00Z",
            evidence={"action_step": 1, "assertions": [{"kind": "selection_matches", "passed": True}]},
        ))
        repository.update_exploration(source, status="completed")
        yield app, client, source


def path(source):
    return f"/api/explorations/{source}/scenarios/CT001/rerun"


def test_historical_preflight_is_read_only_and_explains_missing_recipe(runtime):
    app, client, source = runtime
    before = app.state.repository.get_exploration(source)
    response = client.get(path(source))
    assert response.status_code == 200
    assert response.json()["available"] is False
    assert response.json()["code"] == "missing_recipe"
    assert response.json()["manual_preparation_required"] is True
    assert "banco de dados" in " ".join(response.json()["effects"])
    assert app.state.repository.get_exploration(source) == before
    assert app.state.scenario_rerun_service.tasks == {}
    assert client.post(path(source), json={"confirm": True}).status_code == 409
    assert app.state.repository.get_exploration(source) == before


@pytest.mark.parametrize("review_requested, has_visual_assertion", [
    (False, False), (True, False), (False, True), (True, True),
])
def test_preflight_distinguishes_optional_review_from_an_original_visual_check(
    runtime, review_requested, has_visual_assertion,
):
    app, client, source = runtime
    repository = app.state.repository
    scenario = StoredScenario(**repository.get_exploration(source)["scenarios"][0])
    scenario.evidence["visual_assessment"] = {"requested": review_requested, "status": "assessed"}
    if has_visual_assertion:
        scenario.evidence["assertions"].append({"kind": "visual_model", "expected": "No overlap", "passed": True})
    repository.add_scenario(source, 1, scenario)
    before = repository.get_exploration(source)
    response = client.get(path(source))
    assert response.status_code == 200
    result = response.json()
    assert result["scope"]["visual_review"] is False
    assert result["scope"]["missing_visual_checks"] is has_visual_assertion
    assert result["scope"]["visual_review_omitted"] is (review_requested or has_visual_assertion)
    assert result["checks"] == ["Opção selecionada"]
    assert "sem chamadas ao JEV ou à LLM auxiliar" in " ".join(result["effects"])
    assert repository.get_exploration(source) == before
    assert app.state.scenario_rerun_service.tasks == {}


@pytest.mark.parametrize("body", [{}, {"confirm": False}, {"confirm": 1}, {"confirm": "true"},
                                   {"confirm": True, "sql": "DROP TABLE anything"},
                                   {"confirm": True, "steps": [{"type": "database"}]}])
def test_rerun_requires_strict_explicit_consent_and_never_accepts_custom_execution(body, runtime):
    app, client, source = runtime
    assert client.post(path(source), json=body).status_code == 422
    assert app.state.scenario_rerun_service.tasks == {}


@pytest.mark.parametrize("origin", ["null", "https://unrelated.test", "http://localhost:3000.evil.test"])
def test_untrusted_browser_origin_cannot_start_a_rerun(origin, runtime):
    app, client, source = runtime
    assert client.post(path(source), json={"confirm": True}, headers={"Origin": origin}).status_code == 403
    assert app.state.scenario_rerun_service.tasks == {}


def test_confirmed_start_and_existing_controls_delegate_to_rerun_service_without_old_history_changes(runtime):
    app, client, source = runtime
    repository = app.state.repository
    before = repository.get_exploration(source)
    child = repository.create_exploration(ExplorationRequest(
        "https://shop.test", ModelSelection("local", "agent"), ModelSelection("local", "aux"),
        ExplorationOptions(mode="single_ct_rerun"),
    ))
    calls = []
    service = app.state.scenario_rerun_service
    service.submit = lambda run, case: calls.append(("submit", run, case)) or child
    service.state_for = lambda run: {"source_exploration_id": source, "source_scenario_id": "CT001"} if run == child else {}
    service.resume = lambda run: calls.append(("resume", run))
    service.cancel = lambda run: calls.append(("cancel", run))
    response = client.post(path(source), json={"confirm": True}, headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 202
    assert response.json()["id"] == child and child != source
    assert response.json()["rerun"]["source_scenario_id"] == "CT001"
    assert client.post(f"/api/explorations/{child}/resume").status_code == 202
    assert client.post(f"/api/explorations/{child}/cancel").status_code == 202
    assert calls == [("submit", source, "CT001"), ("resume", child), ("cancel", child)]
    assert repository.get_exploration(source) == before


def test_recipes_are_private_copies_not_api_or_exported_evidence(runtime, tmp_path):
    app, client, source = runtime
    repository = app.state.repository
    recipe = {"version": "ct-rerun-v1", "private_sentinel": "private-replay-metadata-do-not-export"}
    repository.add_action(source, 1, {"action": "select", "target": "e0"}, {
        "before_url": "https://shop.test", "after_url": "https://shop.test",
        "evidence": {"rerun_recipe": recipe},
    })
    before = repository.get_exploration(source)
    projected = client.get(f"/api/explorations/{source}")
    assert projected.status_code == 200
    assert "rerun_recipe" not in projected.text
    assert recipe["private_sentinel"] not in projected.text
    output = app.state.settings.artifact_dir / source / "report.html"
    app.state.exploration_manager.report_renderer.generate(before, str(output))
    repository.update_exploration(source, report_path=str(output))
    original_html = output.read_text()
    response = client.get(f"/api/explorations/{source}/report")
    assert response.status_code == 200
    assert recipe["private_sentinel"] not in response.text
    assert '"run_id"' in response.text
    assert output.read_text() == original_html
    assert repository.get_exploration(source)["actions"] == before["actions"]
    value = {"nested": [{"rerun_recipe": recipe, "passed": True}], "status": "PASS"}
    unchanged = deepcopy(value)
    assert without_rerun_recipes(value) == {"nested": [{"passed": True}], "status": "PASS"}
    assert value == unchanged


def test_unknown_source_or_case_is_not_a_new_execution(runtime):
    _, client, source = runtime
    assert client.get(path("missing-source")).status_code == 404
    assert client.get(path(source).replace("CT001", "CT999")).status_code == 404


def test_historical_preparation_block_has_human_reason_without_rewriting_history(runtime):
    app, client, source = runtime
    repository = app.state.repository
    repository.add_event(source, "info", "scenario_rerun_state", "Estado preservado", {
        "source_exploration_id": "original-run", "source_scenario_id": "CT001",
        "running": False, "phase": "finished", "reason": "before_url_changed",
    })
    repository.update_exploration(source, status="partial", error="before_url_changed")
    before = repository.get_exploration(source)
    response = client.get(f"/api/explorations/{source}")
    assert response.status_code == 200
    assert response.json()["rerun"]["reason_label"] == (
        "A página aberta não é a mesma em que o teste original começou."
    )
    assert response.json()["rerun"]["reason"] == "before_url_changed"
    assert repository.get_exploration(source) == before


def test_capture_plan_handoff_is_explicit_and_keeps_source_history_immutable(runtime):
    app, client, source = runtime
    repository = app.state.repository
    original = repository.get_exploration(source)
    child = repository.create_exploration(ExplorationRequest(
        "https://shop.test", ModelSelection("local", "agent"), ModelSelection("local", "aux"),
        ExplorationOptions(mode="single_ct_rerun"),
    ))
    calls = []
    plan = {"version": 1, "checkpoints": [{"id": "db-1", "gap": 3, "order": 0}]}
    service = app.state.scenario_rerun_service
    service.submit = lambda run, case, **kwargs: calls.append((run, case, kwargs)) or child
    service.state_for = lambda run: {"source_exploration_id": source, "source_scenario_id": "CT001"} if run == child else {}
    response = client.post(path(source), json={"confirm": True, "capture_plan": plan},
                           headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 202
    assert calls == [(source, "CT001", {"capture_plan": plan})]
    assert repository.get_exploration(source) == original


@pytest.mark.parametrize("plan", [
    {"version": True, "checkpoints": []}, {"version": "1", "checkpoints": []},
    {"version": 2, "checkpoints": []},
    {"version": 1, "checkpoints": [], "sql": "DROP TABLE private"},
    {"version": 1, "checkpoints": [{"id": "db-1", "gap": True, "order": 0}]},
    {"version": 1, "checkpoints": [{"id": "db-1", "gap": -1, "order": 0}]},
    {"version": 1, "checkpoints": [{"id": "other", "gap": 1, "order": 0}]},
    {"version": 1, "checkpoints": [{"id": "db-1", "gap": 1, "order": 0, "type": "sql"}]},
    {"version": 1, "checkpoints": [{"id": "db-1", "gap": 1, "order": 0}] * 2},
    {"version": 1, "checkpoints": [{"id": f"db-{index}", "gap": 1, "order": index} for index in range(101)]},
])
def test_capture_plan_schema_rejects_unbounded_or_executable_payloads(runtime, plan):
    app, client, source = runtime
    assert client.post(path(source), json={"confirm": True, "capture_plan": plan}).status_code == 422
    assert app.state.scenario_rerun_service.tasks == {}


def capture_path(run):
    return f"/api/explorations/{run}/capture-print"


def test_capture_endpoint_requires_exact_checkpoint_and_separate_explicit_consent(runtime, user_print_image):
    app, client, source = runtime
    calls = []
    service = app.state.scenario_rerun_service
    service.state_for = lambda run: {"source_exploration_id": "original", "phase": "waiting_capture"}
    service.capture_print = lambda run, identity, version, image: calls.append((run, identity, version, image)) or {"phase": "capturing_print"}
    body = {"confirm": True, "checkpoint_id": "db-1", "checkpoint_version": 2,
            "image_data_url": user_print_image["data_url"]}
    response = client.post(capture_path(source), json=body, headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 202
    assert len(calls) == 1 and calls[0][:3] == (source, "db-1", 2)
    assert calls[0][3].content == user_print_image["bytes"]
    assert calls[0][3].sha256 == user_print_image["sha256"]
    assert user_print_image["data_url"] not in response.text


@pytest.mark.parametrize("body", [
    {}, {"confirm": False, "checkpoint_id": "db-1", "checkpoint_version": 1},
    {"confirm": "true", "checkpoint_id": "db-1", "checkpoint_version": 1},
    {"confirm": True, "checkpoint_id": "db-1", "checkpoint_version": True},
    {"confirm": True, "checkpoint_id": "db-1", "checkpoint_version": "1"},
    {"confirm": True, "checkpoint_id": "db-1", "checkpoint_version": 0},
    {"confirm": True, "checkpoint_id": "other", "checkpoint_version": 1},
    {"confirm": True, "checkpoint_id": "db-1", "checkpoint_version": 1, "sql": "SELECT private"},
])
def test_capture_endpoint_rejects_malformed_or_implicit_authority(runtime, body):
    app, client, source = runtime
    app.state.scenario_rerun_service.capture_print = lambda *_args: pytest.fail("Invalid input reached capture")
    assert client.post(capture_path(source), json=body).status_code == 422


@pytest.mark.parametrize("origin", ["null", "https://unrelated.test", "http://localhost:3000.evil.test"])
def test_capture_endpoint_rejects_untrusted_browser_origins(runtime, user_print_image, origin):
    app, client, source = runtime
    app.state.scenario_rerun_service.capture_print = lambda *_args: pytest.fail("Untrusted origin reached capture")
    body = {"confirm": True, "checkpoint_id": "db-1", "checkpoint_version": 1,
            "image_data_url": user_print_image["data_url"]}
    assert client.post(capture_path(source), json=body, headers={"Origin": origin}).status_code == 403


def test_stale_checkpoint_cannot_capture_or_bypass_the_gate(runtime, user_print_image):
    app, client, source = runtime
    service = app.state.scenario_rerun_service
    service.state_for = lambda run: {"source_exploration_id": "original", "phase": "waiting_capture"}

    def reject_stale(*_args):
        raise ValueError("Este print não está mais aguardando captura.")

    service.capture_print = reject_stale
    body = {"confirm": True, "checkpoint_id": "db-1", "checkpoint_version": 1,
            "image_data_url": user_print_image["data_url"]}
    assert client.post(capture_path(source), json=body).status_code == 409


@pytest.mark.parametrize("image", [None, True, 123, "", "data:image/png;base64,invalid!",
                                   "data:image/svg+xml;base64,PHN2Zy8+", "https://example.test/picture.png"])
def test_capture_endpoint_rejects_invalid_images_without_consuming_confirmation(runtime, image):
    app, client, source = runtime
    original = app.state.repository.get_exploration(source)
    app.state.scenario_rerun_service.capture_print = lambda *_args: pytest.fail("Invalid image reached capture authority")
    body = {"confirm": True, "checkpoint_id": "db-1", "checkpoint_version": 1, "image_data_url": image}
    response = client.post(capture_path(source), json=body, headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 422
    assert app.state.repository.get_exploration(source) == original


def test_capture_endpoint_requires_confirmation_even_with_a_valid_image(runtime, user_print_image):
    app, client, source = runtime
    app.state.scenario_rerun_service.capture_print = lambda *_args: pytest.fail("Missing consent reached capture")
    body = {"checkpoint_id": "db-1", "checkpoint_version": 1, "image_data_url": user_print_image["data_url"]}
    assert client.post(capture_path(source), json=body).status_code == 422
    body["confirm"] = False
    assert client.post(capture_path(source), json=body).status_code == 422


def test_attachment_storage_failure_is_recoverable_and_does_not_leak_server_paths(runtime, user_print_image):
    app, client, source = runtime
    original = app.state.repository.get_exploration(source)

    def storage_failure(*_args):
        raise OSError("Cannot write /private/server/evidence.png")

    app.state.scenario_rerun_service.capture_print = storage_failure
    body = {"confirm": True, "checkpoint_id": "db-1", "checkpoint_version": 1,
            "image_data_url": user_print_image["data_url"]}
    response = client.post(capture_path(source), json=body, headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 503
    assert "/private/server" not in response.text and user_print_image["data_url"] not in response.text
    assert app.state.repository.get_exploration(source) == original


@pytest.mark.parametrize("extra", [{"filename": "../../replace.png"}, {"path": "/tmp/replace.png"},
                                    {"url": "https://example.test/external.png"}, {"sql": "SELECT private"}])
def test_valid_image_does_not_authorize_client_paths_or_extra_execution(runtime, user_print_image, extra):
    app, client, source = runtime
    app.state.scenario_rerun_service.capture_print = lambda *_args: pytest.fail("Unexpected fields reached capture")
    body = {"confirm": True, "checkpoint_id": "db-1", "checkpoint_version": 1,
            "image_data_url": user_print_image["data_url"], **extra}
    assert client.post(capture_path(source), json=body).status_code == 422


@pytest.mark.parametrize("has_browser_capture", [False, True])
def test_user_attachment_remains_in_ct_gallery_but_is_never_a_browser_last_page_preview(
    runtime, user_print_image, has_browser_capture,
):
    app, client, source = runtime
    repository = app.state.repository
    scenario = StoredScenario(**repository.get_exploration(source)["scenarios"][0])
    directory = app.state.settings.artifact_dir / source / "screenshots"
    directory.mkdir(parents=True, exist_ok=True)
    if has_browser_capture:
        (directory / "browser-final.png").write_bytes(user_print_image["bytes"])
        repository.add_screenshot(source, "browser-final.png", "Depois da ação", "CT001")
    attachment_path = directory / "user-attachment.png"
    attachment_path.write_bytes(user_print_image["bytes"])
    attachment = {
        "id": "capture-db-1", "kind": "observation", "title": "Imagem anexada pelo usuário",
        "screenshot": attachment_path.name, "checkpoint_id": "db-1", "planned_gap": 3,
        "occurred_at": "2026-10-16T12:00:00Z", "attached_at": "2026-10-16T12:00:00Z",
        "source": "user_upload", "image_source": "user_upload",
        "attachment": {"sha256": user_print_image["sha256"], "width": 240, "height": 120,
                       "mime_type": "image/png", "size_bytes": len(user_print_image["bytes"])},
    }
    scenario.evidence["manual_captures"] = [attachment]
    scenario.evidence["timeline"] = [attachment]
    scenario.screenshots.append(attachment_path.name)
    repository.add_scenario(source, 1, scenario)
    repository.add_screenshot(source, attachment_path.name, "Imagem anexada pelo usuário", "CT001", image_source="user_upload")
    repository.add_event(source, "info", "manual_print_attached", "Imagem anexada pelo usuário", attachment)
    response = client.get(f"/api/explorations/{source}")
    assert response.status_code == 200
    detail = response.json()
    upload = next(shot for shot in detail["screenshots"] if shot["path"] == attachment_path.name)
    assert upload["image_source"] == "user_upload" and upload["scenario_id"] == "CT001"
    observed_entry = next(entry for entry in detail["scenarios"][0]["evidence"]["timeline"]
                          if entry.get("id") == "capture-db-1")
    assert "url" not in observed_entry and "page_url" not in observed_entry
    expected_preview = f"/api/explorations/{source}/screenshots/browser-final.png" if has_browser_capture else None
    assert detail["latest_screenshot_url"] == detail["screenshot_url"] == expected_preview
    listing_response = client.get("/api/explorations")
    assert listing_response.status_code == 200
    listing = listing_response.json()
    if isinstance(listing, dict):
        listing = listing.get("items", listing.get("explorations", []))
    listed = next(item for item in listing if item["id"] == source)
    assert listed["latest_screenshot_url"] == listed["screenshot_url"] == expected_preview
