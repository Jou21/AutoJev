from __future__ import annotations

import base64
import html
import json
import math
import re
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from autojev.services.scenario_timeline import prepare_scenario_timelines
from autojev.services.scenario_step_order import presentation_scenario_steps
from autojev.services.rerun_projection import without_rerun_recipes
from autojev.services.coverage_ledger import coverage_restriction_ledger
from autojev.services.execution_summary import formatted_execution_duration, report_snapshot_key
from autojev.services.visual_checks import visual_review_counts, visual_unavailability_label
from autojev.services.verification_summary import NON_BUSINESS_CHECKS, verification_summary
from autojev.services.report_localization import (
    ACTION_LABELS, CHECK_LABELS, REPORT_LOCALE_VERSION, action_label, check_label, localize_report_presentation, report_text,
)
from autojev.adapters.report_premium import render_premium_report


def _h(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


RESTRICTION_REASONS = {
    "no_safe_restore_route": "Não foi encontrada uma rota segura para retomar este estado",
    "restore_without_progress": "A retomada repetiu o estado sem progresso",
    "replay_unavailable": "A sequência conhecida não pôde ser reproduzida",
    "replay_without_progress": "A sequência reproduzida não recuperou o estado esperado",
    "action_failed": "A ação não pôde ser executada",
    "candidate_unavailable": "Controle indisponível neste estado",
    "document_navigation_failed": "A navegação direta não carregou um documento válido",
    "restore_step_limit": "A retomada atingiu o limite de segurança sem recuperar o estado",
    "restore_attempt_limit": "As tentativas de retomar este estado foram esgotadas",
    "backtrack_attempt_limit": "As tentativas de voltar a este estado foram esgotadas",
    "policy_blocked": "A política de segurança não permitiu executar esta ação",
    "action_retry_exhausted": "A ação continuou indisponível após as tentativas permitidas",
    "form_validation_precondition_failed": "Não foi possível preparar os demais campos para isolar esta validação",
    "required_field_validation_masked": "A tentativa foi executada, mas a resposta de outro campo impediu validar este campo obrigatório",
    "form_probe_target_not_empty": "O campo continuou preenchido após as tentativas de limpeza; a validação de campo vazio não pôde ser isolada",
}

# Presentation only: never turn these descriptions into new assertions or
# copy protected field values into a second human-readable summary.
CHECK_EXPLANATIONS = {
    "selection_matches": "Conferir se a opção escolhida corresponde à solicitada.",
    "product_sort_order": "Conferir a ordem dos produtos, sem perder ou acrescentar itens.",
    "no_new_browser_errors": "Conferir novos erros atribuídos à aplicação durante a ação.",
    "no_new_application_errors": "Conferir novos erros atribuídos à aplicação durante a ação.",
    "observable_state_change": "Conferir se a interação produziu uma mudança na página.",
    "field_value_matches": "Conferir se o campo manteve o valor preenchido.",
    "field_retains_value": "Conferir se o campo manteve o valor preenchido.",
    "other_fields_unchanged": "Conferir se os outros campos foram preservados.",
    "visual_model": "Revisar uma possível diferença apontada nas imagens; não confirma um defeito funcional sozinha.",
}


class StandaloneHtmlReport:
    """Renders a self-contained, escaped HTML report with inline PNG evidence."""

    _GENERIC_RESOURCE_ERROR = re.compile(
        r"failed to load resource:.*?status(?:\s+(?:of|code))?\s+(\d{3})",
        re.IGNORECASE,
    )

    def __init__(self, artifact_root: Path) -> None:
        self.artifact_root = artifact_root.resolve()

    def _image_path(self, exploration_id: str, relative_path: str) -> Path | None:
        candidate = (
            self.artifact_root
            / exploration_id
            / "screenshots"
            / Path(relative_path).name
        ).resolve()
        allowed = (self.artifact_root / exploration_id / "screenshots").resolve()
        if allowed not in candidate.parents or not candidate.is_file():
            return None
        return candidate

    def _image_data(self, exploration_id: str, relative_path: str) -> str | None:
        candidate = self._image_path(exploration_id, relative_path)
        if candidate is None:
            return None
        mime = {".png": "image/png", ".webp": "image/webp"}.get(candidate.suffix.lower(), "image/jpeg")
        return f"data:{mime};base64,{base64.b64encode(candidate.read_bytes()).decode('ascii')}"

    def _screenshots(self, exploration_id: str, paths: list[str], *, image_label: str = "Captura de evidência") -> str:
        images: list[str] = []
        for path in paths:
            data = self._image_data(exploration_id, path)
            if data:
                images.append(
                    f'<figure><img loading="lazy" src="{data}" alt="{_h(image_label)}"><figcaption>{_h(Path(path).name)}</figcaption></figure>'
                )
        return "".join(images) or '<p class="muted">Nenhuma captura associada.</p>'

    @staticmethod
    def _preview_time(value: object) -> float | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            return timestamp.timestamp()
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _preview_selection(value: object) -> dict[str, Any] | None:
        """Expose recorded option labels, never raw values or arbitrary DOM data."""
        if (not isinstance(value, dict) or value.get("version") != "select-labels-v1"
                or not isinstance(value.get("status"), str)
                or value["status"] not in {"confirmed", "mismatch", "unverified"}):
            return None
        labels: dict[str, str] = {}
        for key in ("requested_label", "before_label", "after_label"):
            label = value.get(key)
            if (isinstance(label, str) and label.strip() and len(label) <= 240
                    and not any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in label)):
                labels[key] = label.strip()
        if not labels:
            return None
        status = value["status"]
        # Missing/invalid labels must not turn a partial capture into a claim
        # that the requested selection was actually observed after the action.
        if status != "unverified" and not all(key in labels for key in ("requested_label", "after_label")):
            status = "unverified"
        return {"version": "select-labels-v1", **labels, "status": status}

    @staticmethod
    def _selection_summary(selection: dict[str, Any] | None) -> str:
        if not selection:
            return ""
        items = "".join(
            '<div class="action-selection-item">'
            f'<span>{label}</span><strong>{_h(selection[key])}</strong></div>'
            for key, label in (
                ("requested_label", "Opção solicitada"),
                ("before_label", "Antes da ação"),
                ("after_label", "Selecionada depois"),
            ) if key in selection
        )
        statuses = {
            "confirmed": "Seleção confirmada",
            "mismatch": "Seleção diferente da solicitada",
            "unverified": "Seleção não confirmada",
        }
        return (
            f'<div class="action-selection-summary" data-selection-status="{selection["status"]}">'
            f'{items}<p class="action-selection-status">{statuses[selection["status"]]}</p></div>'
        )

    @staticmethod
    def _preview_geometry(metadata: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any] | None:
        """Keep only executor-owned, finite CSS-pixel coordinates; never infer a target."""
        action = metadata.get("action")
        if (metadata.get("version") != "action-target-v1"
                or metadata.get("coordinate_space") != "screenshot-css-pixels"
                or not isinstance(action, str)
                or action not in {"click", "fill", "select", "press", "hover"}):
            return None
        image, box = metadata.get("image"), metadata.get("box")
        if not isinstance(image, dict) or not isinstance(box, dict):
            return None

        def number(value: object) -> bool:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return False
            try:
                return math.isfinite(value)
            except OverflowError:
                return False

        if not all(number(image.get(key)) for key in ("width", "height")):
            return None
        if not all(number(box.get(key)) for key in ("x", "y", "width", "height")):
            return None
        width, height = image["width"], image["height"]
        x, y, box_width, box_height = (box[key] for key in ("x", "y", "width", "height"))
        if (width <= 0 or height <= 0 or box_width <= 0 or box_height <= 0
                or x < 0 or y < 0 or x + box_width > width or y + box_height > height):
            return None
        target = metadata.get("target")
        if (not isinstance(target, str) or not target or len(target) > 240
                or any(ord(character) < 32 for character in target)
                or entry.get("target") and target != entry["target"]
                or entry.get("url") and metadata.get("url") != entry["url"]):
            return None
        # Raw values, DOM content, URLs and arbitrary capture metadata must not
        # be copied to an HTML data attribute. Only bounded, recorded option
        # labels are allowed alongside geometry and the ref for select actions.
        geometry = {
            "version": "action-target-v1", "coordinate_space": "screenshot-css-pixels",
            "action": metadata["action"], "target": target,
            "image": {key: image[key] for key in ("width", "height")},
            "box": {key: box[key] for key in ("x", "y", "width", "height")},
        }
        selection = StandaloneHtmlReport._preview_selection(metadata.get("selection")) if action == "select" else None
        if selection:
            geometry["selection"] = selection
        return geometry

    def _preview_filename(self, exploration_id: str, entry: dict[str, Any]) -> str | None:
        """Return only an existing local capture tied to this action's page."""
        metadata = entry.get("action_preview")
        if not isinstance(metadata, dict):
            return None
        screenshot = metadata.get("screenshot")
        if (metadata.get("version") != "action-target-v1"
                or not isinstance(screenshot, str)
                or screenshot != Path(screenshot).name
                or "\\" in screenshot or "\x00" in screenshot
                or entry.get("url") and metadata.get("url") != entry["url"]
                or Path(screenshot).suffix.lower() not in {".png", ".jpg", ".jpeg"}):
            return None
        candidate = (self.artifact_root / exploration_id / "screenshots" / screenshot).resolve()
        allowed = (self.artifact_root / exploration_id / "screenshots").resolve()
        return screenshot if allowed in candidate.parents and candidate.is_file() else None

    def _action_preview(
        self,
        exploration_id: str,
        entry: dict[str, Any],
        *,
        preceding: list[dict[str, Any]],
        canonical_shots: set[str],
    ) -> str:
        metadata = entry.get("action_preview")
        metadata = metadata if isinstance(metadata, dict) else {}
        filename = self._preview_filename(exploration_id, entry) or ""
        geometry = None
        if filename:
            geometry = self._preview_geometry(metadata, entry)
        if not filename:
            # Historical captures can show the state before an action, but
            # cannot reconstruct where it was dispatched. Stay inside this CT,
            # require timing and the same URL, and never use an after-capture.
            action_time = self._preview_time(entry.get("occurred_at"))
            action_url = entry.get("url")
            for candidate in reversed(preceding):
                if candidate.get("kind") == "action":
                    break
                captured = self._preview_time(candidate.get("occurred_at"))
                if (candidate.get("kind") == "observation" and candidate.get("screenshot")
                        and isinstance(action_url, str) and action_url
                        and candidate.get("url") == action_url
                        and captured is not None and action_time is not None
                        and captured <= action_time):
                    filename = Path(str(candidate["screenshot"])).name
                    break
        if not filename:
            return ""
        candidate = (self.artifact_root / exploration_id / "screenshots" / filename).resolve()
        allowed = (self.artifact_root / exploration_id / "screenshots").resolve()
        if allowed not in candidate.parents or not candidate.is_file():
            return ""
        # The original capture is already embedded in a canonical timeline
        # observation in most cases. Reference it instead of duplicating the
        # base64 data for each action. The offline report hydrates this ref.
        data = self._image_data(exploration_id, filename) if filename not in canonical_shots else None
        image_source = f' src="{data}"' if data else ""
        payload = json.dumps(geometry or {}, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        note = (
            "O destaque indica o controle registrado imediatamente antes da interação."
            if geometry else "Posição do alvo não registrada nesta execução."
        )
        selection_summary = self._selection_summary(geometry.get("selection") if geometry else None)
        return (
            '<div class="shots action-preview-shots"><figure class="action-preview" '
            f'data-action-preview="{_h(payload)}">'
            '<div class="action-preview-heading"><strong>Alvo da ação</strong>'
            '<small>Captura anterior à interação</small></div>'
            f'{selection_summary}'
            '<div class="action-preview-frame">'
            f'<img class="action-preview-image" loading="lazy" data-source-shot="{_h(filename)}"'
            f'{image_source} alt="Captura anterior à ação; {_h(note)}"></div>'
            f'<figcaption>{_h(filename)}</figcaption>'
            f'<p class="action-preview-note">{_h(note)}</p></figure></div>'
        )

    @staticmethod
    def _timeline_entry_id(scenario: dict[str, Any], index: int) -> str:
        identity = str(scenario.get("id") or "scenario").encode("utf-8").hex()
        return f"timeline-{identity}-{index}"

    def _action_anchor(self, step: Any, scenarios: list[dict[str, Any]], *, exploration_id: str | None = None) -> str | None:
        if not isinstance(step, int) or isinstance(step, bool) or step < 1:
            return None
        for scenario in scenarios:
            if exploration_id is not None and str(scenario.get("evidence_exploration_id") or exploration_id) != exploration_id:
                continue
            for index, entry in enumerate(presentation_scenario_steps(scenario.get("evidence") or {})):
                if (entry.get("kind") == "action"
                        and entry.get("step_number", entry.get("action_step")) == step):
                    return self._timeline_entry_id(scenario, index)
        return None

    def _restriction_dependency(
        self, exploration_id: str, item: dict[str, Any], scenarios: list[dict[str, Any]],
    ) -> str:
        dependency = item.get("dependency")
        if (not isinstance(dependency, dict)
                or dependency.get("kind") != "failed_inverse_cart_toggle"
                or dependency.get("source") != "actual_business_assertion"
                or not dependency.get("failed_assertion_kinds")):
            return ""
        step = dependency.get("action_step")
        anchor = self._action_anchor(step, scenarios, exploration_id=exploration_id)
        step_note = (
            f'<a href="#{anchor}">Ver falha anterior da etapa {_h(step)} na linha do tempo</a>'
            if anchor else f'Etapa {_h(step)} · ação anterior registrada' if step is not None else
            'Ação anterior registrada sem número de etapa'
        )
        screenshot = str(dependency.get("screenshot") or "")
        image = self._image_data(exploration_id, screenshot) if screenshot else None
        return (
            '<div class="coverage-dependency"><p><strong>Dependência que impediu esta validação</strong></p>'
            '<p>A remoção anterior deste item falhou em uma verificação de negócio. '
            'Isso impediu recuperar o estado necessário para testar sua adição; '
            'a restrição permanece aberta e não conta como aprovação.</p>'
            + '<p>' + step_note + ' · ' + _h(action_label(dependency.get("action")))
            + ' · Controle: ' + _h(dependency.get("name") or "Remove") + '</p>'
            + '<p>Verificações que reprovaram: ' + _h(
                ", ".join(check_label(kind) for kind in dependency["failed_assertion_kinds"])) + '</p>'
            + '<div class="url">' + _h(self._public_issue_url(dependency.get("source_url"))) + '</div>'
            + ('<div class="shots">' + self._screenshots(exploration_id, [screenshot]) + '</div>'
               + f'<a href="{image}" download="{_h(Path(screenshot).name)}">Baixar captura da ação bloqueadora</a>'
               if image else '')
            + '</div>'
        )

    def _restriction_card(
        self, exploration_id: str, item: dict[str, Any], scenarios: list[dict[str, Any]],
    ) -> str:
        probe = item.get("validation_probe") or {}
        field_name = item.get("field_name") or probe.get("name")
        title = (str(field_name) + " · " if field_name else "") + str(
            item.get("name") or item.get("target") or "Controle sem nome"
        )
        blocking_fields = probe.get("blocking_fields") or []
        return (
            '<article class="context-note"><strong>' + _h(title) + '</strong><p>'
            + _h(RESTRICTION_REASONS.get(item.get("reason"), item.get("reason") or "Motivo não informado"))
            + '</p>'
            + ('<p>Campos que impediram a preparação: ' + _h(
                ", ".join(str(field.get("name") or "Campo sem nome") for field in blocking_fields
                          if isinstance(field, dict))) + '</p>' if blocking_fields else '')
            + '<div class="url">' + _h(self._public_issue_url(item.get("source_url") or item.get("target_url")))
            + '</div><small>' + _h(action_label(item.get("action"))) + '</small>'
            + ('<div class="url">Estado a retomar: ' + _h(self._public_issue_url(item["target_url"])) + '</div>'
               if item.get("target_url") and item.get("target_url") != item.get("source_url") else '')
            + self._restriction_dependency(exploration_id, item, scenarios)
            + '</article>'
        )

    def _resolution_card(
        self, exploration_id: str, item: dict[str, Any], scenarios: list[dict[str, Any]],
    ) -> str:
        resolution = item["resolution"]
        step = resolution["action_step"]
        anchor = self._action_anchor(step, scenarios, exploration_id=exploration_id)
        screenshot = str(resolution.get("screenshot") or "")
        image = self._image_data(exploration_id, screenshot) if screenshot else None
        result_note = (
            "Uma verificação reprovou; consulte o resultado do CT. A cobertura foi exercitada, não aprovada."
            if resolution.get("assertions_passed") is False else
            "A ação foi executada; o resultado do CT permanece preservado."
        )
        return (
            '<article class="context-note coverage-resolution"><strong>'
            + _h(item.get("field_name") or item.get("name") or item.get("target") or "Controle sem nome")
            + '</strong><p>Restrição de retomada resolvida com execução observada.</p>'
            + '<p>Motivo original: ' + _h(RESTRICTION_REASONS.get(item.get("reason"), item.get("reason") or "Motivo não informado")) + '</p>'
            + '<p>' + _h(result_note) + '</p>'
            + '<p>' + (f'<a href="#{anchor}">Ver ação da etapa {_h(step)} na linha do tempo</a>'
                       if anchor else f'Etapa {_h(step)} · ação registrada')
            + ' · ' + _h(action_label(resolution.get("action"))) + ' · Alvo: ' + _h(resolution.get("target")) + '</p>'
            + '<time datetime="' + _h(resolution.get("occurred_at")) + '">' + _h(resolution.get("occurred_at")) + '</time>'
            + '<div class="url">' + _h(self._public_issue_url(resolution.get("source_url"))) + '</div>'
            + ('<div class="shots">' + self._screenshots(exploration_id, [screenshot]) + '</div>'
               + f'<a href="{image}" download="{_h(Path(screenshot).name)}">Baixar captura da recuperação</a>'
               if image else '<p class="muted">Captura da recuperação indisponível neste relatório.</p>')
            + '</article>'
        )

    @staticmethod
    def _duration(exploration: dict[str, Any]) -> str:
        return formatted_execution_duration(exploration)

    @staticmethod
    def _usage(exploration: dict[str, Any]) -> tuple[str, str]:
        usage = exploration.get("usage") or {}
        tokens = f"{int(usage.get('total_tokens') or 0):,}".replace(",", ".")
        return tokens, StandaloneHtmlReport._formatted_cost(usage)

    @staticmethod
    def _formatted_cost(usage: dict[str, Any] | None) -> str:
        """An unreported price is not zero, including in historical runs."""
        if not isinstance(usage, dict) or not usage.get("cost_known"):
            return "—"
        value = usage.get("cost")
        if value is None or isinstance(value, bool):
            return "—"
        try:
            amount = float(value)
        except (TypeError, ValueError, OverflowError):
            return "—"
        return f"${amount:.6f}" if math.isfinite(amount) and amount >= 0 else "—"

    def _cost_summary(self, exploration: dict[str, Any]) -> str:
        usage = exploration.get("usage") or {}
        by_role = usage.get("by_role")
        roles = by_role if isinstance(by_role, dict) else {}
        metrics: list[str] = []
        for role, label, bucket in (
            ("jev", "Custo JEV", roles.get("jev")),
            ("auxiliary", "Custo LLM", roles.get("auxiliary")),
            ("total", "Custo total", usage),
        ):
            value = self._formatted_cost(bucket)
            partial = isinstance(bucket, dict) and bucket.get("cost_complete") is False
            note = (
                "Valor parcial · soma dos custos informados."
                if partial and value != "—" else
                "Não registrado nesta execução."
                if role != "total" and not isinstance(bucket, dict) else
                "Custo não informado pelo provedor."
                if value == "—" else ""
            )
            metrics.append(
                f'<div class="metric cost-metric" data-cost-role="{role}">'
                f'<strong>{_h(value)}</strong>{label}'
                + ('<span class="cost-state">Parcial</span>' if partial and value != "—" else '')
                + (f'<small>{_h(note)}</small>' if note else '') + '</div>'
            )
        missing_split = any(not isinstance(roles.get(role), dict) for role in ("jev", "auxiliary"))
        return (
            '<section class="cost-summary" aria-label="Custos por modelo" data-cost-breakdown="roles-v1">'
            + "".join(metrics) + '</section>'
            + ('<p class="cost-note muted">Divisão por modelo não registrada nesta execução. '
               'O total disponível foi preservado; não foi estimada uma divisão.</p>' if missing_split else '')
        )

    @staticmethod
    def _completion(exploration: dict[str, Any]) -> tuple[str, str]:
        status = str(exploration.get("status") or "unknown").lower()
        labels = {
            "completed": "Concluída",
            "partial": "Cobertura parcial",
            "failed": "Falhou",
            "cancelled": "Cancelada",
            "canceled": "Cancelada",
            "postprocessing": "Gerando relatório",
        }
        reason = ""
        relevant = {
            "coverage_complete_with_restrictions",
            "coverage_stalled",
            "coverage_partial",
            "limit_reached",
            "coverage_scope_not_found",
            "failed",
            "cancelled",
            "browser_closed",
        }
        for event in reversed(exploration.get("events") or []):
            event_type = str(event.get("type") or "")
            if (
                status in {"cancelled", "canceled"}
                and event_type not in {"cancelled", "browser_closed"}
            ):
                continue
            if event_type in relevant:
                if (
                    status == "completed"
                    and event_type == "coverage_complete_with_restrictions"
                ):
                    labels["completed"] = "Concluída com restrições"
                reason = str(event.get("message") or "").strip()
                break
        if not reason and status == "failed":
            reason = str(exploration.get("error") or "").strip()
        return labels.get(status, status or "Desconhecido"), report_text(reason)

    @staticmethod
    def _public_issue_url(value: object) -> str:
        """Preserve diagnostic routes while defensively masking secret queries."""

        raw = str(value or "").strip()
        if not raw:
            return ""
        try:
            parsed = urlsplit(raw)
            sensitive = {
                "access_token",
                "api_key",
                "apikey",
                "authorization",
                "key",
                "password",
                "secret",
                "signature",
                "token",
            }
            query = urlencode(
                [
                    (
                        key,
                        "[REDACTED]"
                        if key.lower().replace("-", "_") in sensitive
                        else item,
                    )
                    for key, item in parse_qsl(
                        parsed.query, keep_blank_values=True
                    )
                ]
            )
            return urlunsplit(
                (parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment)
            )
        except ValueError:
            return raw

    @classmethod
    def _issue_status_code(cls, issue: dict[str, Any]) -> int | None:
        value = issue.get("status_code")
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                pass
        match = cls._GENERIC_RESOURCE_ERROR.search(str(issue.get("message") or ""))
        return int(match.group(1)) if match else None

    @staticmethod
    def _issue_timestamp(issue: dict[str, Any]) -> float | None:
        value = issue.get("occurred_at")
        if not value:
            return None
        try:
            return datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            ).timestamp()
        except (TypeError, ValueError):
            return None

    @classmethod
    def _group_issues(cls, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Pair console/HTTP evidence without merging distinct failed requests.

        Historical runs persisted Chromium's generic console line and its HTTP
        response as two rows. A single observation can also contain several
        real failures with the same status, so screenshot + status alone is not
        a safe correlation key. Pair each generic line with at most one precise
        HTTP record (closest timestamp) and leave all other requests separate.
        """

        def make_group(issue: dict[str, Any]) -> dict[str, Any]:
            screenshot = str(issue.get("screenshot") or "").strip()
            public_url = cls._public_issue_url(issue.get("url"))
            channels = issue.get("evidence_channels")
            kinds = (
                [str(channel) for channel in channels if channel]
                if isinstance(channels, list)
                else [str(issue.get("type") or issue.get("kind") or "browser")]
            )
            message = str(issue.get("message") or "Sinal observado no navegador")
            return {
                "status_code": cls._issue_status_code(issue),
                "screenshot": screenshot or None,
                "types": list(dict.fromkeys(kinds)),
                "messages": [message],
                "urls": [public_url] if public_url else [],
                "count": max(len(set(kinds)), 1),
            }

        def merge(
            group: dict[str, Any],
            issue: dict[str, Any],
            *,
            include_urls: bool = True,
        ) -> None:
            incoming = make_group(issue)
            group["count"] += incoming["count"]
            for field in ("types", "messages", "urls"):
                if field == "urls" and not include_urls:
                    continue
                for value in incoming[field]:
                    if value not in group[field]:
                        group[field].append(value)

        precise_by_pair: dict[tuple[str, int], list[int]] = {}
        for index, issue in enumerate(issues):
            if str(issue.get("type") or "") != "http":
                continue
            status = cls._issue_status_code(issue)
            if status is None:
                continue
            screenshot = str(issue.get("screenshot") or "").strip()
            precise_by_pair.setdefault((screenshot, status), []).append(index)

        console_to_http: dict[int, int] = {}
        matched_http: set[int] = set()
        for console_index, issue in enumerate(issues):
            message = str(issue.get("message") or "")
            if (
                str(issue.get("type") or "") != "console"
                or not message.lower().startswith("failed to load resource")
                or cls._issue_status_code(issue) is None
            ):
                continue
            status = cls._issue_status_code(issue)
            screenshot = str(issue.get("screenshot") or "").strip()
            candidates = [
                index
                for index in precise_by_pair.get((screenshot, status), [])
                if index not in matched_http
            ]
            if not candidates:
                continue
            console_time = cls._issue_timestamp(issue)
            timed = [
                (abs(console_time - candidate_time), index)
                for index in candidates
                if console_time is not None
                and (candidate_time := cls._issue_timestamp(issues[index])) is not None
            ]
            if timed:
                delta, http_index = min(timed)
                if delta > 2.0:
                    continue
            elif len(candidates) == 1:
                http_index = candidates[0]
            else:
                continue
            console_to_http[console_index] = http_index
            matched_http.add(http_index)

        groups: OrderedDict[int, dict[str, Any]] = OrderedDict()
        consumed_console: set[int] = set()
        for index, issue in enumerate(issues):
            if index in consumed_console:
                continue
            if index in console_to_http:
                http_index = console_to_http[index]
                group = make_group(issues[http_index])
                # Chromium associates the generic console line with the
                # document URL, not necessarily the resource that failed. The
                # paired HTTP response owns the diagnostic request URL.
                merge(group, issue, include_urls=False)
                groups[index] = group
                consumed_console.add(index)
                continue
            if index in matched_http:
                continue
            groups[index] = make_group(issue)
        return list(groups.values())

    @staticmethod
    def _timeline_time(value: object) -> str:
        if not value:
            return "Horário não registrado"
        try:
            timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return timestamp.strftime("%d/%m/%Y %H:%M:%S %Z").strip()
        except (TypeError, ValueError):
            return str(value)

    @classmethod
    def _selection_for_assertion(
        cls, entries: list[dict[str, Any]], index: int,
    ) -> dict[str, Any] | None:
        """Find labels for this exact attempt, never the latest preview of a CT."""
        entry = entries[index]
        if (entry.get("assertion") or {}).get("kind") != "selection_matches":
            return None

        def step(item: dict[str, Any]) -> tuple[bool, int | None]:
            values = [item[key] for key in ("step_number", "action_step") if item.get(key) is not None]
            if not values:
                return False, None
            if (any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in values)
                    or len(set(values)) != 1):
                return True, None
            return True, values[0]

        has_step, assertion_step = step(entry)
        if has_step and assertion_step is None:
            return None
        preceding = [(position, item) for position, item in enumerate(entries[:index]) if item.get("kind") == "action"]
        if not preceding:
            return None
        _, action = preceding[-1]
        if has_step:
            matches = [item for item in entries if item.get("kind") == "action" and step(item) == (True, assertion_step)]
            if len(matches) != 1 or step(action) != (True, assertion_step):
                return None
        elif step(action) == (True, None):
            return None
        # A selection check cannot borrow a preview from a prior dropdown when
        # a later click, restoration or another attempt intervened.
        preview = action.get("action_preview")
        if not isinstance(preview, dict) or preview.get("version") != "action-target-v1" or preview.get("action") != "select":
            return None
        target, url = action.get("target"), action.get("url")
        if (not isinstance(target, str) or not target or preview.get("target") != target
                or entry.get("target") and entry["target"] != target
                or not isinstance(url, str) or not url or preview.get("url") != url or entry.get("url") != url):
            return None
        action_time = cls._preview_time(action.get("occurred_at"))
        assertion_time = cls._preview_time(entry.get("occurred_at"))
        capture_time = cls._preview_time(preview.get("captured_at"))
        if (action_time is None or assertion_time is None or capture_time is None
                or action_time > assertion_time or capture_time > assertion_time):
            return None
        # Ambiguous duplicate action records must not assign a label to a
        # verification from another attempt with the same clock/target.
        if sum(item.get("occurred_at") == action.get("occurred_at") and item.get("target") == target
               for _, item in preceding) != 1:
            return None
        return cls._preview_selection(preview.get("selection"))

    @staticmethod
    def _selection_assertion_detail(
        assertion: dict[str, Any], selection: dict[str, Any] | None,
    ) -> str:
        """Explain a recorded boolean business check without exposing its JSON first."""
        passed = assertion.get("passed")
        result = "pass" if passed is True else "fail" if passed is False else "unknown"
        titles = {
            "pass": "A opção correta foi selecionada",
            "fail": "A seleção não correspondeu ao esperado",
            "unknown": "O resultado desta verificação não foi registrado",
        }
        descriptions = {
            "pass": "O navegador confirmou que a escolha corresponde à opção solicitada pelo teste.",
            "fail": "A verificação não confirmou a opção solicitada. Compare a escolha esperada com o resultado encontrado.",
            "unknown": "Não há um resultado confirmado para dizer se a opção escolhida corresponde à solicitada.",
        }

        def icon(name: str, css_class: str) -> str:
            paths = {
                "check": '<path d="m6 12 4 4 8-8"/>',
                "cross": '<path d="m8 8 8 8m0-8-8 8"/>',
                "question": '<path d="M9 9a3 3 0 0 1 6 0c0 2-3 2-3 4m0 3h.01"/>',
                "choice": '<path d="m7 10 5 5 5-5"/>',
            }
            return (
                f'<svg class="{css_class}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
                '<circle cx="12" cy="12" r="10"/>' + paths[name] + '</svg>'
            )

        selection = selection or {}
        requested = selection.get("requested_label")
        after = selection.get("after_label")
        consistent = (
            result == "pass" and selection.get("status") == "confirmed"
            or result == "fail" and selection.get("status") == "mismatch"
        )
        # The capture snapshot precedes the observation used by the business
        # assertion. Never present its name as the checked result if they disagree.
        actual_label = after if consistent or result == "unknown" else None
        # Historical captures can lack the post-action label while the business
        # check still confirms the requested value. Use its same-attempt label
        # only with the explicit, strictly boolean value-match evidence.
        expected_check, actual_check = assertion.get("expected"), assertion.get("actual")
        confirmed_requested = (
            bool(requested) and not after and selection.get("status") == "unverified"
            and result == "pass"
            and isinstance(expected_check, dict) and expected_check.get("option_matches") is True
            and isinstance(actual_check, dict) and actual_check.get("option_matches") is True
        )
        if confirmed_requested:
            actual_label = requested
        expected_value = requested or "Opção solicitada pelo teste"
        actual_value = actual_label or {
            "pass": "Corresponde à opção esperada",
            "fail": "Não correspondeu à opção esperada",
            "unknown": "Resultado não disponível",
        }[result]
        state_icon = "check" if result == "pass" else "cross" if result == "fail" else "question"
        expected_icon = "check" if result == "pass" else "choice"
        notes = []
        if not requested or not actual_label:
            notes.append("Os nomes das opções não foram registrados ou não puderam ser vinculados com segurança a esta verificação.")
        if after and result != "unknown" and not consistent:
            notes.append("A captura e a verificação representam instantes diferentes; o resultado da verificação foi preservado.")
        if requested and actual_label == requested and result == "fail":
            notes.append("Os textos exibidos são iguais, mas isso não garante a mesma opção: a verificação dos valores internos não passou.")
        if result != "unknown":
            notes.append("Esta verificação compara a opção escolhida; ela não valida, sozinha, os efeitos dessa escolha na página.")
        else:
            notes.append("Os nomes exibidos, sozinhos, não confirmam que a verificação passou.")
        technical = {key: assertion[key] for key in ("kind", "expected", "actual", "passed") if key in assertion}
        raw = json.dumps(technical, ensure_ascii=False, indent=2)
        expected_display = assertion.get("expected_display")
        technical_description = (
            f'<p><span class="muted">Esperado (descrição): </span>{_h(expected_display)}</p>'
            if isinstance(expected_display, str) and expected_display else ""
        )
        expected_caption = "Escolha solicitada pelo teste" if requested else "Nome da opção não disponível"
        actual_caption = {
            "pass": "Confirmada pela verificação no navegador",
            "fail": "Não confirmada pela verificação no navegador",
            "unknown": "Sem confirmação da verificação",
        }[result]
        if confirmed_requested:
            actual_caption = "Opção solicitada confirmada no navegador"
        return (
            f'<section class="assertion-detail selection-verification" data-result="{result}" aria-label="Comparação da opção selecionada">'
            '<div class="selection-verification-outcome">'
            + icon(state_icon, "selection-state-icon")
            + f'<div><h4>{titles[result]}</h4><p>{descriptions[result]}</p></div></div>'
            '<div class="selection-comparison">'
            '<article class="selection-choice" data-side="expected">'
            + icon(expected_icon, "selection-choice-icon")
            + '<small class="selection-choice-label">Opção esperada</small>'
            f'<strong class="selection-choice-value">{_h(expected_value)}</strong>'
            f'<p class="selection-choice-caption">{expected_caption}</p></article>'
            '<div class="selection-comparison-connector">'
            + icon(state_icon, "selection-state-icon")
            + f'<span class="sr-only">{_h(titles[result])}</span></div>'
            '<article class="selection-choice" data-side="actual">'
            + icon(state_icon, "selection-choice-icon")
            + '<small class="selection-choice-label">Opção encontrada</small>'
            f'<strong class="selection-choice-value">{_h(actual_value)}</strong>'
            f'<p class="selection-choice-caption">{actual_caption}</p></article></div>'
            + ''.join(f'<p class="selection-verification-note">{_h(note)}</p>' for note in notes)
            + '<details class="selection-technical"><summary>Ver dados técnicos</summary>'
            + technical_description + f'<pre>{_h(raw)}</pre></details></section>'
        )

    @staticmethod
    def _sort_assertion_detail(assertion: dict[str, Any]) -> str:
        """Compare recorded product sequences without rejudging the business check."""
        passed = assertion.get("passed")
        result = "pass" if passed is True else "fail" if passed is False else "unknown"
        expected = assertion.get("expected")
        actual = assertion.get("actual")
        expected = expected if isinstance(expected, dict) else {}
        actual = actual if isinstance(actual, dict) else {}

        def products(value: object) -> list[str] | None:
            # Never filter malformed entries: that would shift recorded positions.
            if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
                return None
            return value

        wanted, found = products(expected.get("products")), products(actual.get("products"))
        comparable = wanted is not None and found is not None
        same_members = comparable and Counter(wanted) == Counter(found)
        columns = max(len(wanted or []), len(found or []))
        by = expected.get("by") if isinstance(expected.get("by"), str) else None
        descending = expected.get("descending")
        criterion = {
            ("name", False): "Nome: de A a Z", ("name", True): "Nome: de Z a A",
            ("price", False): "Preço: do menor para o maior", ("price", True): "Preço: do maior para o menor",
        }.get((by, descending), "Critério não registrado") if isinstance(by, str) and isinstance(descending, bool) else "Critério não registrado"
        titles = {
            "pass": "Os produtos estão na ordem correta",
            "fail": "A ordenação dos produtos não passou na verificação",
            "unknown": "Ordenação sem resultado confirmado",
        }
        descriptions = {
            "pass": "A verificação confirmou a ordenação e a presença dos mesmos produtos.",
            "fail": "Compare as duas fileiras para identificar mudanças de posição ou de produtos.",
            "unknown": "As sequências registradas podem ser comparadas, mas não há aprovação confirmada.",
        }

        def icon(state: str, css_class: str) -> str:
            path = {
                "match": '<path d="m6 12 4 4 8-8"/>',
                "different": '<path d="m8 8 8 8m0-8-8 8"/>',
                "missing": '<path d="M7 12h10"/>',
                "equivalent": '<path d="M6 9h12m-3-3 3 3-3 3M18 15H6m3-3-3 3 3 3"/>',
                "unverified": '<path d="M9 9a3 3 0 0 1 6 0c0 2-3 2-3 4m0 3h.01"/>',
                "reference": '<path d="M7 8h10M7 12h10M7 16h6"/>',
            }[state]
            return (
                f'<svg class="{css_class}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
                '<circle cx="12" cy="12" r="10"/>' + path + '</svg>'
            )

        equivalent_positions = False
        values = actual.get("values")

        def position_state(position: int, side: str) -> tuple[str, str]:
            items, other = (wanted, found) if side == "expected" else (found, wanted)
            identity = items[position] if items is not None and position < len(items) else None
            counterpart = other[position] if other is not None and position < len(other) else None
            if not comparable or result == "unknown":
                return "unverified", "Sem confirmação nesta posição"
            if identity is None:
                return "missing", "Não encontrado" if side == "actual" else "Não previsto"
            if identity == counterpart:
                return "match", "Mesma posição" if result == "pass" else "Mesmo produto nesta posição"
            if result == "pass" and same_members and by in {"name", "price"}:
                # The business check permits interchangeable equal-key items.
                # Different identity positions cannot overrule a recorded PASS.
                return "equivalent", "Posição equivalente · empate"
            if side == "expected":
                return "reference", "Posição de referência"
            return "different", "Produto não previsto" if identity not in (wanted or []) else "Em outra posição"

        def row(items: list[str] | None, side: str) -> str:
            nonlocal equivalent_positions
            title = "Ordem esperada" if side == "expected" else "Ordem encontrada"
            count = f"{len(items)} {'produto' if len(items) == 1 else 'produtos'}" if items is not None else "Dados não disponíveis"
            cards = []
            for position in range(columns) if items is not None else []:
                identity = items[position] if position < len(items) else None
                state, note = position_state(position, side)
                if state == "equivalent":
                    equivalent_positions = True
                name = ((identity.removeprefix("name:") or "Produto sem nome registrado") if identity else "Não encontrado" if side == "actual" else "Não previsto")
                if identity and identity.startswith("id:"):
                    name = "Produto · ID " + identity[3:]
                price = ""
                if side == "actual" and identity and by == "price" and isinstance(values, list) and items is not None and len(values) == len(items):
                    value = values[position]
                    if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value):
                        price = f'<span class="sort-product-value">Preço observado: {_h(format(value, ".2f").replace(".", ","))}</span>'
                cards.append(
                    f'<li class="sort-product" data-state="{state}">'
                    f'<span class="sort-position" aria-label="Posição {position + 1}">{position + 1:02d}</span>'
                    f'<strong class="sort-product-name">{_h(name)}</strong>{price}'
                    f'<small class="sort-product-note">{note}</small></li>'
                )
            contents = (
                '<ol class="sort-products">' + ''.join(cards) + '</ol>' if cards else
                '<p class="sort-row-empty">' + ("Sequência de produtos não disponível neste registro." if items is None else "Nenhum produto registrado.") + '</p>'
            )
            heading = f'<div class="sort-row-heading"><h5>{title}</h5><span>{count}</span></div>'
            return (
                f'<section class="sort-row" data-side="{side}" aria-label="{title}">'
                + (heading + contents if side == "expected" else contents + heading) + '</section>'
            )

        pairs = []
        pair_labels = {
            "match": "Mesmo produto nesta posição",
            "different": "Produtos diferentes nesta posição",
            "equivalent": "Produtos em posição equivalente por empate",
            "missing": "Produto sem correspondente nesta posição",
            "unverified": "Comparação sem confirmação nesta posição",
        }
        for position in range(columns):
            if not comparable or result == "unknown":
                state = "unverified"
            elif position >= len(wanted) or position >= len(found):
                state = "missing"
            else:
                state = position_state(position, "actual")[0]
            label = f"Posição {position + 1}: {pair_labels[state]}"
            pairs.append(
                f'<li class="sort-pair" data-state="{state}" aria-label="{label}" title="{label}">'
                + icon(state, "sort-pair-icon") + '</li>'
            )
        connections = (
            '<ol class="sort-pairs" aria-label="Comparação de cada par de produtos">' + ''.join(pairs) + '</ol>'
        ) if pairs else ""
        rows = row(wanted, "expected") + connections + row(found, "actual")
        notes = []
        if not comparable:
            notes.append("Parte da comparação não foi registrada. Os produtos ausentes do registro não foram reconstruídos.")
        if equivalent_positions:
            notes.append("Produtos empatados no critério de ordenação podem trocar de posição. Essas trocas são válidas e não reprovam o teste.")
        if result == "fail" and comparable and wanted == found:
            notes.append("Os produtos aparecem nas mesmas posições, mas a verificação dos valores de ordenação não passou.")
        if result == "pass" and comparable and not same_members:
            notes.append("As listas registradas têm produtos diferentes; o resultado original foi preservado. Confira os dados técnicos.")
        technical = {key: assertion[key] for key in ("kind", "expected", "actual", "passed") if key in assertion}
        technical_description = assertion.get("expected_display")
        detail = f'<p><span class="muted">Esperado (descrição): </span>{_h(technical_description)}</p>' if isinstance(technical_description, str) and technical_description else ""
        state = "match" if result == "pass" else "different" if result == "fail" else "unverified"
        return (
            f'<section class="assertion-detail sort-verification" data-result="{result}" aria-label="Comparação da ordenação dos produtos">'
            '<div class="sort-outcome">' + icon(state, "sort-state-icon")
            + f'<div><h4>{titles[result]}</h4><p>{descriptions[result]}</p></div></div>'
            f'<span class="sort-criterion">{criterion}</span>'
            f'<div class="sort-comparison-scroll" role="region" aria-label="Ordem esperada e encontrada, alinhadas por posição" tabindex="0" style="--sort-columns: {max(columns, 1)}">{rows}</div>'
            + ''.join(f'<p class="sort-verification-note">{_h(note)}</p>' for note in notes)
            + '<details class="sort-technical"><summary>Ver dados técnicos</summary>' + detail
            + f'<pre>{_h(json.dumps(technical, ensure_ascii=False, indent=2))}</pre></details></section>'
        )

    @staticmethod
    def _technical_assertion_detail(assertion: dict[str, Any]) -> str:
        """Explain recorded technical counters without changing their verdict or scope."""
        passed = assertion.get("passed")
        result = "pass" if passed is True else "fail" if passed is False else "unknown"
        actual = assertion.get("actual")
        expected = assertion.get("expected")

        def count(value: object) -> int | None:
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

        zero_templates = {
            "0 attributable application runtime, HTTP or network failures",
            "Nenhuma falha de execução, HTTP ou rede atribuível à aplicação",
            "0 new console or network errors", "0",
        }
        zero_expected = (
            isinstance(expected, str) and expected in zero_templates
        ) or (type(expected) is int and expected == 0)
        channels = [
            ("console_errors", "Execução da aplicação", "Erros de execução e de console", '<path d="m7 8 4 4-4 4m6 0h4"/><rect x="3" y="4" width="18" height="16" rx="3"/>'),
            ("network_errors", "Requisições de rede", "Falhas de HTTP e de conexão", '<path d="M12 8v8M6 16H4v4h4v-4H6m12 0h-2v4h4v-4h-2M6 16v-4h12v4"/><rect x="9" y="3" width="6" height="5" rx="1"/>'),
        ]
        recorded = {
            key: count(actual.get(key)) if isinstance(actual, dict) else None
            for key, *_ in channels
        }
        reference = {
            key: count(expected.get(key)) if isinstance(expected, dict) else 0 if zero_expected else None
            for key, *_ in channels
        }
        has_occurrences = any(value is not None and value > 0 for value in recorded.values())
        complete_zero = all(value == 0 for value in recorded.values())
        if result == "pass" and complete_zero:
            title = "Nenhuma nova falha técnica registrada"
            description = "As duas contagens registradas são zero nesta verificação."
        elif result == "pass":
            title = "Verificação técnica registrada como aprovada"
            description = "Confira as contagens disponíveis abaixo; o resultado original foi preservado."
        elif result == "fail":
            title = "Foram registradas novas falhas técnicas" if has_occurrences else "Verificação técnica reprovada"
            description = "O destaque mostra em qual tipo de registro houve ocorrências." if has_occurrences else "O registro indica uma reprovação; consulte os dados técnicos para mais detalhes."
        else:
            title = "Verificação técnica sem resultado confirmado"
            description = "As contagens disponíveis não substituem um resultado confirmado da verificação."

        def icon(state: str, css_class: str) -> str:
            path = {
                "match": '<path d="m6 12 4 4 8-8"/>',
                "different": '<path d="m8 8 8 8m0-8-8 8"/>',
                "unverified": '<path d="M9 9a3 3 0 0 1 6 0c0 2-3 2-3 4m0 3h.01"/>',
            }[state]
            return (
                f'<svg class="{css_class}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
                '<circle cx="12" cy="12" r="10"/>' + path + '</svg>'
            )

        cards = []
        for key, label, explanation, channel_path in channels:
            wanted, found = reference[key], recorded[key]
            if wanted == 0 and found is not None and found > 0:
                state = "different"
                caption = f"{found} {'nova falha registrada' if found == 1 else 'novas falhas registradas'} nesta verificação."
                comparison_label = "Há falhas registradas; o esperado era zero"
            elif wanted == 0 and found == 0 and (result == "pass" or (result == "fail" and has_occurrences)):
                state = "match"
                caption = "Nenhuma nova falha registrada neste tipo de registro."
                comparison_label = "Zero esperado e zero registrado neste tipo; não é o resultado da verificação inteira"
            else:
                state = "unverified"
                caption = (
                    "Contagem não registrada separadamente nesta verificação." if found is None else
                    "Critério esperado não disponível para esta comparação." if wanted != 0 else
                    "Contagem disponível, mas sem confirmação de aprovação neste registro."
                )
                comparison_label = "Comparação sem confirmação"
            values = []
            for side, value_label, value in (("expected", "Esperado", wanted), ("actual", "Encontrado", found)):
                values.append(
                    f'<div class="technical-value" data-side="{side}">'
                    f'<small class="technical-value-label">{value_label}</small>'
                    f'<strong class="technical-value-count">{value if value is not None else "Não informado"}</strong>'
                    + (f'<span class="technical-value-unit">{"falha" if value == 1 else "falhas"}</span>' if value is not None else '<span class="technical-value-unit">Sem contagem disponível</span>')
                    + '</div>'
                )
            cards.append(
                f'<article class="technical-channel" data-channel="{key}" data-state="{state}" aria-label="{label}">'
                '<header class="technical-channel-heading">'
                '<svg class="technical-channel-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" '
                'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
                + channel_path + f'</svg><div><h5>{label}</h5><p>{explanation}</p></div></header>'
                + '<div class="technical-comparison">' + values[0]
                + f'<span class="technical-pair" role="img" aria-label="{comparison_label}" title="{comparison_label}">'
                + icon(state, "technical-pair-icon") + '</span>' + values[1] + '</div>'
                + f'<p class="technical-channel-caption">{caption}</p></article>'
            )
        notes = []
        if result == "pass" and has_occurrences:
            notes.append("O resultado foi registrado como aprovado, mas há contagens acima de zero. A divergência foi preservada para conferência.")
        if result == "fail" and complete_zero:
            notes.append("As contagens são zero, mas o resultado registrado é reprovado. Não alteramos essa reprovação apenas pelas quantidades.")
        if not isinstance(actual, dict):
            notes.append("O registro não separa as contagens por tipo. Nenhum valor foi estimado a partir dos dados antigos.")
        technical = {key: assertion[key] for key in ("kind", "expected", "actual", "passed", "message") if key in assertion}
        expected_display = assertion.get("expected_display")
        detail = f'<p><span class="muted">Esperado (descrição): </span>{_h(expected_display)}</p>' if isinstance(expected_display, str) and expected_display else ""
        state = "match" if result == "pass" and complete_zero else "different" if result == "fail" else "unverified"
        return (
            f'<section class="assertion-detail technical-verification" data-result="{result}" aria-label="Verificação de novas falhas técnicas">'
            '<div class="technical-outcome">' + icon(state, "technical-state-icon")
            + f'<div><h4>{title}</h4><p>{description}</p></div></div>'
            + '<div class="technical-channels">' + ''.join(cards) + '</div>'
            + ''.join(f'<p class="technical-verification-note">{note}</p>' for note in notes)
            + '<p class="technical-verification-note">As contagens se referem a esta verificação e dependem da captura configurada. '
            'Uma falha técnica não confirma, sozinha, um defeito funcional. O resultado desta verificação foi preservado.</p>'
            + '<details class="technical-raw"><summary>Ver dados técnicos</summary>' + detail
            + f'<pre>{_h(json.dumps(technical, ensure_ascii=False, indent=2))}</pre></details></section>'
        )

    @classmethod
    def _assertion_detail(
        cls, assertion: dict[str, Any], *, selection: dict[str, Any] | None = None,
    ) -> str:
        kind = str(assertion.get("kind") or "Verificação observada")
        if kind == "selection_matches":
            return cls._selection_assertion_detail(assertion, cls._preview_selection(selection))
        if kind == "product_sort_order":
            return cls._sort_assertion_detail(assertion)
        if kind in {"no_new_browser_errors", "no_new_application_errors"}:
            return cls._technical_assertion_detail(assertion)
        visual = kind == "visual_model"
        label = "Indício visual da LLM" if visual else "Verificação observada"
        note = (
            "Reprovação baseada na imagem, não em um bug funcional confirmado. "
            "Confira a captura e a região; a confiança é declarada pelo modelo. "
            "O diagnóstico original do modelo é preservado no idioma em que foi recebido." if visual
            else "A aprovação se aplica a esta verificação, não a toda a "
            "funcionalidade ou aplicação."
        )
        passed = assertion.get("passed")
        verdict = (
            "Confirmada" if passed is True else
            "Não confirmada" if passed is False else "Sem resultado registrado"
        )
        assertion_values = ""
        for key, value_label in (("expected", "Esperado"), ("actual", "Obtido")):
            if key in assertion:
                value = assertion.get("expected_display", assertion[key]) if key == "expected" else assertion[key]
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                assertion_values += f'<div><span class="muted">{value_label}: </span>{_h(value)}</div>'
        return (
            f'<div class="assertion-detail"><span class="event-chip">{label}</span> '
            f'<strong>{_h(check_label(kind))}</strong><p>{verdict}: '
            f'{_h(report_text(assertion.get("message")))}</p>{assertion_values}<p class="muted">{note}</p></div>'
        )

    def _signal_detail(
        self,
        signal: dict[str, Any],
        *,
        rendered_messages: set[str] | None = None,
        rendered_urls: set[str] | None = None,
    ) -> str:
        classification = signal.get("classification")
        if isinstance(classification, dict):
            classification_label = classification.get("label") or classification.get("category")
            explanation = classification.get("explanation") or classification.get("reason")
            impact = classification.get("impact")
        else:
            classification_label = signal.get("label") or signal.get("classification_label") or classification or signal.get("category")
            explanation = signal.get("classification_reason") or signal.get("explanation")
            impact = signal.get("impact_message") or signal.get("impact")
        messages = signal.get("messages")
        if not isinstance(messages, list):
            messages = [signal.get("message") or "Sinal registrado pelo navegador."]
        urls = signal.get("urls")
        if not isinstance(urls, list):
            urls = [signal.get("url")] if signal.get("url") else []
        channels = signal.get("evidence_channels") or signal.get("types") or []
        if not isinstance(channels, list):
            channels = [channels]
        channel_label = " + ".join({"network": "rede", "console": "console", "http": "rede", "pageerror": "exceção na página"}.get(str(item), str(item)) for item in channels if item)
        status = signal.get("status_code")
        classification_label = classification_label or "Impacto funcional não confirmado"
        impact = {
            "informational": "Registro informativo: não confirma falha funcional.",
            "external_integration": "Falha numa integração de telemetria externa. Não reprova a ação funcional por si só.",
            "unconfirmed": "O impacto na funcionalidade ainda não foi confirmado.",
            "technical_check_failed": "Falha técnica da aplicação. As verificações do CT devem demonstrar se houve impacto funcional.",
        }.get(str(impact), impact)
        impact = impact or "Este registro técnico não é, por si só, um bug confirmado nem um CT falho."
        related = signal.get("related_console_errors") or []
        related_detail = (
            f'<details class="correlated-signals"><summary>{len(related) + 1} registros correlacionados · mesma ocorrência</summary>'
            '<p class="muted">Console e rede podem registrar a mesma requisição; não são defeitos adicionais.</p>'
            + "".join(f'<p>{_h(item.get("message"))}</p>' for item in related if isinstance(item, dict))
            + '</details>'
        ) if isinstance(related, list) and related else ''
        association = signal.get("association_reason") or signal.get("association_explanation")
        association_record = signal.get("association")
        if not association and isinstance(association_record, dict):
            association = {
                "during_action": "Registrado durante a ação; coincidência temporal não comprova causalidade.",
                "before_action": "Registrado antes da ação; não é atribuído à ação seguinte.",
                "after_action": "Registrado na observação posterior à ação; causalidade não confirmada.",
                "observation_context": "Preservado no contexto de observação, sem vínculo causal com um CT.",
            }.get(str(association_record.get("kind") or ""))
        return (
            '<div class="signal-detail"><div class="signal-head">'
            f'<span class="event-chip signal-chip">{_h(classification_label)}</span>'
            f'<span class="muted">{_h(channel_label)}{f" · HTTP {_h(status)}" if status else ""}</span></div>'
            + "".join(
                f'<p>{_h(message)}</p>' for message in messages
                if str(message) not in (rendered_messages or set())
            )
            + "".join(
                f'<div class="url">{_h(self._public_issue_url(url))}</div>' for url in urls
                if str(url) not in (rendered_urls or set())
            )
            + (f'<p class="muted">{_h(explanation)}</p>' if explanation else "")
            + f'<p class="impact-note">{_h(impact)}</p>'
            + (f'<p class="muted">Vínculo: {_h(association)}</p>' if association else "")
            + related_detail
            + '</div>'
        )

    @staticmethod
    def _step_presentation(entry: dict[str, Any], kind: str, *, capture_exists: bool = False) -> tuple[str, str, str]:
        """A capture is evidence, not a passed test; use only recorded booleans."""
        if kind == "assertion":
            assertion = entry.get("assertion")
            assertion = assertion if isinstance(assertion, dict) else {}
            purpose = CHECK_EXPLANATIONS.get(str(assertion.get("kind")), "Comparar o resultado observado com o esperado.")
            if assertion.get("passed") is True:
                return "pass", "Aprovada", purpose
            if assertion.get("passed") is False:
                return "fail", "Reprovada", purpose
            return "unknown", "Sem resultado registrado", purpose
        if kind == "action":
            if entry.get("success") is True:
                return "pass", "Ação concluída", "A ação foi executada; isso, sozinho, não confirma o resultado esperado."
            if entry.get("success") is False:
                return "fail", "Não concluída", "A automação não concluiu a ação; isso não confirma um defeito no site."
            return "unknown", "Sem confirmação", "Não há confirmação registrada da conclusão desta ação."
        if kind == "signal":
            return "warning", "Ocorrência registrada", "Ocorrência técnica; sozinha, não reprova o teste."
        if capture_exists:
            if entry.get("image_source") == "user_upload":
                return "recorded", "Imagem anexada", "Imagem escolhida por você; não foi capturada pelo teste e não aprova nem reprova o resultado."
            if entry.get("capture_phase") in {"before_action", "after_action"} and entry.get("checkpoint_id"):
                return "recorded", "Print manual registrado", "Você confirmou a captura durante a pausa; o print não aprova nem reprova o teste."
            return "recorded", "Captura registrada", "Captura salva; não é uma conferência do teste."
        return "info", "Registro disponível", "Observação salva; não é uma conferência do teste."

    def _timeline(
        self, exploration_id: str, scenario: dict[str, Any], *, observation: bool = False,
    ) -> str:
        # A current CT can belong to a later execution while retaining its
        # original CT number in this report. Filenames alone are not globally
        # unique: always resolve evidence in the run that actually recorded it.
        exploration_id = str(scenario.get("evidence_exploration_id") or exploration_id)
        evidence = scenario.get("evidence") or {}
        # A planned attachment belongs at its selected script boundary, even
        # when submitted later. Keep the chronological evidence itself intact.
        entries = list(evidence.get("timeline") or []) if observation else presentation_scenario_steps(evidence)
        planned_order = any(
            (entry.get("presentation_position") or {}).get("status") == "planned"
            for entry in entries
        )
        used_shots = {
            Path(str(item["screenshot"])).name for item in entries if item.get("screenshot")
        }
        # An executor-owned preview is itself the canonical capture on its
        # action. Do not append a second, synthetic "historical" observation
        # that would lose its known timing and duplicate the timeline entry.
        # Invalid/unrelated preview metadata does not consume a legacy shot.
        used_shots.update(
            filename for item in entries
            if item.get("kind") == "action"
            and (filename := self._preview_filename(exploration_id, item))
        )
        # Preserve legacy captures even if a historical record cannot establish
        # a precise timestamp or link to an action. Never invent that ordering.
        for screenshot in scenario.get("screenshots") or []:
            if Path(str(screenshot)).name not in used_shots:
                entries.append({
                    "kind": "observation",
                    "title": "Captura preservada",
                    "message": "Evidência histórica sem horário de captura registrado.",
                    "screenshot": screenshot,
                })
                used_shots.add(Path(str(screenshot)).name)
        canonical_shots = {
            Path(str(item["screenshot"])).name for item in entries if item.get("screenshot")
        }
        rendered: list[str] = []
        labels = {
            "observation": "Observação",
            "action": "Ação",
            "assertion": "Verificação",
            "signal": "Sinal técnico",
        }
        for index, entry in enumerate(entries):
            kind = str(entry.get("kind") or "observation")
            if kind not in labels:
                kind = "observation"
            screenshot = str(entry.get("screenshot") or "")
            provenance = entry.get("observation") or {}
            if not isinstance(provenance, dict):
                provenance = {}
            page_url = provenance.get("page_url") or entry.get("page_url")
            entry_url = entry.get("url")
            if observation and (
                entry_url == page_url or entry_url == provenance.get("request_url")
            ):
                entry_url = None
            detail = ""
            if isinstance(entry.get("signal"), dict):
                detail = self._signal_detail(
                    entry["signal"],
                    rendered_messages={str(entry["message"])}
                    if observation and entry.get("message") else None,
                    rendered_urls={
                        str(url) for url in (
                            page_url, provenance.get("request_url"), entry_url,
                        ) if url
                    } if observation else None,
                )
            if isinstance(entry.get("assertion"), dict):
                detail += self._assertion_detail(
                    entry["assertion"], selection=self._selection_for_assertion(entries, index),
                )
            user_image = entry.get("image_source") == "user_upload"
            timestamp = (entry.get("attached_at") or entry.get("occurred_at")) if user_image else entry.get("occurred_at")
            placement = entry.get("presentation_position") or {}
            placement_note = ""
            if placement.get("status") == "planned":
                boundary = "antes da primeira etapa" if placement.get("gap") == 0 else f'após a etapa {placement.get("gap")} do roteiro de origem'
                placement_note = f'<p class="muted attachment-position-note">Posição escolhida: {_h(boundary)}. O horário exibido é o horário real do anexo.</p>'
            elif placement.get("status") == "unresolved":
                placement_note = '<p class="muted attachment-position-note">Não foi possível vincular com segurança a posição planejada deste anexo. Ele aparece separado dos passos, sem posição atribuída e com seu horário real.</p>'
            title = (
                provenance.get("summary") if observation else None
            ) or entry.get("title") or labels[kind]
            phase = provenance.get("phase")
            if observation:
                # Projected summaries can contain recorded page URLs. Apply
                # the same query redaction as the separate diagnostic fields.
                title = str(title)
                for url in (page_url, provenance.get("request_url"), entry.get("url")):
                    if url:
                        title = title.replace(str(url), self._public_issue_url(url))
            observation_meta = (
                (f'<div class="url">Página: {_h(self._public_issue_url(page_url))}</div>' if page_url else '')
                + (f'<p class="muted">Fase registrada: {_h(phase)}</p>' if phase else '')
            ) if observation else ''
            signal = entry.get("signal") or {}
            if observation and isinstance(signal, dict) and kind == "signal":
                channels = signal.get("evidence_channels") or signal.get("types") or [signal.get("type")]
                if not isinstance(channels, list):
                    channels = [channels]
                request_url = provenance.get("request_url")
                request_recorded = bool(request_url) or bool({"http", "network"}.intersection(channels))
                request_meta = " · ".join(
                    str(value) for value in [
                        provenance.get("method") or signal.get("method"),
                        f'HTTP {provenance.get("status_code") or signal.get("status_code")}'
                        if provenance.get("status_code") or signal.get("status_code") else None,
                    ] if value
                )
                if request_meta and request_recorded:
                    observation_meta += f'<p class="muted">Requisição: {_h(request_meta)}</p>'
                if request_url:
                    observation_meta += f'<div class="url">Destino da requisição: {_h(self._public_issue_url(request_url))}</div>'
            step = entry.get("step_number", entry.get("action_step"))
            action_meta = (
                f'<p class="muted">{f"Etapa {_h(step)}" if step is not None else ""}'
                f'{f" · Alvo: {_h(entry.get('target'))}" if entry.get("target") else ""}</p>'
            ) if step is not None or entry.get("target") else ""
            step_state, step_outcome, step_description = self._step_presentation(
                entry, kind, capture_exists=bool(kind == "observation" and screenshot and self._image_path(exploration_id, screenshot)),
            )
            rendered.append(
                f'<li id="{self._timeline_entry_id(scenario, index)}" class="timeline-entry {kind}" '
                f'data-step-state="{step_state}" data-step-outcome="{_h(step_outcome)}" '
                f'data-step-description="{_h(step_description)}"'
                + (' data-image-source="user_upload"' if user_image else '')
                + '><div class="timeline-marker" aria-hidden="true"></div>'
                '<div class="timeline-content"><div class="event-meta">'
                f'<span class="event-kind">{"Imagem do usuário" if user_image else labels[kind]}</span>'
                f'<time datetime="{_h(timestamp)}">{"Anexado em " if user_image else ""}{_h(self._timeline_time(timestamp))}</time></div>'
                f'<h3>{_h(title)}</h3>'
                + placement_note
                + action_meta
                + observation_meta
                + (f'<p>{_h(entry.get("message"))}</p>' if entry.get("message") else "")
                + (f'<div class="url">{_h(self._public_issue_url(entry_url))}</div>' if entry_url else "")
                + detail
                + (self._action_preview(
                    exploration_id, entry, preceding=entries[:index], canonical_shots=canonical_shots,
                ) if kind == "action" else "")
                + (f'<div class="shots">{self._screenshots(exploration_id, [screenshot], image_label="Imagem anexada pelo usuário" if entry.get("image_source") == "user_upload" else "Captura de evidência")}</div>' if screenshot else "")
                + '</div></li>'
            )
        order_attribute = ' data-step-order="planned"' if planned_order else ''
        return f'<ol class="evidence-timeline"{order_attribute}>' + "".join(rendered) + '</ol>' if rendered else (
            '<p class="muted">Não há evidência temporal registrada para este caso.</p>'
        )

    def _execution_observations(self, exploration_id: str, contexts: list[dict[str, Any]]) -> str:
        """Keep unowned evidence outside test cases, results and CT filters."""
        timelines = []
        entry_count = 0
        for context in contexts:
            evidence = context.get("evidence") or {}
            entries = evidence.get("timeline") or []
            if not entries and not context.get("screenshots"):
                continue
            entry_count += len(entries) or len(context.get("screenshots") or [])
            timelines.append(self._timeline(exploration_id, context, observation=True))
        if not timelines:
            return ""
        return (
            '<section id="execution-observations" aria-label="Observações da execução">'
            '<details class="execution-observations"><summary><strong>Observações da execução</strong>'
            f' · {entry_count} registro(s)</summary>'
            '<p class="muted">Ocorrências registradas fora de um CT identificado, como carregamento '
            'da página e navegação de apoio. Não alteram os resultados nem a contagem de testes. '
            'Página e fase são mostradas somente quando foram registradas; não há atribuição de falha a um CT.</p>'
            + "".join(timelines) + '</details></section>'
        )

    def _scenario_summary(self, scenario: dict[str, Any], status: str) -> str:
        """Didactic, read-only overview of this CT's recorded result and checks."""
        evidence = scenario.get("evidence") or {}
        recorded_assertions = evidence.get("assertions")
        if not isinstance(recorded_assertions, list):
            recorded_assertions = [item.get("assertion") for item in evidence.get("timeline") or []
                                   if isinstance(item, dict) and item.get("kind") == "assertion"]
        assertions = [item for item in recorded_assertions if isinstance(item, dict)]
        confirmed = sum(item.get("passed") is True for item in assertions)
        failed = sum(item.get("passed") is False for item in assertions)
        total = len(assertions)
        verdict = evidence.get("verdict") or {}
        kind = verdict.get("kind") if isinstance(verdict, dict) else None
        headline, symbol = {
            "PASS": ("Este teste passou", "✓"),
            "FAIL": ("Este teste não passou", "×"),
            "BLOCKED": ("Não foi possível concluir este teste", "!"),
            "SKIPPED": ("Este teste está sem validação", "–"),
        }[status]
        if status == "PASS":
            explanation = (
                f"{confirmed} de {total} conferências tiveram o resultado esperado."
                if total and confirmed == total and evidence.get("action_succeeded") is not False else
                "Resultado salvo como aprovado, mas há conferências não confirmadas ou sem resultado. Consulte os detalhes."
                if total else "Resultado salvo como aprovado. Não há conferências detalhadas registradas neste caso."
            )
        elif status == "FAIL":
            explanation = (
                "A automação não conseguiu concluir a ação. Veja o passo em que ela parou."
                if evidence.get("action_succeeded") is False or kind == "execution_failure" else
                "A revisão das imagens apontou uma possível diferença visual. Confira as evidências; isso não confirma um defeito funcional."
                if kind == "visual_failure" else
                "Uma verificação técnica não passou. Confira o erro registrado e seu possível impacto no uso."
                if kind == "technical_failure" else
                f"{failed} de {total} conferências não tiveram o resultado esperado."
                if failed else "O resultado salvo é reprovado. Consulte os passos e as evidências para entender o motivo."
            )
        else:
            explanation = (
                "Uma restrição impediu concluir a validação. Veja o motivo nos detalhes; este caso não conta como aprovado."
                if status == "BLOCKED" else "Não há validação concluída para este caso. Ele não conta como aprovado."
            )
        actions = [item for item in evidence.get("timeline") or []
                   if isinstance(item, dict) and item.get("kind") == "action"]
        action_title = "Ação sem detalhamento registrado"
        if len(actions) > 1:
            action_title = f"{len(actions)} ações registradas neste caso"
        elif actions:
            entry = actions[0]
            preview = entry.get("action_preview")
            preview = preview if isinstance(preview, dict) else {}
            # A single, page/target-bound preview can describe a requested
            # choice. Its before-image cannot prove what was selected after.
            if (preview.get("version") == "action-target-v1"
                    and entry.get("target") and preview.get("target") == entry["target"]
                    and entry.get("url") and preview.get("url") == entry["url"]
                    and isinstance(preview.get("action"), str) and preview["action"] in ACTION_LABELS):
                action_title = action_label(preview["action"])
                selection = self._preview_selection(preview.get("selection")) if preview["action"] == "select" else None
                if selection and selection.get("requested_label"):
                    action_title = f'Selecionar a opção “{selection["requested_label"]}”'
            elif entry.get("title") in ACTION_LABELS.values():
                action_title = str(entry["title"])
            else:
                action_title = "Interagir com a página"
        checks = []
        for assertion in assertions:
            check_kind = str(assertion.get("kind") or "")
            state, outcome, description = self._step_presentation({"assertion": assertion}, "assertion")
            label = check_label(check_kind) if check_kind in CHECK_LABELS else "Conferência registrada"
            icon = {"pass": "✓", "fail": "×", "unknown": "?"}[state]
            checks.append(
                f'<li class="ct-summary-check" data-state="{state}">'
                f'<span class="ct-summary-check-icon" aria-hidden="true">{icon}</span>'
                f'<div><strong>{_h(label)}</strong><small>{_h(description)}</small></div>'
                f'<span class="ct-summary-check-status">{_h(outcome)}</span></li>'
            )
        business_confirmed = any(a.get("passed") is True and str(a.get("kind")) in CHECK_LABELS
                                 and str(a.get("kind")) not in NON_BUSINESS_CHECKS for a in assertions)
        scope = (
            "Este resultado vale para a ação e as conferências deste caso, não para toda a aplicação."
            if business_confirmed else
            "Este caso não tem uma regra de negócio confirmada. Executar uma ação ou não registrar erros não garante que toda a funcionalidade esteja correta."
        )
        if isinstance(evidence.get("rerun_scope"), dict):
            scope += " Reexecução individual: análises por modelo e cartões de consulta ao banco não foram executados."
        blocked_explanation = evidence.get("rerun_blocked_explanation")
        if status == "BLOCKED" and isinstance(blocked_explanation, str) and blocked_explanation:
            explanation = blocked_explanation + " Este caso não conta como aprovado."
        checklist = ('<ul>' + ''.join(checks) + '</ul>') if checks else (
            '<p class="ct-summary-empty">Nenhuma conferência detalhada foi registrada. Consulte os passos e as evidências disponíveis.</p>'
        )
        return (
            f'<section class="ct-overview" data-result="{status}" aria-label="Resumo do teste">'
            '<header class="ct-overview-result">'
            f'<span class="ct-summary-symbol" aria-hidden="true">{symbol}</span>'
            f'<div><h3>{headline}</h3><p>{_h(explanation)}</p></div></header>'
            '<div class="ct-summary-action"><span class="ct-summary-eyebrow">O que foi testado</span>'
            f'<h3>{_h(action_title)}</h3><div class="url">Página: {_h(self._public_issue_url(scenario.get("url")))}</div></div>'
            '<section class="ct-summary-checks" aria-label="Conferências do teste"><h4>O que conferimos</h4>'
            f'{checklist}</section><p class="ct-summary-scope">{_h(scope)}</p></section>'
        )

    def _scenario_card(self, exploration_id: str, scenario: dict[str, Any]) -> str:
        exploration_id = str(scenario.get("evidence_exploration_id") or exploration_id)
        status = str(scenario.get("status", "SKIPPED"))
        if status not in {"PASS", "FAIL", "BLOCKED", "SKIPPED"}:
            status = "SKIPPED"
        steps = "".join(f"<li>{_h(step)}</li>" for step in scenario.get("steps", []))
        preconditions = "".join(
            f"<li>{_h(item)}</li>" for item in scenario.get("preconditions", [])
        ) or "<li>Nenhuma pré-condição documentada.</li>"
        visible_status = {"PASS": "APROVADO", "FAIL": "REPROVADO", "BLOCKED": "BLOQUEADO", "SKIPPED": "SEM VALIDAÇÃO"}[status]
        evidence = scenario.get("evidence") or {}
        verdict = evidence.get("verdict") or {}
        technical_failure = isinstance(verdict, dict) and verdict.get("kind") == "technical_failure"
        if status == "FAIL" and technical_failure:
            visible_status = "REPROVADO · VERIFICAÇÃO TÉCNICA"
        elif status == "FAIL" and verdict.get("kind") == "visual_failure":
            visible_status = "REPROVADO · INDÍCIO VISUAL"
        badge_class = "blocked" if technical_failure else status.lower()
        verdict_detail = (
            f'<div class="verdict-note"><strong>{_h(verdict.get("label"))}</strong>'
            f'<p>{_h(verdict.get("explanation"))}</p>'
            + ('<p class="muted">Resultado histórico preservado; esta apresentação não reexecuta o teste.</p>' if verdict.get("historical") else '')
            + '</div>'
        ) if isinstance(verdict, dict) and verdict.get("label") else ''
        signal_count = len(evidence.get("technical_signals") or [])
        case_label = str(scenario.get("id") or "CT")
        details = f"""
            <details class="case-documentation ct-summary-details"><summary>Ver detalhes do planejamento e da execução</summary>
            <h3>Por que esta ação foi escolhida</h3><p>{_h(scenario.get("description"))}</p>
            {verdict_detail}
            <div class="columns"><section><h3>Pré-condições</h3><ul>{preconditions}</ul></section><section><h3>Passos</h3><ol>{steps}</ol></section></div>
            <div class="result"><section><h3>Esperado</h3><p>{_h(scenario.get("expected_result"))}</p></section><section><h3>Obtido</h3><p>{_h(scenario.get("actual_result"))}</p></section></div></details>"""
        visual_detail = ""
        visual_meta = ""
        visual = evidence.get("visual_assessment") or {}
        if visual.get("requested"):
            visual_meta = " · visual: " + ("analisado" if visual.get("status") == "assessed" else "em análise" if visual.get("status") == "pending" else "indisponível")
            if visual.get("status") == "assessed":
                text = (f"Revisão das imagens: {visual.get('accepted_count', 0)} possível(is) diferença(s) para conferir"
                        if visual.get("accepted_count") else "Imagens revisadas: nenhuma diferença visual apontada")
                note = "A revisão usa as capturas deste caso. Não garante que toda a aparência esteja correta nem confirma, sozinha, um defeito funcional."
                if visual.get("context_limited") is True or visual.get("review_scope") == "saved_capture_only_legacy":
                    note += " Contexto textual limitado: esta revisão usa as capturas salvas; o DOM e o texto visível originais não foram preservados. Não equivale a uma análise com contexto semântico completo."
            elif visual.get("status") == "pending":
                text, note = "Análise visual em andamento", "O resultado visual ainda não foi concluído."
            else:
                text = "Análise visual indisponível · CT baseado nas verificações locais"
                reason = str(visual.get("reason") or "")
                note = (
                    "O provedor limitou as chamadas; as verificações locais foram preservadas."
                    if reason.startswith("auxiliary_rate_limit") else
                    "O orçamento de chamadas auxiliares desta execução foi esgotado."
                    if reason == "auxiliary_request_budget_exhausted" else
                    "O modelo não devolveu uma análise visual no formato obrigatório. A documentação recebida não é uma validação visual."
                    if reason in {"visual_assessment_not_returned", "visual_assessment_schema_invalid"} else
                    visual_unavailability_label(reason).capitalize() + ". As verificações locais foram preservadas."
                )
            visual_detail = f'<div class="context-note ct-summary-visual" data-visual-status="{_h(visual.get("status"))}"><strong>{_h(text)}</strong><p>{_h(note)}</p></div>'
        return f"""<details class="scenario" data-status="{status}">
          <summary><div><span class="case-id">{_h(case_label)}</span><h2>{_h(scenario.get("title"))}</h2><span class="case-meta">{signal_count} sinal(is) técnico(s){visual_meta} · clique para ver a linha do tempo</span></div><span class="badge {badge_class}">{visible_status}</span></summary>
          <div class="scenario-body">{self._scenario_summary(scenario, status)}
          {visual_detail}{details}<h3 class="timeline-heading">Linha do tempo · ações e evidências</h3>
          {self._timeline(exploration_id, scenario)}</div></details>"""

    def generate(self, exploration: dict[str, Any], output_path: str) -> str:
        exploration = without_rerun_recipes(exploration)
        exploration = localize_report_presentation(prepare_scenario_timelines(exploration))
        exploration_id = str(exploration["id"])
        recorded_scenarios = exploration.get("scenarios", [])
        legacy_observations = [
            scenario for scenario in recorded_scenarios
            if str(scenario.get("status") or "").upper() in {"INFO", "CONTEXT"}
        ]
        scenarios = [
            scenario for scenario in recorded_scenarios
            if str(scenario.get("status") or "").upper() not in {"INFO", "CONTEXT"}
        ]
        contexts = list(exploration.get("scenario_contexts") or [])
        context_ids = {context.get("id") for context in contexts}
        for observation in legacy_observations:
            if observation.get("id") not in context_ids:
                contexts.append(observation)
                context_ids.add(observation.get("id"))
        strength = verification_summary(scenarios)
        strength_summary = (
            '<section class="completion" aria-label="Força das verificações"><strong>O que foi realmente verificado</strong>'
            f'<p>{strength["business_verified"]} aprovado(s) com asserção de negócio · '
            f'{strength["interaction_only"]} aprovado(s) apenas pela interação técnica.</p>'
            f'<p>{strength["business_failures"]} falha(s) de asserção de negócio · '
            f'{strength["execution_failures"]} falha(s) na execução da automação · '
            f'{strength["technical_failures"]} falha(s) técnica(s) · '
            f'{strength["visual_findings"]} indício(s) visual(is) a revisar · '
            f'{strength["interaction_failures"]} interação(ões) sem resultado esperado.</p>'
            '<p class="muted">Processamento concluído não significa todos os cenários possíveis. '
            'Aprovação apenas da interação não valida o resultado de negócio. O escopo depende do usuário, '
            'dos estados alcançados, dos valores de entrada e do tamanho da tela.</p></section>'
        )
        coverage = (exploration.get("completion") or {})
        blocked_count = int(coverage.get("blocked_candidates") or 0)
        for event in reversed(exploration.get("events") or []):
            if str(event.get("type") or "").startswith("coverage_complete"):
                coverage = {**coverage, **(event.get("payload") or {})}
                blocked_count = int(coverage.get("candidates_blocked") or coverage.get("blocked_candidates") or blocked_count)
                break
        ledger = coverage_restriction_ledger(coverage, exploration.get("events"))
        restrictions = ledger["restrictions"]
        if ledger["has_individual_records"]:
            blocked_count = ledger["restriction_count"]
        restriction_cards = "".join(
            self._restriction_card(exploration_id, item, scenarios) for item in restrictions
        )
        restriction_note = (
            f'Execução histórica: {blocked_count} controles bloqueados sem detalhes individuais. '
            'Não é possível reconstruir seus motivos com segurança.'
            if blocked_count and not restriction_cards else
            'Nenhuma restrição individual registrada. Isso não garante cobertura de todas as combinações e perfis.'
        )
        coverage_summary = (
            '<details class="completion"><summary><strong>Cobertura e restrições · '
            f'{len(restrictions)} registros</strong></summary>'
            '<p>São lacunas de cobertura, não testes aprovados nem bugs confirmados.</p>'
            + (restriction_cards or '<p class="muted">' + restriction_note + '</p>') + '</details>'
        )
        if ledger["resolved_restrictions"]:
            coverage_summary += (
                '<details class="completion coverage-recovered"><summary><strong>Restrições resolvidas com evidência · '
                + str(ledger["resolved_restriction_count"]) + ' registros</strong></summary>'
                '<p>Impedimentos temporários de retomada foram superados por ações realmente executadas. '
                'Este histórico não aprova o CT nem altera falhas registradas.</p>'
                + "".join(self._resolution_card(exploration_id, item, scenarios)
                          for item in ledger["resolved_restrictions"]) + '</details>'
            )
        summary = exploration.get("summary", {})
        if legacy_observations:
            # Historical context records were sometimes stored alongside CTs.
            # Correct only the displayed counts; preserve every real verdict
            # and leave summaries of runs without that legacy shape unchanged.
            counts = dict.fromkeys(("PASS", "FAIL", "BLOCKED", "SKIPPED"), 0)
            for scenario in scenarios:
                status = str(scenario.get("status") or "SKIPPED").upper()
                counts[status if status in counts else "SKIPPED"] += 1
            summary = {
                **summary,
                "total_scenarios": len(scenarios),
                **{status.lower(): count for status, count in counts.items()},
            }
        focus_area = str(
            (exploration.get("options") or {}).get("focus_area") or ""
        ).strip()
        scope = focus_area or "Aplicação inteira"
        evidence_mode = str(
            (exploration.get("options") or {}).get("auxiliary_evidence_mode")
            or "text"
        )
        evidence_mode_label = (
            "Visual · texto + imagem"
            if evidence_mode == "visual"
            else "Econômico · somente texto"
        )
        visual_summary = ""
        if evidence_mode == "visual":
            visual_counts = visual_review_counts(scenarios)
            options = exploration.get("options") or {}
            review_budget = options.get("auxiliary_max_requests")
            if not isinstance(review_budget, int):
                review_budget = next((
                    (event.get("payload") or {}).get("request_budget")
                    for event in reversed(exploration.get("events") or [])
                    if isinstance((event.get("payload") or {}).get("request_budget"), int)
                ), None)
            budget_copy = (
                f'Limite local registrado: {_h(review_budget)} requisições HTTP auxiliares.'
                if review_budget is not None else 'O limite local de chamadas desta execução não foi registrado.'
            )
            review_attempts = (exploration.get("visual_review_resume") or {}).get("max_attempts") or options.get("auxiliary_review_max_attempts") or 3
            if not isinstance(review_attempts, int) or not 1 <= review_attempts <= 5:
                review_attempts = 3
            title = ("Análise visual em andamento" if visual_counts["pending"]
                     else "Análise visual não realizada" if not visual_counts["assessed"]
                     else "Análise visual parcial" if visual_counts["unavailable"]
                     else "Capturas analisadas · contexto parcial" if visual_counts["context_limited"] else "Capturas analisadas")
            limited_context_copy = (
                f'<p><strong>{visual_counts["context_limited"]} revisões com contexto textual limitado.</strong> '
                'As imagens salvas foram analisadas, mas o DOM e o texto visível originais não foram preservados. '
                'Essas revisões não equivalem a uma análise com o contexto semântico completo da tela.</p>'
                if visual_counts["context_limited"] else ""
            )
            visual_summary = (
                f'<section class="completion visual-review-summary" aria-label="Resultado da análise visual"><strong>{title}</strong>'
                f'<p>{visual_counts["assessed"]} de {visual_counts["total"]} CTs receberam uma análise visual válida · '
                f'{visual_counts["pending"]} em análise · {visual_counts["unavailable"]} indisponíveis.</p>'
                + limited_context_copy
                + "".join(
                    f'<p>{item["count"]} {_h(item["label"])}. <small>Código: {_h(item["reason"])}</small></p>'
                    for item in visual_counts["unavailable_reasons"]
                ) +
                f'<p class="muted">{budget_copy} Retentativas e atualizações de evidência consomem chamadas; reutilização de cache não consome novas chamadas. '
                'O limite não representa uma quantidade de CTs nem falta de crédito no provedor.</p>'
                '<p>Uma revisão visual pendente significa que falta a análise adicional das capturas pela LLM; '
                'ações e verificações locais já registradas continuam válidas. Na aplicação, revisões pendentes podem ser retomadas com um novo limite finito, '
                f'após confirmação de custo, sem repetir a exploração. A retomada usa até {review_attempts} tentativas por revisão, contando a primeira.</p>'
                '<p class="muted">Os resultados de aprovação ou reprovação das verificações locais continuam válidos. Análise visual indisponível não significa aprovação visual; '
                'ausência de indícios não garante cobertura visual completa.</p></section>'
            )
        tokens, _ = self._usage(exploration)
        cost_summary = self._cost_summary(exploration)
        completion_status, completion_reason = self._completion(exploration)
        cards = [self._scenario_card(exploration_id, scenario) for scenario in scenarios]
        execution_observations = self._execution_observations(
            exploration_id, contexts,
        )
        generated = datetime.now().astimezone().strftime("%d/%m/%Y %H:%M:%S %Z")
        document = f"""<!doctype html>
<html lang="pt-BR" data-report-locale="{REPORT_LOCALE_VERSION}" data-report-schema="ct-timeline-v1" data-report-summary="execution-v2" data-coverage-ledger="execution-evidence-v1" data-cost-breakdown="roles-v1" data-visual-review-policy="bounded-v1" data-report-snapshot="{report_snapshot_key(exploration)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AutoJev — Relatório de testes exploratórios</title>
<style>
:root{{--bg:#0b0e14;--panel:#141923;--line:#293142;--text:#edf1f7;--muted:#9ca8ba;--accent:#f0b44d;--pass:#3ddc97;--fail:#ff647c;--blocked:#ffa657;--skipped:#8b98aa;--info:#83bffc}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 Inter,system-ui,sans-serif}}main{{max-width:1120px;margin:auto;padding:48px 24px}}
.eyebrow,.case-id{{color:var(--accent);font-weight:800;letter-spacing:.12em;text-transform:uppercase;font-size:12px}}h1{{font-size:40px;margin:.15em 0}}h2{{margin:.2em 0;font-size:22px;overflow-wrap:anywhere}}h3{{font-size:13px;text-transform:uppercase;color:var(--muted)}}p{{overflow-wrap:anywhere}}.muted,small{{color:var(--muted)}}
.meta,.summary,.cost-summary,.columns,.result{{display:grid;gap:12px}}.meta{{grid-template-columns:repeat(auto-fit,minmax(180px,1fr));margin:24px 0}}.summary{{grid-template-columns:repeat(auto-fit,minmax(120px,1fr));margin:20px 0 12px}}.cost-summary{{grid-template-columns:repeat(3,minmax(0,1fr));margin:0 0 28px}}.cost-metric{{min-width:0}}.cost-metric strong{{overflow-wrap:anywhere;font-variant-numeric:tabular-nums}}.cost-metric small{{display:block;margin-top:6px;font-size:12px}}.cost-state{{display:inline-block;margin-left:8px;padding:1px 6px;border:1px solid var(--accent);border-radius:5px;color:var(--accent);font-size:11px}}.cost-note{{margin:-16px 0 24px;font-size:12px}}
.completion,.reading-guide{{padding:14px 16px;border:1px solid var(--line);border-radius:10px;background:var(--panel);margin:0 0 20px}}.completion{{border-left:3px solid var(--accent)}}.completion strong{{margin-right:8px}}.reading-guide{{color:var(--muted)}}.reading-guide strong{{color:var(--text)}}
.metric,.scenario{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px}}.metric strong{{display:block;font-size:28px}}.report-tools{{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:24px 0}}.search{{flex:1;min-width:240px;background:#10151e;color:var(--text);border:1px solid var(--line);border-radius:10px;padding:10px 13px}}.filters{{display:flex;gap:8px;flex-wrap:wrap}}button{{background:#1c2330;color:var(--text);border:1px solid var(--line);border-radius:999px;padding:8px 14px;cursor:pointer}}button.active{{border-color:var(--accent);color:var(--accent)}}button:focus-visible,summary:focus-visible{{outline:2px solid var(--accent);outline-offset:5px}}
.scenario{{margin:16px 0}}.scenario>summary{{display:flex;justify-content:space-between;gap:20px;cursor:pointer;list-style:none;align-items:flex-start}}.scenario>summary::-webkit-details-marker{{display:none}}.scenario>summary>div{{min-width:0}}.case-meta{{font-size:12px;color:var(--muted)}}.scenario-body{{padding-top:12px;margin-top:16px;border-top:1px solid var(--line)}}.badge{{height:max-content;border-radius:999px;padding:5px 10px;font-weight:800;font-size:12px;white-space:nowrap}}.pass{{color:var(--pass);background:#143328}}.fail{{color:var(--fail);background:#3a1720}}.blocked{{color:var(--blocked);background:#3c2814}}.skipped{{color:var(--skipped);background:#252c36}}.info{{color:var(--info);background:#162a40}}
.url{{color:var(--muted);font:12px/1.6 ui-monospace,monospace;overflow-wrap:anywhere}}.columns,.result{{grid-template-columns:1fr 1fr;margin:15px 0}}.case-documentation{{border-top:1px solid var(--line);padding:14px 0;margin-top:16px}}.case-documentation>summary{{cursor:pointer;color:var(--muted)}}.verdict-note,.context-note{{border:1px solid var(--line);border-radius:10px;background:#10151e;padding:12px 14px;margin:16px 0}}.verdict-note p{{margin:6px 0}}.context-note{{border-color:#29465d;color:#b2cce1}}.execution-observations{{margin-top:28px;padding-top:18px;border-top:1px solid var(--line)}}.execution-observations>summary{{cursor:pointer;color:var(--muted)}}.execution-observations>summary strong{{color:var(--text)}}.execution-observations .evidence-timeline{{margin-top:16px}}
.timeline-heading{{margin:22px 0 14px}}.evidence-timeline{{list-style:none;padding:0;margin:0}}.timeline-entry{{position:relative;display:grid;grid-template-columns:14px minmax(0,1fr);gap:14px;padding-bottom:18px}}.timeline-entry:not(:last-child)::before{{content:"";position:absolute;left:6px;top:14px;bottom:0;width:1px;background:var(--line)}}.timeline-marker{{position:relative;width:13px;height:13px;border:3px solid var(--panel);border-radius:50%;background:#728397;margin-top:6px;box-shadow:0 0 0 1px var(--line)}}.action .timeline-marker{{background:var(--accent)}}.signal .timeline-marker{{background:var(--blocked)}}.assertion .timeline-marker{{background:var(--info)}}.timeline-content{{min-width:0;border:1px solid var(--line);border-radius:10px;background:#10151e;padding:14px 16px}}.event-meta{{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;font-size:11px;color:var(--muted)}}.event-kind{{letter-spacing:.09em;text-transform:uppercase;font-weight:750}}.timeline-content h3{{font-size:16px;text-transform:none;color:var(--text);margin:7px 0}}.timeline-content p{{margin:8px 0}}.event-chip{{display:inline-block;font-size:11px;border:1px solid var(--line);padding:3px 8px;border-radius:6px;color:var(--info)}}.signal-chip{{color:var(--blocked);border-color:#6a4b24}}.signal-head{{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}}.signal-detail,.assertion-detail{{padding-top:10px;margin-top:10px;border-top:1px solid var(--line)}}.impact-note{{color:#c5bca9;font-size:13px}}.shots{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:12px;margin-top:16px}}figure{{margin:0;min-width:0}}img{{display:block;width:100%;border-radius:9px;border:1px solid var(--line)}}figcaption{{color:var(--muted);font-size:12px;margin-top:5px;overflow-wrap:anywhere}}
@media(max-width:720px){{main{{padding:28px 14px}}h1{{font-size:30px}}.summary{{grid-template-columns:1fr 1fr}}.cost-summary,.meta,.columns,.result{{grid-template-columns:1fr}}.scenario{{padding:14px}}.scenario h2{{font-size:18px}}.scenario>summary{{gap:10px}}.timeline-entry{{gap:10px}}.timeline-content{{padding:12px}}.search{{min-width:0;width:100%;flex-basis:100%}}}}@media(prefers-reduced-motion:reduce){{*{{scroll-behavior:auto}}}}
</style></head>"""
        document = render_premium_report(self, exploration, scenarios, contexts, document, {
            "summary": summary, "tokens": tokens, "scope": scope,
            "evidence_mode": evidence_mode_label, "status": completion_status,
            "generated": generated, "completion_reason": completion_reason,
            "cost_summary": cost_summary, "visual_summary": visual_summary,
            "strength_summary": strength_summary, "coverage_summary": coverage_summary,
            "observations": execution_observations,
            "case_cards": cards,
        })
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(document, encoding="utf-8")
        return str(output)
