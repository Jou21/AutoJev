"""Latest CT results and an immutable, owner-aware execution history.

The database remains an append-only record of independent executions. This
read-only projection substitutes a terminal rerun into its original report
slot, never borrowing screenshots or action records from another execution.
"""
from __future__ import annotations

import json
from collections import defaultdict
from copy import deepcopy
from hashlib import sha256
from typing import Any

from autojev.adapters.repository import NotFoundError, SQLiteRepository
from autojev.services.scenario_timeline import prepare_scenario_timelines

VERDICTS = {"PASS", "FAIL", "BLOCKED", "SKIPPED"}
TERMINAL_STATUSES = {"completed", "partial", "failed", "cancelled"}
_PRESENTATION_KEYS = {"execution_history", "execution_revision", "execution_actions", "evidence_exploration_id"}


def _identity(value: Any) -> str | None:
    return value if isinstance(value, str) and value and len(value) <= 512 else None


def _snapshot(scenario: dict[str, Any], owner: str, stable_id: str) -> dict[str, Any]:
    result = {key: deepcopy(value) for key, value in scenario.items() if key not in _PRESENTATION_KEYS}
    result.update(id=stable_id, evidence_exploration_id=owner)
    return result


def _single_result(exploration: dict[str, Any]) -> dict[str, Any] | None:
    scenarios = [entry for entry in exploration.get("scenarios") or []
                 if isinstance(entry, dict) and entry.get("status") in VERDICTS and _identity(entry.get("id"))]
    return scenarios[0] if len(scenarios) == 1 else None


def _associated_actions(exploration: dict[str, Any], scenario: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep only explicit CT action links, not an O(CTs × run actions) copy."""
    evidence = scenario.get("evidence") or {}
    references: set[int | str] = set()

    def remember(value: Any) -> None:
        if type(value) in {int, str}:
            references.add(value)

    remember(evidence.get("action_step"))
    for value in evidence.get("action_steps") or []:
        remember(value)
    for entry in evidence.get("timeline") or []:
        if isinstance(entry, dict) and entry.get("kind") == "action":
            remember(entry.get("step_number"))
            remember(entry.get("action_step"))
    return [deepcopy(action) for action in exploration.get("actions") or []
            if isinstance(action, dict) and action.get("step_number") in references]


def scenario_report_origin(repository: SQLiteRepository, run_id: str) -> dict[str, str | None]:
    """Resolve the original report/CT without projecting any descendant data.

    Active reruns can have no case yet. Every existing ancestor, however,
    must contain the exact referenced case, and an ancestor rerun must bind
    its unique result to one unambiguous durable parent. Bad ancestry keeps
    the link on this execution rather than guessing another report.
    """
    own = repository.get_scenario_revision_ancestry(run_id)
    own_cases = [case for case in own["scenarios"]
                 if _identity(case.get("id")) and case.get("status") in VERDICTS]
    fallback = {"report_exploration_id": run_id,
                "report_scenario_id": own_cases[0]["id"] if len(own_cases) == 1 else None}
    node, expected_case = own, None
    visited: set[str] = set()
    while node["id"] not in visited:
        visited.add(node["id"])
        cases = [case for case in node["scenarios"]
                 if _identity(case.get("id")) and case.get("status") in VERDICTS]
        if expected_case is not None:
            matches = [case for case in cases if case["id"] == expected_case]
            if len(matches) != 1:
                return fallback
        sources = node["sources"]
        if not sources:
            return ({"report_exploration_id": node["id"], "report_scenario_id": expected_case}
                    if expected_case is not None else fallback)
        links: set[tuple[str, str]] = set()
        for source in sources:
            parent = _identity(source.get("source_exploration_id")) if isinstance(source, dict) else None
            parent_case = _identity(source.get("source_scenario_id")) if isinstance(source, dict) else None
            if parent is None or parent_case is None or parent == node["id"]:
                return fallback
            links.add((parent, parent_case))
        if len(links) != 1 or len(cases) > 1 or (expected_case is not None and len(cases) != 1):
            return fallback
        parent, parent_case = next(iter(links))
        # A recorded result that contradicts its source event cannot redirect
        # to another CT. Missing fields are valid on old/unfinished reruns.
        for case in cases:
            case_parent, case_source = case.get("source_exploration_id"), case.get("source_scenario_id")
            if ((case_parent is not None and case_parent != parent)
                    or (case_source is not None and case_source != parent_case)):
                return fallback
        try:
            node = repository.get_scenario_revision_ancestry(parent)
        except NotFoundError:
            return fallback
        expected_case = parent_case
    return fallback


def scenario_revisions_key(exploration: dict[str, Any]) -> str:
    """Fingerprint full current/history evidence, including same-verdict edits."""
    value = [
        {"id": scenario.get("id"), "revision": scenario.get("execution_revision"),
         "history": scenario.get("execution_history") or [],
         "scenario": _snapshot(scenario, str(scenario.get("evidence_exploration_id") or exploration.get("id") or ""),
                               str(scenario.get("id") or "")),
         "actions": scenario.get("execution_actions") or []}
        for scenario in exploration.get("scenarios") or [] if isinstance(scenario, dict)
    ]
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()[:24]


def project_scenario_revisions(repository: SQLiteRepository, exploration: dict[str, Any]) -> dict[str, Any]:
    """Return latest persisted results; incomplete attempts remain history only.

    Numbering is relative to this report: its own stored case is version 0.
    Absolute root metadata still follows rerun-of-rerun parent links. Opening
    an individual rerun therefore includes its descendants, not its siblings.
    """
    source = deepcopy(exploration)
    # Re-project from durable data if a caller accidentally supplies a previous
    # projection. Otherwise a new history could archive an already-new result.
    if source.get("scenario_revisions_key") and _identity(source.get("id")):
        source = repository.get_exploration(source["id"])
    data = prepare_scenario_timelines(source)
    view_id = _identity(data.get("id"))
    if view_id is None:
        return data
    raw_cache: dict[str, dict[str, Any] | None] = {view_id: source}
    prepared_cache: dict[str, dict[str, Any]] = {view_id: data}

    def raw(run_id: str) -> dict[str, Any] | None:
        if run_id not in raw_cache:
            try:
                raw_cache[run_id] = repository.get_exploration(run_id)
            except NotFoundError:
                raw_cache[run_id] = None
        return raw_cache[run_id]

    def prepared(run_id: str) -> dict[str, Any] | None:
        item = raw(run_id)
        if item is not None and run_id not in prepared_cache:
            prepared_cache[run_id] = prepare_scenario_timelines(item)
        return prepared_cache.get(run_id)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in repository.list_scenario_rerun_sources():
        run_id = _identity(row.get("exploration_id"))
        if run_id:
            grouped[run_id].append(row)
    parents: dict[str, tuple[str, str]] = {}
    invalid: set[str] = set()
    for run_id, rows in grouped.items():
        links: set[tuple[str, str]] = set()
        for row in rows:
            payload = row.get("payload")
            parent_id = _identity(payload.get("source_exploration_id")) if isinstance(payload, dict) else None
            parent_case = _identity(payload.get("source_scenario_id")) if isinstance(payload, dict) else None
            if not parent_id or not parent_case or parent_id == run_id:
                invalid.add(run_id)
            else:
                links.add((parent_id, parent_case))
        if len(links) != 1:
            invalid.add(run_id)
        if run_id not in invalid:
            parents[run_id] = next(iter(links))

    root_cache: dict[tuple[str, str], tuple[str, str] | None] = {}

    def root_for(run_id: str, scenario_id: str) -> tuple[str, str] | None:
        key = (run_id, scenario_id)
        if key in root_cache:
            return root_cache[key]
        path: set[tuple[str, str]] = set()
        node = key
        while node not in path:
            path.add(node)
            item = raw(node[0])
            matches = [case for case in (item or {}).get("scenarios") or []
                       if isinstance(case, dict) and case.get("id") == node[1] and case.get("status") in VERDICTS]
            if len(matches) != 1 or node[0] in invalid:
                resolved = None
                break
            if node[0] not in parents:
                resolved = node
                break
            single = _single_result(item or {})
            if single is None or single.get("id") != node[1]:
                resolved = None
                break
            node = parents[node[0]]
        else:
            resolved = None
        for visited in path:
            root_cache[visited] = resolved
        return resolved

    children: dict[tuple[str, str], list[str]] = defaultdict(list)
    for run_id, parent in parents.items():
        children[parent].append(run_id)

    def order(run_id: str) -> tuple[str, str]:
        return (str((raw(run_id) or {}).get("created_at") or ""), run_id)

    for index, slot in enumerate(data.get("scenarios") or []):
        if not isinstance(slot, dict) or slot.get("status") not in VERDICTS:
            continue
        stable_id = _identity(slot.get("id"))
        if stable_id is None:
            continue
        absolute_root = root_for(view_id, stable_id)
        # Malformed/cyclic ancestry cannot assign a result to another CT.
        root_id, root_case = absolute_root or (view_id, stable_id)
        attempts: list[tuple[str, dict[str, Any] | None]] = []
        pending = list(children.get((view_id, stable_id), [])) if absolute_root else []
        visited = {view_id}
        while pending:
            run_id = pending.pop()
            if run_id in visited:
                continue
            visited.add(run_id)
            owner = raw(run_id)
            if owner is None:
                continue
            result = _single_result(owner)
            if result is not None:
                actual_id = result["id"]
                if root_for(run_id, actual_id) != absolute_root:
                    continue
                pending.extend(children.get((run_id, actual_id), []))
            attempts.append((run_id, result))
        attempts.sort(key=lambda value: order(value[0]))
        history: list[dict[str, Any]] = []
        for version, (owner_id, result) in enumerate([(view_id, slot), *attempts]):
            owner = prepared(owner_id) or {}
            actual_id = str(result["id"]) if result else None
            canonical = next((case for case in owner.get("scenarios") or []
                              if isinstance(case, dict) and case.get("id") == actual_id), None) if result else None
            status = str(owner.get("status") or "")
            has_result = canonical is not None and (version == 0 or status in TERMINAL_STATUSES)
            revision = {"version": version, "exploration_id": owner_id, "scenario_id": actual_id,
                        "root_exploration_id": root_id, "root_scenario_id": root_case,
                        "recorded_at": ((canonical or {}).get("created_at") or owner.get("finished_at")
                                        or owner.get("created_at")), "run_status": status}
            history.append({**revision, "status": (canonical or {}).get("status") if has_result else None,
                            "has_result": has_result, "current": False,
                            "scenario": _snapshot(canonical, owner_id, stable_id) if has_result else None,
                            "actions": _associated_actions(owner, canonical) if has_result else []})
        selected = next(entry for entry in reversed(history) if entry["has_result"])
        selected["current"] = True
        current = deepcopy(selected["scenario"])
        current["execution_revision"] = {key: selected[key] for key in (
            "version", "exploration_id", "scenario_id", "root_exploration_id", "root_scenario_id",
            "recorded_at", "run_status")}
        current["execution_history"] = history
        current["execution_actions"] = deepcopy(selected["actions"])
        data["scenarios"][index] = current
    counts = {status: 0 for status in VERDICTS}
    for scenario in data.get("scenarios") or []:
        if isinstance(scenario, dict) and scenario.get("status") in counts:
            counts[scenario["status"]] += 1
    data["summary"] = {**(data.get("summary") or {}), "total_scenarios": sum(counts.values()),
                       **{key.lower(): value for key, value in counts.items()}}
    data["scenario_revisions_key"] = scenario_revisions_key(data)
    return data
