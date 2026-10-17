"""Read-only roteiro ordering, separate from the chronological evidence log.

User attachments happen at real wall-clock times, often after the executor's
read-only assertion batch. Their planned insertion point is presentation
metadata, never authority to rewrite those timestamps or claim that checks ran
again. Future replays anchor a point to the source steps rather than trusting a
numeric gap against a potentially different replay timeline.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any


def _attachment(entry: dict[str, Any]) -> bool:
    return entry.get("kind") == "observation" and (
        entry.get("image_source") == "user_upload" or entry.get("source") == "user_upload"
    )


def _encoded(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _descriptors(entries: list[dict[str, Any]], evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Identify reproducible steps without their volatile time/ref/value result.

    An assertion includes its expected-value digest, not its actual result.
    Repeated identical checks are only matchable if both runs retain the same
    number. Arbitrary observations/signals and old attachments require an exact
    identity; they are never guessed to have been reproduced in a new run.
    """

    descriptors = []
    for entry in entries:
        kind = entry.get("kind")
        assertion = entry.get("assertion")
        if _attachment(entry):
            descriptor = {"kind": "user_upload", "id": entry.get("id"),
                          "screenshot": entry.get("screenshot")}
        elif kind == "action":
            descriptor = {"kind": "action"}
        elif kind == "assertion" and isinstance(assertion, dict) and "expected" in assertion:
            descriptor = {"kind": "assertion", "check_kind": assertion.get("kind"),
                          "expected_sha256": hashlib.sha256(_encoded(assertion["expected"]).encode()).hexdigest()}
        elif kind == "observation" and entry.get("title") in {"Antes da ação", "Before the action", "Before action"}:
            descriptor = {"kind": "observation", "role": "before_action"}
        elif kind == "observation" and entry.get("title") in {"Depois da ação", "After the action", "After action"}:
            descriptor = {"kind": "observation", "role": "after_action"}
        elif (kind == "observation" and entry.get("screenshot") and evidence.get("before_screenshot")
              and Path(str(entry["screenshot"])).name == Path(str(evidence["before_screenshot"])).name):
            descriptor = {"kind": "observation", "role": "before_action"}
        else:
            # Exact stored identities are suitable anchors only when they are
            # actually present. No timestamp-based "nearest step" inference.
            identity = entry.get("id") or hashlib.sha256(_encoded(entry).encode()).hexdigest()
            descriptor = {"kind": "recorded", "record_kind": kind, "id": identity}
        descriptors.append(descriptor)
    totals = Counter(_encoded(descriptor) for descriptor in descriptors)
    seen: Counter[str] = Counter()
    result = []
    for descriptor in descriptors:
        key = _encoded(descriptor)
        result.append({**descriptor, "occurrence": seen[key], "total": totals[key]})
        seen[key] += 1
    return result


def capture_position_anchor(
    source_steps: list[dict[str, Any]], gap: int, evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Snapshot a selected boundary in the *displayed* source roteiro.

    Only safe structural metadata is persisted. No user image bytes, credential
    values, actual assertion values or changed execution times are introduced.
    """

    if type(gap) is not int or not 0 <= gap <= len(source_steps) <= 10000:
        raise ValueError("A posição da imagem não corresponde aos passos do teste original.")
    descriptors = _descriptors(source_steps, evidence or {})
    return {
        "version": 1, "source_count": len(source_steps), "gap": gap,
        "left": descriptors[gap - 1] if gap else None,
        "right": descriptors[gap] if gap < len(descriptors) else None,
    }


def _anchored_gap(anchor: Any, gap: int, descriptors: list[dict[str, Any]]) -> tuple[int | None, str | None]:
    if (not isinstance(anchor, dict) or type(anchor.get("version")) is not int or anchor["version"] != 1
            or type(anchor.get("source_count")) is not int or not 0 <= anchor["source_count"] <= 10000
            or type(anchor.get("gap")) is not int or anchor["gap"] != gap
            or not 0 <= gap <= anchor["source_count"] or "left" not in anchor or "right" not in anchor):
        return None, "invalid_source_anchor"
    left, right = anchor["left"], anchor["right"]
    if ((gap == 0) != (left is None) or (gap == anchor["source_count"]) != (right is None)
            or (left is not None and not isinstance(left, dict))
            or (right is not None and not isinstance(right, dict))):
        return None, "invalid_source_anchor"

    def locate(descriptor: dict[str, Any]) -> int | None:
        matches = [index for index, candidate in enumerate(descriptors) if candidate == descriptor]
        return matches[0] if len(matches) == 1 else None

    if left is None and right is None:
        return (0, None) if not descriptors else (None, "source_steps_changed")
    left_index = locate(left) if left is not None else -1
    right_index = locate(right) if right is not None else len(descriptors)
    if left_index is None or right_index is None:
        return None, "source_anchor_unavailable"
    if right_index != left_index + 1:
        return None, "source_steps_changed"
    return right_index, None


def presentation_scenario_steps(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Project uploaded images into the selected roteiro slots, without writes.

    Legacy successful reruns already recorded a numeric ``planned_gap``. These
    can be displayed at that validated boundary. If a source step disappeared,
    an anchor is malformed or a partial replay cannot support the old numeric
    boundary, retain the image visibly with an unresolved position instead of
    silently clamping it to another step or manufacturing missing evidence.
    """

    if not isinstance(evidence, dict) or not isinstance(evidence.get("timeline"), list):
        return []
    entries = [deepcopy(entry) for entry in evidence["timeline"] if isinstance(entry, dict)]
    originals = [entry for entry in entries if not _attachment(entry)]
    descriptors = _descriptors(originals, evidence)
    insertions: dict[int, list[tuple[int, int, dict[str, Any]]]] = {}
    unresolved = []
    for ordinal, entry in enumerate(entries):
        if not _attachment(entry):
            continue
        gap, order = entry.get("planned_gap"), entry.get("planned_order")
        resolved, reason, method = None, None, "legacy_gap"
        if type(gap) is not int or not 0 <= gap <= 10000 or type(order) is not int or not 0 <= order <= 10000:
            reason = "invalid_planned_position"
        elif "position_anchor" in entry:
            method = "source_anchor"
            resolved, reason = _anchored_gap(entry["position_anchor"], gap, descriptors)
        elif evidence.get("missing_original_checks") or evidence.get("rerun_blocked_reason"):
            reason = "source_steps_unavailable"
        elif gap <= len(originals):
            resolved = gap
        else:
            reason = "planned_position_out_of_range"
        entry["presentation_position"] = {
            "status": "planned" if resolved is not None else "unresolved",
            "gap": gap, "order": order, "method": method,
            **({"reason": reason} if reason else {}),
        }
        if resolved is not None:
            insertions.setdefault(resolved, []).append((order, ordinal, entry))
        else:
            unresolved.append(entry)
    result = []
    for gap in range(len(originals) + 1):
        result.extend(entry for _order, _ordinal, entry in sorted(insertions.get(gap, [])))
        if gap < len(originals):
            result.append(originals[gap])
    return [*result, *unresolved]
