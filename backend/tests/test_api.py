from pathlib import Path

from fastapi.testclient import TestClient

from autojev.config import Settings
from autojev.main import create_app


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
                "models": [{"id": "jev-model", "label": "JEV"}],
            },
        )
        assert created.status_code == 201
        payload = created.json()
        assert payload["has_api_key"] is True
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

        assert client.delete(f"/api/providers/{provider_id}").status_code == 204
        assert client.get(f"/api/providers/{provider_id}").status_code == 404


def test_exploration_request_does_not_echo_credentials(tmp_path):
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
                "options": {
                    "max_steps": 3,
                    "max_duration_seconds": 30,
                    "focusArea": "  Financeiro   / Faturas  ",
                },
            },
        )

        assert response.status_code == 202
        assert response.json()["options"]["focus_area"] == "Financeiro / Faturas"


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
