from __future__ import annotations

import base64
import html
import json
import re
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from autojev.services.scenario_timeline import prepare_scenario_timelines


def _h(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


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
                    f'<figure><img loading="lazy" src="{data}" alt="Screenshot de evidência"><figcaption>{_h(Path(path).name)}</figcaption></figure>'
                )
        return "".join(images) or '<p class="muted">Nenhuma captura associada.</p>'

    @staticmethod
    def _duration(exploration: dict[str, Any]) -> str:
        try:
            started = datetime.fromisoformat(str(exploration["started_at"]))
            finished = datetime.fromisoformat(str(exploration["finished_at"]))
            seconds = max(int((finished - started).total_seconds()), 0)
            minutes, seconds = divmod(seconds, 60)
            hours, minutes = divmod(minutes, 60)
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        except (KeyError, TypeError, ValueError):
            return "—"

    @staticmethod
    def _usage(exploration: dict[str, Any]) -> tuple[str, str]:
        usage = exploration.get("usage") or {}
        tokens = f"{int(usage.get('total_tokens') or 0):,}".replace(",", ".")
        if not usage.get("cost_known"):
            return tokens, "—"
        return tokens, f"${float(usage.get('cost') or 0):.6f}"

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
        return labels.get(status, status or "Desconhecido"), reason

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
        label = "Verificação técnica" if technical else "Verificação observada"
        note = (
            "Erros de console/rede não demonstram, isoladamente, um defeito "
            "funcional. O resultado desta verificação foi preservado."
            if technical
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
                value = assertion[key]
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                assertion_values += f'<div><span class="muted">{value_label}: </span>{_h(value)}</div>'
        return (
            f'<div class="assertion-detail"><span class="event-chip">{label}</span> '
            f'<strong>{_h(kind)}</strong><p>{verdict}: '
            f'{_h(assertion.get("message"))}</p>{assertion_values}<p class="muted">{note}</p></div>'
        )

    def _signal_detail(self, signal: dict[str, Any]) -> str:
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
        channel_label = " + ".join(str(item) for item in channels if item)
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
            + "".join(f'<p>{_h(message)}</p>' for message in messages)
            + "".join(
                f'<div class="url">{_h(self._public_issue_url(url))}</div>' for url in urls
            )
            + (f'<p class="muted">{_h(explanation)}</p>' if explanation else "")
            + f'<p class="impact-note">{_h(impact)}</p>'
            + (f'<p class="muted">Vínculo: {_h(association)}</p>' if association else "")
            + related_detail
            + '</div>'
        )

    def _timeline(self, exploration_id: str, scenario: dict[str, Any]) -> str:
        evidence = scenario.get("evidence") or {}
        entries = list(evidence.get("timeline") or [])
        used_shots = {
            str(item.get("screenshot")) for item in entries if item.get("screenshot")
        }
        # Preserve legacy captures even if a historical record cannot establish
        # a precise timestamp or link to an action. Never invent that ordering.
        for screenshot in scenario.get("screenshots") or []:
            if str(screenshot) not in used_shots:
                entries.append({
                    "kind": "observation",
                    "title": "Captura preservada",
                    "message": "Evidência histórica sem horário de captura registrado.",
                    "screenshot": screenshot,
                })
                used_shots.add(str(screenshot))
        rendered: list[str] = []
        labels = {
            "observation": "Observação",
            "action": "Ação",
            "assertion": "Verificação",
            "signal": "Sinal técnico",
        }
        for entry in entries:
            kind = str(entry.get("kind") or "observation")
            if kind not in labels:
                kind = "observation"
            screenshot = str(entry.get("screenshot") or "")
            detail = ""
            if isinstance(entry.get("signal"), dict):
                detail = self._signal_detail(entry["signal"])
            if isinstance(entry.get("assertion"), dict):
                detail += self._assertion_detail(entry["assertion"])
            timestamp = entry.get("occurred_at")
            step = entry.get("step_number", entry.get("action_step"))
            action_meta = (
                f'<p class="muted">{f"Etapa {_h(step)}" if step is not None else ""}'
                f'{f" · Alvo: {_h(entry.get('target'))}" if entry.get("target") else ""}</p>'
            ) if step is not None or entry.get("target") else ""
            rendered.append(
                f'<li class="timeline-entry {kind}"><div class="timeline-marker" aria-hidden="true"></div>'
                '<div class="timeline-content"><div class="event-meta">'
                f'<span class="event-kind">{labels[kind]}</span>'
                f'<time datetime="{_h(timestamp)}">{_h(self._timeline_time(timestamp))}</time></div>'
                f'<h3>{_h(entry.get("title") or labels[kind])}</h3>'
                + action_meta
                + (f'<p>{_h(entry.get("message"))}</p>' if entry.get("message") else "")
                + (f'<div class="url">{_h(self._public_issue_url(entry.get("url")))}</div>' if entry.get("url") else "")
                + detail
                + (f'<div class="shots">{self._screenshots(exploration_id, [screenshot])}</div>' if screenshot else "")
                + '</div></li>'
            )
        return '<ol class="evidence-timeline">' + "".join(rendered) + '</ol>' if rendered else (
            '<p class="muted">Não há evidência temporal registrada para este caso.</p>'
        )

    def _scenario_card(self, exploration_id: str, scenario: dict[str, Any], *, context: bool = False) -> str:
        status = "INFO" if context else str(scenario.get("status", "SKIPPED"))
        if status not in {"PASS", "FAIL", "BLOCKED", "SKIPPED", "INFO"}:
            status = "SKIPPED"
        steps = "".join(f"<li>{_h(step)}</li>" for step in scenario.get("steps", []))
        preconditions = "".join(
            f"<li>{_h(item)}</li>" for item in scenario.get("preconditions", [])
        ) or "<li>Nenhuma pré-condição documentada.</li>"
        visible_status = "CONTEXTO" if context else "SEM VALIDAÇÃO" if status == "SKIPPED" else status
        evidence = scenario.get("evidence") or {}
        verdict = evidence.get("verdict") or {}
        technical_failure = isinstance(verdict, dict) and verdict.get("kind") == "technical_failure"
        if status == "FAIL" and technical_failure:
            visible_status = "FAIL · VERIFICAÇÃO TÉCNICA"
        badge_class = "blocked" if technical_failure else status.lower()
        verdict_detail = (
            f'<div class="verdict-note"><strong>{_h(verdict.get("label"))}</strong>'
            f'<p>{_h(verdict.get("explanation"))}</p>'
            + ('<p class="muted">Resultado histórico preservado; esta apresentação não reexecuta o teste.</p>' if verdict.get("historical") else '')
            + '</div>'
        ) if isinstance(verdict, dict) and verdict.get("label") and not context else ''
        signal_count = len(evidence.get("technical_signals") or [])
        case_label = "Evidência de contexto · não é um CT" if context else str(scenario.get("id") or "CT")
        description = (
            '<p class="context-note">Registros de início, restauração ou sem vínculo confiável '
            'com um CT. Não são testes aprovados e não entram na contagem de CTs.</p>'
            if context else ''
        )
        details = '' if context else f"""
            <details class="case-documentation"><summary>Descrição e passos do caso</summary>
            <div class="columns"><section><h3>Pré-condições</h3><ul>{preconditions}</ul></section><section><h3>Passos</h3><ol>{steps}</ol></section></div>
            <div class="result"><section><h3>Esperado</h3><p>{_h(scenario.get("expected_result"))}</p></section><section><h3>Obtido</h3><p>{_h(scenario.get("actual_result"))}</p></section></div></details>"""
        return f"""<details class="scenario{' scenario-context' if context else ''}" data-status="{status}">
          <summary><div><span class="case-id">{_h(case_label)}</span><h2>{_h(scenario.get("title"))}</h2><span class="case-meta">{signal_count} sinal(is) técnico(s) · clique para ver a linha do tempo</span></div><span class="badge {badge_class}">{visible_status}</span></summary>
          <div class="scenario-body">{description}<p>{_h(scenario.get("description"))}</p>
          <div class="url">{_h(self._public_issue_url(scenario.get("url")))}</div>
          {verdict_detail}{details}<h3 class="timeline-heading">Linha do tempo · ações e evidências</h3>
          {self._timeline(exploration_id, scenario)}</div></details>"""

    def generate(self, exploration: dict[str, Any], output_path: str) -> str:
        exploration = prepare_scenario_timelines(exploration)
        exploration_id = str(exploration["id"])
        scenarios = exploration.get("scenarios", [])
        summary = exploration.get("summary", {})
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
        tokens, cost = self._usage(exploration)
        completion_status, completion_reason = self._completion(exploration)
        cards = [self._scenario_card(exploration_id, scenario) for scenario in scenarios]
        cards.extend(
            self._scenario_card(exploration_id, context, context=True)
            for context in exploration.get("scenario_contexts") or []
        )
        generated = datetime.now().astimezone().strftime("%d/%m/%Y %H:%M:%S %Z")
        safe_filter_data = json.dumps(["ALL", "PASS", "FAIL", "BLOCKED", "SKIPPED", "INFO"])
        document = f"""<!doctype html>
<html lang="pt-BR" data-report-schema="ct-timeline-v1"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AutoJev — Exploratory Test Report</title>
<style>
:root{{--bg:#0b0e14;--panel:#141923;--line:#293142;--text:#edf1f7;--muted:#9ca8ba;--accent:#f0b44d;--pass:#3ddc97;--fail:#ff647c;--blocked:#ffa657;--skipped:#8b98aa;--info:#83bffc}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 Inter,system-ui,sans-serif}}main{{max-width:1120px;margin:auto;padding:48px 24px}}
.eyebrow,.case-id{{color:var(--accent);font-weight:800;letter-spacing:.12em;text-transform:uppercase;font-size:12px}}h1{{font-size:40px;margin:.15em 0}}h2{{margin:.2em 0;font-size:22px;overflow-wrap:anywhere}}h3{{font-size:13px;text-transform:uppercase;color:var(--muted)}}p{{overflow-wrap:anywhere}}.muted,small{{color:var(--muted)}}
.meta,.summary,.columns,.result{{display:grid;gap:12px}}.meta{{grid-template-columns:repeat(auto-fit,minmax(180px,1fr));margin:24px 0}}.summary{{grid-template-columns:repeat(auto-fit,minmax(120px,1fr));margin:20px 0 28px}}
.completion,.reading-guide{{padding:14px 16px;border:1px solid var(--line);border-radius:10px;background:var(--panel);margin:0 0 20px}}.completion{{border-left:3px solid var(--accent)}}.completion strong{{margin-right:8px}}.reading-guide{{color:var(--muted)}}.reading-guide strong{{color:var(--text)}}
.metric,.scenario{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px}}.metric strong{{display:block;font-size:28px}}.report-tools{{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:24px 0}}.search{{flex:1;min-width:240px;background:#10151e;color:var(--text);border:1px solid var(--line);border-radius:10px;padding:10px 13px}}.filters{{display:flex;gap:8px;flex-wrap:wrap}}button{{background:#1c2330;color:var(--text);border:1px solid var(--line);border-radius:999px;padding:8px 14px;cursor:pointer}}button.active{{border-color:var(--accent);color:var(--accent)}}button:focus-visible,summary:focus-visible{{outline:2px solid var(--accent);outline-offset:5px}}
.scenario{{margin:16px 0}}.scenario>summary{{display:flex;justify-content:space-between;gap:20px;cursor:pointer;list-style:none;align-items:flex-start}}.scenario>summary::-webkit-details-marker{{display:none}}.scenario>summary>div{{min-width:0}}.case-meta{{font-size:12px;color:var(--muted)}}.scenario-body{{padding-top:12px;margin-top:16px;border-top:1px solid var(--line)}}.badge{{height:max-content;border-radius:999px;padding:5px 10px;font-weight:800;font-size:12px;white-space:nowrap}}.pass{{color:var(--pass);background:#143328}}.fail{{color:var(--fail);background:#3a1720}}.blocked{{color:var(--blocked);background:#3c2814}}.skipped{{color:var(--skipped);background:#252c36}}.info{{color:var(--info);background:#162a40}}
.url{{color:var(--muted);font:12px/1.6 ui-monospace,monospace;overflow-wrap:anywhere}}.columns,.result{{grid-template-columns:1fr 1fr;margin:15px 0}}.case-documentation{{border-top:1px solid var(--line);padding:14px 0;margin-top:16px}}.case-documentation>summary{{cursor:pointer;color:var(--muted)}}.verdict-note,.context-note{{border:1px solid var(--line);border-radius:10px;background:#10151e;padding:12px 14px;margin:16px 0}}.verdict-note p{{margin:6px 0}}.context-note{{border-color:#29465d;color:#b2cce1}}.scenario-context .case-id{{color:var(--info)}}
.timeline-heading{{margin:22px 0 14px}}.evidence-timeline{{list-style:none;padding:0;margin:0}}.timeline-entry{{position:relative;display:grid;grid-template-columns:14px minmax(0,1fr);gap:14px;padding-bottom:18px}}.timeline-entry:not(:last-child)::before{{content:"";position:absolute;left:6px;top:14px;bottom:0;width:1px;background:var(--line)}}.timeline-marker{{position:relative;width:13px;height:13px;border:3px solid var(--panel);border-radius:50%;background:#728397;margin-top:6px;box-shadow:0 0 0 1px var(--line)}}.action .timeline-marker{{background:var(--accent)}}.signal .timeline-marker{{background:var(--blocked)}}.assertion .timeline-marker{{background:var(--info)}}.timeline-content{{min-width:0;border:1px solid var(--line);border-radius:10px;background:#10151e;padding:14px 16px}}.event-meta{{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;font-size:11px;color:var(--muted)}}.event-kind{{letter-spacing:.09em;text-transform:uppercase;font-weight:750}}.timeline-content h3{{font-size:16px;text-transform:none;color:var(--text);margin:7px 0}}.timeline-content p{{margin:8px 0}}.event-chip{{display:inline-block;font-size:11px;border:1px solid var(--line);padding:3px 8px;border-radius:6px;color:var(--info)}}.signal-chip{{color:var(--blocked);border-color:#6a4b24}}.signal-head{{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}}.signal-detail,.assertion-detail{{padding-top:10px;margin-top:10px;border-top:1px solid var(--line)}}.impact-note{{color:#c5bca9;font-size:13px}}.shots{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:12px;margin-top:16px}}figure{{margin:0;min-width:0}}img{{display:block;width:100%;border-radius:9px;border:1px solid var(--line)}}figcaption{{color:var(--muted);font-size:12px;margin-top:5px;overflow-wrap:anywhere}}
@media(max-width:720px){{main{{padding:28px 14px}}h1{{font-size:30px}}.summary{{grid-template-columns:1fr 1fr}}.meta,.columns,.result{{grid-template-columns:1fr}}.scenario{{padding:14px}}.scenario h2{{font-size:18px}}.scenario>summary{{gap:10px}}.timeline-entry{{gap:10px}}.timeline-content{{padding:12px}}.search{{min-width:0;width:100%;flex-basis:100%}}}}@media(prefers-reduced-motion:reduce){{*{{scroll-behavior:auto}}}}
</style></head><body data-report-schema="ct-timeline-v1"><main>
<div class="eyebrow">AutoJev</div><h1>Exploratory Test Report</h1><p class="muted">Relatório autônomo com evidências observadas pelo Playwright.</p>
<div class="meta"><div><strong>Aplicação</strong><div class="url">{_h(exploration.get("app_url"))}</div></div><div><strong>Escopo</strong><div>{_h(scope)}</div></div><div><strong>Evidência da LLM</strong><div>{_h(evidence_mode_label)}</div></div><div><strong>Status</strong><div>{_h(completion_status)}</div></div><div><strong>Duração</strong><div>{_h(self._duration(exploration))}</div></div><div><strong>Gerado em</strong><div>{_h(generated)}</div></div></div>
{f'<div class="completion"><strong>Motivo do encerramento:</strong>{_h(completion_reason)}</div>' if completion_reason else ''}
<section class="summary"><div class="metric"><strong>{int(summary.get("total_scenarios", 0))}</strong>Total</div><div class="metric"><strong>{int(summary.get("pass", 0))}</strong>PASS</div><div class="metric"><strong>{int(summary.get("fail", 0))}</strong>FAIL</div><div class="metric"><strong>{int(summary.get("blocked", 0))}</strong>BLOCKED</div><div class="metric"><strong>{int(summary.get("skipped", 0))}</strong>Sem validação</div><div class="metric"><strong>{_h(tokens)}</strong>Tokens</div><div class="metric"><strong>{_h(cost)}</strong>Custo</div></section>
<div class="reading-guide"><strong>Cada caso conta sua história.</strong> Abra um CT para ver ações, capturas, verificações e sinais técnicos no momento em que foram registrados. Um sinal de console/rede não é automaticamente um defeito funcional. Registros sem vínculo confiável ficam em cartões de contexto, sem contar como CTs.</div>
<div class="report-tools"><input class="search" id="search" type="search" placeholder="Buscar por CT, URL, sinal ou evidência" aria-label="Buscar cenários"><div class="filters" id="filters"></div></div><section id="scenarios">{"".join(cards) or '<p class="muted">Nenhum cenário gerado.</p>'}</section>
</main><script>
const statuses={safe_filter_data};const root=document.getElementById('filters');const search=document.getElementById('search');let active='ALL';
function apply(){{const term=search.value.trim().toLowerCase();document.querySelectorAll('.scenario').forEach(x=>{{x.hidden=(active!=='ALL'&&x.dataset.status!==active)||(term&&!x.textContent.toLowerCase().includes(term))}})}}
for(const status of statuses){{const b=document.createElement('button');b.textContent=status==='SKIPPED'?'SEM VALIDAÇÃO':status==='INFO'?'CONTEXTO':status==='ALL'?'TODOS':status;b.className=status==='ALL'?'active':'';b.onclick=()=>{{active=status;document.querySelectorAll('#filters button').forEach(x=>x.classList.remove('active'));b.classList.add('active');apply()}};root.appendChild(b)}}search.addEventListener('input',apply);
</script></body></html>"""
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(document, encoding="utf-8")
        return str(output)
