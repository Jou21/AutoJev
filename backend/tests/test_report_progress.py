from uuid import uuid4

import pytest

from autojev.adapters.report import StandaloneHtmlReport
from autojev.services.report_cache import ReportCache
from autojev.services.report_progress import ExportCancelled, Preparation
from test_scenario_rerun_api import runtime


def test_render_progress_counts_completed_cases_and_cache(tmp_path):
    progress = Preparation(("root", "initial"))
    states = []
    original = progress.update

    def update(*args):
        original(*args)
        states.append(progress.snapshot())

    progress.update = update
    renderer = StandaloneHtmlReport(tmp_path, progress=progress)
    snapshot = {"id": "root", "scenarios": [{"id": f"CT{n}", "title": str(n), "status": "PASS"} for n in range(3)]}
    cache = ReportCache()
    destination = tmp_path / "report.html"
    cache.render(renderer, snapshot, destination, tmp_path, progress)
    counts = [state["completed"] for state in states if state["stage"] == "rendering"]
    assert counts == [0, 1, 1, 2, 2, 3]
    assert states[-1] == {"stage": "ready", "completed": 1, "total": 1}
    states.clear()
    cache.render(renderer, snapshot, destination, tmp_path, progress)
    assert [state["stage"] for state in states] == ["cache", "ready"]
    progress.cancelled.set()
    with pytest.raises(ExportCancelled):
        cache.render(renderer, snapshot, destination, tmp_path, progress)


def test_cancel_during_case_does_not_publish_cache(tmp_path):
    progress = Preparation(("root",))
    cache = ReportCache()
    renderer = StandaloneHtmlReport(tmp_path, progress=progress)
    original = renderer._scenario_card

    def card(*args):
        result = original(*args)
        progress.cancelled.set()
        return result

    renderer._scenario_card = card
    with pytest.raises(ExportCancelled):
        cache.render(renderer, {"id": "root", "scenarios": [{"id": "CT1", "title": "One", "status": "PASS"}]}, tmp_path / "report.html", tmp_path, progress)
    assert cache.key is None


def test_scoped_route_progress_and_unknown_cancel(runtime):
    app, client, source = runtime
    identity = str(uuid4())
    path = f"/api/test-suites/{source}/report?scope=initial&export_id={identity}"
    response = client.get(path)
    assert response.status_code == 200, response.text
    progress_path = f"/api/test-suites/report-exports/{identity}"
    response = client.get(progress_path)
    assert response.json() == {"stage": "ready", "completed": 1, "total": 1}
    assert response.headers["cache-control"] == "private, no-store"
    assert app.state.test_suite_service.report_progress.entries[identity].scope == (source, "initial", None, None, False)
    assert client.get(path).status_code == 409
    assert client.delete(progress_path).json() == {"cancelled": False}
    assert client.get(f"/api/test-suites/report-exports/{uuid4()}").status_code == 404
    pending_id = str(uuid4())
    pending = app.state.test_suite_service.report_progress.begin(pending_id, (source,))
    assert client.delete(f"/api/test-suites/report-exports/{pending_id}").json() == {"cancelled": True}
    with pytest.raises(ExportCancelled):
        pending.check()


def test_linked_tracked_export_keeps_historical_html_and_cleans_response(runtime):
    app, client, source = runtime
    directory = app.state.settings.artifact_dir / source
    directory.mkdir(parents=True, exist_ok=True)
    original = directory / "report.html"
    original.write_text("<html>historical</html>")
    app.state.repository.update_exploration(source, report_path=str(original))
    before = set(directory.iterdir())
    identity = str(uuid4())
    response = client.get(f"/api/explorations/{source}/report?export_id={identity}")
    assert response.status_code == 200, response.text
    assert original.read_text() == "<html>historical</html>"
    assert set(directory.iterdir()) == before
    assert client.get(f"/api/test-suites/report-exports/{identity}").json()["stage"] == "ready"
