"""Planned attachment positions are presentation only, not rewritten history."""

from copy import deepcopy

from autojev.api.routes import _exploration_payload
from autojev.services.scenario_timeline import TIMELINE_VERSION


def _snapshot(gap=2):
    original = [
        {"id": "before", "kind": "observation", "title": "Antes da ação", "occurred_at": "2026-10-16T15:00:00Z"},
        {"id": "action", "kind": "action", "title": "Selecionar opção", "occurred_at": "2026-10-16T15:00:01Z"},
        {"id": "after", "kind": "observation", "title": "Depois da ação", "occurred_at": "2026-10-16T15:00:02Z"},
        {"id": "selection", "kind": "assertion", "title": "Opção selecionada", "occurred_at": "2026-10-16T15:00:03Z", "assertion": {"kind": "selection_matches", "passed": True}},
        {"id": "sort", "kind": "assertion", "title": "Ordenação dos produtos", "occurred_at": "2026-10-16T15:00:03Z", "assertion": {"kind": "product_sort_order", "passed": True}},
        {"id": "errors", "kind": "assertion", "title": "Erros durante a ação", "occurred_at": "2026-10-16T15:00:03Z", "assertion": {"kind": "no_new_browser_errors", "passed": True}},
    ]
    capture = {
        "id": "capture-db-1", "kind": "observation", "title": "Imagem anexada pelo usuário",
        "image_source": "user_upload", "source": "user_upload", "screenshot": "user-print.png",
        "occurred_at": "2026-10-16T15:05:00Z", "attached_at": "2026-10-16T15:05:00Z",
        "checkpoint_id": "db-1", "planned_gap": gap, "planned_order": 0, "capture_phase": "after_action",
    }
    return {
        "id": "rerun", "app_url": "https://shop.test", "status": "completed", "options": {},
        "timeline_version": TIMELINE_VERSION,
        "scenarios": [{"id": "CT001", "title": "Selecionar opção", "status": "PASS", "evidence": {
            "timeline": [*original, capture], "manual_captures": [deepcopy(capture)],
            "capture_plan": {"version": 1, "checkpoints": [{"id": "db-1", "gap": gap, "order": 0}]},
        }}],
    }


def test_api_exposes_planned_steps_without_reordering_or_mutating_chronology():
    stored = _snapshot()
    unchanged = deepcopy(stored)
    payload = _exploration_payload(stored)
    evidence = payload["scenarios"][0]["evidence"]
    assert [entry["id"] for entry in evidence["timeline"]] == ["before", "action", "after", "selection", "sort", "errors", "capture-db-1"]
    assert [entry["id"] for entry in evidence["presentation_steps"]] == ["before", "action", "capture-db-1", "after", "selection", "sort", "errors"]
    attachment = evidence["presentation_steps"][2]
    assert attachment["presentation_position"]["status"] == "planned"
    assert attachment["presentation_position"]["gap"] == 2
    assert attachment["occurred_at"] == attachment["attached_at"] == "2026-10-16T15:05:00Z"
    assert "assertion" not in attachment
    assert len([entry for entry in evidence["presentation_steps"] if entry["id"] == "capture-db-1"]) == 1
    assert payload["scenarios"][0]["status"] == "PASS"
    assert stored == unchanged
    evidence["presentation_steps"][0]["title"] = "Presentation edit"
    assert evidence["timeline"][0]["title"] == "Antes da ação"
    assert stored == unchanged


def test_api_does_not_guess_an_invalid_attachment_position():
    stored = _snapshot(gap=500)
    unchanged = deepcopy(stored)
    payload = _exploration_payload(stored)
    evidence = payload["scenarios"][0]["evidence"]
    assert [entry["id"] for entry in evidence["presentation_steps"]] == [entry["id"] for entry in evidence["timeline"]]
    assert evidence["presentation_steps"][-1]["presentation_position"]["status"] == "unresolved"
    assert stored == unchanged


def test_api_projection_is_idempotent_and_does_not_duplicate_attachments():
    first = _exploration_payload(_snapshot())
    second = _exploration_payload(first)
    assert second["scenarios"][0]["evidence"]["timeline"] == first["scenarios"][0]["evidence"]["timeline"]
    assert second["scenarios"][0]["evidence"]["presentation_steps"] == first["scenarios"][0]["evidence"]["presentation_steps"]
