from __future__ import annotations

from fastapi.testclient import TestClient

import laya_service as svc


def _state() -> dict:
    return {
        "url": "https://shop.example/inventory",
        "title": "Products",
        "visible_text": "Products " * 500,
        "console_errors": ["TypeError: x is undefined"],
        "candidate_moves": {
            f"move_{i:03d}": {"action": "click", "name": f"Item {i}", "href": f"/item/{i}", "target": f"e{i}", "value": None}
            for i in range(1, 31)
        },
    }


def _questions() -> dict:
    return {
        "next_move": {
            "type": "choice",
            "instructions": "Which move?",
            "criteria": {f"move_{i:03d}": {"move": f"Execute click on e{i}", "semantics": {"tag": "a"}} for i in range(1, 31)},
        },
        "qa_focus": {"type": "choice", "instructions": "Which lens?", "criteria": {"a": "Functional", "b": "Navigation"}},
        "issue_visible": {"type": "noul", "instructions": "Is there an issue?", "criteria": {"true": "yes", "false": "no"}},
    }


class FakeLaya:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, text: str, questions: dict) -> dict:
        self.calls.append((text, questions))
        first = next(iter(questions["next_move"]["criteria"]))
        return {
            "answers": {
                "next_move": {"choice": first, "confidence": 0.7},
                "qa_focus": {"choice": "b"},
                "issue_visible": {"noul": 0.2},
            },
            "routing": {"model": "english"},
        }


def test_decisions_returns_jev_shaped_answers_and_condenses_input() -> None:
    fake = FakeLaya()
    client = TestClient(svc.create_app(fake))

    response = client.post("/api/alpha/decisions", json={"model": svc.MODEL_ID, "state": _state(), "questions": _questions()})

    assert response.status_code == 200
    assert response.json()["answers"] == {
        "next_move": {"choice": "move_001", "confidence": 0.7},
        "qa_focus": {"choice": "b"},
        "issue_visible": {"noul": 0.2},
    }
    text, questions = fake.calls[0]
    assert len(text) <= svc.STATE_CHAR_BUDGET
    assert text.startswith("URL: https://shop.example/inventory")
    options = questions["next_move"]["criteria"]
    assert len(options) == svc.MAX_OPTIONS  # 30 candidates trimmed to Laya's budget
    assert options["move_001"] == "click Item 1 -> /item/1"
    assert all(len(label) <= svc.OPTION_CHAR_BUDGET for label in options.values())
    assert "criteria" not in questions["issue_visible"]  # true/false labels bias noul


def test_minimal_connection_check_state_is_accepted() -> None:
    client = TestClient(svc.create_app(lambda text, questions: {"answers": {"ok": {"noul": 0.9}}}))

    response = client.post(
        "/api/alpha/decisions",
        json={"model": svc.MODEL_ID, "state": {"connection_check": True}, "questions": {"ok": {"type": "noul", "instructions": "Is this a connection check?"}}},
    )

    assert response.json() == {"answers": {"ok": {"noul": 0.9}}}


def test_malformed_laya_output_is_a_502_not_a_fake_decision() -> None:
    client = TestClient(svc.create_app(lambda text, questions: {"answers": {"ok": {"noul": "yes"}}}))

    response = client.post(
        "/api/alpha/decisions",
        json={"state": {}, "questions": {"ok": {"type": "noul", "instructions": "?"}}},
    )

    assert response.status_code == 502


def test_models_endpoint_declares_text_only_capabilities() -> None:
    client = TestClient(svc.create_app(lambda text, questions: {}))

    body = client.get("/v1/models").json()

    assert body["data"][0]["id"] == svc.MODEL_ID
    assert body["data"][0]["architecture"] == {"input_modalities": ["text"], "output_modalities": ["text"]}
