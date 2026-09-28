from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from autojev.domain.models import ActionResult, AgentDecision, Observation

_DESTRUCTIVE_WORDS = {
    "apagar",
    "comprar",
    "delete",
    "erase",
    "excluir",
    "logout",
    "pay",
    "purchase",
    "remover",
    "remove",
    "sair",
    "sign out",
}
_CLICKABLE_TAGS = {"a", "button"}
_CLICKABLE_ROLES = {"button", "link", "menuitem", "tab", "checkbox", "radio"}
_FILLABLE_INPUT_TYPES = {
    "email",
    "number",
    "search",
    "tel",
    "text",
    "url",
}
_PASSIVE_ACTIONS = {"reload", "screenshot", "wait"}
_REPLAYABLE_ACTIONS = {"click", "hover", "press"}
_SAFE_REPLAY_KEYS = {
    "arrowdown",
    "arrowleft",
    "arrowright",
    "arrowup",
    "enter",
    "escape",
    "space",
    "tab",
}
_MAX_REPLAY_DEPTH = 12
_MAX_REPLAY_FAILURES = 2
_MAX_RESTORE_STEPS = 24
_MAX_NAVIGATE_RESTORE_FAILURES = 1
_MAX_SELECT_OPTIONS = 8
_MAX_EXPOSED_MOVES = 30
_EXIT_WORDS = {
    "back",
    "cancel",
    "close",
    "dismiss",
    "fechar",
    "return",
    "voltar",
}
_MENU_WORDS = {"drawer", "menu", "navigation", "navegacao", "sidebar"}
_OPEN_WORDS = {"abrir", "open", "show"}
_CLOSE_WORDS = {"close", "fechar", "hide"}
_FOCUS_STOPWORDS = {
    "a",
    "area",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "e",
    "menu",
    "o",
    "screen",
    "submenu",
    "tela",
    "the",
}


def canonical_url(value: str) -> str:
    """Return a stable route identity without throwing query or hash away."""

    parsed = urlsplit(value)
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    path = parsed.path or "/"
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            query,
            parsed.fragment,
        )
    )


def _normalise_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", value).strip().lower()


def _meaningful_tokens(value: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]+", _normalise_text(value))
        if len(token) > 1 and token not in _FOCUS_STOPWORDS
    ]


def _normalise_state(value: Any) -> bool | str | None:
    """Normalise DOM/ARIA state without introducing layout coordinates."""

    if value is None or isinstance(value, bool):
        return value
    normalised = _normalise_text(str(value))
    if normalised == "true":
        return True
    if normalised == "false":
        return False
    return normalised


def semantic_element_fingerprint(element: dict[str, Any]) -> str:
    """Identify an element independently of volatile Playwright refs (e0/e1)."""

    href = str(element.get("href") or "")
    if href:
        with suppress(ValueError):
            href = canonical_url(href)
    stable = {
        "tag": _normalise_text(str(element.get("tag") or "")),
        "role": _normalise_text(str(element.get("role") or "")),
        "type": _normalise_text(str(element.get("type") or "")),
        "name": _normalise_text(str(element.get("name") or ""))[:300],
        "href": href,
        "context": _normalise_text(str(element.get("context") or ""))[:160],
        "in_navigation": bool(element.get("in_navigation")),
        "aria_haspopup": _normalise_text(str(element.get("aria_haspopup") or "")),
    }
    return hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


@dataclass(slots=True)
class _CoverageState:
    key: str
    url: str
    title: str
    candidates: dict[str, dict[str, Any]] = field(default_factory=dict)
    inherited: set[str] = field(default_factory=set)
    attempted: set[str] = field(default_factory=set)
    blocked: set[str] = field(default_factory=set)
    failures: dict[str, int] = field(default_factory=dict)
    passive_actions: set[str] = field(default_factory=set)
    visits: int = 0


@dataclass(frozen=True, slots=True)
class _TransitionRecipe:
    """A selector-free recipe for recreating an observed DOM state."""

    parent_key: str
    child_key: str
    fingerprint: str
    action: str
    value: str | None = None
    key: str | None = None


@dataclass(frozen=True, slots=True)
class _IssuedRestoreStep:
    kind: str
    target_key: str
    source_key: str
    expected_key: str
    fingerprint: str | None = None


class CoveragePlanner:
    """Small deterministic coverage layer around the LLM decision maker.

    The LLM still chooses the kind of interaction.  This class owns durable
    memory, element novelty, the route stack and completion/backtracking rules.
    """

    def __init__(self, start_url: str, focus_area: str | None = None) -> None:
        self.start_url = canonical_url(start_url)
        self.origin = urlsplit(self.start_url)[:2]
        self.focus_area = " ".join((focus_area or "").split()) or None
        self.focus_reached = not bool(self.focus_area)
        self.focus_root: str | None = None
        self.focus_entry_anchor: str | None = None
        self.focus_entry_child: str | None = None
        self.states: dict[str, _CoverageState] = {}
        self.path: list[str] = []
        self.current_key: str | None = None
        self.active_states: set[str] = set()
        self.global_destinations_attempted: set[str] = set()
        self.global_navigation_attempted: set[str] = set()
        # A semantic control only adds coverage value once per route.  Without
        # this memory, reversible interfaces (cart add/remove, filters, menus)
        # create a power set of equivalent DOM snapshots and the planner later
        # tries to restore every obsolete combination.
        self.global_route_moves_attempted: set[tuple[str, str]] = set()
        self.backtrack_failures: dict[str, int] = {}
        self.coverage_root_key: str | None = None
        self.transitions: dict[str, list[_TransitionRecipe]] = {}
        self.restoration_target: str | None = None
        self.restoration_failures: dict[tuple[str, str, str], int] = {}
        self.restoration_steps = 0
        self._issued_restore_step: _IssuedRestoreStep | None = None
        self.unreachable_restorations: list[dict[str, Any]] = []

    @staticmethod
    def _clickable(element: dict[str, Any]) -> bool:
        tag = _normalise_text(str(element.get("tag") or ""))
        role = _normalise_text(str(element.get("role") or ""))
        element_type = _normalise_text(str(element.get("type") or ""))
        has_popup = _normalise_text(str(element.get("aria_haspopup") or ""))
        return (
            tag in _CLICKABLE_TAGS
            or role in _CLICKABLE_ROLES
            or has_popup not in {"", "false", "none"}
            or (
                tag == "input"
                and element_type in {"button", "checkbox", "radio", "submit"}
            )
        )

    @classmethod
    def _candidate_actions(
        cls, element: dict[str, Any]
    ) -> list[tuple[str, str | None]]:
        """Return the bounded set of safe atomic moves for one control.

        Coverage is tracked per move, rather than per DOM element.  This lets
        JEV compare hover/click menu behavior and each safe select option while
        keeping the executor constrained to prevalidated interactions.
        """

        if cls._clickable(element):
            role = _normalise_text(str(element.get("role") or ""))
            has_popup = _normalise_text(str(element.get("aria_haspopup") or ""))
            moves: list[tuple[str, str | None]] = []
            if role == "menuitem" or has_popup not in {"", "false", "none"}:
                moves.append(("hover", None))
            moves.append(("click", None))
            return moves
        if element.get("read_only"):
            return []
        tag = _normalise_text(str(element.get("tag") or ""))
        element_type = _normalise_text(str(element.get("type") or "text")) or "text"
        if tag == "textarea":
            return [("fill", "AutoJev QA exploratory input")]
        if tag == "input" and element_type in _FILLABLE_INPUT_TYPES:
            values = {
                "email": "autojev.qa@example.com",
                "number": "1",
                "search": "AutoJev QA",
                "tel": "11999999999",
                "text": "AutoJev QA exploratory input",
                "url": "https://example.com",
            }
            return [("fill", values[element_type])]
        if tag == "select":
            options = element.get("options")
            if not isinstance(options, list):
                return []
            moves = []
            seen_values: set[str] = set()
            for option in options:
                if not isinstance(option, dict) or option.get("disabled") is True:
                    continue
                value = str(option.get("value") or "")
                if not value or value in seen_values:
                    continue
                surface = _normalise_text(
                    f"{option.get('label') or ''} {option.get('value') or ''}"
                )
                if any(word in surface for word in _DESTRUCTIVE_WORDS):
                    continue
                seen_values.add(value)
                moves.append(("select", value))
                if len(moves) >= _MAX_SELECT_OPTIONS:
                    break
            return moves
        return []

    @classmethod
    def _candidate_action(
        cls, element: dict[str, Any]
    ) -> tuple[str, str | None] | None:
        """Return the first move for compatibility with deterministic fallback."""

        actions = cls._candidate_actions(element)
        return actions[0] if actions else None

    @staticmethod
    def _move_key(
        element_fingerprint: str,
        action: str,
        value: str | None,
        key: str | None = None,
    ) -> str:
        raw = {
            "element": element_fingerprint,
            "action": action,
            "value": value,
            "key": key,
        }
        return hashlib.sha256(
            json.dumps(raw, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()

    @staticmethod
    def _move_action(element: dict[str, Any]) -> str:
        return str(element.get("_move_action") or "click")

    @staticmethod
    def _move_value(element: dict[str, Any]) -> str | None:
        value = element.get("_move_value")
        return str(value) if value is not None else None

    @staticmethod
    def _move_keypress(element: dict[str, Any]) -> str | None:
        key = element.get("_move_key")
        return str(key) if key is not None else None

    @classmethod
    def _safe_candidate(
        cls,
        element: dict[str, Any],
        origin: tuple[str, str],
        *,
        require_interactable: bool = True,
    ) -> bool:
        if (
            not element.get("ref")
            or element.get("enabled") is False
            or (
                require_interactable
                and element.get("interactable") is False
            )
        ):
            return False
        tag = _normalise_text(str(element.get("tag") or ""))
        element_type = _normalise_text(str(element.get("type") or ""))
        name = _normalise_text(str(element.get("name") or ""))
        if not name and not element.get("href"):
            return False
        if any(word in name for word in _DESTRUCTIVE_WORDS):
            return False
        # The frontier includes reversible clicks plus form controls that can
        # be exercised with deterministic, non-secret synthetic data. Password,
        # file, hidden and other high-risk input types never enter it.
        if not cls._candidate_actions(element):
            return False
        if tag == "input" and element_type in {"hidden", "password"}:
            return False
        href = str(element.get("href") or "")
        if href:
            parsed = urlsplit(href)
            if parsed.scheme not in {"http", "https"} or parsed[:2] != origin:
                return False
        return True

    @staticmethod
    def _is_redundant_self_link(
        observation: Observation, element: dict[str, Any]
    ) -> bool:
        """Reject an exact self-link unless it exposes a separate UI state."""

        if _normalise_text(str(element.get("tag") or "")) != "a":
            return False
        href = str(element.get("href") or "")
        if not href:
            return False
        if _normalise_text(str(element.get("aria_haspopup") or "")) not in {
            "",
            "false",
            "none",
        }:
            return False
        if any(
            element.get(name) is not None
            for name in ("aria_expanded", "aria_pressed", "aria_selected")
        ):
            return False
        with suppress(ValueError):
            return canonical_url(href) == canonical_url(observation.url)
        return False

    @staticmethod
    def _menu_toggle_kind(element: dict[str, Any]) -> str | None:
        surface = _normalise_text(
            f"{element.get('name') or ''} {element.get('aria_label') or ''}"
        )
        tokens = set(re.findall(r"[a-z0-9]+", surface))
        if not tokens.intersection(_MENU_WORDS):
            return None
        if tokens.intersection(_CLOSE_WORDS):
            return "close"
        if tokens.intersection(_OPEN_WORDS):
            return "open"
        return None

    @classmethod
    def _menu_is_open(cls, observation: Observation) -> bool:
        """Infer an open drawer only from explicit semantic visibility state."""

        for element in observation.interactive_elements:
            if not isinstance(element, dict):
                continue
            toggle_kind = cls._menu_toggle_kind(element)
            popup = _normalise_text(str(element.get("aria_haspopup") or ""))
            role = _normalise_text(str(element.get("role") or ""))
            if _normalise_state(element.get("aria_expanded")) is True and (
                toggle_kind is not None
                or popup in {"menu", "true"}
                or role == "menuitem"
            ):
                return True
            if toggle_kind != "close":
                continue
            if element.get("interactable") is True:
                return True
            # Compatibility for observations captured before hit-testing was
            # available. Horizontal visibility changes for an off-canvas
            # drawer, but stays stable while the document scrolls vertically.
            if (
                element.get("in_horizontal_viewport") is True
                and _normalise_state(element.get("aria_hidden")) is not True
            ):
                return True
        return False

    def _candidate_map(
        self, observation: Observation, *, require_interactable: bool = True
    ) -> dict[str, dict[str, Any]]:
        candidates: dict[str, dict[str, Any]] = {}
        menu_is_open = self._menu_is_open(observation)
        for element in observation.interactive_elements:
            if not isinstance(element, dict) or not self._safe_candidate(
                element,
                self.origin,
                require_interactable=require_interactable,
            ):
                continue
            if self._is_redundant_self_link(observation, element):
                continue
            if menu_is_open and self._menu_toggle_kind(element) == "open":
                continue
            fingerprint = semantic_element_fingerprint(element)
            for action, value in self._candidate_actions(element):
                move_key = self._move_key(fingerprint, action, value)
                move = dict(element)
                move["_element_fingerprint"] = fingerprint
                move["_move_action"] = action
                move["_move_value"] = value
                move["_move_key"] = None
                if action == "select":
                    option = next(
                        (
                            item
                            for item in element.get("options") or []
                            if isinstance(item, dict)
                            and str(item.get("value") or "") == value
                        ),
                        None,
                    )
                    if option is not None:
                        move["_option_label"] = str(
                            option.get("label") or option.get("value") or ""
                        )[:200]
                        move["_option_selected"] = option.get("selected") is True
                # Equivalent duplicate controls/moves do not add coverage
                # value, but distinct actions or select values always do.
                candidates.setdefault(move_key, move)
        return candidates

    @staticmethod
    def _is_exit_control(element: dict[str, Any]) -> bool:
        surface = _normalise_text(
            f"{element.get('name') or ''} {element.get('aria_label') or ''}"
        )
        tokens = set(re.findall(r"[a-z0-9]+", surface))
        return bool(tokens & _EXIT_WORDS)

    def state_key(self, observation: Observation) -> str:
        # State identity must survive ordinary vertical scrolling. Hit-testing
        # still gates the executable frontier, but a control temporarily under
        # a sticky header (or now above the viewport) cannot manufacture a new
        # application state merely because its clickability changed.
        candidate_keys = sorted(
            self._candidate_map(observation, require_interactable=False)
        )
        raw = {
            "url": canonical_url(observation.url),
            "title": _normalise_text(observation.title),
            "elements": candidate_keys,
        }
        digest = hashlib.sha256(
            json.dumps(raw, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()[:20]
        return f"{raw['url']}::{digest}"

    def effect_key(self, observation: Observation) -> str:
        """Fingerprint visible state, including control values but never secrets."""

        controls = []
        for element in observation.interactive_elements:
            if not isinstance(element, dict):
                continue
            controls.append(
                {
                    "semantic": semantic_element_fingerprint(element),
                    "has_value": bool(element.get("has_value")),
                    "checked": element.get("checked"),
                    "selected_index": element.get("selected_index"),
                    "aria_expanded": _normalise_state(element.get("aria_expanded")),
                    "aria_hidden": _normalise_state(element.get("aria_hidden")),
                    "aria_pressed": _normalise_state(element.get("aria_pressed")),
                    "aria_selected": _normalise_state(element.get("aria_selected")),
                    # Horizontal surface visibility and ARIA state capture
                    # drawers/menus. Hit-testing, vertical viewport position
                    # and document coordinates are intentionally excluded so
                    # scrolling cannot manufacture a new application state.
                    "in_horizontal_viewport": element.get("in_horizontal_viewport"),
                }
            )
        raw = {
            "url": canonical_url(observation.url),
            "title": _normalise_text(observation.title),
            "text": _normalise_text(observation.text)[:12000],
            "controls": sorted(
                controls,
                key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
            ),
        }
        return hashlib.sha256(
            json.dumps(raw, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()

    def _matches_focus(self, observation: Observation) -> bool:
        if not self.focus_area:
            return True
        strong_surface = _normalise_text(
            f"{canonical_url(observation.url)} {observation.title}"
        )
        tokens = _meaningful_tokens(self.focus_area)
        leaf = re.split(r"\s*(?:>|/|→)\s*", self.focus_area)[-1]
        leaf_tokens = _meaningful_tokens(leaf)
        return bool(tokens) and (
            all(token in strong_surface for token in tokens)
            or bool(leaf_tokens)
            and all(token in strong_surface for token in leaf_tokens)
        )

    def _activate_focus(self, key: str) -> None:
        self.focus_reached = True
        self.focus_root = key
        self.coverage_root_key = key
        self.active_states = {key}
        # The navigation used to find the requested area is not part of the
        # requested test scope. Backtracking must stop at this root.
        self.path = [key]

    def _link_is_inside_focus(self, element: dict[str, Any]) -> bool:
        """Keep focused exploration inside the selected route subtree.

        Buttons and non-link controls belong to the current screen. Links are
        constrained to descendants of the route where the focus was reached,
        or to destinations whose accessible label explicitly matches the
        requested scope. This keeps global sidebars from pulling a focused run
        into unrelated modules while still allowing details/tabs below it.
        """

        if not self.focus_area or not self.focus_reached or not self.focus_root:
            return True
        href = str(element.get("href") or "")
        if not href:
            if element.get("in_navigation"):
                surface = _normalise_text(
                    f"{element.get('name') or ''} {element.get('context') or ''}"
                )
                return any(
                    token in surface for token in _meaningful_tokens(self.focus_area)
                )
            return True

        focus_tokens = _meaningful_tokens(self.focus_area)
        surface = _normalise_text(f"{element.get('name') or ''} {href}")
        if focus_tokens and any(token in surface for token in focus_tokens):
            return True

        return self._url_is_inside_focus(href)

    def _url_is_inside_focus(self, value: str) -> bool:
        if not self.focus_area or not self.focus_reached or not self.focus_root:
            return True
        root = urlsplit(self.states[self.focus_root].url)
        destination = urlsplit(canonical_url(value))
        if destination[:2] != root[:2]:
            return False

        root_path = (root.path or "/").rstrip("/") or "/"
        destination_path = (destination.path or "/").rstrip("/") or "/"
        path_inside = destination_path == root_path or (
            root_path != "/" and destination_path.startswith(f"{root_path}/")
        )

        root_fragment = root.fragment.strip("/")
        destination_fragment = destination.fragment.strip("/")
        if root_fragment and destination_path == root_path:
            fragment_inside = destination_fragment == root_fragment or bool(
                destination_fragment
                and destination_fragment.startswith(f"{root_fragment}/")
            )
            return path_inside and fragment_inside
        if root_path == "/" and destination_fragment:
            # At an SPA root without a focused fragment there is no reliable
            # ancestry signal; only explicit focus labels were accepted above.
            return False
        return path_inside

    def observe(self, observation: Observation) -> str:
        key = self.state_key(observation)
        candidates = self._candidate_map(observation)
        state = self.states.get(key)
        if state is None:
            state = _CoverageState(
                key=key,
                url=canonical_url(observation.url),
                title=observation.title,
                candidates=candidates,
            )
            self.states[key] = state
        else:
            # Refs and hit-test availability belong to the current DOM
            # observation. Keeping a previously clickable candidate after a
            # drawer/header starts intercepting it makes Playwright retry a
            # stale control and can create a false FAIL. Coverage memory lives
            # in attempted/inherited/blocked; candidate metadata is refreshed.
            state.candidates = candidates
        state.visits += 1
        self.current_key = key
        if not self.path:
            self.path.append(key)
        if self.coverage_root_key is None:
            self.coverage_root_key = key

        if (
            self.focus_area
            and not self.focus_reached
            and self._matches_focus(observation)
        ):
            self._activate_focus(key)
        return key

    def _is_active(self, key: str) -> bool:
        return (
            not self.focus_reached or not self.focus_area or key in self.active_states
        )

    def _remaining(self, state: _CoverageState) -> list[tuple[str, dict[str, Any]]]:
        if not self._is_active(state.key):
            return []
        result: list[tuple[str, dict[str, Any]]] = []
        coverage_root_key = self.focus_root if self.focus_reached else None
        if coverage_root_key is None and self.path:
            coverage_root_key = self.path[0]
        root_state = self.states.get(coverage_root_key or "")
        for fingerprint, element in state.candidates.items():
            if fingerprint in state.attempted or fingerprint in state.inherited:
                continue
            if (
                self._move_action(element) == "select"
                and element.get("_option_selected") is True
            ):
                # Re-selecting the active value succeeds at the browser layer
                # but produces no verifiable effect, yielding a misleading
                # SKIPPED scenario instead of new coverage.
                continue
            if (state.url, fingerprint) in self.global_route_moves_attempted:
                continue
            if (
                element.get("in_navigation")
                and fingerprint in self.global_navigation_attempted
            ):
                continue
            if (
                element.get("in_navigation")
                and root_state is not None
                and state.key != root_state.key
                and fingerprint in root_state.candidates
            ):
                # A global sidebar is a set of sibling branches. It is covered
                # at the scope root, not from every descendant screen. Newly
                # revealed submenu controls have new fingerprints and remain
                # eligible here.
                continue
            href = str(element.get("href") or "")
            if href and canonical_url(href) in self.global_destinations_attempted:
                continue
            if not self._link_is_inside_focus(element):
                continue
            result.append((fingerprint, element))

        focus = _normalise_text(self.focus_area or "")

        def priority(
            item: tuple[str, dict[str, Any]],
        ) -> tuple[int, int, int, float, str, int, int]:
            _, element = item
            surface = _normalise_text(
                f"{element.get('name') or ''} {element.get('href') or ''}"
            )
            focus_rank = (
                0
                if focus
                and any(token in surface for token in _meaningful_tokens(focus))
                else 1
            )
            navigation_rank = 1 if element.get("in_navigation") else 0
            exit_rank = 1 if self._is_exit_control(element) else 0
            try:
                top = float(element.get("document_top", 0))
            except (TypeError, ValueError):
                top = 0
            # Exiting the current surface is always the last local option.
            # In particular, a focused label such as "Voltar Usuários" must
            # not outrank a non-exit control merely because it matches scope.
            action_rank = 0 if self._move_action(element) == "hover" else 1
            selected_rank = 1 if element.get("_option_selected") else 0
            return (
                exit_rank,
                focus_rank,
                navigation_rank,
                top,
                surface,
                action_rank,
                selected_rank,
            )

        return sorted(result, key=priority)

    def _forced_action(
        self, state: _CoverageState, rationale: str
    ) -> AgentDecision | None:
        remaining = self._remaining(state)
        non_exit = [item for item in remaining if not self._is_exit_control(item[1])]
        # Keep this invariant independent of the presentation/sorting policy:
        # a forced action never closes or leaves a surface while a non-exit
        # candidate on that same surface is still available.
        candidates = non_exit or remaining
        for _, element in candidates:
            return AgentDecision(
                action=self._move_action(element),
                target=str(element["ref"]),
                value=self._move_value(element),
                key=self._move_keypress(element),
                rationale=rationale,
            )
        return None

    def _move_for_decision(
        self,
        candidates: list[tuple[str, dict[str, Any]]],
        decision: AgentDecision,
    ) -> tuple[str, dict[str, Any]] | None:
        """Resolve an exact issued move, including select value/menu variant."""

        for move_key, element in candidates:
            if str(element.get("ref")) != str(decision.target):
                continue
            if self._move_action(element) != decision.action:
                continue
            expected_value = self._move_value(element)
            actual_value = str(decision.value) if decision.value is not None else None
            if expected_value != actual_value and not (
                decision.action == "fill" and decision.credential_key
            ):
                continue
            expected_key = self._move_keypress(element)
            actual_key = str(decision.key) if decision.key is not None else None
            if expected_key != actual_key:
                continue
            return move_key, element
        return None

    def _frontier_states(self) -> list[_CoverageState]:
        return [
            state
            for key, state in self.states.items()
            if self._is_active(key) and self._remaining(state)
        ]

    def frontier_exhausted(self) -> bool:
        return not self._frontier_states()

    def can_finish(self) -> bool:
        # The requested focus may legitimately not exist; completion is still
        # valid once the reachable discovery frontier has been exhausted. A
        # browser does not need to physically unwind every already-covered DOM
        # state merely to end on the original screen.
        return self.frontier_exhausted() and self.restoration_target is None

    def coverage_complete(self) -> bool:
        """True only when the frontier settled without blocked candidates."""

        if not self.can_finish():
            return False
        return not any(
            state.blocked for key, state in self.states.items() if self._is_active(key)
        )

    @staticmethod
    def _recipe_id(recipe: _TransitionRecipe) -> str:
        return ":".join(
            (
                recipe.parent_key,
                recipe.child_key,
                recipe.fingerprint,
                recipe.action,
                recipe.key or "",
                recipe.value or "",
            )
        )

    def _navigate_failure_key(
        self, target_key: str, ancestor_key: str
    ) -> tuple[str, str, str]:
        """Group failed restores by URL, not by volatile DOM snapshot.

        Several state fingerprints can share one URL. Retrying ``goto`` for
        every fingerprint only reloads the same document and was the source of
        the visible restore loop in real runs.
        """

        ancestor = self.states.get(ancestor_key)
        destination = ancestor.url if ancestor is not None else ancestor_key
        return ("navigate", target_key, canonical_url(destination))

    def _register_transition(
        self,
        parent_key: str,
        child_key: str,
        fingerprint: str,
        decision: AgentDecision,
        *,
        is_focus_entry: bool = False,
    ) -> None:
        """Remember how a safe semantic interaction produced a new state.

        Refs/selectors are deliberately excluded. During replay the semantic
        fingerprint is resolved against the freshly observed DOM, so React
        rerenders and Playwright ref renumbering cannot redirect the action.
        """

        if decision.action not in _REPLAYABLE_ACTIONS or decision.credential_key:
            return
        if parent_key == child_key:
            return
        element = self.states[parent_key].candidates.get(fingerprint)
        if element is None or not self._safe_candidate(element, self.origin):
            return
        if not self._link_is_inside_focus(element):
            return
        if (
            self.focus_area
            and self.focus_reached
            and not is_focus_entry
            and (not self._is_active(parent_key) or not self._is_active(child_key))
        ):
            return

        key = decision.key
        if decision.action == "press":
            key = key or "Enter"
            if _normalise_text(key) not in _SAFE_REPLAY_KEYS:
                return
        value = decision.value
        if value is not None:
            value = str(value)[:120]
        recipe = _TransitionRecipe(
            parent_key=parent_key,
            child_key=child_key,
            fingerprint=fingerprint,
            action=decision.action,
            value=value,
            key=key,
        )
        outgoing = self.transitions.setdefault(parent_key, [])
        if recipe not in outgoing:
            outgoing.append(recipe)
        if is_focus_entry:
            # The anchor remains outside the exploration scope. It exists only
            # so a DOM-only focused root (modal/submenu on the same URL) can be
            # recreated without making unrelated base controls eligible.
            self.focus_entry_anchor = parent_key
            self.focus_entry_child = child_key

    def _is_focus_entry_recipe(self, recipe: _TransitionRecipe) -> bool:
        return (
            recipe.parent_key == self.focus_entry_anchor
            and recipe.child_key == self.focus_entry_child
            and recipe.child_key == self.focus_root
        )

    def _find_replay_path(
        self, start_key: str, target_key: str
    ) -> list[_TransitionRecipe] | None:
        if start_key == target_key:
            return []
        queue: list[tuple[str, list[_TransitionRecipe]]] = [(start_key, [])]
        visited = {start_key}
        while queue:
            state_key, path = queue.pop(0)
            if len(path) >= _MAX_REPLAY_DEPTH:
                continue
            for recipe in self.transitions.get(state_key, []):
                failure_key = (
                    "replay",
                    target_key,
                    self._recipe_id(recipe),
                )
                if (
                    self.restoration_failures.get(failure_key, 0)
                    >= _MAX_REPLAY_FAILURES
                ):
                    continue
                is_focus_entry = self._is_focus_entry_recipe(recipe)
                if not self._is_active(recipe.child_key) or (
                    not self._is_active(recipe.parent_key) and not is_focus_entry
                ):
                    continue
                next_path = [*path, recipe]
                if recipe.child_key == target_key:
                    return next_path
                if recipe.child_key not in visited:
                    visited.add(recipe.child_key)
                    queue.append((recipe.child_key, next_path))
        return None

    def _url_restorable(self, state_key: str) -> bool:
        if state_key == self.focus_entry_anchor:
            return True
        if state_key == self.focus_root and self.focus_entry_anchor is not None:
            return False
        if state_key == self.coverage_root_key or state_key == self.focus_root:
            return True
        state = self.states[state_key]
        return any(
            recipe.child_key == state_key
            and self.states[recipe.parent_key].url != state.url
            for recipes in self.transitions.values()
            for recipe in recipes
        )

    def _restorable_ancestor(self, target_key: str) -> str | None:
        root_key = self.focus_root or self.coverage_root_key
        if root_key is None:
            return None
        path = self._find_replay_path(root_key, target_key)
        if path is None:
            return None
        ancestors = [root_key, *(recipe.child_key for recipe in path[:-1])]
        for ancestor_key in reversed(ancestors):
            failure_key = self._navigate_failure_key(target_key, ancestor_key)
            if (
                self._url_restorable(ancestor_key)
                and self.restoration_failures.get(failure_key, 0)
                < _MAX_NAVIGATE_RESTORE_FAILURES
            ):
                return ancestor_key
        if self.focus_entry_anchor is not None:
            anchor_path = self._find_replay_path(self.focus_entry_anchor, target_key)
            failure_key = self._navigate_failure_key(
                target_key, self.focus_entry_anchor
            )
            if (
                anchor_path is not None
                and self.restoration_failures.get(failure_key, 0)
                < _MAX_NAVIGATE_RESTORE_FAILURES
            ):
                return self.focus_entry_anchor
        return None

    def _focus_boundary_restoration(
        self, current_key: str
    ) -> tuple[AgentDecision, str] | None:
        if self.focus_entry_anchor is None:
            return None
        for state in self._frontier_states():
            self.restoration_target = state.key
            self.restoration_steps = 0
            restored = self._continue_restoration(current_key)
            if restored:
                action, reason = restored
                return action, reason.replace("frontier_", "focus_boundary_")
        return None

    def _abandon_restoration(self, target_key: str, *, reason: str) -> dict[str, Any]:
        """Settle one unreachable frontier as blocked without ending the run."""

        state = self.states.get(target_key)
        remaining = self._remaining(state) if state is not None else []
        if state is not None:
            for fingerprint, _ in remaining:
                state.attempted.add(fingerprint)
                state.blocked.add(fingerprint)
        self.restoration_target = None
        self.restoration_steps = 0
        self._issued_restore_step = None
        settlement = {
            "reason": reason,
            "target_state": target_key,
            "target_url": state.url if state is not None else None,
            "blocked_candidates": len(remaining),
            "blocked_moves": [
                {
                    "name": str(element.get("name") or element.get("ref") or "")[:200],
                    "action": self._move_action(element),
                    "href": str(element.get("href") or "")[:500] or None,
                }
                for _, element in remaining[:30]
            ],
            "remaining_frontier_states": len(self._frontier_states()),
            "remaining_frontier_candidates": sum(
                len(self._remaining(candidate)) for candidate in self._frontier_states()
            ),
        }
        self.unreachable_restorations.append(settlement)
        return settlement

    def next_restoration_action(
        self, observation: Observation
    ) -> tuple[AgentDecision, str] | None:
        """Return deterministic graph traversal without spending an LLM call.

        Restoration decisions are deterministic consequences of the coverage
        graph.  Exposing them lets the orchestrator keep executing a known
        replay/navigation recipe without paying for a fresh model decision on
        every intermediate step.  Backtracking from an exhausted live state is
        deterministic for the same reason: the model chooses *what* to test,
        while the coverage graph owns how to reach the next known frontier.
        """

        key = self.observe(observation)
        restoration = self._continue_restoration(key)
        if restoration:
            return restoration

        state = self.states[key]
        if not self._remaining(state) and not self.frontier_exhausted():
            return self._backtrack_decision()
        return None

    def abandon_stalled_restoration(
        self, *, reason: str = "stalled_restoration"
    ) -> dict[str, Any] | None:
        """Block only the active unreachable target so other frontiers continue.

        The caller remains responsible for deciding when its anti-loop limit
        has been reached.  This method deliberately preserves that limit while
        turning a local restore failure into a documented coverage restriction
        instead of a fatal error for the complete exploration.
        """

        if self.restoration_target is None:
            return None
        return self._abandon_restoration(self.restoration_target, reason=reason)

    def _replay_decision(
        self, recipe: _TransitionRecipe, target_key: str
    ) -> AgentDecision | None:
        state = self.states.get(recipe.parent_key)
        element = state.candidates.get(recipe.fingerprint) if state else None
        if (
            element is None
            or not self._safe_candidate(element, self.origin)
            or not self._link_is_inside_focus(element)
        ):
            return None
        return AgentDecision(
            action=recipe.action,
            target=str(element["ref"]),
            value=recipe.value,
            key=recipe.key,
            rationale=(
                "Restore a known DOM-only frontier by replaying a safe semantic "
                "transition with a freshly resolved element reference."
            ),
        )

    def _continue_restoration(
        self, current_key: str
    ) -> tuple[AgentDecision, str] | None:
        target_key = self.restoration_target
        if target_key is None:
            return None
        target = self.states.get(target_key)
        if (
            target is None
            or not self._is_active(target_key)
            or not self._remaining(target)
        ):
            self.restoration_target = None
            self.restoration_steps = 0
            return None
        if current_key == target_key:
            self.restoration_target = None
            self.restoration_steps = 0
            return None
        if self.restoration_steps >= _MAX_RESTORE_STEPS:
            self._abandon_restoration(target_key, reason="restore_step_limit")
            return None

        # Prefer continuing from the state that is already open. This avoids a
        # reload when, for example, the parent dialog has just been recreated.
        for _ in range(_MAX_REPLAY_FAILURES + 1):
            path = self._find_replay_path(current_key, target_key)
            if not path:
                break
            recipe = path[0]
            decision = self._replay_decision(recipe, target_key)
            failure_key = ("replay", target_key, self._recipe_id(recipe))
            if decision is not None:
                self.restoration_steps += 1
                self._issued_restore_step = _IssuedRestoreStep(
                    kind="replay",
                    target_key=target_key,
                    source_key=current_key,
                    expected_key=recipe.child_key,
                    fingerprint=recipe.fingerprint,
                )
                return decision, "frontier_restore_replay"
            self.restoration_failures[failure_key] = _MAX_REPLAY_FAILURES

        ancestor_key = self._restorable_ancestor(target_key)
        if ancestor_key is None:
            # Preserve the old direct-URL recovery for route states that were
            # not entered by a replayable DOM interaction. A same-URL state is
            # never treated as URL-restorable without a semantic recipe.
            if target.url != self.states[current_key].url:
                ancestor_key = target_key
            else:
                self._abandon_restoration(target_key, reason="no_restore_route")
                return None
        failure_key = self._navigate_failure_key(target_key, ancestor_key)
        if (
            self.restoration_failures.get(failure_key, 0)
            >= _MAX_NAVIGATE_RESTORE_FAILURES
        ):
            self._abandon_restoration(target_key, reason="restore_attempt_limit")
            return None
        self.restoration_steps += 1
        self._issued_restore_step = _IssuedRestoreStep(
            kind="navigate",
            target_key=target_key,
            source_key=current_key,
            expected_key=ancestor_key,
        )
        return (
            AgentDecision(
                action="navigate",
                url=self.states[ancestor_key].url,
                rationale=(
                    "Restore the nearest known route ancestor before replaying "
                    "its selector-free semantic transition recipe."
                ),
            ),
            "frontier_restore_navigate",
        )

    def _backtrack_decision(
        self,
    ) -> tuple[AgentDecision, str | None] | None:
        if len(self.path) > 1:
            current = self.path[-1]
            current_state = self.states[current]
            previous = self.states[self.path[-2]]
            failures = self.backtrack_failures.get(current, 0)
            # Re-loading the same URL cannot recreate an older ephemeral DOM
            # snapshot.  One attempt is enough; repeating it only burns model
            # calls while landing on the identical live state.  Different-URL
            # parents still get browser-history plus one direct-URL fallback.
            failure_limit = 1 if current_state.url == previous.url else 2
            if failures >= failure_limit:
                # Browser history and a direct URL fallback both failed to
                # recreate this logical parent. Settle only that inaccessible
                # branch and keep walking the remaining graph.
                for fingerprint, _ in self._remaining(previous):
                    previous.attempted.add(fingerprint)
                    previous.blocked.add(fingerprint)
                self.path.pop(-2)
                return self._backtrack_decision()
            if current_state.url == previous.url:
                return (
                    AgentDecision(
                        action="navigate",
                        url=previous.url,
                        rationale=(
                            "The logical parent is another DOM state on this same route; "
                            "reload its known same-origin URL instead of crossing unrelated browser history."
                        ),
                    ),
                    "dom_state_backtrack_navigate",
                )
            if failures == 0:
                return (
                    AgentDecision(
                        action="go_back",
                        rationale="Coverage of this state is exhausted; return to the parent state and resume its untried alternatives.",
                    ),
                    None,
                )
            return (
                AgentDecision(
                    action="navigate",
                    url=previous.url,
                    rationale="Browser history did not restore the parent state; return to its known same-origin URL.",
                ),
                None,
            )

        # A remote frontier may be a modal/tab/submenu with the same URL. Its
        # URL alone cannot restore it, so replay the semantic transition graph.
        for state in self._frontier_states():
            if state.key != self.current_key:
                self.restoration_target = state.key
                self.restoration_steps = 0
                restored = self._continue_restoration(self.current_key or "")
                if restored:
                    return restored
        return None

    def reconcile(
        self, observation: Observation, decision: AgentDecision
    ) -> tuple[AgentDecision, str | None]:
        key = self.observe(observation)
        state = self.states[key]
        if self.focus_area and self.focus_reached and key not in self.active_states:
            focus_restore = self._focus_boundary_restoration(key)
            if focus_restore:
                return focus_restore
            backtrack = self._backtrack_decision()
            if backtrack:
                return backtrack[0], "focus_boundary_return"
            if self.focus_root:
                return (
                    AgentDecision(
                        action="navigate",
                        url=self.states[self.focus_root].url,
                        rationale="Return immediately to the requested focused exploration root.",
                    ),
                    "focus_boundary_return",
                )
        restoration = self._continue_restoration(key)
        if restoration:
            return restoration
        remaining = self._remaining(state)

        # Browser history is an execution mechanism, never a planning source.
        # Reconcile every model-requested back navigation through the logical
        # coverage graph regardless of path depth.  Previously only the root
        # did this, allowing an exhausted same-route child to bounce through
        # the authenticated login entry forever.
        if decision.action == "go_back" and not remaining:
            at_coverage_root = len(self.path) <= 1
            backtrack = self._backtrack_decision()
            if backtrack:
                return backtrack[0], backtrack[1] or (
                    "root_backtrack_replaced"
                    if at_coverage_root
                    else "model_backtrack_reconciled"
                )
            if self.can_finish():
                return (
                    AgentDecision(
                        action="finish",
                        rationale=(
                            "The reachable coverage frontier is exhausted; do not "
                            "cross the application or authenticated-session boundary."
                        ),
                    ),
                    (
                        "root_backtrack_replaced_by_finish"
                        if at_coverage_root
                        else "model_backtrack_replaced_by_finish"
                    ),
                )

        if decision.action == "finish":
            forced = self._forced_action(
                state,
                "The agent requested finish while this state still has a safe, untried interactive element.",
            )
            if forced:
                return forced, "finish_replaced_by_frontier_action"
            if self.frontier_exhausted():
                return decision, None
            backtrack = self._backtrack_decision()
            if backtrack:
                return backtrack[0], backtrack[1] or "finish_replaced_by_backtrack"
            return decision, None

        if decision.action in {"go_back", "navigate"} and remaining:
            forced = self._forced_action(
                state,
                "Finish the current state's safe, untried controls before leaving it.",
            )
            if forced:
                return forced, "premature_navigation_replaced"

        if decision.action in {"click", "fill", "select", "press", "hover"}:
            selected = self._move_for_decision(remaining, decision)
            if selected is None:
                same_ref = [
                    (move_key, element)
                    for move_key, element in remaining
                    if str(element.get("ref")) == str(decision.target)
                ]
                if same_ref:
                    _, replacement = same_ref[0]
                    return (
                        AgentDecision(
                            action=self._move_action(replacement),
                            target=str(replacement["ref"]),
                            value=self._move_value(replacement),
                            key=self._move_keypress(replacement),
                            rationale=(
                                "The proposed action/value was not an available atomic move; "
                                "use the next safe variant for this control."
                            ),
                        ),
                        "incompatible_form_action_replaced",
                    )
                forced = self._forced_action(
                    state,
                    "The proposed element was already attempted or unavailable; use the next safe semantic candidate.",
                )
                if forced:
                    return forced, "repeated_target_replaced"
                backtrack = self._backtrack_decision()
                if backtrack:
                    return (
                        backtrack[0],
                        backtrack[1] or "unavailable_target_replaced_by_backtrack",
                    )
                if self.can_finish():
                    return (
                        AgentDecision(
                            action="finish",
                            rationale="All safe candidates inside the selected scope are exhausted.",
                        ),
                        "unavailable_target_replaced_by_finish",
                    )
            else:
                selected_move_key, element = selected
                non_exit_remaining = [
                    candidate_element
                    for move_key, candidate_element in remaining
                    if move_key != selected_move_key
                    and not self._is_exit_control(candidate_element)
                ]
                if self._is_exit_control(element) and non_exit_remaining:
                    forced = self._forced_action(
                        state,
                        "Finish the remaining controls in this dialog or screen before using its exit control.",
                    )
                    if forced and (
                        forced.target != decision.target
                        or forced.action != decision.action
                        or forced.value != decision.value
                    ):
                        return forced, "premature_exit_replaced"

        if (
            decision.action in _PASSIVE_ACTIONS
            and decision.action in state.passive_actions
        ):
            forced = self._forced_action(
                state,
                "A passive action was already used in this state; continue with a new interactive candidate.",
            )
            if forced:
                return forced, "passive_loop_replaced"
            backtrack = self._backtrack_decision()
            if backtrack:
                return (
                    backtrack[0],
                    backtrack[1] or "passive_loop_replaced_by_backtrack",
                )

        return decision, None

    def record_action(
        self,
        before: Observation,
        decision: AgentDecision,
        result: ActionResult,
        after: Observation,
    ) -> None:
        before_key = self.observe(before)
        focus_was_reached = self.focus_reached
        before_state = self.states[before_key]
        issued_restore = self._issued_restore_step
        selected_move = self._move_for_decision(
            list(before_state.candidates.items()), decision
        )
        transition_fingerprint: str | None = None
        entered_focus_by_target = False
        if selected_move is not None:
            fingerprint, element = selected_move
            transition_fingerprint = fingerprint
            policy_blocked = "policyblocked" in result.message.lower()
            if result.success:
                before_state.attempted.add(fingerprint)
                before_state.failures.pop(fingerprint, None)
            elif policy_blocked:
                before_state.attempted.add(fingerprint)
                before_state.blocked.add(fingerprint)
            else:
                failures = before_state.failures.get(fingerprint, 0) + 1
                before_state.failures[fingerprint] = failures
                # One retry handles a transient rerender/stale locator. A
                # second failure classifies the candidate as blocked so a
                # permanently unavailable control cannot make the run infinite.
                if failures >= 2:
                    before_state.attempted.add(fingerprint)
                    before_state.blocked.add(fingerprint)
            classified = fingerprint in before_state.attempted
            if result.success or policy_blocked:
                self.global_route_moves_attempted.add((before_state.url, fingerprint))
                # The exact same semantic move may remain mounted while a
                # same-route state evolves (filters, carts, toggles). If it is
                # exercised in a descendant, settle the matching move in its
                # logical ancestors too; otherwise the planner later tries to
                # restore an obsolete DOM snapshot just to repeat completed
                # work.
                for ancestor_key in self.path:
                    if ancestor_key == before_key:
                        continue
                    ancestor = self.states.get(ancestor_key)
                    if ancestor is None or fingerprint not in ancestor.candidates:
                        continue
                    ancestor.attempted.add(fingerprint)
                    ancestor.failures.pop(fingerprint, None)
                    if policy_blocked:
                        ancestor.blocked.add(fingerprint)
            if classified and element.get("in_navigation"):
                self.global_navigation_attempted.add(fingerprint)
            if self.focus_area and not self.focus_reached:
                leaf = re.split(r"\s*(?:>|/|→)\s*", self.focus_area)[-1]
                leaf_tokens = _meaningful_tokens(leaf)
                surface = _normalise_text(
                    f"{element.get('name') or ''} {element.get('href') or ''}"
                )
                entered_focus_by_target = bool(leaf_tokens) and all(
                    token in surface for token in leaf_tokens
                )
            href = str(element.get("href") or "")
            if classified and href and decision.action == "click":
                self.global_destinations_attempted.add(canonical_url(href))
        if decision.action in _PASSIVE_ACTIONS:
            before_state.passive_actions.add(decision.action)

        after_key_hint = self.state_key(after)
        child_is_new = after_key_hint not in self.states
        after_key = self.observe(after)
        if issued_restore is not None and child_is_new:
            # A failed restore can land on a previously unseen variant of a
            # known route (for example, another cart combination).  That
            # incidental snapshot is evidence, not a new exploration branch;
            # otherwise each recovery attempt grows the frontier it is trying
            # to reduce.
            child_state = self.states[after_key]
            child_state.inherited.update(child_state.candidates)
        if result.success and after_key != before_key and child_is_new:
            # A child often keeps its parent's controls mounted. Propagate only
            # moves already classified in the parent; still-untried shared
            # controls remain executable in the current live DOM. This avoids
            # both duplicate work and artificial backtracking after every
            # small same-page mutation.
            child_state = self.states[after_key]
            classified_parent = before_state.attempted | before_state.inherited
            child_state.inherited.update(
                classified_parent & set(child_state.candidates)
            )
        if (
            entered_focus_by_target
            and result.success
            and after_key != before_key
            and not self.focus_reached
        ):
            self._activate_focus(after_key)
        if (
            self.focus_reached
            and self._url_is_inside_focus(after.url)
            and (
                not self.focus_area
                or after_key != self.focus_entry_anchor
                and (
                    after_key in self.active_states
                    or before_key in self.active_states
                    or self._matches_focus(after)
                )
            )
        ):
            self.active_states.add(after_key)

        is_focus_entry = bool(
            self.focus_area
            and result.success
            and after_key == self.focus_root
            and before_key != after_key
            and (not focus_was_reached or entered_focus_by_target)
        )

        if (
            result.success
            and after_key != before_key
            and transition_fingerprint is not None
        ):
            self._register_transition(
                before_key,
                after_key,
                transition_fingerprint,
                decision,
                is_focus_entry=is_focus_entry,
            )

        if issued_restore is not None:
            if issued_restore.source_key == before_key:
                if issued_restore.kind == "replay" and issued_restore.fingerprint:
                    recipe = _TransitionRecipe(
                        parent_key=before_key,
                        child_key=issued_restore.expected_key,
                        fingerprint=issued_restore.fingerprint,
                        action=decision.action,
                        value=(
                            str(decision.value)[:120]
                            if decision.value is not None
                            else None
                        ),
                        key=decision.key,
                    )
                    failure_key = (
                        "replay",
                        issued_restore.target_key,
                        self._recipe_id(recipe),
                    )
                    if result.success and after_key == issued_restore.expected_key:
                        self.restoration_failures.pop(failure_key, None)
                    else:
                        self.restoration_failures[failure_key] = (
                            self.restoration_failures.get(failure_key, 0) + 1
                        )
                elif issued_restore.kind == "navigate":
                    failure_key = self._navigate_failure_key(
                        issued_restore.target_key,
                        issued_restore.expected_key,
                    )
                    restored = result.success and (
                        after_key == issued_restore.expected_key
                        or self._find_replay_path(after_key, issued_restore.target_key)
                        is not None
                    )
                    if restored:
                        self.restoration_failures.pop(failure_key, None)
                    else:
                        self.restoration_failures[failure_key] = (
                            self.restoration_failures.get(failure_key, 0) + 1
                        )
                if after_key == issued_restore.target_key:
                    self.restoration_target = None
                    self.restoration_steps = 0
            self._issued_restore_step = None

        if decision.action in {"go_back", "navigate"} and (
            not result.success or after_key == before_key
        ):
            failures = self.backtrack_failures.get(before_key, 0) + 1
            if (
                decision.action == "go_back"
                and "policyblocked" in result.message.lower()
            ):
                # Crossing an authentication/application boundary is
                # deterministic, not transient.  Skip the identical retry and
                # settle or restore another logical frontier immediately.
                failures = max(failures, 2)
            self.backtrack_failures[before_key] = failures
        elif result.success and after_key != before_key:
            self.backtrack_failures.pop(before_key, None)

        if after_key != before_key and result.success:
            if after_key in self.path:
                self.path = self.path[: self.path.index(after_key) + 1]
            elif decision.action == "go_back" and len(self.path) > 1:
                self.path.pop()
                if self.path[-1] != after_key:
                    self.path.append(after_key)
            else:
                if not self.path or self.path[-1] != before_key:
                    self.path.append(before_key)
                self.path.append(after_key)
        self.current_key = after_key

    def context(self, observation: Observation) -> dict[str, Any]:
        key = self.observe(observation)
        state = self.states[key]
        remaining = self._remaining(state)
        active_states = [
            candidate
            for candidate_key, candidate in self.states.items()
            if self._is_active(candidate_key)
        ]
        candidates_discovered = sum(
            len(set(candidate.candidates) - candidate.inherited)
            for candidate in active_states
        )
        candidates_attempted = sum(
            len(candidate.attempted) for candidate in active_states
        )
        candidates_blocked = sum(len(candidate.blocked) for candidate in active_states)
        candidates_inherited = sum(
            len(candidate.inherited) for candidate in active_states
        )
        candidates_retry_pending = sum(
            len(
                [
                    fingerprint
                    for fingerprint in candidate.failures
                    if fingerprint not in candidate.attempted
                ]
            )
            for candidate in active_states
        )
        frontier_candidates = sum(
            len(self._remaining(candidate)) for candidate in active_states
        )
        return {
            "scope": self.focus_area or "entire same-origin application",
            "focus_reached": self.focus_reached,
            "state_key": key,
            "known_states": len(self.states),
            "active_states": len(active_states),
            "path_depth": len(self.path),
            "frontier_states": len(self._frontier_states()),
            "transition_recipes": sum(
                len(recipes) for recipes in self.transitions.values()
            ),
            "restoration_target": self.restoration_target,
            "restoration_steps": self.restoration_steps,
            "restorations_abandoned": len(self.unreachable_restorations),
            "last_unreachable_restoration": (
                self.unreachable_restorations[-1]
                if self.unreachable_restorations
                else None
            ),
            "candidates_discovered": candidates_discovered,
            "candidates_attempted": candidates_attempted,
            "candidates_blocked": candidates_blocked,
            "candidates_inherited": candidates_inherited,
            "candidates_retry_pending": candidates_retry_pending,
            "frontier_candidates": frontier_candidates,
            "current_untried": [
                {
                    "move_key": move_key,
                    "ref": element.get("ref"),
                    "name": str(element.get("name") or "")[:160],
                    "href": element.get("href"),
                    "tag": element.get("tag"),
                    "role": element.get("role"),
                    "type": element.get("type"),
                    "context": str(element.get("context") or "")[:160] or None,
                    "in_navigation": bool(element.get("in_navigation")),
                    "in_viewport": bool(element.get("in_viewport")),
                    "document_top": element.get("document_top"),
                    "is_exit_control": self._is_exit_control(element),
                    "suggested_action": self._move_action(element),
                    "suggested_value": self._move_value(element),
                    "suggested_key": self._move_keypress(element),
                    "option_label": element.get("_option_label"),
                    "option_selected": element.get("_option_selected"),
                }
                for move_key, element in remaining[:_MAX_EXPOSED_MOVES]
            ],
            "completion_rule": (
                "finish is valid only when current_untried is empty, all known frontier states "
                "are exhausted, and backtracking has returned to the scope root"
            ),
        }
