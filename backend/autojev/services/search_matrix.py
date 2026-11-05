"""Exercise a period search with many date ranges, not a single one.

A screen with a start/end date pair and a "Pesquisar" button is not covered by
one search: an empty month proves little. The matrix runs the search for each
of the 24 months of this year and the one before. Whichever range returned the
most records is searched again at the end so the table is on screen for the
rest of the exploration (row actions, pagination, details).

Everything here is deterministic: no model decides which dates to type. The
planner calls :meth:`SearchMatrix.step` once per loop turn and executes the
returned decision like any other action, so each search is also a test case.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from autojev.domain.models import AgentDecision, Observation

MONTHS_PER_YEAR = 12
MAX_YEARS_BACK = 1          # this year and the one before: 24 months
MAX_FIELD_RETRIES = 2
MAX_SKIPPED_RANGES = 2
MAX_BUSY_WAITS = 30          # a slow search: up to ~12 s of spinner, polled every 0.4 s
MIN_EMPTY_WAITS = 0          # no fixed pause: only a loading page is waited for
MAX_ENABLE_WAITS = 3         # the form validates after a tick
MAX_REQUIRED_FIELDS = 6      # required fields the matrix will fill to enable the search
MAX_UNLOCK_TRIES = 12        # other select options tried when the search stays disabled

_NORMALISE_ACCENTS = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüç", "aaaaaeeeeiiiiooooouuuuc")
_DATE_EXAMPLE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
_DATE_HINT = re.compile(r"\b(?:dd|d)/(?:mm|m)/(?:aaaa|yyyy)\b")
_START = re.compile(r"\b(?:inicio|inicial|desde|de|from|start)\b")
_END = re.compile(r"\b(?:termino|fim|final|ate|to|end)\b")
_SEARCH = re.compile(
    r"\b(?:pesquisar|buscar|consultar|filtrar|procurar|aplicar|search|find|filter)\b"
)


_NO_RECORDS_NOTICE = re.compile(
    r"(?:nao foram encontrad|nenhum registro|sem registros?|nao ha registros?|nenhum resultado|no (?:results|records))"
)


def _validation_messages(observation: Observation) -> tuple[list[str], bool]:
    """(real validation messages, whether a "no records" notice was shown).

    Alert regions carry both: the toast "Não foram encontrados registros" is
    the empty answer to the search, not something the person got wrong.
    """

    errors, notice = [], False
    for message in observation.page_state.get("validation_errors") or []:
        if not message:
            continue
        if _NO_RECORDS_NOTICE.search(_norm(message)):
            notice = True
        else:
            errors.append(str(message)[:120])
    return errors, notice


def _wait(why: str, milliseconds: int = 400) -> tuple[AgentDecision, str]:
    return AgentDecision(action="wait", value=str(milliseconds), rationale=why, fast=True), "search_matrix_wait"


def _results_pending(observation: Observation, waits: int) -> bool:
    """The page is still loading, or says "no records" too soon to trust."""

    busy = bool(observation.page_state.get("busy"))
    rows = int((observation.page_state.get("results") or {}).get("rows") or 0)
    return (busy and waits < MAX_BUSY_WAITS) or (rows == 0 and waits < MIN_EMPTY_WAITS)


def _split_identifier(value: Any) -> str:
    """``valorTransacao`` / ``cnpj_cooperado`` -> "valor transacao" / "cnpj cooperado"."""

    text = re.sub(r"([a-z0-9])([A-Z])", lambda m: f"{m.group(1)} {m.group(2)}", str(value or ""))
    return _norm(re.sub(r"[_\-.]+", " ", text))


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower().translate(_NORMALISE_ACCENTS)).strip()


@dataclass(slots=True)
class DatePair:
    """A start/end date field pair and the search button that applies it."""

    index: int
    start: dict[str, Any]
    end: dict[str, Any]
    button: dict[str, Any] | None
    iso: bool

    def render(self, day: date) -> str:
        return day.isoformat() if self.iso else day.strftime("%d/%m/%Y")


def _is_date_field(element: dict[str, Any]) -> bool:
    if _norm(element.get("tag")) != "input" or element.get("read_only") or element.get("enabled") is False:
        return False
    kind = _norm(element.get("type"))
    if kind == "date":
        return True
    if kind not in {"", "text"}:
        return False
    shown = f"{element.get('placeholder') or ''} {element.get('name') or ''}"
    if _DATE_EXAMPLE.search(shown) or _DATE_HINT.search(_norm(shown)):
        return True
    return bool(re.search(r"\bdata\b", _norm(element.get("label"))))


def _looks_like(pattern: re.Pattern[str], element: dict[str, Any]) -> bool:
    return bool(pattern.search(_norm(element.get("label")) or _norm(element.get("name"))))


def detect_date_pairs(observation: Observation) -> list[DatePair]:
    """Consecutive date fields, in reading order, paired as start and end."""

    fields = [e for e in observation.interactive_elements if isinstance(e, dict) and _is_date_field(e)]
    fields.sort(key=lambda e: (round(float(e.get("document_top") or 0) / 12), int(str(e.get("ref") or "e0")[1:] or 0)))
    buttons = [
        e for e in observation.interactive_elements
        if isinstance(e, dict) and _norm(e.get("tag")) in {"button", "input"}
        and _SEARCH.search(_norm(e.get("name")))
    ]
    pairs: list[DatePair] = []
    cursor = 0
    while cursor + 1 < len(fields):
        first, second = fields[cursor], fields[cursor + 1]
        # A lone "Data" field followed by an unrelated one is not a range.
        if _looks_like(_END, first) and not _looks_like(_END, second):
            cursor += 1
            continue
        floor = float(second.get("document_top") or 0) - 4
        below = [b for b in buttons if float(b.get("document_top") or 0) >= floor]
        button = min(below, key=lambda b: float(b.get("document_top") or 0)) if below else (buttons[0] if buttons else None)
        pairs.append(DatePair(
            index=len(pairs), start=first, end=second, button=button,
            iso=_norm(first.get("type")) == "date",
        ))
        cursor += 2
    return pairs


def month_ranges(today: date, max_years_back: int = MAX_YEARS_BACK) -> list[dict[str, Any]]:
    """Every month of this year, then every month of earlier years (newest first)."""

    ranges: list[dict[str, Any]] = []
    for back in range(max_years_back + 1):
        year = today.year - back
        for month in range(1, MONTHS_PER_YEAR + 1):
            ranges.append({
                "label": f"{month:02d}/{year}",
                "start": date(year, month, 1),
                "end": date(year, month, calendar.monthrange(year, month)[1]),
                "earlier_year": back > 0,
            })
    return ranges


def _same_date(actual: str | None, day: date) -> bool:
    """The field holds this day, however its mask writes it (digits only)."""

    digits = re.sub(r"\D", "", actual or "")
    return digits in {day.strftime("%d%m%Y"), day.strftime("%Y%m%d")}


def _field_values(observation: Observation) -> dict[str, str]:
    return {
        str(item.get("ref")): str(item.get("value"))
        for item in observation.page_state.get("fields", [])
        if isinstance(item, dict) and item.get("ref") and item.get("value") is not None
    }


@dataclass
class SearchMatrix:
    """Progress through the ranges of one date-pair search form."""

    key: str
    today: date
    ranges: list[dict[str, Any]] = field(default_factory=list)
    position: int = 0
    stage: str = "fill_first"            # fill_first -> fill_second -> search -> await_results
    done: bool = False
    stopped_reason: str | None = None
    records: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    _previous_start: date | None = None
    _retries: int = 0
    _waits: int = 0
    _enable_waits: int = 0
    _required_tries: int = 0
    _unlock_tried: set = field(default_factory=set)
    _unlock_announced: bool = False
    _unlock_last: tuple[str, str] | None = None   # (field, option) tried last: the one that enabled the search
    _unlock_context: dict[str, Any] = field(default_factory=dict)
    section: str | None = None
    _skips: int = 0
    _replaying: bool = False
    _announced: bool = False

    def __post_init__(self) -> None:
        if not self.ranges:
            self.ranges = month_ranges(self.today)

    # ------------------------------------------------------------------ helpers
    @property
    def current(self) -> dict[str, Any]:
        return self.ranges[self.position]

    def _emit(self, kind: str, **payload: Any) -> None:
        self.events.append({"type": kind, "matrix": self.key, **payload})

    def _finish(self, reason: str) -> None:
        self.done = True
        self.stopped_reason = reason
        found = [r for r in self.records if r["rows"] > 0]
        self._emit(
            "search_matrix_completed",
            reason=reason,
            section=self.section,
            searches=len([r for r in self.records if not r.get("replay")]),
            with_records=[r["label"] for r in found if not r.get("replay")],
            best=max(found, key=lambda r: r["rows"])["label"] if found else None,
            found_records=bool(found),
        )

    def _abort(self, reason: str) -> None:
        self._finish(reason)

    # ------------------------------------------------------------------ the machine
    def step(self, observation: Observation, pair: DatePair) -> tuple[AgentDecision, str] | None:
        if self.done:
            return None
        if not self._announced:
            self._announced = True
            self.section = section_of(pair)
            self._emit(
                "search_matrix_started",
                first_range=self.ranges[0]["label"],
                planned_ranges=len(self.ranges),
                section=self.section,
            )
        if self.stage == "await_results":
            if _results_pending(observation, self._waits):
                self._waits += 1
                return _wait(f"Aguardando o resultado da busca do período {self.current['label']}.")
            self._waits = 0
            self._collect(observation)
            if self.done:
                return None
        if pair.button is None:
            self._abort("no_search_button")
            return None
        return self._next_input(observation, pair)

    def _collect(self, observation: Observation) -> None:
        results = observation.page_state.get("results") or {}
        rows = int(results.get("rows") or 0)
        _, notice = _validation_messages(observation)
        current = self.current
        record = {
            "label": current["label"], "rows": rows,
            "empty_state": bool(results.get("empty_state")) or notice,
            "start": current["start"].isoformat(), "end": current["end"].isoformat(),
            "replay": self._replaying,
        }
        self.records.append(record)
        self._emit("search_matrix_range", **record, earlier_year=current["earlier_year"],
                   ranges_done=len([r for r in self.records if not r["replay"]]))
        self._retries = 0
        if self._replaying:
            self._finish("best_range_shown")
            return
        # Every planned month is searched: an empty month proves little and a
        # month with records says nothing about its neighbours.
        if self.position + 1 >= len(self.ranges):
            self._conclude()
            return
        self._previous_start = current["start"]
        self.position += 1
        self.stage = "fill_first"

    def _conclude(self) -> None:
        """Leave the best range on screen so the table can be explored."""

        best = max((r for r in self.records if r["rows"] > 0), key=lambda r: r["rows"], default=None)
        last = self.records[-1]
        if best is None or best["label"] == last["label"]:
            self._finish("records_on_screen" if best else "no_records_found")
            return
        index = next(i for i, r in enumerate(self.ranges) if r["label"] == best["label"])
        self._previous_start = self.current["start"]
        self.position = index
        self._replaying = True
        self.stage = "fill_first"

    def _next_input(self, observation: Observation, pair: DatePair) -> tuple[AgentDecision, str] | None:
        target = self.current
        start_value, end_value = pair.render(target["start"]), pair.render(target["end"])
        moving_forward = self._previous_start is None or target["start"] >= self._previous_start
        # Keep start <= end after every keystroke: later range -> end first.
        order = (("end", pair.end, end_value), ("start", pair.start, start_value)) if moving_forward \
            else (("start", pair.start, start_value), ("end", pair.end, end_value))
        values = _field_values(observation)

        if self.stage in {"fill_first", "fill_second"}:
            index = 0 if self.stage == "fill_first" else 1
            name, element, wanted = order[index]
            self.stage = "fill_second" if index == 0 else "search"
            if _same_date(values.get(str(element.get("ref"))), target["start" if name == "start" else "end"]):
                # Already typed (e.g. a replay of the same range): do not retype.
                return self._next_input(observation, pair)
            return (
                AgentDecision(
                    action="fill", target=str(element["ref"]), value=wanted,
                    # The last date typed also leaves the field (Tab) so the form validates the range.
                    key="Tab" if index == 1 else None, fast=True,
                    rationale=(
                        f"Busca por período {target['label']}: "
                        f"{'data de término' if name == 'end' else 'data de início'} {wanted}."
                    ),
                    scenario_hint={
                        "title": f"Pesquisar período {target['label']}",
                        "description": f"Informa {wanted} no campo {'término' if name == 'end' else 'início'} da busca por período.",
                        "preconditions": [f"A tela {urlsplit(observation.url).path} está aberta"],
                        "expected_result": "O campo aceita a data informada.",
                    },
                ),
                "search_matrix_fill",
            )

        # stage == "search": confirm both dates really were accepted first.
        accepted = (
            _same_date(values.get(str(pair.start.get("ref"))), target["start"])
            and _same_date(values.get(str(pair.end.get("ref"))), target["end"])
        )
        if not accepted and self._retries < MAX_FIELD_RETRIES:
            self._retries += 1
            self.stage = "fill_first"
            return self._next_input(observation, pair)
        if not accepted:
            self._emit("search_matrix_range", label=target["label"], rows=0, empty_state=False,
                       skipped="date_not_accepted", replay=self._replaying)
            return self._skip_range(observation, pair)
        if pair.button.get("enabled") is False:
            if self._enable_waits < MAX_ENABLE_WAITS:
                self._enable_waits += 1
                return _wait("Aguardando o botão de busca habilitar.", 400)
            required = missing_required(observation, pair)
            if required and self._required_tries < MAX_REQUIRED_FIELDS:
                # The form wants more than the dates: satisfy its required
                # fields once (they keep their value for the other months).
                self._required_tries += 1
                self._enable_waits = 0
                return required_field_step(required[0])
            unlock = next_unlock_option(observation, pair, self._unlock_tried)
            if unlock is not None and len(self._unlock_tried) < MAX_UNLOCK_TRIES:
                # Some screens only enable the search for a specific choice
                # ("Todos" is selectable but not accepted): try the others.
                element, option, key = unlock
                if not self._unlock_tried:
                    # What the page offered before any change: the defect to report.
                    current = str((element.get("selected_values") or [""])[0] or "")
                    self._unlock_context = {
                        "default": next(
                            (str(o.get("label") or o.get("value") or "") for o in element.get("options") or []
                             if isinstance(o, dict) and str(o.get("value") or "") == current),
                            current,
                        ),
                        "required": element.get("required") is True,
                        "button": str(pair.button.get("name") or "") or None,
                        "messages": blocked_by(observation, pair),
                    }
                self._unlock_tried.add(key)
                self._unlock_last = (key[0], str(option.get("label") or option["value"]))
                self._enable_waits = MAX_ENABLE_WAITS - 1          # one look, not three, per option
                return (
                    AgentDecision(
                        action="select", target=str(element["ref"]), value=str(option["value"]), fast=True,
                        rationale=(
                            f"A busca continua desabilitada: tenta «{_field_title(element)}» = "
                            f"{option.get('label') or option['value']}."
                        ),
                    ),
                    "search_matrix_unlock",
                )
            self._emit("search_matrix_range", label=target["label"], rows=0, empty_state=False,
                       skipped="search_disabled", replay=self._replaying,
                       blocked_by=blocked_by(observation, pair))
            return self._skip_range(observation, pair)
        if self._unlock_tried and not self._unlock_announced:
            self._unlock_announced = True
            field_title, option_label = self._unlock_last
            self._emit(
                "search_matrix_unlocked", field=field_title, tried=len(self._unlock_tried),
                option=option_label, section=self.section, **self._unlock_context,
            )
        self.stage = "await_results"
        self._retries = 0
        self._waits = 0
        self._enable_waits = 0
        return (
            AgentDecision(
                action="click", target=str(pair.button["ref"]),
                rationale=f"Executa a busca do período {target['label']} ({start_value} a {end_value}).",
                scenario_hint={
                    "title": f"Pesquisar período {target['label']}",
                    "description": f"Pesquisa registros entre {start_value} e {end_value}.",
                    "preconditions": [f"As datas {start_value} e {end_value} estão preenchidas"],
                    "expected_result": "A busca responde com uma tabela de registros ou com o aviso de que não há registros no período.",
                },
            ),
            "search_matrix_search",
        )

    def _skip_range(self, observation: Observation, pair: DatePair) -> tuple[AgentDecision, str] | None:
        self._retries = 0
        self._enable_waits = 0
        self._skips += 1
        # Dates the form keeps rejecting say something about the form, not the
        # range: stop instead of walking through every month.
        if self._replaying or self._skips > MAX_SKIPPED_RANGES or self.position + 1 >= len(self.ranges):
            self._finish("range_not_accepted")
            return None
        self.records.append({"label": self.current["label"], "rows": 0, "empty_state": False,
                             "start": "", "end": "", "replay": False, "skipped": True})
        self._previous_start = self.current["start"]
        self.position += 1
        self.stage = "fill_first"
        return self._next_input(observation, pair)


# ======================================================================================
# Other test data: vary each field of the search, one at a time
# ======================================================================================

MAX_VARIATION_FIELDS = 12
SAME_ROW_TOLERANCE = 40
MAX_VARIANTS_PER_FIELD = 6
MAX_SELECT_OPTIONS = 10

_CPF_CNPJ = re.compile(r"\b(?:cpf|cnpj)\b")
_MONEY = re.compile(r"\b(?:valor|r\$|preco|montante|saldo)\b")
_QUANTITY = re.compile(r"\b(?:quantidade|qtd|qtde|numero|nr|codigo|cod)\b")
_PHONE = re.compile(r"\b(?:telefone|celular|fone)\b")
_EMAIL = re.compile(r"\be-?mail\b")


def readonly_date_fields(observation: Observation) -> list[str]:
    """Date fields a person can only fill with a calendar (not typeable)."""

    found = []
    for element in observation.interactive_elements:
        if not isinstance(element, dict) or _norm(element.get("tag")) != "input" or not element.get("read_only"):
            continue
        shown = f"{element.get('placeholder') or ''} {element.get('name') or ''}"
        if _norm(element.get("type")) == "date" or _DATE_EXAMPLE.search(shown) or re.search(
            r"\bdata\b", _norm(element.get("label"))
        ):
            found.append(str(element.get("label") or element.get("name") or "campo de data")[:60])
    return found


def _is_variable_field(element: dict[str, Any]) -> bool:
    if not isinstance(element, dict) or element.get("enabled") is False or element.get("read_only"):
        return False
    tag, kind = _norm(element.get("tag")), _norm(element.get("type"))
    if tag == "select":
        return bool(element.get("options"))
    if tag == "textarea":
        return True
    return tag == "input" and kind in {"", "text", "search", "number", "email", "tel", "url", "date"}


SPECIAL_CHARACTERS = "'\";<>&%$#@!"


def variants_for(element: dict[str, Any]) -> list[tuple[str, str]]:
    """Test data for one field: valid, boundary and invalid values, by what it asks for."""

    tag, kind = _norm(element.get("tag")), _norm(element.get("type"))
    subject = " ".join(filter(None, (
        _norm(element.get("label")), _norm(element.get("name")), _split_identifier(element.get("field_id")),
    )))
    if tag == "select":
        out: list[tuple[str, str]] = []
        for option in element.get("options") or []:
            if not isinstance(option, dict) or option.get("disabled") or not str(option.get("value") or ""):
                continue
            out.append((f"opção {str(option.get('label') or option['value'])[:40]}", str(option["value"])))
        return out[:MAX_SELECT_OPTIONS]
    shown = f"{element.get('placeholder') or ''} {element.get('name') or ''}"
    if kind == "date":
        values = [("data válida", "2026-01-15"), ("data antiga", "2000-01-01"), ("data futura", "2099-12-31")]
    elif _DATE_EXAMPLE.search(shown) or _DATE_HINT.search(_norm(shown)):
        values = [("data válida", "15/01/2026"), ("data inexistente", "31/02/2026"), ("só zeros", "00/00/0000")]
    elif _CPF_CNPJ.search(subject):
        values = [("CPF válido", "529.982.247-25"), ("CNPJ válido", "11.222.333/0001-81"),
                  ("documento inválido", "111.111.111-11"), ("curto demais", "123"), ("letras", "abcdef")]
    elif _MONEY.search(subject):
        values = [("valor comum", "100,00"), ("zero", "0,00"), ("negativo", "-1,00"),
                  ("valor enorme", "999999999,99"), ("letras", "abc")]
    elif kind == "number" or _QUANTITY.search(subject):
        values = [("um", "1"), ("zero", "0"), ("negativo", "-1"), ("número enorme", "999999999"), ("com decimais", "1.5")]
    elif kind == "email" or _EMAIL.search(subject):
        values = [("e-mail válido", "autojev.qa@example.com"), ("sem arroba", "sem-arroba"),
                  ("e-mail longo", "a" * 60 + "@example.com")]
    elif kind == "tel" or _PHONE.search(subject):
        values = [("telefone válido", "11999999999"), ("letras", "abc"), ("curto demais", "123")]
    else:
        values = [("texto comum", "AutoJev QA"), ("texto longo", "A" * 120),
                  ("caracteres especiais", SPECIAL_CHARACTERS), ("espaços nas pontas", "  teste  "),
                  ("só números", "1234567890")]
    return values[:MAX_VARIANTS_PER_FIELD]


def variable_fields(observation: Observation, pair: DatePair) -> list[dict[str, Any]]:
    """Fields of the same search section: below the previous button, above this one."""

    if pair.button is None:
        return []
    button_top = float(pair.button.get("document_top") or 0)
    earlier = [
        float(e.get("document_top") or 0) for e in observation.interactive_elements
        if isinstance(e, dict) and _norm(e.get("tag")) in {"button", "input"}
        and _SEARCH.search(_norm(e.get("name"))) and float(e.get("document_top") or 0) < button_top - 1
    ]
    floor = max(earlier) if earlier else float("-inf")
    skip = {str(pair.start.get("ref")), str(pair.end.get("ref"))}
    fields = [
        e for e in observation.interactive_elements
        if _is_variable_field(e) and str(e.get("ref")) not in skip
        # Same row as the button counts too: filters are often laid out in one line.
        and floor < float(e.get("document_top") or 0) <= button_top + SAME_ROW_TOLERANCE
    ]
    fields.sort(key=lambda e: (round(float(e.get("document_top") or 0) / 12), int(str(e.get("ref") or "e0")[1:] or 0)))
    return fields[:MAX_VARIATION_FIELDS]


def missing_required(observation: Observation, pair: DatePair) -> list[dict[str, Any]]:
    """Required fields of this search section that are still empty."""

    empty = []
    for element in variable_fields(observation, pair):
        if element.get("required") is not True:
            continue
        if _norm(element.get("tag")) == "select":
            current = (element.get("selected_values") or [""])[0]
            if not str(current or "") and any(
                isinstance(o, dict) and str(o.get("value") or "") and not o.get("disabled")
                for o in element.get("options") or []
            ):
                empty.append(element)
        elif element.get("has_value") is False:
            empty.append(element)
    return empty


def next_unlock_option(
    observation: Observation, pair: DatePair, tried: set,
) -> tuple[dict[str, Any], dict[str, Any], tuple[str, str]] | None:
    """Another option of a select in this section, to find the choice that enables the search."""

    for element in variable_fields(observation, pair):
        if _norm(element.get("tag")) != "select":
            continue
        current = str((element.get("selected_values") or [""])[0] or "")
        for option in element.get("options") or []:
            if not isinstance(option, dict) or option.get("disabled"):
                continue
            value = str(option.get("value") or "")
            key = (_field_title(element), value)
            if not value or value == current or key in tried:
                continue
            return element, option, key
    return None


def required_field_step(element: dict[str, Any]) -> tuple[AgentDecision, str]:
    """Give a required field a value the search can use."""

    title = _field_title(element)
    if _norm(element.get("tag")) == "select":
        option = next(
            o for o in element.get("options") or []
            if isinstance(o, dict) and str(o.get("value") or "") and not o.get("disabled")
        )
        return (
            AgentDecision(action="select", target=str(element["ref"]), value=str(option["value"]), fast=True,
                          rationale=f"«{title}» é obrigatório: escolhe «{option.get('label') or option['value']}» para habilitar a busca."),
            "search_matrix_required",
        )
    variants = variants_for(element)
    value = variants[0][1] if variants else "AutoJev QA"
    return (
        AgentDecision(action="fill", target=str(element["ref"]), value=value, key="Tab", fast=True,
                      rationale=f"«{title}» é obrigatório: informa um valor para habilitar a busca."),
        "search_matrix_required",
    )


def blocked_by(observation: Observation, pair: DatePair) -> list[str]:
    """Why the search stayed disabled, in the page's own words where possible."""

    reasons = [f"campo obrigatório vazio: {_field_title(e)}" for e in missing_required(observation, pair)]
    errors, _ = _validation_messages(observation)
    return [*reasons, *errors][:4]


def _reset_value(element: dict[str, Any]) -> str:
    if _norm(element.get("tag")) != "select":
        return ""
    options = [o for o in element.get("options") or [] if isinstance(o, dict) and not o.get("disabled")]
    blank = next((o for o in options if not str(o.get("value") or "")), None)
    return str((blank or (options[0] if options else {})).get("value") or "")


def section_of(pair: DatePair) -> str | None:
    """The title of the block the search belongs to ("Arquivo Recebido"), when the page has one."""

    for element in (pair.button, pair.start):
        if isinstance(element, dict) and str(element.get("section") or "").strip():
            return " ".join(str(element["section"]).split())[:80]
    return None


def _field_title(element: dict[str, Any]) -> str:
    return str(
        element.get("label") or element.get("name") or element.get("placeholder")
        or element.get("field_id") or "campo"
    )[:60]


@dataclass
class FieldVariations:
    """Search again with other test data, changing one field at a time."""

    key: str
    restore_best_range: bool = False
    plan: list[dict[str, Any]] = field(default_factory=list)
    field_index: int = 0
    variant_index: int = 0
    stage: str = "plan"            # plan -> set -> (blur) -> search -> await -> (reset) ... await_restore
    waits: int = 0
    busy_waits: int = 0
    enable_waits: int = 0
    messages_before: list = field(default_factory=list)
    done: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)

    def _emit(self, event_type: str, **payload: Any) -> None:
        self.events.append({"type": event_type, "matrix": self.key, **payload})

    def _finish(self, reason: str) -> None:
        self.done = True
        self._emit(
            "search_variation_completed", reason=reason,
            fields=len(self.plan), searches=len(self.results),
            with_records=sum(1 for r in self.results if r["rows"]),
            with_validation=sum(1 for r in self.results if r["validation_errors"]),
        )

    # ---------------------------------------------------------------- helpers
    def _locate(self, observation: Observation, pair: DatePair, entry: dict[str, Any]) -> dict[str, Any] | None:
        same = [
            e for e in variable_fields(observation, pair)
            if _field_title(e) == entry["title"] and _norm(e.get("tag")) == entry["tag"]
        ]
        return same[min(entry["ordinal"], len(same) - 1)] if same else None

    @staticmethod
    def _hint(entry: dict[str, Any], kind: str, path: str) -> dict[str, Any]:
        return {
            "title": f"Pesquisar com {entry['title']}: {kind}",
            "description": f"Informa {kind} em «{entry['title']}» e executa a busca.",
            "preconditions": [f"A tela {path} está aberta"],
            "expected_result": "A busca responde com registros, com aviso de que não há registros ou com uma mensagem de validação.",
        }

    def _record(self, entry, kind, value, *, rows, errors, empty, skipped=None, blocked_by=None,
                carried_over=False) -> None:
        record = {"field": entry["title"], "kind": kind, "value": str(value)[:60], "rows": rows,
                  "validation_errors": len(errors), "first_error": errors[0] if errors else None,
                  "empty_state": empty}
        if skipped:
            record["skipped"] = skipped
        if blocked_by:
            record["blocked_by"] = blocked_by
        if carried_over:
            # The same message was already on screen before this search: it may
            # be the previous search's alert still showing, not this value's.
            record["carried_over"] = True
        self.results.append(record)
        self._emit("search_variation_result", **record)

    def _collect(self, observation: Observation) -> None:
        entry = self.plan[self.field_index]
        kind, value = entry["variants"][self.variant_index]
        results = observation.page_state.get("results") or {}
        errors, notice = _validation_messages(observation)
        self._record(entry, kind, value, rows=int(results.get("rows") or 0), errors=errors,
                     empty=bool(results.get("empty_state")) or notice,
                     carried_over=bool(errors) and all(e in self.messages_before for e in errors))
        self._advance_variant()

    def _advance_variant(self) -> None:
        self.variant_index += 1
        entry = self.plan[self.field_index]
        self.stage = "reset" if self.variant_index >= len(entry["variants"]) else "set"

    def _next_field(self) -> None:
        self.field_index += 1
        self.variant_index = 0
        self.stage = "set"

    def _make_plan(self, observation: Observation, pair: DatePair) -> None:
        seen: dict[tuple[str, str], int] = {}
        for element in variable_fields(observation, pair):
            variants = variants_for(element)
            if not variants:
                continue
            key = (_field_title(element), _norm(element.get("tag")))
            seen[key] = seen.get(key, -1) + 1
            self.plan.append({"title": key[0], "tag": key[1], "ordinal": seen[key],
                              "variants": variants, "reset": _reset_value(element)})
        self._emit(
            "search_variation_started", fields=[p["title"] for p in self.plan],
            planned_searches=sum(len(p["variants"]) for p in self.plan),
        )
        self.stage = "set"

    # ---------------------------------------------------------------- the machine
    def step(self, observation: Observation, pair: DatePair) -> tuple[AgentDecision, str] | None:
        if self.done:
            return None
        if pair.button is None:
            self._finish("no_search_button")
            return None
        if observation.page_state.get("busy") and self.stage in {"plan", "set", "search", "reset"} \
                and self.busy_waits < MAX_BUSY_WAITS:
            # A loading page hides and blocks fields: look again once it settles.
            self.busy_waits += 1
            return _wait("Aguardando a página terminar de carregar.")
        self.busy_waits = 0
        if self.stage == "plan":
            self._make_plan(observation, pair)
        if self.stage == "await_restore":
            # Leave only once the table is back on screen.
            if _results_pending(observation, self.waits):
                self.waits += 1
                return _wait("Aguardando a tabela do melhor período voltar à tela.")
            self._finish("best_range_restored")
            return None
        if self.stage == "await":
            if _results_pending(observation, self.waits):
                self.waits += 1
                return _wait("Aguardando o resultado da busca com os dados informados.")
            self.waits = 0
            self._collect(observation)
        path = urlsplit(observation.url).path

        while self.field_index < len(self.plan):
            entry = self.plan[self.field_index]
            element = self._locate(observation, pair, entry)
            if element is None:
                if self.stage != "reset":
                    self._emit("search_variation_result", field=entry["title"], kind="campo ausente",
                               rows=0, validation_errors=0, empty_state=False, skipped="field_not_found")
                self._next_field()
                continue
            kind, value = entry["variants"][min(self.variant_index, len(entry["variants"]) - 1)]
            action = "select" if entry["tag"] == "select" else "fill"
            if self.stage == "set":
                self.stage = "search"
                return (
                    AgentDecision(
                        action=action, target=str(element["ref"]), value=value,
                        key="Tab" if action == "fill" else None, fast=True,
                        rationale=f"Variação de dados em «{entry['title']}»: {kind}.",
                        scenario_hint=self._hint(entry, kind, path),
                    ),
                    "search_variation_set",
                )
            if self.stage == "search":
                if pair.button.get("enabled") is False:
                    if self.enable_waits < MAX_ENABLE_WAITS:
                        self.enable_waits += 1
                        return _wait("Aguardando o botão de busca habilitar.", 400)
                    self.enable_waits = 0
                    self._record(entry, kind, value, rows=0, errors=[], empty=False, skipped="search_disabled",
                                 blocked_by=blocked_by(observation, pair))
                    self._advance_variant()
                    continue
                self.enable_waits = 0
                self.waits = 0
                self.messages_before = _validation_messages(observation)[0]
                self.stage = "await"
                return (
                    AgentDecision(
                        action="click", target=str(pair.button["ref"]),
                        rationale=f"Executa a busca com «{entry['title']}» = {kind}.",
                        scenario_hint=self._hint(entry, kind, path),
                    ),
                    "search_variation_search",
                )
            if self.stage == "reset":
                self._next_field()
                return (
                    AgentDecision(
                        action=action, target=str(element["ref"]), value=entry["reset"], fast=True,
                        rationale=f"Limpa «{entry['title']}» antes de variar o próximo campo.",
                    ),
                    "search_variation_reset",
                )
            break

        if self.field_index >= len(self.plan):
            if self.restore_best_range and self.stage != "await_restore":
                self.stage = "await_restore"
                self.waits = 0
                return (
                    AgentDecision(
                        action="click", target=str(pair.button["ref"]),
                        rationale="Restaura a busca do melhor período para a tabela de registros ficar na tela.",
                        scenario_hint={
                            "title": "Pesquisar o melhor período novamente",
                            "description": "Repete a busca sem variações para voltar à tabela de registros.",
                            "preconditions": [f"A tela {path} está aberta"],
                            "expected_result": "A tabela de registros é exibida.",
                        },
                    ),
                    "search_variation_search",
                )
            self._finish("all_fields_varied")
        return None
