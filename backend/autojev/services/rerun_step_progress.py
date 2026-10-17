"""A read-only live roteiro, based only on evidence of the current replay.

The source plan carries structural identities, never its verdicts, values or
image contents. A completed old CT is not evidence that the replay succeeded.
Planned display order is intentionally independent of actual event timestamps.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from copy import deepcopy
from typing import Any

from autojev.services.report_localization import ACTION_LABELS, CHECK_LABELS, check_label
from autojev.services.scenario_step_order import presentation_scenario_steps
from autojev.services.scenario_timeline import prepare_scenario_timelines


TERMINAL = {"completed", "partial", "failed", "cancelled"}


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _identity(entry: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    kind = entry.get("kind")
    if entry.get("image_source") == "user_upload" or entry.get("source") == "user_upload":
        return {"kind": "historical_attachment"}
    if kind == "action":
        return {"kind": "action"}
    if kind == "assertion":
        assertion = entry.get("assertion") or {}
        if not isinstance(assertion, dict):
            return {"kind": "unreplayed", "record_kind": "assertion"}
        check_kind = assertion.get("kind")
        if not isinstance(check_kind, str) or check_kind not in CHECK_LABELS:
            return {"kind": "unreplayed", "record_kind": "assertion"}
        return {"kind": "assertion", "check_kind": check_kind,
                **({"expected_sha256": _digest(assertion["expected"])} if "expected" in assertion else {})}
    if kind == "observation":
        title = str(entry.get("title") or "")
        role = ("before_action" if title in {"Antes da ação", "Before the action", "Before action"} else
                "after_action" if title in {"Depois da ação", "After the action", "After action", "Estado após a ação"} else None)
        if role is None and entry.get("screenshot") and entry.get("screenshot") == evidence.get("before_screenshot"):
            role = "before_action"
        if role:
            return {"kind": "observation", "role": role}
    # Unknown source steps must not become green merely because the replay
    # emitted another step of the same kind.
    return {"kind": "unreplayed", "record_kind": kind if kind in {"observation", "signal"} else "observation"}


def _identities(entries: list[dict[str, Any]], evidence: dict[str, Any]) -> list[dict[str, Any]]:
    identities = [_identity(entry, evidence) for entry in entries]
    totals = Counter(_digest(identity) for identity in identities)
    seen: Counter[str] = Counter()
    result = []
    for identity in identities:
        key = _digest(identity)
        result.append({**identity, "occurrence": seen[key], "total": totals[key]})
        seen[key] += 1
    return result


def build_step_plan(source_steps: list[dict[str, Any]], capture_plan: dict[str, Any],
                    evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    """Snapshot safe source structure and insert authorized manual waits."""
    evidence = evidence or {}
    identities = _identities(source_steps, evidence)
    gaps: dict[int, list[dict[str, Any]]] = {}
    for checkpoint in capture_plan.get("checkpoints") or []:
        if not isinstance(checkpoint, dict):
            continue
        gap = checkpoint.get("gap")
        if (type(gap) is int and 0 <= gap <= len(source_steps)
                and isinstance(checkpoint.get("id"), str) and re.fullmatch(r"db-[0-9]{1,9}", checkpoint["id"])
                and type(checkpoint.get("order")) is int and 0 <= checkpoint["order"] <= 10000):
            gaps.setdefault(gap, []).append(checkpoint)
    rows = []
    for index in range(len(source_steps) + 1):
        for checkpoint in sorted(gaps.get(index, []), key=lambda value: value.get("order", 0)):
            rows.append({"id": f"checkpoint-{checkpoint['id']}", "title": "Aguardar print", "kind": "capture_wait",
                         "checkpoint_id": checkpoint["id"], "gap": index, "order": checkpoint.get("order", 0)})
        if index == len(source_steps):
            break
        entry, identity = source_steps[index], identities[index]
        kind = str(entry.get("kind") or "observation")
        if kind not in {"observation", "action", "assertion", "signal"}:
            kind = "observation"
        title = "Registro da página"
        if identity["kind"] == "historical_attachment":
            title = "Imagem anexada pelo usuário"
        elif kind == "action":
            original_title = str(entry.get("title") or "")
            title = original_title if original_title in ACTION_LABELS.values() else ACTION_LABELS.get(original_title.removeprefix("Ação: "), "Ação do teste")
        elif kind == "assertion":
            title = check_label(identity.get("check_kind")) if identity.get("check_kind") in CHECK_LABELS else "Conferência do teste"
            if identity.get("check_kind") in {"no_new_browser_errors", "no_new_application_errors"}:
                title = "Erros durante a ação"
        elif identity.get("role") == "before_action":
            title = "Antes da ação"
        elif identity.get("role") == "after_action":
            title = "Depois da ação"
        elif kind == "signal":
            title = "Sinal técnico registrado"
        rows.append({"id": f"source-{index}", "title": title, "kind": kind,
                     "source_index": index, "identity": identity})
    return {"version": 1, "steps": rows}


def progress_check_bindings(original_action: dict[str, Any] | None, current_assertions: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Preserve only digests of a recipe-authorized volatile-reference rebind.

    ``element_visible`` is the sole direct check rebound by the resolver. No
    browser reference/value is exposed, and another check kind cannot use this
    mapping to acquire approval for a different requested value.
    """
    original = ((original_action or {}).get("decision") or {}).get("assertions") or []
    result = []
    for old, new in zip(original, current_assertions):
        if isinstance(old, dict) and isinstance(new, dict) and old.get("kind") == new.get("kind") == "element_visible":
            result.append({"kind": "element_visible", "source_expected_sha256": _digest(old.get("expected")),
                           "current_expected_sha256": _digest(new.get("expected"))})
    return result


def _current_steps(item: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    # This item is the *current* run only; no source result is consulted.
    prepared = prepare_scenario_timelines(item)
    scenarios = prepared.get("scenarios") or []
    scenario = next((value for value in scenarios if value.get("id") == "CT001"), None)
    evidence = (scenario or {}).get("evidence") or {}
    return presentation_scenario_steps(evidence), evidence


def project_step_progress(item: dict[str, Any], rerun: dict[str, Any],
                          step_plan: dict[str, Any] | None = None) -> dict[str, Any]:
    """Project truthful live statuses; this function never changes saved data."""
    plan = step_plan or rerun.get("step_plan") or {}
    if plan.get("version") != 1 or not isinstance(plan.get("steps"), list):
        return {"version": 1, "steps": [], "completed": 0, "total": 0,
                "notice": "O roteiro original não está disponível. As evidências desta execução permanecem preservadas."}
    entries, evidence = _current_steps(item)
    identities = _identities(entries, evidence)
    bindings = rerun.get("step_check_bindings") or []
    rebound = {}
    for binding in bindings if isinstance(bindings, list) else []:
        if (isinstance(binding, dict) and binding.get("kind") == "element_visible"
                and all(isinstance(binding.get(key), str) and re.fullmatch(r"[0-9a-f]{64}", binding[key])
                        for key in ("source_expected_sha256", "current_expected_sha256"))):
            rebound[binding["source_expected_sha256"]] = binding["current_expected_sha256"]
    matches: dict[str, list[dict[str, Any]]] = {}
    for entry, identity in zip(entries, identities):
        matches.setdefault(_digest(identity), []).append(entry)
    uploads: dict[str, list[dict[str, Any]]] = {}
    # Confirmed uploads have their own stored event. Accepted-but-uncommitted
    # images and a mere captures_completed counter cannot finish a card.
    for entry in entries:
        if entry.get("image_source") == "user_upload" and entry.get("screenshot"):
            uploads.setdefault(str(entry.get("checkpoint_id") or ""), []).append(entry)
    for event in item.get("events") or []:
        payload = event.get("payload") or {}
        if event.get("type") == "manual_print_attached" and isinstance(payload, dict) and payload.get("image_source") == "user_upload" and payload.get("screenshot"):
            uploads.setdefault(str(payload.get("checkpoint_id") or ""), []).append(payload)
    terminal = str(item.get("status")) in TERMINAL or rerun.get("phase") == "finished"
    phase = str(rerun.get("phase") or "queued")
    checkpoint = rerun.get("capture_checkpoint") or {}
    checkpoint_id = checkpoint.get("id") or checkpoint.get("checkpoint_id")
    rows = []
    for source_row in plan["steps"]:
        if not isinstance(source_row, dict):
            continue
        row = {key: deepcopy(source_row[key]) for key in ("id", "title", "kind", "source_index", "checkpoint_id") if key in source_row}
        row.update(state="pending", label="Aguardando", description="Esta etapa ainda não tem um resultado registrado nesta reexecução.")
        identity = source_row.get("identity") or {}
        entry = None
        if row.get("kind") == "capture_wait":
            candidates = uploads.get(str(row.get("checkpoint_id")), [])
            entry = candidates[0] if candidates else None
            if entry is not None:
                row.update(state="done", label="Imagem anexada", description="Sua imagem foi anexada. Ela não aprova nem reprova o teste.")
            elif checkpoint_id == row.get("checkpoint_id") and phase in {"waiting_capture", "capturing_print"}:
                row.update(state="waiting" if phase == "waiting_capture" else "active",
                           label="Aguardando seu print" if phase == "waiting_capture" else "Salvando imagem",
                           description="Cole com Ctrl+V, arraste ou escolha uma imagem e confirme para continuar." if phase == "waiting_capture" else "A confirmação foi recebida. A imagem só será concluída após ser salva.")
        elif identity.get("kind") in {"historical_attachment", "unreplayed"}:
            row.update(state="unavailable", label="Não reexecutado", description="Este registro pertence ao roteiro original e não é reproduzido automaticamente.")
        elif identity.get("kind") == "assertion" and identity.get("check_kind") == "visual_model":
            row.update(state="skipped", label="Não executada", description="A análise visual por modelo não é repetida nesta reexecução local.")
        else:
            match_identity = deepcopy(identity)
            if match_identity.get("check_kind") == "element_visible" and match_identity.get("expected_sha256") in rebound:
                match_identity["expected_sha256"] = rebound[match_identity["expected_sha256"]]
            candidates = matches.get(_digest(match_identity), [])
            entry = candidates[0] if len(candidates) == 1 else None
            if entry is not None and row.get("kind") == "action":
                success = entry.get("success")
                if success is True:
                    row.update(state="done", label="Ação concluída", description="A execução da ação foi confirmada. As conferências mostram seu resultado.")
                elif success is False:
                    row.update(state="failed", label="Ação falhou", description="O navegador registrou uma falha ao executar esta ação.")
                else:
                    row.update(state="unavailable", label="Sem confirmação", description="A ação foi registrada, mas não há confirmação de sucesso.")
            elif entry is not None and row.get("kind") == "assertion":
                passed = (entry.get("assertion") or {}).get("passed")
                if passed is True:
                    row.update(state="done", label="Aprovada", description="Esta conferência passou nesta reexecução.")
                elif passed is False:
                    row.update(state="failed", label="Reprovada", description="Esta conferência encontrou uma diferença nesta reexecução.")
                else:
                    row.update(state="unavailable", label="Sem resultado", description="A conferência não tem um resultado confirmado.")
            elif entry is not None and row.get("kind") == "observation":
                row.update(state="done", label="Captura registrada" if entry.get("screenshot") else "Estado registrado",
                           description="O estado da página foi registrado nesta reexecução; isso não é uma aprovação do teste.")
        if entry is not None:
            if isinstance(entry.get("occurred_at") or entry.get("attached_at"), str):
                row["occurred_at"] = entry.get("occurred_at") or entry.get("attached_at")
            if isinstance(entry.get("id"), str):
                row["evidence_entry_id"] = entry["id"]
        rows.append(row)
    # Never color unperformed steps green at cancellation, timeout or failure.
    if terminal:
        for row in rows:
            if row["state"] in {"pending", "active", "waiting"}:
                row.update(state="blocked" if str(item.get("status")) != "completed" else "unavailable",
                           label="Não concluída", description="Não há resultado desta etapa nesta reexecução. As evidências já registradas foram preservadas.")
    elif not any(row["state"] in {"active", "waiting"} for row in rows):
        if phase in {"preparing", "waiting_preparation", "validating_preparation", "queued"}:
            current = next((row for row in rows if row["state"] == "pending" and row.get("kind") != "capture_wait"), None)
            if current:
                current.update(state="waiting" if phase == "waiting_preparation" else "active", label="Preparando a página",
                               description="Conclua os pré-requisitos e confirme a continuação. Nenhuma ação do CT está concluída ainda.")
        elif phase == "executing":
            current = next((row for row in rows if row["state"] == "pending" and row.get("kind") == "action"), None)
            if current:
                current.update(state="active", label="Executando", description="A ação está em andamento. Aguardando um resultado confirmado.")
    active = next((row for row in rows if row["state"] in {"waiting", "active"}), None)
    return {"version": 1, "steps": rows, "completed": sum(row["state"] == "done" for row in rows), "total": len(rows),
            **({"current_id": active["id"]} if active else {}),
            "notice": "Os cards seguem o roteiro planejado. As conferências locais são registradas juntas após a ação e podem estar concluídas antes de um print posicionado entre elas. Os horários reais são preservados."}
