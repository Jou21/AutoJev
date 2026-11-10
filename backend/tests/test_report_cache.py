from copy import deepcopy

import pytest

from autojev.services.report_cache import ReportCache


def test_cache_reuses_only_fresh_exact_scope(tmp_path):
    cache = ReportCache()
    capture = tmp_path / "capture.png"
    capture.write_bytes(b"first")
    destination = tmp_path / "download.html"

    class Renderer:
        calls = 0
        capture_dependencies = {}

        def generate(self, snapshot, path):
            self.calls += 1
            self.capture_dependencies = {capture: cache.signature(capture)}
            destination.write_text(str(snapshot))

    renderer = Renderer()
    snapshot = {"id": "root", "suite_context": {"scope": "initial", "generated_at": "one"}, "scenarios": [{"id": "CT001"}], "portable_report": {"version": 1}}
    def render():
        cache.render(renderer, snapshot, destination, tmp_path)
    render()
    original = destination.read_bytes()
    snapshot["suite_context"]["generated_at"] = "two"
    render()
    assert renderer.calls == 1
    assert destination.read_bytes() == original
    for change in (
        lambda: snapshot["suite_context"].update(scope="round", round_name="one"),
        lambda: snapshot["suite_context"].update(focused_case_id="CT001"),
        lambda: snapshot["scenarios"][0].update(status="FAIL"),
        lambda: snapshot["portable_report"].update(version=2),
        lambda: capture.write_bytes(b"new evidence"),
        lambda: capture.unlink(),
        lambda: capture.write_bytes(b"arrived later"),
        lambda: cache.path.unlink(),
    ):
        before = renderer.calls
        change()
        render()
        assert renderer.calls == before + 1


def test_failed_generation_does_not_publish_cache(tmp_path):
    cache = ReportCache()
    destination = tmp_path / "download.html"

    class Renderer:
        capture_dependencies = {}
        fail = True
        calls = 0
        def generate(self, snapshot, path):
            self.calls += 1
            destination.write_text("partial" if self.fail else "complete")
            if self.fail:
                raise ValueError("generation failed")

    renderer = Renderer()
    snapshot = {"id": "root"}
    before = deepcopy(snapshot)
    with pytest.raises(ValueError):
        cache.render(renderer, snapshot, destination, tmp_path)
    assert cache.key is None
    renderer.fail = False
    cache.render(renderer, snapshot, destination, tmp_path)
    assert renderer.calls == 2
    assert destination.read_text() == "complete"
    assert snapshot == before


def test_evidence_changed_during_render_is_not_reused(tmp_path):
    cache = ReportCache()
    capture = tmp_path / "capture.png"
    capture.write_bytes(b"before")
    destination = tmp_path / "download.html"

    class Renderer:
        calls = 0
        def generate(self, snapshot, path):
            self.calls += 1
            self.capture_dependencies = {capture: cache.signature(capture)}
            destination.write_text("report")
            capture.write_bytes(str(self.calls).encode())

    renderer = Renderer()
    for _ in range(2):
        cache.render(renderer, {"id": "root"}, destination, tmp_path)
    assert renderer.calls == 2
