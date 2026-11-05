"""Private, fail-closed recipes for rerunning one already observed action.

Recipes contain semantic hashes, not DOM snapshots, old browser refs or field
contents. They are captured *before* dispatch, and can only be resolved against
an equivalent fresh observation. A recipe is not a navigation/setup plan.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from autojev.domain.models import AgentDecision, Observation
from autojev.services.coverage import CoveragePlanner, semantic_element_fingerprint

RECIPE_VERSION = "ct-rerun-v1"
_ACTIONS = frozenset({"click", "select", "fill", "hover", "press", "navigate", "reload", "go_back", "wait", "screenshot"})
_TARGET_ACTIONS = frozenset({"click", "select", "fill", "hover", "press"})
_PRESS_KEYS = frozenset({
    "arrowdown", "arrowleft", "arrowright", "arrowup", "enter", "escape", "space", "tab",
})
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_REF = re.compile(r"e\d+\Z")
_SENSITIVE = re.compile(
    r"password|passwd|senha|credential|authorization|authentication|username|user[._ -]?name|"
    r"login|session|secret|api[._ -]?key|token|(?:^|\W)(?:otp|one[._ -]?time)(?:$|\W)",
    re.I,
)
_SECRET_VALUE = re.compile(
    r"\[REDACTED\]|\[FIELD VALUE\]|(?:bearer\s+|sk-[a-z0-9]|gh[pousr]_|"
    r"eyJ[a-zA-Z0-9_-]{8,}\.)(?:\S*)",
    re.I,
)
_PUBLIC_SYNTHETIC_VALUES = frozenset({
    "", "AutoJev QA exploratory input", "AutoJev QA", "autojev.qa@example.com",
    "11999999999", "1", "https://example.com", "01001000",
})
# These are transport/executor history, not observable application state. In
# particular, cart_expectation is an accumulated *intent ledger*, whereas cart,
# products, checkout and form tokens below describe the current application.
_NON_SEMANTIC_STATE_KEYS = frozenset({
    "navigation_response", "document_response", "cart_expectation",
})


class RerunRecipeBlocked(ValueError):
    """A persisted action cannot be dispatched safely in the current state."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _credential_mapping(credentials: Mapping[str, str] | None) -> dict[str, str]:
    result = dict(credentials or {})
    if any(not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,99}", key)
           or not isinstance(value, str) or not value for key, value in result.items()):
        raise RerunRecipeBlocked("invalid_credentials")
    if any(value in key for key in result for value in result.values()):
        raise RerunRecipeBlocked("unsafe_input_reference")
    return result


def _input_key(decision: AgentDecision, credentials: Mapping[str, str]) -> str | None:
    if decision.credential_key:
        if decision.action != "fill":
            raise RerunRecipeBlocked("credential_action")
        if decision.credential_key not in credentials:
            raise RerunRecipeBlocked("missing_recipe_input")
        if decision.value is not None and decision.value != credentials[decision.credential_key]:
            raise RerunRecipeBlocked("conflicting_recipe_input")
        return decision.credential_key
    if decision.action == "fill" and decision.value is not None:
        matches = sorted(key for key, value in credentials.items() if value == decision.value)
        return matches[0] if matches else None
    return None


def _check_observed_secrets(before: Observation, secrets: Iterable[str]) -> None:
    # Hashing visible secret text would create a dictionary-attack oracle.
    observed = json.dumps({"url": before.url, "title": before.title, "text": before.text,
                           "elements": before.interactive_elements, "state": before.page_state},
                          ensure_ascii=False)
    if any(secret and secret in observed for secret in secrets):
        raise RerunRecipeBlocked("private_observation")


def required_recipe_inputs(recipe: dict[str, Any]) -> list[str]:
    """Return sorted vault aliases, never values; validate enabled recipes first."""
    if isinstance(recipe, dict) and recipe.get("enabled") is False:
        return []
    validate_rerun_recipe(recipe, recipe.get("origin", ""))
    key = recipe["decision"].get("input_ref")
    return sorted(set(([key] if key else []) + list(recipe.get("state_inputs", {}).values())))


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _origin(url: str) -> str:
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError
        if parsed.username is not None or parsed.password is not None:
            raise ValueError
        port = parsed.port
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        default = 443 if parsed.scheme.lower() == "https" else 80
        suffix = f":{port}" if port is not None and port != default else ""
        return f"{parsed.scheme.lower()}://{host}{suffix}"
    except (TypeError, ValueError) as exc:
        raise RerunRecipeBlocked("invalid_source_url") from exc


def _public_literal(value: Any, secrets: Iterable[str], *, limit: int = 300) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise RerunRecipeBlocked("unsafe_replay_value")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise RerunRecipeBlocked("unsafe_replay_value")
    if (_SECRET_VALUE.search(value) or _SENSITIVE.search(value)
            or any(secret and secret in value for secret in secrets)):
        raise RerunRecipeBlocked("sensitive_replay_value")
    return value


def _public_url(value: Any, secrets: Iterable[str]) -> str:
    url = _public_literal(value, secrets, limit=2048)
    # Executor public URLs encode redaction markers/query values again. A
    # redacted source is not a durable route to replay, even if its marker is
    # percent-encoded and no longer spells [REDACTED] in the literal URL.
    _public_literal(unquote(url), secrets, limit=2048)
    _origin(url)
    parsed = urlsplit(url)
    for name, _ in parse_qsl(parsed.query, keep_blank_values=True):
        if _SENSITIVE.search(name):
            raise RerunRecipeBlocked("sensitive_source_url")
    if _SENSITIVE.search(parsed.fragment):
        raise RerunRecipeBlocked("sensitive_source_url")
    return url


def _is_sensitive_target(element: dict[str, Any], before: Observation) -> bool:
    if element.get("sensitive") or str(element.get("type", "")).casefold() == "password":
        return True
    properties = " ".join(str(element.get(key) or "") for key in (
        "name", "type", "semantic_identity", "id", "autocomplete", "form",
    ))
    properties = unicodedata.normalize("NFKD", properties)
    if _SENSITIVE.search(properties):
        return True
    return any(
        isinstance(field, dict) and field.get("ref") == element.get("ref")
        and (field.get("sensitive") or str(field.get("type", "")).casefold() == "password")
        for field in before.page_state.get("fields", [])
    )


def _by_ref(before: Observation, ref: Any) -> dict[str, Any]:
    if not isinstance(ref, str) or not _REF.fullmatch(ref):
        raise RerunRecipeBlocked("unsupported_target")
    matches = [element for element in before.interactive_elements
               if isinstance(element, dict) and element.get("ref") == ref]
    if len(matches) != 1:
        raise RerunRecipeBlocked("missing_target" if not matches else "ambiguous_target")
    return matches[0]


def _by_fingerprint(before: Observation, fingerprint: Any) -> dict[str, Any]:
    if not isinstance(fingerprint, str) or not _HASH.fullmatch(fingerprint):
        raise RerunRecipeBlocked("invalid_target_fingerprint")
    matches = [element for element in before.interactive_elements
               if isinstance(element, dict)
               and semantic_element_fingerprint(element) == fingerprint]
    if len(matches) != 1:
        raise RerunRecipeBlocked("missing_target" if not matches else "ambiguous_target")
    _by_ref(before, matches[0].get("ref"))
    return matches[0]


def _portable_field_value(field: dict[str, Any], refs: dict[str, dict[str, Any]]) -> Any:
    """Prove a portable field state without serializing private contents.

    The executor uses a per-session HMAC key. Only explicitly public synthetic
    values or confirmed empty controls can be compared across fresh sessions.
    We never guess a nonempty value from has_value=True or copy it into recipes.
    """
    element = refs.get(str(field.get("ref") or ""))
    if field.get("has_value") is False:
        return {"public_value_digest": _digest("")}
    if field.get("sensitive"):
        raise RerunRecipeBlocked("unportable_field_state")
    if element is not None and str(element.get("tag", "")).casefold() == "select":
        values = element.get("selected_values")
        field_values = field.get("selected_values")
        options = element.get("options")
        index = element.get("selected_index")
        masked_values = None
        if isinstance(values, list) and values and all(isinstance(value, str) for value in values):
            # _sanitize_page_state deliberately masks non-synthetic node.value
            # in the field's selected_values too; the independently observed
            # select/options still declare exactly which public option is on.
            selected = values[0]
            masked_values = [
                value.replace(selected, "[FIELD VALUE]") if len(selected) >= 3
                else "[FIELD VALUE]" if value == selected else value
                for value in values
            ]
        if (not isinstance(values, list) or not values
                or not all(isinstance(value, str) for value in values)
                or field_values not in (values, masked_values)
                or str(field.get("type", "")).casefold() not in {"select-one", "select-multiple"}
                or not isinstance(options, list)
                or not all(isinstance(option, dict) for option in options)
                or not isinstance(index, int) or isinstance(index, bool)
                or not 0 <= index < len(options)
                or options[index].get("value") not in values
                or [option.get("value") for option in options if option.get("selected") is True] != values):
            raise RerunRecipeBlocked("unverifiable_selection_state")
        for value in values:
            _public_literal(value, (), limit=120)
        return {"public_selection_digest": _digest(values)}
    value = field.get("value")
    public_values = set(_PUBLIC_SYNTHETIC_VALUES)
    if element is not None:
        public_values.update(value for action, value in CoveragePlanner._candidate_actions(element)
                             if action == "fill" and isinstance(value, str))
    if isinstance(value, str) and (value in public_values or re.fullmatch(r"AutoJev QA [a-z ]{1,48}", value)):
        return {"public_value_digest": _digest(value)}
    if field.get("value_token"):
        # Per-executor HMAC tokens cannot be verified by a new browser. Do not
        # offer an enabled recipe that is guaranteed to fail in that session.
        raise RerunRecipeBlocked("unportable_field_state")
    # Unknown legacy fields must not look equal just because both are nonempty.
    if field.get("has_value"):
        raise RerunRecipeBlocked("unverifiable_field_state")
    return None


def recipe_state_digest(
    before: Observation, *, state_inputs: Mapping[str, str] | None = None,
    credentials: Mapping[str, str] | None = None,
) -> str:
    """Hash complete sanitized semantic state with ref-independent bindings."""
    refs = {str(element["ref"]): element for element in before.interactive_elements
            if isinstance(element, dict) and element.get("ref")}
    identities = {ref: semantic_element_fingerprint(element) for ref, element in refs.items()}

    def normalize(value: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            is_field = "has_value" in value and ("value_token" in value or "type" in value)
            normalized = {
                name: normalize(item, name) for name, item in value.items()
                if not name.startswith("_")
                and name not in _NON_SEMANTIC_STATE_KEYS
                and not (is_field and name in {"value", "value_token"})
            }
            if is_field:
                fingerprint = identities.get(str(value.get("ref")))
                alias = (state_inputs or {}).get(fingerprint)
                if alias is not None:
                    attestations = getattr(before.page_state, "rerun_field_bindings",
                                             before.page_state.get("_rerun_field_bindings", {}))
                    if (attestations.get(str(value.get("ref"))) != alias
                            or value.get("has_value") is not True):
                        raise RerunRecipeBlocked("unverified_recipe_input_state")
                    # Executor masks alias fills as sensitive on replay even
                    # when the original literal fill was an ordinary control.
                    normalized.pop("sensitive", None)
                    normalized["field_value_state"] = {"input_ref": alias}
                else:
                    normalized["field_value_state"] = _portable_field_value(value, refs)
            return normalized
        if isinstance(value, list):
            return [normalize(item, key) for item in value]
        if isinstance(value, str) and (key == "ref" or key.endswith("_refs")):
            if value not in identities:
                raise RerunRecipeBlocked("unbound_state_reference")
            return {"element_fingerprint": identities[value]}
        return value

    controls = []
    for element in before.interactive_elements:
        if not isinstance(element, dict):
            continue
        controls.append({
            "fingerprint": semantic_element_fingerprint(element),
            **{key: element.get(key) for key in (
                "enabled", "read_only", "required", "has_value", "checked",
                "selected_index", "selected_values", "options",
            )},
        })
    return _digest({
        "page_state": normalize(before.page_state),
        "controls": sorted(controls, key=lambda item: json.dumps(item, sort_keys=True)),
    })


def recipe_effect_digest(before: Observation) -> str:
    """Use the same observable effect identity as exploration coverage."""
    return CoveragePlanner(before.url).effect_key(before)


def _decision_payload(
    before: Observation, decision: AgentDecision, origin: str, secrets: Iterable[str],
    credentials: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], str | None]:
    credentials = credentials or {}
    input_key = _input_key(decision, credentials)
    if (input_key is None and decision.action == "fill" and isinstance(decision.value, str)):
        element = _by_ref(before, decision.target)
        public = False
        if not _is_sensitive_target(element, before):
            try:
                literal = _public_literal(decision.value, secrets, limit=120)
                public = literal == "" or ("fill", literal) in CoveragePlanner._candidate_actions(element)
            except RerunRecipeBlocked:
                pass
        if not public:
            input_key = "replay_" + semantic_element_fingerprint(element)
    if before.page_state.get("authentication_challenge") is True and input_key is None:
        raise RerunRecipeBlocked("authentication_required")
    probe = (decision.scenario_hint or {}).get("validation_probe")
    probe_contract = None
    if probe:
        if not isinstance(probe, dict) or probe.get("kind") != "required_field" or decision.action != "click":
            raise RerunRecipeBlocked("unsupported_validation_probe")
        fields = [field for field in before.page_state.get("fields", [])
                  if field.get("identity") == probe.get("field_identity")]
        if len(fields) != 1 or fields[0].get("has_value") is not False or fields[0].get("sensitive"):
            raise RerunRecipeBlocked("unsupported_validation_probe")
        field = _by_ref(before, fields[0].get("ref"))
        if _is_sensitive_target(field, before):
            raise RerunRecipeBlocked("sensitive_target")
        fingerprint = semantic_element_fingerprint(field)
        _by_fingerprint(before, fingerprint)
        probe_contract = {"kind": "required_field", "target_fingerprint": fingerprint}
    if decision.action not in _ACTIONS:
        raise RerunRecipeBlocked("unsupported_action")
    payload: dict[str, Any] = {"action": decision.action, "assertions": []}
    if probe_contract:
        payload["validation_probe"] = probe_contract
    fingerprint = None
    if decision.action in _TARGET_ACTIONS:
        element = _by_ref(before, decision.target)
        fingerprint = semantic_element_fingerprint(element)
        _by_fingerprint(before, fingerprint)  # Reject ambiguous captures now.
        if input_key is not None:
            # Only fill implements vault substitution in the executor. Keep this
            # policy local: password fields must not enter the coverage frontier.
            if (element.get("enabled") is False or element.get("read_only")
                    or element.get("href")
                    or not (str(element.get("tag", "")).casefold() == "textarea"
                            or (str(element.get("tag", "")).casefold() == "input"
                                and str(element.get("type") or "text").casefold() in {
                                    "text", "password", "email", "tel", "search", "url", "number"}))):
                raise RerunRecipeBlocked("unsafe_target")
            payload["input_ref"] = input_key
        elif _is_sensitive_target(element, before):
            raise RerunRecipeBlocked("sensitive_target")
        elif not CoveragePlanner._safe_candidate(element, urlsplit(before.url)[:2],
                                                 require_interactable=False):
            raise RerunRecipeBlocked("unsafe_target")
        if input_key is not None:
            pass
        elif decision.action in {"fill", "select"}:
            value = _public_literal(decision.value, secrets, limit=120)
            if not (decision.action == "fill" and value == "") and (decision.action, value) not in CoveragePlanner._candidate_actions(element):
                raise RerunRecipeBlocked("non_deterministic_fill" if decision.action == "fill"
                                         else "unsupported_selection")
            payload["value"] = value
        elif decision.value is not None:
            raise RerunRecipeBlocked("unexpected_action_value")
        if decision.action == "press" or (decision.action == "fill" and decision.key is not None):
            key = _public_literal(decision.key or "Enter", secrets, limit=20)
            if key.casefold() not in _PRESS_KEYS:
                raise RerunRecipeBlocked("unsupported_key")
            payload["key"] = key
        elif decision.key is not None:
            raise RerunRecipeBlocked("unexpected_action_key")
        if decision.url is not None:
            raise RerunRecipeBlocked("unexpected_action_url")
    else:
        if (decision.target is not None or decision.key is not None
                or (decision.value is not None and decision.action != "wait")):
            raise RerunRecipeBlocked("unexpected_action_target")
        if decision.action == "wait":
            value = decision.value if decision.value is not None else "500"
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,4}", value) or int(value) > 5000:
                raise RerunRecipeBlocked("unsupported_wait")
            payload["value"] = value
        if decision.action == "navigate":
            url = _public_url(decision.url, secrets)
            if _origin(url) != origin:
                raise RerunRecipeBlocked("cross_origin_destination")
            payload["url"] = url
        elif decision.url is not None:
            raise RerunRecipeBlocked("unexpected_action_url")
    if not isinstance(decision.assertions, list) or len(decision.assertions) > 30:
        raise RerunRecipeBlocked("unsupported_assertion")
    for assertion in decision.assertions:
        if not isinstance(assertion, dict) or set(assertion) != {"kind", "expected"}:
            raise RerunRecipeBlocked("unsupported_assertion")
        kind = assertion["kind"]
        if kind == "element_visible":
            expected = _by_ref(before, assertion["expected"])
            expected_fingerprint = semantic_element_fingerprint(expected)
            _by_fingerprint(before, expected_fingerprint)
            payload["assertions"].append({"kind": kind, "target_fingerprint": expected_fingerprint})
        elif kind in {"url_equals", "url_contains", "title_contains", "text_contains"}:
            expected_literal = _public_literal(assertion["expected"], secrets)
            if _SENSITIVE.search(expected_literal):
                raise RerunRecipeBlocked("sensitive_assertion")
            payload["assertions"].append({"kind": kind, "expected": expected_literal})
        else:
            raise RerunRecipeBlocked("unsupported_assertion")
    return payload, fingerprint


def build_rerun_recipe(
    before: Observation, decision: AgentDecision, app_url: str,
    *, secret_values: Iterable[str] = (), credentials: Mapping[str, str] | None = None,
    capture_journey: bool = False,
) -> dict[str, Any]:
    """Capture an action using an explicit EphemeralCredentialVault.get mapping.

    Only fill supports aliases. Unknown private fills get a target-derived alias;
    no value or value-derived digest is stored. Nonempty masked fields remain
    unportable: possession of a credential does not prove browser field content.
    Empty sensitive fields are portable without weakening nonempty guards.
    """
    disabled: dict[str, Any] = {"version": RECIPE_VERSION, "enabled": False}
    if capture_journey:
        disabled["journey_capture"] = True
    try:
        credentials = _credential_mapping(credentials)
        secrets = tuple(secret_values) + tuple(credentials.values())
        origin = _origin(app_url)
        before_url = _public_url(before.url, secrets)
        if _origin(before_url) != origin:
            raise RerunRecipeBlocked("cross_origin_source")
        payload, fingerprint = _decision_payload(before, decision, origin, secrets, credentials)
        _check_observed_secrets(before, (*secrets, decision.value)
                                if "input_ref" in payload and decision.value else secrets)
        state_inputs = {}
        for ref, alias in getattr(before.page_state, "rerun_field_bindings",
                                             before.page_state.get("_rerun_field_bindings", {})).items():
            state_inputs[semantic_element_fingerprint(_by_ref(before, ref))] = alias
        return {
            **disabled, "enabled": True,
            **({"state_inputs": state_inputs} if state_inputs else {}),
            "origin": origin, "before_url": before_url,
            "before_effect_digest": recipe_effect_digest(before),
            "before_state_digest": recipe_state_digest(before, state_inputs=state_inputs, credentials=credentials),
            "target_fingerprint": fingerprint, "decision": payload,
        }
    except RerunRecipeBlocked as exc:
        return {**disabled, "reason": exc.reason}
    except (TypeError, ValueError, AttributeError):
        return {**disabled, "reason": "invalid_observation"}


def validate_rerun_recipe(recipe: dict[str, Any], app_url: str) -> None:
    """Reject malformed/private/cross-origin metadata before opening a browser.

    This is structural preflight only. resolve_rerun_decision must still check
    the current observation and exact original control/value before dispatch.
    """
    if not isinstance(recipe, dict) or recipe.get("version") != RECIPE_VERSION:
        raise RerunRecipeBlocked("missing_or_unsupported_recipe")
    if recipe.get("enabled") is not True:
        raise RerunRecipeBlocked(str(recipe.get("reason") or "unsupported_recipe"))
    try:
        origin = _origin(app_url)
        if recipe.get("origin") != origin or _origin(_public_url(recipe.get("before_url"), ())) != origin:
            raise RerunRecipeBlocked("cross_origin_source")
        for name in ("before_effect_digest", "before_state_digest"):
            if not isinstance(recipe.get(name), str) or not _HASH.fullmatch(recipe[name]):
                raise RerunRecipeBlocked("invalid_precondition_digest")
        state_inputs = recipe.get("state_inputs", {})
        if (not isinstance(state_inputs, dict) or any(
                not isinstance(fp, str) or not _HASH.fullmatch(fp)
                or not isinstance(alias, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,99}", alias)
                for fp, alias in state_inputs.items())):
            raise RerunRecipeBlocked("invalid_input_reference")
        raw = recipe.get("decision")
        if (not isinstance(raw, dict) or set(raw) - {"action", "value", "key", "url", "assertions", "validation_probe", "input_ref"}
                or raw.get("action") not in _ACTIONS):
            raise RerunRecipeBlocked("invalid_decision")
        action = raw["action"]
        probe = raw.get("validation_probe")
        if probe is not None and (action != "click" or not isinstance(probe, dict)
                or set(probe) != {"kind", "target_fingerprint"} or probe.get("kind") != "required_field"
                or not isinstance(probe.get("target_fingerprint"), str)
                or not _HASH.fullmatch(probe["target_fingerprint"])):
            raise RerunRecipeBlocked("unsupported_validation_probe")
        fingerprint = recipe.get("target_fingerprint")
        if action in _TARGET_ACTIONS:
            if not isinstance(fingerprint, str) or not _HASH.fullmatch(fingerprint):
                raise RerunRecipeBlocked("invalid_target_fingerprint")
        elif fingerprint is not None:
            raise RerunRecipeBlocked("unexpected_action_target")
        input_ref = raw.get("input_ref")
        if "input_ref" in raw:
            if (action != "fill" or "value" in raw or not isinstance(input_ref, str)
                    or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,99}", input_ref)):
                raise RerunRecipeBlocked("invalid_input_reference")
        if input_ref is not None:
            pass
        elif action == "wait":
            if (not isinstance(raw.get("value"), str)
                    or not re.fullmatch(r"[0-9]{1,4}", raw["value"]) or int(raw["value"]) > 5000):
                raise RerunRecipeBlocked("unsupported_wait")
        elif action in {"fill", "select"}:
            value = _public_literal(raw.get("value"), (), limit=120)
            if action == "fill" and value not in _PUBLIC_SYNTHETIC_VALUES and not re.fullmatch(
                r"AutoJev QA [a-z ]{1,48}", value,
            ):
                raise RerunRecipeBlocked("non_deterministic_fill")
        elif "value" in raw:
            raise RerunRecipeBlocked("unexpected_action_value")
        if action == "press" or (action == "fill" and "key" in raw):
            if _public_literal(raw.get("key"), (), limit=20).casefold() not in _PRESS_KEYS:
                raise RerunRecipeBlocked("unsupported_key")
        elif "key" in raw:
            raise RerunRecipeBlocked("unexpected_action_key")
        if action == "navigate":
            if _origin(_public_url(raw.get("url"), ())) != origin:
                raise RerunRecipeBlocked("cross_origin_destination")
        elif "url" in raw:
            raise RerunRecipeBlocked("unexpected_action_url")
        if not isinstance(raw.get("assertions"), list) or len(raw["assertions"]) > 30:
            raise RerunRecipeBlocked("unsupported_assertion")
        for assertion in raw["assertions"]:
            if not isinstance(assertion, dict):
                raise RerunRecipeBlocked("unsupported_assertion")
            if assertion.get("kind") == "element_visible":
                if (set(assertion) != {"kind", "target_fingerprint"}
                        or not isinstance(assertion.get("target_fingerprint"), str)
                        or not _HASH.fullmatch(assertion["target_fingerprint"])):
                    raise RerunRecipeBlocked("unsupported_assertion")
            elif (assertion.get("kind") in {"url_equals", "url_contains", "title_contains", "text_contains"}
                  and set(assertion) == {"kind", "expected"}):
                _public_literal(assertion["expected"], ())
            else:
                raise RerunRecipeBlocked("unsupported_assertion")
    except RerunRecipeBlocked:
        raise
    except (TypeError, ValueError, AttributeError, KeyError) as exc:
        raise RerunRecipeBlocked("invalid_recipe") from exc


def resolve_rerun_decision(
    recipe: dict[str, Any], before: Observation, app_url: str | None = None,
    *, credentials: Mapping[str, str] | None = None,
) -> AgentDecision:
    """Bind fresh refs and explicit per-rerun vault credentials without caching.

    Input decisions carry credential_key, not plaintext; execution must receive
    the same mapping. No login or navigation semantics are invented.
    """
    validate_rerun_recipe(recipe, app_url or (recipe.get("origin") if isinstance(recipe, dict) else ""))
    try:
        credentials = _credential_mapping(credentials)
        for key in required_recipe_inputs(recipe):
            if key not in credentials:
                raise RerunRecipeBlocked("missing_recipe_input")
        _check_observed_secrets(before, credentials.values())
        origin = _origin(app_url or recipe.get("origin"))
        if recipe.get("origin") != origin or _origin(before.url) != origin:
            raise RerunRecipeBlocked("cross_origin_source")
        if before.url != recipe.get("before_url"):
            raise RerunRecipeBlocked("before_url_changed")
        raw = recipe.get("decision")
        if not isinstance(raw, dict) or set(raw) - {"action", "value", "key", "url", "assertions", "validation_probe", "input_ref"}:
            raise RerunRecipeBlocked("invalid_decision")
        target = None
        if raw.get("action") in _TARGET_ACTIONS:
            target = str(_by_fingerprint(before, recipe.get("target_fingerprint"))["ref"])
        elif recipe.get("target_fingerprint") is not None:
            raise RerunRecipeBlocked("unexpected_action_target")
        assertions = []
        if not isinstance(raw.get("assertions"), list):
            raise RerunRecipeBlocked("unsupported_assertion")
        for assertion in raw["assertions"]:
            if not isinstance(assertion, dict):
                raise RerunRecipeBlocked("unsupported_assertion")
            if assertion.get("kind") == "element_visible":
                if set(assertion) != {"kind", "target_fingerprint"}:
                    raise RerunRecipeBlocked("unsupported_assertion")
                ref = str(_by_fingerprint(before, assertion.get("target_fingerprint"))["ref"])
                assertions.append({"kind": "element_visible", "expected": ref})
            else:
                assertions.append(dict(assertion))
        scenario_hint = None
        if raw.get("validation_probe"):
            field = _by_fingerprint(before, raw["validation_probe"]["target_fingerprint"])
            matches = [entry for entry in before.page_state.get("fields", []) if entry.get("ref") == field["ref"]]
            if len(matches) != 1 or not matches[0].get("identity"):
                raise RerunRecipeBlocked("unsupported_validation_probe")
            scenario_hint = {"validation_probe": {"kind": "required_field",
                "field_identity": matches[0]["identity"]}}
        decision = AgentDecision(
            action=raw.get("action"), rationale="Reexecute the original observed CT action.",
            target=target, value=raw.get("value"), credential_key=raw.get("input_ref"),
            key=raw.get("key"), url=raw.get("url"),
            assertions=assertions, scenario_hint=scenario_hint,
        )
        # Reapply the same action/value/scope policy to persisted data, not just
        # to freshly captured decisions. Unknown fields/values fail closed.
        payload, _ = _decision_payload(before, decision, origin, tuple(credentials.values()), credentials)
        if payload != raw:
            raise RerunRecipeBlocked("invalid_decision")
        if recipe_effect_digest(before) != recipe.get("before_effect_digest"):
            raise RerunRecipeBlocked("before_effect_changed")
        if recipe_state_digest(before, state_inputs=recipe.get("state_inputs"),
                               credentials=credentials) != recipe.get("before_state_digest"):
            raise RerunRecipeBlocked("before_state_changed")
        return decision
    except RerunRecipeBlocked:
        raise
    except (TypeError, ValueError, AttributeError, KeyError) as exc:
        raise RerunRecipeBlocked("invalid_recipe") from exc
