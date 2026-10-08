from copy import deepcopy
from html.parser import HTMLParser
import json

import pytest

from autojev.adapters.report import StandaloneHtmlReport


class _PreviewParser(HTMLParser):
    def __init__(self, document):
        super().__init__()
        self.previews = []
        self.images = []
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "figure" and "action-preview" in attributes.get("class", "").split():
            self.previews.append(json.loads(attributes["data-action-preview"]))
        if tag == "img":
            self.images.append(attributes)


@pytest.fixture
def report(tmp_path):
    screenshots = tmp_path / "run" / "screenshots"
    screenshots.mkdir(parents=True)
    (screenshots / "before.png").write_bytes(b"\x89PNG\r\n\x1a\nbefore")
    (screenshots / "after.png").write_bytes(b"\x89PNG\r\n\x1a\nafter")
    return StandaloneHtmlReport(tmp_path)


def _metadata(**changes):
    return {
        "version": "action-target-v1", "coordinate_space": "screenshot-css-pixels",
        "action": "select", "target": "e2", "screenshot": "before.png",
        "captured_at": "2026-10-08T12:00:00.100Z", "url": "https://shop.test/products",
        "image": {"width": 1440, "height": 900},
        "box": {"x": 1100.5, "y": 80.5, "width": 190, "height": 32},
        **changes,
    }


def _before(**changes):
    return {
        "kind": "observation", "screenshot": "before.png",
        "occurred_at": "2026-10-08T12:00:00Z", "url": "https://shop.test/products",
        "title": "Antes da ação", **changes,
    }


def _action(**changes):
    return {
        "kind": "action", "occurred_at": "2026-10-08T12:00:01Z",
        "url": "https://shop.test/products", "target": "e2", "title": "Selecionar opção",
        "action_preview": _metadata(), **changes,
    }


def _timeline(report, *entries):
    scenario = {"id": "CT001", "evidence": {"timeline": list(entries)}}
    before = deepcopy(scenario)
    document = report._timeline("run", scenario)
    assert scenario == before
    return document, _PreviewParser(document)


def test_action_preview_references_original_before_capture_without_duplicating_base64(report):
    document, parsed = _timeline(report, _before(), _action())
    assert len(parsed.previews) == 1
    assert parsed.previews[0] == {
        "version": "action-target-v1", "coordinate_space": "screenshot-css-pixels",
        "action": "select", "target": "e2", "image": {"width": 1440, "height": 900},
        "box": {"x": 1100.5, "y": 80.5, "width": 190, "height": 32},
    }
    preview_image = next(image for image in parsed.images if image.get("class") == "action-preview-image")
    assert preview_image["data-source-shot"] == "before.png"
    assert "src" not in preview_image
    assert document.count("data:image/png;base64,") == 1
    assert "Alvo da ação" in document
    assert "Captura anterior à interação" in document
    assert '<figcaption>before.png</figcaption>' in document


def test_action_preview_embeds_capture_once_when_no_other_timeline_entry_contains_it(report):
    document, parsed = _timeline(report, _action())
    assert len(parsed.previews) == 1
    assert parsed.images[0]["src"].startswith("data:image/png;base64,")
    assert document.count("data:image/png;base64,") == 1


def test_action_owned_capture_is_not_reintroduced_as_an_untimed_observation(report):
    scenario = {
        "id": "CT001", "screenshots": ["before.png"],
        "evidence": {"timeline": [_action()]},
    }
    original = deepcopy(scenario)
    document = report._timeline("run", scenario)
    assert document.count('class="timeline-entry action"') == 1
    assert 'class="timeline-entry observation"' not in document
    assert "Captura preservada" not in document
    assert document.count("data:image/png;base64,") == 1
    assert len(_PreviewParser(document).previews) == 1
    assert scenario == original


def test_unrelated_preview_does_not_hide_a_preserved_legacy_capture(report):
    scenario = {
        "id": "CT001", "screenshots": ["before.png"],
        "evidence": {"timeline": [_action(action_preview=_metadata(url="https://shop.test/cart"))]},
    }
    document = report._timeline("run", scenario)
    assert "Captura preservada" in document
    assert document.count("data:image/png;base64,") == 1
    assert _PreviewParser(document).previews == []


def test_historical_action_shows_only_its_reliable_before_capture_without_inventing_coordinates(report):
    document, parsed = _timeline(report, _before(screenshot="screenshots/before.png"), _action(action_preview=None))
    assert parsed.previews == [{}]
    assert "Posição do alvo não registrada nesta execução." in document
    assert "data-source-shot=\"before.png\"" in document
    assert document.count("data:image/png;base64,") == 1


@pytest.mark.parametrize("before_changes", [
    {"url": "https://shop.test/cart"},
    {"url": None},
    {"occurred_at": None},
    {"occurred_at": "not-a-date"},
    {"occurred_at": "2026-10-08T12:00:02Z"},
    {"kind": "signal"},
])
def test_historical_action_does_not_use_untrusted_earlier_capture(report, before_changes):
    _, parsed = _timeline(report, _before(**before_changes), _action(action_preview=None))
    assert parsed.previews == []


def test_historical_action_never_uses_capture_after_action_or_across_an_intervening_action(report):
    _, after_only = _timeline(report, _action(action_preview=None), _before())
    assert after_only.previews == []
    _, stale_before = _timeline(
        report, _before(), _action(action_preview=None),
        _action(action_preview=None, occurred_at="2026-10-08T12:00:02Z"),
    )
    assert len(stale_before.previews) == 1


@pytest.mark.parametrize("metadata_changes", [
    {"coordinate_space": "viewport-device-pixels"},
    {"version": "untrusted-version"},
    {"action": "<script>alert(1)</script>"},
    {"action": ["click"]},
    {"target": "another-ref"},
    {"target": ""},
    {"target": True},
    {"target": "bad\nref"},
    {"image": {"width": True, "height": 900}},
    {"image": {"width": float("nan"), "height": 900}},
    {"image": {"width": float("inf"), "height": 900}},
    {"image": {"width": 10 ** 400, "height": 900}},
    {"image": {"width": 1440, "height": 0}},
    {"box": {"x": -1, "y": 10, "width": 20, "height": 20}},
    {"box": {"x": 1400, "y": 10, "width": 100, "height": 20}},
    {"box": {"x": 10, "y": 899, "width": 20, "height": 20}},
    {"box": {"x": 10, "y": 10, "width": 0, "height": 20}},
    {"box": {"x": 10, "y": 10, "width": 20, "height": False}},
    {"box": {"x": 10, "y": 10, "width": 20}},
])
def test_invalid_metadata_preserves_safe_before_capture_but_never_draws_a_marker(report, metadata_changes):
    _, parsed = _timeline(report, _before(), _action(action_preview=_metadata(**metadata_changes)))
    assert parsed.previews == [{}]


def test_mismatched_preview_url_uses_matching_historical_capture_not_unrelated_metadata(report):
    document, parsed = _timeline(report, _before(), _action(action_preview=_metadata(
        screenshot="after.png", url="https://shop.test/another-page",
    )))
    assert parsed.previews == [{}]
    assert "data-source-shot=\"before.png\"" in document
    assert "data-source-shot=\"after.png\"" not in document


def test_preview_drops_arbitrary_metadata_and_escapes_target_without_html_injection(report):
    target = 'e2\"<img src=x onerror=alert(1)>'
    document, parsed = _timeline(report, _action(target=target, action_preview=_metadata(
        target=target, password="never-expose-this", value="credential-secret",
        html="<script>injected-script</script>", image={"width": 1440, "height": 900, "secret": "dimension-secret"},
    )))
    assert parsed.previews[0]["target"] == target
    for secret in ("never-expose-this", "credential-secret", "injected-script", "dimension-secret"):
        assert secret not in document
    assert '<img src=x onerror=alert(1)>' not in document
    assert "&lt;img" in document


@pytest.mark.parametrize("screenshot", ["missing.png", "../before.png", "https://evil.test/before.png", "x\\before.png"])
def test_missing_or_unsafe_explicit_capture_cannot_claim_a_target_preview(report, screenshot):
    _, parsed = _timeline(report, _action(action_preview=_metadata(screenshot=screenshot)))
    assert parsed.previews == []


def test_no_geometry_is_inferred_from_screenshot_names_in_another_case(report):
    first = report._timeline("run", {"id": "CT001", "evidence": {"timeline": [_before()]}})
    second = report._timeline("run", {"id": "CT002", "evidence": {"timeline": [_action(action_preview=None)]}})
    assert "before.png" in first
    assert "action-preview" not in second


def test_generated_offline_report_keeps_action_preview_and_does_not_duplicate_capture(report, tmp_path):
    run = {
        "id": "run", "status": "completed", "app_url": "https://shop.test/products",
        "scenarios": [{
            "id": "CT001", "status": "PASS", "title": "Selecionar opção",
            "url": "https://shop.test/products", "evidence": {"timeline": [_before(), _action()]},
        }],
    }
    before = deepcopy(run)
    output = tmp_path / "report.html"
    report.generate(run, str(output))
    document = output.read_text()
    parsed = _PreviewParser(document)
    assert len(parsed.previews) == 1
    assert parsed.previews[0]["box"]["x"] == 1100.5
    assert document.count("data:image/png;base64,") == 1
    assert run == before
