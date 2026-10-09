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


def _selection(**changes):
    return {
        "version": "select-labels-v1", "requested_label": "Name (Z to A)",
        "before_label": "Name (A to Z)", "after_label": "Name (Z to A)",
        "status": "confirmed", **changes,
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


def test_dropdown_preview_displays_recorded_requested_previous_and_actual_option_labels(report):
    selection = _selection()
    document, parsed = _timeline(report, _before(), _action(action_preview=_metadata(selection=selection)))
    assert parsed.previews[0]["selection"] == selection
    assert '<div class="action-selection-summary" data-selection-status="confirmed">' in document
    assert '<span>Opção solicitada</span><strong>Name (Z to A)</strong>' in document
    assert '<span>Antes da ação</span><strong>Name (A to Z)</strong>' in document
    assert '<span>Selecionada depois</span><strong>Name (Z to A)</strong>' in document
    assert '<p class="action-selection-status">Seleção confirmada</p>' in document
    assert document.index('class="action-selection-summary"') < document.index('class="action-preview-frame"')
    # The original before capture remains the only embedded image.
    assert document.count("data:image/png;base64,") == 1


@pytest.mark.parametrize("status, expected", [
    ("confirmed", "Seleção confirmada"),
    ("mismatch", "Seleção diferente da solicitada"),
    ("unverified", "Seleção não confirmada"),
])
def test_dropdown_preview_communicates_the_recorded_selection_status(report, status, expected):
    selection = _selection(status=status, after_label="Name (A to Z)" if status == "mismatch" else "Name (Z to A)")
    document, parsed = _timeline(report, _action(action_preview=_metadata(selection=selection)))
    assert parsed.previews[0]["selection"]["status"] == status
    assert f'<p class="action-selection-status">{expected}</p>' in document
    assert 'data-selection-status="' + status + '"' in document


def test_dropdown_preview_trusts_value_based_mismatch_even_if_option_labels_are_identical(report):
    selection = _selection(status="mismatch", requested_label="Mesmo nome", after_label="Mesmo nome")
    document, parsed = _timeline(report, _action(action_preview=_metadata(selection=selection)))
    assert parsed.previews[0]["selection"]["status"] == "mismatch"
    assert "Seleção diferente da solicitada" in document


@pytest.mark.parametrize("status", ["confirmed", "mismatch"])
@pytest.mark.parametrize("missing", ["requested_label", "after_label"])
def test_dropdown_preview_does_not_claim_verification_when_a_comparison_label_is_missing(report, status, missing):
    selection = _selection(status=status)
    selection.pop(missing)
    document, parsed = _timeline(report, _action(action_preview=_metadata(selection=selection)))
    assert parsed.previews[0]["selection"]["status"] == "unverified"
    assert missing not in parsed.previews[0]["selection"]
    assert "Seleção não confirmada" in document
    assert "Seleção confirmada" not in document
    assert "Seleção diferente da solicitada" not in document


def test_dropdown_preview_can_confirm_without_a_previous_label(report):
    selection = _selection()
    selection.pop("before_label")
    document, parsed = _timeline(report, _action(action_preview=_metadata(selection=selection)))
    assert parsed.previews[0]["selection"]["status"] == "confirmed"
    assert '<span>Antes da ação</span>' not in document
    assert "Seleção confirmada" in document


@pytest.mark.parametrize("action", ["click", "fill", "press", "hover"])
def test_non_dropdown_actions_ignore_selection_metadata(report, action):
    document, parsed = _timeline(report, _action(action_preview=_metadata(action=action, selection=_selection())))
    assert "selection" not in parsed.previews[0]
    assert "action-selection-summary" not in document
    assert "Name (Z to A)" not in document


@pytest.mark.parametrize("selection", [
    None,
    "untrusted-selection",
    [],
    {},
    _selection(version="unknown-version"),
    _selection(status="approved"),
    _selection(status=["confirmed"]),
    _selection(requested_label=None, before_label=None, after_label=None),
    _selection(requested_label="", before_label="   ", after_label="\n"),
])
def test_invalid_dropdown_metadata_preserves_the_control_marker_without_claiming_option_labels(report, selection):
    document, parsed = _timeline(report, _action(action_preview=_metadata(selection=selection)))
    assert parsed.previews[0]["box"]["x"] == 1100.5
    assert "selection" not in parsed.previews[0]
    assert "action-selection-summary" not in document


@pytest.mark.parametrize("invalid_label", [True, 42, [], {}, " ", "a" * 241, "Name\n(A to Z)", "Name\x7f(A to Z)", "Name\x85(A to Z)"])
def test_dropdown_labels_must_be_bounded_nonblank_strings_without_control_characters(report, invalid_label):
    selection = _selection(requested_label=invalid_label)
    document, parsed = _timeline(report, _action(action_preview=_metadata(selection=selection)))
    assert "requested_label" not in parsed.previews[0]["selection"]
    assert parsed.previews[0]["selection"]["status"] == "unverified"
    assert '<span>Opção solicitada</span>' not in document
    assert "Seleção não confirmada" in document


def test_dropdown_labels_are_escaped_and_arbitrary_values_never_enter_the_report(report):
    malicious_label = '\"><script>unsafe-option-label</script><img src=x onerror=alert(1)>'
    selection = _selection(
        requested_label=malicious_label,
        after_label=malicious_label,
        before_label="  Name (A to Z)  ",
        requested_value="never-expose-raw-value",
        after_value="never-expose-another-value",
        html="<script>arbitrary-dom-content</script>",
        password="never-expose-password",
    )
    document, parsed = _timeline(report, _action(action_preview=_metadata(selection=selection)))
    assert parsed.previews[0]["selection"] == {
        "version": "select-labels-v1", "requested_label": malicious_label,
        "before_label": "Name (A to Z)", "after_label": malicious_label,
        "status": "confirmed",
    }
    assert '<script>unsafe-option-label</script>' not in document
    assert '<img src=x onerror=alert(1)>' not in document
    assert "&lt;script&gt;unsafe-option-label&lt;/script&gt;" in document
    for private_value in ("never-expose-raw-value", "never-expose-another-value", "arbitrary-dom-content", "never-expose-password"):
        assert private_value not in document


def test_historical_dropdown_does_not_infer_selection_from_its_title_or_description(report):
    document, parsed = _timeline(report, _before(), _action(
        action_preview=None,
        title="Selecionar opção: Name (Z to A)",
        description="A opção Name (Z to A) deveria ficar selecionada.",
    ))
    assert parsed.previews == [{}]
    assert "action-selection-summary" not in document
    assert "Seleção confirmada" not in document
