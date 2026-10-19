"""A durable initial page observation, distinct from pre-dispatch revalidation.

Only this replay's explicit event is accepted. Phase/position and the original
test's screenshots never prove that an observation happened in the new run.
"""
from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

INITIAL_OBSERVATION_EVENT = "rerun_before_observation"
_MESSAGE = "Estado inicial registrado antes de continuar o roteiro."
_ENTRY_FIELDS = {"id", "source_step_id", "kind", "title", "occurred_at", "url", "screenshot", "message"}


def _time(value: Any) -> float | None:
    if not isinstance(value, str) or len(value) > 100:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def _validated_entry(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict) or set(entry) != _ENTRY_FIELDS:
        return None
    source_id = entry.get("source_step_id")
    if (not isinstance(source_id, str) or not re.fullmatch(r"source-(?:0|[1-9][0-9]{0,3})", source_id)
            or entry.get("id") != f"rerun-{source_id}-before" or entry.get("kind") != "observation"
            or entry.get("title") != "Antes da ação" or entry.get("message") != _MESSAGE
            or _time(entry.get("occurred_at")) is None):
        return None
    url = entry.get("url")
    if not isinstance(url, str) or len(url) > 10000:
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        _ = parsed.port  # Reject malformed authorities without accessing the URL.
    except ValueError:
        return None
    screenshot = entry.get("screenshot")
    if screenshot is not None and (not isinstance(screenshot, str) or
                                   not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\.(?:png|jpe?g|webp)", screenshot, re.IGNORECASE)
                                   or PurePosixPath(screenshot).name != screenshot):
        return None
    return deepcopy(entry)


def initial_observation_from_events(item: dict[str, Any]) -> dict[str, Any] | None:
    """Return the first valid, immutable observation from the current run only.

    Repository event collections are already scoped to ``item.id``. Explicit
    foreign-run IDs are nevertheless rejected when a caller supplies one. No
    source exploration, archived scenario or generic screenshot is consulted.
    """
    for event in item.get("events") or []:
        if not isinstance(event, dict) or event.get("type") != INITIAL_OBSERVATION_EVENT:
            continue
        if "exploration_id" in event and event["exploration_id"] != item.get("id"):
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict) or set(payload) != {"scenario_id", "entry"} or payload.get("scenario_id") != "CT001":
            continue
        entry = _validated_entry(payload.get("entry"))
        if entry is not None:
            return entry
    return None


def with_initial_observation(evidence: dict[str, Any], entry: dict[str, Any] | None) -> dict[str, Any]:
    """Canonize one initial before-card while retaining real dispatch metadata.

    This changes a scenario's presentation copy, never its raw action result,
    checks, action timing or the browser's pre-dispatch observation. Ambiguous
    multi-attempt before entries are not silently collapsed into one record.
    """
    result = deepcopy(evidence)
    initial = _validated_entry(entry)
    if initial is None:
        return result
    timeline = result.get("timeline") or []
    if not isinstance(timeline, list) or any(not isinstance(row, dict) for row in timeline):
        return result
    before_shot = result.get("before_screenshot")

    def before(row: dict[str, Any]) -> bool:
        return (row.get("kind") == "observation" and row.get("source") != "user_upload"
                and row.get("image_source") != "user_upload" and
                (row.get("title") in {"Antes da ação", "Before action", "Before the action"}
                 or (before_shot and row.get("screenshot") == before_shot)))

    candidates = [row for row in timeline if before(row)]
    if len(candidates) > 1:
        return result
    previous = candidates[0] if candidates else {
        "kind": "observation", "title": "Antes da ação", "screenshot": before_shot,
        "occurred_at": result.get("before_observed_at"), "url": result.get("before_url"),
    }
    if (any(previous.get(key) for key in ("screenshot", "occurred_at", "url"))
            and any(previous.get(key) != initial.get(key) for key in ("screenshot", "occurred_at", "url"))):
        result.setdefault("dispatch_before_observation", {
            **deepcopy(previous), "title": "Revalidação imediatamente antes da ação",
        })
    result["before_screenshot"] = initial["screenshot"]
    result["before_observed_at"] = initial["occurred_at"]
    # One initial before-entry keeps source-step identities and insertion
    # anchors stable. A revalidation is metadata, not a second source step.
    result["timeline"] = sorted([initial, *(row for row in timeline if not before(row))],
                                key=lambda row: (_time(row.get("occurred_at")) is None,
                                                 _time(row.get("occurred_at")) or 0))
    return result
