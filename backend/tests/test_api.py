from pathlib import Path

from autojev.adapters.openai_compatible import ProviderConnectionError
from autojev.api.routes import _phase_payload
from autojev.config import Settings
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
