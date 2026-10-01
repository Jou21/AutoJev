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

    def generate(self, exploration: dict[str, Any], output_path: str) -> str:
        exploration_id = str(exploration["id"])
        scenarios = exploration.get("scenarios", [])
        issues = exploration.get("issues", [])
        issue_groups = self._group_issues(issues)
        occurrence_label = (
            "ocorrência" if len(issue_groups) == 1 else "ocorrências"
        )
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
        cards: list[str] = []
        for scenario in scenarios:
            status = str(scenario.get("status", "SKIPPED"))
            if status not in {"PASS", "FAIL", "BLOCKED", "SKIPPED"}:
                status = "SKIPPED"
            steps = "".join(
                f"<li>{_h(step)}</li>" for step in scenario.get("steps", [])
            )
            preconditions = (
                "".join(
                    f"<li>{_h(item)}</li>" for item in scenario.get("preconditions", [])
                )
                or "<li>Nenhuma pré-condição documentada.</li>"
            )
            assertions = scenario.get("evidence", {}).get("assertions", [])
            evidence = (
                "".join(
                    "<li><strong>{}</strong>: {} — {}</li>".format(
                        _h(item.get("kind")),
                        "confirmada" if item.get("passed") else "não confirmada",
                        _h(item.get("message")),
                    )
                    for item in assertions
                )
                or "<li>Sem asserção verificável; cenário não pode receber PASS.</li>"
            )
            visible_status = "SEM VALIDAÇÃO" if status == "SKIPPED" else status
            cards.append(
                f"""<details class="scenario" data-status="{status}">
                  <summary><div><span class="case-id">{_h(scenario.get("id"))}</span><h2>{_h(scenario.get("title"))}</h2></div><span class="badge {status.lower()}">{visible_status}</span></summary>
                  <div class="scenario-body">
                  <p>{_h(scenario.get("description"))}</p>
                  <div class="url">{_h(scenario.get("url"))}</div>
                  <div class="columns"><section><h3>Pré-condições</h3><ul>{preconditions}</ul></section><section><h3>Passos</h3><ol>{steps}</ol></section></div>
                  <div class="result"><section><h3>Esperado</h3><p>{_h(scenario.get("expected_result"))}</p></section><section><h3>Obtido</h3><p>{_h(scenario.get("actual_result"))}</p></section></div>
                  <details><summary>Evidência verificada</summary><ul>{evidence}</ul></details>
                  <div class="shots">{self._screenshots(exploration_id, scenario.get("screenshots", []))}</div>
                  </div>
                </details>"""
            )
        issue_cards = (
            "".join(
                f"""<article class="issue"><div class="issue-head"><strong>{_h(" + ".join(group["types"]))}</strong>
            <span>{_h(group.get("status_code") or "")}</span></div>
            <p class="muted">{int(group["count"])} sinal(is) correlacionado(s) nesta ocorrência.</p>
            {''.join(f'<p>{_h(message)}</p>' for message in group["messages"])}
            {''.join(f'<small class="issue-url">{_h(url)}</small>' for url in group["urls"])}
            <div class="shots">{self._screenshots(exploration_id, [group["screenshot"]] if group.get("screenshot") else [])}</div></article>"""
                for group in issue_groups
            )
            or '<p class="muted">Nenhum erro de console ou rede capturado.</p>'
        )
        generated = datetime.now().astimezone().strftime("%d/%m/%Y %H:%M:%S %Z")
        safe_filter_data = json.dumps(["ALL", "PASS", "FAIL", "BLOCKED", "SKIPPED"])
        document = f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AutoJev — Exploratory Test Report</title>
<style>
:root{{--bg:#0b0e14;--panel:#141923;--line:#293142;--text:#edf1f7;--muted:#9ca8ba;--accent:#f0b44d;--pass:#3ddc97;--fail:#ff647c;--blocked:#ffa657;--skipped:#8b98aa}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 Inter,system-ui,sans-serif}}main{{max-width:1120px;margin:auto;padding:48px 24px}}.eyebrow,.case-id{{color:var(--accent);font-weight:800;letter-spacing:.12em;text-transform:uppercase}}h1{{font-size:40px;margin:.15em 0}}h2{{margin:.2em 0;font-size:22px}}h3{{font-size:13px;text-transform:uppercase;color:var(--muted)}}.muted,small{{color:var(--muted)}}.meta,.summary,.columns,.result{{display:grid;gap:12px}}.meta{{grid-template-columns:repeat(auto-fit,minmax(180px,1fr));margin:24px 0}}.summary{{grid-template-columns:repeat(auto-fit,minmax(120px,1fr));margin:20px 0 28px}}.completion{{padding:14px 16px;border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:10px;background:var(--panel);margin:0 0 24px}}.completion strong{{margin-right:8px}}.metric,.scenario,.issue{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px}}.metric strong{{display:block;font-size:28px}}.report-tools{{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:24px 0}}.search{{flex:1;min-width:240px;background:#10151e;color:var(--text);border:1px solid var(--line);border-radius:10px;padding:10px 13px}}.filters{{display:flex;gap:8px;flex-wrap:wrap}}button{{background:#1c2330;color:var(--text);border:1px solid var(--line);border-radius:999px;padding:8px 14px;cursor:pointer}}button.active{{border-color:var(--accent);color:var(--accent)}}.scenario{{margin:16px 0}}.scenario>summary{{display:flex;justify-content:space-between;gap:20px;cursor:pointer;list-style:none}}.scenario>summary::-webkit-details-marker{{display:none}}.scenario-body{{padding-top:12px}}.badge{{height:max-content;border-radius:999px;padding:5px 10px;font-weight:800}}.pass{{color:var(--pass);background:#143328}}.fail{{color:var(--fail);background:#3a1720}}.blocked{{color:var(--blocked);background:#3c2814}}.skipped{{color:var(--skipped);background:#252c36}}.url{{color:var(--muted);font-family:monospace;overflow-wrap:anywhere}}.columns,.result{{grid-template-columns:1fr 1fr;margin:15px 0}}.scenario-body>details{{border-top:1px solid var(--line);padding-top:12px}}.shots{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin-top:16px}}figure{{margin:0}}img{{width:100%;border-radius:9px;border:1px solid var(--line)}}figcaption{{color:var(--muted);font-size:12px}}.issue{{margin:10px 0;border-left:3px solid var(--fail)}}.issue-head{{display:flex;justify-content:space-between;gap:12px}}.issue-url{{display:block;font-family:monospace;overflow-wrap:anywhere;margin-top:4px}}@media(max-width:720px){{main{{padding:28px 14px}}h1{{font-size:30px}}.summary{{grid-template-columns:1fr 1fr}}.meta,.columns,.result{{grid-template-columns:1fr}}}}
</style></head><body><main>
<div class="eyebrow">AutoJev</div><h1>Exploratory Test Report</h1><p class="muted">Relatório autônomo com evidências observadas pelo Playwright.</p>
<div class="meta"><div><strong>Aplicação</strong><div class="url">{_h(exploration.get("app_url"))}</div></div><div><strong>Escopo</strong><div>{_h(scope)}</div></div><div><strong>Evidência da LLM</strong><div>{_h(evidence_mode_label)}</div></div><div><strong>Status</strong><div>{_h(completion_status)}</div></div><div><strong>Duração</strong><div>{_h(self._duration(exploration))}</div></div><div><strong>Gerado em</strong><div>{_h(generated)}</div></div></div>
{f'<div class="completion"><strong>Motivo do encerramento:</strong>{_h(completion_reason)}</div>' if completion_reason else ''}
<section class="summary"><div class="metric"><strong>{int(summary.get("total_scenarios", 0))}</strong>Total</div><div class="metric"><strong>{int(summary.get("pass", 0))}</strong>PASS</div><div class="metric"><strong>{int(summary.get("fail", 0))}</strong>FAIL</div><div class="metric"><strong>{int(summary.get("blocked", 0))}</strong>BLOCKED</div><div class="metric"><strong>{int(summary.get("skipped", 0))}</strong>Sem validação</div><div class="metric"><strong>{_h(tokens)}</strong>Tokens</div><div class="metric"><strong>{_h(cost)}</strong>Custo</div></section>
<div class="report-tools"><input class="search" id="search" type="search" placeholder="Buscar por cenário, URL ou evidência" aria-label="Buscar cenários"><div class="filters" id="filters"></div></div><section id="scenarios">{"".join(cards) or '<p class="muted">Nenhum cenário gerado.</p>'}</section>
<h2>Sinais técnicos observados ({len(issue_groups)} {occurrence_label})</h2><p class="muted">{len(issues)} registro(s) persistido(s) de console/rede foram correlacionados por requisição quando havia evidência temporal. Um mesmo request pode aparecer nos dois canais e não representa dois defeitos; requisições distintas continuam separadas.</p><section>{issue_cards}</section>
</main><script>
const statuses={safe_filter_data};const root=document.getElementById('filters');const search=document.getElementById('search');let active='ALL';
function apply(){{const term=search.value.trim().toLowerCase();document.querySelectorAll('.scenario').forEach(x=>{{x.hidden=(active!=='ALL'&&x.dataset.status!==active)||(term&&!x.textContent.toLowerCase().includes(term))}})}}
for(const status of statuses){{const b=document.createElement('button');b.textContent=status==='SKIPPED'?'SEM VALIDAÇÃO':status;b.className=status==='ALL'?'active':'';b.onclick=()=>{{active=status;document.querySelectorAll('#filters button').forEach(x=>x.classList.remove('active'));b.classList.add('active');apply()}};root.appendChild(b)}}search.addEventListener('input',apply);
</script></body></html>"""
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(document, encoding="utf-8")
        return str(output)
