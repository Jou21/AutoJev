"""Defects the explorer can state from what it saw, beyond browser signals.

An HTTP 404 or a console error says little about the screen. These say what a
tester would file: a screen with no way back, a search the default filters
cannot run, result titles shown with nothing under them, searches that never
find data to test with. Each finding carries the steps to reproduce, the
expected and the observed behaviour and the screenshot of the moment it was
seen. They are observations of this run, to be confirmed by a person, not a
verdict on a test case.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlsplit

from autojev.domain.models import AgentDecision, Observation

SEVERITY_ORDER = {"alta": 0, "média": 1, "baixa": 2, "impedimento": 3}

_BACK = re.compile(
    r"\b(?:voltar|retornar|cancelar|fechar|sair|back|cancel|close|return)\b"
    r"|arrow_back|chevron_left|keyboard_backspace|^[×✕x]$"
)
_SEARCH = re.compile(r"\b(?:pesquisar|buscar|consultar|filtrar|procurar|search|find|filter)\b")


def _norm(value: Any) -> str:
    return " ".join(str(value or "").split()).lower()


def _path(url: Any) -> str:
    return urlsplit(str(url or "")).path.rstrip("/") or "/"


def _finding(kind: str, key: str, **fields: Any) -> dict[str, Any]:
    return {"id": hashlib.sha1(f"{kind}|{key}".encode()).hexdigest()[:16], "kind": kind, **fields}


def _element(observation: Observation, ref: str | None) -> dict[str, Any] | None:
    return next(
        (e for e in observation.interactive_elements if isinstance(e, dict) and e.get("ref") == ref),
        None,
    )


def dead_end(before: Observation, decision: AgentDecision, after: Observation) -> dict[str, Any] | None:
    """A click led to another screen, the control vanished and nothing leads back.

    Only the browser's Back button (or the global menu, hidden in a flyout)
    returns. A link to the previous route, a breadcrumb to it, or a
    Voltar/Cancelar/Fechar control counts as a way back; the global
    navigation menu that opened the screen is not the screen's own way back.
    """

    if decision.action != "click":
        return None
    source, target = _path(before.url), _path(after.url)
    if source == target:
        return None
    trigger = _element(before, decision.target)
    name = _norm((trigger or {}).get("name"))
    if not trigger or not name or trigger.get("in_navigation"):
        return None
    elements = [e for e in after.interactive_elements if isinstance(e, dict)]
    if any(_norm(e.get("name")) == name for e in elements):
        return None
    for element in elements:
        if element.get("href") and _path(element["href"]) == source:
            return None
        if not element.get("in_navigation") and _BACK.search(_norm(element.get("name"))):
            return None
    self_links = sorted({
        str(e.get("name")).strip() for e in elements
        if e.get("href") and _path(e["href"]) == target and not e.get("in_navigation") and str(e.get("name") or "").strip()
    })
    label = str(trigger.get("name")).strip()
    actual = (
        f"«{label}» desaparece e a nova tela ({target}) não oferece Voltar, Cancelar, Fechar nem link para "
        f"{source}; só o botão Voltar do navegador ou o menu retornam."
    )
    if self_links:
        actual += " O link " + ", ".join(f"«{link}»" for link in self_links) + " aponta para a própria tela nova."
    return _finding(
        "dead_end", f"{source}|{target}|{name}",
        category="Navegação", severity="média",
        title=f"Sem caminho de volta depois de «{label}»",
        url=after.url, screenshot=after.screenshot,
        steps=[f"Acessar {source}", f"Clicar em «{label}»"],
        expected=f"A nova tela oferece uma forma de voltar para {source} (Voltar/Cancelar ou breadcrumb).",
        actual=actual,
    )


def empty_result_titles(observation: Observation) -> dict[str, Any] | None:
    """Result-area titles shown with nothing under them on a search screen.

    Before a search returns data, the titles of the empty result blocks are
    noise ("Arquivo Recebido" over a blank area): the page should show nothing,
    or a hint such as "Faça uma pesquisa", until there is something to list.
    """

    results = observation.page_state.get("results") or {}
    titles = [str(t) for t in results.get("empty_sections") or [] if str(t or "").strip()]
    if not titles or int(results.get("rows") or 0) or observation.page_state.get("busy"):
        return None
    if not any(
        isinstance(e, dict) and _SEARCH.search(_norm(e.get("name")))
        for e in observation.interactive_elements
    ):
        return None
    page = _path(observation.url)
    shown = ", ".join(f"«{t}»" for t in titles[:6])
    return _finding(
        "empty_result_titles", f"{page}|{'|'.join(sorted(_norm(t) for t in titles))}",
        category="Interface", severity="baixa",
        title="Títulos de resultado exibidos sem conteúdo",
        url=observation.url, screenshot=observation.screenshot,
        steps=[f"Acessar {page}", "Observar a área de resultados antes de a pesquisa trazer registros"],
        expected="Nada (ou uma orientação como «Faça uma pesquisa») até a busca encontrar registros.",
        actual=f"A área de resultados mostra {shown} sem tabela, mensagem ou dado abaixo.",
    )


class FindingDetector:
    """Runs the on-screen detectors and reports each finding once per run."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def _new(self, *findings: dict[str, Any] | None) -> list[dict[str, Any]]:
        fresh = []
        for finding in findings:
            if finding and finding["id"] not in self._seen:
                self._seen.add(finding["id"])
                fresh.append(finding)
        return fresh

    def observe(self, observation: Observation) -> list[dict[str, Any]]:
        return self._new(empty_result_titles(observation))

    def after_action(
        self, before: Observation, decision: AgentDecision, after: Observation,
    ) -> list[dict[str, Any]]:
        return self._new(dead_end(before, decision, after), empty_result_titles(after))


def _search_name(section: Any, matrix: Any) -> str:
    """«Arquivo Recebido» when the block has a title, else "pesquisa nº 1"."""

    if section:
        return f"«{section}»"
    position = str(matrix or "").rsplit("#", 1)[-1]
    return f"pesquisa nº {int(position) + 1}" if position.isdigit() else "pesquisa da tela"


def search_findings(events: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Findings stated by the period/data searches: blocked defaults, no test data."""

    started: dict[str, dict[str, Any]] = {}
    completed: dict[str, dict[str, Any]] = {}
    variations: dict[str, list[dict[str, Any]]] = {}
    findings: list[dict[str, Any]] = []
    for event in events or []:
        if not isinstance(event, dict) or not isinstance(event.get("payload"), dict):
            continue
        kind, payload = event.get("type"), event["payload"]
        matrix = str(payload.get("matrix") or "")
        if kind == "search_matrix_started":
            started[matrix] = payload
        elif kind == "search_matrix_completed":
            completed[matrix] = payload
        elif kind == "search_variation_result" and not payload.get("skipped"):
            variations.setdefault(matrix, []).append(payload)
        elif kind == "search_matrix_unlocked":
            section = payload.get("section") or (started.get(matrix) or {}).get("section")
            page = matrix.rsplit("#", 1)[0]
            field = payload.get("field") or "campo"
            default = payload.get("default")
            button = payload.get("button") or "Pesquisar"
            name = _search_name(section, matrix)
            where = f"em {name}" if section else f"na {name}"
            actual = (
                f"«{button}» {'de ' + name if section else 'da ' + name} continua desabilitado com as datas preenchidas"
                + (f" e «{field}» = «{default}»" if default else "")
                + (f"; só habilitou com «{field}» = «{payload.get('option')}»" if payload.get("option") else "")
                + ("." if payload.get("messages") else ", sem mensagem explicando o que falta.")
            )
            if payload.get("messages"):
                actual += " Mensagens na tela: " + "; ".join(str(m) for m in payload["messages"]) + "."
            findings.append(_finding(
                "search_blocked_by_default", f"{page}|{section or matrix}|{field}",
                category="Formulário", severity="média",
                title=(f"Pesquisa de {name}" if section else f"{name[:1].upper()}{name[1:]}")
                + f" bloqueada com o valor padrão de «{field}»",
                url=page, screenshot=payload.get("screenshot"),
                steps=[
                    f"Acessar {page}",
                    f"{where[:1].upper()}{where[1:]}, preencher as datas de início e de término",
                    f"Manter «{field}» = «{default}»" if default else f"Manter «{field}» como vem",
                ],
                expected=(
                    f"«{button}» habilita com o valor que a tela já oferece"
                    + (" (ou o valor padrão não é oferecido, já que o campo é obrigatório)" if payload.get("required") else "")
                    + ", ou a tela informa o que falta."
                ),
                actual=actual,
            ))
    empty = [
        matrix for matrix, done in completed.items()
        if not done.get("found_records") and not any(v.get("rows") for v in variations.get(matrix, []))
        and int(done.get("searches") or 0) > 0
    ]
    if empty and not any(done.get("found_records") for done in completed.values()):
        searches = sum(int(completed[m].get("searches") or 0) + len(variations.get(m, [])) for m in empty)
        by_page: dict[str, list[str]] = {}
        for m in empty:
            section = completed[m].get("section") or (started.get(m) or {}).get("section")
            by_page.setdefault(m.rsplit("#", 1)[0], []).append(_search_name(section, m))
        where = "; ".join(f"{page}: {', '.join(names)}" for page, names in by_page.items())
        findings.append(_finding(
            "no_test_data", "|".join(sorted(empty)),
            category="Massa de dados", severity="impedimento",
            title="Falta de massa de dados para a pesquisa",
            url=empty[0].rsplit("#", 1)[0], screenshot=None,
            steps=["Pesquisar cada mês do ano atual e do anterior", "Variar os demais filtros, um de cada vez"],
            expected="Ao menos um período ou filtro retorna registros, para testar a tabela, a paginação e as ações por linha.",
            actual=(
                f"Nenhuma das {searches} pesquisas retornou registros ({where}). A tabela de resultados, a paginação "
                "e as ações por linha não puderam ser testadas: é preciso carregar massa de teste para este usuário/cooperativa."
            ),
        ))
    return findings
