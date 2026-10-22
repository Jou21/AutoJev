from pathlib import Path
import os
import pytest

from autojev.adapters.openai_compatible import ProviderConnectionError
from autojev.api.routes import _phase_payload
from autojev.config import Settings
from autojev.domain.models import (
    ExplorationOptions,
    ExplorationRequest,
    ModelSelection,
    ScenarioStatus,
)
from autojev.domain.models import (
    TestScenario as StoredScenario,
)
from autojev.main import create_app
from fastapi.testclient import TestClient


def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_path=tmp_path / "db.sqlite3",
        artifact_dir=tmp_path / "artifacts",
        secret_key="test-passphrase",
        cors_origins=("http://localhost:3000",),
        headless=True,
        browser="chromium",
        http_timeout_seconds=1,
    )


def test_economical_mode_does_not_count_unrequested_visual_reviews_as_unavailable(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        repository = app.state.repository
        for mode in ("text", "visual"):
            run_id = repository.create_exploration(ExplorationRequest(
                app_url="https://example.test", agent=ModelSelection("local", "agent"),
                auxiliary_llm=ModelSelection("local", "auxiliary"),
                options=ExplorationOptions(auxiliary_evidence_mode=mode),
            ))
            repository.add_scenario(run_id, 1, StoredScenario(
                id="CT001", title="Abrir carrinho", description="Evidência local",
                preconditions=[], steps=["Clicar"], expected_result="Carrinho aberto",
                actual_result="Carrinho aberto", status=ScenarioStatus.PASS,
                url="https://example.test/cart", created_at="2026-10-04T12:00:00+00:00",
                screenshots=[], evidence={},
            ))
            counts = client.get(f"/api/explorations/{run_id}").json()["visual_review_summary"]
            assert counts["total"] == 1
            assert counts["requested"] is (mode == "visual")
            assert counts["unavailable"] == (1 if mode == "visual" else 0)
            assert counts.get("not_requested", 0) == (1 if mode == "text" else 0)
            assert not counts["assessed"] and not counts["rate_limited"]


@pytest.mark.parametrize("missing_marker", [' data-report-locale="pt-BR-v1"', ' data-report-layout="premium-v1"', ' data-report-flows="functional-flows-v1"', None])
def test_portuguese_presentation_refreshes_cached_english_report_without_rewriting_evidence(tmp_path, missing_marker):
    from autojev.adapters.report import StandaloneHtmlReport

    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        repository = app.state.repository
        run_id = repository.create_exploration(ExplorationRequest(
            app_url="https://example.test", agent=ModelSelection("local", "agent"),
            auxiliary_llm=ModelSelection("local", "auxiliary"), options=ExplorationOptions(),
        ))
        repository.add_scenario(run_id, 1, StoredScenario(
            id="CT001", title="Select: Name (Z to A)", description="Observed", preconditions=[],
            steps=["Execute select on e2"], expected_result="The selected option must match the requested value.",
            actual_result="Playwright executed select successfully", status=ScenarioStatus.PASS,
            url="https://example.test", created_at="2026-10-08T03:00:00Z", evidence={},
        ))
        original = tmp_path / "artifacts" / run_id / "report.html"
        repository.update_exploration(run_id, status="completed", report_path=str(original))
        StandaloneHtmlReport(tmp_path / "artifacts").generate(repository.get_exploration(run_id), str(original))
        # Missing markers or older presentation sources must invalidate the
        # cache while leaving the original artifact immutable.
        old_document = original.read_text()
        if missing_marker:
            old_document = old_document.replace(missing_marker, '')
        old_document = old_document.replace("Relatório de testes exploratórios", "Exploratory Test Report")
        original.write_text(old_document)
        if missing_marker is None:
            os.utime(original, (1, 1))
        before = repository.get_exploration(run_id)
        response = client.get(f"/api/explorations/{run_id}/report")
        assert response.status_code == 200
        assert 'data-report-locale="pt-BR-v1"' in response.text
        assert 'data-report-layout="premium-v1"' in response.text
        assert 'data-report-flows="functional-flows-v1"' in response.text
        assert "Relatório de testes exploratórios" in response.text
        assert "Selecionar opção: Name (Z to A)" in response.text
        assert "O Playwright executou a ação selecionar opção com sucesso" in response.text
        assert original.read_text() == old_document
        assert repository.get_exploration(run_id) == before
        assert client.get(f"/api/explorations/{run_id}/report").text == response.text
        projected = client.get(f"/api/explorations/{run_id}").json()
        assert projected["scenarios"][0]["title"] == "Selecionar opção: Name (Z to A)"
        assert projected["scenarios"][0]["status"] == "PASS"
        assert repository.get_exploration(run_id) == before


def test_legacy_report_is_presented_with_ct_timeline_without_rewriting_history(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        repository = app.state.repository
        exploration_id = repository.create_exploration(ExplorationRequest(
            app_url="https://example.test", agent=ModelSelection("local", "agent"),
            auxiliary_llm=ModelSelection("local", "auxiliary"), options=ExplorationOptions(),
        ))
        scenario = StoredScenario(
            id="CT001", title="Abrir carrinho", description="Caso histórico",
            preconditions=[], steps=["Clicar"], expected_result="Carrinho aberto",
            actual_result="Carrinho aberto; erro de rede registrado", status=ScenarioStatus.FAIL,
            url="https://example.test/cart", created_at="2026-10-02T12:00:06+00:00",
            screenshots=["after.png"], evidence={"action_succeeded": True, "assertions": [
                {"kind": "observable_state_change", "passed": True},
                {"kind": "no_new_browser_errors", "passed": False, "actual": {"network_errors": 1}},
            ]},
        )
        repository.add_scenario(exploration_id, 1, scenario)
        repository.add_action(exploration_id, 1, {"action": "click"}, {
            "action": "click", "success": True, "started_at": "2026-10-02T12:00:02+00:00",
            "finished_at": "2026-10-02T12:00:04+00:00", "before_url": "https://example.test/items",
            "after_url": "https://example.test/cart", "screenshot": "after.png",
            "assertions": scenario.evidence["assertions"],
        })
        repository.add_issue(exploration_id, {"type": "http", "status_code": 401,
            "message": "POST returned HTTP 401", "url": "https://events.backtrace.io/api/submit",
            "occurred_at": "2026-10-02T12:00:03+00:00", "screenshot": "after.png"})
        directory = tmp_path / "artifacts" / exploration_id
        directory.mkdir(parents=True)
        original_path = directory / "report.html"
        original_document = "<html><body>Relatório original sem linha do tempo</body></html>"
        original_path.write_text(original_document)
        repository.update_exploration(exploration_id, status="completed", report_path=str(original_path))

        response = client.get(f"/api/explorations/{exploration_id}/report")
        assert response.status_code == 200
        assert 'data-report-schema="ct-timeline-v1"' in response.text
        assert "Linha do tempo" in response.text
        assert "Telemetria externa" in response.text
        assert "Verificação técnica falhou" in response.text
        assert "Sinais técnicos observados (" not in response.text
        assert original_path.read_text() == original_document
        assert repository.get_exploration(exploration_id)["scenarios"][0]["status"] == "FAIL"
        assert repository.get_report_path(exploration_id) == str(original_path)
        second = client.get(f"/api/explorations/{exploration_id}/report")
        assert second.status_code == 200 and second.text == response.text
        cached = directory / "report-ct-timeline-v1.html"
        cached.write_text('<html data-report-schema="ct-timeline-v1">Interrupted write')
        repaired = client.get(f"/api/explorations/{exploration_id}/report")
        assert repaired.status_code == 200
        assert repaired.text.rstrip().endswith("</html>")
        assert "Interrupted write" not in repaired.text
        assert original_path.read_text() == original_document
        details = client.get(f"/api/explorations/{exploration_id}").json()
        assert details["scenarios"][0]["evidence"]["technical_signals"][0]["affects_verdict"] is False
        assert details["scenarios"][0]["status"] == "FAIL"
        assert details["summary"]["total_scenarios"] == 1


def test_current_timeline_report_refreshes_total_duration_without_changing_original_or_verdicts(tmp_path):
    from autojev.adapters.report import StandaloneHtmlReport

    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        repository = app.state.repository
        exploration_id = repository.create_exploration(ExplorationRequest(
            app_url="https://example.test", agent=ModelSelection("local", "agent"),
            auxiliary_llm=ModelSelection("local", "auxiliary"),
            options=ExplorationOptions(auxiliary_evidence_mode="visual"),
        ))
        scenario = StoredScenario(
            id="CT001", title="Local assertion", description="Recorded evidence", preconditions=[],
            steps=["Click"], expected_result="Visible", actual_result="Visible", status=ScenarioStatus.PASS,
            url="https://example.test", created_at="2026-10-04T03:22:55+00:00", evidence={"visual_assessment": {
                "requested": True, "status": "unavailable", "reason": "documentation_time_limit",
            }},
        )
        repository.add_scenario(exploration_id, 1, scenario)
        repository.update_exploration(exploration_id, status="completed",
                                      started_at="2026-10-04T03:22:54+00:00", finished_at="2026-10-04T03:28:48+00:00")
        directory = tmp_path / "artifacts" / exploration_id
        original = directory / "report.html"
        StandaloneHtmlReport(tmp_path / "artifacts").generate(repository.get_exploration(exploration_id), str(original))
        original_document = original.read_text()
        assert "00:05:54" in original_document
        repository.update_exploration(exploration_id, finished_at="2026-10-04T03:38:50+00:00", report_path=str(original))
        snapshot_before = repository.get_exploration(exploration_id)
        response = client.get(f"/api/explorations/{exploration_id}/report")
        assert response.status_code == 200
        assert "00:15:56" in response.text and "00:05:54" not in response.text
        assert "1 sem revisão antes do limite de espera das revisões auxiliares" in response.text
        assert original.read_text() == original_document
        assert repository.get_exploration(exploration_id) == snapshot_before
        assert client.get(f"/api/explorations/{exploration_id}/report").text == response.text
        details = client.get(f"/api/explorations/{exploration_id}").json()
        assert details["duration_summary"]["total_seconds"] == 956
        assert details["visual_review_summary"]["unavailable_reasons"][0]["reason"] == "documentation_time_limit"
        assert details["scenarios"][0]["status"] == "PASS"


def test_report_cache_refreshes_role_costs_without_rewriting_original_or_history(tmp_path):
    from autojev.adapters.report import StandaloneHtmlReport
    from autojev.services.scenario_revisions import project_scenario_revisions

    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        repository = app.state.repository
        exploration_id = repository.create_exploration(ExplorationRequest(
            app_url="https://example.test", agent=ModelSelection("local", "same-model"),
            auxiliary_llm=ModelSelection("local", "same-model"), options=ExplorationOptions(),
        ))

        def roles(jev, auxiliary):
            return {
                "jev": {"cost": jev, "cost_known": True, "cost_complete": True},
                "auxiliary": {"cost": auxiliary, "cost_known": True, "cost_complete": True},
            }

        repository.update_exploration(
            exploration_id, status="completed", started_at="2026-10-07T12:00:00Z",
            finished_at="2026-10-07T12:01:00Z", cost=0.03, cost_known=True,
            cost_complete=True, usage_by_role=roles(0.01, 0.02),
        )
        directory = tmp_path / "artifacts" / exploration_id
        original = directory / "report.html"
        # A current cache uses the same immutable revision projection as GET
        # /report; a raw snapshot already needs presentation regeneration.
        current = project_scenario_revisions(repository, repository.get_exploration(exploration_id))
        StandaloneHtmlReport(tmp_path / "artifacts").generate(current, str(original))
        original_document = original.read_text()
        repository.update_exploration(exploration_id, report_path=str(original))
        assert client.get(f"/api/explorations/{exploration_id}/report").text == original_document

        # The total and all timestamps remain identical; only attribution changes.
        repository.update_exploration(exploration_id, usage_by_role=roles(0.02, 0.01))
        snapshot_before = repository.get_exploration(exploration_id)
        response = client.get(f"/api/explorations/{exploration_id}/report")
        assert response.status_code == 200
        assert 'data-cost-role="jev"><strong>$0.020000</strong>' in response.text
        assert 'data-cost-role="auxiliary"><strong>$0.010000</strong>' in response.text
        assert 'data-cost-role="total"><strong>$0.030000</strong>' in response.text
        assert original.read_text() == original_document
        assert repository.get_exploration(exploration_id) == snapshot_before
        assert client.get(f"/api/explorations/{exploration_id}/report").text == response.text

        # A report without the new layout marker is never considered current,
        # even if its snapshot happens to match and the original is intact.
        cached = directory / "report-ct-timeline-v1.html"
        cached.write_text(response.text.replace(' data-cost-breakdown="roles-v1"', ''))
        repaired = client.get(f"/api/explorations/{exploration_id}/report")
        assert 'data-cost-breakdown="roles-v1"' in repaired.text
        assert original.read_text() == original_document


def test_health_and_provider_crud_never_return_api_key(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        created = client.post(
            "/api/providers",
            json={
                "name": "LiteLLM",
                "baseUrl": "http://localhost:4000",
                "apiKey": "sk-sensitive",
                "models": [
                    {
                        "id": "jev-model",
                        "label": "JEV",
                        "inputModalities": ["text", "image"],
                    }
                ],
            },
        )
        assert created.status_code == 201
        payload = created.json()
        assert payload["has_api_key"] is True
        assert payload["models"][0]["input_modalities"] == ["text", "image"]
        assert payload["models"][0]["supports_vision"] is True
        assert payload["models"][0]["capabilities_known"] is True
        assert "api_key" not in payload and "apiKey" not in payload
        assert "sk-sensitive" not in created.text

        provider_id = payload["id"]
        updated = client.patch(
            f"/api/providers/{provider_id}",
            json={
                "enabled": False,
                "models": [
                    {
                        "id": "jev-model",
                        "model_id": "jev-model",
                        "label": "JEV",
                        "enabled": True,
                    }
                ],
            },
        )
        assert updated.status_code == 200
        assert updated.json()["enabled"] is False
        assert updated.json()["has_api_key"] is True
        # Ordinary provider edits do not erase catalog capabilities when the
        # client does not resend those optional fields.
        assert updated.json()["models"][0]["supports_vision"] is True
        catalog = client.get("/api/models?include_disabled=true").json()
        assert catalog[0]["input_modalities"] == ["text", "image"]
        assert catalog[0]["supports_vision"] is True

        assert client.delete(f"/api/providers/{provider_id}").status_code == 204
        assert client.get(f"/api/providers/{provider_id}").status_code == 404


def test_invalid_openrouter_key_is_not_saved(tmp_path, monkeypatch):
    app = create_app(settings(tmp_path))

    async def reject_key(_client):
        raise ProviderConnectionError(
            "A chave do OpenRouter foi recusada. Gere uma nova chave."
        )

    monkeypatch.setattr(
        "autojev.adapters.openai_compatible.OpenAICompatibleClient.validate_credentials",
        reject_key,
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/providers",
            json={
                "name": "OpenRouter",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": "sk-or-invalid",
            },
        )

        assert response.status_code == 422
        assert "chave do OpenRouter foi recusada" in response.json()["detail"]
        assert client.get("/api/providers").json() == []


def test_model_discovery_refreshes_existing_capabilities(tmp_path, monkeypatch):
    app = create_app(settings(tmp_path))

    async def catalog(_client):
        return [
            {
                "id": "existing-model",
                "label": "Catalog label",
                "input_modalities": ["text", "image"],
                "supports_vision": True,
                "capabilities_known": True,
            },
            {
                "id": "text-model",
                "label": "Text model",
                "input_modalities": ["text"],
                "supports_vision": False,
                "capabilities_known": True,
            },
        ]

    monkeypatch.setattr(
        "autojev.adapters.openai_compatible.OpenAICompatibleClient.list_models",
        catalog,
    )
    with TestClient(app) as client:
        provider = client.post(
            "/api/providers",
            json={
                "name": "Compatible provider",
                "base_url": "http://localhost:4000",
                "models": [
                    {
                        "id": "existing-model",
                        "label": "My custom label",
                        "enabled": False,
                    }
                ],
            },
        ).json()

        response = client.post(
            f"/api/providers/{provider['id']}/discover-models"
        )

        assert response.status_code == 200
        models = {model["id"]: model for model in response.json()["models"]}
        assert models["existing-model"] == {
            "id": "existing-model",
            "label": "My custom label",
            "enabled": False,
            "input_modalities": ["text", "image"],
            "output_modalities": [],
            "supports_vision": True,
            "capabilities_known": True,
        }
        assert models["text-model"]["supports_vision"] is False
        assert models["text-model"]["capabilities_known"] is True


def test_visual_exploration_rejects_auxiliary_model_without_image_support(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        provider = client.post(
            "/api/providers",
            json={
                "name": "Provider",
                "base_url": "http://localhost:4000",
                "models": [{"id": "text-model", "input_modalities": ["text"]}],
            },
        ).json()
        submitted: list[str] = []
        app.state.exploration_manager.submit = submitted.append

        response = client.post(
            "/api/explorations",
            json={
                "app_url": "http://localhost:3000",
                "agent": {
                    "provider_id": provider["id"],
                    "model_id": "text-model",
                },
                "auxiliary_llm": {
                    "provider_id": provider["id"],
                    "model_id": "text-model",
                },
                "options": {"auxiliary_evidence_mode": "visual"},
            },
        )

        assert response.status_code == 422
        assert "does not declare image input support" in response.json()["detail"]
        assert submitted == []


def test_provider_edits_preserve_image_only_output_and_unknown_capabilities(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        provider = client.post(
            "/api/providers",
            json={
                "name": "Provider",
                "base_url": "http://localhost:4000",
                "models": [
                    {
                        "id": "image-generator",
                        "input_modalities": ["text", "image"],
                        "output_modalities": ["image"],
                    },
                    {"id": "unknown-model"},
                ],
            },
        ).json()
        response = client.patch(
            f"/api/providers/{provider['id']}",
            json={
                "models": [
                    {
                        "id": "image-generator",
                        "input_modalities": ["text", "image"],
                        "supports_vision": False,
                    },
                    {
                        "id": "unknown-model",
                        "input_modalities": [],
                        "output_modalities": [],
                        "supports_vision": False,
                        "capabilities_known": False,
                    },
                ]
            },
        )
        assert response.status_code == 200
        models = {model["id"]: model for model in response.json()["models"]}
        assert models["image-generator"]["output_modalities"] == ["image"]
        assert models["image-generator"]["supports_vision"] is False
        assert models["unknown-model"]["capabilities_known"] is False

        app.state.exploration_manager.submit = lambda _: None
        rejected = client.post(
            "/api/explorations",
            json={
                "app_url": "http://localhost:3000",
                "agent": {"provider_id": provider["id"], "model_id": "unknown-model"},
                "auxiliary_llm": {
                    "provider_id": provider["id"],
                    "model_id": "image-generator",
                },
                "options": {"auxiliary_evidence_mode": "visual"},
            },
        )
        assert rejected.status_code == 422


def test_exploration_request_does_not_echo_credentials(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        provider = client.post(
            "/api/providers",
            json={
                "name": "Provider",
                "base_url": "http://localhost:4000",
                "models": [
                    {"id": "model", "input_modalities": ["text", "image"]}
                ],
            },
        ).json()
        # Keep this contract test deterministic; the engine itself is tested separately.
        app.state.exploration_manager.submit = lambda exploration_id: None
        response = client.post(
            "/api/explorations",
            json={
                "app_url": "http://localhost:3000",
                "agent": {"provider_id": provider["id"], "model_id": "model"},
                "auxiliary_llm": {
                    "provider_id": provider["id"],
                    "model_id": "model",
                },
                "options": {"max_steps": 3, "max_duration_seconds": 30},
                "credentials": {"password": "never-return-this"},
            },
        )
        assert response.status_code == 202
        assert "never-return-this" not in response.text
        details = client.get(response.json()["detail_url"])
        assert details.status_code == 200
        assert "never-return-this" not in details.text
        history = client.get("/api/explorations").json()
        assert history[0]["counters"] == {
            "actions": 0,
            "issues": 0,
            "scenarios": 0,
            "pages_visited": 1,
        }
        assert "actions" not in history[0]


def test_exploration_accepts_optional_focused_scope_alias(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        provider = client.post(
            "/api/providers",
            json={
                "name": "Provider",
                "base_url": "http://localhost:4000",
                "models": [
                    {"id": "model", "input_modalities": ["text", "image"]}
                ],
            },
        ).json()
        app.state.exploration_manager.submit = lambda exploration_id: None
        response = client.post(
            "/api/explorations",
            json={
                "app_url": "http://localhost:3000",
                "agent": {"provider_id": provider["id"], "model_id": "model"},
                "auxiliary_llm": {
                    "provider_id": provider["id"],
                    "model_id": "model",
                },
                "options": {
                    "max_steps": 3,
                    "max_duration_seconds": 30,
                    "focusArea": "  Financeiro   / Faturas  ",
                    "auxiliaryEvidenceMode": "visual",
                },
            },
        )

        assert response.status_code == 202
        assert response.json()["options"]["focus_area"] == "Financeiro / Faturas"
        assert response.json()["options"]["auxiliary_evidence_mode"] == "visual"


def test_exploration_defaults_to_coverage_driven_without_fixed_limits(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        provider = client.post(
            "/api/providers",
            json={
                "name": "Provider",
                "base_url": "http://localhost:4000",
                "models": [{"id": "model"}],
            },
        ).json()
        app.state.exploration_manager.submit = lambda exploration_id: None

        response = client.post(
            "/api/explorations",
            json={
                "app_url": "http://localhost:3000",
                "agent": {"provider_id": provider["id"], "model_id": "model"},
                "auxiliary_llm": {
                    "provider_id": provider["id"],
                    "model_id": "model",
                },
            },
        )

        assert response.status_code == 202
        payload = response.json()
        assert payload["options"]["max_steps"] is None
        assert payload["options"]["max_duration_seconds"] is None
        assert payload["options"]["auxiliary_evidence_mode"] == "text"
        assert payload["progress"] is None
        assert payload["browser_progress"] == {
            "current": 0,
            "total": None,
            "percentage": None,
        }
        assert payload["overall_progress"] == 0
        assert payload["current_step"] == "Etapa 0 · cobrindo até esgotar"


def test_exploration_rejects_invalid_provider_key_before_browser_login(
    tmp_path, monkeypatch
):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        provider = client.post(
            "/api/providers",
            json={
                "name": "Provider",
                "base_url": "https://provider.example/v1",
                "api_key": "sk-or-invalid",
                "models": [{"id": "typesafe/jev-1.13"}],
            },
        ).json()

        async def reject_key(_client):
            raise ProviderConnectionError(
                "A chave do OpenRouter foi recusada. Gere uma nova chave."
            )

        monkeypatch.setattr(
            "autojev.adapters.openai_compatible.OpenAICompatibleClient.validate_credentials",
            reject_key,
        )
        submitted: list[str] = []
        app.state.exploration_manager.submit = submitted.append

        response = client.post(
            "/api/explorations",
            json={
                "app_url": "https://example.test",
                "agent": {
                    "provider_id": provider["id"],
                    "model_id": "typesafe/jev-1.13",
                },
                "auxiliary_llm": {
                    "provider_id": provider["id"],
                    "model_id": "typesafe/jev-1.13",
                },
            },
        )

        assert response.status_code == 422
        assert "chave do OpenRouter foi recusada" in response.json()["detail"]
        assert submitted == []
        assert client.get("/api/explorations").json() == []


def test_validation_errors_never_echo_secrets(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        response = client.post(
            "/api/explorations",
            json={
                "app_url": "http://localhost:3000",
                "agent": {"provider_id": "missing", "model_id": "model"},
                "auxiliary_llm": {"provider_id": "missing", "model_id": "model"},
                "credentials": {" ": "must-never-be-echoed"},
            },
        )
        assert response.status_code == 422
        assert "must-never-be-echoed" not in response.text
        assert "input" not in response.text


def test_phase_payload_separates_browser_actions_from_scenario_documentation():
    result = {
        "status": "postprocessing",
        "steps_completed": 100,
        "options": {"max_steps": 100},
        "scenarios": [{"id": f"CT{index:03d}"} for index in range(1, 68)],
    }
    events = [
        {
            "type": "scenario_enriched",
            "message": "CT021 aprimorado",
            "payload": {
                "current": 21,
                "total": 67,
                "enriched": 21,
                "local_evidence": 46,
                "stage": "enrichment",
            },
        }
    ]

    payload = _phase_payload(result, events)

    assert payload["phase"] == "documenting"
    assert payload["browser_progress"] == {
        "current": 100,
        "total": 100,
        "percentage": 100,
    }
    assert payload["phase_progress"] == {
        "current": 21,
        "total": 67,
        "percentage": 31,
    }
    assert payload["overall_progress"] == 78
    assert payload["postprocessing"]["enriched"] == 21


def test_phase_payload_keeps_exhaustive_exploration_progress_indeterminate():
    payload = _phase_payload(
        {
            "status": "running",
            "steps_completed": 137,
            "options": {"max_steps": None},
        },
        [],
    )

    assert payload["phase"] == "exploring"
    assert payload["browser_progress"] == {
        "current": 137,
        "total": None,
        "percentage": None,
    }
    assert payload["overall_progress"] is None


def test_phase_payload_exposes_completed_with_restrictions():
    payload = _phase_payload(
        {
            "status": "completed",
            "steps_completed": 90,
            "options": {"max_steps": None},
            "scenarios": [],
        },
        [
            {
                "type": "coverage_complete_with_restrictions",
                "message": "Reachable coverage exhausted with restrictions",
                "payload": {
                    "candidates_blocked": 7,
                    "restorations_abandoned": 2,
                },
            }
        ],
    )

    assert payload["phase_label"] == "Exploração concluída com restrições"
    assert "controles" in payload["phase_description"]
    assert payload["completion"] == {
        "type": "coverage_complete_with_restrictions",
        "reason": None,
        "message": "Reachable coverage exhausted with restrictions",
        "known_states": None,
        "frontier_states": None,
        "frontier_candidates": None,
        "blocked_candidates": 7,
        "restorations_abandoned": 2,
        "restrictions": [],
        "unreachable_restorations": [],
    }


def test_phase_payload_uses_history_completion_summary_without_full_event_list():
    payload = _phase_payload(
        {
            "status": "completed",
            "steps_completed": 12,
            "options": {"max_steps": None},
            "completion_event": {
                "type": "coverage_complete_with_restrictions",
                "message": "Coverage completed with restrictions",
                "payload": {"candidates_blocked": 4},
            },
        },
        [],
    )

    assert payload["phase_label"] == "Exploração concluída com restrições"
    assert payload["completion"]["type"] == "coverage_complete_with_restrictions"
    assert payload["completion"]["blocked_candidates"] == 4


def test_cancelled_history_does_not_reuse_an_earlier_coverage_completion():
    stale = {
        "type": "coverage_complete_with_restrictions",
        "message": "Coverage was exhausted before post-processing",
        "payload": {"candidates_blocked": 4},
    }

    payload = _phase_payload(
        {
            "status": "cancelled",
            "steps_completed": 12,
            "options": {"max_steps": None},
            "completion_event": stale,
            "scenarios": [],
        },
        [],
    )

    assert payload["phase_label"] == "Exploração cancelada"
    assert payload["completion"]["type"] is None


def test_browser_closed_is_exposed_as_the_cancellation_reason():
    payload = _phase_payload(
        {
            "status": "cancelled",
            "steps_completed": 12,
            "options": {"max_steps": None},
            "scenarios": [],
        },
        [
            {
                "type": "browser_closed",
                "message": "Browser page is not active",
                "payload": {"reason": "browser_closed"},
            }
        ],
    )

    assert payload["phase_label"] == "Exploração cancelada"
    assert payload["completion"]["type"] == "browser_closed"
    assert payload["completion"]["reason"] == "browser_closed"


def test_queue_counters_survive_legacy_draft_events_and_explicit_zero():
    result = {"status": "running", "steps_completed": 2, "options": {}, "scenarios": []}
    measured = {"type": "scenario_enrichment_waiting_provider", "payload": {
        "current": 1, "total": 7, "enriched": 1, "pending": 6,
        "deferred": 2, "provider_requests": 3,
    }}
    draft = {"type": "scenario_draft_created", "payload": {"current": 1, "total": 8}}
    queue = _phase_payload(result, [measured, draft])["postprocessing"]
    assert queue["pending"] == 6
    assert queue["deferred"] == 2
    assert queue["requests"] == 3
    done = {"type": "scenario_postprocessing_completed", "payload": {
        "pending": 0, "deferred": 0, "provider_requests": 5,
    }}
    queue = _phase_payload(result, [measured, draft, done])["postprocessing"]
    assert queue["pending"] == queue["deferred"] == 0
    assert queue["requests"] == 5


def test_cancelled_run_keeps_individual_restrictions_without_complete_event():
    restriction = {"id": "r1", "reason": "no_safe_restore_route", "name": "Cancel"}
    payload = _phase_payload(
        {"status": "cancelled", "steps_completed": 2, "options": {}},
        [
            {"type": "coverage_restriction_recorded", "payload": restriction},
            {"type": "coverage_restriction_recorded", "payload": restriction},
            {"type": "cancelled", "payload": {}},
        ],
    )
    assert payload["completion"]["restrictions"] == [restriction]


def test_phase_payload_resolved_ledger_matches_active_count_without_rewriting_terminal_status():
    recovered = {"id": "r1", "name": "Continue Shopping", "status": "resolved", "reason": "no_safe_restore_route",
                 "resolution": {"source": "actual_semantic_move_execution", "action_step": 47,
                     "occurred_at": "2026-10-04T12:00:00+00:00", "source_url": "https://shop.test/cart",
                     "action": "click", "target": "e4", "screenshot": "047.png", "success": True,
                     "assertions_passed": False}}
    block = {"id": "r1", "name": "Continue Shopping", "reason": "no_safe_restore_route"}
    masked = {"id": "r2", "name": "Continue", "reason": "required_field_validation_masked"}
    payload = _phase_payload({"status": "completed", "steps_completed": 48, "options": {}}, [
        {"type": "coverage_restriction_recorded", "payload": block},
        {"type": "coverage_restriction_resolved", "payload": recovered},
        {"type": "coverage_complete_with_restrictions", "payload": {"restrictions": [block, masked], "candidates_blocked": 2}},
    ])
    completion = payload["completion"]
    assert completion["restrictions"] == [masked]
    assert completion["blocked_candidates"] == 1
    assert completion["resolved_restrictions"] == [recovered]
    assert completion["resolved_restriction_count"] == 1
    assert completion["type"] == "coverage_complete_with_restrictions"


def test_phase_payload_explicit_resolution_snapshot_survives_history_without_event_list():
    item = {"id": "r1", "status": "resolved", "resolution": {
        "source": "actual_semantic_move_execution", "action_step": 3,
        "occurred_at": "2026-10-04T12:00:00+00:00", "source_url": "https://shop.test/cart",
        "action": "click", "success": True, "assertions_passed": True,
    }}
    payload = _phase_payload({"status": "completed", "options": {}, "completion_event": {
        "type": "coverage_complete", "payload": {"restrictions": [], "resolved_restrictions": [item]},
    }}, [])
    assert payload["completion"]["restrictions"] == []
    assert payload["completion"]["blocked_candidates"] == 0
    assert payload["completion"]["resolved_restrictions"] == [item]
