from __future__ import annotations

import base64
import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _h(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


class StandaloneHtmlReport:
    """Renders a self-contained, escaped HTML report with inline PNG evidence."""

    def __init__(self, artifact_root: Path) -> None:
        self.artifact_root = artifact_root.resolve()

    def _image_data(self, exploration_id: str, relative_path: str) -> str | None:
        candidate = (
            self.artifact_root / exploration_id / "screenshots" / Path(relative_path).name
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

    def generate(self, exploration: dict[str, Any], output_path: str) -> str:
        exploration_id = str(exploration["id"])
        scenarios = exploration.get("scenarios", [])
        issues = exploration.get("issues", [])
        summary = exploration.get("summary", {})
        cards: list[str] = []
        for scenario in scenarios:
            status = str(scenario.get("status", "SKIPPED"))
            if status not in {"PASS", "FAIL", "BLOCKED", "SKIPPED"}:
                status = "SKIPPED"
            steps = "".join(f"<li>{_h(step)}</li>" for step in scenario.get("steps", []))
            preconditions = "".join(
                f"<li>{_h(item)}</li>" for item in scenario.get("preconditions", [])
            ) or "<li>Nenhuma pré-condição documentada.</li>"
            assertions = scenario.get("evidence", {}).get("assertions", [])
            evidence = "".join(
                "<li><strong>{}</strong>: {} — {}</li>".format(
                    _h(item.get("kind")),
                    "confirmada" if item.get("passed") else "não confirmada",
                    _h(item.get("message")),
                )
                for item in assertions
            ) or "<li>Sem asserção verificável; cenário não pode receber PASS.</li>"
            cards.append(
                f"""<details class="scenario" data-status="{status}">
                  <summary><div><span class="case-id">{_h(scenario.get('id'))}</span><h2>{_h(scenario.get('title'))}</h2></div><span class="badge {status.lower()}">{status}</span></summary>
                  <div class="scenario-body">
                  <p>{_h(scenario.get('description'))}</p>
                  <div class="url">{_h(scenario.get('url'))}</div>
                  <div class="columns"><section><h3>Pré-condições</h3><ul>{preconditions}</ul></section><section><h3>Passos</h3><ol>{steps}</ol></section></div>
                  <div class="result"><section><h3>Esperado</h3><p>{_h(scenario.get('expected_result'))}</p></section><section><h3>Obtido</h3><p>{_h(scenario.get('actual_result'))}</p></section></div>
                  <details><summary>Evidência verificada</summary><ul>{evidence}</ul></details>
                  <div class="shots">{self._screenshots(exploration_id, scenario.get('screenshots', []))}</div>
                  </div>
                </details>"""
            )
        issue_cards = "".join(
            f"""<article class="issue"><strong>{_h(issue.get('type') or issue.get('kind'))}</strong>
            <span>{_h(issue.get('status_code'))}</span><p>{_h(issue.get('message'))}</p><small>{_h(issue.get('url'))}</small>
            <div class="shots">{self._screenshots(exploration_id, [issue['screenshot']] if issue.get('screenshot') else [])}</div></article>"""
            for issue in issues
        ) or '<p class="muted">Nenhum erro de console ou rede capturado.</p>'
        generated = datetime.now().astimezone().strftime("%d/%m/%Y %H:%M:%S %Z")
        safe_filter_data = json.dumps(["ALL", "PASS", "FAIL", "BLOCKED", "SKIPPED"])
        document = f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AutoJev — Exploratory Test Report</title>
<style>
:root{{--bg:#0b0e14;--panel:#141923;--line:#293142;--text:#edf1f7;--muted:#9ca8ba;--accent:#f0b44d;--pass:#3ddc97;--fail:#ff647c;--blocked:#ffa657;--skipped:#8b98aa}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 Inter,system-ui,sans-serif}}main{{max-width:1120px;margin:auto;padding:48px 24px}}.eyebrow,.case-id{{color:var(--accent);font-weight:800;letter-spacing:.12em;text-transform:uppercase}}h1{{font-size:40px;margin:.15em 0}}h2{{margin:.2em 0;font-size:22px}}h3{{font-size:13px;text-transform:uppercase;color:var(--muted)}}.muted,small{{color:var(--muted)}}.meta,.summary,.columns,.result{{display:grid;gap:12px}}.meta{{grid-template-columns:2fr 1fr 1fr;margin:24px 0}}.summary{{grid-template-columns:repeat(5,1fr);margin:20px 0 28px}}.metric,.scenario,.issue{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px}}.metric strong{{display:block;font-size:28px}}.report-tools{{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:24px 0}}.search{{flex:1;min-width:240px;background:#10151e;color:var(--text);border:1px solid var(--line);border-radius:10px;padding:10px 13px}}.filters{{display:flex;gap:8px;flex-wrap:wrap}}button{{background:#1c2330;color:var(--text);border:1px solid var(--line);border-radius:999px;padding:8px 14px;cursor:pointer}}button.active{{border-color:var(--accent);color:var(--accent)}}.scenario{{margin:16px 0}}.scenario>summary{{display:flex;justify-content:space-between;gap:20px;cursor:pointer;list-style:none}}.scenario>summary::-webkit-details-marker{{display:none}}.scenario-body{{padding-top:12px}}.badge{{height:max-content;border-radius:999px;padding:5px 10px;font-weight:800}}.pass{{color:var(--pass);background:#143328}}.fail{{color:var(--fail);background:#3a1720}}.blocked{{color:var(--blocked);background:#3c2814}}.skipped{{color:var(--skipped);background:#252c36}}.url{{color:var(--muted);font-family:monospace;overflow-wrap:anywhere}}.columns,.result{{grid-template-columns:1fr 1fr;margin:15px 0}}.scenario-body>details{{border-top:1px solid var(--line);padding-top:12px}}.shots{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin-top:16px}}figure{{margin:0}}img{{width:100%;border-radius:9px;border:1px solid var(--line)}}figcaption{{color:var(--muted);font-size:12px}}.issue{{margin:10px 0;border-left:3px solid var(--fail)}}@media(max-width:720px){{.summary{{grid-template-columns:1fr 1fr}}.meta,.columns,.result{{grid-template-columns:1fr}}}}
</style></head><body><main>
<div class="eyebrow">AutoJev</div><h1>Exploratory Test Report</h1><p class="muted">Relatório autônomo com evidências observadas pelo Playwright.</p>
<div class="meta"><div><strong>Aplicação</strong><div class="url">{_h(exploration.get('app_url'))}</div></div><div><strong>Duração</strong><div>{_h(self._duration(exploration))}</div></div><div><strong>Gerado em</strong><div>{_h(generated)}</div></div></div>
<section class="summary"><div class="metric"><strong>{int(summary.get('total_scenarios',0))}</strong>Total</div><div class="metric"><strong>{int(summary.get('pass',0))}</strong>PASS</div><div class="metric"><strong>{int(summary.get('fail',0))}</strong>FAIL</div><div class="metric"><strong>{int(summary.get('blocked',0))}</strong>BLOCKED</div><div class="metric"><strong>{int(summary.get('skipped',0))}</strong>SKIPPED</div></section>
<div class="report-tools"><input class="search" id="search" type="search" placeholder="Buscar por cenário, URL ou evidência" aria-label="Buscar cenários"><div class="filters" id="filters"></div></div><section id="scenarios">{''.join(cards) or '<p class="muted">Nenhum cenário gerado.</p>'}</section>
<h2>Problemas observados ({len(issues)})</h2><section>{issue_cards}</section>
</main><script>
const statuses={safe_filter_data};const root=document.getElementById('filters');const search=document.getElementById('search');let active='ALL';
function apply(){{const term=search.value.trim().toLowerCase();document.querySelectorAll('.scenario').forEach(x=>{{x.hidden=(active!=='ALL'&&x.dataset.status!==active)||(term&&!x.textContent.toLowerCase().includes(term))}})}}
for(const status of statuses){{const b=document.createElement('button');b.textContent=status;b.className=status==='ALL'?'active':'';b.onclick=()=>{{active=status;document.querySelectorAll('#filters button').forEach(x=>x.classList.remove('active'));b.classList.add('active');apply()}};root.appendChild(b)}}search.addEventListener('input',apply);
</script></body></html>"""
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(document, encoding="utf-8")
        return str(output)
