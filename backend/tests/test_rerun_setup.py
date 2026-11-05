from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from autojev.domain.models import Observation
from autojev.services.rerun_setup import (
    AuthenticationSetupBlocked,
    build_authentication_setup,
    execute_authentication_setup,
    required_authentication_inputs,
    validate_authentication_setup,
)

APP = "https://app.test/login"
VAULT = {"username": "alice@example.test", "password": "private-password"}


def observation():
    return Observation(url=APP, title="Login", text="Sign in", interactive_elements=[
        {"ref": "e1", "tag": "input", "type": "email", "name": "Email", "form": "login"},
        {"ref": "e2", "tag": "input", "type": "password", "name": "Password", "form": "login"},
        {"ref": "e3", "tag": "button", "name": "Sign in", "form": "login"},
    ])


class Executor:
    def __init__(self):
        self.calls = []
        self.result = SimpleNamespace(status="success", reason="authenticated")

    async def attempt_login(self, credentials, before):
        self.calls.append((credentials, before))
        return self.result


def run(executor, setup, before, credentials=VAULT):
    return asyncio.run(execute_authentication_setup(executor, setup, before, credentials))


def test_explicit_setup_rebinds_refs_and_forwards_only_required_vault_inputs():
    before = observation()
    setup = build_authentication_setup(before, VAULT, APP)
    validate_authentication_setup(setup, APP)
    for index, control in enumerate(before.interactive_elements):
        control["ref"] = f"e{index + 90}"
        control["value"] = "[FIELD VALUE]"
    before.page_state = {"cookies": "must not be used"}
    executor = Executor()
    assert run(executor, setup, before, {**VAULT, "extra": "not forwarded"}) is executor.result
    assert executor.calls == [(VAULT, before)]
    assert required_authentication_inputs(setup) == ["username", "password"]


def test_manual_original_explicitly_requests_both_credentials():
    setup = build_authentication_setup(observation(), None, APP)
    executor = Executor()
    with pytest.raises(AuthenticationSetupBlocked, match="missing_authentication_inputs") as error:
        run(executor, setup, observation(), None)
    assert error.value.required_inputs == ["username", "password"]
    assert not executor.calls


def test_partial_vault_requests_only_missing_alias():
    setup = build_authentication_setup(observation(), VAULT, APP)
    with pytest.raises(AuthenticationSetupBlocked) as error:
        run(Executor(), setup, observation(), {"username": "new-user"})
    assert error.value.required_inputs == ["password"]


@pytest.mark.parametrize("change", ["url", "name", "form", "type", "extra", "disabled", "duplicate"])
def test_changed_url_or_controls_never_dispatch(change):
    before = observation()
    setup = build_authentication_setup(before, VAULT, APP)
    if change == "url":
        before.url = "https://app.test/other-login"
    elif change == "extra":
        before.interactive_elements.append({"ref": "e9", "tag": "input", "type": "checkbox", "name": "Agree"})
    elif change == "duplicate":
        before.interactive_elements.append({**before.interactive_elements[0], "ref": "e9"})
    elif change == "disabled":
        before.interactive_elements[0]["enabled"] = False
    else:
        before.interactive_elements[0][change] = "changed"
    executor = Executor()
    with pytest.raises(AuthenticationSetupBlocked):
        run(executor, setup, before)
    assert not executor.calls


@pytest.mark.parametrize("change", ["extra", "alias", "field", "version", "enabled", "controls", "before_url"])
def test_persisted_metadata_is_strictly_validated(change):
    setup = build_authentication_setup(observation(), VAULT, APP)
    if change == "extra":
        setup["cookies"] = "no"
    elif change == "alias":
        setup["required_inputs"].append("token")
    elif change == "field":
        setup["fields"]["username"] = "not-a-hash"
    elif change == "controls":
        setup["controls"].append(setup["controls"][0])
    else:
        setup[change] = None
    with pytest.raises(AuthenticationSetupBlocked):
        validate_authentication_setup(setup, APP)


def test_no_secrets_values_storage_or_old_refs_persist_and_no_mutation():
    before = observation()
    before.interactive_elements[0]["value"] = VAULT["username"]
    before.page_state = {"localStorage": {"token": VAULT["password"]}}
    baseline = copy.deepcopy(before)
    setup = build_authentication_setup(before, VAULT, APP)
    serialized = json.dumps(setup)
    assert setup["enabled"]
    assert all(value not in serialized for value in VAULT.values())
    assert all(key not in serialized for key in ("localStorage", "cookies", '"e1"'))
    assert before == baseline


def test_known_secrets_in_semantic_attributes_disable_capture():
    before = observation()
    before.interactive_elements[0]["name"] = VAULT["username"]
    assert build_authentication_setup(before, VAULT, APP)["enabled"] is False


@pytest.mark.parametrize("url", ["https://evil.test/login", "https://app.test/login?token=abc", "https://u:p@app.test/login"])
def test_unsafe_urls_disable_capture(url):
    before = observation()
    before.url = url
    assert build_authentication_setup(before, VAULT, APP)["enabled"] is False


def test_login_failure_is_returned_without_manual_fallback_or_retry():
    executor = Executor()
    executor.result = SimpleNamespace(status="manual_required", reason="authentication_challenge")
    setup = build_authentication_setup(observation(), VAULT, APP)
    assert run(executor, setup, observation()) is executor.result
    assert len(executor.calls) == 1


def test_cross_origin_validation_blocks_setup():
    setup = build_authentication_setup(observation(), VAULT, APP)
    with pytest.raises(AuthenticationSetupBlocked, match="cross_origin_authentication"):
        validate_authentication_setup(setup, "https://evil.test")
