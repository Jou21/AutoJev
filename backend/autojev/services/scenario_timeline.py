"""Project stored browser evidence into CT timelines without rewriting history.

Old runs did not store a request-to-CT link. Only explicit links, unique capture
matches or unique action time windows are used. Unmatched evidence belongs to a
context card, never to an arbitrarily nearby test or to an invented PASS.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from autojev.services.action_dispatch import is_stale_undispatched_action
from autojev.services.technical_signals import (
    classify_signal,
    correlate_issues,
    signal_occurrence_id,
)
from autojev.services.verification_summary import NON_BUSINESS_CHECKS

TIMELINE_VERSION = 1


def _time(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _shot(value: Any) -> str:
    return Path(str(value)).name if value else ""


def _merge_signal(
    current: dict[str, Any], richer: dict[str, Any], app_url: str
) -> dict[str, Any]:
    """Keep native action attribution while adding late correlated channels."""

    merged = {**richer, **current}
    merged["evidence_channels"] = list(
        dict.fromkeys(
            [
                *(current.get("evidence_channels") or []),
                *(richer.get("evidence_channels") or []),
            ]
        )
    )
    related = [
        *(current.get("related_console_errors") or []),
        *(richer.get("related_console_errors") or []),
    ]
    unique = {json.dumps(item, sort_keys=True, default=str): item for item in related}
    merged["related_console_errors"] = list(unique.values())
    return classify_signal(merged, app_url)


def _unique_timeline(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for entry in entries:
        identity = str(entry.get("id") or "")
        if not identity:
            identity = hashlib.sha256(
                json.dumps(entry, sort_keys=True, default=str).encode()
            ).hexdigest()[:20]
            entry["id"] = identity
        if identity not in seen:
            result.append(entry)
            seen.add(identity)
    # Stable sort preserves before/action/assertion/after ordering for entries
    # sharing a timestamp. Unknown legacy timestamps remain explicitly unknown.
    return sorted(
        result,
        key=lambda entry: (
            _time(entry.get("occurred_at")) is None,
            _time(entry.get("occurred_at")) or 0,
        ),
    )


def _action_entries(
    action: dict[str, Any], screenshots: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    result = action.get("result") or {}
    decision = action.get("decision") or {}
    step = action.get("step_number")
    started = result.get("started_at")
    finished = result.get("finished_at")
    entries: list[dict[str, Any]] = []
    start_time = _time(started)
    before_shots = [
        shot
        for shot in screenshots
        if _time(shot.get("created_at")) is not None
        and start_time is not None
        and _time(shot.get("created_at")) <= start_time
        and shot.get("path")
    ]
    if before_shots:
        shot = max(before_shots, key=lambda item: _time(item.get("created_at")) or 0)
        entries.append(
            {
                "id": f"step-{step}-before",
                "kind": "observation",
                "occurred_at": shot.get("created_at"),
                "title": "Estado anterior · última captura disponível",
                "message": "Captura salva antes desta ação; não é uma nova execução.",
                "url": result.get("before_url"),
                "screenshot": _shot(shot["path"]),
                "step_number": step,
            }
        )
    entries.append(
        {
            "id": f"step-{step}-action",
            "kind": "action",
            "occurred_at": started,
            "title": f"Ação: {decision.get('action') or result.get('action') or 'interação'}",
            "message": result.get("message") or decision.get("rationale") or "",
            "url": result.get("before_url"),
            "step_number": step,
            "success": result.get("success"),
            "target": decision.get("target") or result.get("target"),
        }
    )
    for index, assertion in enumerate(result.get("assertions") or []):
        entries.append(
            {
                "id": f"step-{step}-assertion-{index}",
                "kind": "assertion",
                "occurred_at": finished,
                "title": "Verificação observada",
                "message": assertion.get("message") or "",
                "url": result.get("after_url"),
                "step_number": step,
                "assertion": assertion,
            }
        )
    entries.append(
        {
            "id": f"step-{step}-after",
            "kind": "observation",
            "occurred_at": finished,
            "title": "Estado após a ação",
            "message": result.get("message") or "",
            "url": result.get("after_url"),
            "screenshot": _shot(result.get("screenshot")) or None,
            "step_number": step,
        }
    )
    return entries


def _verdict(scenario: dict[str, Any], *, historical: bool) -> dict[str, Any]:
    evidence = scenario.get("evidence") or {}
    status = str(scenario.get("status") or "SKIPPED").upper()
    failed = [a for a in evidence.get("assertions") or [] if a.get("passed") is False]
    if (
        status == "FAIL"
        and failed
        and all(
            str(a.get("kind")) in {"no_new_browser_errors", "no_new_application_errors"}
            for a in failed
        )
    ):
        return {
            "kind": "technical_failure",
            "label": "Verificação técnica falhou",
            "explanation": (
                "Uma verificação de console/rede falhou. Isso não confirma, por si só, "
                "um defeito funcional. O resultado original foi preservado."
                if historical
                else "Uma verificação técnica da aplicação falhou; consulte a requisição "
                "ou exceção na linha do tempo. O impacto funcional precisa ser validado."
            ),
            "historical": historical,
        }
    if status == "FAIL" and failed and all(a.get("kind") == "visual_model" for a in failed):
        kind, label, explanation = (
            "visual_failure", "Indício visual · revisão necessária",
            ("A revisão das capturas encontrou uma divergência visual de alta confiança. "
            "O CT foi reprovado por esse indício, não por um bug funcional confirmado; "
            "consulte a imagem, a região indicada e a origem da expectativa."),
        )
    elif status == "FAIL" and evidence.get("action_succeeded") is False:
        kind, label, explanation = (
            "execution_failure",
            "Falha na execução da ação",
            "A automação não concluiu a ação. Isso não comprova automaticamente um bug do site.",
        )
    elif status == "FAIL":
        kind, label, explanation = (
            "assertion_failure",
            "Verificação do CT falhou",
            "Uma condição esperada não foi observada. Consulte a verificação e as evidências.",
        )
    elif status == "PASS":
        business_checks = any(
            a.get("kind") and a.get("passed") is True
            and a.get("kind") not in NON_BUSINESS_CHECKS
            for a in evidence.get("assertions") or []
        )
        kind, label, explanation = (
            "validated" if business_checks else "interaction_only",
            "Verificações aprovadas" if business_checks else "Interação técnica verificada",
            "As verificações executadas passaram; isso não representa todas as regras de negócio."
            if business_checks else "A interação alterou a tela sem novos erros atribuídos. "
            "Não houve uma verificação específica do resultado de negócio neste CT.",
        )
    elif status == "BLOCKED" and any(a.get("kind") == "form_validation_probe_precondition" for a in failed):
        kind, label, explanation = (
            "not_validated", "Validação impedida por uma pré-condição",
            "A tentativa foi executada, mas outro campo defeituoso impediu isolar ou atribuir o resultado esperado. "
            "Consulte os campos bloqueadores e a resposta da aplicação na linha do tempo; não é uma aprovação.",
        )
    elif status == "BLOCKED" and (evidence.get("business_settle") or {}).get("status") == "api_wait_timed_out":
        kind, label, explanation = (
            "not_validated", "Resultado ainda pendente",
            ("A ação foi executada, mas a API não terminou dentro da espera limitada. "
            "Isso não comprova um defeito nem uma aprovação; as demais opções foram preservadas."),
        )
    else:
        kind, label, explanation = (
            "not_validated",
            "Sem validação concluída" if status != "BLOCKED" else "Execução bloqueada",
            "Este registro não deve ser contado como teste aprovado.",
        )
    return {
        "kind": kind,
        "label": label,
        "explanation": explanation,
        "historical": historical,
    }


def prepare_scenario_timelines(exploration: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic, read-only presentation of current/legacy runs."""

    data = deepcopy(exploration)
    if data.get("timeline_version") == TIMELINE_VERSION:
        return data
    app_url = str(data.get("app_url") or "")
    actions = data.get("actions") or []
    screenshots = data.get("screenshots") or []
    scenarios = data.get("scenarios") or []
    action_by_step = {action.get("step_number"): action for action in actions}
    owners_by_step: dict[Any, list[dict[str, Any]]] = {}
    owners_by_shot: dict[str, list[dict[str, Any]]] = {}
    by_id = {str(scenario.get("id")): scenario for scenario in scenarios}
    included_signals: set[str] = set()
    signal_owners: dict[str, list[dict[str, Any]]] = {}

    for scenario in scenarios:
        evidence = scenario.setdefault("evidence", {})
        native_timeline = bool(evidence.get("timeline"))
        entries = list(evidence.get("timeline") or [])
        for entry in entries:
            if (
                entry.get("step_number") is None
                and entry.get("action_step") is not None
            ):
                entry["step_number"] = entry["action_step"]
        steps = set(evidence.get("action_steps") or [])
        if evidence.get("action_step") is not None:
            steps.add(evidence["action_step"])
        steps.update(
            entry["step_number"]
            for entry in entries
            if entry.get("kind") == "action" and entry.get("step_number") is not None
        )
        scenario_shots = {
            _shot(path) for path in scenario.get("screenshots") or [] if path
        }
        if not steps:
            steps.update(
                action.get("step_number")
                for action in actions
                if _shot((action.get("result") or {}).get("screenshot"))
                in scenario_shots
            )
        for step in sorted(steps, key=lambda value: str(value)):
            owners_by_step.setdefault(step, []).append(scenario)
            action = action_by_step.get(step)
            if action is not None:
                shot = _shot((action.get("result") or {}).get("screenshot"))
                if shot:
                    owners_by_shot.setdefault(shot, []).append(scenario)
                if not native_timeline:
                    entries.extend(_action_entries(action, screenshots))
        if not steps:
            for shot in scenario_shots:
                owners_by_shot.setdefault(shot, []).append(scenario)
        if not entries:
            # Metadata on old CTs may lack the original action timestamps. Do
            # not reuse documentation created_at as if it were execution time.
            if evidence.get("action"):
                entries.append(
                    {
                        "id": f"{scenario.get('id')}-legacy-action",
                        "kind": "action",
                        "occurred_at": evidence.get("action_started_at"),
                        "title": f"Ação registrada: {evidence['action']}",
                        "message": evidence.get("message")
                        or "Horário da ação não registrado.",
                        "url": evidence.get("before_url") or scenario.get("url"),
                    }
                )
            for index, assertion in enumerate(evidence.get("assertions") or []):
                entries.append(
                    {
                        "id": f"{scenario.get('id')}-legacy-assertion-{index}",
                        "kind": "assertion",
                        "occurred_at": evidence.get("action_finished_at"),
                        "title": "Verificação registrada",
                        "message": assertion.get("message") or "",
                        "url": evidence.get("after_url") or scenario.get("url"),
                        "assertion": assertion,
                    }
                )
        captured = {_shot(entry.get("screenshot")) for entry in entries}
        for shot in scenario_shots - captured:
            record = next((s for s in screenshots if _shot(s.get("path")) == shot), {})
            entries.append(
                {
                    "id": f"{scenario.get('id')}-capture-{shot}",
                    "kind": "observation",
                    "occurred_at": record.get("created_at"),
                    "title": "Captura de evidência",
                    "message": record.get("label") or "",
                    "url": scenario.get("url"),
                    "screenshot": shot,
                }
            )
        signals: dict[str, dict[str, Any]] = {}
        for entry in entries:
            if entry.get("kind") == "signal" and isinstance(entry.get("signal"), dict):
                signal = classify_signal(entry["signal"], app_url)
                signal_id = str(
                    signal.get("occurrence_id") or signal_occurrence_id(signal)
                )
                signal["occurrence_id"] = signal_id
                entry["signal"] = signal
                signals[signal_id] = signal
        for raw_signal in evidence.get("technical_signals") or []:
            signal = classify_signal(raw_signal, app_url)
            signal_id = str(signal.get("occurrence_id") or signal_occurrence_id(signal))
            signal["occurrence_id"] = signal_id
            if signal_id not in signals:
                entries.append(_signal_entry(signal))
                signals[signal_id] = signal
        included_signals.update(signals)
        for identity in signals:
            signal_owners.setdefault(identity, []).append(scenario)
        evidence["technical_signals"] = list(signals.values())
        evidence["timeline"] = entries
        evidence["verdict"] = _verdict(scenario, historical=not native_timeline)

    contexts: dict[str, dict[str, Any]] = {}
    for action in actions:
        result = action.get("result") or {}
        if not is_stale_undispatched_action(result):
            continue
        step = action.get("step_number")
        if step in owners_by_step:
            continue  # A preserved historical CT already owns this attempt.
        key = f"step-{step}"
        entries = _action_entries(action, screenshots)
        for entry in entries:
            if entry.get("kind") == "action":
                entry["title"] = "Automação · referência alterada antes da ação"
                entry["message"] = (
                    "Nenhum comando foi executado neste controle. A página foi observada "
                    "novamente; a recuperação tem tentativa limitada. Não é um CT reprovado "
                    "nem uma aprovação funcional."
                )
                entry["action_dispatch"] = dict(result["evidence"]["action_dispatch"])
        contexts[key] = {
            "id": "CTX-" + hashlib.sha256(key.encode()).hexdigest()[:10],
            "title": f"Contexto de automação · referência atualizada · etapa {step}",
            "description": "Tentativa não executada, preservada fora dos resultados dos CTs.",
            "status": "INFO", "url": result.get("after_url") or data.get("app_url"),
            "screenshots": [],
            "evidence": {
                "timeline": entries, "technical_signals": [],
                "action_dispatch": dict(result["evidence"]["action_dispatch"]),
                "verdict": {
                    "kind": "context", "label": "Tentativa não executada",
                    "explanation": "Referência obsoleta recusada antes do comando; não comprova defeito da aplicação.",
                },
            },
        }
    for raw in correlate_issues(data.get("issues") or []):
        signal = classify_signal(raw, app_url)
        identity = str(signal.get("occurrence_id") or signal_occurrence_id(signal))
        signal["occurrence_id"] = identity
        if identity in included_signals:
            # The request can gain its console partner after a CT draft was
            # persisted. Do not lose this richer global record or replace the
            # native action attribution with a weaker historical inference.
            for owner in signal_owners.get(identity, []):
                evidence = owner["evidence"]
                for index, current in enumerate(evidence["technical_signals"]):
                    if current.get("occurrence_id") == identity:
                        merged = _merge_signal(current, signal, app_url)
                        evidence["technical_signals"][index] = merged
                        for entry in evidence["timeline"]:
                            if (entry.get("signal") or {}).get(
                                "occurrence_id"
                            ) == identity:
                                entry["signal"] = merged
            continue
        association = signal.get("association") or {}
        if not isinstance(association, dict):
            association = {}
        scenario_id = signal.get("scenario_id") or association.get("scenario_id")
        owner = by_id.get(str(scenario_id)) if scenario_id else None
        step = signal.get("step_number", association.get("step_number"))
        candidates = owners_by_step.get(step, []) if step is not None else []
        if owner is None and len(candidates) == 1:
            owner = candidates[0]
        if owner is None:
            shot_owners = owners_by_shot.get(_shot(signal.get("screenshot")), [])
            unique = {str(item.get("id")): item for item in shot_owners}
            if len(unique) == 1:
                owner = next(iter(unique.values()))
                association = {
                    "kind": "observation_context",
                    "scenario_id": owner.get("id"),
                    "reason": "Mesma captura de observação; não comprova causalidade.",
                }
        if owner is None and _time(signal.get("occurred_at")) is not None:
            timestamp = _time(signal["occurred_at"])
            timed = [
                action
                for action in actions
                if _time((action.get("result") or {}).get("started_at")) is not None
                and _time((action.get("result") or {}).get("finished_at")) is not None
                and _time(action["result"]["started_at"])
                <= timestamp
                <= _time(action["result"]["finished_at"])
            ]
            if len(timed) == 1:
                step = timed[0].get("step_number")
                candidates = owners_by_step.get(step, [])
                if len(candidates) == 1:
                    owner = candidates[0]
                    association = {
                        "kind": "during_action",
                        "step_number": step,
                        "scenario_id": owner.get("id"),
                        "reason": "Ocorrência na janela da ação; correlação temporal, não causalidade confirmada.",
                    }
        if owner is not None:
            signal["association"] = association or {"kind": "observation_context"}
            owner["evidence"]["technical_signals"].append(signal)
            owner["evidence"]["timeline"].append(_signal_entry(signal))
            signal_owners.setdefault(identity, []).append(owner)
        else:
            if step is None:
                matching = [
                    action
                    for action in actions
                    if _shot(signal.get("screenshot"))
                    and _shot((action.get("result") or {}).get("screenshot"))
                    == _shot(signal.get("screenshot"))
                ]
                if len(matching) == 1:
                    step = matching[0].get("step_number")
            key = (
                f"step-{step}"
                if step is not None
                else f"capture-{_shot(signal.get('screenshot')) or 'unlinked'}"
            )
            if key not in contexts:
                title = (
                    f"Navegação de apoio · etapa {step}"
                    if step is not None
                    else "Contexto da execução · preparação ou observação sem CT associado"
                )
                contexts[key] = {
                    "id": "CTX-" + hashlib.sha256(key.encode()).hexdigest()[:10],
                    "title": title,
                    "description": "Evidência preservada sem atribuição segura a um CT funcional. Não é um teste aprovado ou falho.",
                    "status": "INFO",
                    "url": (action_by_step.get(step, {}).get("result") or {}).get(
                        "after_url"
                    )
                    or data.get("app_url"),
                    "screenshots": [],
                    "evidence": {
                        "timeline": _action_entries(action_by_step[step], screenshots)
                        if step in action_by_step
                        else [],
                        "technical_signals": [],
                        "verdict": {
                            "kind": "context",
                            "label": "Contexto, não um CT validado",
                            "explanation": "Sem associação segura a um CT; não altera os resultados dos testes.",
                        },
                    },
                }
            context = contexts[key]
            signal["association"] = {
                "kind": "observation_context",
                "step_number": step,
                "reason": "Sem associação segura a um CT funcional.",
            }
            context["evidence"]["technical_signals"].append(signal)
            context["evidence"]["timeline"].append(_signal_entry(signal))
            signal_owners.setdefault(identity, []).append(context)
        included_signals.add(identity)

    for item in [*scenarios, *contexts.values()]:
        evidence = item["evidence"]
        if (
            evidence.get("verdict", {}).get("kind") == "technical_failure"
            and evidence.get("verdict", {}).get("historical")
            and not evidence["technical_signals"]
        ):
            evidence["verdict"]["explanation"] += (
                " Esta execução antiga registrou a contagem de erros, mas não "
                "armazenou a requisição exata vinculada a esta ação. Os registros "
                "sem vínculo seguro estão nos cartões de contexto."
            )
        evidence["timeline"] = _unique_timeline(evidence["timeline"])
        item["screenshots"] = list(
            dict.fromkeys(
                [
                    *item.get("screenshots", []),
                    *[
                        _shot(entry["screenshot"])
                        for entry in evidence["timeline"]
                        if entry.get("screenshot")
                    ],
                ]
            )
        )
        evidence["timeline_version"] = TIMELINE_VERSION
    data["scenario_contexts"] = list(contexts.values())
    data["timeline_version"] = TIMELINE_VERSION
    data["technical_signal_summary"] = {
        "occurrences": len(included_signals),
        "associated_with_cts": sum(
            len(s["evidence"]["technical_signals"]) for s in scenarios
        ),
        "context_occurrences": sum(
            len(c["evidence"]["technical_signals"]) for c in contexts.values()
        ),
        "confirmed_functional_bugs": None,
    }
    return data


def _signal_entry(signal: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"signal-{signal['occurrence_id']}",
        "kind": "signal",
        "occurred_at": signal.get("occurred_at"),
        "title": signal.get("label") or "Sinal técnico · impacto a investigar",
        "message": signal.get("message") or "",
        "url": signal.get("url"),
        "screenshot": _shot(signal.get("screenshot")) or None,
        "signal": signal,
    }
