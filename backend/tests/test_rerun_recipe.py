from __future__ import annotations

import copy
import json
from dataclasses import asdict

import pytest

from autojev.adapters.secrets import redact
from autojev.domain.models import AgentDecision, Observation
from autojev.services.rerun_recipe import (
    RerunRecipeBlocked,
    build_rerun_recipe,
    recipe_state_digest,
    resolve_rerun_decision,
    validate_rerun_recipe,
)


APP = "https://app.test/inventory"


def observation(*, ref="e1", name="Open details", state=None, **control):
    return Observation(
        url=APP, title="Inventory", text="Inventory - details",
        interactive_elements=[{
            "ref": ref, "tag": "button", "name": name, "enabled": True, **control,
        }],
        page_state=copy.deepcopy(state or {}),
    )


def decision(action="click", **kwargs):
    return AgentDecision(action=action, target="e1", rationale="Private model reasoning", **kwargs)


def test_ref_independent_recipe_rebinds_unique_live_control_and_assertion():
    old = observation(semantic_identity="button:data-test=open-details")
    original = decision(assertions=[{"kind": "element_visible", "expected": "e1"}])
    recipe = build_rerun_recipe(old, original, APP)
    assert recipe["enabled"] is True
    current = observation(ref="e98", semantic_identity="button:data-test=open-details",
                          document_top=900, in_viewport=False, interactable=False)
    actual = resolve_rerun_decision(recipe, current, APP)
    assert actual.action == "click"
    assert actual.target == "e98"
    assert actual.assertions == [{"kind": "element_visible", "expected": "e98"}]
    assert '"e1"' not in json.dumps(recipe["decision"])
    assert "Private model reasoning" not in json.dumps(recipe)


def test_capture_and_resolve_do_not_mutate_observation_decision_or_recipe():
    before = observation(state={"cart": {"count": 0, "link_refs": ["e1"]}})
    original = decision()
    baseline = copy.deepcopy((before, original))
    recipe = build_rerun_recipe(before, original, APP)
    recipe_baseline = copy.deepcopy(recipe)
    assert resolve_rerun_decision(recipe, before, APP).target == "e1"
    assert (before, original) == baseline
    assert recipe == recipe_baseline


@pytest.mark.parametrize("change, reason", [
    ({"name": "Another button"}, "missing_target"),
    ({"enabled": False}, "unsafe_target"),
    ({"read_only": True}, "before_state_changed"),
])
def test_live_target_changes_fail_closed(change, reason):
    recipe = build_rerun_recipe(observation(), decision(), APP)
    fresh = observation(**change)
    with pytest.raises(RerunRecipeBlocked, match=reason):
        resolve_rerun_decision(recipe, fresh, APP)


def test_ambiguous_target_is_rejected_at_capture_and_resolution():
    before = observation()
    recipe = build_rerun_recipe(before, decision(), APP)
    before.interactive_elements.append({**before.interactive_elements[0], "ref": "e2"})
    assert build_rerun_recipe(before, decision(), APP)["reason"] == "ambiguous_target"
    with pytest.raises(RerunRecipeBlocked, match="ambiguous_target"):
        resolve_rerun_decision(recipe, before, APP)


@pytest.mark.parametrize("url, reason", [
    ("https://elsewhere.test/inventory", "cross_origin_source"),
    ("https://app.test/inventory?sort=desc", "before_url_changed"),
    ("https://app.test/inventory#details", "before_url_changed"),
])
def test_exact_url_and_configured_origin_preconditions(url, reason):
    before = observation()
    recipe = build_rerun_recipe(before, decision(), APP)
    before.url = url
    with pytest.raises(RerunRecipeBlocked, match=reason):
        resolve_rerun_decision(recipe, before, APP)
    with pytest.raises(RerunRecipeBlocked, match="cross_origin_source"):
        resolve_rerun_decision(recipe, observation(), "https://different.test")


def test_visible_effect_or_cart_state_change_blocks_original_action():
    before = observation(state={"cart": {"count": 0, "items": []}})
    recipe = build_rerun_recipe(before, decision(), APP)
    before.text = "Inventory changed"
    with pytest.raises(RerunRecipeBlocked, match="before_effect_changed"):
        resolve_rerun_decision(recipe, before, APP)
    before.text = "Inventory - details"
    before.page_state["cart"]["count"] = 1
    with pytest.raises(RerunRecipeBlocked, match="before_state_changed"):
        resolve_rerun_decision(recipe, before, APP)


def test_page_state_refs_rebind_and_field_form_values_are_checked_without_serializing():
    before = observation(tag="input", type="text", name="Search", state={
        "fields": [{"ref": "e1", "identity": "search", "type": "text", "has_value": True,
                    "value_token": "a" * 64, "value": "AutoJev QA"}],
        "forms": [{"id": "search-form", "submit_refs": ["e1"]}],
    })
    recipe = build_rerun_recipe(before, decision("fill", value="AutoJev QA search"), APP)
    assert recipe["enabled"] is True
    assert "a" * 64 not in json.dumps(recipe)
    fresh = copy.deepcopy(before)
    fresh.interactive_elements[0]["ref"] = "e20"
    fresh.page_state["fields"][0]["ref"] = "e20"
    fresh.page_state["forms"][0]["submit_refs"] = ["e20"]
    assert resolve_rerun_decision(recipe, fresh, APP).target == "e20"
    fresh.page_state["fields"][0]["value"] = "AutoJev QA exploratory input"
    with pytest.raises(RerunRecipeBlocked, match="before_state_changed"):
        resolve_rerun_decision(recipe, fresh, APP)


@pytest.mark.parametrize("value, has_value", [("", False), ("AutoJev QA", True)])
def test_empty_and_explicit_public_synthetic_values_survive_executor_token_rotation(value, has_value):
    before = observation(state={"fields": [{
        "ref": "e1", "type": "search", "value": value, "has_value": has_value,
        "value_token": "a" * 64,
    }]})
    after = copy.deepcopy(before)
    after.page_state["fields"][0]["value_token"] = "b" * 64
    assert recipe_state_digest(before) == recipe_state_digest(after)
    after.page_state["fields"][0]["has_value"] = not has_value
    after.page_state["fields"][0]["value"] = "AutoJev QA exploratory input" if not has_value else ""
    assert recipe_state_digest(before) != recipe_state_digest(after)


def test_unverifiable_nonempty_legacy_field_is_not_a_replay_precondition():
    before = observation(state={"fields": [{"ref": "e1", "type": "text", "has_value": True}]})
    assert build_rerun_recipe(before, decision(), APP)["reason"] == "unverifiable_field_state"


@pytest.mark.parametrize("action, reason", [
    ("finish", "unsupported_action"),
])
def test_history_and_unallowlisted_actions_are_explicitly_disabled(action, reason):
    recipe = build_rerun_recipe(observation(), decision(action), APP)
    assert recipe == {"version": "ct-rerun-v1", "enabled": False, "reason": reason}


@pytest.mark.parametrize("name, field_type", [
    ("Password", "password"), ("Username", "text"), ("API key", "text"),
    ("OTP token", "text"), ("Senha", "password"),
])
def test_sensitive_controls_require_explicit_input_not_persisted_values(name, field_type):
    recipe = build_rerun_recipe(observation(tag="input", type=field_type, name=name),
                                decision("fill", value="do-not-persist"), APP)
    assert recipe["enabled"] is True
    assert recipe["decision"]["input_ref"].startswith("replay_")
    assert "do-not-persist" not in json.dumps(recipe)
    credential = build_rerun_recipe(observation(), decision(credential_key="password"), APP)
    assert credential["reason"] == "credential_action"


def test_authentication_challenge_disables_even_otherwise_safe_action():
    before = observation(state={"authentication_challenge": True})
    assert build_rerun_recipe(before, decision(), APP)["reason"] == "authentication_required"


def test_structured_probe_semantics_are_not_silently_dropped():
    original = decision(scenario_hint={"validation_probe": {"kind": "required_field"}})
    assert build_rerun_recipe(observation(), original, APP)["reason"] == "unsupported_validation_probe"
    prose = decision(scenario_hint={"title": "A descriptive title"})
    assert build_rerun_recipe(observation(), prose, APP)["enabled"] is True


@pytest.mark.parametrize("value", ["a real input value", "standard_user", "secret-password"])
def test_nonpublic_fill_becomes_required_input(value):
    before = observation(tag="input", type="text", name="City")
    recipe = build_rerun_recipe(before, decision("fill", value=value), APP)
    assert recipe["enabled"] is True
    assert recipe["decision"]["input_ref"].startswith("replay_")
    assert value not in json.dumps(recipe)


@pytest.mark.parametrize("action, kwargs, control", [
    ("fill", {"value": "AutoJev QA city"}, {"tag": "input", "type": "text", "name": "City"}),
    ("fill", {"value": "01001000"}, {"tag": "input", "type": "text", "name": "Postal code"}),
    ("select", {"value": "za"}, {"tag": "select", "name": "Sort products", "options": [
        {"value": "az", "label": "A to Z"}, {"value": "za", "label": "Z to A"},
    ]}),
    ("hover", {}, {}), ("press", {"key": "Escape"}, {}),
])
def test_supported_atomic_actions_roundtrip_with_new_refs(action, kwargs, control):
    before = observation(**control)
    recipe = build_rerun_recipe(before, decision(action, **kwargs), APP)
    assert recipe["enabled"] is True
    before.interactive_elements[0]["ref"] = "e42"
    resolved = resolve_rerun_decision(recipe, before, APP)
    assert resolved.action == action
    assert resolved.target == "e42"
    assert resolved.value == kwargs.get("value")


@pytest.mark.parametrize("action, url", [("navigate", "https://app.test/cart"), ("reload", None)])
def test_navigation_and_reload_have_no_target(action, url):
    original = AgentDecision(action=action, rationale="original", url=url)
    recipe = build_rerun_recipe(observation(), original, APP)
    assert recipe["enabled"] is True
    resolved = resolve_rerun_decision(recipe, observation(), APP)
    assert resolved.target is None
    assert resolved.action == action
    assert resolved.url == url


def test_navigation_outside_origin_and_credential_bearing_urls_are_not_persisted():
    original = AgentDecision(action="navigate", rationale="", url="https://elsewhere.test/cart")
    assert build_rerun_recipe(observation(), original, APP)["reason"] == "cross_origin_destination"
    for url in ["https://user:password@app.test/inventory", "https://app.test/?token=super-private",
                "https://app.test/#access_token=super-private",
                "https://app.test/?q=%5BREDACTED%5D",
                "https://app.test/?q=%73%65%63%72%65%74"]:
        before = observation()
        before.url = url
        recipe = build_rerun_recipe(before, decision(), APP)
        assert recipe["enabled"] is False
        assert "super-private" not in json.dumps(recipe)


def test_known_in_memory_credentials_cannot_be_copied_into_select_assertions_or_url():
    before = observation(tag="select", name="Choice", options=[{"value": "abc123", "label": "Option"}])
    recipe = build_rerun_recipe(before, decision("select", value="abc123"), APP,
                                secret_values=["abc123"])
    assert recipe["reason"] == "sensitive_replay_value"
    before = observation()
    recipe = build_rerun_recipe(before, decision(assertions=[{"kind": "text_contains", "expected": "abc123"}]),
                                APP, secret_values=["abc123"])
    assert recipe["reason"] == "sensitive_replay_value"
    before.url = APP + "?q=abc123"
    assert build_rerun_recipe(before, decision(), APP, secret_values=["abc123"])["enabled"] is False


def test_existing_dom_values_and_text_only_survive_as_hashes_not_snapshot_data():
    before = observation(state={"fields": [{"ref": "e1", "type": "text", "has_value": True,
                                             "value_token": "c" * 64, "value": "private field contents"}]})
    before.text = "Customer: PRIVATE CUSTOMER DATA"
    recipe = build_rerun_recipe(before, decision(), APP)
    assert recipe["enabled"] is False
    assert recipe["reason"] == "unportable_field_state"
    serialized = json.dumps(recipe)
    for private in ["private field contents", "PRIVATE CUSTOMER DATA", "c" * 64,
                    "Open details", "Inventory", "Private model reasoning"]:
        assert private not in serialized
    assert recipe == redact(recipe, ["an unrelated sufficiently long credential"])


@pytest.mark.parametrize("assertion", [
    {"kind": "element_visible", "expected": "#arbitrary-selector"},
    {"kind": "element_visible", "expected": "e999"},
    {"kind": "unsupported", "expected": "anything"},
    {"kind": "text_contains", "expected": "password=super-secret"},
])
def test_unbindable_or_sensitive_assertions_disable_recipe(assertion):
    recipe = build_rerun_recipe(observation(), decision(assertions=[assertion]), APP)
    assert recipe["enabled"] is False
    assert "super-secret" not in json.dumps(recipe)


def test_persisted_recipe_is_revalidated_instead_of_trusting_a_replay_value():
    before = observation(tag="input", type="text", name="City")
    recipe = build_rerun_recipe(before, decision("fill", value="AutoJev QA city"), APP)
    recipe["decision"]["value"] = "an arbitrary persisted credential"
    with pytest.raises(RerunRecipeBlocked, match="sensitive_replay_value"):
        resolve_rerun_decision(recipe, before, APP)
    recipe["decision"]["credential_key"] = "password"
    with pytest.raises(RerunRecipeBlocked, match="invalid_decision"):
        resolve_rerun_decision(recipe, before, APP)


def test_missing_recipe_and_missing_or_malformed_preconditions_never_dispatch():
    before = observation()
    for recipe in [{}, None, {"version": "future", "enabled": True}]:
        with pytest.raises(RerunRecipeBlocked, match="missing_or_unsupported_recipe"):
            resolve_rerun_decision(recipe, before, APP)
    recipe = build_rerun_recipe(before, decision(), APP)
    del recipe["before_state_digest"]
    with pytest.raises(RerunRecipeBlocked, match="invalid_precondition_digest"):
        resolve_rerun_decision(recipe, before, APP)


def test_select_state_is_portable_across_real_executor_hmac_keys():
    from autojev.adapters.playwright_executor import PlaywrightExecutor
    from autojev.domain.models import ExplorationOptions

    one = PlaywrightExecutor(APP, ExplorationOptions())
    two = PlaywrightExecutor(APP, ExplorationOptions())
    raw = {"fields": [{
        "ref": "e1", "identity": "sort", "type": "select-one", "has_value": True,
        "sensitive": False, "selected_values": ["az"], "_raw_value": "az",
    }]}
    state_one = one._sanitize_page_state(copy.deepcopy(raw))
    state_two = two._sanitize_page_state(copy.deepcopy(raw))
    assert state_one["fields"][0]["value_token"] != state_two["fields"][0]["value_token"]
    assert "value" not in state_one["fields"][0]
    options = [{"value": "az", "label": "A to Z", "selected": True},
               {"value": "za", "label": "Z to A", "selected": False}]
    before = observation(tag="select", name="Sort", selected_index=0,
                         selected_values=["az"], options=options, state=state_one)
    recipe = build_rerun_recipe(before, decision("select", value="za"), APP)
    assert recipe["enabled"] is True
    fresh = copy.deepcopy(before)
    fresh.page_state = state_two
    fresh.interactive_elements[0]["ref"] = "e21"
    fresh.page_state["fields"][0]["ref"] = "e21"
    assert resolve_rerun_decision(recipe, fresh, APP).target == "e21"
    fresh.page_state["fields"][0]["selected_values"] = ["za"]
    with pytest.raises(RerunRecipeBlocked, match="unverifiable_selection_state"):
        resolve_rerun_decision(recipe, fresh, APP)


def test_synthetic_fill_checks_match_across_executor_hmac_keys_without_reusing_tokens():
    from autojev.adapters.playwright_executor import PlaywrightExecutor
    from autojev.domain.models import ActionResult, ExplorationOptions, utc_now
    from autojev.services.business_checks import check_business_outcomes
    from autojev.services.scenario_rerun import ScenarioRerunService

    value = "AutoJev QA city"
    identity = "form:id=address::input:id=city"
    old_executor = PlaywrightExecutor(APP, ExplorationOptions())
    fresh_executor = PlaywrightExecutor(APP, ExplorationOptions())
    tokens = []
    checks_by_session = []
    recipes = []
    for index, executor in enumerate((old_executor, fresh_executor)):
        ref = "e1" if index == 0 else "e21"
        state = {"fields": [
            {"identity": identity, "ref": ref, "label": "City", "type": "text",
             "form": "address", "sensitive": False, "has_value": False, "_raw_value": ""},
            {"identity": "form:id=address::input:id=peer", "ref": "e2", "label": "Peer", "type": "text",
             "form": "address", "sensitive": False, "has_value": False, "_raw_value": ""},
        ]}
        before = observation(ref=ref, tag="input", type="text", name="City",
                             state=executor._sanitize_page_state(copy.deepcopy(state)))
        before.interactive_elements.append({"ref": "e2", "tag": "input", "type": "text",
                                            "name": "Peer", "enabled": True})
        current_decision = AgentDecision("fill", "", target=ref, value=value)
        recipes.append(build_rerun_recipe(before, current_decision, APP))
        state["fields"][0].update(has_value=True, _raw_value=value)
        after = copy.deepcopy(before)
        after.page_state = executor._sanitize_page_state(state)
        token = executor._field_value_token(value)
        tokens.append(token)
        result = ActionResult(
            "fill", True, utc_now(), utc_now(), APP, APP, target=ref, value=value,
            evidence={"business_input": {"identity": identity, "expected_token": token}},
        )
        result.assertions = check_business_outcomes(before, current_decision, result, after)
        assert {check.kind for check in result.assertions} == {"field_value_matches", "other_fields_unchanged"}
        assert all(check.passed for check in result.assertions)
        checks_by_session.append(result)
    assert tokens[0] != tokens[1]
    assert recipes[0] == recipes[1]
    original = {"evidence": {"assertions": [asdict(check) for check in checks_by_session[0].assertions]}}
    missing = ScenarioRerunService._missing_checks(original, {"decision": {"assertions": []}},
                                                  current_decision, checks_by_session[1])
    assert missing == []


def test_nonempty_sensitive_field_cannot_be_compared_by_has_value_alone():
    before = observation(state={"fields": [{
        "ref": "e2", "type": "text", "sensitive": True, "has_value": True,
    }]})
    before.interactive_elements.append({"ref": "e2", "tag": "input", "name": "Credential field"})
    assert build_rerun_recipe(before, decision(), APP)["reason"] == "unportable_field_state"


def test_unbound_page_state_references_do_not_collapse_to_an_unknown_identity():
    before = observation(state={"cart": {"link_refs": ["e999"]}})
    assert build_rerun_recipe(before, decision(), APP)["reason"] == "unbound_state_reference"


@pytest.mark.parametrize("mutate", [
    lambda recipe: recipe.update(before_state_digest="not-a-hash"),
    lambda recipe: recipe.update(before_url="https://app.test/?access_token=secret"),
    lambda recipe: recipe["decision"].update(key="Control+A"),
    lambda recipe: recipe["decision"].update(action="delete"),
    lambda recipe: recipe.update(target_fingerprint="e1"),
])
def test_structural_preflight_fails_before_a_browser_is_required(mutate):
    recipe = build_rerun_recipe(observation(), decision(), APP)
    validate_rerun_recipe(recipe, APP)
    mutate(recipe)
    with pytest.raises(RerunRecipeBlocked):
        validate_rerun_recipe(recipe, APP)


def test_transport_and_intent_history_do_not_replace_real_cart_preconditions():
    first = observation(state={"cart": {"count": 0}, "document_response": {"status_code": 200},
                               "cart_expectation": {"revision": 1, "source": "initial"}})
    second = copy.deepcopy(first)
    second.page_state["navigation_response"] = {"status_code": 200}
    second.page_state["cart_expectation"] = {"revision": 50, "source": "observed_click"}
    assert recipe_state_digest(first) == recipe_state_digest(second)
    second.page_state["cart"]["count"] = 2
    assert recipe_state_digest(first) != recipe_state_digest(second)


def test_clearing_a_public_field_is_a_reproducible_action():
    before = observation(tag="input", type="text", name="First Name", semantic_identity="first-name")
    recipe = build_rerun_recipe(before, decision("fill", value=""), APP)
    assert recipe["enabled"] is True
    assert resolve_rerun_decision(recipe, before, APP).value == ""


def test_generated_values_remain_portable_between_different_field_names():
    before = observation(tag="input", type="text", name="Last Name", semantic_identity="last-name",
        state={"fields": [{"ref": "e1", "type": "text", "has_value": True,
                           "value": "AutoJev QA first name", "value_token": "old-session"}]})
    after = copy.deepcopy(before)
    after.page_state["fields"][0]["value_token"] = "new-session"
    recipe = build_rerun_recipe(before, decision("fill", value=""), APP)
    assert recipe["enabled"] is True
    assert resolve_rerun_decision(recipe, after, APP).value == ""


def test_executor_preserves_only_explicit_generated_field_values():
    from autojev.adapters.playwright_executor import PlaywrightExecutor
    executor = PlaywrightExecutor.__new__(PlaywrightExecutor)
    executor.secret_values = ()
    executor._field_value_token = lambda raw: "a" * 64
    executor._redact_text = lambda text: text
    state = executor._sanitize_page_state({"fields": [
        {"ref": "e1", "type": "text", "_raw_value": "AutoJev QA first name", "sensitive": False},
        {"ref": "e2", "type": "text", "_raw_value": "01001000", "sensitive": False},
        {"ref": "e3", "type": "text", "_raw_value": "private user value", "sensitive": False},
        {"ref": "e4", "type": "password", "_raw_value": "private-password", "sensitive": True},
    ]})
    assert state["fields"][0]["value"] == "AutoJev QA first name"
    assert state["fields"][1]["value"] == "01001000"
    assert "value" not in state["fields"][2]
    assert "value" not in state["fields"][3]
    assert "private user value" not in json.dumps(state)


def test_retry_history_replays_only_the_durably_bound_current_attempt():
    from autojev.services.scenario_rerun import ScenarioRerunService
    first = {"kind": "action", "step_number": 1}
    latest = {"kind": "action", "step_number": 7}
    def action(step, value):
        return {"step_number": step, "decision": {"action": "fill", "value": value},
                "result": {"evidence": {"business_input": {"identity": "first-name"},
                                       "timeline": [{"kind": "action", "step_number": step}]}}}
    item = {"actions": [action(1, "AutoJev QA"), action(7, "AutoJev QA")]}
    scenario = {"evidence": {"action_step": 7, "timeline": [first, latest]}}
    baseline = copy.deepcopy((item, scenario))
    assert ScenarioRerunService._attempt_timeline(item, scenario) == [latest]
    assert (item, scenario) == baseline
    item["actions"][0]["decision"]["value"] = "different action"
    assert ScenarioRerunService._attempt_timeline(item, scenario) == [first, latest]
    item["actions"] = []
    assert ScenarioRerunService._attempt_timeline(item, scenario) == [first, latest]


def test_required_field_probe_contract_rebinds_the_field_without_losing_its_meaning():
    before = observation(state={"fields": [{"ref": "e2", "identity": "first-name", "type": "text", "has_value": False}]})
    before.interactive_elements.append({"ref": "e2", "tag": "input", "type": "text", "name": "First Name", "enabled": True})
    original = decision(scenario_hint={"validation_probe": {"kind": "required_field", "field_identity": "first-name"}})
    recipe = build_rerun_recipe(before, original, APP)
    assert recipe["enabled"] is True
    current = copy.deepcopy(before)
    current.interactive_elements[1]["ref"] = "e99"
    current.page_state["fields"][0]["ref"] = "e99"
    current.page_state["fields"][0]["identity"] = "first-name"
    replay = resolve_rerun_decision(recipe, current, APP)
    assert replay.scenario_hint == original.scenario_hint
    current.page_state["fields"][0]["has_value"] = True
    with pytest.raises(RerunRecipeBlocked):
        resolve_rerun_decision(recipe, current, APP)


def test_vault_alias_recipe_requires_fresh_credentials_and_rebinds_password():
    from autojev.adapters.secrets import EphemeralCredentialVault
    from autojev.services.rerun_recipe import required_recipe_inputs

    vault = EphemeralCredentialVault()
    vault.put("capture", {"password": "private-original-832"})
    before = observation(tag="input", type="password", name="Password", sensitive=True,
                         has_value=False, state={"authentication_challenge": True, "fields": [
                             {"ref": "e1", "type": "password", "sensitive": True, "has_value": False},
                         ]})
    recipe = build_rerun_recipe(before, decision("fill", credential_key="password"), APP,
                                credentials=vault.get("capture"))
    assert recipe["enabled"] is True
    assert required_recipe_inputs(recipe) == ["password"]
    assert recipe["decision"] == {"action": "fill", "assertions": [], "input_ref": "password"}
    baseline = copy.deepcopy(recipe)
    vault.discard("capture")
    with pytest.raises(RerunRecipeBlocked, match="missing_recipe_input"):
        resolve_rerun_decision(recipe, before, APP)
    vault.put("rerun", {"password": "changed-secret-933"})
    fresh = copy.deepcopy(before)
    fresh.interactive_elements[0]["ref"] = "e9"
    fresh.page_state["fields"][0]["ref"] = "e9"
    actual = resolve_rerun_decision(recipe, fresh, APP, credentials=vault.get("rerun"))
    assert (actual.action, actual.target, actual.credential_key, actual.value) == (
        "fill", "e9", "password", None)
    assert recipe == baseline
    assert "private-original-832" not in json.dumps(recipe)
    assert "changed-secret-933" not in json.dumps(asdict(actual))


def test_matching_private_value_uses_deterministic_alias_not_value_digest():
    from autojev.services.rerun_recipe import required_recipe_inputs

    before = observation(tag="input", type="text", name="Search")
    recipes = [build_rerun_recipe(before, decision("fill", value=value), APP,
                                 credentials={"z_input": value, "a_input": value})
               for value in ("private-value-821", "another-private-921")]
    assert recipes[0] == recipes[1]
    assert recipes[0]["enabled"] is True
    assert required_recipe_inputs(recipes[0]) == ["a_input"]
    assert resolve_rerun_decision(recipes[0], before, credentials={"a_input": "fresh-value"}).value is None
    assert build_rerun_recipe(before, decision("fill", value="private-value-821"), APP)["enabled"] is True


@pytest.mark.parametrize("action", ["click", "select", "navigate", "login"])
def test_credential_substitution_is_supported_only_by_executor_fill(action):
    recipe = build_rerun_recipe(observation(), decision(action, credential_key="account"), APP,
                                credentials={"account": "private-777"})
    assert recipe["enabled"] is False
    assert recipe["reason"] == "credential_action"


@pytest.mark.parametrize("control", [
    {"enabled": False}, {"read_only": True}, {"type": "file"},
    {"type": "hidden"}, {"tag": "button"}, {"href": "https://evil.test"},
])
def test_vault_binding_does_not_enable_unsafe_fill_targets(control):
    attributes = {"tag": "input", "type": "text", **control}
    recipe = build_rerun_recipe(observation(**attributes), decision("fill", credential_key="account"),
                                APP, credentials={"account": "private-777"})
    assert recipe["reason"] == "unsafe_target"


def test_masked_nonempty_sensitive_fields_are_not_assumed_equal_to_vault():
    before = observation(tag="input", type="password", name="Password", state={"fields": [
        {"ref": "e1", "type": "password", "sensitive": True, "has_value": True},
    ]})
    recipe = build_rerun_recipe(before, decision("fill", credential_key="account"), APP,
                                credentials={"account": "private-777"})
    assert recipe["reason"] == "unportable_field_state"


def test_visible_secret_is_not_hashed_into_recipe():
    before = observation(tag="input", type="text", name="Search")
    before.text = "Account private-777"
    recipe = build_rerun_recipe(before, decision("fill", credential_key="account"), APP,
                                credentials={"account": "private-777"})
    assert recipe == {"version": "ct-rerun-v1", "enabled": False, "reason": "private_observation"}


@pytest.mark.parametrize("patch", [
    {"input_ref": "account", "value": "AutoJev QA"},
    {"input_ref": ""}, {"input_ref": None}, {"input_ref": "bad alias"},
    {"input_ref": "account", "action": "click"},
])
def test_malformed_recipe_input_references_are_rejected(patch):
    before = observation(tag="input", type="text", name="Search")
    recipe = build_rerun_recipe(before, decision("fill", credential_key="account"), APP,
                                credentials={"account": "private-777"})
    recipe["decision"].update(patch)
    with pytest.raises(RerunRecipeBlocked, match="invalid_input_reference"):
        validate_rerun_recipe(recipe, APP)


def test_missing_conflicting_and_unsafe_alias_inputs_are_explicit_gaps():
    before = observation(tag="input", type="text", name="Search")
    assert build_rerun_recipe(before, decision("fill", credential_key="account"), APP)["reason"] == "missing_recipe_input"
    assert build_rerun_recipe(before, decision("fill", credential_key="account", value="different"),
                              APP, credentials={"account": "private-777"})["reason"] == "conflicting_recipe_input"
    assert build_rerun_recipe(before, decision("fill", credential_key="private-777"), APP,
                              credentials={"private-777": "private-777"})["reason"] == "unsafe_input_reference"


@pytest.mark.parametrize("action,value", [("go_back", None), ("screenshot", None),
                                          ("wait", None), ("wait", "1200")])
def test_supported_journey_actions_and_opt_in_marker(action, value):
    original = AgentDecision(action=action, rationale="Replay", value=value)
    recipe = build_rerun_recipe(observation(), original, APP, capture_journey=True)
    assert recipe["enabled"] is True
    assert recipe["journey_capture"] is True
    actual = resolve_rerun_decision(recipe, observation(), APP)
    assert actual.action == action
    assert actual.value == (value or "500" if action == "wait" else None)
    assert "journey_capture" not in build_rerun_recipe(observation(), original, APP)
    disabled = build_rerun_recipe(observation(), decision("finish"), APP, capture_journey=True)
    assert disabled["journey_capture"] is True
    assert disabled["enabled"] is False


@pytest.mark.parametrize("value", ["-1", "5001", "1.5", "secret", "10000"])
def test_wait_duration_is_bounded(value):
    recipe = build_rerun_recipe(observation(), AgentDecision(action="wait", rationale="Wait", value=value), APP)
    assert recipe["reason"] == "unsupported_wait"


def test_public_fill_tab_preserves_executor_semantics():
    before = observation(tag="input", type="text", name="City")
    recipe = build_rerun_recipe(before, decision("fill", value="AutoJev QA city", key="Tab"), APP)
    assert recipe["enabled"] is True
    actual = resolve_rerun_decision(recipe, before, APP)
    assert actual.key == "Tab"
    assert actual.value == "AutoJev QA city"


def test_private_field_preconditions_require_fresh_executor_equality_attestation():
    from autojev.services.rerun_recipe import required_recipe_inputs

    before = observation(state={"fields": [
        {"ref": "e2", "type": "password", "sensitive": True, "has_value": True},
    ], "_rerun_field_bindings": {"e2": "password"}})
    before.interactive_elements.append({"ref": "e2", "tag": "input", "type": "password",
                                        "name": "Password", "has_value": True})
    recipe = build_rerun_recipe(before, decision(), APP, credentials={"password": "original-772"})
    assert recipe["enabled"] is True
    assert required_recipe_inputs(recipe) == ["password"]
    assert "original-772" not in json.dumps(recipe)
    fresh = copy.deepcopy(before)
    fresh.interactive_elements[1]["ref"] = "e19"
    fresh.page_state["fields"][0]["ref"] = "e19"
    fresh.page_state["_rerun_field_bindings"] = {"e19": "password"}
    assert resolve_rerun_decision(recipe, fresh, APP, credentials={"password": "new-882"}).action == "click"
    del fresh.page_state["_rerun_field_bindings"]
    with pytest.raises(RerunRecipeBlocked, match="unverified_recipe_input_state"):
        resolve_rerun_decision(recipe, fresh, APP, credentials={"password": "new-882"})
    with pytest.raises(RerunRecipeBlocked, match="missing_recipe_input"):
        resolve_rerun_decision(recipe, fresh, APP)


def test_executor_attests_only_exact_live_values_and_never_stores_raw_in_observation():
    from autojev.adapters.playwright_executor import PlaywrightExecutor

    executor = PlaywrightExecutor.__new__(PlaywrightExecutor)
    executor.secret_values = ["private-777"]
    executor._rerun_credentials = {"account": "private-777"}
    executor._field_value_token = lambda raw: "a" * 64
    executor._redact_text = lambda text: text
    state = executor._sanitize_page_state({"fields": [
        {"ref": "e1", "type": "password", "sensitive": True, "has_value": True,
         "_raw_value": "private-777"},
        {"ref": "e2", "type": "password", "sensitive": True, "has_value": True,
         "_raw_value": "different"},
    ]})
    assert state.rerun_field_bindings == {"e1": "account"}
    assert "_rerun_field_bindings" not in json.dumps(state)
    assert "rerun_field_bindings" not in json.dumps(asdict(observation(state=state)))
    assert "private-777" not in json.dumps(state)
    assert "value_token" not in json.dumps(state)
    executor._rerun_credentials = {"account": "changed-888"}
    state = executor._sanitize_page_state({"fields": [
        {"ref": "e1", "type": "password", "sensitive": True, "has_value": True,
         "_raw_value": "private-777"},
    ]})
    assert "_rerun_field_bindings" not in state


def test_private_fill_tab_preserves_credential_executor_semantics():
    before = observation(tag="input", type="password", name="Password")
    recipe = build_rerun_recipe(before, decision("fill", credential_key="account", key="Tab"),
                                APP, credentials={"account": "private-777"})
    assert recipe["enabled"] is True
    actual = resolve_rerun_decision(recipe, before, APP, credentials={"account": "private-777"})
    assert actual.credential_key == "account"
    assert actual.key == "Tab"
    assert actual.value is None


async def test_real_private_fill_then_click_replays_in_fresh_browser_without_secret_digests():
    from pathlib import Path
    from autojev.adapters.playwright_executor import PlaywrightExecutor
    from autojev.domain.models import ExplorationOptions

    playwright_api = pytest.importorskip("playwright.async_api")
    html = ('<title>Form</title><label>City<input id="city"></label>'
            '<button onclick="this.textContent=\'Opened\'">Open details</button>')
    credentials = {"city": "private-original-889"}
    recipes = []
    captured_tokens = []
    async with playwright_api.async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            for run in range(2):
                credentials = {"city": "private-original-889" if run == 0 else "private-rotated-990"}
                context = await browser.new_context()
                page = await context.new_page()
                await page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
                await page.goto(APP)
                executor = PlaywrightExecutor(Path("."), ExplorationOptions(capture_screenshots=False))
                executor._page, executor._context = page, context
                executor._allowed_origin = executor._origin(APP)
                try:
                    before = await executor.observe("initial")
                    if run == 0:
                        target = next(item["ref"] for item in before.interactive_elements if item["name"] == "City")
                        original = AgentDecision(action="fill", target=target, value=credentials["city"], rationale="Fill")
                        recipes.append(build_rerun_recipe(before, original, APP, credentials=credentials))
                    actual = resolve_rerun_decision(recipes[0], before, APP, credentials=credentials)
                    rebuilt_fill = build_rerun_recipe(before, actual, APP, credentials=credentials)
                    assert rebuilt_fill == recipes[0]
                    # Capture uses the literal input; replay uses the vault alias.
                    result, after = await executor.execute(original if run == 0 else actual, credentials)
                    assert result.success
                    assert getattr(after.page_state, "rerun_field_bindings")
                    assert "rerun_field_bindings" not in json.dumps(asdict(after))
                    token = executor._field_value_token(credentials["city"])
                    captured_tokens.append(token)
                    if run == 0:
                        assert result.evidence["business_input"]["expected_token"] == token
                        from autojev.services.business_checks import check_business_outcomes
                        checks = check_business_outcomes(before, original, result, after)
                        field_check = next(check for check in checks if check.kind == "field_value_matches")
                        assert field_check.passed
                        assert token not in json.dumps(asdict(field_check))
                    else:
                        assert "business_input" not in result.evidence
                        from autojev.services.business_checks import check_business_outcomes
                        checks = check_business_outcomes(before, actual, result, after)
                        field_check = next(check for check in checks if check.kind == "field_value_matches")
                        assert field_check.passed
                        assert field_check.expected["value_matches"] is True
                        assert field_check.actual["value_matches"] is True
                        assert token not in json.dumps(asdict(field_check))
                        # Mutate the real DOM, not a fabricated attestation. A
                        # fresh observation must fail the same durable check.
                        await page.locator("#city").fill("wrong-actual-private-771")
                        wrong = await executor.observe("wrong-value")
                        checks = check_business_outcomes(before, actual, result, wrong)
                        wrong_check = next(check for check in checks if check.kind == "field_value_matches")
                        assert not wrong_check.passed
                        assert wrong_check.actual["value_matches"] is False
                        with pytest.raises(RerunRecipeBlocked, match="unverified_recipe_input_state"):
                            resolve_rerun_decision(recipes[1], wrong, APP, credentials=credentials)
                        await page.locator("#city").fill(credentials["city"])
                        after = await executor.observe("restored-value")
                    if run == 0:
                        target = next(item["ref"] for item in after.interactive_elements if item["name"] == "Open details")
                        original = AgentDecision(action="click", target=target, rationale="Click")
                        recipes.append(build_rerun_recipe(after, original, APP, credentials=credentials))
                        assert recipes[1]["enabled"] is True
                    rebuilt_click = build_rerun_recipe(after, AgentDecision(
                        action="click", target=next(item["ref"] for item in after.interactive_elements
                                                    if item["name"] == "Open details"), rationale="Rebuild"),
                        APP, credentials=credentials)
                    assert rebuilt_click == recipes[1]
                    actual = resolve_rerun_decision(recipes[1], after, APP, credentials=credentials)
                    result, _ = await executor.execute(actual, credentials)
                    assert result.success
                finally:
                    await executor.close()
        finally:
            await browser.close()
    assert captured_tokens[0] != captured_tokens[1]
    serialized = json.dumps(recipes)
    assert "private-original-889" not in serialized
    assert "private-rotated-990" not in serialized
    assert all(token not in serialized for token in captured_tokens)
