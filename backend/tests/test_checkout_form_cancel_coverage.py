"""Checkout information cancellation is a native, finite coverage branch."""

from copy import deepcopy

import pytest

from autojev.domain.models import AgentDecision, Observation
from autojev.services.business_checks import check_business_outcomes
from autojev.services.coverage import CoveragePlanner
from test_form_probe_coverage import action_result, form_page


def checkout_form(populated=False):
    page = form_page(values=(True, True, True), cart_count=int(populated))
    page.page_state["cart"].update(items_known=True, items=[{"name": "Backpack", "price": "29.99"}] if populated else [])
    page.interactive_elements.append({"ref": "cancel", "tag": "button", "type": "button", "name": "Cancel",
                                      "enabled": True, "interactable": True,
                                      "semantic_identity": 'control:["button","id","cancel"]'})
    return page


def prepared(page):
    planner = CoveragePlanner(page.url)
    state = planner.states[planner.observe(page)]
    for key, move in state.candidates.items():
        if planner._move_action(move) == "fill":
            state.attempted.add(key)
            planner.global_route_moves_attempted.add((state.url, key))
        if move.get("_probe_target"):
            planner.form_probes_completed.add((state.url, planner._form_group(move), move["_probe_target"]))
    return planner, state


@pytest.mark.parametrize("populated", [False, True])
def test_checkout_form_cancel_precedes_departing_submit_after_required_negatives(populated):
    page = checkout_form(populated)
    planner, state = prepared(page)
    selected = planner.next_restoration_action(page)
    assert selected and selected[1] == "checkout_form_cancellation_prioritized"
    decision = selected[0]
    assert decision.target == "cancel"
    assert planner.restoration_target == state.key
    cart = Observation(url="https://app.test/cart", title="Cart", text="Your Cart",
                       interactive_elements=[{"ref": "next", "tag": "button", "name": "Checkout",
                                              "enabled": True, "interactable": True}],
                       page_state={"cart": deepcopy(page.page_state["cart"])})
    result = action_result(page, cart, decision)
    result.assertions.extend(check_business_outcomes(page, decision, result, cart))
    checks = {a.kind: a for a in result.assertions}
    assert checks["cart_persistence"].passed
    assert checks["checkout_form_cancel_progress"].passed
    planner.record_action(page, decision, result, cart)
    planner.observe(page)
    assert planner._checkout_form_exit_action(planner.states[planner.state_key(page)]) is None
    assert any(planner._is_form_submit(move) and not move.get("_probe_target")
               for _, move in planner._remaining(planner.states[planner.state_key(page)]))


def test_cancel_cannot_preempt_unexecuted_required_probe_or_unattempted_field():
    page = checkout_form()
    planner, state = prepared(page)
    planner.form_probes_completed.clear()
    assert planner._checkout_form_exit_action(state) is None
    for key, move in state.candidates.items():
        if move.get("_probe_target"):
            planner.form_probes_completed.add((state.url, planner._form_group(move), move["_probe_target"]))
    state.attempted.clear()
    planner.global_route_moves_attempted.clear()
    assert planner._checkout_form_exit_action(state) is None


def test_non_checkout_cancel_is_not_promoted_and_noop_cancellation_fails():
    page = checkout_form()
    planner, _ = prepared(page)
    decision = AgentDecision("click", "Test cancellation", target="cancel")
    result = action_result(page, page, decision)
    checks = {a.kind: a for a in check_business_outcomes(page, decision, result, page)}
    assert checks["checkout_form_cancel_progress"].passed is False
    other = deepcopy(page)
    other.url, other.title = "https://app.test/settings", "Settings"
    planner = CoveragePlanner(other.url)
    assert planner._checkout_form_exit_action(planner.states[planner.observe(other)]) is None


@pytest.mark.parametrize("populated", [False, True])
def test_checkout_cart_link_is_a_real_finite_exit_before_untried_positive_submit(populated):
    form = checkout_form(populated)
    form.interactive_elements.append({"ref": "cart-header", "tag": "a", "name": "Cart",
                                      "href": "https://app.test/cart", "raw_href": "/cart",
                                      "enabled": True, "interactable": True})
    form.page_state["cart"]["link_refs"] = ["cart-header"]
    planner, state = prepared(form)
    # Cancellation is still exercised separately and has priority; it does not
    # silently approve the distinct header-link handler.
    selected = planner.next_restoration_action(form)
    assert selected[0].target == "cancel"
    decision = planner._checkout_form_exit_action(state, role="cart_entry")
    assert decision.target == "cart-header"
    cart = Observation(url="https://app.test/cart", title="Cart", text="Your Cart",
                       interactive_elements=[], page_state={"cart": deepcopy(form.page_state["cart"])})
    result = action_result(form, cart, decision)
    result.assertions.extend(check_business_outcomes(form, decision, result, cart))
    # This observation has a known basket but no intent-ledger revision: the
    # native oracle truthfully names this check cart_contents_match.
    assert any(a.kind == "cart_contents_match" and a.passed for a in result.assertions)
    planner.record_action(form, decision, result, cart)
    fresh_state = planner.states[planner.observe(form)]
    assert planner._checkout_form_exit_action(fresh_state, role="cart_entry") is None
    assert any(planner._is_form_submit(move) and not move.get("_probe_target")
               for _, move in planner._remaining(fresh_state))
