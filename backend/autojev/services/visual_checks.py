"""Guard visual-model findings before they can become negative QA evidence.

The model never decides a CT status. A conservative local schema and evidence
checks admit only concrete, high-confidence observations in an image actually
sent in this call. No finding (or no image) is never a blanket visual PASS.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)


class _Region(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)
    y: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)
    width: float = Field(gt=0, le=1, allow_inf_nan=False, strict=True)
    height: float = Field(gt=0, le=1, allow_inf_nan=False, strict=True)

    @model_validator(mode="after")
    def contained(self) -> _Region:
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("The evidence region extends outside the supplied image")
        return self


class _Evidence(BaseModel):
    model_config = ConfigDict(extra="ignore")
    image_ref: Literal["before", "after"]
    region: _Region
    page_url: str = Field(min_length=1, max_length=2000)
    element_label: str = Field(min_length=2, max_length=200)
    expected_basis: Literal[
        "visible_text",
        "same_item_before_after",
        "geometric_occlusion",
        "content_clipped",
        "resource_failure",
        "explicit_requirement",
    ]
    expected_excerpt: str = Field(default="", max_length=1000)


class _Finding(BaseModel):
    model_config = ConfigDict(extra="ignore")
    category: Literal[
        "overlap",
        "clipped_content",
        "broken_image",
        "unreadable_text",
        "missing_expected_content",
        "image_content_mismatch",
        "layout_disruption",
    ]
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)
    expected: str = Field(min_length=12, max_length=1500)
    observed: str = Field(
        min_length=20,
        max_length=2000,
        validation_alias=AliasChoices("observed", "description"),
    )
    evidence: _Evidence


_CATEGORY_LABELS = {
    "overlap": "Sobreposição de conteúdo",
    "clipped_content": "Conteúdo cortado",
    "broken_image": "Imagem não carregada",
    "unreadable_text": "Texto visualmente ilegível",
    "missing_expected_content": "Conteúdo esperado ausente",
    "image_content_mismatch": "Imagem incompatível com o conteúdo identificado",
    "layout_disruption": "Possível quebra de layout",
}


def visual_finding_schema() -> dict[str, Any]:
    """Use the same finding contract for provider output and local admission."""
    return _Finding.model_json_schema()


def _normal_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _objective_basis(
    finding: dict[str, Any],
    *,
    reference_text: str,
    before_text: str,
    supplied_images: dict[str, dict[str, Any]],
    interactive_elements: list[dict[str, Any]] | None = None,
) -> str | None:
    evidence = finding["evidence"]
    category = finding["category"]
    basis = evidence["expected_basis"]
    label = _normal_text(evidence["element_label"])
    excerpt = _normal_text(evidence["expected_excerpt"])
    reference = _normal_text(reference_text)
    # Our floating status UI can be present in evidence screenshots. The
    # instruction to ignore it is not a trust boundary: reject these claims
    # locally, including when the model names the obscured application control
    # rather than naming the overlay itself. This is uncertainty, not a site
    # failure. Do not reject legitimate application overlays in general.
    claim = " ".join(
        _normal_text(value)
        for value in (
            evidence["element_label"],
            evidence["expected_excerpt"],
            finding["expected"],
            finding["observed"],
        )
    )
    if re.search(
        r"\bauto[\s-]?jev\b|\bjev\s+(?:floating\s+|status\s+|side\s+)?(?:panel|overlay)\b"
        r"|\bpainel\s+(?:(?:flutuante|lateral)\s+)?(?:do\s+)?jev\b",
        claim,
    ):
        return "autojev_overlay_is_not_application_evidence"
    if category == "missing_expected_content" and interactive_elements:
        # A model can miss a small button in a low-resolution capture. The
        # browser's independent viewport/hit-test evidence contradicts an
        # absent-control claim, but it does not establish a visual PASS. An
        # offscreen, obstructed or merely named DOM node is not that evidence.
        for element in interactive_elements:
            if not isinstance(element, dict):
                continue
            if (
                element.get("in_viewport") is not True
                or element.get("interactable") is not True
                or element.get("occluded") is True
                or element.get("obstructed_by_overlay") is True
                or str(element.get("aria_hidden") or "").casefold() == "true"
            ):
                continue
            names = {
                _normal_text(element.get(key))
                for key in ("name", "text", "aria_label")
                if element.get(key)
            }
            if label and label in names:
                return "missing_control_contradicted_by_browser_evidence"
    # A named visual area can describe a clipped region, but a semantic claim
    # about an incorrect/missing product needs an actual textual identity.
    if category in {"image_content_mismatch", "missing_expected_content"}:
        if basis not in {
            "visible_text",
            "same_item_before_after",
            "explicit_requirement",
        }:
            return "objective_product_identity_missing"
        if len(excerpt) < 8 or excerpt not in reference or label not in reference:
            return "expected_identity_not_found_in_supplied_text"
        if basis == "explicit_requirement":
            # No user baseline/requirement is currently supplied to this API.
            # Model wording cannot manufacture one.
            return "explicit_visual_requirement_not_supplied"
        if basis == "same_item_before_after" and (
            "before" not in supplied_images or label not in _normal_text(before_text)
        ):
            return "same_item_baseline_not_available"
    elif basis == "explicit_requirement":
        return "explicit_visual_requirement_not_supplied"
    elif basis == "same_item_before_after":
        if (
            "before" not in supplied_images
            or not before_text
            or label not in _normal_text(before_text)
        ):
            return "same_item_baseline_not_available"
    if category == "layout_disruption" and basis not in {
        "geometric_occlusion",
        "content_clipped",
    }:
        return "layout_preference_is_not_a_test_baseline"
    if category == "overlap" and basis != "geometric_occlusion":
        return "overlap_requires_obscured_content_evidence"
    if category == "clipped_content" and basis != "content_clipped":
        return "clipping_requires_cut_content_evidence"
    if category == "broken_image" and basis != "resource_failure":
        return "broken_image_requires_visible_loading_failure"
    return None


def assess_visual_findings(
    raw: Any,
    *,
    images: list[dict[str, Any]],
    model: str,
    reference_text: str,
    before_text: str = "",
    action_succeeded: bool = True,
    interactive_elements: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return an assessment and only guarded *negative* visual assertions."""

    assessment: dict[str, Any] = {
        "requested": True,
        "status": "assessed",
        "source": "visual_model",
        "model": model,
        "images": images,
        "findings": [],
        "accepted_count": 0,
        "uncertain_count": 0,
        "rejected_count": 0,
        "limitations": [
            "Revisão baseada somente nas capturas enviadas, em baixa resolução.",
            "Confiança declarada pelo modelo não é uma medida de precisão nem confirmação de bug.",
            "Ausência de achados não significa aprovação visual completa; não há baseline de design.",
        ],
    }
    if not any(image.get("ref") == "after" and image.get("screenshot") and image.get("url") for image in images):
        assessment.update(status="unavailable", reason="after_screenshot_not_available")
        return assessment, []
    if not isinstance(raw, dict) or not isinstance(raw.get("findings"), list):
        assessment.update(status="unavailable", reason="visual_assessment_not_returned")
        return assessment, []
    if len(raw["findings"]) > 12:
        assessment.update(
            status="unavailable", reason="visual_assessment_schema_invalid"
        )
        assessment["rejected_count"] = len(raw["findings"])
        return assessment, []
    supplied_images = {image["ref"]: image for image in images}
    assertions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in raw["findings"]:
        try:
            finding = _Finding.model_validate(value).model_dump()
        except ValidationError:
            assessment["rejected_count"] += 1
            continue
        evidence = finding["evidence"]
        image = supplied_images.get(evidence["image_ref"])
        reason: str | None = None
        if image is None:
            reason = "referenced_image_not_sent"
        elif evidence["page_url"] != image.get("url"):
            reason = "finding_page_does_not_match_image"
        elif evidence["image_ref"] != "after":
            reason = "before_image_is_context_not_current_result"
        elif not action_succeeded:
            reason = "action_not_completed_visual_context_only"
        elif finding["confidence"] < 0.9:
            reason = "visual_confidence_below_threshold"
        else:
            reason = _objective_basis(
                finding,
                reference_text=reference_text,
                before_text=before_text,
                supplied_images=supplied_images,
                interactive_elements=interactive_elements,
            )
        identity = hashlib.sha256(
            json.dumps(
                {
                    "finding": finding,
                    "screenshot": image.get("screenshot") if image else None,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()[:20]
        if identity in seen:
            continue
        seen.add(identity)
        finding.update(
            id=f"visual-{identity}",
            label=_CATEGORY_LABELS[finding["category"]],
            source="visual_model",
            accepted=reason is None,
            decision_reason=reason or "concrete_high_confidence_visual_evidence",
            confirmed_functional_bug=False,
            screenshot=image.get("screenshot") if image else None,
        )
        assessment["findings"].append(finding)
        if reason:
            assessment["uncertain_count"] += 1
            continue
        assessment["accepted_count"] += 1
        assertions.append(
            {
                "kind": "visual_model",
                "expected": {
                    "criterion": finding["expected"],
                    "category": finding["category"],
                    "source": "visual_model",
                    "evidence_basis": evidence["expected_basis"],
                },
                "actual": {
                    "observed": finding["observed"],
                    "confidence": finding["confidence"],
                    "image_ref": evidence["image_ref"],
                    "region": evidence["region"],
                    "screenshot": image["screenshot"],
                    "url": image["url"],
                    "source": "visual_model",
                    "confirmed_functional_bug": False,
                    "finding_id": finding["id"],
                },
                "passed": False,
                "message": f"Indício visual de alta confiança · {finding['label']}: {finding['observed']}",
            }
        )
    if assessment["rejected_count"] and not assessment["findings"]:
        assessment.update(status="unavailable", reason="visual_assessment_schema_invalid")
        return assessment, []
    assessment["reason"] = (
        "visual_findings_require_review"
        if assertions
        else "visual_findings_uncertain"
        if assessment["uncertain_count"]
        else "visual_findings_invalid"
        if assessment["rejected_count"]
        else "no_visual_deviation_reported"
    )
    return assessment, assertions


def visual_unavailability_label(reason: str) -> str:
    if reason.startswith("auxiliary_rate_limit"):
        return "limitados pelo provedor"
    return {
        "documentation_time_limit": "sem revisão antes do limite de espera das revisões auxiliares",
        "TimeoutError": "com tempo de resposta da LLM auxiliar excedido",
        "auxiliary_timeout": "com tempo de resposta da LLM auxiliar excedido",
        "auxiliary_provider_pause_exceeds_budget": "com espera exigida pelo provedor maior que o limite desta execução",
        "auxiliary_request_budget_exhausted": "sem revisão após esgotar o orçamento de chamadas auxiliares",
        "visual_assessment_not_returned": "com resposta visual ausente ou inválida",
        "visual_assessment_schema_invalid": "com resposta visual ausente ou inválida",
        "visual_findings_invalid": "com resposta visual ausente ou inválida",
        "auxiliary_response_invalid": "com resposta auxiliar ausente ou inválida",
        "auxiliary_response_schema_invalid": "com resposta auxiliar ausente ou inválida",
        "screenshot_not_available": "sem captura disponível para a revisão visual",
        "after_screenshot_not_available": "sem captura posterior à ação disponível",
        "vision_rejected_text_fallback": "com entrada de imagem recusada pelo modelo",
        "auxiliary_authentication_rejected": "com autenticação do provedor recusada",
        "auxiliary_provider_error": "com falha na requisição ao provedor",
        "auxiliary_unavailable": "com provedor indisponível",
        "auxiliary_backlog_full": "sem revisão por fila auxiliar cheia",
        "exploration_cancelled": "sem revisão devido ao cancelamento da execução",
        "backend_shutdown": "sem revisão devido ao encerramento do backend",
        "documentation_interrupted": "com revisão auxiliar interrompida",
        "auxiliary_worker_stopped": "com processamento auxiliar interrompido",
        "auxiliary_worker_unavailable": "sem processamento auxiliar disponível",
        "local_evidence_mode": "preservados somente com evidência local",
    }.get(reason, "sem revisão visual válida; motivo não classificado")


def visual_review_counts(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, Any] = {"total": len(scenarios), "assessed": 0, "pending": 0, "unavailable": 0, "rate_limited": 0, "incomplete_response": 0}
    unavailable_reasons: dict[str, int] = {}
    for scenario in scenarios:
        visual = (scenario.get("evidence") or {}).get("visual_assessment") or {}
        reason = str(visual.get("reason") or "")
        if visual.get("status") == "assessed" and reason != "visual_findings_invalid":
            counts["assessed"] += 1
        elif visual.get("status") == "pending":
            counts["pending"] += 1
        else:
            counts["unavailable"] += 1
            reason = reason or "reason_not_recorded"
            unavailable_reasons[reason] = unavailable_reasons.get(reason, 0) + 1
            if reason.startswith("auxiliary_rate_limit"):
                counts["rate_limited"] += 1
            if reason in {"visual_assessment_not_returned", "visual_assessment_schema_invalid", "visual_findings_invalid"}:
                counts["incomplete_response"] += 1
    # Every unavailable CT has exactly one recorded reason. Do not attribute
    # timeouts/local tail limits to provider throttling or silently omit them.
    counts["unavailable_reasons"] = [
        {"reason": reason, "count": count, "label": visual_unavailability_label(reason)}
        for reason, count in sorted(unavailable_reasons.items())
    ]
    return counts


def unavailable_visual_assessment(
    *,
    requested: bool,
    model: str,
    reason: str,
    images: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "requested": requested,
        "status": "unavailable" if requested else "disabled",
        "source": "visual_model",
        "model": model,
        "reason": reason,
        "images": images or [],
        "findings": [],
        "accepted_count": 0,
        "uncertain_count": 0,
        "rejected_count": 0,
        "limitations": [
            "Nenhuma validação visual foi concluída; o resultado permanece baseado nas verificações locais."
        ],
    }
