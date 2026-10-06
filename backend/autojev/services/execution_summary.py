"""Read-only execution timing shared by the API and report presentation."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
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
    endpoints = [
        _timestamp(exploration.get("finished_at")),
        _timestamp(exploration.get("updated_at")),
        *[_timestamp(event.get("created_at")) for event in exploration.get("events") or []],
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
    """Invalidate presentation-only caches when the terminal endpoint changes."""
    duration = execution_duration_summary(exploration)
    snapshot = (exploration.get("status"), duration["started_at"], duration["ended_at"])
    return sha256(repr(snapshot).encode("utf-8")).hexdigest()[:20]


def formatted_execution_duration(exploration: dict[str, Any]) -> str:
    seconds = execution_duration_summary(exploration)["total_seconds"]
    if seconds is None:
        return "—"
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
