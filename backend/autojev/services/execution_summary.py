"""Read-only execution timing shared by the API and report presentation."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any


LIVE_STATUSES = {"queued", "pending", "running", "waiting_input", "postprocessing"}


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (TypeError, ValueError):
        return None


def execution_duration_summary(exploration: dict[str, Any]) -> dict[str, Any]:
    """Include the auxiliary/report tail, without rewriting historical dates.

    During report generation finished_at still marks the browser's end. Later
    recorded events prove that the execution continued. The final timestamp
    and those events therefore share one maximum endpoint. Live runs keep an
    open endpoint so the dashboard can continue its clock.
    """
    started = _timestamp(exploration.get("started_at")) or _timestamp(exploration.get("created_at"))
    # A visual-only replay may happen days later. Its billing/events refresh
    # updated_at but do not mean the original browser exploration lasted days.
    resume_starts = [
        _timestamp(event.get("created_at")) for event in exploration.get("events") or []
        if (event.get("type") or event.get("event_type")) == "visual_review_resume_started"
    ]
    resumed_at = min((value for value in resume_starts if value is not None), default=None)
    updated = _timestamp(exploration.get("updated_at"))
    endpoints = [
        _timestamp(exploration.get("finished_at")),
        updated if resumed_at is None or (updated and updated < resumed_at) else None,
        *[value for event in exploration.get("events") or []
          if not str(event.get("type") or event.get("event_type") or "").startswith("visual_review_resume_")
          and (value := _timestamp(event.get("created_at"))) is not None
          and (resumed_at is None or value < resumed_at)],
    ]
    recorded_end = max((value for value in endpoints if value is not None), default=None)
    ongoing = str(exploration.get("status") or "").lower() in LIVE_STATUSES
    ended = None if ongoing else recorded_end
    seconds = max(int((ended - started).total_seconds()), 0) if started and ended else None
    return {
        "started_at": started.isoformat() if started else None,
        "ended_at": ended.isoformat() if ended else None,
        "total_seconds": seconds,
        "ongoing": ongoing,
        "scope": "execution_including_postprocessing",
    }


def report_snapshot_key(exploration: dict[str, Any]) -> str:
    """Invalidate presentation caches when timing or recorded usage changes.

    Role attribution can arrive after the original report was written. Keep
    both its values and completeness flags in the key, without changing any
    historical artifact or inferring costs that were never recorded.
    """
    duration = execution_duration_summary(exploration)
    snapshot = {
        "presentation": "visual-replay-v1",
        "status": exploration.get("status"),
        "started_at": duration["started_at"],
        "ended_at": duration["ended_at"],
        "usage": exploration.get("usage") or {},
        "visual_reviews": [
            {"id": scenario.get("id"),
             "assessment": (scenario.get("evidence") or {}).get("visual_assessment"),
             "provenance": (scenario.get("evidence") or {}).get("visual_review_provenance"),
             "history": (scenario.get("evidence") or {}).get("visual_assessment_history"),
             "status": scenario.get("status")}
            for scenario in exploration.get("scenarios") or []
        ],
        # Use the durable state, not a presentation-only default injected by
        # the API. Direct rendering and GET /report must share one cache key.
        "visual_resume": next((
            event.get("payload") for event in reversed(exploration.get("events") or [])
            if (event.get("type") or event.get("event_type")) == "visual_review_resume_state"
        ), None),
    }
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()[:20]


def formatted_execution_duration(exploration: dict[str, Any]) -> str:
    seconds = execution_duration_summary(exploration)["total_seconds"]
    if seconds is None:
        return "—"
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
