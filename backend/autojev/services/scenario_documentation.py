"""Describe executed checks locally; models need not rewrite browser facts.

Input values are deliberately absent here. Field retention checks keep their
protected evidence in the timeline, not in a second human-readable copy.
"""

from __future__ import annotations

from typing import Any

from autojev.domain.models import ActionResult, AgentDecision, Observation
from autojev.services.report_localization import action_label, report_text, report_title


def local_scenario_documentation(
    observation: Observation, decision: AgentDecision, result: ActionResult,
) -> dict[str, Any]:
    hint = decision.scenario_hint or {}
    probe = hint.get("validation_probe") or {}
    probe_title = (
        f"Validação obrigatória: {probe.get('name') or probe.get('label') or 'campo obrigatório'}"
        if probe.get("kind") == "required_field" else None
    )
    def check_description(assertion: Any) -> str:
        message = str(assertion.message or "").strip()
        if message and message.casefold() != "observed":
            return report_text(message)
        return {
            "no_new_browser_errors": "Não surgirem novas falhas técnicas atribuíveis à aplicação.",
            "no_new_application_errors": "Não surgirem novas falhas técnicas atribuíveis à aplicação.",
            "observable_state_change": "A interação produzir uma mudança observável na página.",
        }.get(assertion.kind, f"Verificar a condição {assertion.kind}.")

    checks = list(dict.fromkeys(check_description(a) for a in result.assertions))
    expected = "\n".join(checks) or str(
        hint.get("expected_result") or "Nenhuma condição específica foi declarada."
    )
    observed = [
        f"{'Confirmada' if a.passed else 'Não confirmada'}: {check_description(a)}"
        for a in result.assertions
    ]
    actual = "\n".join(observed) or report_text(result.message) or f"O navegador terminou em {result.after_url}"
    if not result.success:
        actual = f"A ação não foi concluída: {result.message}\n{actual}"
    target = decision.target or decision.url
    action = action_label(decision.action) + (f" em {target}" if target else " na página observada")
    return {
        "title": report_title(probe_title or hint.get("title") or
                              f"Explore {decision.action} on {observation.title or observation.url}")[:300],
        "description": report_text(hint.get("description") or decision.rationale)[:3000],
        "preconditions": [report_text(item)[:500] for item in hint.get("preconditions", []) if item][:20],
        "steps": [action, *(f"Verificar: {message}" for message in checks)][:50],
        "expected_result": report_text(expected)[:3000],
        "actual_result": actual[:3000],
    }
