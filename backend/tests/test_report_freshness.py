from pathlib import Path

from fastapi.testclient import TestClient

from autojev.config import Settings
from autojev.domain.models import ExplorationOptions, ExplorationRequest, ModelSelection
from autojev.main import create_app


def test_report_html_is_not_reused_from_http_cache_and_original_evidence_is_preserved(tmp_path):
    app = create_app(Settings(
        data_dir=tmp_path,
        database_path=tmp_path / "db.sqlite3",
        artifact_dir=tmp_path / "artifacts",
        secret_key="test-passphrase",
        cors_origins=("http://localhost:3000",),
        headless=True,
        browser="chromium",
        http_timeout_seconds=1,
    ))
    with TestClient(app) as client:
        repository = app.state.repository
        run_id = repository.create_exploration(ExplorationRequest(
            app_url="https://example.test",
            agent=ModelSelection("local", "agent"),
            auxiliary_llm=ModelSelection("local", "auxiliary"),
            options=ExplorationOptions(),
        ))
        original = tmp_path / "artifacts" / run_id / "report.html"
        original.parent.mkdir(parents=True)
        original.write_text("<html><body>Immutable original evidence</body></html>")
        repository.update_exploration(run_id, status="completed", report_path=str(original))
        before = repository.get_exploration(run_id)
        original_bytes = original.read_bytes()

        first = client.get(f"/api/explorations/{run_id}/report")
        assert first.status_code == 200
        for response in (first, client.get(f"/api/explorations/{run_id}/report", headers={
            "If-None-Match": first.headers.get("etag", '"previous-report"'),
            "If-Modified-Since": first.headers.get("last-modified", "Sat, 17 Oct 2026 00:00:00 GMT"),
        })):
            assert response.status_code == 200
            directives = {part.strip().lower() for part in response.headers["cache-control"].split(",")}
            assert {"private", "no-store"} <= directives
            assert response.headers["pragma"].lower() == "no-cache"
            assert "text/html" in response.headers["content-type"]
            assert 'data-report-layout="premium-v1"' in response.text
            assert response.text == first.text

        assert original.read_bytes() == original_bytes
        assert repository.get_exploration(run_id) == before
        assert repository.get_report_path(run_id) == str(original)
        assert Path(original).is_file()
