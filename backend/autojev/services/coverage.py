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
_REPLAYABLE_ACTIONS = {"click", "hover", "press", "select", "fill"}
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
_RECOVERABLE_RESTRICTION_REASONS = {
    "no_safe_restore_route", "backtrack_attempt_limit", "restore_step_limit",
    "restore_attempt_limit", "stalled_restoration", "consecutive_restore_without_progress",
}
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
    in_navigation = bool(element.get("in_navigation"))
    # An onclick navigation anchor often declares href="#". The browser's
    # resolved .href includes the current route, which is not its destination
    # and must not manufacture a fresh global menu control on every page.
    # Keep meaningful hash routes and real URLs distinct. Older observations
    # did not preserve getAttribute('href'); their trailing bare # is safe too.
    placeholder_navigation = in_navigation and (
        ("raw_href" in element and element.get("raw_href") in {"", "#"})
        or "raw_href" not in element and href.endswith("#")
    )
    if placeholder_navigation:
        href = "#"
    if href:
        with suppress(ValueError):
            if not placeholder_navigation:
                href = canonical_url(href)
    name = _normalise_text(str(element.get("name") or ""))[:300]
    context = _normalise_text(str(element.get("context") or ""))[:160]
    if in_navigation:
        # Descendant labels, expanded submenus and live counters describe the
        # navigation's state, not the identity of an existing item. Prefer its
        # stable owner marker; old observations can at least retain owner tag.
        # Distinct newly revealed children still have distinct names/hrefs.
        context = _normalise_text(str(element.get("navigation_owner") or ""))[:160]
        if not context:
            context = _normalise_text(str(element.get("context") or "")).split(" ", 1)[0]
    else:
        menu_tokens = set(re.findall(r"[a-z0-9]+", name))
        if menu_tokens.intersection(_MENU_WORDS) and menu_tokens.intersection(
            _OPEN_WORDS | _CLOSE_WORDS
        ):
            # Drawer toggles frequently sit outside nav and inherit the whole
            # header/menu text. Their changing siblings cannot create coverage
            # debt for another open/close of every cart and submenu variant.
            context = _normalise_text(str(element.get("overlay_group") or ""))[:160]
    # Inline validation feedback is data, not another copy of the same input
    # or submit. Retain row/card contexts for repeated line-item controls, but
    # anchor conventional form controls to their observed stable form owner.
    if element.get("form") and (context.startswith("form ") or context == "form") and (
        element.get("tag") in {"input", "select", "textarea"}
        or element.get("type") == "submit"
        or name in {"continue", "continuar", "next", "submit", "save", "salvar"}
        or set(re.findall(r"[a-z0-9]+", name)).intersection(_EXIT_WORDS)
    ):
        context = f"form:{element['form']}"
    # Only the executor can establish that a declared DOM marker is unique.
    # This identity excludes mutable sibling text (cart contents, validation
    # feedback) while retaining the control's name/action/destination below.
    # Unmarked or ambiguous controls keep their existing contextual identity.
    semantic_identity = element.get("semantic_identity")
    if isinstance(semantic_identity, str) and semantic_identity:
        context = f"identity:{semantic_identity}"
    # A cart badge describes live data, not a different navigation control.
    # Restrict this to complete cart-link labels: numbers in product names,
    # row contexts and other functional controls retain their identity.
    cart_label = re.fullmatch(
        r"(?P<label>(?:shopping\s+)?cart|carrinho(?:\s+de\s+compras)?)"
        r"(?:[\s,:()\[\]-]*(?:empty|vazi[oa]|\d+\s*(?:items?|itens?|products?|produtos?)?)"
        r"[\s()\[\]]*)?",
        name,
    )
    if href and cart_label and (
        _normalise_text(str(element.get("tag") or "")) == "a"
        or _normalise_text(str(element.get("role") or "")) == "link"
    ):
        name = cart_label.group("label")
        context = ""
    stable = {
        "tag": _normalise_text(str(element.get("tag") or "")),
        "role": _normalise_text(str(element.get("role") or "")),
        "type": _normalise_text(str(element.get("type") or "")),
        "name": name,
        "href": href,
        "context": context,
        "in_navigation": in_navigation,
        "aria_haspopup": _normalise_text(str(element.get("aria_haspopup") or "")),
    }
    if in_navigation and element.get("navigation_control"):
        # Declared item IDs/test markers disambiguate homonymous siblings
        # without depending on text of every other (possibly hidden) item.
        stable["navigation_control"] = _normalise_text(str(element["navigation_control"]))[:200]
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
    form_setup_required: set[str] = field(default_factory=set)
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
    # Only values generated by the deterministic planner, never existing DOM
    # field contents or credentials. Restore prerequisites before submitting.
    setup: tuple[tuple[str, str, str], ...] = ()
    checkout_submit_verified: bool = False


@dataclass(frozen=True, slots=True)
class _IssuedRestoreStep:
    kind: str
    target_key: str
    source_key: str
    expected_key: str
    fingerprint: str | None = None
    recipe_parent_key: str | None = None


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
        self.restoration_edge_uses: dict[tuple[str, str, str], int] = {}
        self.restoration_steps = 0
        self._issued_restore_step: _IssuedRestoreStep | None = None
        self.unreachable_restorations: list[dict[str, Any]] = []
        self.restrictions: list[dict[str, Any]] = []
        self._restriction_ids: set[str] = set()
        self._restriction_cursor = 0
        self.restriction_resolutions: list[dict[str, Any]] = []
        self._restriction_resolution_cursor = 0
        self.restriction_updates: list[dict[str, Any]] = []
        self._restriction_update_cursor = 0
        self._failed_cart_toggle_prerequisites: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self.verified_document_urls: set[str] = set()
        self.failed_document_urls: set[str] = set()
        self.synthetic_form_values: dict[tuple[str, str], tuple[str, str]] = {}
        self.form_probes_completed: set[tuple[str, str, str]] = set()
        self.form_happy_paths_completed: set[tuple[str, str]] = set()
        self.failed_form_fields: set[tuple[str, str]] = set()
        # Preparation is finite even when filling one field clears another.
        # Count issued local setup actions per probe, not visits or DOM refs.
        self._form_probe_setup_attempts: dict[tuple[str, str, str, str, str], int] = {}

    def _restrict(
        self, state: _CoverageState, move_key: str, element: dict[str, Any],
        *, reason: str, source_url: str | None = None,
    ) -> None:
        """Persist every excluded move, not merely the most recent branch."""
        identity = hashlib.sha256(f"{state.key}:{move_key}:{reason}".encode()).hexdigest()[:24]
        if identity in self._restriction_ids:
            return
        self._restriction_ids.add(identity)
        probe = element.get("_validation_probe")
        record = {
            "id": identity, "reason": reason,
            "status": "active",
            "source_url": source_url or state.url, "target_url": state.url,
            "state_key": state.key, "move_key": move_key,
            "action": self._move_action(element), "target": element.get("ref"),
            "name": str(element.get("name") or "")[:200],
            "href": str(element.get("href") or "")[:500] or None,
            **({"validation_probe": dict(probe), "field_name": probe.get("name")}
               if isinstance(probe, dict) else {}),
        }
        entity = element.get("_cart_entity")
        if isinstance(entity, dict) and element.get("_observed_cart_action") == "add":
            record["cart_entity"] = dict(entity)
            dependency = self._failed_cart_toggle_prerequisites.get(self._cart_dependency_key(state.url, entity))
            if reason in _RECOVERABLE_RESTRICTION_REASONS and dependency:
                record["dependency"] = dict(dependency)
        self.restrictions.append(record)

    def drain_restrictions(self) -> list[dict[str, Any]]:
        """Return new records once for orchestration/event persistence."""
        records = self.restrictions[self._restriction_cursor:]
        self._restriction_cursor = len(self.restrictions)
        return [dict(record) for record in records]

    def drain_restriction_resolutions(self) -> list[dict[str, Any]]:
        """Emit exact-execution recovery records once, keeping their history."""
        records = self.restriction_resolutions[self._restriction_resolution_cursor:]
        self._restriction_resolution_cursor = len(self.restriction_resolutions)
        return [dict(record) for record in records]

    def drain_restriction_updates(self) -> list[dict[str, Any]]:
        """Emit later causal evidence without discarding the original debt."""
        records = self.restriction_updates[self._restriction_update_cursor:]
        self._restriction_update_cursor = len(self.restriction_updates)
        return [dict(record) for record in records]

    @staticmethod
    def _cart_dependency_key(url: str, entity: dict[str, Any]) -> tuple[str, str, str, str]:
        return (url, str(entity["surface"]), str(entity["item_id"]), str(entity["control_identity"]))

    @staticmethod
    def _cart_product_refs(observation: Observation) -> dict[str, dict[str, Any]]:
        """Ground inverse toggles in unique structured entities, never labels.

        The executor may report a declared item ID or an exact observed name
        ID. Both remain route/surface scoped; duplicated IDs or references are
        ambiguous and cannot establish a prerequisite relationship.
        """
        state = observation.page_state
        detail = state.get("product_detail")
        surfaces = {
            "products": state.get("products") or [],
            "cart.items": (state.get("cart") or {}).get("items") or [],
            "product_detail": [detail] if isinstance(detail, dict) else [],
        }
        matches: dict[str, list[dict[str, Any]]] = {}
        for surface, products in surfaces.items():
            if not isinstance(products, list):
                continue
            valid = [item for item in products if isinstance(item, dict)
                     and isinstance(item.get("id"), str) and item["id"]
                     and item.get("cart_action") in {"add", "remove"}]
            for product in valid:
                if sum(item["id"] == product["id"] for item in valid) != 1:
                    continue
                refs = product.get("action_refs") or []
                if not isinstance(refs, list) or len(refs) != 1:
                    # Multiple mounted cart buttons for one entity require
                    # independent control ownership; a deduplicated candidate
                    # map cannot prove which inverse toggle stayed unchanged.
                    continue
                identity = {
                    "surface": surface, "item_id": product["id"],
                    "identity_source": "observed_name_id" if product["id"].startswith("name:") else "observed_item_id",
                    "cart_action": product["cart_action"],
                }
                for ref in refs:
                    if isinstance(ref, str) and ref:
                        matches.setdefault(ref, []).append(identity)
        return {ref: items[0] for ref, items in matches.items() if len(items) == 1}

    def _record_cart_toggle_prerequisite(
        self, state: _CoverageState, element: dict[str, Any], decision: AgentDecision,
        result: ActionResult, after: Observation,
    ) -> None:
        """Explain blocked Add coverage only after a native Remove failed.

        A failing counter alone is not proof that Add cannot be exercised.
        Require the exact entity's control to remain Remove and a failed local
        cart_control_state assertion expecting Add. Never rerun or bypass the
        faulty prerequisite, and never resolve the dependent Add restriction.
        """
        entity = element.get("_cart_entity")
        if (not result.success or decision.action != "click" or not isinstance(entity, dict)
                or element.get("_observed_cart_action") != "remove"
                or canonical_url(after.url) != state.url):
            return
        key = self._cart_dependency_key(state.url, entity)
        matches = [move for move in self._candidate_map(after).values()
                   if isinstance(move.get("_cart_entity"), dict)
                   and self._cart_dependency_key(state.url, move["_cart_entity"]) == key]
        if len(matches) != 1:
            return
        if matches[0].get("_observed_cart_action") == "add" and any(
            assertion.kind == "cart_control_state" and assertion.passed
            and assertion.expected == "add" and assertion.actual == "add"
            for assertion in result.assertions
        ):
            # A later real recovery invalidates the remembered obstruction for
            # future debt. Existing records retain their historical evidence;
            # only actual Add execution can resolve their untested coverage.
            self._failed_cart_toggle_prerequisites.pop(key, None)
            return
        failed_toggle = any(assertion.kind == "cart_control_state" and not assertion.passed
                            and assertion.expected == "add" and assertion.actual == "remove"
                            for assertion in result.assertions)
        if not failed_toggle or matches[0].get("_observed_cart_action") != "remove":
            return
        dependency = {
            "kind": "failed_inverse_cart_toggle", "source": "actual_business_assertion",
            "item_id": entity["item_id"], "surface": entity["surface"],
            "identity_source": entity["identity_source"], "source_url": state.url,
            "required_action": "add", "name": str(element.get("name") or "")[:200],
            "action": decision.action, "action_step": result.evidence.get("action_step"),
            "occurred_at": result.finished_at, "screenshot": result.screenshot,
            "failed_assertion_kinds": [assertion.kind for assertion in result.assertions if not assertion.passed],
        }
        self._failed_cart_toggle_prerequisites[key] = dependency
        for record in self.restrictions:
            record_entity = record.get("cart_entity")
            if (record.get("status") == "resolved" or record.get("reason") not in _RECOVERABLE_RESTRICTION_REASONS
                    or record.get("target_url") != state.url or not isinstance(record_entity, dict)
                    or self._cart_dependency_key(state.url, record_entity) != key
                    or record.get("dependency") == dependency):
                continue
            record["dependency"] = dict(dependency)
            self.restriction_updates.append(dict(record))

    def _resolve_executed_restrictions(
        self, state: _CoverageState, move_key: str, decision: AgentDecision,
        result: ActionResult,
    ) -> None:
        """Resolve temporary route debt only after that exact move was tested.

        Reaching a route or seeing a similarly named control is insufficient.
        A later actual execution may pass or fail its business assertions; both
        exercise coverage, and neither result is rewritten by this ledger.
        """
        if not result.success or decision.action != "click":
            return
        recovered = []
        for record in self.restrictions:
            if (record.get("status") == "resolved"
                    or record.get("reason") not in _RECOVERABLE_RESTRICTION_REASONS
                    or record.get("target_url") != state.url
                    or record.get("move_key") != move_key):
                continue
            resolution = {
                "source": "actual_semantic_move_execution",
                "action_step": result.evidence.get("action_step"),
                "occurred_at": result.finished_at,
                "source_url": result.before_url,
                "after_url": result.after_url,
                "action": decision.action,
                "target": decision.target,
                "screenshot": result.screenshot,
                "success": result.success,
                "assertions_passed": all(assertion.passed for assertion in result.assertions),
                "assertion_kinds": [assertion.kind for assertion in result.assertions],
            }
            record.update(status="resolved", resolution=resolution)
            recovered.append(record)
            self.restriction_resolutions.append(dict(record))
        if recovered and not any(
            record.get("status") != "resolved" and record.get("target_url") == state.url
            and record.get("move_key") == move_key for record in self.restrictions
        ):
            for historical in self.states.values():
                if historical.url == state.url:
                    historical.blocked.discard(move_key)

    def _observe_document_response(self, metadata: Any) -> None:
        if not isinstance(metadata, dict) or not metadata.get("url"):
            return
        url = canonical_url(str(metadata["url"]))
        status = metadata.get("status_code")
        if isinstance(status, int) and status >= 400:
            self.failed_document_urls.add(url)
            self.verified_document_urls.discard(url)
        elif isinstance(status, int) and 200 <= status < 400:
            self.verified_document_urls.add(url)

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
                "text": cls._safe_fill_value(element),
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

    @staticmethod
    def _safe_fill_value(element: dict[str, Any]) -> str:
        """Use distinct synthetic text per observed label to expose field mixups."""

        name = _normalise_text(str(element.get("name") or ""))
        if re.search(r"\b(?:zip|postal|cep)\b", name):
            return "01001000"
        label = re.sub(r"[^a-z ]", " ", name)
        label = re.sub(r"\s+", " ", label).strip()[:48]
        return f"AutoJev QA {label}" if label else "AutoJev QA exploratory input"

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
                and (element.get("interactable") is False
                     or element.get("obstructed_by_overlay") is True)
            )
        ):
            return False
        tag = _normalise_text(str(element.get("tag") or ""))
        element_type = _normalise_text(str(element.get("type") or ""))
        name = _normalise_text(str(element.get("name") or ""))
        if not name and not element.get("href"):
            return False
        destructive = _DESTRUCTIVE_WORDS
        if element.get("reversible_cart_action") == "remove" and re.fullmatch(
            r"(?:remove(?: from cart)?|remover(?: do carrinho)?)", name,
        ):
            destructive = destructive - {"remove", "remover"}
        if any(word in name for word in destructive):
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
        """Reject only an exact link explicitly marked as the current page."""

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
            for name in (
                "aria_expanded",
                "aria_pressed",
                "aria_selected",
                "aria_haspopup",
            )
        ):
            return False
        # A same-URL anchor may still run application code. Suppress only the
        # explicit HTML/ARIA declaration that this is the current navigation
        # item; other anchors get one bounded attempt and a no-op is omitted
        # from the CT inventory.
        if _normalise_text(str(element.get("aria_current") or "")) not in {
            "page",
            "location",
            "true",
        }:
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
                element.get("interactable") is None
                and element.get("in_horizontal_viewport") is True
                and _normalise_state(element.get("aria_hidden")) is not True
            ):
                return True
        return False

    @staticmethod
    def _cart_journey_bucket(observation: Observation) -> str | None:
        """Keep one empty and one populated purchase lane, not a cart power set.

        Prefer the executor's action-intent ledger over a possibly broken cart
        badge. This is coverage context only: it never changes the observed cart
        or supplies an expected business result during restoration.
        """
        state = observation.page_state
        expected = state.get("cart_expectation") or {}
        cart = state.get("cart") or {}
        checkout = state.get("checkout") or {}
        for source in (expected, cart, checkout):
            if source.get("items_known") is True and isinstance(source.get("items"), list):
                return "populated" if source["items"] else "empty"
        if any(item.get("present") is True for item in expected.get("pending_membership", [])
               if isinstance(item, dict)):
            return "populated"
        count = cart.get("count")
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            return "populated" if count else "empty"
        return None

    @classmethod
    def _cart_journey_role(cls, observation: Observation, element: dict[str, Any]) -> str | None:
        """Recognize only grounded shopping entry/form/confirmation controls."""
        name = _normalise_text(str(element.get("name") or ""))
        cart = observation.page_state.get("cart") or {}
        checkout = observation.page_state.get("checkout") or {}
        if checkout.get("stage") == "review" and re.fullmatch(
            r"cancel|cancel order|cancelar|cancelar pedido", name,
        ):
            # Cancelling a review must preserve its actual basket. Keep one
            # empty and one populated cancellation test, not all combinations
            # of products; a successful empty exit cannot consume this check.
            return "checkout_review_exit"
        if (checkout.get("stage") not in {"review", "complete"}
                and re.fullmatch(r"cancel|cancelar", name)
                and re.search(r"\bcheckout\b", _normalise_text(
                    f"{urlsplit(observation.url).path} {observation.title}"))
                and any(field.get("form") and not field.get("sensitive")
                        and field.get("type") != "password"
                        for field in observation.page_state.get("fields", []))
                and any(form.get("submit_refs") for form in observation.page_state.get("forms", []))):
            return "checkout_form_exit"
        if cls._is_exit_control(element):
            return None
        ref = element.get("ref")
        if ref in (cart.get("link_refs") or []):
            return "cart_entry"
        if element.get("href") and re.fullmatch(
            r"(?:shopping\s+)?cart(?:[\s,:()\[\]-]*(?:empty|\d+\s*items?)[\s()\[\]]*)?"
            r"|carrinho(?:\s+de\s+compras)?(?:[\s,:()\[\]-]*(?:vazi[oa]|\d+\s*itens?)[\s()\[\]]*)?",
            name,
        ):
            return "cart_entry"
        if re.fullmatch(r"checkout|check out|proceed to checkout|ir para checkout|finalizar compra", name):
            return "checkout_entry"
        if checkout.get("stage") == "review" and (
            ref in (checkout.get("finish_refs") or [])
            or re.fullmatch(r"finish|place order|complete order|finalizar pedido|confirmar pedido", name)
        ):
            return "finish"
        form = str(element.get("form") or "")
        checkout_surface = _normalise_text(
            f"{form} {urlsplit(observation.url).path} {observation.title}"
        )
        if form and re.search(r"\bcheckout\b", checkout_surface) and (
            element.get("tag") in {"input", "select", "textarea"}
            or cls._is_form_submit(element)
        ):
            return "checkout_form"
        return None

    def _candidate_map(
        self, observation: Observation, *, require_interactable: bool = True
    ) -> dict[str, dict[str, Any]]:
        candidates: dict[str, dict[str, Any]] = {}
        menu_is_open = self._menu_is_open(observation)
        cart_bucket = self._cart_journey_bucket(observation)
        cart_products_by_ref = self._cart_product_refs(observation)
        fields = {
            str(field.get("ref")): field
            for field in observation.page_state.get("fields", [])
            if isinstance(field, dict) and field.get("ref")
        }
        sensitive_forms = {str(field["form"]) for field in fields.values()
                           if field.get("form") and field.get("sensitive") is True}
        sensitive_forms.update(str(element["form"]) for element in observation.interactive_elements
                               if element.get("form") and (
                                   element.get("sensitive") is True
                                   or _normalise_text(str(element.get("type") or "")) == "password"))
        for document_order, element in enumerate(observation.interactive_elements):
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
            journey_role = self._cart_journey_role(observation, element) if cart_bucket else None
            if journey_role:
                # Ordinary controls stay route-deduplicated. Only the finite
                # purchase lane distinguishes empty from populated, so an
                # early empty checkout cannot consume the later real purchase.
                fingerprint = hashlib.sha256(
                    f"{fingerprint}:cart_journey:{cart_bucket}".encode()
                ).hexdigest()
            for action, value in self._candidate_actions(element):
                move_key = self._move_key(fingerprint, action, value)
                move = dict(element)
                field = fields.get(str(element.get("ref")))
                if field is not None:
                    move["required"] = bool(element.get("required") or field.get("required"))
                    move["field_identity"] = field.get("identity")
                move["_element_fingerprint"] = fingerprint
                move["_document_order"] = document_order
                move["_move_action"] = action
                move["_move_value"] = value
                move["_move_key"] = None
                product = cart_products_by_ref.get(str(element.get("ref")))
                if product and action == "click":
                    move["_observed_cart_action"] = product["cart_action"]
                    # The unique observed entity identifies the row/card. The
                    # mutable Add/Remove label and its surrounding text cannot
                    # become a different toggle identity; structural semantics
                    # still distinguish different control kinds on that row.
                    # Toggle IDs/test markers themselves may change from
                    # add-to-cart to remove. The already-unique structured
                    # entity anchors this inverse dependency; compare only
                    # control structure here, not its action-specific marker.
                    control_identity = semantic_element_fingerprint({
                        **element, "name": "", "context": "", "semantic_identity": None,
                    })
                    move["_cart_entity"] = {key: product[key] for key in ("surface", "item_id", "identity_source")}
                    move["_cart_entity"]["control_identity"] = control_identity
                if journey_role:
                    move["_cart_journey"] = cart_bucket
                    move["_cart_journey_role"] = journey_role
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
        # Negative submissions have their own semantic move identity. They
        # remain hidden until ordinary required inputs have been exercised,
        # then run before a departing positive submit erases the live form.
        normal = list(candidates.values())
        for submit in normal:
            if (not self._is_form_submit(submit)
                    or str(submit.get("form") or "") in sensitive_forms):
                continue
            group = self._form_group(submit)
            peers = [move for move in normal if self._is_form_submit(move)
                     and self._form_group(move) == group]
            if len(peers) != 1:
                continue
            for field in normal:
                if (field.get("required") is not True or self._move_action(field) != "fill"
                        or self._form_group(field) != group):
                    continue
                field_fp = str(field["_element_fingerprint"])
                probe_key = self._move_key(str(submit["_element_fingerprint"]), "required_probe", field_fp)
                probe = dict(submit)
                probe["_probe_target"] = field_fp
                probe["_validation_probe"] = {
                    "kind": "required_field", "field_identity": field.get("field_identity") or field_fp,
                    "planner_field": field_fp, "name": str(field.get("name") or ""),
                    "form": group, "expected": "validation_rejected",
                }
                candidates[probe_key] = probe
        return candidates

    def _overlay_dismissal(self, observation: Observation) -> AgentDecision | None:
        """Close a harmless menu before retrying an obstructed background.

        Dialogs with unsaved fields are not automatically dismissed. The
        executor supplies explicit harmless-dismiss metadata; the narrow menu
        fallback also works with older observations.
        """
        obstructed = [
            element for element in observation.interactive_elements
            if isinstance(element, dict)
            and element.get("obstructed_by_overlay") is True
            and self._safe_candidate(element, self.origin, require_interactable=False)
        ]
        if not obstructed:
            return None
        blocked_moves = {
            key: move for key, move in self._candidate_map(observation, require_interactable=False).items()
            if move.get("ref") in {element.get("ref") for element in obstructed}
            and not move.get("_probe_target")
        }
        uncovered = any(
            (canonical_url(observation.url), key) not in self.global_route_moves_attempted
            and not (move.get("in_navigation") and key in self.global_navigation_attempted)
            and not (move.get("href") and not move.get("_cart_journey_role")
                     and canonical_url(str(move["href"])) in self.global_destinations_attempted)
            and not any(key in previous.blocked for previous in self.states.values()
                        if previous.url == canonical_url(observation.url))
            for key, move in blocked_moves.items()
        )
        # A menu may be an intentional intermediate step in a known recipe.
        # Never immediately close it merely because previously-covered
        # background controls are obscured: that undoes restoration progress.
        if self.restoration_target is not None:
            current_path = self._live_replay_path(self.current_key or "", self.restoration_target)
            if current_path and self._replay_decision(current_path[0], self.restoration_target,
                                                     current_key=self.current_key) is not None:
                return None
            if not uncovered:
                uncovered = any(
                    (path := self._find_replay_path(key, self.restoration_target))
                    and path[0].fingerprint in blocked_moves
                    for key, previous in self.states.items()
                    if previous.url == canonical_url(observation.url)
                )
        if not uncovered:
            return None
        state = self.states.get(self.current_key or "")
        if state is not None and any(
            element.get("in_overlay") and not element.get("overlay_dismiss")
            and not self._is_exit_control(element)
            for _, element in self._remaining(state)
        ):
            return None
        dismiss = [
            element for element in observation.interactive_elements
            if isinstance(element, dict) and self._safe_candidate(element, self.origin)
            and (element.get("overlay_dismiss") is True
                 or self._menu_is_open(observation)
                 and self._menu_toggle_kind(element) == "close")
        ]
        if len(dismiss) != 1:
            return None
        return AgentDecision(
            action="click", target=str(dismiss[0]["ref"]),
            rationale="Dismiss the observed harmless overlay before testing obstructed background controls.",
        )

    def _inline_validation_dismissal(self, state: _CoverageState) -> AgentDecision | None:
        """Exercise an observed transient error control before submit erases it.

        DOM discovery independently proves that this is a harmless button in
        form-owned validation feedback. Labels alone never grant this policy.
        Its native click/check is real coverage, not an invented recovery.
        """
        candidates = [element for _, element in self._remaining(state)
                      if element.get("validation_dismiss") is True
                      and element.get("validation_form")
                      and self._move_action(element) == "click"]
        if len(candidates) != 1:
            return None
        return AgentDecision(
            action="click", target=str(candidates[0]["ref"]),
            rationale="Verify the observed inline validation dismissal before a form submission removes its transient control.",
        )

    def _form_probe_ready(self, state: _CoverageState, group: str) -> bool:
        """Require genuinely exercised ordinary fields, not a invented happy path.

        Test negatives while the conventional form is still mounted, before
        its positive submit can leave the route or consume a cart lifecycle.
        Failed native fills still count as attempts, never as retained values.
        """
        if (state.url, group) in self.form_happy_paths_completed:
            return True
        required = [(move_key, field) for move_key, field in state.candidates.items()
                    if not field.get("_probe_target") and self._move_action(field) == "fill"
                    and field.get("required") is True and self._form_group(field) == group]
        return bool(required) and all(
            move_key in state.attempted | state.inherited
            or (state.url, move_key) in self.global_route_moves_attempted
            for move_key, _ in required
        )

    def _form_probe_action(self, state: _CoverageState) -> AgentDecision | None:
        """Exercise each observed required field empty, with usable peers filled.

        This is a bounded generic policy for conventional non-secret forms.
        A probe is attempted once; business checks, not action success, judge
        whether the application rejected the invalid submission correctly.
        A broken peer cannot blanket-block all targets: feedback may still
        identify this target. If it instead masks this target's validation,
        the executed probe remains an explicit dependency restriction.
        """
        by_form: dict[str, list[dict[str, Any]]] = {}
        submits: dict[str, list[dict[str, Any]]] = {}
        for element in state.candidates.values():
            if element.get("_probe_target"):
                continue
            group = self._form_group(element)
            if not group or element.get("in_navigation"):
                continue
            if self._move_action(element) == "fill":
                by_form.setdefault(group, []).append(element)
            elif self._is_form_submit(element):
                submits.setdefault(group, []).append(element)
        for group, fields in by_form.items():
            if not self._form_probe_ready(state, group):
                continue
            if len(submits.get(group, [])) != 1 or any(
                field.get("has_value") is None for field in fields
            ):
                continue
            # Deduplicate semantic fields, rather than dynamic browser refs.
            fields = list({str(field["_element_fingerprint"]): field for field in fields}.values())
            submit = submits[group][0]
            for target in fields:
                if target.get("required") is not True:
                    continue
                identity = str(target["_element_fingerprint"])
                probe = (state.url, group, identity)
                if probe in self.form_probes_completed:
                    continue
                probe_key = self._move_key(str(submit["_element_fingerprint"]), "required_probe", identity)
                if probe_key not in state.candidates:
                    # Candidate discovery intentionally excludes secret/auth
                    # forms and ambiguous submits. Do not reconstruct a probe
                    # here that bypasses those safety constraints.
                    continue
                blocking_fields: list[dict[str, Any]] = []
                for field in fields:
                    if (field is target or field.get("required") is not True
                            or field.get("has_value") is not False):
                        continue
                    field_fp = str(field["_element_fingerprint"])
                    setup_key = (state.url, group, identity, field_fp, "fill")
                    setup_attempts = self._form_probe_setup_attempts.get(setup_key, 0)
                    known_broken = (state.url, field_fp) in self.failed_form_fields
                    if not known_broken and setup_attempts < 2:
                        self._form_probe_setup_attempts[setup_key] = setup_attempts + 1
                        return AgentDecision(
                            action="fill", target=str(field["ref"]), value=self._move_value(field),
                            rationale="Prepare another required form field before a bounded negative validation probe.",
                        )
                    blocking_fields.append({
                        "field_identity": field.get("field_identity") or field_fp,
                        "name": str(field.get("name") or ""),
                        "planner_field": field_fp,
                        "reason": "input_value_not_retained" if known_broken else "form_probe_setup_retry_exhausted",
                    })
                if target.get("has_value") is True:
                    setup_key = (state.url, group, identity, identity, "clear")
                    setup_attempts = self._form_probe_setup_attempts.get(setup_key, 0)
                    if setup_attempts >= 2:
                        # An unempty target does not execute its negative test.
                        # Preserve this limitation rather than submit a valid
                        # form and manufacture a required-validation verdict.
                        self.form_probes_completed.add(probe)
                        state.attempted.add(probe_key)
                        state.blocked.add(probe_key)
                        self._restrict(state, probe_key, state.candidates.get(probe_key, submit),
                                       reason="form_probe_target_not_empty")
                        continue
                    self._form_probe_setup_attempts[setup_key] = setup_attempts + 1
                    return AgentDecision(
                        action="fill", target=str(target["ref"]), value="",
                        rationale="Clear this required field for a bounded negative test without bypassing the application's input behavior.",
                    )
                hint = {"validation_probe": {
                    "kind": "required_field", "field_identity": target.get("field_identity") or identity,
                    "name": str(target.get("name") or ""), "form": group,
                    "planner_field": identity, "expected": "validation_rejected",
                    "strategy": "blocked_peer_observed" if blocking_fields else "isolated_required_field",
                    "isolation_established": not blocking_fields,
                    "blocking_fields": blocking_fields,
                }}
                return AgentDecision(
                    action="click", target=str(submit["ref"]), scenario_hint=hint,
                    rationale=("Submit this form once and verify feedback attributable to the empty target; retain any masking peer failure as a dependency restriction."
                               if blocking_fields else "Submit this conventional form with only one required field empty and verify attributable rejection."),
                )
        # Negatives deliberately leave their target blank. Restore only
        # previously exercised healthy synthetic inputs for the still-pending
        # positive submit; this is setup, not another coverage claim.
        self._refresh_form_setup(state)
        return None

    @staticmethod
    def _is_exit_control(element: dict[str, Any]) -> bool:
        surface = _normalise_text(
            f"{element.get('name') or ''} {element.get('aria_label') or ''}"
        )
        tokens = set(re.findall(r"[a-z0-9]+", surface))
        return bool(tokens & _EXIT_WORDS)

    @staticmethod
    def _form_group(element: dict[str, Any]) -> str | None:
        if element.get("form"):
            group = str(element["form"])
            if element.get("_cart_journey_role") == "checkout_form":
                group += f"::cart_journey:{element['_cart_journey']}"
            return group
        context = _normalise_text(str(element.get("context") or ""))
        return context if context.startswith("form ") or context == "form" else None

    @classmethod
    def _is_form_submit(cls, element: dict[str, Any]) -> bool:
        if cls._move_action(element) != "click" or cls._is_exit_control(element):
            return False
        element_type = _normalise_text(str(element.get("type") or ""))
        if element_type == "submit":
            return True
        return cls._form_group(element) is not None and bool(
            set(_meaningful_tokens(str(element.get("name") or "")))
            & {
                "avancar", "buscar", "cadastrar", "confirm", "confirmar",
                "continue", "continuar", "enviar", "next", "pesquisar",
                "proximo", "salvar", "save", "submit",
            }
        )

    def _form_transaction_moves(
        self,
        state: _CoverageState,
        remaining: list[tuple[str, dict[str, Any]]],
    ) -> list[tuple[str, dict[str, Any]]]:
        """Keep observed fields and their safe submit ahead of leaving a form."""

        field_groups = {
            self._form_group(element)
            for element in state.candidates.values()
            if self._move_action(element) in {"fill", "select"}
            and not element.get("in_navigation")
        }
        return [
            item
            for item in remaining
            if not item[1].get("in_navigation")
            and (
                self._move_action(item[1]) in {"fill", "select"}
                or self._is_form_submit(item[1])
                and self._form_group(item[1]) in field_groups
            )
        ]

    def _refresh_form_setup(self, state: _CoverageState) -> None:
        """Restore blank, previously covered fields needed by a pending submit.

        Filling a field adds coverage only once, but a route restoration may
        erase its value before the submit has been exercised. This bounded
        setup work is removed after one issued fill; business assertions own
        the verdict if the application refuses to retain the value.
        """

        pending_submits = {
            self._form_group(element)
            for _, element in self._remaining(state)
            if self._is_form_submit(element)
        }
        state.form_setup_required.intersection_update(
            move_key for move_key, element in state.candidates.items()
            if element.get("has_value") is False
            and self._form_group(element) in pending_submits
            and (state.url, str(element.get("_element_fingerprint"))) not in self.failed_form_fields
        )
        for move_key, element in state.candidates.items():
            if (
                self._move_action(element) == "fill"
                and element.get("has_value") is False
                and move_key not in state.blocked
                and self._form_group(element) in pending_submits
                and (state.url, str(element.get("_element_fingerprint"))) not in self.failed_form_fields
                and (
                    move_key in state.attempted | state.inherited
                    or (state.url, move_key) in self.global_route_moves_attempted
                )
            ):
                state.form_setup_required.add(move_key)

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
                    "selected_values": sorted(
                        str(value)
                        for value in (element.get("selected_values") or [])
                    ),
                    "aria_expanded": _normalise_state(element.get("aria_expanded")),
                    "aria_hidden": _normalise_state(element.get("aria_hidden")),
                    "aria_pressed": _normalise_state(element.get("aria_pressed")),
                    "aria_selected": _normalise_state(element.get("aria_selected")),
                    # Hit-testing and viewport/document coordinates are
                    # intentionally excluded so scrolling cannot manufacture
                    # a new application state.
                }
            )
        raw = {
            "url": canonical_url(observation.url),
            "title": _normalise_text(observation.title),
            "text": _normalise_text(observation.text)[:12000],
            "menu_open": self._menu_is_open(observation),
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
        self._observe_document_response(observation.page_state.get("navigation_response"))
        self._observe_document_response(observation.page_state.get("document_response"))
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
        if state.form_setup_required:
            # Setup is a prerequisite of a genuinely untried submission, not
            # a permanent debt owned by every historical blank-field snapshot.
            # A real submission from a fresh same-route/lane variant can settle
            # that prerequisite. It cannot approve an untested field or probe.
            pending_groups = {
                self._form_group(move)
                for move_key, move in state.candidates.items()
                if self._is_form_submit(move)
                and move_key not in state.attempted | state.inherited | state.blocked
                and (state.url, move_key) not in self.global_route_moves_attempted
                and (not move.get("_probe_target") or (
                    self._form_probe_ready(state, str(self._form_group(move)))
                    and (state.url, str(self._form_group(move)), str(move["_probe_target"]))
                    not in self.form_probes_completed
                ))
            }
            state.form_setup_required.intersection_update(
                move_key for move_key, move in state.candidates.items()
                if self._form_group(move) in pending_groups and move_key not in state.blocked
            )
        result: list[tuple[str, dict[str, Any]]] = []
        coverage_root_key = self.focus_root if self.focus_reached else None
        if coverage_root_key is None and self.path:
            coverage_root_key = self.path[0]
        root_state = self.states.get(coverage_root_key or "")
        for fingerprint, element in state.candidates.items():
            if fingerprint in state.blocked:
                continue
            if element.get("_probe_target"):
                group = self._form_group(element)
                if (not self._form_probe_ready(state, str(group))
                        or (state.url, str(group), str(element["_probe_target"])) in self.form_probes_completed):
                    continue
            setup_required = fingerprint in state.form_setup_required
            if not setup_required and (
                fingerprint in state.attempted or fingerprint in state.inherited
            ):
                continue
            if (
                self._move_action(element) == "select"
                and element.get("_option_selected") is True
                and (
                    element.get("multiple") is not True
                    or len(element.get("selected_values") or []) == 1
                )
            ):
                # Re-selecting the active value succeeds at the browser layer
                # but produces no verifiable effect, yielding a misleading
                # SKIPPED scenario instead of new coverage.
                continue
            if not setup_required and (
                state.url, fingerprint
            ) in self.global_route_moves_attempted:
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
            if (href and element.get("in_navigation") and not element.get("_cart_journey_role")
                    and canonical_url(href) in self.global_destinations_attempted):
                continue
            if not self._link_is_inside_focus(element):
                continue
            result.append((fingerprint, element))

        focus = _normalise_text(self.focus_area or "")
        transaction_keys = {
            move_key for move_key, _ in self._form_transaction_moves(state, result)
        }

        def priority(
            item: tuple[str, dict[str, Any]],
        ) -> tuple[int, int, int, int, int, float, str, int, int]:
            move_key, element = item
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
                fallback_order = element.get("_document_order", 0) if move_key in transaction_keys else 0
                top = float(element.get("document_top", fallback_order))
            except (TypeError, ValueError):
                top = 0
            # Exiting the current surface is always the last local option.
            # In particular, a focused label such as "Voltar Usuários" must
            # not outrank a non-exit control merely because it matches scope.
            action_rank = 0 if self._move_action(element) == "hover" else 1
            selected_rank = 1 if element.get("_option_selected") else 0
            transaction_rank = (
                0
                if move_key in transaction_keys
                and self._move_action(element) in {"fill", "select"}
                else 1 if move_key in transaction_keys else 2
            )
            # Distinct row/detail destinations are cheap, finite coverage.
            # Visit them before toggling a cart creates many transient states.
            detail_rank = 0 if element.get("href") and not element.get("in_navigation") else 1
            return (
                exit_rank,
                transaction_rank,
                focus_rank,
                navigation_rank,
                detail_rank,
                top,
                surface,
                action_rank,
                selected_rank,
            )

        return sorted(result, key=priority)

    def _checkout_review_exit_action(self, state: _CoverageState) -> AgentDecision | None:
        """Test a review's cancellation before confirmation clears its basket."""
        remaining = self._remaining(state)
        exits = [element for _, element in remaining
                 if element.get("_cart_journey_role") == "checkout_review_exit"]
        if not exits:
            return None
        element = exits[0]
        if any(move.get("_cart_journey_role") == "finish" for _, move in remaining):
            # The Cancel itself is an ordinary native test, not a replay or an
            # approval of Finish. Preserve this real review as a pending target
            # so safe observed form prerequisites can restore it immediately
            # afterwards, before later removals erase the selected products.
            self.restoration_target = state.key
            self.restoration_steps = 0
        return AgentDecision(
            action=self._move_action(element), target=str(element["ref"]),
            value=self._move_value(element), key=self._move_keypress(element),
            rationale="Verify checkout cancellation preserves the actual basket before order confirmation consumes its prerequisites.",
        )

    def _local_menu_opener_action(self, state: _CoverageState) -> AgentDecision | None:
        """Exercise a finite route-local opener before departing its surface.

        Ordinary field setup and negative form checks retain priority. Testing
        the actual mounted opener avoids reconstructing obsolete cart/form
        snapshots just to open their shared drawer later.
        """
        remaining = self._remaining(state)
        if any(self._move_action(move) in {"fill", "select"}
               for _, move in self._form_transaction_moves(state, remaining)):
            return None
        openers = [move for _, move in remaining
                   if self._move_action(move) == "click"
                   and self._menu_toggle_kind(move) == "open"]
        if len(openers) != 1:
            return None
        return AgentDecision(
            action="click", target=str(openers[0]["ref"]),
            rationale="Verify this route's actual untried menu opener before departing the live surface; keep revealed submenu controls as separate work.",
        )

    def _checkout_form_exit_action(
        self, state: _CoverageState, *, role: str = "checkout_form_exit",
    ) -> AgentDecision | None:
        """Test one real checkout-form cancellation per empty/populated lane.

        Required-field probes retain priority. Cancel is not another submit or
        a blanket approval of sibling controls: the still-pending form must be
        restored through actual observed navigation and synthetic setup.
        """
        if role not in {"checkout_form_exit", "cart_entry"}:
            return None
        remaining = self._remaining(state)
        exits = [move for _, move in remaining
                 if move.get("_cart_journey_role") == role]
        submits = [move for _, move in remaining if self._is_form_submit(move)
                   and not move.get("_probe_target")
                   and move.get("_cart_journey_role") == "checkout_form"]
        if len(exits) != 1 or len(submits) != 1:
            return None
        group = self._form_group(submits[0])
        fields = [(key, move) for key, move in state.candidates.items()
                  if self._move_action(move) == "fill" and self._form_group(move) == group]
        if not fields or not all(key in state.attempted | state.inherited
                                 or (state.url, key) in self.global_route_moves_attempted
                                 for key, _ in fields):
            return None
        if any(move.get("required") and (state.url, group, str(move["_element_fingerprint"]))
               not in self.form_probes_completed for _, move in fields):
            return None
        self.restoration_target = state.key
        self.restoration_steps = 0
        return AgentDecision(
            action="click", target=str(exits[0]["ref"]),
            rationale=("Verify checkout form cancellation after its required-field negatives, then restore the observed form to test its pending positive submission."
                       if role == "checkout_form_exit" else
                       "Verify the observed checkout-form cart link before the successful submission consumes this lane; resume its still-untried submission through observed navigation."),
        )

    def _populated_checkout_action(self, state: _CoverageState) -> AgentDecision | None:
        """Complete one live populated purchase before removing/resetting it.

        This never adds a product or reconstructs a historical basket. It only
        prioritizes safe controls already present in the current observation;
        failed business checks still fail and other frontiers still continue.
        """
        remaining = self._remaining(state)
        transaction = [item for item in self._form_transaction_moves(state, remaining)
                       if item[1].get("_cart_journey") == "populated"
                       and item[1].get("_cart_journey_role") == "checkout_form"
                       and not item[1].get("_probe_target")]
        candidates = transaction or sorted(
            (item for item in remaining
             if item[1].get("_cart_journey") == "populated"
             and item[1].get("_cart_journey_role") in {"cart_entry", "checkout_entry", "finish"}),
            key=lambda item: {"finish": 0, "checkout_entry": 1, "cart_entry": 2}[item[1]["_cart_journey_role"]],
        )
        if not candidates:
            return None
        _, element = candidates[0]
        return AgentDecision(
            action=self._move_action(element), target=str(element["ref"]),
            value=self._move_value(element), key=self._move_keypress(element),
            rationale="Verify one populated-cart purchase lane before reversible removals or resets erase its business prerequisites.",
        )

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
            hint = (decision.scenario_hint or {}).get("validation_probe")
            if element.get("_probe_target"):
                if not isinstance(hint, dict) or hint.get("planner_field") != element["_probe_target"]:
                    continue
            elif hint:
                continue
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

    def decision_key(
        self, observation: Observation, decision: AgentDecision
    ) -> str | None:
        """Return a ref-independent semantic identity for an issued move."""

        probe = (decision.scenario_hint or {}).get("validation_probe")
        if isinstance(probe, dict):
            return hashlib.sha256(json.dumps({
                "url": canonical_url(observation.url), "probe": probe,
                "action": decision.action,
            }, sort_keys=True).encode()).hexdigest()
        if decision.action == "fill" and decision.value == "":
            target = next((field for field in observation.interactive_elements
                           if field.get("ref") == decision.target), None)
            if target is not None:
                return self._move_key(semantic_element_fingerprint(target), "fill", "")
        selected = self._move_for_decision(
            list(self._candidate_map(observation).items()), decision
        )
        return selected[0] if selected is not None else None

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
        return not any(record.get("status") != "resolved" for record in self.restrictions) and not any(
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
        result: ActionResult | None = None,
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
        if decision.action in {"fill", "select"} and (
            decision.action, decision.value
        ) not in self._candidate_actions(element):
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
            setup=tuple(
                (str(field["_element_fingerprint"]), action, value)
                for field in self.states[parent_key].candidates.values()
                if self._is_form_submit(element)
                and self._form_group(field) == self._form_group(element)
                and (stored := self.synthetic_form_values.get((self.states[parent_key].url, str(field["_element_fingerprint"]))))
                and (action := stored[0]) and (value := stored[1])
            ),
            checkout_submit_verified=bool(
                result is not None and element.get("_cart_journey_role") == "checkout_form"
                and self._is_form_submit(element)
                and any(assertion.kind == "form_progress" and assertion.passed
                        for assertion in result.assertions)
            ),
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

    def _matches_pending_state(self, actual_key: str, expected_key: str) -> bool:
        """Accept a live route variant when it exposes every pending move."""

        if actual_key == expected_key:
            return True
        actual = self.states.get(actual_key)
        expected = self.states.get(expected_key)
        if actual is None or expected is None or actual.url != expected.url:
            return False
        pending = {move_key for move_key, _ in self._remaining(expected)}
        return bool(pending) and pending.issubset(actual.candidates)

    def _live_replay_path(
        self, current_key: str, target_key: str
    ) -> list[_TransitionRecipe] | None:
        direct = self._find_replay_path(current_key, target_key)
        if direct == []:
            return direct
        current = self.states.get(current_key)
        if current is None:
            return direct
        # A restore can change counters or other already-classified controls.
        # Resolve the first semantic move in the fresh DOM instead of forcing
        # an exact historical snapshot or using its stale element reference.
        # An exact snapshot can have a longer observed route than another
        # snapshot on this same URL. Compare their safe replay recipes instead
        # of committing to the exact state's detour first. The first semantic
        # move must still exist in the live DOM; executing the selected recipe
        # continues to verify every actual resulting state and prerequisite.
        paths = [direct] if direct else []
        for key, state in self.states.items():
            if key == current_key or state.url != current.url or not self._is_active(key):
                continue
            path = self._find_replay_path(key, target_key)
            if path and path[0].fingerprint in current.candidates:
                paths.append(path)
        return min(paths, key=len) if paths else None

    def _url_restorable(self, state_key: str) -> bool:
        state = self.states[state_key]
        if state.url in self.failed_document_urls:
            return False
        if state_key == self.focus_root and self.focus_entry_anchor is not None:
            return False
        if state_key in {self.focus_entry_anchor, self.coverage_root_key, self.focus_root}:
            # The exploration's first *authenticated* state can be a SPA route
            # reached by login, not the document URL the user supplied. Being
            # the logical root does not prove that this route supports GET.
            return state.url == self.start_url or state.url in self.verified_document_urls
        # A URL change after a click does NOT prove that the server can render
        # this SPA route with GET. Only a successful document response does.
        return state.url in self.verified_document_urls

    def _restorable_ancestor(self, target_key: str) -> str | None:
        root_key = self.focus_root or self.coverage_root_key
        if root_key is None:
            return None
        path = self._find_replay_path(root_key, target_key)
        if path is None:
            return None
        ancestors = [root_key, *(recipe.child_key for recipe in path[:-1])]
        # Prefer the scope root and replay actual application transitions.
        # Deep-link GETs lose transient form/cart state even when HTTP succeeds.
        for ancestor_key in ancestors:
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
            for fingerprint, element in remaining:
                state.attempted.add(fingerprint)
                state.blocked.add(fingerprint)
                self._restrict(state, fingerprint, element, reason=reason,
                               source_url=self.states[self.current_key].url if self.current_key in self.states else None)
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
                for _, element in remaining
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
        overlay = self._overlay_dismissal(observation)
        if overlay:
            return overlay, "obstructing_overlay_dismissed"
        feedback = self._inline_validation_dismissal(self.states[key])
        if feedback:
            return feedback, "inline_validation_feedback_dismissed"
        opener = self._local_menu_opener_action(self.states[key])
        if opener and not any(move.get("_probe_target") for _, move in self._remaining(self.states[key])):
            return opener, "route_local_menu_prioritized"
        review_exit = self._checkout_review_exit_action(self.states[key])
        if review_exit:
            return review_exit, "checkout_cancellation_prioritized"
        restoration = self._continue_restoration(key)
        if restoration:
            return restoration
        probe = self._form_probe_action(self.states[key])
        if probe:
            return probe, "isolated_required_field_probe"
        opener = self._local_menu_opener_action(self.states[key])
        if opener:
            return opener, "route_local_menu_prioritized"
        form_exit = self._checkout_form_exit_action(self.states[key])
        if form_exit:
            return form_exit, "checkout_form_cancellation_prioritized"
        cart_exit = self._checkout_form_exit_action(self.states[key], role="cart_entry")
        if cart_exit:
            return cart_exit, "checkout_form_cart_navigation_prioritized"

        state = self.states[key]
        purchase = self._populated_checkout_action(state)
        if purchase:
            return purchase, "populated_checkout_prioritized"
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
        self, recipe: _TransitionRecipe, target_key: str, *, current_key: str | None = None
    ) -> AgentDecision | None:
        state = self.states.get(current_key or recipe.parent_key)
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

    def _recipe_setup_decision(self, recipe: _TransitionRecipe, current_key: str) -> AgentDecision | None:
        current = self.states[current_key]
        for fingerprint, action, value in recipe.setup:
            field = next((element for element in current.candidates.values()
                          if element.get("_element_fingerprint") == fingerprint
                          and (action, value) in self._candidate_actions(element)), None)
            if field is None:
                continue
            needs_setup = (action == "fill" and field.get("has_value") is False
                           or action == "select" and value not in (field.get("selected_values") or []))
            if needs_setup:
                return AgentDecision(
                    action=action, target=str(field["ref"]), value=value,
                    rationale="Recreate the observed synthetic form prerequisites before replaying its safe submit.",
                )
        return None

    @staticmethod
    def _route_failure_key(
        target_key: str, source_url: str, move_key: str, destination: str,
    ) -> tuple[str, str, str]:
        return ("route", target_key, f"{source_url}:{move_key}:{destination}")

    def _route_restoration_hop(
        self, current_key: str, target_key: str,
    ) -> tuple[AgentDecision, str] | None:
        """Reach a pending route through fresh controls, not an old basket.

        Route identity is sufficient for a navigation prerequisite; it is not
        evidence that the historical business state or its checks passed. The
        normal pending-move comparison still decides whether the actual fresh
        DOM exposes the target work. Never manufacture a deep-link GET here.
        """
        current, target = self.states[current_key], self.states[target_key]
        if current.url == target.url:
            return None

        def safe_route(state: _CoverageState) -> bool:
            root = self.states.get(self.coverage_root_key or "")
            authentication_entry = (root is not None and root.url != self.start_url
                                    and state.url == self.start_url)
            return (not authentication_entry and self._is_active(state.key)
                    and urlsplit(state.url)[:2] == self.origin
                    and self._url_is_inside_focus(state.url))

        def route_move(element: dict[str, Any], destination: str, *, learned: bool = False,
                       recipe: _TransitionRecipe | None = None) -> bool:
            name = _normalise_text(str(element.get("name") or ""))
            mutating_control = bool(re.search(
                r"\b(?:reset|clear|limpar|reiniciar|add\s+to\s+cart|adicionar\s+ao\s+carrinho)\b", name,
            ))
            learned_checkout_submit = bool(
                learned and recipe is not None and recipe.checkout_submit_verified
                and recipe.setup and element.get("_cart_journey_role") == "checkout_form"
            )
            if (self._move_action(element) != "click"
                    or self._is_form_submit(element) and not learned_checkout_submit
                    or element.get("_probe_target")
                    or mutating_control or element.get("reversible_cart_action")
                    or element.get("_cart_journey_role") == "finish"
                    or not self._safe_candidate(element, self.origin)
                    or not self._link_is_inside_focus(element)):
                return False
            href = str(element.get("href") or "")
            if href:
                placeholder = ("raw_href" in element and element.get("raw_href") in {"", "#"}
                               or "raw_href" not in element and href.endswith("#"))
                if placeholder:
                    # React navigation often declares href="#" and handles
                    # the click itself. That is not a discoverable destination,
                    # but a previously executed, same-origin transition proves
                    # where this exact semantic control went. Only that learned
                    # edge can be replayed; never infer a route from its label.
                    return learned and self._clickable(element)
                # A bad card destination is a defect, not a useful recovery
                # recipe. Do not repeatedly replay it to restore other work.
                return canonical_url(href) == destination
            return self._clickable(element)

        def resolve_move(parent: _CoverageState, fingerprint: str) -> tuple[str, dict[str, Any]] | None:
            historical = parent.candidates.get(fingerprint)
            if historical is None:
                return None
            identity = semantic_element_fingerprint(historical)
            matches = [(move_key, element) for move_key, element in current.candidates.items()
                       if self._move_action(element) == "click"
                       and not element.get("_probe_target")
                       and semantic_element_fingerprint(element) == identity]
            return matches[0] if len(matches) == 1 else None

        def available(move_key: str, destination: str) -> bool:
            edge = (target_key, current_key, move_key)
            failure = self._route_failure_key(target_key, current.url, move_key, destination)
            return (self.restoration_edge_uses.get(edge, 0) < _MAX_REPLAY_FAILURES
                    and self.restoration_failures.get(failure, 0) < _MAX_REPLAY_FAILURES)

        # A real, currently mounted same-origin anchor is a stronger route
        # prerequisite than a long recipe through obsolete transient states.
        choices = [(move_key, element, target_key, None)
                   for move_key, element in current.candidates.items()
                   if element.get("href") and safe_route(target)
                   and route_move(element, target.url)
                   and available(move_key, target.url)]
        if not choices:
            # Cross-route edges can be reused across live cart/counter variants
            # only to reach their route. Same-route modal/form state continues
            # to require the existing selector-free semantic replay graph.
            queue: list[tuple[str, tuple[str, dict[str, Any], str, _TransitionRecipe | None] | None, int]] = [(current.url, None, 0)]
            visited = {current.url}
            while queue and not choices:
                source_url, first, depth = queue.pop(0)
                if depth >= _MAX_REPLAY_DEPTH:
                    continue
                for parent_key, recipes in self.transitions.items():
                    parent = self.states[parent_key]
                    if parent.url != source_url or not safe_route(parent):
                        continue
                    for recipe in recipes:
                        child = self.states[recipe.child_key]
                        historical = parent.candidates.get(recipe.fingerprint)
                        if (recipe.action != "click" or child.url == source_url
                                or not safe_route(child) or historical is None
                                or not route_move(historical, child.url, learned=True, recipe=recipe)):
                            continue
                        if first is None:
                            resolved = resolve_move(parent, recipe.fingerprint)
                            if resolved is None:
                                continue
                            move_key, element = resolved
                            if not route_move(element, child.url, learned=True, recipe=recipe) or not available(move_key, child.url):
                                continue
                            hop = (move_key, element, child.key, recipe)
                        else:
                            hop = first
                        if child.url == target.url:
                            choices.append(hop)
                            break
                        if child.url not in visited:
                            visited.add(child.url)
                            queue.append((child.url, hop, depth + 1))
                    if choices:
                        break
        if not choices:
            return None
        move_key, element, expected_key, recipe = choices[0]
        if recipe is not None and recipe.checkout_submit_verified:
            # Replay only values generated by this planner for a previously
            # successful checkout submission. Route shortcuts must recreate
            # required fields, not click Continue on a fresh empty form.
            required_fields = [field for field in current.candidates.values()
                               if self._move_action(field) == "fill" and field.get("required")
                               and self._form_group(field) == self._form_group(element)]
            setup_ids = {identity for identity, _, _ in recipe.setup}
            if any(str(field["_element_fingerprint"]) not in setup_ids for field in required_fields):
                return None
            setup = self._recipe_setup_decision(recipe, current_key)
            if setup is not None:
                self.restoration_steps += 1
                self._issued_restore_step = _IssuedRestoreStep(
                    kind="setup", target_key=target_key, source_key=current_key, expected_key=current_key,
                )
                return setup, "frontier_restore_form_setup"
        edge = (target_key, current_key, move_key)
        self.restoration_steps += 1
        self.restoration_edge_uses[edge] = self.restoration_edge_uses.get(edge, 0) + 1
        self._issued_restore_step = _IssuedRestoreStep(
            kind="route", target_key=target_key, source_key=current_key,
            expected_key=expected_key, fingerprint=move_key,
        )
        return AgentDecision(
            action="click", target=str(element["ref"]),
            rationale="Reach a pending route using a fresh safe navigation control; verify its actual live state before resuming uncovered work.",
        ), "frontier_restore_route"

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
        if self._matches_pending_state(current_key, target_key):
            if current_key != target_key:
                # A fresh route variant can expose the historical frontier
                # with some controls already exercised/inherited. Transfer
                # ownership to that live state; otherwise the old snapshot
                # remains pending and is selected again forever without a
                # browser action. New live controls stay unclassified.
                target.inherited.update(
                    move_key for move_key, _ in self._remaining(target)
                    if move_key in self.states[current_key].candidates
                )
            self.restoration_target = None
            self.restoration_steps = 0
            return None
        current = self.states.get(current_key)
        if (
            current is not None
            and current.url == target.url
            and self._remaining(current)
        ):
            # Exercise fresh live work on the pending route before recreating
            # an obsolete cart/form combination. A known historical replay
            # recipe is not more valuable than a currently mounted untried
            # control. Keep the original frontier; only actual selected moves
            # may settle its coverage after the live variant is exercised.
            self.restoration_target = None
            self.restoration_steps = 0
            return None
        if self.restoration_steps >= _MAX_RESTORE_STEPS:
            self._abandon_restoration(target_key, reason="restore_step_limit")
            return None

        live_path = self._live_replay_path(current_key, target_key)
        next_step_crosses_route = bool(
            live_path and self.states[live_path[0].child_key].url != self.states[current_key].url
        )
        has_cart_prerequisite = any(
            (move := self.states[step.parent_key].candidates.get(step.fingerprint)) is not None
            and (move.get("_observed_cart_action") in {"add", "remove"}
                 or move.get("reversible_cart_action") in {"add", "remove"})
            for step in live_path or []
        )
        route_hop = (self._route_restoration_hop(current_key, target_key)
                     if not live_path or len(live_path) > 1 and next_step_crosses_route
                     and not has_cart_prerequisite else None)
        # A same-route recipe can be a real item/form/modal prerequisite.
        # Short-cutting straight to its eventual URL skips that prerequisite
        # and loops between empty carts and already-tested navigation. Route
        # shortcuts may replace cross-route detours, never native state setup.
        # This includes a later Add/Remove after reaching a product detail:
        # jumping to Cart before that step cannot expose its pending SKU.
        if route_hop:
            return route_hop

        # Prefer continuing from the state that is already open. This avoids a
        # reload when, for example, the parent dialog has just been recreated.
        for _ in range(_MAX_REPLAY_FAILURES + 1):
            path = self._live_replay_path(current_key, target_key)
            if not path:
                break
            recipe = path[0]
            decision = self._replay_decision(recipe, target_key, current_key=current_key)
            failure_key = ("replay", target_key, self._recipe_id(recipe))
            edge_key = (target_key, current_key, recipe.fingerprint)
            if self.restoration_edge_uses.get(edge_key, 0) >= _MAX_REPLAY_FAILURES:
                # Successful clicks can still be a cycle (open/close menu,
                # cart/history) with no target reached. Count these too; a
                # recipe's immediate DOM success must not reset overall debt.
                self.restoration_failures[failure_key] = _MAX_REPLAY_FAILURES
                continue
            if decision is not None:
                setup = self._recipe_setup_decision(recipe, current_key)
                if setup is not None:
                    self.restoration_steps += 1
                    self._issued_restore_step = _IssuedRestoreStep(
                        kind="setup", target_key=target_key, source_key=current_key,
                        expected_key=current_key,
                    )
                    return setup, "frontier_restore_form_setup"
                self.restoration_steps += 1
                self.restoration_edge_uses[edge_key] = self.restoration_edge_uses.get(edge_key, 0) + 1
                self._issued_restore_step = _IssuedRestoreStep(
                    kind="replay",
                    target_key=target_key,
                    source_key=current_key,
                    expected_key=recipe.child_key,
                    fingerprint=recipe.fingerprint,
                    recipe_parent_key=recipe.parent_key,
                )
                return decision, "frontier_restore_replay"
            self.restoration_failures[failure_key] = _MAX_REPLAY_FAILURES

        # A shared SPA drawer can remain open after entering a different
        # route. The candidate map deliberately suppresses its opener while
        # open; that does not make this route's untested opener unreachable.
        # A freshly observed, unique harmless menu close is local setup, not
        # approval of the opener or another route's business prerequisites.
        if current is not None and current.url == target.url and any(
            move_key not in current.candidates and self._menu_toggle_kind(move) == "open"
            for move_key, move in self._remaining(target)
        ):
            closes = [(move_key, move) for move_key, move in current.candidates.items()
                      if self._move_action(move) == "click"
                      and self._menu_toggle_kind(move) == "close"
                      and move.get("interactable") is True
                      and self._safe_candidate(move, self.origin)
                      and self._link_is_inside_focus(move)]
            if len(closes) == 1:
                move_key, move = closes[0]
                edge_key = (target_key, current_key, move_key)
                if self.restoration_edge_uses.get(edge_key, 0) < _MAX_REPLAY_FAILURES:
                    self.restoration_steps += 1
                    self.restoration_edge_uses[edge_key] = self.restoration_edge_uses.get(edge_key, 0) + 1
                    self._issued_restore_step = _IssuedRestoreStep(
                        kind="replay", target_key=target_key, source_key=current_key,
                        expected_key=target_key, fingerprint=move_key,
                    )
                    return AgentDecision(
                        action="click", target=str(move["ref"]),
                        rationale="Close the observed shared drawer to expose this route's still-untested opener; verify the fresh state before resuming coverage.",
                    ), "frontier_restore_menu_setup"

        ancestor_key = self._restorable_ancestor(target_key)
        if ancestor_key is None:
            # Preserve the old direct-URL recovery for route states that were
            # not entered by a replayable DOM interaction. A same-URL state is
            # never treated as URL-restorable without a semantic recipe.
            if target.url != self.states[current_key].url and self._url_restorable(target_key):
                ancestor_key = target_key
            else:
                self._abandon_restoration(target_key, reason="no_safe_restore_route")
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
        # Backtracking exists to reach pending work, not to physically unwind
        # already-covered route history. Replaying a covered predecessor can
        # start a new successful review/cart/detail cycle with fresh restore
        # budgets even though none of its controls adds coverage. Keep all
        # states and transition evidence, but skip these empty logical parents.
        while (len(self.path) > 1 and self._is_active(self.current_key or self.path[-1])
               and not self._remaining(self.states[self.path[-2]])):
            self.path.pop(-2)
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
                self._abandon_restoration(previous.key, reason="backtrack_attempt_limit")
                self.path.pop(-2)
                return self._backtrack_decision()
            reverse_path = self._live_replay_path(self.current_key or current, previous.key)
            if reverse_path:
                self.restoration_target = previous.key
                self.restoration_steps = 0
                replay = self._continue_restoration(self.current_key or current)
                if replay:
                    return replay
            if current_state.url == previous.url:
                if not self._url_restorable(previous.key):
                    self.restoration_target = previous.key
                    restored = self._continue_restoration(self.current_key or current)
                    if restored:
                        return restored
                    # Only the unrecoverable predecessor is settled; siblings
                    # elsewhere in the graph remain eligible.
                    self.path.pop(-2)
                    return self._backtrack_decision()
                self._issued_restore_step = _IssuedRestoreStep(
                    kind="backtrack",
                    target_key=current,
                    source_key=self.current_key or current,
                    expected_key=previous.key,
                )
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
                self._issued_restore_step = _IssuedRestoreStep(
                    kind="backtrack",
                    target_key=current,
                    source_key=self.current_key or current,
                    expected_key=previous.key,
                )
                return (
                    AgentDecision(
                        action="go_back",
                        rationale="Coverage of this state is exhausted; return to the parent state and resume its untried alternatives.",
                    ),
                    None,
                )
            if not self._url_restorable(previous.key):
                self.restoration_target = previous.key
                restored = self._continue_restoration(self.current_key or current)
                if restored:
                    return restored
                self.path.pop(-2)
                return self._backtrack_decision()
            self._issued_restore_step = _IssuedRestoreStep(
                kind="backtrack",
                target_key=current,
                source_key=self.current_key or current,
                expected_key=previous.key,
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
        feedback = self._inline_validation_dismissal(state)
        if feedback:
            return feedback, "inline_validation_feedback_dismissed"
        opener = self._local_menu_opener_action(state)
        if opener and not any(move.get("_probe_target") for _, move in self._remaining(state)):
            return opener, "route_local_menu_prioritized"
        review_exit = self._checkout_review_exit_action(state)
        if review_exit:
            return review_exit, "checkout_cancellation_prioritized"
        restoration = self._continue_restoration(key)
        if restoration:
            return restoration
        overlay = self._overlay_dismissal(observation)
        if overlay:
            return overlay, "obstructing_overlay_dismissed"
        probe = self._form_probe_action(state)
        if probe:
            return probe, "isolated_required_field_probe"
        opener = self._local_menu_opener_action(state)
        if opener:
            return opener, "route_local_menu_prioritized"
        form_exit = self._checkout_form_exit_action(state)
        if form_exit:
            return form_exit, "checkout_form_cancellation_prioritized"
        cart_exit = self._checkout_form_exit_action(state, role="cart_entry")
        if cart_exit:
            return cart_exit, "checkout_form_cart_navigation_prioritized"
        remaining = self._remaining(state)
        purchase = self._populated_checkout_action(state)
        if purchase:
            return purchase, "populated_checkout_prioritized"
        if decision.action == "navigate" and decision.url:
            destination = canonical_url(decision.url)
            known_targets = [known for known in self.states.values() if known.url == destination]
            unsafe_get = destination in self.failed_document_urls or bool(
                known_targets and not any(self._url_restorable(known.key) for known in known_targets)
            )
            if unsafe_get:
                for target in known_targets:
                    if self._remaining(target):
                        self.restoration_target = target.key
                        restored = self._continue_restoration(key)
                        if restored:
                            return restored
                forced = self._forced_action(state, "Use a safe observed interaction instead of a known unsupported deep-link GET.")
                if forced:
                    return forced, "unsupported_document_navigation_replaced"
                backtrack = self._backtrack_decision()
                if backtrack:
                    return backtrack[0], "unsupported_document_navigation_replaced_by_recovery"
                return AgentDecision("finish", "The requested deep-link GET is unsupported; all remaining blocked controls are explicit restrictions."), "unsupported_document_navigation_replaced_by_finish"

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
                transaction = self._form_transaction_moves(state, remaining)
                pending_fields = [
                    item for item in transaction
                    if self._move_action(item[1]) in {"fill", "select"}
                ]
                leaving_form = bool(
                    element.get("href")
                    or element.get("in_navigation")
                    or self._is_exit_control(element)
                )
                if (
                    pending_fields and self._is_form_submit(element)
                    or transaction and leaving_form
                ):
                    _, next_element = (pending_fields or transaction)[0]
                    if str(next_element.get("ref")) != str(decision.target):
                        return (
                            AgentDecision(
                                action=self._move_action(next_element),
                                target=str(next_element["ref"]),
                                value=self._move_value(next_element),
                                key=self._move_keypress(next_element),
                                rationale=(
                                    "Complete the current form's safe fields and submit "
                                    "before leaving through header or navigation controls."
                                ),
                            ),
                            "premature_form_navigation_replaced",
                        )
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
        self._observe_document_response(result.evidence.get("navigation_response"))
        issued_restore = self._issued_restore_step
        selected_move = self._move_for_decision(
            list(before_state.candidates.items()), decision
        )
        if selected_move is None and decision.action == "fill" and decision.value == "":
            selected_move = next((item for item in before_state.candidates.items()
                                  if item[1].get("ref") == decision.target
                                  and self._move_action(item[1]) == "fill"
                                  and item[1].get("required") is True), None)
        transition_fingerprint: str | None = None
        entered_focus_by_target = False
        if selected_move is not None:
            fingerprint, element = selected_move
            before_state.form_setup_required.discard(fingerprint)
            transition_fingerprint = fingerprint
            if result.success and decision.action in {"fill", "select"}:
                field_key = (before_state.url, str(element["_element_fingerprint"]))
                if (decision.action, decision.value) in self._candidate_actions(element):
                    self.synthetic_form_values[field_key] = (decision.action, str(decision.value))
                elif decision.value == "":
                    self.synthetic_form_values.pop(field_key, None)
            if result.success and self._is_form_submit(element):
                group = self._form_group(element)
                if element.get("_probe_target"):
                    self.form_probes_completed.add((before_state.url, str(group), str(element["_probe_target"])))
                    if any(assertion.kind == "form_validation_probe_precondition" and not assertion.passed
                           for assertion in result.assertions):
                        before_state.blocked.add(fingerprint)
                        probe_move = {**element, "_validation_probe":
                                      dict((decision.scenario_hint or {}).get("validation_probe") or {})}
                        self._restrict(before_state, fingerprint, probe_move,
                                       reason="required_field_validation_masked")
                else:
                    self.form_happy_paths_completed.add((before_state.url, str(group)))
            policy_blocked = "policyblocked" in result.message.lower()
            if result.success:
                before_state.attempted.add(fingerprint)
                before_state.failures.pop(fingerprint, None)
            elif policy_blocked:
                before_state.attempted.add(fingerprint)
                before_state.blocked.add(fingerprint)
                self._restrict(before_state, fingerprint, element, reason="policy_blocked")
                if element.get("_probe_target"):
                    self.form_probes_completed.add((before_state.url, str(self._form_group(element)), str(element["_probe_target"])))
            else:
                failures = before_state.failures.get(fingerprint, 0) + 1
                before_state.failures[fingerprint] = failures
                # One retry handles a transient rerender/stale locator. A
                # second failure classifies the candidate as blocked so a
                # permanently unavailable control cannot make the run infinite.
                if failures >= 2:
                    before_state.attempted.add(fingerprint)
                    before_state.blocked.add(fingerprint)
                    self._restrict(before_state, fingerprint, element, reason="action_retry_exhausted")
                    if element.get("_probe_target"):
                        self.form_probes_completed.add((before_state.url, str(self._form_group(element)), str(element["_probe_target"])))
                    elif decision.action == "fill":
                        self.failed_form_fields.add((before_state.url, str(element["_element_fingerprint"])))
            classified = fingerprint in before_state.attempted
            if result.success or policy_blocked:
                self.global_route_moves_attempted.add((before_state.url, fingerprint))
                self._resolve_executed_restrictions(before_state, fingerprint, decision, result)
                self._record_cart_toggle_prerequisite(before_state, element, decision, result, after)
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
                    if (ancestor is None
                            or canonical_url(ancestor.url) != canonical_url(before_state.url)
                            or fingerprint not in ancestor.candidates):
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
            if classified and href and decision.action == "click" and result.success:
                declared = canonical_url(href)
                before_route = urlsplit(before.url)._replace(fragment="")
                declared_route = urlsplit(declared)._replace(fragment="")
                # Product cards and SPA handlers often share href="#" while
                # their distinct click handlers open different entities. A
                # declared URL is deduplication evidence only when navigation
                # actually followed it, not merely because a click ran.
                if (declared_route != before_route
                        and declared == canonical_url(after.url)
                        and element.get("in_navigation")):
                    self.global_destinations_attempted.add(declared)
        if decision.action in _PASSIVE_ACTIONS:
            before_state.passive_actions.add(decision.action)

        after_key_hint = self.state_key(after)
        child_is_new = after_key_hint not in self.states
        after_key = self.observe(after)
        if selected_move is not None and decision.action == "fill" and decision.value:
            _, filled_field = selected_move
            identity = str(filled_field["_element_fingerprint"])
            observed_field = next((field for field in self.states[after_key].candidates.values()
                                   if field.get("_element_fingerprint") == identity), None)
            if result.success and observed_field is not None and observed_field.get("has_value") is False:
                # Native fill success cannot establish a validation
                # prerequisite if the application immediately clears it.
                self.failed_form_fields.add((before_state.url, identity))
        # A restored route may reveal a usable form in a different cart/menu
        # variant. Never mark all its controls covered merely because this was
        # recovery work. Only actually classified semantic moves are inherited.
        if (result.success and after_key != before_key and child_is_new
                and canonical_url(after.url) == canonical_url(before.url)):
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
            and not (decision.scenario_hint or {}).get("validation_probe")
            and not any(not assertion.passed and assertion.kind in {
                "product_detail_identity", "url_equals", "url_contains", "navigation_destination",
                "form_progress",
            } for assertion in result.assertions)
        ):
            self._register_transition(
                before_key,
                after_key,
                transition_fingerprint,
                decision,
                is_focus_entry=is_focus_entry,
                result=result,
            )

        if issued_restore is not None:
            backtrack_restored = False
            if issued_restore.source_key == before_key:
                if issued_restore.kind == "replay" and issued_restore.fingerprint:
                    recipe = _TransitionRecipe(
                        parent_key=issued_restore.recipe_parent_key or before_key,
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
                    if result.success and self._matches_pending_state(after_key, issued_restore.expected_key):
                        self.restoration_failures.pop(failure_key, None)
                    else:
                        self.restoration_failures[failure_key] = (
                            self.restoration_failures.get(failure_key, 0) + 1
                        )
                elif issued_restore.kind == "route" and issued_restore.fingerprint:
                    expected_url = self.states[issued_restore.expected_key].url
                    failure_key = self._route_failure_key(
                        issued_restore.target_key, before_state.url,
                        issued_restore.fingerprint, expected_url,
                    )
                    # A navigation prerequisite succeeded when its route was
                    # actually reached, even if a cart/menu variant differs.
                    # No pending move or business assertion is approved here.
                    if result.success and self.states[after_key].url == expected_url:
                        self.restoration_failures.pop(failure_key, None)
                    else:
                        self.restoration_failures[failure_key] = self.restoration_failures.get(failure_key, 0) + 1
                elif issued_restore.kind == "navigate":
                    failure_key = self._navigate_failure_key(
                        issued_restore.target_key,
                        issued_restore.expected_key,
                    )
                    restored = result.success and (
                        self._matches_pending_state(after_key, issued_restore.expected_key)
                        or self._live_replay_path(after_key, issued_restore.target_key)
                        is not None
                    )
                    if restored:
                        self.restoration_failures.pop(failure_key, None)
                    else:
                        self.restoration_failures[failure_key] = (
                            self.restoration_failures.get(failure_key, 0) + 1
                        )
                elif issued_restore.kind == "backtrack":
                    backtrack_restored = (
                        result.success and self._matches_pending_state(after_key, issued_restore.expected_key)
                    )
                    if backtrack_restored:
                        self.backtrack_failures.pop(issued_restore.target_key, None)
                    else:
                        self.backtrack_failures[issued_restore.target_key] = (
                            self.backtrack_failures.get(
                                issued_restore.target_key, 0
                            )
                            + 1
                        )
                if result.success and self._matches_pending_state(after_key, issued_restore.target_key):
                    # Settle the historical frontier using the fresh DOM. Keep
                    # any genuinely new controls eligible in the live variant.
                    expected_state = self.states[issued_restore.target_key]
                    if after_key != issued_restore.target_key:
                        expected_state.inherited.update(
                            move_key for move_key, _ in self._remaining(expected_state)
                            if move_key in self.states[after_key].candidates
                        )
                    self.restoration_target = None
                    self.restoration_steps = 0
                    self.restoration_edge_uses = {
                        key: uses for key, uses in self.restoration_edge_uses.items()
                        if key[0] != issued_restore.target_key
                    }
                if result.success:
                    self._refresh_form_setup(self.states[after_key])
            self._issued_restore_step = None
        else:
            backtrack_restored = False

        issued_backtrack = issued_restore is not None and issued_restore.kind == "backtrack"
        if not issued_backtrack and decision.action in {"go_back", "navigate"} and (
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
        elif not issued_backtrack and result.success and after_key != before_key:
            self.backtrack_failures.pop(before_key, None)

        if issued_backtrack:
            if backtrack_restored and issued_restore is not None:
                expected = issued_restore.expected_key
                if expected in self.path:
                    self.path = self.path[: self.path.index(expected) + 1]
        elif after_key != before_key and result.success:
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
            "unreachable_restorations": list(self.unreachable_restorations),
            "restrictions": [dict(record) for record in self.restrictions
                             if record.get("status") != "resolved"],
            "restriction_count": sum(record.get("status") != "resolved" for record in self.restrictions),
            "resolved_restrictions": [dict(record) for record in self.restrictions
                                      if record.get("status") == "resolved"],
            "resolved_restriction_count": sum(record.get("status") == "resolved" for record in self.restrictions),
            "coverage_complete": self.coverage_complete(),
            "coverage_basis": "observed_semantic_frontier_not_all_possible_scenarios",
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
                    "validation_probe": element.get("_validation_probe"),
                }
                for move_key, element in remaining[:_MAX_EXPOSED_MOVES]
            ],
            "completion_rule": (
                "finish is valid only when current_untried is empty, all known frontier states "
                "are exhausted. Blocked moves are explicit restrictions, never proof of complete application coverage"
            ),
        }
