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
    ("go_back", "unsupported_action"), ("wait", "unsupported_action"),
    ("screenshot", "unsupported_action"), ("finish", "unsupported_action"),
])
def test_history_and_unallowlisted_actions_are_explicitly_disabled(action, reason):
    recipe = build_rerun_recipe(observation(), decision(action), APP)
    assert recipe == {"version": "ct-rerun-v1", "enabled": False, "reason": reason}


@pytest.mark.parametrize("name, field_type", [
    ("Password", "password"), ("Username", "text"), ("API key", "text"),
    ("OTP token", "text"), ("Senha", "password"),
])
def test_credentials_and_sensitive_controls_never_create_a_recipe(name, field_type):
    recipe = build_rerun_recipe(observation(tag="input", type=field_type, name=name),
                                decision("fill", value="do-not-persist"), APP)
    assert recipe["enabled"] is False
    assert recipe["reason"] == "sensitive_target"
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
def test_fill_value_must_equal_a_deterministic_public_planner_input(value):
    before = observation(tag="input", type="text", name="City")
    recipe = build_rerun_recipe(before, decision("fill", value=value), APP)
    assert recipe["enabled"] is False
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
