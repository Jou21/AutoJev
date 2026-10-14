"""Offline, navigable presentation of recorded exploratory-test evidence.

No provider/browser calls, invented coverage percentages or rewritten verdicts.
The existing evidence renderer remains the source of each case's full timeline.
"""
from __future__ import annotations

from collections import Counter, OrderedDict
from datetime import datetime
import html
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from autojev.services.execution_summary import execution_duration_summary
from autojev.services.report_localization import check_label
from autojev.services.verification_summary import evidence_verification_summary

REPORT_LAYOUT_VERSION = "premium-v1"
ASSETS = Path(__file__).with_name("report_assets")
STATUS_LABELS = {"PASS": "Aprovado", "FAIL": "Reprovado", "BLOCKED": "Bloqueado", "SKIPPED": "Sem validação"}
CATEGORIES = {"business": "Asserção de negócio", "interaction": "Interação técnica", "visual": "Indício visual", "execution": "Execução da automação", "technical": "Verificação técnica", "unverified": "Sem validação"}
NAVIGATION = (
    ("overview", "Visão geral", "home"), ("tests", "Testes", "tests"),
    ("results", "Resultados", "result"), ("journey", "Jornada observada", "route"),
    ("evidence", "Evidências", "image"), ("timeline", "Linha do tempo", "clock"),
    ("coverage", "Regras e cobertura", "shield"), ("settings", "Configurações", "settings"),
)


def _h(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _icon(name: str) -> str:
    paths = {
        "home": '<path d="m3 10 9-7 9 7v10H3z"/><path d="M9 20v-7h6v7"/>',
        "tests": '<path d="m4 6 2 2 4-4M4 13l2 2 4-4M13 6h7M13 13h7M4 20h16"/>',
        "result": '<rect x="4" y="5" width="16" height="16" rx="3"/><path d="M9 5V3h6v2M8 10h8M8 14h5"/>',
        "route": '<circle cx="5" cy="5" r="2"/><circle cx="19" cy="19" r="2"/><path d="M7 5h8a4 4 0 0 1 0 8H9a4 4 0 0 0 0 8h8"/>',
        "image": '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1"/><path d="m3 17 6-6 4 4 3-3 5 5"/>',
        "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
        "shield": '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z"/><path d="m8 12 3 3 5-6"/>',
        "settings": '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="16" cy="17" r="3"/>',
        "download": '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
        "robot": '<rect x="3" y="6" width="18" height="15" rx="5"/><path d="M12 2v4M7 12h1m8 0h1M8 17h8M1 11v6m22-6v6"/>',
        "database": '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
        "file": '<path d="M14 3H5v18h14V8z"/><path d="M14 3v5h5M8 12h8M8 16h8"/>',
        "eye": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
        "chevron": '<path d="m9 5 7 7-7 7"/>',
        "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
    }
    return f'<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{paths.get(name, paths["tests"])}</svg>'


def _timestamp(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo else result.astimezone()
    except (TypeError, ValueError, OverflowError):
        return None


def _seconds(start: Any, end: Any) -> float | None:
    first, last = _timestamp(start), _timestamp(end)
    return max(0, (last - first).total_seconds()) if first and last else None


def _time(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f} s".replace(".", ",")


def _category(scenario: dict[str, Any]) -> str:
    evidence = scenario.get("evidence") or {}
    verdict = (evidence.get("verdict") or {}).get("kind")
    special = {"visual_failure": "visual", "execution_failure": "execution", "technical_failure": "technical"}
    if verdict in special:
        return special[verdict]
    level = evidence_verification_summary(evidence)["level"]
    return {"business": "business", "interaction_only": "interaction"}.get(level, "unverified")


def _page(url: str) -> str:
    try:
        parsed = urlsplit(url)
        return (parsed.path or "/") if parsed.netloc else (url or "Página não registrada")
    except ValueError:
        return "Página não registrada"


def _panel(title: str, content: str, subtitle: str = "") -> str:
    return f'<section class="panel"><div class="panel-heading"><div><h2>{_h(title)}</h2>{f"<p>{_h(subtitle)}</p>" if subtitle else ""}</div></div>{content}</section>'


def _empty(message: str) -> str:
    return f'<p class="empty-state">{_h(message)}</p>'


def _json(value: Any) -> str:
    # Application labels can contain HTML/script terminators. They are data,
    # never executable markup, including when this file is opened offline.
    return json.dumps(value, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render_premium_report(renderer: Any, exploration: dict[str, Any], scenarios: list[dict[str, Any]], contexts: list[dict[str, Any]], legacy_document: str, fragments: dict[str, Any]) -> str:
    cases, pages, rules = [], OrderedDict(), OrderedDict()
    execution_time = execution_duration_summary(exploration)
    started = _timestamp(execution_time.get("started_at"))
    duration = execution_time.get("total_seconds")
    actions = {item.get("step_number"): item.get("result") or {} for item in exploration.get("actions") or []}
    case_html, evidence_cards, execution_events = [], [], []
    evidence_seen: set[str] = set()
    shot_owners: dict[str, list[tuple[int, str]]] = {}
    for owner_index, owner in enumerate(scenarios):
        owner_evidence = owner.get("evidence") or {}
        for path in dict.fromkeys([entry.get("screenshot") for entry in owner_evidence.get("timeline") or [] if entry.get("screenshot")] + list(owner.get("screenshots") or [])):
            shot_owners.setdefault(Path(path).name, []).append((owner_index, str(owner.get("id") or f"CT{owner_index+1:03d}")))
    for index, scenario in enumerate(scenarios):
        evidence = scenario.get("evidence") or {}
        timeline = evidence.get("timeline") or []
        status = str(scenario.get("status") or "SKIPPED").upper()
        status = status if status in STATUS_LABELS else "SKIPPED"
        url = renderer._public_issue_url(scenario.get("url") or "")
        page = _page(url)
        category = _category(scenario)
        elapsed = _seconds(evidence.get("action_started_at"), evidence.get("action_finished_at"))
        first = _timestamp(evidence.get("action_started_at"))
        # Multi-action cases use recorded action durations, not LLM/documentation timestamps.
        steps = {entry.get("step_number", entry.get("action_step")) for entry in timeline if entry.get("kind") == "action"}
        recorded_times = [_seconds(actions[step].get("started_at"), actions[step].get("finished_at")) for step in steps if step in actions]
        if steps and len(recorded_times) == len(steps) and all(value is not None for value in recorded_times):
            elapsed = sum(recorded_times)
        elif len(steps) > 1:
            elapsed = None
        action_starts = [_timestamp(actions[step].get("started_at")) for step in steps if step in actions]
        if any(action_starts):
            first = min(value for value in action_starts if value is not None)
        if first is None:
            first = next((_timestamp(entry.get("occurred_at")) for entry in timeline if _timestamp(entry.get("occurred_at"))), None)
        offset = max(0, (first - started).total_seconds()) if started and first else None
        signals = evidence.get("technical_signals") or []
        case = {"index": index, "id": str(scenario.get("id") or f"CT{index+1:03d}"), "title": str(scenario.get("title") or "Caso sem título"), "status": status, "category": category, "page": page, "url": url, "duration_seconds": elapsed, "signals": len(signals), "offset": offset}
        case["search"] = " ".join([case["id"], case["title"], url, str(scenario.get("description") or ""), str(scenario.get("actual_result") or ""), *[str(item.get("message") or "") for item in signals]])
        cases.append(case)
        record = pages.setdefault(url or "Página não registrada", {"url": url, "page": page, "cases": [], "durations": []})
        record["cases"].append(case)
        if elapsed is not None:
            record["durations"].append(elapsed)
        # Reuse canonical cards, including their embedded images, instead of
        # reading and encoding every capture a second time for this layout.
        prepared_cards = fragments.get("case_cards")
        card = prepared_cards[index] if prepared_cards is not None else renderer._scenario_card(str(exploration["id"]), scenario)
        card = card.replace('<details class="scenario" ', f'<details class="scenario" data-case-index="{index}" hidden ', 1)
        case_html.append(card)
        for assertion in evidence.get("assertions") or []:
            kind = str(assertion.get("kind") or "")
            if kind:
                counts = rules.setdefault(kind, Counter())
                counts["total"] += 1
                counts["passed" if assertion.get("passed") is True else "failed" if assertion.get("passed") is False else "unknown"] += 1
        if first:
            label = STATUS_LABELS[status]
            execution_events.append((first, f'<li class="execution-event" data-event-kind="{status}"><span class="event-time">{_h(first.astimezone().strftime("%H:%M:%S"))}</span><span class="event-dot {status.lower()}"></span><div class="event-body"><button class="case-title-button" data-open-case="{index}">{_h(case["id"])} · {_h(label)}</button><p>{_h(case["title"])}</p><small>{_h(page)} · {_h(CATEGORIES[category])}</small></div></li>'))
        paths = list(dict.fromkeys([entry.get("screenshot") for entry in timeline if entry.get("screenshot")] + list(scenario.get("screenshots") or [])))
        for shot in paths:
            name = Path(shot).name
            key = name
            # Only reference files embedded by the canonical timeline renderer.
            if key in evidence_seen or not (renderer.artifact_root / str(exploration["id"]) / "screenshots" / name).is_file():
                continue
            evidence_seen.add(key)
            owners = shot_owners[name]
            owner_ids = ", ".join(owner_id for _, owner_id in owners)
            links = "".join(f'<button class="button button-ghost" data-open-case="{owner_index}">{_h(owner_id)} →</button>' for owner_index, owner_id in owners)
            evidence_cards.append(f'<article class="evidence-card" data-evidence-kind="screenshot" data-case-index="{index}" data-filename="{_h(name)}" data-search="{_h(owner_ids)}"><button data-preview-evidence aria-label="Ampliar captura {_h(name)}"><img loading="lazy" data-source-case="{index}" data-source-shot="{_h(name)}" alt="{_h(owner_ids)} · {_h(name)}"></button><div class="evidence-meta"><strong>{_h(case["id"])}</strong><p>{_h(name)}</p><small>{_h(page)} · {len(owners)} CT(s) associado(s)</small><div>{links}</div></div></article>')
        if signals:
            evidence_cards.append(f'<article class="evidence-card" data-evidence-kind="log" data-case-index="{index}"><div class="evidence-meta"><span class="event-chip">Console e rede</span><h3>{_h(case["id"])} · {len(signals)} sinais técnicos</h3><p>{_h(str(signals[0].get("message") or "Sinal técnico registrado")[:300])}</p><small>Um sinal técnico não confirma sozinho um defeito funcional.</small><button class="button button-ghost" data-open-case="{index}">Ver na linha do tempo do CT</button></div></article>')
        if evidence.get("assertions"):
            evidence_cards.append(f'<article class="evidence-card" data-evidence-kind="data" data-case-index="{index}"><div class="evidence-meta"><span class="event-chip">Dados e verificações</span><h3>{_h(case["id"])}</h3><p>{len(evidence["assertions"])} condições registradas, com valores esperados e observados.</p><button class="button button-ghost" data-open-case="{index}">Examinar verificações</button></div></article>')

    summary = fragments["summary"]
    total = len(cases)
    counts = Counter(case["status"] for case in cases)
    # Some historical snapshots contain only a summary, without individual CTs.
    displayed = {key: int(summary.get(key.lower(), counts[key])) for key in STATUS_LABELS}
    shown_total = int(summary.get("total_scenarios", total))
    metrics = [(shown_total, "Total", "total"), (displayed["PASS"], "Aprovados", "pass"), (displayed["FAIL"], "Reprovados", "fail"), (displayed["BLOCKED"], "Bloqueados", "blocked"), (displayed["SKIPPED"], "Sem validação", "skipped"), (fragments["tokens"], "Tokens", "tokens")]
    metrics_html = '<section class="summary" aria-label="Resumo dos testes">' + "".join(f'<div class="metric" data-tone="{tone}"><strong>{_h(value)}</strong>{label}</div>' for value, label, tone in metrics) + '</section>'
    legend, gradient, angle = [], [], 0.0
    for status, color in (("PASS", "#24d99b"), ("FAIL", "#ff617b"), ("BLOCKED", "#f5b746"), ("SKIPPED", "#7f91a8")):
        count = displayed[status]
        percent = count / shown_total * 100 if shown_total else 0
        end = angle + percent
        gradient.append(f"{color} {angle:.3f}% {end:.3f}%")
        angle = end
        legend.append(f'<div class="legend-row"><span class="legend-dot" style="background:{color}"></span><span>{STATUS_LABELS[status]}</span><strong>{count}</strong><small>{percent:.1f}% dos CTs</small></div>')
    donut = f'<div class="chart-layout"><div class="donut-wrap"><div class="donut" style="--donut:conic-gradient({",".join(gradient) if shown_total else "#273445 0 100%"})"><div class="donut-center"><strong>{shown_total}</strong><span>testes</span></div></div></div><div class="chart-legend">{"".join(legend)}</div></div>'
    chart = _duration_chart(cases, duration)
    category_counts = Counter(case["category"] for case in cases)
    categories_html = '<div class="category-list">' + "".join(f'<div class="category-row"><span>{label}</span><strong>{category_counts[key]}</strong><div class="progress-track"><span class="progress-fill" style="width:{category_counts[key]/max(1,total)*100:.2f}%"></span></div></div>' for key, label in CATEGORIES.items()) + '</div>'
    top_pages = _pages_table(list(pages.values())[:6], compact=True)
    meta = f'<div class="meta"><div><strong>Aplicação</strong><div class="url">{_h(renderer._public_issue_url(exploration.get("app_url")))}</div></div><div><strong>Escopo</strong><div>{_h(fragments["scope"])}</div></div><div><strong>Evidência da LLM</strong><div>{_h(fragments["evidence_mode"])}</div></div><div><strong>Status</strong><div>{_h(fragments["status"])}</div></div><div><strong>Duração total</strong><div>{_h(renderer._duration(exploration))}</div></div><div><strong>Gerado em</strong><div>{_h(fragments["generated"])}</div></div></div>'
    overview = meta + '<p class="muted">A duração total inclui revisões auxiliares e geração do relatório.</p>' + metrics_html + '<div class="dashboard-grid">' + _panel("Resultado dos testes", donut, "Distribuição dos resultados registrados") + _panel("Atividade ao longo da execução", chart, "Início dos CTs por intervalo · não representa uso de CPU") + _panel("Natureza das verificações", categories_html, "Classificação derivada das evidências de cada CT") + _panel("Páginas com casos registrados", top_pages, "Contagem de CTs, não percentual de cobertura da página") + '</div>' + fragments["cost_summary"] + fragments["visual_summary"] + fragments["strength_summary"]
    rerun_source = next((event.get("payload") for event in exploration.get("events", [])
                         if event.get("type") == "scenario_rerun_source"
                         and isinstance(event.get("payload"), dict)), None)
    if rerun_source:
        overview = (
            '<div class="reading-guide rerun-provenance"><strong>Reexecução individual de '
            + _h(rerun_source.get("source_scenario_id")) + '.</strong> '
            'Este é um novo resultado; o relatório original permanece inalterado. '
            'Foram previstas somente a ação original e as conferências locais. '
            'Análises por modelo e cartões de consulta ao banco não são executados. '
            '<br>Execução de origem: <span class="url">'
            + _h(rerun_source.get("source_exploration_id")) + '</span></div>' + overview
        )
    if shown_total != total:
        overview += f'<p class="reading-guide">Resumo histórico preservado: {shown_total} CTs registrados; detalhes individuais disponíveis de {total}. As listas e filtros usam somente os detalhes preservados.</p>'
    if fragments["completion_reason"]:
        overview += '<div class="completion"><strong>Motivo do encerramento:</strong>' + _h(fragments["completion_reason"]) + '</div>'
    tests = '<div class="report-tools"><input class="search" id="search" type="search" placeholder="Buscar CT, título, URL ou sinal técnico" aria-label="Buscar cenários"><button id="download-csv" class="button button-ghost">Baixar CSV</button></div><div class="report-tools"><div class="filters" id="filters"></div><select id="category-filter" aria-label="Filtrar por categoria"><option value="ALL">Todas as categorias</option>' + "".join(f'<option value="{key}">{label}</option>' for key, label in CATEGORIES.items()) + '</select><select id="page-filter" aria-label="Filtrar por página"><option value="ALL">Todas as páginas</option>' + "".join(f'<option value="{_h(page)}">{_h(page)}</option>' for page in dict.fromkeys(case["page"] for case in cases)) + '</select><button id="clear-filters" class="button button-ghost">Limpar filtros</button></div>'
    tests += '<div class="table-wrap"><table class="report-table"><thead><tr><th>CT</th><th>Título</th><th>Resultado</th><th>Categoria</th><th>Página</th><th>Tempo da ação</th><th>Sinais</th><th>Detalhes</th></tr></thead><tbody id="case-rows">' + "".join(_case_row(case) for case in cases) + '</tbody></table></div><div class="table-footer"><span id="table-count" role="status"></span><div class="pagination" id="pagination" aria-label="Paginação dos testes"></div><select id="page-size" aria-label="Testes por página"><option value="15">15 por página</option><option value="30">30 por página</option><option value="50">50 por página</option></select></div><p class="muted">Tempo da ação usa horários registrados pelo executor. A duração total inclui navegação, revisões auxiliares e relatório.</p>'
    results = (
        '<div class="result-navigation">'
        '<div class="result-location"><button id="case-back" class="button button-ghost">← Voltar para testes</button>'
        '<h1 id="result-view-title" tabindex="-1">Resultado do teste</h1></div>'
        '<div class="result-actions"><div class="result-case-navigation" role="group" aria-label="Navegar entre testes">'
        '<button id="case-edit" class="button button-ghost" data-edit-current>Editar passos</button>'
        '<button id="case-prev" class="button button-ghost">← Teste anterior</button>'
        '<button id="case-next" class="button button-ghost">Teste seguinte →</button></div>'
        '<div id="result-report-actions" class="result-report-actions"></div></div></div>'
        '<h2 id="case-title" class="result-title">Detalhes do caso</h2><section id="scenarios">'
        + ("".join(case_html) or _empty("Nenhum caso individual registrado nesta execução.")) + '</section>'
    )
    evidence_view = '<div class="report-tools"><input id="evidence-search" class="search" type="search" aria-label="Buscar evidências" placeholder="Buscar por CT, página ou arquivo"><div class="filters"><button class="active" data-evidence-filter="all">Todas</button><button data-evidence-filter="screenshot">Capturas</button><button data-evidence-filter="log">Console e rede</button><button data-evidence-filter="data">Dados</button></div></div><p class="muted">Capturas e registros reais desta execução. Nenhum vídeo foi registrado. Cada arquivo aparece uma vez na galeria, com links para todos os CTs que compartilham a captura.</p><div class="evidence-grid" id="evidence-grid">' + ("".join(evidence_cards) or _empty("Nenhuma evidência associada a um CT está disponível.")) + '</div>' + fragments["observations"]
    for key, title in (("started_at", "Início da execução"), ("ended_at", "Encerramento da execução")):
        timestamp = _timestamp(execution_time.get(key))
        if timestamp:
            execution_events.append((timestamp, f'<li class="execution-event" data-event-kind="system"><span class="event-time">{timestamp.astimezone().strftime("%H:%M:%S")}</span><span class="event-dot info"></span><div class="event-body"><strong>{title}</strong><p>{_h(fragments["status"] if key == "ended_at" else "Exploração iniciada com as configurações registradas.")}</p></div></li>'))
    execution_events.sort(key=lambda pair: pair[0])
    timeline_view = '<div class="timeline-controls report-tools"><input id="timeline-search" class="search" type="search" aria-label="Buscar na linha do tempo" placeholder="Buscar CT ou evento"><select id="timeline-kind" aria-label="Filtrar eventos"><option value="ALL">Todos os eventos</option><option value="PASS">Aprovados</option><option value="FAIL">Reprovados</option><option value="BLOCKED">Bloqueados</option><option value="SKIPPED">Sem validação</option><option value="system">Execução</option></select></div><p class="muted">Ordem cronológica de início dos CTs e marcos da execução. Abra um CT para ver todas as suas ações, capturas e sinais técnicos. Horários no fuso local do relatório.</p><ol class="execution-timeline" id="execution-events">' + ("".join(item[1] for item in execution_events) or _empty("Nenhum horário de execução foi preservado.")) + '</ol>'
    rule_rows = "".join(f'<tr><td>{_h(check_label(kind))}</td><td>{counts["total"]}</td><td>{counts["passed"]}</td><td>{counts["failed"]}</td><td>{counts["unknown"]}</td></tr>' for kind, counts in rules.items())
    coverage = '<div class="coverage-note reading-guide"><strong>Cobertura observada, não cobertura absoluta.</strong> As tabelas mostram somente páginas e condições realmente registradas. Não há uma lista completa de requisitos para calcular um percentual de cobertura de toda a aplicação.</div><div class="coverage-tabs filters"><button class="active" data-coverage-tab="pages">Por página</button><button data-coverage-tab="rules">Verificações por CT</button><button data-coverage-tab="restrictions">Restrições e retomadas</button></div><div data-coverage-panel="pages">' + _panel("Páginas verificadas", _pages_table(list(pages.values())), "CTs atribuídos à URL registrada no caso") + '</div><div data-coverage-panel="rules" hidden>' + _panel("Condições do resultado atual", '<div class="table-wrap"><table class="report-table"><thead><tr><th>Verificação</th><th>Executadas</th><th>Confirmadas</th><th>Não confirmadas</th><th>Sem resultado</th></tr></thead><tbody>' + rule_rows + '</tbody></table></div>' if rules else _empty("Nenhuma asserção estruturada registrada."), "Contagens do resultado atual de cada CT, não de todas as tentativas. Verificações de tentativas anteriores permanecem na linha do tempo do caso. Inclui condições de negócio, interação, técnicas e indícios visuais.") + '</div><div data-coverage-panel="restrictions" hidden>' + fragments["coverage_summary"] + '</div>'
    journey = _journey(exploration, cases, renderer)
    settings = _settings({**exploration, "app_url": renderer._public_issue_url(exploration.get("app_url") or "")}, fragments)
    contents = {"overview": overview, "tests": tests, "results": results, "journey": journey, "evidence": evidence_view, "timeline": timeline_view, "coverage": coverage, "settings": settings}
    nav = "".join(f'<button data-report-nav="{key}" class="{"active" if key == "overview" else ""}" {"aria-current=page" if key == "overview" else ""}>{_icon(icon)}<span>{label}</span>{f"<span class=nav-count>{shown_total}</span>" if key == "tests" else ""}</button>' for key, label, icon in NAVIGATION)
    views = "".join(f'<section class="report-view" data-view="{key}" aria-label="{label}" {"" if key == "overview" else "hidden"}>{contents[key]}</section>' for key, label, _ in NAVIGATION)
    public_app = renderer._public_issue_url(exploration.get("app_url") or "")
    try:
        app_name = urlsplit(public_app).hostname or "Aplicação em teste"
    except ValueError:
        app_name = "Aplicação em teste"
    payload = {"run_id": str(exploration["id"]), "cases": cases, "pages": [item["page"] for item in pages.values()], "started_at": exploration.get("started_at"), "duration_seconds": duration}
    data = '<script id="report-data" type="application/json">' + _json(payload) + '</script>'
    head = legacy_document.split('</head>', 1)[0]
    head = head.replace('<html lang="pt-BR"', f'<html data-report-layout="{REPORT_LAYOUT_VERSION}" lang="pt-BR"', 1)
    css = (ASSETS / "premium.css").read_text() + '\n' + (ASSETS / "journey.css").read_text()
    js = (ASSETS / "premium.js").read_text()
    data = _case_editor() + data + '<script id="report-drafts" type="application/json">{"version":1,"cases":{}}</script>'
    return head + f'<style>{css}</style></head><body class="report-premium" data-view="overview" data-theme="dark" data-density="comfortable" data-report-schema="ct-timeline-v1"><aside class="report-sidebar"><div class="report-brand"><span class="brand-mark">{_icon("robot")}</span><div><strong class="brand-name">AUTOJEV</strong><span class="brand-caption">Relatório exploratório</span></div></div><nav aria-label="Seções do relatório">{nav}</nav><div class="sidebar-footer"><span class="legend-dot"></span>Relatório local e independente<small>Dados preservados · sem chamadas de IA</small></div></aside><main class="report-main"><header class="report-topbar"><div><span class="page-kicker">{_h(app_name)} / RELATÓRIO</span><h1 id="view-title" tabindex="-1">Visão geral</h1><p id="view-subtitle">Uma visão clara dos testes, resultados e evidências da execução.</p></div><div class="toolbar"><button id="print-report" class="button button-ghost">Imprimir</button><button id="download-report" class="button button-primary">{_icon("download")}Baixar relatório</button></div></header>{views}<noscript><style>.report-view[hidden],#scenarios .scenario[hidden]{{display:block!important}}.report-sidebar,.report-tools,.result-navigation,.toolbar{{display:none!important}}</style><p>JavaScript está desativado. Todas as seções são exibidas para leitura; abra os casos para ver suas evidências.</p></noscript></main><dialog id="evidence-dialog" class="evidence-dialog"><div class="dialog-toolbar"><strong id="evidence-caption">Captura registrada</strong><a id="download-evidence" class="button button-ghost" download>Baixar captura</a><button id="close-evidence" class="button button-ghost" aria-label="Fechar captura">Fechar ✕</button></div><div class="dialog-content"><img id="evidence-preview" alt="Captura de evidência ampliada"></div></dialog>{data}<script>{js}</script></body></html>'


def _case_row(case: dict[str, Any]) -> str:
    return f'<tr data-case-index="{case["index"]}"><td><span class="case-id">{_h(case["id"])}</span></td><td><button class="case-title-button" data-open-case="{case["index"]}">{_h(case["title"])}</button></td><td><span class="badge {case["status"].lower()}">{STATUS_LABELS[case["status"]]}</span></td><td>{CATEGORIES[case["category"]]}</td><td class="page-url" title="{_h(case["url"])}">{_h(case["page"])}</td><td>{_time(case["duration_seconds"])}</td><td>{case["signals"]}</td><td><button class="button button-ghost" data-open-case="{case["index"]}" aria-label="Abrir {_h(case["id"])}">Ver →</button></td></tr>'


def _pages_table(pages: list[dict[str, Any]], compact: bool = False) -> str:
    rows = []
    for page in pages:
        cases = page["cases"]
        counts = Counter(item["status"] for item in cases)
        avg = sum(page["durations"]) / len(page["durations"]) if page["durations"] else None
        # Different item/query states may share the same path. Show their
        # redacted query/fragment rather than indistinguishable duplicate rows.
        try:
            parsed = urlsplit(page["url"])
            variant = (f"?{parsed.query}" if parsed.query else "") + (f"#{parsed.fragment}" if parsed.fragment else "")
        except ValueError:
            variant = ""
        hint = f'<small class="page-variant">{_h(variant)}</small>' if variant else ""
        rows.append(f'<tr><td class="page-url" title="{_h(page["url"])}">{_h(page["page"])}{hint}</td><td>{len(cases)}</td><td>{counts["PASS"]}</td><td>{counts["FAIL"]}</td>' + ('' if compact else f'<td>{counts["BLOCKED"] + counts["SKIPPED"]}</td>') + f'<td>{_time(avg)}</td></tr>')
    return '<div class="table-wrap"><table class="report-table"><thead><tr><th>Página</th><th>CTs</th><th>Aprovados</th><th>Reprovados</th>' + ('' if compact else '<th>Não validados</th>') + '<th>Tempo médio da ação</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>' if rows else _empty("Nenhuma página com CT registrado.")


def _duration_chart(cases: list[dict[str, Any]], duration: float | None) -> str:
    timed = [case for case in cases if case["offset"] is not None]
    if not timed or not duration:
        return _empty("Não há horários suficientes para construir o gráfico da execução.")
    bins = 36
    buckets = [Counter() for _ in range(bins)]
    for case in timed:
        buckets[min(bins - 1, int(case["offset"] / max(duration, 1) * bins))][case["status"]] += 1
    maximum = max(1, max(sum(item.values()) for item in buckets))
    bars = []
    for index, bucket in enumerate(buckets):
        bottom = 132
        for status, color in (("PASS", "#24d99b"), ("FAIL", "#ff617b"), ("BLOCKED", "#f5b746"), ("SKIPPED", "#7f91a8")):
            count = bucket[status]
            height = count / maximum * 100
            bottom -= height
            if height:
                bars.append(f'<rect x="{16+index*12}" y="{bottom:.2f}" width="8" height="{height:.2f}" rx="2" fill="{color}"><title>{STATUS_LABELS[status]}: {count} CTs</title></rect>')
    labels = ''.join(f'<text x="{16+i*104}" y="157" fill="currentColor" font-size="10">{int(duration*i/4)//60:02d}:{int(duration*i/4)%60:02d}</text>' for i in range(5))
    return f'<div class="time-chart"><strong>{int(duration)//60:02d}:{int(duration)%60:02d}</strong><span>Duração total</span><svg viewBox="0 0 460 166" role="img" aria-label="Distribuição temporal dos inícios dos casos">{"".join(bars)}{labels}</svg><small>Horário de execução disponível em {len(timed)} de {len(cases)} CTs.</small></div>'


def _journey(exploration: dict[str, Any], cases: list[dict[str, Any]], renderer: Any) -> str:
    routes = []
    for action in exploration.get("actions") or []:
        result = action.get("result") or {}
        for key in ("before_url", "after_url"):
            url = renderer._public_issue_url(result.get(key) or "")
            if url and (not routes or routes[-1] != url):
                routes.append(url)
    if not routes:
        return _empty("A sequência de navegação não foi preservada nesta execução. As páginas dos CTs continuam disponíveis em Regras e cobertura.")
    nodes, pickers = [], []
    discovered = list(dict.fromkeys(routes))
    for position, url in enumerate(discovered):
        associated = [case for case in cases if case["url"] == url]
        failures = sum(item["status"] == "FAIL" for item in associated)
        visits = routes.count(url)
        test_word = "teste" if len(associated) == 1 else "testes"
        stage = "Primeira página visitada" if position == 0 else "Última página descoberta" if position == len(discovered) - 1 else "Página visitada"
        try:
            parsed = urlsplit(url)
            variant = (f"?{parsed.query}" if parsed.query else "") + (f"#{parsed.fragment}" if parsed.fragment else "")
        except ValueError:
            variant = ""
        visit_word = "visita" if visits == 1 else "visitas"
        failure_word = "reprovado" if failures == 1 else "reprovados"
        content = (
            '<span class="journey-card-top">'
            f'<span class="journey-number">{position+1:02d}</span>'
            f'<span class="journey-page-info"><span class="journey-stage-label journey-sr-only">{stage}</span>'
            f'<span class="journey-page-title" role="heading" aria-level="3">{_h(_page(url))}</span>'
            + (f'<small class="page-variant">{_h(variant)}</small>' if variant else '')
            + '</span></span><span class="journey-card-bottom"><span class="journey-node-stats">'
            f'<span>{_icon("file")}<span><strong>{len(associated)}</strong> {test_word}</span></span>'
            f'<span>{_icon("eye")}<span><strong>{visits}</strong> {visit_word}</span></span></span>'
            f'<span class="journey-failures {"has-failures" if failures else "journey-sr-only"}">{failures} {failure_word}</span>'
            + (f'<span class="journey-card-arrow">{_icon("chevron")}</span>' if associated else '<span class="journey-no-tests">Sem CT atribuído a esta página.</span>')
            + '</span>'
        )
        picker_id = f"journey-cases-{position}"
        label = f'Página {position+1:02d}: {_page(url)}{variant}. {len(associated)} {test_word}, {visits} {visit_word}, {failures} {failure_word}. Abrir testes desta página.'
        control = (
            f'<button type="button" class="journey-page-card" data-journey-page="{position}" '
            f'aria-label="{_h(label)}" aria-controls="{picker_id}" aria-expanded="false">{content}</button>'
            if associated else f'<div class="journey-page-card journey-page-empty">{content}</div>'
        )
        nodes.append(
            f'<li class="journey-node" data-journey-position="{position+1}">{control}</li>'
        )
        if associated:
            rows = ''.join(
                '<article class="journey-case-row">'
                f'<span class="case-id">{_h(case["id"])}</span><strong>{_h(case["title"])}</strong>'
                f'<span class="badge {case["status"].lower()}">{STATUS_LABELS[case["status"]]}</span>'
                '<div class="journey-case-actions">'
                f'<button class="button button-ghost" data-open-case="{case["index"]}">Ver resultado</button>'
                f'<button class="button button-ghost" data-edit-case="{case["index"]}">Editar passos</button>'
                '</div></article>' for case in associated
            )
            pickers.append(
                f'<section id="{picker_id}" class="journey-case-picker panel" tabindex="-1" aria-label="Testes da página {_h(_page(url))}" hidden>'
                '<header class="journey-picker-heading"><div>'
                f'<h3>{len(associated)} {test_word} desta página</h3><p class="url">{_h(url)}</p></div>'
                '<button class="button button-ghost" data-journey-close aria-label="Fechar testes desta página">Fechar ✕</button></header>'
                '<div class="report-tools journey-picker-tools"><input type="search" class="search" data-journey-search '
                'aria-label="Buscar teste nesta página" placeholder="Buscar por número, título ou resultado">'
                f'<span class="muted" data-journey-count>{len(associated)} {test_word}</span></div>'
                f'<div class="journey-case-list">{rows}</div><p class="empty-state" data-journey-empty hidden>Nenhum teste encontrado nesta página.</p></section>'
            )
    sequence = ' <span class="journey-connector" aria-hidden="true">→</span> '.join(f'<span>{_h(_page(url))}</span>' for url in routes)
    page_list = (
        '<section class="journey-map-panel" aria-label="Mapa das páginas em ordem de descoberta">'
        '<div class="journey-map-meta">'
        f'<span class="event-chip">{len(discovered)} {"página descoberta" if len(discovered) == 1 else "páginas descobertas"}</span></div>'
        '<div class="journey-route"><canvas class="journey-route-canvas" aria-hidden="true" focusable="false"></canvas>'
        '<span class="journey-terminal journey-terminal-start" aria-hidden="true">INÍCIO</span>'
        '<span class="journey-terminal journey-terminal-end" aria-hidden="true">FIM</span>'
        '<ol class="journey-track" role="list" aria-label="Páginas em ordem da primeira visita">'
        + ''.join(nodes) + '</ol></div></section>'
    )
    return (
        '<div class="reading-guide journey-info-strip">' + _icon("info") + '<div><strong>Jornada observada nesta execução.</strong> '
        'Cada cartão representa uma página, na ordem em que foi descoberta. Não representa um teste end-to-end único '
        'nem comprova uma jornada de compra completa. As repetições e os retornos são preservados na sequência. '
        '“FIM” indica apenas o fim das páginas descobertas.</div></div>'
        + page_list + ''.join(pickers)
        + '<details class="journey-sequence-details panel"><summary>Ver a sequência real de navegação</summary>'
        f'<p class="muted">{len(routes)} posições de navegação, com retornos e mudanças de rota observadas</p>'
        f'<div class="journey-sequence">{sequence}</div></details>'
    )


def _case_editor() -> str:
    return (
        '<dialog id="case-editor" class="case-editor-dialog" aria-labelledby="case-editor-title">'
        '<header class="case-editor-header"><div><span class="page-kicker">Roteiro do teste</span>'
        '<h2 id="case-editor-title">Editar passos do teste</h2></div>'
        '<button id="case-editor-close" class="button button-ghost" aria-label="Fechar editor de passos">Fechar ✕</button></header>'
        '<div class="case-editor-notice">Rascunho visual · não executado · não altera este resultado. '
        'As etapas registradas ficam preservadas; os cards extras não executam consultas nem acessam bancos de dados.</div>'
        '<div class="case-editor-layout"><aside id="case-editor-palette" class="case-editor-palette">'
        '<span class="ct-summary-eyebrow">Adicionar ao roteiro</span>'
        f'<button id="database-step-card" class="database-step-card" draggable="true">{_icon("database")}'
        '<strong>Consulta em banco de dados</strong><small>Somente visual · sem conexão ou execução</small></button>'
        '<p class="muted">Arraste o card para um espaço entre as etapas ou use “Adicionar aqui”.</p></aside>'
        '<section class="case-editor-canvas" aria-label="Etapas do roteiro"><h3>Passos do teste</h3>'
        '<p class="muted">Etapas originais são somente leitura. Posicione seus novos cards entre elas.</p>'
        '<ol id="case-editor-steps"></ol></section></div>'
        '<footer class="case-editor-footer"><p id="case-editor-feedback" role="status" aria-live="polite"></p>'
        '<div><button id="case-editor-rerun" class="button button-ghost">Ver disponibilidade de reexecução</button>'
        '<button id="case-editor-save" class="button button-primary">Guardar rascunho neste relatório</button></div></footer></dialog>'
    )


def _settings(exploration: dict[str, Any], fragments: dict[str, Any]) -> str:
    options = exploration.get("options") or {}
    viewport = f'{options.get("viewport_width", "—")} × {options.get("viewport_height", "—")}'
    fields = [("Aplicação", exploration.get("app_url")), ("Escopo", fragments["scope"]), ("JEV", (exploration.get("agent") or {}).get("model_id")), ("LLM auxiliar", (exploration.get("auxiliary_llm") or {}).get("model_id")), ("Modo de evidência", fragments["evidence_mode"]), ("Tamanho da tela", viewport)]
    info = ''.join(f'<div class="setting-field"><label>{label}</label><div>{_h(value or "Não registrado")}</div></div>' for label, value in fields)
    return '<div class="reading-guide">As opções abaixo alteram somente a apresentação deste arquivo. Não modificam a execução, resultados, evidências ou configurações da aplicação.</div><form id="settings-form"><div class="settings-grid">' + _panel("Configuração da execução", info, "Dados registrados · somente leitura") + _panel("Exibição de evidências", '<label class="setting-row"><span>Mostrar capturas</span><input id="show-screenshots" type="checkbox" checked></label><label class="setting-row"><span>Mostrar detalhes técnicos</span><input id="show-technical" type="checkbox" checked></label><label class="setting-row"><span>Mostrar observações da execução</span><input id="show-observations" type="checkbox" checked></label><div class="setting-field"><label for="theme-select">Tema</label><select id="theme-select"><option value="dark">Escuro</option><option value="light">Claro</option></select></div><div class="setting-field"><label for="density-select">Densidade</label><select id="density-select"><option value="comfortable">Confortável</option><option value="compact">Compacta</option></select></div>') + '</div><button id="save-settings" type="submit" class="button button-primary">Aplicar preferências</button><p id="settings-feedback" class="settings-feedback" role="status"></p></form>'
