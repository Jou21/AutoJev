from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any

from autojev.services.findings import SEVERITY_ORDER, search_findings


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


def ai_flagged_gaps(events: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Gaps the auxiliary model saw on screen that the explorer never covered.

    They come from the ``unexplored_notes`` of ``frontier_advice`` events. A
    note is a lead to investigate, not a verified defect or an approved test,
    so it is kept apart from the individually recorded restrictions. The same
    note on the same page is reported once.
    """

    gaps: dict[str, dict[str, Any]] = {}
    for event in events or []:
        if not isinstance(event, dict) or event.get("type") != "frontier_advice":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        page = str(payload.get("url") or "")
        for note in payload.get("unexplored_notes") or []:
            text = " ".join(str(note).split())
            if not text:
                continue
            normalised = re.sub(r"\W+", " ", text).strip().lower()
            key = hashlib.sha1(f"{page}|{normalised}".encode()).hexdigest()[:16]
            gaps.setdefault(key, {
                "id": key,
                "note": text[:300],
                "url": page or None,
                "screenshot": payload.get("screenshot"),
                "trigger": payload.get("trigger"),
                "focus_status": payload.get("focus_status"),
            })
    return list(gaps.values())


def functional_findings(events: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Defects the run observed, most severe first; each listed once.

    Screen findings are recorded as ``functional_finding`` events while the run
    goes; search findings (a default the search refuses, no test data) are read
    from the search events. Browser signals (HTTP, console) stay in the CT
    timelines: they are not findings by themselves.
    """

    found: dict[str, dict[str, Any]] = {}
    for event in events or []:
        if isinstance(event, dict) and event.get("type") == "functional_finding" and isinstance(event.get("payload"), dict):
            payload = event["payload"]
            if payload.get("id"):
                found.setdefault(str(payload["id"]), {**payload, "observed_at": event.get("created_at") or event.get("timestamp")})
    for finding in search_findings(events):
        found.setdefault(finding["id"], finding)
    return sorted(found.values(), key=lambda f: SEVERITY_ORDER.get(str(f.get("severity")), 9))


def search_coverage(events: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """What the period/data searches covered, from the activity log.

    ``None`` when the run never exercised a search form. Ranges and data
    variations are listed exactly as they were searched, with how many records
    and validation messages each returned, so "no records found" is a stated
    result and not an unexplained gap.
    """

    ranges: list[dict[str, Any]] = []
    variations: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    matrix_done: dict[str, Any] | None = None
    variation_done: dict[str, Any] | None = None
    for event in events or []:
        if not isinstance(event, dict):
            continue
        kind, payload = event.get("type"), event.get("payload")
        if not isinstance(payload, dict):
            continue
        if kind == "search_matrix_range" and not payload.get("replay"):
            ranges.append({key: payload.get(key) for key in (
                "matrix", "label", "rows", "empty_state", "skipped", "blocked_by")})
        elif kind == "search_matrix_completed":
            matrix_done = payload
        elif kind == "search_variation_result":
            variations.append({key: payload.get(key) for key in (
                "matrix", "field", "kind", "value", "rows", "validation_errors", "first_error", "skipped",
                "blocked_by", "carried_over")})
        elif kind == "search_variation_completed":
            variation_done = payload
        elif kind == "search_matrix_unsupported":
            unsupported.append({"fields": payload.get("fields") or [], "reason": payload.get("reason")})
    if not (ranges or variations or unsupported or matrix_done):
        return None
    found = [r["label"] for r in ranges if r.get("rows")]
    return {
        "ranges": ranges,
        "months_with_records": found,
        "found_records": bool(found),
        "stopped_reason": (matrix_done or {}).get("reason"),
        "variations": variations,
        "variation_searches": (variation_done or {}).get("searches", len(variations)),
        "variations_with_validation": sum(1 for v in variations if v.get("validation_errors")),
        "unsupported": unsupported,
    }
