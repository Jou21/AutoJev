"""Private authentication prerequisite captured before the original login.

Capture only around a successful original attempt_login/attempt_filled_login,
then persist this contract, never browser storage or credentials. This uses the
executor's guarded login strategy, not general exploratory action dispatch.
Callers must collect required_authentication_inputs from their volatile vault
before execution; manually filled originals still require both input aliases.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from autojev.domain.models import Observation
from autojev.services.rerun_recipe import RerunRecipeBlocked

_VERSION = "ct-authentication-v1"
_KEYS = ["username", "password"]
_ATTRIBUTES = (
    "tag", "role", "type", "name", "id", "semantic_identity", "form",
    "autocomplete", "placeholder", "required", "enabled", "read_only",
    "disabled", "visible", "formaction", "formmethod",
)
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_LOGIN = re.compile(r"^(log[ -]?in|logar|sign[ -]?in|sign on|entrar|acessar|autenticar|iniciar sessão)(\b|$)", re.I)
_USERNAME = re.compile(r"user|usuario|usuário|e.?mail|login|account|conta|telefone|phone|identifier", re.I)


class AuthenticationSetupBlocked(RerunRecipeBlocked):
    def __init__(self, reason: str, required_inputs: list[str] | None = None) -> None:
        self.required_inputs = list(required_inputs or [])
        super().__init__(reason)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _origin(url: str) -> str:
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None):
            raise ValueError
        if parsed.query or re.search(r"token|secret|\[redacted\]|%5bredacted%5d", url, re.I):
            raise ValueError
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        return f"{parsed.scheme}://{host}:{port}"
    except (TypeError, ValueError) as exc:
        raise AuthenticationSetupBlocked("unsafe_authentication_url") from exc


def _snapshot(before: Observation) -> dict[str, Any]:
    controls = before.interactive_elements
    if not controls or any(not isinstance(control, dict) for control in controls):
        raise AuthenticationSetupBlocked("unobserved_authentication_controls")
    refs = [control.get("ref") for control in controls]
    if any(not isinstance(ref, str) or not re.fullmatch(r"e\d+", ref) for ref in refs) or len(set(refs)) != len(refs):
        raise AuthenticationSetupBlocked("ambiguous_authentication_controls")
    inputs = [control for control in controls if control.get("tag") == "input"]
    passwords = [control for control in inputs if control.get("type") == "password"]
    usernames = [control for control in inputs if control.get("type", "text") in {"text", "email", "tel"}]
    submits = [control for control in controls
               if (control.get("tag") == "button" or control.get("role") == "button"
                   or control.get("type") in {"submit", "button"})
               and _LOGIN.search(str(control.get("name", "")))]
    if len(passwords) != 1 or len(usernames) != 1 or len(submits) != 1:
        raise AuthenticationSetupBlocked("ambiguous_authentication_controls")
    username, password, submit = usernames[0], passwords[0], submits[0]
    hint = " ".join(str(username.get(key) or "") for key in ("name", "id", "autocomplete", "placeholder", "semantic_identity"))
    if username.get("type") != "email" and not _USERNAME.search(hint):
        raise AuthenticationSetupBlocked("ambiguous_authentication_username")
    if any(control.get("enabled") is False or control.get("disabled")
           or control.get("read_only") or control.get("visible") is False
           for control in (username, password, submit)):
        raise AuthenticationSetupBlocked("unsafe_authentication_controls")
    # No observed value, text, cookies, tokens or storage enters the contract.
    def signature(control: dict[str, Any]) -> str:
        return _digest({key: control[key] for key in _ATTRIBUTES if key in control})
    fingerprints = sorted(signature(control) for control in controls)
    if len(set(fingerprints)) != len(fingerprints):
        raise AuthenticationSetupBlocked("ambiguous_authentication_controls")
    return {"url_digest": _digest(before.url), "controls": fingerprints,
            "fields": {"username": signature(username), "password": signature(password),
                       "submit": signature(submit)}}


def build_authentication_setup(
    before: Observation, credentials: Mapping[str, str] | None, app_url: str,
) -> dict[str, Any]:
    """Build before dispatch; retain only when the original login succeeds.

    Missing original credentials do not disable replay: execution explicitly
    requests username and password instead of silently falling back to manual.
    """
    try:
        origin = _origin(app_url)
        if _origin(before.url) != origin:
            raise AuthenticationSetupBlocked("cross_origin_authentication")
        snapshot = _snapshot(before)
        if credentials is not None:
            if not isinstance(credentials, Mapping) or any(not isinstance(value, str) for value in credentials.values()):
                raise AuthenticationSetupBlocked("invalid_authentication_credentials")
            # Do not persist even dictionary-attackable hashes of known secrets.
            material = json.dumps({"url": before.url, "controls": [
                {key: control[key] for key in _ATTRIBUTES if key in control}
                for control in before.interactive_elements]}, ensure_ascii=False)
            if any(value and value in material for value in credentials.values()):
                raise AuthenticationSetupBlocked("private_authentication_observation")
        return {"version": _VERSION, "enabled": True, "strategy": "guarded_login",
                "origin": origin, "before_url": before.url, "required_inputs": list(_KEYS), **snapshot}
    except (RerunRecipeBlocked, TypeError, ValueError) as exc:
        return {"version": _VERSION, "enabled": False,
                "reason": getattr(exc, "reason", "invalid_authentication_setup"),
                "required_inputs": list(_KEYS)}


def required_authentication_inputs(setup: dict[str, Any]) -> list[str]:
    """Validate the persisted allowlist and return vault aliases, never values."""
    expected = {"version", "enabled", "strategy", "origin", "required_inputs",
                "url_digest", "controls", "fields", "before_url"}
    if (not isinstance(setup, dict) or set(setup) != expected
            or setup.get("version") != _VERSION or setup.get("enabled") is not True
            or setup.get("strategy") != "guarded_login"
            or setup.get("required_inputs") != _KEYS):
        raise AuthenticationSetupBlocked("invalid_authentication_setup")
    if not isinstance(setup["origin"], str) or _origin(setup["origin"]) != setup["origin"]:
        raise AuthenticationSetupBlocked("invalid_authentication_setup")
    if (not isinstance(setup["before_url"], str)
            or _origin(setup["before_url"]) != setup["origin"]
            or _digest(setup["before_url"]) != setup["url_digest"]):
        raise AuthenticationSetupBlocked("invalid_authentication_setup")
    controls, fields = setup["controls"], setup["fields"]
    if (not isinstance(controls, list) or len(controls) < 3
            or any(not isinstance(value, str) or not _HASH.fullmatch(value) for value in controls)
            or controls != sorted(set(controls)) or not isinstance(fields, dict)
            or set(fields) != {"username", "password", "submit"}
            or any(not isinstance(value, str) or value not in controls for value in fields.values())
            or len(set(fields.values())) != 3
            or not isinstance(setup["url_digest"], str) or not _HASH.fullmatch(setup["url_digest"])):
        raise AuthenticationSetupBlocked("invalid_authentication_setup")
    return list(_KEYS)


def validate_authentication_setup(setup: dict[str, Any], app_url: str) -> None:
    """Validate private persisted metadata against the authorized application."""
    required_authentication_inputs(setup)
    if _origin(app_url) != setup["origin"]:
        raise AuthenticationSetupBlocked("cross_origin_authentication")


async def execute_authentication_setup(
    executor: Any, setup: dict[str, Any], observation: Observation,
    credentials: Mapping[str, str] | None,
) -> Any:
    """Verify the saved URL/control allowlist, then attempt guarded login once.

    The supplied observation must be freshly obtained from this executor.
    The executor additionally validates live DOM identity, form action, origin,
    challenges and confirmation. Its one-shot attempt guard remains authoritative.
    Outcomes (including rejection/manual_required) are returned without retries;
    this helper never calls attempt_filled_login or obtains browser storage.
    """
    required_authentication_inputs(setup)
    if credentials is not None and not isinstance(credentials, Mapping):
        raise AuthenticationSetupBlocked("invalid_authentication_credentials")
    vault = credentials or {}
    missing = [key for key in _KEYS if not isinstance(vault.get(key), str) or not vault[key]]
    if missing:
        raise AuthenticationSetupBlocked("missing_authentication_inputs", missing)
    if _origin(observation.url) != setup["origin"]:
        raise AuthenticationSetupBlocked("cross_origin_authentication")
    snapshot = _snapshot(observation)
    if any(snapshot[key] != setup[key] for key in snapshot):
        raise AuthenticationSetupBlocked("authentication_controls_changed")
    return await executor.attempt_login({key: vault[key] for key in _KEYS}, observation)
