from __future__ import annotations

from copy import deepcopy
import json
from typing import Any


def _identity(record: dict[str, Any]) -> str:
    """Legacy records without IDs still need stable deduplication."""
    return str(record.get("id") or json.dumps(record, sort_keys=True, default=str))


def _has_execution_provenance(record: dict[str, Any]) -> bool:
    resolution = record.get("resolution")
    if not isinstance(resolution, dict) or not record.get("id"):
        return False
    step = resolution.get("action_step")
    return (
        record.get("status") == "resolved"
        and resolution.get("source") == "actual_semantic_move_execution"
        and isinstance(step, int) and not isinstance(step, bool) and step > 0
        and resolution.get("success") is True
        and isinstance(resolution.get("assertions_passed"), bool)
        and bool(resolution.get("occurred_at"))
        and bool(resolution.get("source_url"))
        and bool(resolution.get("action"))
    )


def coverage_restriction_ledger(
    coverage: dict[str, Any] | None,
    events: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Project recorded restrictions without guessing historical recoveries.

    Only an explicit resolution event or terminal planner resolution snapshot
    with actual execution provenance retires a restriction. Reaching the same
    URL, finding a similarly named control, or a later PASS does not. A tested
    action may retain a business FAIL while resolving its restoration debt.
    The original objects and the historical terminal outcome are untouched.
    """
    coverage = coverage if isinstance(coverage, dict) else {}
    active: dict[str, dict[str, Any]] = {}
    resolved: dict[str, dict[str, Any]] = {}
    has_individual_records = False

    def record_block(item: Any) -> None:
        nonlocal has_individual_records
        if not isinstance(item, dict):
            return
        has_individual_records = True
        identity = _identity(item)
        # A terminal active snapshot can be older than its resolution event.
        if identity not in resolved:
            active[identity] = deepcopy(item)

    def record_resolution(item: Any) -> None:
        nonlocal has_individual_records
        if not isinstance(item, dict) or not _has_execution_provenance(item):
            return
        has_individual_records = True
        identity = _identity(item)
        original = active.pop(identity, {})
        resolved[identity] = {**original, **deepcopy(item)}

    # Explicit snapshots also serve the history API, which does not include
    # the full event list. Invalid resolution claims remain coverage gaps.
    for item in coverage.get("restrictions") or []:
        record_block(item)
    for item in coverage.get("resolved_restrictions") or []:
        if isinstance(item, dict) and _has_execution_provenance(item):
            record_resolution(item)
        else:
            record_block(item)

    for event in events or []:
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        payload = event.get("payload") or {}
        if event_type == "coverage_restriction_recorded":
            record_block(payload)
        elif event_type == "coverage_restriction_updated" and isinstance(payload, dict):
            # A late causal diagnosis supplements the original record. It is
            # not permission to invent an unknown restriction, rewrite its
            # original cause/status, or claim that it was resolved.
            identity = str(payload.get("id") or "")
            original = active.get(identity) or resolved.get(identity)
            dependency = payload.get("dependency")
            if original is not None and isinstance(dependency, dict):
                original["dependency"] = deepcopy(dependency)
        elif event_type == "coverage_restriction_resolved":
            record_resolution(payload)
        elif str(event_type or "").startswith("coverage_complete") and isinstance(payload, dict):
            for item in payload.get("restrictions") or []:
                record_block(item)
            for item in payload.get("resolved_restrictions") or []:
                if isinstance(item, dict) and _has_execution_provenance(item):
                    record_resolution(item)
                else:
                    record_block(item)

    return {
        "restrictions": list(active.values()),
        "resolved_restrictions": list(resolved.values()),
        "restriction_count": len(active),
        "resolved_restriction_count": len(resolved),
        "has_individual_records": has_individual_records,
    }
