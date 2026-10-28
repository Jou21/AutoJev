"""Local CPU service that exposes Laya through AutoJev's typed-decision contract.

AutoJev asks its decision model (JEV) typed questions at ``/api/alpha/decisions``.
Laya answers the same kind of question (``choice`` / ``noul``) but has a small
option budget and no OpenAI-compatible API, so this service adapts both sides:

* it renders the observation into a short text state,
* it condenses each question's criteria into short labels, keeping at most
  ``MAX_OPTIONS`` choices,
* it runs Laya on CPU and returns ``{"answers": {...}}`` in JEV's shape.

Run it on loopback only; it has no authentication.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

MODEL_ID = "convaiinnovations/laya"
# "multilingual" (322M, 1024 tokens, ~650 MB) covers pt-BR; "english" is 421M / 512 tokens.
# "typed-decisions" is tuned on four fixed synthetic workflows, not QA exploration.
CHECKPOINT = os.getenv("LAYA_CHECKPOINT", "multilingual")
MAX_OPTIONS = int(os.getenv("LAYA_MAX_OPTIONS", "20"))
STATE_CHAR_BUDGET = int(os.getenv("LAYA_STATE_CHARS", "2400"))
OPTION_CHAR_BUDGET = 90

Predictor = Callable[[str, dict[str, Any]], dict[str, Any]]


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value if value is not None else "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_state(state: dict[str, Any]) -> str:
    """Flatten an observation into a short text, most decisive facts first."""

    lines: list[str] = []
    for key, label in (("url", "URL"), ("title", "Title"), ("requested_scope", "Scope")):
        if state.get(key):
            lines.append(f"{label}: {_clip(state[key], 200)}")
    for key, label in (("console_errors", "Console errors"), ("network_errors", "Network errors")):
        items = state.get(key)
        if isinstance(items, list) and items:
            lines.append(f"{label}: " + " | ".join(_clip(item, 160) for item in items[-3:]))
    history = state.get("recent_history")
    if isinstance(history, list) and history:
        lines.append(
            "Recent: " + " | ".join(_clip(json.dumps(item, ensure_ascii=False), 120) for item in history[-3:])
        )
    coverage = state.get("coverage_state")
    if isinstance(coverage, dict) and coverage.get("path_depth") is not None:
        lines.append(f"Path depth: {coverage['path_depth']}")
    text = state.get("visible_text")
    if text:
        lines.append(f"Page text: {_clip(text, 1200)}")
    if not lines:
        lines.append(_clip(json.dumps(state, ensure_ascii=False, default=str), 400))
    return _clip("\n".join(lines), STATE_CHAR_BUDGET)


def _option_label(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("move") or value.get("name") or json.dumps(value, ensure_ascii=False)
    return _clip(value, OPTION_CHAR_BUDGET)


def _candidate_criteria(state: dict[str, Any]) -> dict[str, str]:
    moves = state.get("candidate_moves")
    if not isinstance(moves, dict):
        return {}
    criteria: dict[str, str] = {}
    for move_id, move in moves.items():
        if not isinstance(move, dict):
            continue
        parts = [str(move.get("action") or ""), str(move.get("name") or "")]
        if move.get("option_label"):
            parts.append(f"option {move['option_label']}")
        if move.get("href"):
            parts.append(f"-> {move['href']}")
        criteria[str(move_id)] = _clip(" ".join(part for part in parts if part), OPTION_CHAR_BUDGET)
    return criteria


def condense_question(key: str, question: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Shrink one JEV question to what Laya's option budget can hold."""

    kind = question.get("type")
    result: dict[str, Any] = {"type": kind, "instructions": _clip(question.get("instructions"), 240)}
    if kind != "choice":
        # ``noul`` labels such as "true"/"false" can bias Laya, so they are dropped.
        return result
    raw = question.get("criteria")
    criteria: dict[str, str] = {}
    if key == "next_move":
        criteria = _candidate_criteria(state)
    if not criteria and isinstance(raw, dict):
        criteria = {str(name): _option_label(value) for name, value in raw.items()}
    elif not criteria and isinstance(raw, list):
        criteria = {str(name): _clip(name, OPTION_CHAR_BUDGET) for name in raw}
    result["criteria"] = dict(list(criteria.items())[:MAX_OPTIONS])
    return result


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def normalise_answer(question: dict[str, Any], raw: Any) -> dict[str, Any]:
    """Return Laya's answer in JEV's shape, omitting what Laya did not provide."""

    if not isinstance(raw, dict):
        raise ValueError("Laya returned a malformed answer")
    kind = question.get("type")
    if kind == "noul":
        probability = _number(raw.get("noul"))
        if probability is None:
            raise ValueError("Laya returned no noul probability")
        return {"noul": min(1.0, max(0.0, probability))}
    choice = raw.get("choice")
    if not isinstance(choice, str):
        raise ValueError("Laya returned no choice")
    answer: dict[str, Any] = {"choice": choice}
    confidence = _number(raw.get("confidence"))
    if confidence is not None:
        answer["confidence"] = confidence
    probabilities = raw.get("probabilities")
    if isinstance(probabilities, dict):
        answer["probabilities"] = {
            str(name): value for name, value in probabilities.items() if _number(value) is not None
        }
    return answer


def decide(
    predict: Predictor, state: dict[str, Any], questions: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    condensed = {key: condense_question(key, question, state) for key, question in questions.items()}
    result = predict(render_state(state), condensed)
    raw_answers = result.get("answers") if isinstance(result, dict) else None
    if not isinstance(raw_answers, dict):
        raise ValueError("Laya returned no answers")
    return {
        "answers": {
            key: normalise_answer(question, raw_answers.get(key))
            for key, question in condensed.items()
        }
    }


class DecisionRequest(BaseModel):
    model: str = MODEL_ID
    state: dict[str, Any] = Field(default_factory=dict)
    questions: dict[str, dict[str, Any]] = Field(default_factory=dict)


def _load_router() -> Any:
    from laya import Router  # heavy import (torch); only when first needed

    # ``Router`` downloads lazily on first use; ``preload`` does it now, and only
    # for the one checkpoint served, instead of every checkpoint it knows about.
    router = Router(device=os.getenv("LAYA_DEVICE", "cpu"), max_loaded=1)
    return router.preload([CHECKPOINT])


def create_app(predictor: Predictor | None = None) -> FastAPI:
    """Build the app. ``predictor`` is injectable so tests need not load Laya."""

    state: dict[str, Any] = {"router": None}
    lock = asyncio.Lock()

    def router() -> Any:
        if state["router"] is None:
            state["router"] = _load_router()
        return state["router"]

    def predict(text: str, questions: dict[str, Any]) -> dict[str, Any]:
        if predictor is not None:
            return predictor(text, questions)
        # Pin the checkpoint: automatic routing would also download the English one.
        return router().predict(text, questions, model=CHECKPOINT)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # The first load downloads the checkpoints (~800 MB); doing it at startup
        # keeps that wait out of AutoJev's 45 s request timeout.
        if predictor is None and os.getenv("LAYA_PRELOAD", "1") != "0":
            await asyncio.to_thread(router)
        yield

    app = FastAPI(title="AutoJev Laya service", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "model": MODEL_ID, "loaded": predictor is not None or state["router"] is not None}

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        modalities = {"input_modalities": ["text"], "output_modalities": ["text"]}
        return {"data": [{"id": MODEL_ID, "name": "Laya (CPU local)", "architecture": modalities}]}

    @app.post("/api/alpha/decisions")
    async def decisions(body: DecisionRequest) -> dict[str, Any]:
        if not body.questions:
            raise HTTPException(status_code=422, detail="At least one question is required")
        async with lock:  # one model instance, one inference at a time
            try:
                return await asyncio.to_thread(decide, predict, body.state, body.questions)
            except ValueError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc

    return app


app = create_app()
