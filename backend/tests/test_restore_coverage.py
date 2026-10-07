from __future__ import annotations

import pytest

from autojev.domain.models import ActionResult, AgentDecision, AssertionResult, Observation, utc_now
from autojev.services.coverage import CoveragePlanner, _MAX_REPLAY_FAILURES, _MAX_RESTORE_STEPS
from test_form_probe_coverage import action_result, apply_action, form_page


def page(url: str, elements: list[dict], *, populated: bool | None = None) -> Observation:
    state = ({"cart": {"items_known": True, "items": [{"name": "Item"}] if populated else []}}
             if populated is not None else {})
    return Observation(url=url, title="Application", text="", interactive_elements=elements, page_state=state)


def button(ref: str, name: str, **extra) -> dict:
    return {"ref": ref, "tag": "button", "name": name, "enabled": True,
            "interactable": True, **extra}


def anchor(ref: str, name: str, href: str, **extra) -> dict:
    return {"ref": ref, "tag": "a", "name": name, "href": href,
            "raw_href": href, "enabled": True, "interactable": True, **extra}


def outcome(before: Observation, after: Observation, *, success=True) -> ActionResult:
    return ActionResult(action="click", success=success, started_at=utc_now(),
                        finished_at=utc_now(), before_url=before.url, after_url=after.url)


def test_restore_cart_exit_via_live_anchor_without_recreating_obsolete_basket():
    current = page("https://app.test/catalog", [anchor("e91", "Cart, 3 items", "https://app.test/cart")], populated=True)
    historical = page("https://app.test/cart", [button("e4", "Continue Shopping"), button("e5", "Checkout")], populated=False)
    planner = CoveragePlanner(current.url)
    target_key = planner.observe(historical)
    planner.states[target_key].attempted.update(
        key for key, move in planner.states[target_key].candidates.items() if move["name"] == "Checkout"
    )
    planner.observe(current)
    planner.path = [planner.state_key(current)]
    planner.restoration_target = target_key

    decision, reason = planner.next_restoration_action(current)
    assert (decision.action, decision.target, reason) == ("click", "e91", "frontier_restore_route")
    assert decision.url is None  # never an unverified deep-link GET

    fresh = page(historical.url, [button("e101", "Continue Shopping"), button("e102", "Checkout")], populated=True)
    planner.record_action(current, decision, outcome(current, fresh), fresh)
    assert planner.restoration_target is None
    assert not planner._remaining(planner.states[target_key])
    assert any(move["name"] == "Continue Shopping" for _, move in planner._remaining(planner.states[planner.state_key(fresh)]))
    assert not planner.restrictions
    assert not planner.states[planner.state_key(fresh)].attempted  # live work still needs actual testing


def test_restore_checkout_route_across_cart_lane_variants_uses_fresh_checkout_ref():
    catalog = page("https://app.test/catalog", [anchor("e0", "Cart", "https://app.test/cart")], populated=False)
    old_cart = page("https://app.test/cart", [button("e1", "Checkout"), button("e2", "Continue Shopping")], populated=False)
    target = page("https://app.test/checkout", [button("e3", "Open Menu")], populated=False)
    planner = CoveragePlanner(catalog.url)
    planner.record_action(catalog, AgentDecision("click", "cart", target="e0"), outcome(catalog, old_cart), old_cart)
    planner.record_action(old_cart, AgentDecision("click", "checkout", target="e1"), outcome(old_cart, target), target)
    target_key = planner.state_key(target)
    live_cart = page(old_cart.url, [button("e80", "Checkout"), button("e81", "Continue Shopping")], populated=True)
    planner.observe(live_cart)
    planner.path = [planner.state_key(live_cart)]
    planner.restoration_target = target_key

    decision, reason = planner.next_restoration_action(live_cart)
    assert (decision.target, reason) == ("e80", "frontier_restore_route")
    assert decision.value is None and decision.credential_key is None
    fresh_checkout = page(target.url, [button("e90", "Open Menu"), button("e91", "Cancel")], populated=True)
    planner.record_action(live_cart, decision, outcome(live_cart, fresh_checkout), fresh_checkout)
    assert planner.restoration_target is None
    assert not planner.restrictions
    assert any(move["name"] == "Open Menu" for _, move in planner._remaining(planner.states[planner.state_key(fresh_checkout)]))


def test_cross_route_graph_uses_short_navigation_lane_instead_of_product_history():
    home = page("https://app.test/catalog", [anchor("e0", "Cart", "https://app.test/cart")])
    old_cart = page("https://app.test/cart", [button("e1", "Checkout")], populated=False)
    checkout = page("https://app.test/checkout", [button("e2", "Open Menu")])
    planner = CoveragePlanner(home.url)
    planner.record_action(home, AgentDecision("click", "cart", target="e0"), outcome(home, old_cart), old_cart)
    planner.record_action(old_cart, AgentDecision("click", "checkout", target="e1"), outcome(old_cart, checkout), checkout)
    # A rerender adds a control but does not invalidate the live navigation.
    live_home = page(home.url, [anchor("e70", "Cart", old_cart.url), button("e71", "New local control")])
    planner.observe(live_home)
    planner.path = [planner.state_key(live_home)]
    planner.restoration_target = planner.state_key(checkout)
    decision, reason = planner.next_restoration_action(live_home)
    assert (decision.target, reason) == ("e70", "frontier_restore_route")
    new_cart = page(old_cart.url, [button("e80", "Checkout")], populated=True)
    planner.record_action(live_home, decision, outcome(live_home, new_cart), new_cart)
    assert not planner.restoration_failures  # different cart snapshot is a successful route hop
    decision, reason = planner.next_restoration_action(new_cart)
    assert (decision.target, reason) == ("e80", "frontier_restore_route")


@pytest.mark.parametrize("extra", [{"raw_href": "#"}, {"raw_href": ""}, {"interactable": False}, {"enabled": False}])
def test_route_hop_rejects_placeholder_or_unavailable_anchors(extra):
    current = page("https://app.test/catalog", [anchor("e0", "Open cart", "https://app.test/cart", **extra)])
    target = page("https://app.test/cart", [button("e1", "Continue Shopping")])
    planner = CoveragePlanner(current.url)
    target_key = planner.observe(target)
    current_key = planner.observe(current)
    assert planner._route_restoration_hop(current_key, target_key) is None


def test_route_hop_does_not_use_mismatched_product_card_destination():
    catalog = page("https://app.test/catalog", [anchor("e0", "Item A", "https://app.test/products/a")])
    wrong = page("https://app.test/products/b", [button("e1", "Details")])
    planner = CoveragePlanner(catalog.url)
    planner.record_action(catalog, AgentDecision("click", "item", target="e0"), outcome(catalog, wrong), wrong)
    assert planner._route_restoration_hop(planner.state_key(catalog), planner.state_key(wrong)) is None


@pytest.mark.parametrize("raw_href", ["#", ""])
def test_route_hop_replays_only_observed_javascript_placeholder_destination(raw_href):
    catalog = page("https://app.test/catalog", [
        anchor("e0", "Open cart", "https://app.test/catalog#", raw_href=raw_href,
               semantic_identity='control:["a","id","cart-link"]'),
    ], populated=False)
    cart = page("https://app.test/cart", [button("e1", "Checkout")], populated=False)
    checkout = page("https://app.test/checkout", [button("e2", "Open Menu")])
    planner = CoveragePlanner(catalog.url)
    catalog_key = planner.observe(catalog)
    target_key = planner.observe(checkout)
    assert planner._route_restoration_hop(catalog_key, target_key) is None
    planner.record_action(catalog, AgentDecision("click", "observed cart entry", target="e0"),
                          outcome(catalog, cart), cart)
    planner.record_action(cart, AgentDecision("click", "observed checkout entry", target="e1"),
                          outcome(cart, checkout), checkout)
    live = page(catalog.url, [
        anchor("e80", "Open cart", "https://app.test/catalog#", raw_href=raw_href,
               semantic_identity='control:["a","id","cart-link"]'),
        button("e81", "New mounted control"),
    ], populated=True)
    live_key = planner.observe(live)
    planner.restoration_target = target_key
    decision, reason = planner._route_restoration_hop(live_key, target_key)
    assert (decision.target, decision.url, reason) == ("e80", None, "frontier_restore_route")
    # Reaching a prerequisite route never approves the checkout's pending work.
    fresh_cart = page(cart.url, [button("e90", "Checkout")], populated=True)
    planner.record_action(live, decision, outcome(live, fresh_cart), fresh_cart)
    assert planner.restoration_target == target_key
    assert planner._remaining(planner.states[target_key])


def test_bad_product_identity_is_not_learned_as_javascript_restore_route():
    catalog = page("https://app.test/catalog", [
        anchor("e0", "Product A", "https://app.test/catalog#", raw_href="#"),
    ])
    wrong = page("https://app.test/products/b", [button("e1", "Details")])
    planner = CoveragePlanner(catalog.url)
    result = outcome(catalog, wrong)
    result.assertions.append(AssertionResult("product_detail_identity", "A", "B", False, "Wrong product"))
    planner.record_action(catalog, AgentDecision("click", "failed product destination", target="e0"),
                          result, wrong)
    assert not planner.transitions.get(planner.state_key(catalog))
    assert planner._route_restoration_hop(planner.state_key(catalog), planner.state_key(wrong)) is None


def test_learned_placeholder_route_failure_is_still_bounded_without_approval():
    catalog = page("https://app.test/catalog", [
        anchor("e0", "Open cart", "https://app.test/catalog#", raw_href="#"),
    ])
    cart = page("https://app.test/cart", [button("e1", "Inspect cart")])
    planner = CoveragePlanner(catalog.url)
    planner.record_action(catalog, AgentDecision("click", "observed cart", target="e0"),
                          outcome(catalog, cart), cart)
    target_key = planner.state_key(cart)
    live = page(catalog.url, [
        anchor("e80", "Open cart", "https://app.test/catalog#", raw_href="#"),
        button("e81", "New state"),
    ])
    current_key = planner.observe(live)
    planner.restoration_target = target_key
    for _ in range(_MAX_REPLAY_FAILURES):
        decision, _ = planner._route_restoration_hop(current_key, target_key)
        planner.record_action(live, decision, outcome(live, live), live)
    assert planner._route_restoration_hop(current_key, target_key) is None
    assert planner._remaining(planner.states[target_key])
    assert not planner.states[target_key].attempted


def test_route_hop_does_not_submit_forms_or_replay_cart_mutation():
    current = page("https://app.test/form", [button("e0", "Continue", type="submit", form="f"),
                                            button("e1", "Add to cart", reversible_cart_action="add"),
                                            button("e3", "Add to cart"), button("e4", "Reset App State")])
    target = page("https://app.test/next", [button("e2", "Open Menu")])
    for ref in ("e0", "e1", "e3", "e4"):
        planner = CoveragePlanner(current.url)
        planner.record_action(current, AgentDecision("click", "unsafe prerequisite", target=ref), outcome(current, target), target)
        assert planner._route_restoration_hop(planner.state_key(current), planner.state_key(target)) is None


def learned_checkout(*, native_progress=True, synthetic=True):
    form = form_page(values=(False, False, False), cart_count=1)
    planner = CoveragePlanner(form.url)
    planner.observe(form)
    if synthetic:
        for ref in ("e0", "e1", "e2"):
            field = next(move for move in planner.states[planner.current_key].candidates.values()
                         if move.get("ref") == ref and planner._move_action(move) == "fill")
            decision = AgentDecision("fill", "Synthetic checkout setup", target=ref,
                                     value=planner._move_value(field))
            after, result = apply_action(form, decision, broken_ref=None)
            planner.record_action(form, decision, result, after)
            form = after
    review = page("https://app.test/checkout-step-two", [button("finish", "Finish")], populated=True)
    submit = AgentDecision("click", "Continue checkout", target="submit")
    result = action_result(form, review, submit)
    if native_progress is not None:
        result.assertions.append(AssertionResult("form_progress", "review", review.url,
                                                 native_progress, "Observed checkout progress"))
    planner.record_action(form, submit, result, review)
    return planner, review


def test_verified_checkout_restore_fills_fresh_form_before_replaying_continue():
    planner, review = learned_checkout()
    fresh = form_page(values=(False, False, False), cart_count=1)
    # References are observation-local, so restoration must bind them again.
    for item in fresh.interactive_elements:
        item["ref"] = "fresh-" + item["ref"]
    for field in fresh.page_state["fields"]:
        field["ref"] = "fresh-" + field["ref"]
    fresh.page_state["forms"][0]["submit_refs"] = ["fresh-submit"]
    target_key = planner.state_key(review)
    planner.restoration_target = target_key
    selected_refs = []
    for _ in range(3):
        current_key = planner.observe(fresh)
        selected = planner._route_restoration_hop(current_key, target_key)
        assert selected and selected[1] == "frontier_restore_form_setup"
        decision = selected[0]
        assert decision.action == "fill" and decision.value
        assert decision.credential_key is None
        selected_refs.append(decision.target)
        after, result = apply_action(fresh, decision, broken_ref=None)
        planner.record_action(fresh, decision, result, after)
        fresh = after
    assert set(selected_refs) == {"fresh-e0", "fresh-e1", "fresh-e2"}
    decision, reason = planner._route_restoration_hop(planner.observe(fresh), target_key)
    assert (decision.action, decision.target, reason) == ("click", "fresh-submit", "frontier_restore_route")
    assert not planner.states[target_key].attempted  # reaching review does not approve Finish


@pytest.mark.parametrize("native_progress,synthetic", [(None, True), (False, True), (True, False)])
def test_checkout_route_shortcut_requires_native_progress_and_synthetic_setup(native_progress, synthetic):
    planner, review = learned_checkout(native_progress=native_progress, synthetic=synthetic)
    fresh = form_page(values=(False, False, False), cart_count=1)
    assert planner._route_restoration_hop(planner.observe(fresh), planner.state_key(review)) is None


def test_checkout_route_shortcut_rejects_new_required_field_without_proven_setup():
    planner, review = learned_checkout()
    fresh = form_page(values=(False, False, False), cart_count=1)
    fresh.interactive_elements.append({"ref": "unknown", "tag": "input", "type": "text",
                                      "name": "Delivery code", "form": "checkout", "required": True,
                                      "has_value": False, "enabled": True, "interactable": True})
    assert planner._route_restoration_hop(planner.observe(fresh), planner.state_key(review)) is None


def test_historical_form_setup_debt_ends_when_its_real_submit_is_exercised():
    planner, _ = learned_checkout()
    old_blank = form_page(values=(True, True, False), cart_count=1)
    old_blank.interactive_elements.append(button("dismiss", "Dismiss error"))
    old_state = planner.states[planner.observe(old_blank)]
    postal_key = next(move_key for move_key, move in old_state.candidates.items()
                      if move.get("ref") == "e2" and planner._move_action(move) == "fill")
    # This snapshot owed setup while Continue was untried. The genuine
    # continuation has since been executed from another live DOM variant.
    old_state.form_setup_required.add(postal_key)
    planner.form_probes_completed.update(
        (old_state.url, planner._form_group(move), move["_probe_target"])
        for move in old_state.candidates.values() if move.get("_probe_target")
    )
    assert postal_key not in {key for key, _ in planner._remaining(old_state)}
    assert any(move["name"] == "Dismiss error" for _, move in planner._remaining(old_state))


def test_live_form_setup_remains_required_until_actual_submit_was_attempted():
    form = form_page(values=(True, True, False), cart_count=1)
    planner = CoveragePlanner(form.url)
    state = planner.states[planner.observe(form)]
    postal_key = next(move_key for move_key, move in state.candidates.items()
                      if move.get("ref") == "e2" and planner._move_action(move) == "fill")
    state.attempted.add(postal_key)
    planner.global_route_moves_attempted.add((state.url, postal_key))
    state.form_setup_required.add(postal_key)
    assert postal_key in {key for key, _ in planner._remaining(state)}


def test_blocked_setup_field_cannot_reappear_as_repeated_restoration_debt():
    form = form_page(values=(True, True, False), cart_count=1)
    planner = CoveragePlanner(form.url)
    state = planner.states[planner.observe(form)]
    key = next(move_key for move_key, move in state.candidates.items()
               if move.get("ref") == "e2" and planner._move_action(move) == "fill")
    state.form_setup_required.add(key)
    state.attempted.add(key)
    state.blocked.add(key)
    assert key not in {move_key for move_key, _ in planner._remaining(state)}
    assert key in state.blocked  # the restriction was not converted to PASS


def test_route_local_menu_opener_is_exercised_before_populated_cart_departure():
    catalog = page("https://app.test/catalog", [button("open", "Open Menu"),
                                              anchor("cart", "Cart", "https://app.test/cart")], populated=True)
    planner = CoveragePlanner(catalog.url)
    decision, reason = planner.next_restoration_action(catalog)
    assert (decision.target, reason) == ("open", "route_local_menu_prioritized")
    opened = page(catalog.url, [button("open", "Open Menu", aria_expanded=True),
                              button("close", "Close Menu", in_navigation=True),
                              anchor("cart", "Cart", "https://app.test/cart")], populated=True)
    planner.record_action(catalog, decision, outcome(catalog, opened), opened)
    selected = planner.next_restoration_action(opened)
    assert selected is None or selected[0].target != "open"


def test_route_local_menu_opener_cannot_preempt_ordinary_form_field_setup():
    form = form_page(values=(False, False, False), cart_count=1)
    form.interactive_elements.append(button("open", "Open Menu"))
    planner = CoveragePlanner(form.url)
    state = planner.states[planner.observe(form)]
    assert planner._local_menu_opener_action(state) is None


def test_route_shortcut_cannot_skip_later_product_add_prerequisite_for_cart_remove():
    catalog = page("https://app.test/catalog", [anchor("cart", "Cart", "https://app.test/cart"),
                                              anchor("detail", "Bike", "https://app.test/product?id=0")], populated=False)
    detail = page("https://app.test/product?id=0", [button("add", "Add to cart", reversible_cart_action="add"),
                                                  anchor("cart", "Cart", "https://app.test/cart")], populated=False)
    selected_detail = page(detail.url, [button("remove", "Remove", reversible_cart_action="remove"),
                                       anchor("cart", "Cart", "https://app.test/cart")], populated=True)
    cart = page("https://app.test/cart", [button("remove-cart", "Remove", reversible_cart_action="remove")], populated=True)
    planner = CoveragePlanner(catalog.url)
    for before, after, ref in ((catalog, detail, "detail"), (detail, selected_detail, "add"),
                               (selected_detail, cart, "cart")):
        planner.record_action(before, AgentDecision("click", "Observed safe prerequisite", target=ref),
                              outcome(before, after), after)
    planner.observe(catalog)
    planner.path = [planner.state_key(catalog)]
    planner.restoration_target = planner.state_key(cart)
    decision, reason = planner.next_restoration_action(catalog)
    assert (decision.target, reason) == ("detail", "frontier_restore_replay")
    assert not planner.states[planner.state_key(cart)].attempted
    planner.record_action(catalog, decision, outcome(catalog, detail), detail)
    decision, reason = planner.next_restoration_action(detail)
    assert (decision.target, reason) == ("add", "frontier_restore_replay")


def shared_drawer_states():
    closed = page("https://app.test/detail", [button("open-old", "Open Menu", aria_expanded=False)])
    opened = page(closed.url, [button("open-hidden", "Open Menu", aria_expanded=True),
                              button("close-fresh", "Close Menu", in_navigation=True)])
    planner = CoveragePlanner("https://app.test/catalog")
    target_key = planner.observe(closed)
    current_key = planner.observe(opened)
    # Closing the shared drawer was already covered elsewhere, but here it
    # must still be usable as local setup for an untested opener.
    for move_key, move in planner.states[current_key].candidates.items():
        if move["name"] == "Close Menu":
            planner.global_navigation_attempted.add(move_key)
    planner.path = [current_key]
    planner.restoration_target = target_key
    return planner, closed, opened, target_key


def test_shared_open_drawer_does_not_make_same_route_opener_unreachable():
    planner, closed, opened, target_key = shared_drawer_states()
    selected = planner.next_restoration_action(opened)
    assert selected and selected[1] == "frontier_restore_menu_setup"
    decision = selected[0]
    assert (decision.action, decision.target) == ("click", "close-fresh")
    assert not planner.states[target_key].attempted
    fresh = page(closed.url, [button("open-fresh", "Open Menu", aria_expanded=False)])
    planner.record_action(opened, decision, outcome(opened, fresh), fresh)
    assert planner.restoration_target is None
    remaining = planner._remaining(planner.states[planner.state_key(fresh)])
    assert len(remaining) == 1 and remaining[0][1]["ref"] == "open-fresh"
    assert not planner.restrictions  # setup did not fabricate opener approval


def test_shared_drawer_setup_noop_is_bounded_and_keeps_opener_restriction():
    planner, _, opened, target_key = shared_drawer_states()
    for _ in range(_MAX_REPLAY_FAILURES):
        decision, reason = planner.next_restoration_action(opened)
        assert reason == "frontier_restore_menu_setup"
        planner.record_action(opened, decision, outcome(opened, opened), opened)
    assert planner.next_restoration_action(opened) is None
    assert planner.states[target_key].blocked
    assert planner.restrictions[0]["reason"] == "no_safe_restore_route"


def test_drawer_setup_requires_unique_available_close_not_an_arbitrary_dismissal():
    for kind in ("ambiguous", "unavailable", "modal"):
        planner, _, opened, _ = shared_drawer_states()
        if kind == "ambiguous":
            opened.interactive_elements.append(button("other-close", "Close Menu", context="another drawer"))
            key = planner.observe(opened)
            for move_key in planner.states[key].candidates:
                planner.global_navigation_attempted.add(move_key)
        elif kind == "unavailable":
            opened.interactive_elements[-1]["interactable"] = False
        else:
            opened.interactive_elements[-1]["name"] = "Dismiss Dialog"
        selected = planner.next_restoration_action(opened)
        assert selected is None or selected[1] != "frontier_restore_menu_setup"


def test_route_hop_preserves_external_and_focused_boundaries():
    current = page("https://app.test/orders", [anchor("e0", "Cart", "https://app.test/cart"),
                                              anchor("e1", "Partner", "https://partner.test/cart")])
    for target_url in ("https://app.test/cart", "https://partner.test/cart"):
        target = page(target_url, [button("e2", "Open Menu")])
        planner = CoveragePlanner(current.url, focus_area="Orders")
        current_key = planner.observe(current)
        target_key = planner.observe(target)
        assert planner._route_restoration_hop(current_key, target_key) is None


def test_route_hop_does_not_return_to_pre_authentication_entry():
    dashboard = page("https://app.test/dashboard", [anchor("e0", "Home", "https://app.test/login")])
    login = page("https://app.test/login", [button("e1", "Help")])
    planner = CoveragePlanner(login.url)
    current_key = planner.observe(dashboard)
    target_key = planner.observe(login)
    assert planner._route_restoration_hop(current_key, target_key) is None


def test_wrong_destination_hop_is_bounded_and_not_accepted_as_restore():
    current = page("https://app.test/catalog", [anchor("e0", "Cart", "https://app.test/cart")])
    target = page("https://app.test/cart", [button("e1", "Continue Shopping")])
    planner = CoveragePlanner(current.url)
    target_key = planner.observe(target)
    current_key = planner.observe(current)
    planner.restoration_target = target_key
    for _ in range(_MAX_REPLAY_FAILURES):
        decision, reason = planner._route_restoration_hop(current_key, target_key)
        assert reason == "frontier_restore_route"
        planner.record_action(current, decision, outcome(current, current), current)
        assert planner.restoration_target == target_key
    assert planner._route_restoration_hop(current_key, target_key) is None
    assert planner.restoration_steps == _MAX_REPLAY_FAILURES
    assert planner._remaining(planner.states[target_key])  # not mislabeled covered


def test_route_hop_obeys_existing_overall_restore_step_limit():
    current = page("https://app.test/catalog", [anchor("e0", "Cart", "https://app.test/cart")])
    target = page("https://app.test/cart", [button("e1", "Continue Shopping")])
    planner = CoveragePlanner(current.url)
    target_key = planner.observe(target)
    planner.observe(current)
    planner.restoration_target = target_key
    planner.restoration_steps = _MAX_RESTORE_STEPS
    assert planner._continue_restoration(planner.state_key(current)) is None
    assert planner.restrictions[0]["reason"] == "restore_step_limit"


def test_backtrack_skips_covered_ancestors_instead_of_replaying_navigation_cycle():
    catalog = page("https://app.test/catalog", [anchor("e0", "Cart", "https://app.test/cart")])
    cart = page("https://app.test/cart", [button("e1", "Checkout")])
    review = page("https://app.test/review", [button("e2", "Cancel")])
    pending = page("https://app.test/help", [button("e3", "Read help")])
    planner = CoveragePlanner(catalog.url)
    planner.record_action(catalog, AgentDecision("click", "cart", target="e0"), outcome(catalog, cart), cart)
    planner.record_action(cart, AgentDecision("click", "checkout", target="e1"), outcome(cart, review), review)
    planner.record_action(review, AgentDecision("click", "cancel", target="e2"), outcome(review, cart), cart)
    planner.observe(pending)
    planner.observe(review)
    # A covered predecessor with an observed forward replay route must not
    # restart a fresh restoration budget merely to unwind the route stack.
    planner.path = [planner.state_key(catalog), planner.state_key(cart), planner.state_key(review)]
    assert planner._find_replay_path(planner.state_key(review), planner.state_key(cart))
    assert not planner._remaining(planner.states[planner.state_key(cart)])
    assert planner._backtrack_decision() is None
    assert planner.path == [planner.state_key(review)]
    assert planner.restrictions[0]["target_url"] == pending.url
    assert planner.restrictions[0]["reason"] == "no_safe_restore_route"
    assert planner.can_finish()


def test_backtrack_retains_pending_ancestor_and_does_not_skip_its_real_work():
    catalog = page("https://app.test/catalog", [anchor("e0", "Cart", "https://app.test/cart"),
                                              button("e4", "Untried category")])
    cart = page("https://app.test/cart", [button("e1", "Checkout")])
    review = page("https://app.test/review", [])
    planner = CoveragePlanner(catalog.url)
    planner.record_action(catalog, AgentDecision("click", "cart", target="e0"), outcome(catalog, cart), cart)
    planner.record_action(cart, AgentDecision("click", "checkout", target="e1"), outcome(cart, review), review)
    selected = planner._backtrack_decision()
    assert selected[0].action == "go_back"
    assert planner._issued_restore_step.expected_key == planner.state_key(catalog)
    assert planner.path == [planner.state_key(catalog), planner.state_key(review)]
    assert any(move["name"] == "Untried category"
               for _, move in planner._remaining(planner.states[planner.state_key(catalog)]))
    assert not planner.restrictions


def test_same_route_live_work_preempts_known_obsolete_state_replay_without_approval():
    historical = page("https://app.test/cart", [button("e1", "Continue Shopping"),
                                             button("e2", "Inspect old item")])
    fresh = page(historical.url, [button("e90", "Recreate old basket"),
                                button("e91", "Continue Shopping"),
                                button("e92", "Inspect current item")])
    planner = CoveragePlanner(historical.url)
    planner.record_action(fresh, AgentDecision("click", "known old recipe", target="e90"),
                          outcome(fresh, historical), historical)
    target_key = planner.state_key(historical)
    current_key = planner.observe(fresh)
    planner.restoration_target = target_key
    assert planner._live_replay_path(current_key, target_key)
    assert not planner._matches_pending_state(current_key, target_key)
    assert planner._continue_restoration(current_key) is None
    assert planner.restoration_target is None
    assert not planner.states[target_key].inherited
    assert {move["name"] for _, move in planner._remaining(planner.states[target_key])} == {
        "Continue Shopping", "Inspect old item",
    }
    chosen, _ = planner.reconcile(fresh, AgentDecision("finish", "try current work"))
    assert chosen.action == "click" and chosen.target in {"e91", "e92"}
    assert not planner.restrictions


def test_route_shortcut_does_not_skip_known_same_route_business_prerequisite():
    empty_catalog = page("https://app.test/catalog", [anchor("e0", "Cart, empty", "https://app.test/cart"),
                                                     button("e1", "Add to cart", context="article Item A")], populated=False)
    full_catalog = page(empty_catalog.url, [anchor("e90", "Cart, 1 item", "https://app.test/cart"),
                                           button("e91", "Remove", context="article Item A", reversible_cart_action="remove")], populated=True)
    full_cart = page("https://app.test/cart", [button("e2", "Inspect Item A"),
                                             button("e3", "Continue Shopping")], populated=True)
    planner = CoveragePlanner(empty_catalog.url)
    planner.record_action(empty_catalog, AgentDecision("click", "actual add", target="e1"),
                          outcome(empty_catalog, full_catalog), full_catalog)
    planner.record_action(full_catalog, AgentDecision("click", "actual cart", target="e90"),
                          outcome(full_catalog, full_cart), full_cart)
    current_key = planner.observe(empty_catalog)
    target_key = planner.state_key(full_cart)
    planner.path = [current_key]
    planner.restoration_target = target_key
    assert len(planner._live_replay_path(current_key, target_key)) == 2

    decision, reason = planner._continue_restoration(current_key)
    assert (decision.target, reason) == ("e1", "frontier_restore_replay")
    # A mounted Cart link does not prove the desired item precondition exists.
    assert decision.target != "e0"
    assert planner._remaining(planner.states[target_key])
    planner.record_action(empty_catalog, decision, outcome(empty_catalog, full_catalog), full_catalog)
    decision, reason = planner._continue_restoration(planner.state_key(full_catalog))
    assert (decision.target, reason) == ("e90", "frontier_restore_replay")
    planner.record_action(full_catalog, decision, outcome(full_catalog, full_cart), full_cart)
    assert planner.restoration_target is None
    assert not planner.restrictions
    assert len(planner._remaining(planner.states[target_key])) == 2  # reached, not executed/approved


def test_live_replay_prefers_shorter_observed_path_without_approving_target_work():
    current = page("https://app.test/catalog", [button("e1", "Open report"),
                                               button("e2", "Open reports index")])
    historical = page(current.url, [button("e80", "Open report"),
                                    button("e81", "Open reports index"),
                                    button("e82", "Old unrelated control")])
    index = page("https://app.test/reports", [button("e3", "Open report")])
    target = page("https://app.test/report", [button("e4", "Verify report")])
    planner = CoveragePlanner(current.url)
    planner.record_action(current, AgentDecision("click", "observed detour", target="e2"),
                          outcome(current, index), index)
    planner.record_action(index, AgentDecision("click", "actual report", target="e3"),
                          outcome(index, target), target)
    planner.record_action(historical, AgentDecision("click", "observed direct control", target="e80"),
                          outcome(historical, target), target)
    current_key = planner.observe(current)
    target_key = planner.state_key(target)
    assert len(planner._find_replay_path(current_key, target_key)) == 2

    path = planner._live_replay_path(current_key, target_key)
    assert len(path) == 1
    decision = planner._replay_decision(path[0], target_key, current_key=current_key)
    assert decision.target == "e1"  # resolve a fresh ref, never historical e80
    assert planner._remaining(planner.states[target_key])
    assert not planner.states[target_key].attempted
    assert not planner.restrictions

    # A previously failed short recipe remains disallowed; the known longer
    # route is still eligible instead of removing the target's coverage debt.
    failure_key = ("replay", target_key, planner._recipe_id(path[0]))
    planner.restoration_failures[failure_key] = _MAX_REPLAY_FAILURES
    assert len(planner._live_replay_path(current_key, target_key)) == 2
    assert planner._remaining(planner.states[target_key])


def test_detail_cart_action_does_not_approve_same_named_catalog_surface():
    shared = {"semantic_identity": 'owner:["article","data-product-id","0"]'}
    catalog = page("https://app.test/catalog", [button("e1", "Add to cart", **shared),
                                              anchor("e2", "View Item", "https://app.test/detail?id=0")])
    detail = page("https://app.test/detail?id=0", [button("e80", "Add to cart", **shared)])
    planner = CoveragePlanner(catalog.url)
    planner.record_action(catalog, AgentDecision("click", "actual detail navigation", target="e2"),
                          outcome(catalog, detail), detail)
    # The declared row/control identity can legitimately be reused on another
    # page. Its native test still belongs to that page, not to every ancestor
    # exposing the same product and action label.
    planner.record_action(detail, AgentDecision("click", "actual detail add", target="e80"),
                          outcome(detail, detail), detail)
    catalog_state = planner.states[planner.state_key(catalog)]
    detail_state = planner.states[planner.state_key(detail)]
    assert any(move["name"] == "Add to cart" for _, move in planner._remaining(catalog_state))
    assert not planner._remaining(detail_state)
    assert not planner.restrictions


def test_new_route_does_not_inherit_classified_control_from_preceding_page():
    shared = {"semantic_identity": 'control:["button","id","primary-action"]'}
    first = page("https://app.test/first", [button("e1", "Save", **shared)])
    second = page("https://app.test/second", [button("e90", "Save", **shared)])
    planner = CoveragePlanner(first.url)
    planner.record_action(first, AgentDecision("click", "actual first-page save", target="e1"),
                          outcome(first, second), second)
    first_state = planner.states[planner.state_key(first)]
    second_state = planner.states[planner.state_key(second)]
    assert not planner._remaining(first_state)
    assert any(move["name"] == "Save" for _, move in planner._remaining(second_state))
    assert not second_state.inherited
    assert not second_state.attempted
    assert not planner.restrictions


def wrong_product_route(*, card_tag="button", target_url="https://app.test/detail?id=2"):
    card = (anchor("card", "View Shirt", "https://app.test/detail?id=1")
            if card_tag == "a" else button("card", "View Shirt"))
    catalog = page("https://app.test/catalog", [card])
    catalog.page_state["products"] = [{"id": "shirt", "name": "Shirt", "price": "15.99",
                                       "link_refs": ["card"]}]
    actual = page(target_url, [button("open", "Open Menu"),
                               button("back", "Back to products")])
    actual.page_state["product_detail"] = {"id": "onesie", "name": "Onesie", "price": "7.99"}
    decision = AgentDecision("click", "Actually observed product card", target="card")
    result = outcome(catalog, actual)
    result.assertions = [
        AssertionResult("product_detail_identity", "Shirt", "Onesie", False, "Wrong actual product"),
        AssertionResult("product_detail_price", 15.99, 7.99, False, "Wrong actual price"),
    ]
    if card_tag == "a":
        result.assertions.append(AssertionResult("navigation_destination", card["href"], target_url,
                                                False, "Wrong actual anchor destination"))
    return catalog, actual, decision, result


@pytest.mark.parametrize("card_tag", ["button", "a"])
def test_failed_product_card_retains_only_its_actual_native_route_and_fail_verdict(card_tag):
    catalog, actual, decision, result = wrong_product_route(card_tag=card_tag)
    planner = CoveragePlanner(catalog.url)
    planner.record_action(catalog, decision, result, actual)
    root_key, target_key = planner.state_key(catalog), planner.state_key(actual)
    recipe = planner.transitions[root_key][0]
    assert recipe.child_key == target_key
    assert recipe.observed_wrong_product_destination is True
    assert not recipe.checkout_submit_verified and not recipe.setup
    assert all(check.passed is False for check in result.assertions)
    assert not planner.states[target_key].attempted
    assert {move["name"] for _, move in planner._remaining(planner.states[target_key])} == {
        "Open Menu", "Back to products",
    }

    # Fresh refs on the already-observed source route are used, never a GET of
    # either the intended or erroneous detail URL or a guessed corrected card.
    fresh = page(catalog.url, [{**catalog.interactive_elements[0], "ref": "card-fresh"},
                               button("help", "Help")])
    fresh.page_state["products"] = [{**catalog.page_state["products"][0], "link_refs": ["card-fresh"]}]
    current_key = planner.observe(fresh)
    planner.path = [current_key]
    planner.restoration_target = target_key
    if card_tag == "a":
        hop = planner._route_restoration_hop(current_key, target_key)
        assert hop and hop[0].target == "card-fresh"
        assert hop[0].url is None and hop[0].credential_key is None
        planner._issued_restore_step = None  # inspect, then exercise native replay
    resumed, reason = planner._continue_restoration(current_key)
    assert (resumed.action, resumed.target, resumed.url, reason) == (
        "click", "card-fresh", None, "frontier_restore_replay",
    )
    second = outcome(fresh, actual)
    second.assertions = list(result.assertions)
    planner.record_action(fresh, resumed, second, actual)
    assert not planner.restrictions
    assert planner.restoration_target is None
    assert all(check.passed is False for check in second.assertions)
    assert {move["name"] for _, move in planner._remaining(planner.states[target_key])} == {
        "Open Menu", "Back to products",
    }
    opener, _ = planner.next_restoration_action(actual)
    assert opener.target == "open"  # mounted work remains native/executable
    opened = page(actual.url, [button("close", "Close Menu"), button("back", "Back to products")])
    opened.page_state["product_detail"] = dict(actual.page_state["product_detail"])
    planner.record_action(actual, opener, outcome(actual, opened), opened)
    close, _ = planner.reconcile(opened, AgentDecision("click", "Actual menu close", target="close"))
    assert close.target == "close"
    planner.record_action(opened, close, outcome(opened, actual), actual)
    back, _ = planner.reconcile(actual, AgentDecision("click", "Actual detail's back control", target="back"))
    assert back.target == "back" and back.url is None
    planner.record_action(actual, back, outcome(actual, catalog), catalog)
    assert not planner._remaining(planner.states[target_key])
    assert all(check.passed is False for check in result.assertions)


@pytest.mark.parametrize("duplicate_identity", ["alias", "different_id", "different_provenance"])
def test_wrong_product_cart_card_collapses_only_exact_physical_row_aliases(duplicate_identity):
    catalog, actual, decision, result = wrong_product_route()
    catalog.url = "https://app.test/cart"
    result.before_url = catalog.url
    row = {**catalog.page_state["products"][0], "identity_source": "declared_product_id"}
    catalog.page_state["products"] = [dict(row)]
    alias = dict(row)
    if duplicate_identity == "different_id":
        alias["id"] = "another-homonymous-shirt"
    elif duplicate_identity == "different_provenance":
        alias["identity_source"] = "observed_name_id"
    catalog.page_state["cart"] = {"items": [alias], "items_known": True}
    planner = CoveragePlanner(catalog.url)
    planner.record_action(catalog, decision, result, actual)
    recipes = planner.transitions.get(planner.state_key(catalog), [])
    assert bool(recipes) is (duplicate_identity == "alias")
    assert all(not assertion.passed for assertion in result.assertions)


def test_wrong_anchor_without_product_failure_never_teaches_declared_href_another_destination():
    catalog, actual, decision, result = wrong_product_route(card_tag="a")
    result.assertions = [result.assertions[-1]]
    planner = CoveragePlanner(catalog.url)
    planner.record_action(catalog, decision, result, actual)
    root_key, target_key = planner.state_key(catalog), planner.state_key(actual)
    assert not planner.transitions.get(root_key)
    # Even an unflagged, otherwise safe learned click cannot grant the special
    # declared-href mismatch shortcut.
    planner._register_transition(root_key, target_key, planner.decision_key(catalog, decision), decision)
    assert planner._route_restoration_hop(planner.observe(catalog), target_key) is None


@pytest.mark.parametrize("kind", ["url_equals", "url_contains", "form_progress"])
def test_failed_product_recovery_does_not_relax_other_failed_transition_contracts(kind):
    catalog, actual, decision, result = wrong_product_route()
    result.assertions.append(AssertionResult(kind, "Expected", "Actual", False, "Independent failed contract"))
    planner = CoveragePlanner(catalog.url)
    planner.record_action(catalog, decision, result, actual)
    assert not planner.transitions.get(planner.state_key(catalog))
    assert not result.assertions[-1].passed


@pytest.mark.parametrize("unsafe", ["native_failure", "cross_origin", "credential", "source_form",
                                  "unexpected_form", "password", "password_field", "sensitive_field",
                                  "missing_product", "unobserved_card", "auth_entry"])
def test_wrong_product_edge_rejects_native_failure_forms_credentials_and_foreign_routes(unsafe):
    catalog, actual, decision, result = wrong_product_route()
    start = catalog.url
    if unsafe == "native_failure":
        result.success = False
    elif unsafe == "cross_origin":
        actual.url = "https://foreign.test/detail?id=2"
        result.after_url = actual.url
    elif unsafe == "credential":
        decision.credential_key = "username"
    elif unsafe == "source_form":
        catalog.interactive_elements[0]["form"] = "checkout"
    elif unsafe == "unexpected_form":
        actual.page_state["forms"] = [{"id": "checkout", "submit_refs": ["submit"]}]
    elif unsafe == "password":
        actual.interactive_elements.append({"ref": "secret", "tag": "input", "type": "password", "sensitive": True})
    elif unsafe == "password_field":
        actual.page_state["fields"] = [{"ref": "secret", "type": "password"}]
    elif unsafe == "sensitive_field":
        actual.page_state["fields"] = [{"ref": "secret", "type": "text", "sensitive": True}]
    elif unsafe == "missing_product":
        actual.page_state.pop("product_detail")
    elif unsafe == "unobserved_card":
        catalog.page_state.pop("products")
    elif unsafe == "auth_entry":
        start = "https://app.test/login"
        actual.url = start
        result.after_url = start
    planner = CoveragePlanner(start)
    planner.record_action(catalog, decision, result, actual)
    assert not planner.transitions.get(planner.state_key(catalog))
    assert not result.assertions[0].passed


@pytest.mark.parametrize("same_origin", [True, False])
def test_price_only_failure_keeps_actual_native_route_only_within_origin(same_origin):
    catalog, actual, decision, result = wrong_product_route()
    result.assertions = [result.assertions[1]]
    if not same_origin:
        actual.url = "https://foreign.test/detail?id=2"
        result.after_url = actual.url
    planner = CoveragePlanner(catalog.url)
    planner.record_action(catalog, decision, result, actual)
    recipes = planner.transitions.get(planner.state_key(catalog), [])
    assert bool(recipes) is same_origin
    if recipes:
        assert not recipes[0].observed_wrong_product_destination
    assert not result.assertions[0].passed


def test_wrong_product_native_replay_still_stops_after_two_wrong_actual_destinations():
    catalog, actual, decision, result = wrong_product_route()
    planner = CoveragePlanner(catalog.url)
    planner.record_action(catalog, decision, result, actual)
    target_key = planner.state_key(actual)
    unexpected = page("https://app.test/another-detail", [button("back", "Back to products")])
    unexpected.page_state["product_detail"] = {"name": "A different wrong item"}
    planner.restoration_target = target_key
    for _ in range(_MAX_REPLAY_FAILURES):
        planner.observe(catalog)
        resumed, reason = planner._continue_restoration(planner.state_key(catalog))
        assert resumed.target == "card" and reason == "frontier_restore_replay"
        failed = outcome(catalog, unexpected)
        failed.assertions = list(result.assertions)
        planner.record_action(catalog, resumed, failed, unexpected)
        assert all(not check.passed for check in failed.assertions)
    planner.observe(catalog)
    assert planner._continue_restoration(planner.state_key(catalog)) is None
    assert planner.restoration_target is None
    assert planner.restrictions
    assert all(record["reason"] == "no_safe_restore_route" for record in planner.restrictions)
    assert all(not check.passed for check in result.assertions)


def temporarily_blocked_exit() -> tuple[CoveragePlanner, Observation, str]:
    historical = page("https://app.test/cart", [button("e1", "Continue Shopping")])
    planner = CoveragePlanner(historical.url)
    target_key = planner.observe(historical)
    planner.restoration_target = target_key
    planner.abandon_stalled_restoration(reason="no_safe_restore_route")
    assert planner.drain_restrictions()[0]["status"] == "active"
    return planner, historical, target_key


@pytest.mark.parametrize("passed", [True, False])
def test_later_exact_control_execution_resolves_temporary_debt_without_changing_verdict(passed):
    planner, historical, target_key = temporarily_blocked_exit()
    current = page(historical.url, [button("e90", "Continue Shopping")])
    current.title = "Live cart variant"
    after = page("https://app.test/catalog", [])
    decision = AgentDecision("click", "actually test", target="e90")
    result = outcome(current, after)
    result.evidence["action_step"] = 96
    result.screenshot = "096-click.png"
    result.assertions = [AssertionResult("cart_persisted", "expected item", "observed item", passed, "Actual local check")]
    planner.record_action(current, decision, result, after)

    assert planner.context(after)["restrictions"] == []
    assert planner.context(after)["resolved_restriction_count"] == 1
    assert planner.states[target_key].blocked == set()
    assert planner.coverage_complete()
    assert len(planner.restrictions) == 1  # original history was not deleted
    record = planner.drain_restriction_resolutions()[0]
    assert record["id"] == planner.restrictions[0]["id"]
    assert record["status"] == "resolved"
    assert record["resolution"]["source"] == "actual_semantic_move_execution"
    assert record["resolution"]["action_step"] == 96
    assert record["resolution"]["screenshot"] == "096-click.png"
    assert record["resolution"]["assertions_passed"] is passed
    assert result.assertions[0].passed is passed
    assert planner.drain_restriction_resolutions() == []
    assert planner.drain_restrictions() == []


def test_same_control_on_another_route_does_not_resolve_historical_debt():
    planner, historical, target_key = temporarily_blocked_exit()
    current = page("https://app.test/different-cart", [button("e90", "Continue Shopping")])
    decision = AgentDecision("click", "different route", target="e90")
    planner.record_action(current, decision, outcome(current, current), current)
    assert planner.restrictions[0]["status"] == "active"
    assert planner.states[target_key].blocked
    assert not planner.drain_restriction_resolutions()


def test_same_name_in_a_different_context_does_not_resolve_historical_debt():
    planner, historical, target_key = temporarily_blocked_exit()
    current = page(historical.url, [button("e90", "Continue Shopping", context="dialog Recent order")])
    decision = AgentDecision("click", "different control", target="e90")
    planner.record_action(current, decision, outcome(current, current), current)
    assert planner.restrictions[0]["status"] == "active"
    assert planner.states[target_key].blocked
    assert not planner.drain_restriction_resolutions()


def test_reaching_target_route_does_not_resolve_unexecuted_exit():
    planner, historical, target_key = temporarily_blocked_exit()
    current = page("https://app.test/catalog", [anchor("e90", "Cart", historical.url)])
    decision = AgentDecision("click", "reach route", target="e90")
    planner.record_action(current, decision, outcome(current, historical), historical)
    assert planner.restrictions[0]["status"] == "active"
    assert planner.states[target_key].blocked
    assert not planner.drain_restriction_resolutions()


def test_failed_execution_does_not_resolve_temporary_debt():
    planner, historical, target_key = temporarily_blocked_exit()
    decision = AgentDecision("click", "still unavailable", target="e1")
    planner.record_action(historical, decision, outcome(historical, historical, success=False), historical)
    assert planner.restrictions[0]["status"] == "active"
    assert planner.states[target_key].blocked
    assert not planner.drain_restriction_resolutions()


@pytest.mark.parametrize("reason", ["required_field_validation_masked", "policy_blocked", "action_retry_exhausted"])
def test_non_restoration_restrictions_are_not_resolved_by_generic_execution(reason):
    planner, historical, target_key = temporarily_blocked_exit()
    planner.restrictions[0]["reason"] = reason
    decision = AgentDecision("click", "unrelated execution", target="e1")
    planner.record_action(historical, decision, outcome(historical, historical), historical)
    assert planner.restrictions[0]["status"] == "active"
    assert planner.states[target_key].blocked
    assert not planner.drain_restriction_resolutions()


def product_page(*, action="add", item_id="sku-42", url="https://app.test/catalog", ref="e1", surface="products"):
    element = button(ref, "Add to cart" if action == "add" else "Remove",
                     context="article data-product-id=sku-42 Desk Lamp",
                     **({"reversible_cart_action": "remove"} if action == "remove" else {}))
    observation = page(url, [element])
    product = {"id": item_id, "name": "Desk Lamp", "cart_action": action, "action_refs": [ref]}
    if surface == "product_detail":
        observation.page_state["product_detail"] = product
    else:
        observation.page_state["products"] = [product]
    return observation


def failed_remove(before, after, *, step=52):
    result = outcome(before, after)
    result.evidence["action_step"] = step
    result.screenshot = f"{step:03}-remove.png"
    result.assertions = [AssertionResult("cart_count", 0, 1, False, "Count must decrease"),
                         AssertionResult("cart_control_state", "add", "remove", False, "Remove must expose Add")]
    return result


def block_catalog_add(planner, historical):
    target_key = planner.observe(historical)
    planner.restoration_target = target_key
    planner.abandon_stalled_restoration(reason="no_safe_restore_route")
    return target_key


@pytest.mark.parametrize("late_failure", [False, True])
def test_failed_exact_inverse_toggle_explains_add_restriction_without_claiming_coverage(late_failure):
    historical = product_page()
    before = product_page(action="remove", ref="e90")
    after = product_page(action="remove", ref="e91")
    planner = CoveragePlanner(historical.url)
    if late_failure:
        block_catalog_add(planner, historical)
        planner.drain_restrictions()
    decision = AgentDecision("click", "actually remove observed product", target="e90")
    result = failed_remove(before, after)
    planner.record_action(before, decision, result, after)
    if not late_failure:
        block_catalog_add(planner, historical)
    record = planner.restrictions[0]
    assert record["status"] == "active" and record["reason"] == "no_safe_restore_route"
    assert record["dependency"]["kind"] == "failed_inverse_cart_toggle"
    assert record["dependency"]["item_id"] == "sku-42"
    assert record["dependency"]["surface"] == "products"
    assert record["dependency"]["action_step"] == 52
    assert record["dependency"]["screenshot"] == "052-remove.png"
    assert record["dependency"]["failed_assertion_kinds"] == ["cart_count", "cart_control_state"]
    assert len(planner.drain_restriction_updates()) == int(late_failure)
    assert planner.drain_restriction_updates() == []
    assert planner.drain_restriction_resolutions() == []
    assert not planner.coverage_complete()
    assert not any(assertion.passed for assertion in result.assertions)


@pytest.mark.parametrize("change", ["different_id", "different_route", "different_surface", "counter_only", "command_failed", "control_now_add", "ambiguous_id", "ambiguous_controls", "missing_id"])
def test_inverse_dependency_does_not_infer_relationship_from_similar_product_name(change):
    historical = product_page()
    before = product_page(action="remove", ref="e90")
    after = product_page(action="remove", ref="e91")
    if change == "different_id":
        before.page_state["products"][0]["id"] = "sku-99"
        after.page_state["products"][0]["id"] = "sku-99"
    elif change == "different_route":
        before.url = after.url = "https://app.test/cart"
    elif change == "different_surface":
        before = product_page(action="remove", ref="e90", surface="product_detail")
        after = product_page(action="remove", ref="e91", surface="product_detail")
    elif change == "control_now_add":
        after = product_page(action="add", ref="e91")
    elif change == "ambiguous_id":
        before.page_state["products"].append({**before.page_state["products"][0], "action_refs": ["e92"]})
        after.page_state["products"].append({**after.page_state["products"][0], "action_refs": ["e92"]})
    elif change == "ambiguous_controls":
        before.page_state["products"][0]["action_refs"].append("e92")
        after.page_state["products"][0]["action_refs"].append("e92")
    elif change == "missing_id":
        before.page_state["products"][0].pop("id")
        after.page_state["products"][0].pop("id")
    planner = CoveragePlanner(historical.url)
    block_catalog_add(planner, historical)
    result = failed_remove(before, after)
    if change == "counter_only":
        result.assertions.pop()
    elif change == "command_failed":
        result.success = False
    planner.record_action(before, AgentDecision("click", "test remove", target="e90"), result, after)
    assert "dependency" not in planner.restrictions[0]
    assert planner.restrictions[0]["status"] == "active"
    assert not planner.drain_restriction_updates()


def test_real_inverse_recovery_does_not_contaminate_later_restoration_debt():
    historical = product_page()
    before = product_page(action="remove", ref="e90")
    stuck = product_page(action="remove", ref="e91")
    planner = CoveragePlanner(historical.url)
    planner.record_action(before, AgentDecision("click", "failed remove", target="e90"),
                          failed_remove(before, stuck), stuck)
    recovered = product_page(action="add", ref="e92")
    recovered_result = outcome(stuck, recovered)
    recovered_result.assertions = [AssertionResult("cart_control_state", "add", "add", True, "Actual toggle recovered")]
    planner.record_action(stuck, AgentDecision("click", "real retry success", target="e91"), recovered_result, recovered)
    block_catalog_add(planner, historical)
    assert "dependency" not in planner.restrictions[0]
    assert planner.restrictions[0]["status"] == "active"
