from __future__ import annotations

import base64
import html
import json
import math
import re
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from autojev.services.scenario_timeline import prepare_scenario_timelines
from autojev.services.coverage_ledger import coverage_restriction_ledger
from autojev.services.execution_summary import formatted_execution_duration, report_snapshot_key
from autojev.services.visual_checks import visual_review_counts, visual_unavailability_label
from autojev.services.verification_summary import verification_summary
from autojev.services.report_localization import (
    REPORT_LOCALE_VERSION, action_label, check_label, localize_report_presentation, report_text,
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


class StandaloneHtmlReport:
    """Renders a self-contained, escaped HTML report with inline PNG evidence."""

    _GENERIC_RESOURCE_ERROR = re.compile(
        r"failed to load resource:.*?status(?:\s+(?:of|code))?\s+(\d{3})",
        re.IGNORECASE,
    )

    def __init__(self, artifact_root: Path) -> None:
        self.artifact_root = artifact_root.resolve()

    def _image_data(self, exploration_id: str, relative_path: str) -> str | None:
        candidate = (
            self.artifact_root
            / exploration_id
            / "screenshots"
            / Path(relative_path).name
        ).resolve()
        allowed = (self.artifact_root / exploration_id / "screenshots").resolve()
        if allowed not in candidate.parents or not candidate.is_file():
            return None
        mime = "image/png" if candidate.suffix.lower() == ".png" else "image/jpeg"
        return f"data:{mime};base64,{base64.b64encode(candidate.read_bytes()).decode('ascii')}"

    def _screenshots(self, exploration_id: str, paths: list[str]) -> str:
        images: list[str] = []
        for path in paths:
            data = self._image_data(exploration_id, path)
            if data:
                images.append(
                    f'<figure><img loading="lazy" src="{data}" alt="Captura de evidência"><figcaption>{_h(Path(path).name)}</figcaption></figure>'
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
        # Values, DOM content, URLs and arbitrary capture metadata must not be
        # copied to an HTML data attribute: it needs only geometry and the ref.
        return {
            "version": "action-target-v1", "coordinate_space": "screenshot-css-pixels",
            "action": metadata["action"], "target": target,
            "image": {key: image[key] for key in ("width", "height")},
            "box": {key: box[key] for key in ("x", "y", "width", "height")},
        }

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
        return (
            '<div class="shots action-preview-shots"><figure class="action-preview" '
            f'data-action-preview="{_h(payload)}">'
            '<div class="action-preview-heading"><strong>Alvo da ação</strong>'
            '<small>Captura anterior à interação</small></div>'
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

    def _action_anchor(self, step: Any, scenarios: list[dict[str, Any]]) -> str | None:
        if not isinstance(step, int) or isinstance(step, bool) or step < 1:
            return None
        for scenario in scenarios:
            for index, entry in enumerate((scenario.get("evidence") or {}).get("timeline") or []):
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
        anchor = self._action_anchor(step, scenarios)
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
        anchor = self._action_anchor(step, scenarios)
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

    @staticmethod
    def _assertion_detail(assertion: dict[str, Any]) -> str:
        kind = str(assertion.get("kind") or "Verificação observada")
        technical = kind in {"no_new_browser_errors", "no_new_application_errors"}
        visual = kind == "visual_model"
        label = "Indício visual da LLM" if visual else "Verificação técnica" if technical else "Verificação observada"
        note = (
            "Erros de console/rede não demonstram, isoladamente, um defeito "
            "funcional. O resultado desta verificação foi preservado."
            if technical else "Reprovação baseada na imagem, não em um bug funcional confirmado. "
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

    def _timeline(
        self, exploration_id: str, scenario: dict[str, Any], *, observation: bool = False,
    ) -> str:
        evidence = scenario.get("evidence") or {}
        entries = list(evidence.get("timeline") or [])
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
                detail += self._assertion_detail(entry["assertion"])
            timestamp = entry.get("occurred_at")
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
            rendered.append(
                f'<li id="{self._timeline_entry_id(scenario, index)}" class="timeline-entry {kind}"><div class="timeline-marker" aria-hidden="true"></div>'
                '<div class="timeline-content"><div class="event-meta">'
                f'<span class="event-kind">{labels[kind]}</span>'
                f'<time datetime="{_h(timestamp)}">{_h(self._timeline_time(timestamp))}</time></div>'
                f'<h3>{_h(title)}</h3>'
                + action_meta
                + observation_meta
                + (f'<p>{_h(entry.get("message"))}</p>' if entry.get("message") else "")
                + (f'<div class="url">{_h(self._public_issue_url(entry_url))}</div>' if entry_url else "")
                + detail
                + (self._action_preview(
                    exploration_id, entry, preceding=entries[:index], canonical_shots=canonical_shots,
                ) if kind == "action" else "")
                + (f'<div class="shots">{self._screenshots(exploration_id, [screenshot])}</div>' if screenshot else "")
                + '</div></li>'
            )
        return '<ol class="evidence-timeline">' + "".join(rendered) + '</ol>' if rendered else (
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

    def _scenario_card(self, exploration_id: str, scenario: dict[str, Any]) -> str:
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
            <details class="case-documentation"><summary>Descrição e passos do caso</summary>
            <div class="columns"><section><h3>Pré-condições</h3><ul>{preconditions}</ul></section><section><h3>Passos</h3><ol>{steps}</ol></section></div>
            <div class="result"><section><h3>Esperado</h3><p>{_h(scenario.get("expected_result"))}</p></section><section><h3>Obtido</h3><p>{_h(scenario.get("actual_result"))}</p></section></div></details>"""
        visual_detail = ""
        visual_meta = ""
        visual = evidence.get("visual_assessment") or {}
        if visual.get("requested"):
            visual_meta = " · visual: " + ("analisado" if visual.get("status") == "assessed" else "em análise" if visual.get("status") == "pending" else "indisponível")
            if visual.get("status") == "assessed":
                text = (f"{visual.get('accepted_count', 0)} indício(s) visual(is) · revisão necessária"
                        if visual.get("accepted_count") else "Capturas analisadas · nenhum indício visual aceito")
                note = "A confiança é declarada pelo modelo. Não há aprovação visual completa nem bug funcional confirmado apenas pela imagem."
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
            visual_detail = f'<div class="context-note"><strong>{_h(text)}</strong><p>{_h(note)}</p></div>'
        return f"""<details class="scenario" data-status="{status}">
          <summary><div><span class="case-id">{_h(case_label)}</span><h2>{_h(scenario.get("title"))}</h2><span class="case-meta">{signal_count} sinal(is) técnico(s){visual_meta} · clique para ver a linha do tempo</span></div><span class="badge {badge_class}">{visible_status}</span></summary>
          <div class="scenario-body"><p>{_h(scenario.get("description"))}</p>
          <div class="url">{_h(self._public_issue_url(scenario.get("url")))}</div>
          {verdict_detail}{visual_detail}{details}<h3 class="timeline-heading">Linha do tempo · ações e evidências</h3>
          {self._timeline(exploration_id, scenario)}</div></details>"""

    def generate(self, exploration: dict[str, Any], output_path: str) -> str:
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
