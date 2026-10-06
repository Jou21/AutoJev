from __future__ import annotations

import pytest
from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.domain.models import (
    ActionResult,
    AgentDecision,
    ExplorationOptions,
    Observation,
    utc_now,
)
from autojev.services.coverage import (
    CoveragePlanner,
    _IssuedRestoreStep,
    semantic_element_fingerprint,
)


def observation(
    url: str, elements: list[dict], *, title: str = "Page", text: str = ""
) -> Observation:
    return Observation(
        url=url,
        title=title,
        text=text,
        interactive_elements=elements,
    )


def result(
    action: str, before: str, after: str, *, success: bool = True
) -> ActionResult:
    return ActionResult(
        action=action,
        success=success,
        started_at=utc_now(),
        finished_at=utc_now(),
        before_url=before,
        after_url=after,
    )


@pytest.mark.parametrize("already_exercised", [False, True])
def test_live_variant_adopts_historical_frontier_without_restoration_loop(already_exercised):
    control = {"ref": "e1", "tag": "button", "name": "Add to cart", "enabled": True}
    old = observation("https://app.test/inventory", [control])
    live = observation(old.url, [{**control, "ref": "e8"},
                                {"ref": "e9", "tag": "button", "name": "Close menu", "enabled": True}])
    planner = CoveragePlanner(old.url)
    old_key = planner.observe(old)
    live_key = planner.observe(live)
    old_moves = set(planner.states[old_key].candidates)
    planner.states[live_key].attempted.update(set(planner.states[live_key].candidates) - old_moves)
    if already_exercised:
        planner.states[live_key].inherited.update(old_moves)
    planner.path = [live_key]
    planner.restoration_target = old_key
    assert planner.next_restoration_action(live) is None
    assert planner.restoration_target is None
    assert not planner._remaining(planner.states[old_key])
    live_remaining = planner.context(live)["current_untried"]
    if already_exercised:
        assert planner.can_finish()  # no old snapshot silently left pending
        assert not live_remaining
    else:
        assert not planner.can_finish()
        assert [item["name"] for item in live_remaining] == ["Add to cart"]


def test_state_and_element_identity_are_semantic_and_preserve_route_parts():
    first = {
        "ref": "e0",
        "tag": "a",
        "name": "Orders",
        "href": "https://app.test/list?q=open#table",
        "enabled": True,
    }
    renumbered = {**first, "ref": "e91", "document_top": 800, "in_viewport": False}
    planner = CoveragePlanner("https://app.test/")

    a = observation("https://app.test/list?q=open#table", [first])
    b = observation("https://app.test/list?q=open#table", [renumbered])
    other_query = observation("https://app.test/list?q=closed#table", [renumbered])
    other_hash = observation("https://app.test/list?q=open#details", [renumbered])

    assert semantic_element_fingerprint(first) == semantic_element_fingerprint(
        renumbered
    )
    assert planner.state_key(a) == planner.state_key(b)
    assert planner.state_key(a) != planner.state_key(other_query)
    assert planner.state_key(a) != planner.state_key(other_hash)


def test_effect_key_tracks_visible_text_and_control_state_without_splitting_state():
    base_element = {
        "ref": "e0",
        "tag": "input",
        "type": "text",
        "name": "Busca",
        "enabled": True,
        "has_value": False,
    }
    planner = CoveragePlanner("https://app.test/search")
    base = observation(
        "https://app.test/search", [base_element], title="Busca", text="Sem filtro"
    )
    value_changed = observation(
        "https://app.test/search",
        [{**base_element, "has_value": True}],
        title="Busca",
        text="Sem filtro",
    )
    text_changed = observation(
        "https://app.test/search",
        [base_element],
        title="Busca",
        text="1 resultado encontrado",
    )

    assert planner.state_key(base) == planner.state_key(value_changed)
    assert planner.state_key(base) == planner.state_key(text_changed)
    assert planner.effect_key(base) != planner.effect_key(value_changed)
    assert planner.effect_key(base) != planner.effect_key(text_changed)


def test_effect_key_tracks_interactive_surface_state_but_ignores_vertical_scroll():
    planner = CoveragePlanner("https://app.test/products")
    control = {
        "ref": "e0",
        "tag": "button",
        "name": "Add to cart",
        "enabled": True,
        "interactable": True,
        "in_horizontal_viewport": True,
        "in_viewport": True,
        "document_top": 120,
    }
    top = observation("https://app.test/products", [control], text="Products")
    scrolled = observation(
        top.url,
        [{**control, "in_viewport": False, "document_top": 1120}],
        text="Products",
    )
    temporarily_occluded = observation(
        top.url,
        [{**control, "interactable": False, "in_viewport": True}],
        text="Products",
    )
    covered = observation(
        top.url,
        [{**control, "interactable": False, "aria_hidden": "true"}],
        text="Products",
    )

    assert planner.effect_key(top) == planner.effect_key(scrolled)
    assert planner.effect_key(top) == planner.effect_key(temporarily_occluded)
    assert planner.state_key(top) == planner.state_key(temporarily_occluded)
    assert planner.effect_key(top) != planner.effect_key(covered)
    assert [item["name"] for item in planner.context(top)["current_untried"]] == [
        "Add to cart"
    ]
    assert planner.context(temporarily_occluded)["current_untried"] == []


def test_offcanvas_menu_states_have_distinct_effects_and_safe_candidates():
    planner = CoveragePlanner("https://app.test/products")
    open_toggle = {
        "ref": "e0",
        "tag": "button",
        "name": "Open Menu",
        "enabled": True,
        "interactable": True,
        "in_horizontal_viewport": True,
        "aria_hidden": False,
    }
    menu_link = {
        "ref": "e1",
        "tag": "a",
        "role": "button",
        "name": "All Items",
        "href": "https://app.test/inventory",
        "enabled": True,
        "in_navigation": True,
        "interactable": False,
        "in_horizontal_viewport": False,
        "aria_hidden": True,
    }
    close_toggle = {
        "ref": "e2",
        "tag": "button",
        "name": "Close Menu",
        "enabled": True,
        "interactable": False,
        "in_horizontal_viewport": False,
        "aria_hidden": True,
    }
    closed = observation(
        "https://app.test/products", [open_toggle, menu_link, close_toggle]
    )
    opened = observation(
        closed.url,
        [
            {**open_toggle, "interactable": False},
            {
                **menu_link,
                "interactable": True,
                "in_horizontal_viewport": True,
                "aria_hidden": False,
            },
            {
                **close_toggle,
                "interactable": True,
                "in_horizontal_viewport": True,
                "aria_hidden": False,
            },
        ],
    )

    assert planner.effect_key(closed) != planner.effect_key(opened)
    assert planner.state_key(closed) != planner.state_key(opened)
    assert {item["name"] for item in planner.context(closed)["current_untried"]} == {
        "Open Menu"
    }
    assert {item["name"] for item in planner.context(opened)["current_untried"]} == {
        "All Items",
        "Close Menu",
    }


def test_exact_self_link_is_not_a_candidate_but_stateful_same_url_link_is():
    page = observation(
        "https://app.test/cart?tab=items",
        [
            {
                "ref": "e0",
                "tag": "a",
                "name": "Cart, 1 item",
                "href": "https://app.test/cart?tab=items",
                "aria_current": "page",
                "enabled": True,
            },
            {
                "ref": "e1",
                "tag": "a",
                "name": "Saved items",
                "href": "https://app.test/cart?tab=saved",
                "enabled": True,
            },
            {
                "ref": "e2",
                "tag": "a",
                "name": "Open cart menu",
                "href": "https://app.test/cart?tab=items",
                "aria_haspopup": "menu",
                "aria_expanded": "false",
                "enabled": True,
            },
            {
                "ref": "e3",
                "tag": "a",
                "role": "button",
                "name": "Refresh current cart",
                "href": "https://app.test/cart?tab=items",
                "enabled": True,
            },
        ],
    )
    planner = CoveragePlanner(page.url)

    names = {item["name"] for item in planner.context(page)["current_untried"]}
    assert "Cart, 1 item" not in names
    assert names == {"Saved items", "Open cart menu", "Refresh current cart"}


def test_decision_key_is_stable_when_a_retry_receives_a_fresh_ref():
    first = observation(
        "https://app.test/orders",
        [{"ref": "e1", "tag": "button", "name": "Retry", "enabled": True}],
    )
    fresh = observation(
        first.url,
        [{"ref": "e9", "tag": "button", "name": "Retry", "enabled": True}],
    )
    planner = CoveragePlanner(first.url)

    first_key = planner.decision_key(
        first, AgentDecision(action="click", target="e1", rationale="Retry")
    )
    fresh_key = planner.decision_key(
        fresh, AgentDecision(action="click", target="e9", rationale="Retry")
    )

    assert first_key is not None
    assert first_key == fresh_key


def test_safe_form_controls_join_frontier_with_deterministic_typed_actions():
    page = observation(
        "https://app.test/search",
        [
            {
                "ref": "e0",
                "tag": "input",
                "type": "search",
                "name": "Buscar pedidos",
                "enabled": True,
            },
            {
                "ref": "e1",
                "tag": "select",
                "name": "Status",
                "enabled": True,
                "options": [
                    {
                        "value": "all",
                        "label": "Todos",
                        "selected": True,
                        "disabled": False,
                    },
                    {
                        "value": "open",
                        "label": "Em aberto",
                        "selected": False,
                        "disabled": False,
                    },
                ],
            },
            {
                "ref": "e2",
                "tag": "input",
                "type": "password",
                "name": "Senha",
                "enabled": True,
            },
        ],
    )
    planner = CoveragePlanner(page.url)

    context = planner.context(page)

    assert [item["ref"] for item in context["current_untried"]] == ["e0", "e1"]
    assert context["current_untried"][0]["suggested_action"] == "fill"
    assert context["current_untried"][0]["suggested_value"] == "AutoJev QA"
    assert context["current_untried"][1]["suggested_action"] == "select"
    assert context["current_untried"][1]["suggested_value"] == "open"
    assert context["current_untried"][1]["option_label"] == "Em aberto"

    forced, reason = planner.reconcile(page, AgentDecision("finish", "done"))
    assert (forced.action, forced.target, forced.value) == (
        "fill",
        "e0",
        "AutoJev QA",
    )
    assert reason == "finish_replaced_by_frontier_action"

    compatible, reason = planner.reconcile(
        page,
        AgentDecision(action="click", target="e0", rationale="wrong action"),
    )
    assert (compatible.action, compatible.target, compatible.value) == (
        "fill",
        "e0",
        "AutoJev QA",
    )
    assert reason == "incompatible_form_action_replaced"


def test_select_options_are_bounded_atomic_moves_and_do_not_split_state():
    options = [
        {
            "value": f"status-{index}",
            "label": f"Status {index}",
            "selected": index == 0,
            "disabled": False,
        }
        for index in range(10)
    ]
    options.extend(
        [
            {"value": "", "label": "Escolha", "selected": False},
            {"value": "delete", "label": "Delete records", "selected": False},
            {"value": "disabled", "label": "Disabled", "disabled": True},
        ]
    )
    element = {
        "ref": "e7",
        "tag": "select",
        "name": "Status interno",
        "enabled": True,
        "selected_index": 0,
        "options": options,
    }
    page = observation("https://app.test/orders", [element])
    changed_options = [
        {**option, "selected": option.get("value") == "status-1"} for option in options
    ]
    changed = observation(
        page.url,
        [{**element, "selected_index": 1, "options": changed_options}],
    )
    planner = CoveragePlanner(page.url)

    assert planner.state_key(page) == planner.state_key(changed)
    assert planner.effect_key(page) != planner.effect_key(changed)

    moves = planner.context(page)["current_untried"]
    assert len(moves) == 7
    assert {move["suggested_value"] for move in moves} == {
        f"status-{index}" for index in range(1, 8)
    }
    assert moves[0]["suggested_value"] == "status-1"
    assert moves[-1]["suggested_value"] == "status-7"
    assert all(move["suggested_action"] == "select" for move in moves)
    assert all(move["option_label"].startswith("Status ") for move in moves)

    chosen = AgentDecision(
        action="select",
        target="e7",
        value="status-3",
        rationale="Exercise this exact safe option",
    )
    reconciled, reason = planner.reconcile(page, chosen)
    assert reconciled == chosen
    assert reason is None
    planner.record_action(page, chosen, result("select", page.url, page.url), changed)

    remaining = planner.context(changed)["current_untried"]
    assert "status-3" not in {move["suggested_value"] for move in remaining}
    assert "status-1" not in {move["suggested_value"] for move in remaining}
    assert "status-0" in {move["suggested_value"] for move in remaining}
    assert len(remaining) == 6


def test_multi_select_tracks_selected_values_and_keeps_state_changing_choices():
    control = {
        "ref": "e0",
        "tag": "select",
        "name": "Tags",
        "enabled": True,
        "multiple": True,
        "selected_index": 0,
        "selected_values": ["a", "b"],
        "options": [
            {"value": "a", "label": "A", "selected": True},
            {"value": "b", "label": "B", "selected": True},
            {"value": "c", "label": "C", "selected": False},
        ],
    }
    before = observation("https://app.test/search", [control], text="Search")
    after = observation(
        before.url,
        [
            {
                **control,
                "selected_values": ["a"],
                "options": [
                    {"value": "a", "label": "A", "selected": True},
                    {"value": "b", "label": "B", "selected": False},
                    {"value": "c", "label": "C", "selected": False},
                ],
            }
        ],
        text="Search",
    )
    planner = CoveragePlanner(before.url)

    before_values = {
        item["suggested_value"] for item in planner.context(before)["current_untried"]
    }
    after_values = {
        item["suggested_value"] for item in planner.context(after)["current_untried"]
    }

    assert before_values == {"a", "b", "c"}
    assert after_values == {"b", "c"}
    assert planner.effect_key(before) != planner.effect_key(after)


def test_hoverable_menu_exposes_hover_and_click_as_independent_moves():
    menu = {
        "ref": "e0",
        "tag": "div",
        "aria_haspopup": "menu",
        "name": "Products",
        "href": "https://app.test/products",
        "enabled": True,
        "in_navigation": True,
    }
    page = observation("https://app.test/", [menu])
    planner = CoveragePlanner(page.url)

    moves = planner.context(page)["current_untried"]
    assert [(move["suggested_action"], move["ref"]) for move in moves] == [
        ("hover", "e0"),
        ("click", "e0"),
    ]
    assert len({move["move_key"] for move in moves}) == 2

    hover = AgentDecision(action="hover", target="e0", rationale="Reveal submenu")
    reconciled, reason = planner.reconcile(page, hover)
    assert reconciled == hover
    assert reason is None
    planner.record_action(page, hover, result("hover", page.url, page.url), page)

    after_hover = planner.context(page)["current_untried"]
    assert [move["suggested_action"] for move in after_hover] == ["click"]
    assert planner.global_destinations_attempted == set()

    click = AgentDecision(action="click", target="e0", rationale="Open destination")
    reconciled, reason = planner.reconcile(page, click)
    assert reconciled == click
    assert reason is None


def test_new_child_inherits_only_classified_parent_moves_and_settles_shared_move():
    root = observation(
        "https://app.test/",
        [
            {
                "ref": "e0",
                "tag": "button",
                "name": "Open submenu",
                "enabled": True,
            },
            {
                "ref": "e1",
                "tag": "button",
                "name": "Shared parent action",
                "enabled": True,
            },
        ],
    )
    child = observation(
        root.url,
        [
            {
                "ref": "e10",
                "tag": "button",
                "name": "Open submenu",
                "enabled": True,
            },
            {
                "ref": "e11",
                "tag": "button",
                "name": "Shared parent action",
                "enabled": True,
            },
            {
                "ref": "e12",
                "tag": "button",
                "name": "Child-only action",
                "enabled": True,
            },
        ],
    )
    planner = CoveragePlanner(root.url)
    open_child = AgentDecision(
        action="click", target="e0", rationale="Open the child state"
    )

    reconciled, reason = planner.reconcile(root, open_child)
    assert reconciled == open_child
    assert reason is None
    planner.record_action(
        root,
        open_child,
        result("click", root.url, child.url),
        child,
    )

    child_context = planner.context(child)
    assert child_context["candidates_inherited"] == 1
    assert [move["name"] for move in child_context["current_untried"]] == [
        "Child-only action",
        "Shared parent action",
    ]

    # The still-live shared control can be covered without restoring an
    # obsolete same-route snapshot. Its exact ancestor move is then settled.
    shared = AgentDecision(
        action="click", target="e11", rationale="Cover the shared action"
    )
    planner.record_action(
        child,
        shared,
        result("click", child.url, child.url),
        child,
    )
    parent_context = planner.context(root)
    assert parent_context["current_untried"] == []


def test_exhausted_frontier_can_finish_without_physical_dom_stack_unwind():
    root = observation(
        "https://app.test/inventory",
        [{"ref": "e0", "tag": "button", "name": "Enable", "enabled": True}],
    )
    changed = observation(
        root.url,
        [{"ref": "e7", "tag": "button", "name": "Disable", "enabled": True}],
    )
    planner = CoveragePlanner(root.url)
    enable, _ = planner.reconcile(root, AgentDecision("finish", "cover"))
    planner.record_action(root, enable, result("click", root.url, root.url), changed)
    disable, _ = planner.reconcile(changed, AgentDecision("finish", "cover"))
    planner.record_action(
        changed, disable, result("click", changed.url, changed.url), changed
    )

    finish, reason = planner.reconcile(changed, AgentDecision("finish", "done"))

    assert finish.action == "finish"
    assert reason is None
    assert planner.can_finish() is True


def test_same_route_backtrack_navigates_known_url_instead_of_browser_history():
    root = observation(
        "https://app.test/inventory",
        [
            {"ref": "e0", "tag": "button", "name": "Open state", "enabled": True},
            {"ref": "e1", "tag": "button", "name": "Root sibling", "enabled": True},
        ],
    )
    child = observation(
        root.url,
        [{"ref": "e7", "tag": "button", "name": "Child action", "enabled": True}],
    )
    planner = CoveragePlanner(root.url)
    enter = AgentDecision(action="click", target="e0", rationale="Open state")
    planner.reconcile(root, enter)
    planner.record_action(root, enter, result("click", root.url, root.url), child)
    local, _ = planner.reconcile(child, AgentDecision("finish", "cover"))
    planner.record_action(child, local, result("click", child.url, child.url), child)

    restore, reason = planner.reconcile(child, AgentDecision("finish", "cover"))

    assert restore.action == "navigate"
    assert restore.url == root.url
    assert reason == "dom_state_backtrack_navigate"

    planner.record_action(
        child,
        restore,
        result("navigate", child.url, child.url, success=False),
        child,
    )
    finish, _ = planner.reconcile(child, AgentDecision("finish", "cover"))

    assert finish.action == "finish"
    assert planner.can_finish() is True
    assert planner.coverage_complete() is False


def test_exhausted_child_backtracks_mechanically_before_another_model_call():
    root = observation(
        "https://app.test/inventory",
        [
            {"ref": "e0", "tag": "button", "name": "Open state", "enabled": True},
            {"ref": "e1", "tag": "button", "name": "Root sibling", "enabled": True},
        ],
    )
    child = observation(
        root.url,
        [{"ref": "e7", "tag": "button", "name": "Child action", "enabled": True}],
    )
    planner = CoveragePlanner(root.url)
    enter = AgentDecision(action="click", target="e0", rationale="Open state")
    planner.record_action(root, enter, result("click", root.url, root.url), child)
    local, _ = planner.reconcile(child, AgentDecision("finish", "cover"))
    planner.record_action(child, local, result("click", child.url, child.url), child)

    mechanical = planner.next_restoration_action(child)

    assert mechanical is not None
    decision, reason = mechanical
    assert decision.action == "navigate"
    assert decision.url == root.url
    assert reason == "dom_state_backtrack_navigate"


def test_same_route_backtrack_counts_a_different_wrong_snapshot_as_failure():
    url = "https://app.test/inventory"
    root = observation(
        url,
        [
            {"ref": "e0", "tag": "button", "name": "Open state", "enabled": True},
            {"ref": "e1", "tag": "button", "name": "Root sibling", "enabled": True},
        ],
        text="Root",
    )
    child = observation(
        url,
        [{"ref": "e7", "tag": "button", "name": "Child action", "enabled": True}],
        text="Child",
    )
    wrong_snapshot = observation(url, [], text="Unexpected snapshot")
    planner = CoveragePlanner(url)
    enter = AgentDecision(action="click", target="e0", rationale="Open state")
    planner.record_action(root, enter, result("click", url, url), child)
    local, _ = planner.reconcile(child, AgentDecision("finish", "cover"))
    planner.record_action(child, local, result("click", url, url), child)
    child_key = planner.state_key(child)

    restore = planner.next_restoration_action(child)
    assert restore is not None
    decision, reason = restore
    assert reason == "dom_state_backtrack_navigate"
    planner.record_action(
        child,
        decision,
        result("navigate", url, url),
        wrong_snapshot,
    )

    assert planner.backtrack_failures[child_key] == 1
    # The same URL may land on many already-covered DOM snapshots. After the
    # bounded miss, the inaccessible parent branch is documented as blocked
    # instead of oscillating forever among those snapshots.
    assert planner.next_restoration_action(wrong_snapshot) is None
    assert planner.can_finish() is True
    assert planner.coverage_complete() is False


def test_repeated_model_go_back_is_reconciled_and_never_retried_unchanged():
    root = observation(
        "https://app.test/inventory",
        [
            {"ref": "e0", "tag": "button", "name": "Open state", "enabled": True},
            {"ref": "e1", "tag": "button", "name": "Root sibling", "enabled": True},
        ],
    )
    child = observation(
        root.url,
        [{"ref": "e7", "tag": "button", "name": "Child action", "enabled": True}],
    )
    planner = CoveragePlanner(root.url)
    enter = AgentDecision(action="click", target="e0", rationale="Open state")
    planner.record_action(root, enter, result("click", root.url, root.url), child)
    local, _ = planner.reconcile(child, AgentDecision("finish", "cover"))
    planner.record_action(child, local, result("click", child.url, child.url), child)

    first, _ = planner.reconcile(child, AgentDecision("go_back", "leave"))
    assert first.action == "navigate"
    planner.record_action(
        child,
        first,
        result("navigate", child.url, child.url, success=False),
        child,
    )

    second, reason = planner.reconcile(child, AgentDecision("go_back", "leave"))

    assert second.action == "finish"
    assert reason == "model_backtrack_replaced_by_finish"


def test_policy_blocked_back_edge_is_not_executed_a_second_time():
    root = observation(
        "https://app.test/inventory",
        [
            {"ref": "e0", "tag": "a", "name": "Details", "href": "https://app.test/item", "enabled": True},
            {"ref": "e1", "tag": "button", "name": "Root sibling", "enabled": True},
        ],
    )
    child = observation("https://app.test/item", [])
    planner = CoveragePlanner(root.url)
    enter = AgentDecision(action="click", target="e0", rationale="Open details")
    planner.record_action(
        root,
        enter,
        result("click", root.url, child.url),
        child,
    )

    first, _ = planner.reconcile(child, AgentDecision("go_back", "return"))
    assert first.action == "go_back"
    blocked = result("go_back", child.url, child.url, success=False)
    blocked.message = (
        "PermissionError: PolicyBlocked: browser back would cross the "
        "authenticated session boundary"
    )
    planner.record_action(child, first, blocked, child)

    second, _ = planner.reconcile(child, AgentDecision("go_back", "return"))

    assert second.action != "go_back"


def test_frontier_exposes_at_most_thirty_atomic_moves_per_decision():
    elements = [
        {
            "ref": f"e{index}",
            "tag": "button",
            "role": "menuitem",
            "name": f"Menu {index}",
            "enabled": True,
        }
        for index in range(20)
    ]
    planner = CoveragePlanner("https://app.test/")
    context = planner.context(observation("https://app.test/", elements))

    assert context["frontier_candidates"] == 40
    assert len(context["current_untried"]) == 30


def test_local_context_distinguishes_homonymous_row_actions():
    first = {
        "ref": "e0",
        "tag": "button",
        "name": "Details",
        "context": "tr Order 1001 Customer Alice Details",
        "enabled": True,
    }
    second = {
        **first,
        "ref": "e1",
        "context": "tr Order 1002 Customer Bob Details",
    }
    planner = CoveragePlanner("https://app.test/orders")
    context = planner.context(observation("https://app.test/orders", [first, second]))

    assert {move["ref"] for move in context["current_untried"]} == {"e0", "e1"}


def test_finish_walks_frontier_then_backtracks_and_resumes_parent_candidates():
    root = observation(
        "https://app.test/",
        [
            {
                "ref": "e0",
                "tag": "a",
                "name": "Area A",
                "href": "https://app.test/a",
                "enabled": True,
                "document_top": 10,
            },
            {
                "ref": "e1",
                "tag": "a",
                "name": "Area B",
                "href": "https://app.test/b",
                "enabled": True,
                "document_top": 20,
            },
        ],
    )
    area_a = observation("https://app.test/a", [], title="Area A")
    planner = CoveragePlanner(root.url)

    first, reason = planner.reconcile(root, AgentDecision("finish", "done"))
    assert (first.action, first.target, reason) == (
        "click",
        "e0",
        "finish_replaced_by_frontier_action",
    )
    planner.record_action(root, first, result("click", root.url, area_a.url), area_a)

    back, reason = planner.reconcile(area_a, AgentDecision("finish", "done"))
    assert back.action == "go_back"
    assert reason == "finish_replaced_by_backtrack"

    # Refs change after browser history restoration. Semantic memory must still
    # skip Area A and resume the other branch.
    restored = observation(
        root.url,
        [
            {
                "ref": "e7",
                "tag": "a",
                "name": "Area A",
                "href": "https://app.test/a",
                "enabled": True,
            },
            {
                "ref": "e8",
                "tag": "a",
                "name": "Area B",
                "href": "https://app.test/b",
                "enabled": True,
            },
        ],
    )
    planner.record_action(
        area_a, back, result("go_back", area_a.url, root.url), restored
    )
    second, reason = planner.reconcile(restored, AgentDecision("finish", "done"))
    assert (second.action, second.target, reason) == (
        "click",
        "e8",
        "finish_replaced_by_frontier_action",
    )


def test_noop_go_back_falls_back_to_known_same_origin_parent_url():
    parent = observation(
        "https://app.test/dashboard?tab=all#menu",
        [
            {
                "ref": "e0",
                "tag": "a",
                "name": "Child",
                "href": "https://app.test/child",
                "enabled": True,
            },
            {
                "ref": "e1",
                "tag": "a",
                "name": "Sibling",
                "href": "https://app.test/sibling",
                "enabled": True,
            },
        ],
    )
    child = observation("https://app.test/child", [], title="Child")
    planner = CoveragePlanner(parent.url)
    click, _ = planner.reconcile(parent, AgentDecision("finish", "done"))
    planner.record_action(parent, click, result("click", parent.url, child.url), child)

    back, _ = planner.reconcile(child, AgentDecision("finish", "done"))
    assert back.action == "go_back"
    planner.record_action(
        child,
        back,
        result("go_back", child.url, child.url),
        child,
    )

    fallback, reason = planner.reconcile(child, AgentDecision("finish", "done"))
    assert fallback.action == "navigate"
    assert fallback.url == parent.url
    assert reason == "finish_replaced_by_backtrack"


def test_nested_dom_only_frontier_is_replayed_with_fresh_semantic_refs():
    url = "https://app.test/dashboard"
    root = observation(
        url,
        [{"ref": "e0", "tag": "button", "name": "Abrir modal", "enabled": True}],
        title="Dashboard",
    )
    modal = observation(
        url,
        [
            {
                "ref": "e10",
                "tag": "button",
                "name": "Abrir submenu",
                "enabled": True,
                "context": "dialog Preferências",
            }
        ],
        title="Dashboard — Preferências",
    )
    nested = observation(
        url,
        [
            {
                "ref": "e20",
                "tag": "button",
                "name": "Opção A",
                "enabled": True,
                "context": "dialog Preferências submenu",
            },
            {
                "ref": "e21",
                "tag": "button",
                "name": "Opção B",
                "enabled": True,
                "context": "dialog Preferências submenu",
            },
        ],
        title="Dashboard — Preferências — Submenu",
    )
    planner = CoveragePlanner(url)

    open_modal, _ = planner.reconcile(root, AgentDecision("finish", "cover"))
    planner.record_action(root, open_modal, result("click", url, url), modal)
    open_submenu, _ = planner.reconcile(modal, AgentDecision("finish", "cover"))
    planner.record_action(modal, open_submenu, result("click", url, url), nested)

    # The first nested option closes the complete React surface while its
    # sibling remains untried. URL navigation cannot distinguish these states.
    leave_surface, _ = planner.reconcile(
        nested,
        AgentDecision(action="click", target="e20", rationale="exercise A"),
    )
    restored_root = observation(
        url,
        [{"ref": "e7", "tag": "button", "name": "Abrir modal", "enabled": True}],
        title="Dashboard",
    )
    planner.record_action(
        nested,
        leave_surface,
        result("click", url, url),
        restored_root,
    )

    # The stored recipe contains no old selector/ref: e0 is re-resolved as e7.
    reopen_modal, reason = planner.reconcile(
        restored_root, AgentDecision("finish", "cover")
    )
    assert (reopen_modal.action, reopen_modal.target, reason) == (
        "click",
        "e7",
        "frontier_restore_replay",
    )
    reopened_modal = observation(
        url,
        [
            {
                "ref": "e17",
                "tag": "button",
                "name": "Abrir submenu",
                "enabled": True,
                "context": "dialog Preferências",
            }
        ],
        title="Dashboard — Preferências",
    )
    planner.record_action(
        restored_root,
        reopen_modal,
        result("click", url, url),
        reopened_modal,
    )

    reopen_submenu, reason = planner.reconcile(
        reopened_modal, AgentDecision("finish", "cover")
    )
    assert (reopen_submenu.action, reopen_submenu.target, reason) == (
        "click",
        "e17",
        "frontier_restore_replay",
    )
    reopened_nested = observation(
        url,
        [
            {
                "ref": "e30",
                "tag": "button",
                "name": "Opção A",
                "enabled": True,
                "context": "dialog Preferências submenu",
            },
            {
                "ref": "e31",
                "tag": "button",
                "name": "Opção B",
                "enabled": True,
                "context": "dialog Preferências submenu",
            },
        ],
        title="Dashboard — Preferências — Submenu",
    )
    planner.record_action(
        reopened_modal,
        reopen_submenu,
        result("click", url, url),
        reopened_nested,
    )

    sibling, reason = planner.reconcile(
        reopened_nested, AgentDecision("finish", "cover")
    )
    assert (sibling.action, sibling.target, reason) == (
        "click",
        "e31",
        "finish_replaced_by_frontier_action",
    )
    assert planner.context(reopened_nested)["restoration_target"] is None


def test_dom_only_replay_failure_is_bounded_and_marks_frontier_blocked():
    url = "https://app.test/dashboard"
    root = observation(
        url,
        [{"ref": "e0", "tag": "button", "name": "Abrir modal", "enabled": True}],
        title="Dashboard",
    )
    modal = observation(
        url,
        [
            {"ref": "e10", "tag": "button", "name": "Opção A", "enabled": True},
            {"ref": "e11", "tag": "button", "name": "Opção B", "enabled": True},
        ],
        title="Dashboard — Modal",
    )
    planner = CoveragePlanner(url)
    open_modal, _ = planner.reconcile(root, AgentDecision("finish", "cover"))
    planner.record_action(root, open_modal, result("click", url, url), modal)
    close_early, _ = planner.reconcile(
        modal,
        AgentDecision(action="click", target="e10", rationale="exercise A"),
    )
    planner.record_action(modal, close_early, result("click", url, url), root)

    for _ in range(2):
        replay, reason = planner.reconcile(root, AgentDecision("finish", "cover"))
        assert reason == "frontier_restore_replay"
        planner.record_action(
            root,
            replay,
            result("click", url, url, success=False),
            root,
        )

    settled, reason = planner.reconcile(root, AgentDecision("finish", "cover"))
    context = planner.context(root)

    assert settled.action == "finish"
    assert reason is None
    assert context["frontier_candidates"] == 0
    assert context["candidates_blocked"] >= 1
    assert planner.can_finish() is True
    assert planner.coverage_complete() is False


def test_focused_scope_is_exposed_and_becomes_backtracking_root():
    landing = observation(
        "https://app.test/",
        [
            {
                "ref": "e0",
                "tag": "a",
                "name": "Financeiro",
                "href": "https://app.test/financeiro",
                "enabled": True,
            }
        ],
        title="Home",
    )
    focused = observation(
        "https://app.test/financeiro#faturas",
        [{"ref": "e2", "tag": "button", "name": "Nova fatura", "enabled": True}],
        title="Financeiro — Faturas",
        text="Faturas em aberto",
    )
    planner = CoveragePlanner(landing.url, "Financeiro / Faturas")
    planner.observe(landing)
    assert planner.context(landing)["scope"] == "Financeiro / Faturas"
    assert planner.focus_reached is False

    planner.observe(focused)
    context = planner.context(focused)
    assert planner.focus_reached is True
    assert context["path_depth"] == 1
    assert context["current_untried"][0]["name"] == "Nova fatura"


def test_global_sidebar_text_does_not_mark_focus_as_reached():
    landing = observation(
        "https://app.test/",
        [
            {
                "ref": "e0",
                "tag": "a",
                "name": "Configurações",
                "href": "https://app.test/settings",
                "enabled": True,
            }
        ],
        title="Dashboard",
        text="Dashboard Configurações Usuários Permissões Financeiro Relatórios",
    )
    planner = CoveragePlanner(landing.url, "Configurações > Usuários > Permissões")

    planner.observe(landing)

    assert planner.focus_reached is False


def test_focused_scope_excludes_global_navigation_but_keeps_descendants():
    focused = observation(
        "https://app.test/admin/users#permissions",
        [
            {
                "ref": "e0",
                "tag": "a",
                "name": "Detalhes do usuário",
                "href": "https://app.test/admin/users/42",
                "enabled": True,
            },
            {
                "ref": "e1",
                "tag": "a",
                "name": "Financeiro",
                "href": "https://app.test/financeiro",
                "enabled": True,
            },
            {
                "ref": "e2",
                "tag": "button",
                "name": "Nova permissão",
                "enabled": True,
            },
            {
                "ref": "e3",
                "tag": "button",
                "name": "Relatórios",
                "enabled": True,
                "in_navigation": True,
                "context": "aside Navegação principal",
            },
        ],
        title="Usuários — Permissões",
        text="Administração de usuários e permissões",
    )
    planner = CoveragePlanner(focused.url, "Usuários > Permissões")

    context = planner.context(focused)
    refs = {item["ref"] for item in context["current_untried"]}

    assert planner.focus_reached is True
    assert refs == {"e0", "e2"}
    replaced, reason = planner.reconcile(
        focused,
        AgentDecision(action="click", target="e1", rationale="leave scope"),
    )
    assert replaced.action == "click"
    assert replaced.target in {"e0", "e2"}
    assert reason == "repeated_target_replaced"


def test_focused_scope_returns_immediately_after_javascript_button_escapes():
    focused = observation(
        "https://app.test/admin/users",
        [
            {
                "ref": "e0",
                "tag": "button",
                "name": "Abrir dashboard",
                "enabled": True,
            }
        ],
        title="Usuários",
    )
    outside = observation(
        "https://app.test/dashboard",
        [
            {
                "ref": "e1",
                "tag": "button",
                "name": "Controle fora do escopo",
                "enabled": True,
            }
        ],
        title="Dashboard",
    )
    planner = CoveragePlanner(focused.url, "Usuários")

    click, _ = planner.reconcile(
        focused,
        AgentDecision(action="click", target="e0", rationale="open dashboard"),
    )
    planner.record_action(
        focused,
        click,
        result("click", focused.url, outside.url),
        outside,
    )

    outside_context = planner.context(outside)
    assert outside_context["current_untried"] == []
    assert outside_context["active_states"] == 1

    returned, reason = planner.reconcile(
        outside,
        AgentDecision(action="click", target="e1", rationale="keep exploring"),
    )
    assert returned.action == "go_back"
    assert reason == "focus_boundary_return"


def test_dom_only_focus_replays_entry_anchor_without_exploring_base_controls():
    url = "https://app.test/"
    base = observation(
        url,
        [
            {
                "ref": "e0",
                "tag": "button",
                "name": "Usuários",
                "enabled": True,
                "document_top": 10,
            },
            {
                "ref": "e1",
                "tag": "button",
                "name": "Financeiro",
                "enabled": True,
                "document_top": 20,
            },
        ],
        title="Dashboard",
    )
    focused = observation(
        url,
        [
            {"ref": "e10", "tag": "button", "name": "Teste A", "enabled": True},
            {"ref": "e11", "tag": "button", "name": "Teste B", "enabled": True},
        ],
        title="Usuários",
    )
    planner = CoveragePlanner(url, "Usuários")

    enter_focus, _ = planner.reconcile(base, AgentDecision("finish", "find scope"))
    assert enter_focus.target == "e0"
    planner.record_action(base, enter_focus, result("click", url, url), focused)
    assert planner.focus_reached is True
    assert planner.focus_entry_anchor is not None

    # A closes the submenu automatically, leaving B as a remote DOM frontier.
    close_early, _ = planner.reconcile(
        focused,
        AgentDecision(action="click", target="e10", rationale="exercise A"),
    )
    restored_base = observation(
        url,
        [
            {
                "ref": "e7",
                "tag": "button",
                "name": "Usuários",
                "enabled": True,
                "document_top": 10,
            },
            {
                "ref": "e8",
                "tag": "button",
                "name": "Financeiro",
                "enabled": True,
                "document_top": 20,
            },
        ],
        title="Dashboard",
    )
    planner.record_action(
        focused,
        close_early,
        result("click", url, url),
        restored_base,
    )

    # Although the model proposes an out-of-scope base control, the planner
    # resolves the focus-entry fingerprint to its fresh ref and reopens it.
    reopen, reason = planner.reconcile(
        restored_base,
        AgentDecision(action="click", target="e8", rationale="leave scope"),
    )
    assert (reopen.action, reopen.target, reason) == (
        "click",
        "e7",
        "focus_boundary_restore_replay",
    )
    assert planner.context(restored_base)["current_untried"] == []

    reopened_focus = observation(
        url,
        [
            {"ref": "e20", "tag": "button", "name": "Teste A", "enabled": True},
            {"ref": "e21", "tag": "button", "name": "Teste B", "enabled": True},
        ],
        title="Usuários",
    )
    planner.record_action(
        restored_base,
        reopen,
        result("click", url, url),
        reopened_focus,
    )
    sibling, reason = planner.reconcile(
        reopened_focus, AgentDecision("finish", "cover focused frontier")
    )

    assert (sibling.action, sibling.target, reason) == (
        "click",
        "e21",
        "finish_replaced_by_frontier_action",
    )
    assert planner.context(reopened_focus)["active_states"] == 1


def test_focused_scope_prioritizes_local_control_before_matching_back_button():
    focused = observation(
        "https://app.test/admin/users",
        [
            {
                "ref": "e0",
                "tag": "button",
                "name": "Voltar Usuários",
                "enabled": True,
                "document_top": 10,
            },
            {
                "ref": "e1",
                "tag": "button",
                "name": "Editar perfil",
                "enabled": True,
                "document_top": 500,
            },
        ],
        title="Usuários",
    )
    planner = CoveragePlanner(focused.url, "Usuários")

    context = planner.context(focused)
    assert [item["ref"] for item in context["current_untried"]] == ["e1", "e0"]

    decision, reason = planner.reconcile(
        focused,
        AgentDecision(action="click", target="e0", rationale="go back"),
    )
    assert decision.target == "e1"
    assert reason == "premature_exit_replaced"


def test_only_safe_executable_form_fields_join_deterministic_frontier():
    form = observation(
        "https://app.test/search",
        [
            {
                "ref": "e0",
                "tag": "input",
                "type": "text",
                "name": "Busca",
                "enabled": True,
            },
            {"ref": "e1", "tag": "select", "name": "Status", "enabled": True},
        ],
        title="Busca",
    )
    planner = CoveragePlanner(form.url)

    planner.observe(form)

    context = planner.context(form)
    assert context["frontier_candidates"] == 1
    assert context["current_untried"][0]["suggested_action"] == "fill"
    # A select without observed options cannot be executed safely and is
    # intentionally excluded instead of leaving coverage unfinishable.
    assert {item["ref"] for item in context["current_untried"]} == {"e0"}
    assert planner.can_finish() is False


def test_back_navigation_is_delayed_until_current_state_is_exhausted():
    page = observation(
        "https://app.test/dashboard",
        [{"ref": "e0", "tag": "button", "name": "Abrir detalhes", "enabled": True}],
    )
    planner = CoveragePlanner(page.url)

    decision, reason = planner.reconcile(
        page, AgentDecision(action="go_back", rationale="leave early")
    )

    assert decision.action == "click"
    assert decision.target == "e0"
    assert reason == "premature_navigation_replaced"


def test_go_back_from_exhausted_coverage_root_finishes_instead_of_crossing_boundary():
    root = observation("https://app.test/inventory", [])
    planner = CoveragePlanner(root.url)
    planner.observe(root)

    decision, reason = planner.reconcile(
        root,
        AgentDecision(action="go_back", rationale="leave root"),
    )

    assert decision.action == "finish"
    assert reason == "root_backtrack_replaced_by_finish"
    assert planner.can_finish() is True


def test_transient_candidate_failure_retries_once_then_blocks_completion():
    page = observation(
        "https://app.test/dashboard",
        [{"ref": "e0", "tag": "button", "name": "Detalhes", "enabled": True}],
    )
    planner = CoveragePlanner(page.url)

    first, _ = planner.reconcile(page, AgentDecision("finish", "cover"))
    planner.record_action(
        page,
        first,
        result("click", page.url, page.url, success=False),
        page,
    )
    after_first = planner.context(page)
    assert after_first["candidates_retry_pending"] == 1
    assert after_first["current_untried"][0]["ref"] == "e0"

    retry, _ = planner.reconcile(page, AgentDecision("finish", "retry"))
    assert retry.target == "e0"
    planner.record_action(
        page,
        retry,
        result("click", page.url, page.url, success=False),
        page,
    )

    after_second = planner.context(page)
    assert after_second["candidates_retry_pending"] == 0
    assert after_second["candidates_blocked"] == 1
    assert after_second["current_untried"] == []
    assert planner.can_finish() is True
    assert planner.coverage_complete() is False


def test_permanent_action_failure_is_recorded_and_next_candidate_still_runs():
    page = observation(
        "https://app.test/dashboard",
        [
            {"ref": "e0", "tag": "button", "name": "Broken action", "enabled": True},
            {"ref": "e1", "tag": "button", "name": "Healthy action", "enabled": True},
        ],
    )
    planner = CoveragePlanner(page.url)

    first, _ = planner.reconcile(
        page, AgentDecision("click", "exercise", target="e0")
    )
    planner.record_action(
        page,
        first,
        result("click", page.url, page.url, success=False),
        page,
    )
    retry, _ = planner.reconcile(
        page, AgentDecision("click", "retry", target="e0")
    )
    planner.record_action(
        page,
        retry,
        result("click", page.url, page.url, success=False),
        page,
    )

    following, reason = planner.reconcile(page, AgentDecision("finish", "done"))

    assert following.action == "click"
    assert following.target == "e1"
    assert reason == "finish_replaced_by_frontier_action"


def test_repeated_global_navigation_control_is_only_covered_once():
    navigation_a = {
        "ref": "e0",
        "tag": "button",
        "name": "Clientes",
        "enabled": True,
        "in_navigation": True,
        "context": "aside Navegação principal",
    }
    navigation_b = {
        "ref": "e1",
        "tag": "button",
        "name": "Produtos",
        "enabled": True,
        "in_navigation": True,
        "context": "aside Navegação principal",
    }
    first_page = observation(
        "https://app.test/#/clientes", [navigation_a, navigation_b]
    )
    second_page = observation(
        "https://app.test/#/produtos",
        [{**navigation_a, "ref": "e7"}, {**navigation_b, "ref": "e8"}],
    )
    planner = CoveragePlanner(first_page.url)

    first, _ = planner.reconcile(first_page, AgentDecision("finish", "cover"))
    assert first.target == "e0"
    planner.record_action(
        first_page,
        first,
        result("click", first_page.url, second_page.url),
        second_page,
    )
    back, _ = planner.reconcile(second_page, AgentDecision("finish", "cover"))
    assert back.action == "go_back"
    restored = observation(
        first_page.url,
        [{**navigation_a, "ref": "e7"}, {**navigation_b, "ref": "e8"}],
    )
    planner.record_action(
        second_page,
        back,
        result("go_back", second_page.url, restored.url),
        restored,
    )
    next_decision, _ = planner.reconcile(restored, AgentDecision("finish", "cover"))

    assert next_decision.target == "e8"


def test_equivalent_move_is_covered_once_per_route_across_dom_snapshots():
    url = "https://shop.test/inventory"
    shared = {
        "ref": "e0",
        "tag": "button",
        "name": "Add Backpack to cart",
        "enabled": True,
        "context": "product Sauce Labs Backpack",
    }
    first = observation(url, [shared])
    later_snapshot = observation(
        url,
        [
            {**shared, "ref": "e21"},
            {
                "ref": "e22",
                "tag": "button",
                "name": "Add Bike Light to cart",
                "enabled": True,
                "context": "product Sauce Labs Bike Light",
            },
        ],
    )
    planner = CoveragePlanner(url)
    add_backpack = AgentDecision("click", "cover", target="e0")

    planner.record_action(
        first,
        add_backpack,
        result("click", url, url),
        first,
    )

    assert [
        move["name"] for move in planner.context(later_snapshot)["current_untried"]
    ] == ["Add Bike Light to cart"]


def test_restore_navigation_failures_are_grouped_by_canonical_url():
    url = "https://shop.test/inventory"
    first = observation(
        url,
        [{"ref": "e0", "tag": "button", "name": "State A", "enabled": True}],
    )
    second = observation(
        url,
        [{"ref": "e1", "tag": "button", "name": "State B", "enabled": True}],
    )
    planner = CoveragePlanner(url)
    first_key = planner.observe(first)
    second_key = planner.observe(second)

    assert first_key != second_key
    assert planner._navigate_failure_key("target", first_key) == (
        "navigate",
        "target",
        url,
    )
    assert planner._navigate_failure_key(
        "target", first_key
    ) == planner._navigate_failure_key("target", second_key)


def test_stalled_restore_blocks_only_unreachable_target_and_keeps_other_frontier():
    cart = observation(
        "https://shop.test/cart",
        [{"ref": "e0", "tag": "button", "name": "Checkout", "enabled": True}],
    )
    inventory = observation(
        "https://shop.test/inventory",
        [
            {
                "ref": "e1",
                "tag": "button",
                "name": "Open product",
                "enabled": True,
            }
        ],
    )
    planner = CoveragePlanner(inventory.url)
    cart_key = planner.observe(cart)
    planner.observe(inventory)
    planner.restoration_target = cart_key

    settlement = planner.abandon_stalled_restoration()

    assert settlement is not None
    assert settlement["blocked_candidates"] == 1
    assert settlement["blocked_moves"] == [
        {"name": "Checkout", "action": "click", "href": None}
    ]
    assert planner.context(cart)["current_untried"] == []
    assert [move["name"] for move in planner.context(inventory)["current_untried"]] == [
        "Open product"
    ]
    assert planner.can_finish() is False
    assert planner.coverage_complete() is False


def test_unexpected_restore_snapshot_keeps_untested_live_controls_eligible():
    current = observation("https://shop.test/inventory", [])
    target = observation(
        "https://shop.test/cart",
        [{"ref": "e0", "tag": "button", "name": "Checkout", "enabled": True}],
    )
    unexpected = observation(
        "https://shop.test/cart",
        [
            {
                "ref": "e8",
                "tag": "button",
                "name": "Continue shopping",
                "enabled": True,
            }
        ],
    )
    planner = CoveragePlanner(current.url)
    current_key = planner.observe(current)
    target_key = planner.observe(target)
    planner.restoration_target = target_key
    planner._issued_restore_step = _IssuedRestoreStep(
        kind="navigate",
        target_key=target_key,
        source_key=current_key,
        expected_key=target_key,
    )

    planner.record_action(
        current,
        AgentDecision("navigate", "restore", url=target.url),
        result("navigate", current.url, target.url),
        unexpected,
    )

    assert [item["name"] for item in planner.context(unexpected)["current_untried"]] == ["Continue shopping"]
    assert planner.context(unexpected)["frontier_states"] == 2


def test_restored_checkout_variant_keeps_form_and_setup_before_header_cart():
    root = observation("https://shop.test/inventory", [])
    elements = [
        {"ref": "e0", "tag": "a", "name": "Cart, 6 items", "href": "https://shop.test/cart", "document_top": 0, "enabled": True},
        *[{"ref": f"e{i}", "tag": "input", "type": "text", "name": name, "form": "checkout", "has_value": True, "enabled": True, "document_top": 100 + i} for i, name in enumerate(["First Name", "Last Name", "Postal Code"], 1)],
        {"ref": "e4", "tag": "input", "type": "submit", "name": "Continue", "form": "checkout", "enabled": True, "document_top": 400},
    ]
    checkout = observation("https://shop.test/checkout", elements, title="Checkout")
    planner = CoveragePlanner(root.url)
    root_key = planner.observe(root)
    target_key = planner.observe(checkout)
    target_state = planner.states[target_key]
    for fingerprint, candidate in target_state.candidates.items():
        if planner._move_action(candidate) == "fill":
            target_state.attempted.add(fingerprint)
            planner.global_route_moves_attempted.add((target_state.url, fingerprint))
    blank_variant = observation(checkout.url, [
        {**item, "has_value": False} if item["tag"] == "input" and item.get("type") == "text" else {**item, "name": "Cart, empty"} if item["tag"] == "a" else item
        for item in elements
    ] + [{"ref": "e5", "tag": "button", "name": "Help", "enabled": True}], title="Checkout")
    planner.restoration_target = target_key
    planner._issued_restore_step = _IssuedRestoreStep(kind="navigate", target_key=target_key, source_key=root_key, expected_key=target_key)
    planner.record_action(root, AgentDecision("navigate", "restore", url=checkout.url), result("navigate", root.url, checkout.url), blank_variant)
    assert planner.restoration_target is None
    assert {item["name"] for item in planner.context(blank_variant)["current_untried"]} >= {"First Name", "Last Name", "Postal Code", "Continue", "Help"}
    current = blank_variant
    for field_ref in ("e1", "e2", "e3", "e4"):
        decision, _ = planner.reconcile(current, AgentDecision("click", "leave prematurely", target="e0"))
        assert decision.target == field_ref
        after = observation(current.url, [{**item, "has_value": True} if item["ref"] == field_ref else item for item in current.interactive_elements], title="Checkout")
        planner.record_action(current, decision, result(decision.action, current.url, after.url), after)
        current = after
    assert planner.states[planner.current_key].form_setup_required == set()


def test_cart_badge_count_is_not_a_new_navigation_target_or_state():
    planner = CoveragePlanner("https://shop.test/inventory")
    empty = {"ref": "e0", "tag": "a", "name": "Cart, empty", "href": "https://shop.test/cart", "context": "header Cart, empty"}
    full = {**empty, "ref": "e90", "name": "Cart, 6 items", "context": "header Cart, 6 items"}
    assert semantic_element_fingerprint(empty) == semantic_element_fingerprint(full)
    assert planner.state_key(observation(planner.start_url, [empty])) == planner.state_key(observation(planner.start_url, [full]))


def test_form_validation_messages_do_not_create_duplicate_fill_or_submit_moves():
    planner = CoveragePlanner("https://shop.test/checkout")
    base = [{"ref": "e0", "tag": "input", "type": "text", "name": "Last Name", "form": "form#checkout", "context": "form checkout Continue"},
            {"ref": "e1", "tag": "input", "type": "submit", "name": "Continue", "form": "form#checkout", "context": "form checkout Continue"}]
    failed = [{**el, "ref": f"e{n+9}", "context": "form checkout Error Last Name is required Continue"} for n, el in enumerate(base)]
    assert planner.state_key(observation(planner.start_url, base)) == planner.state_key(observation(planner.start_url, failed))
    row = {**base[0], "context": "tr Product A Amount"}
    other_row = {**row, "context": "tr Product B Amount"}
    assert semantic_element_fingerprint(row) != semantic_element_fingerprint(other_row)


def test_form_dismiss_control_is_not_reinvented_for_each_validation_message():
    dismiss = {"ref": "e0", "tag": "button", "type": "button", "name": "Dismiss error",
               "form": "form#checkout", "context": "form checkout Error First name is required Continue"}
    another = {**dismiss, "ref": "e9", "context": "form checkout Error Last name is required Continue"}
    assert semantic_element_fingerprint(dismiss) == semantic_element_fingerprint(another)
    assert semantic_element_fingerprint(dismiss) != semantic_element_fingerprint(
        {**dismiss, "form": "form#delivery"})
    # Row-specific controls in a form still preserve their contextual identity.
    row = {**dismiss, "name": "Cancel item", "context": "tr Product A Cancel item"}
    assert semantic_element_fingerprint(row) != semantic_element_fingerprint(
        {**row, "context": "tr Product B Cancel item"})


def test_current_screen_controls_are_exhausted_before_global_navigation():
    page = observation(
        "https://app.test/#/users",
        [
            {
                "ref": "e0",
                "tag": "button",
                "name": "Relatórios",
                "enabled": True,
                "in_navigation": True,
                "context": "aside Navegação principal",
                "document_top": 10,
            },
            {
                "ref": "e1",
                "tag": "button",
                "name": "Detalhes do usuário",
                "enabled": True,
                "document_top": 400,
            },
        ],
    )
    planner = CoveragePlanner(page.url)

    decision, _ = planner.reconcile(page, AgentDecision("finish", "cover"))

    assert decision.target == "e1"


def test_global_sidebar_is_not_used_to_leave_a_child_before_backtracking():
    root_elements = [
        {
            "ref": "e0",
            "tag": "button",
            "name": "Area A",
            "enabled": True,
            "in_navigation": True,
            "context": "aside Navegação principal",
        },
        {
            "ref": "e1",
            "tag": "button",
            "name": "Area B",
            "enabled": True,
            "in_navigation": True,
            "context": "aside Navegação principal",
        },
    ]
    root = observation("https://app.test/#/", root_elements)
    child = observation(
        "https://app.test/#/a",
        [
            {**root_elements[1], "ref": "e7"},
            {
                "ref": "e8",
                "tag": "button",
                "name": "Ver detalhes",
                "enabled": True,
            },
        ],
    )
    planner = CoveragePlanner(root.url)
    enter, _ = planner.reconcile(root, AgentDecision("finish", "cover"))
    planner.record_action(root, enter, result("click", root.url, child.url), child)

    local, _ = planner.reconcile(child, AgentDecision("finish", "cover"))
    assert local.target == "e8"
    planner.record_action(child, local, result("click", child.url, child.url), child)
    leave, reason = planner.reconcile(child, AgentDecision("finish", "cover"))

    assert leave.action == "go_back"
    assert reason == "finish_replaced_by_backtrack"


@pytest.mark.asyncio
@pytest.mark.parametrize(("headless", "full_page"), [(False, False), (True, True)])
async def test_screenshot_does_not_scroll_a_visible_browser(
    tmp_path, headless, full_page
):
    class EmptyLocator:
        async def count(self):
            return 0

    class FakePage:
        def __init__(self):
            self.screenshot_options = None

        async def evaluate(self, *_args):
            return None

        def locator(self, *_args):
            return EmptyLocator()

        async def screenshot(self, **kwargs):
            self.screenshot_options = kwargs

    executor = PlaywrightExecutor(
        tmp_path,
        ExplorationOptions(capture_screenshots=True),
        headless=headless,
    )
    executor._page = FakePage()

    await executor._capture("coverage")

    assert executor._page.screenshot_options["full_page"] is full_page
    assert executor._page.screenshot_options["mask_color"] == "#101416"


@pytest.mark.asyncio
async def test_observed_ref_uses_stored_selector_without_dom_marker(tmp_path):
    class FakeLocator:
        async def count(self):
            return 1

    class FakePage:
        def __init__(self):
            self.selector = None

        def locator(self, selector):
            self.selector = selector
            return FakeLocator()

    executor = PlaywrightExecutor(tmp_path, ExplorationOptions())
    executor._page = FakePage()
    executor._ref_selectors = {
        "e0": "html > body:nth-of-type(1) > button:nth-of-type(2)"
    }

    await executor._locator("e0")

    assert executor._page.selector == executor._ref_selectors["e0"]
    assert "data-autojev-ref" not in executor._page.selector


def test_all_abandoned_candidates_are_individually_auditable_without_truncation():
    target = observation("https://app.test/branch", [
        {"ref": f"e{i}", "tag": "button", "name": f"Option {i}", "enabled": True}
        for i in range(45)
    ])
    planner = CoveragePlanner("https://app.test/")
    key = planner.observe(target)
    planner.restoration_target = key
    settlement = planner.abandon_stalled_restoration(reason="no_safe_restore_route")
    assert settlement["blocked_candidates"] == 45
    assert len(settlement["blocked_moves"]) == 45
    context = planner.context(target)
    assert len(context["restrictions"]) == 45
    assert context["unreachable_restorations"] == [settlement]
    assert context["coverage_complete"] is False
    assert len(planner.drain_restrictions()) == 45
    assert planner.drain_restrictions() == []
    planner.restoration_target = key
    planner.abandon_stalled_restoration(reason="no_safe_restore_route")
    assert len(planner.restrictions) == 45


def test_action_retry_restriction_is_not_lost_when_no_restoration_happens():
    page = observation("https://app.test/", [
        {"ref": "e0", "tag": "button", "name": "Unavailable action", "enabled": True}
    ])
    planner = CoveragePlanner(page.url)
    decision = AgentDecision("click", "exercise", target="e0")
    for _ in range(2):
        planner.record_action(page, decision, result("click", page.url, page.url, success=False), page)
    records = planner.drain_restrictions()
    assert len(records) == 1
    assert records[0]["reason"] == "action_retry_exhausted"
    assert records[0]["name"] == "Unavailable action"
    assert planner.can_finish() and not planner.coverage_complete()


def test_spa_deep_link_is_not_assumed_to_support_document_get():
    root = observation("https://app.test/", [
        {"ref": "e0", "tag": "a", "name": "Open details", "href": "https://app.test/items/1"}
    ])
    detail = observation("https://app.test/items/1", [
        {"ref": "e1", "tag": "button", "name": "Read more"}
    ])
    planner = CoveragePlanner(root.url)
    click = AgentDecision("click", "open", target="e0")
    planner.record_action(root, click, result("click", root.url, detail.url), detail)
    key = planner.state_key(detail)
    assert not planner._url_restorable(key)
    planner._observe_document_response({"url": detail.url, "status_code": 200})
    assert planner._url_restorable(key)
    planner._observe_document_response({"url": detail.url, "status_code": 404})
    assert not planner._url_restorable(key)


def test_authenticated_exploration_root_does_not_imply_get_supported():
    planner = CoveragePlanner("https://app.test/")
    authenticated = observation("https://app.test/inventory.html", [
        {"ref": "e0", "tag": "button", "name": "Open Menu"},
    ])
    authenticated.page_state["document_response"] = {"url": "https://app.test/", "status_code": 200}
    key = planner.observe(authenticated)
    assert key == planner.coverage_root_key
    assert not planner._url_restorable(key)
    planner._observe_document_response({"url": authenticated.url, "status_code": 200})
    assert planner._url_restorable(key)


def test_focus_anchor_requires_document_proof_when_login_changed_route():
    planner = CoveragePlanner("https://app.test/", "Orders")
    authenticated = observation("https://app.test/inventory.html", [
        {"ref": "e0", "tag": "button", "name": "Open Orders"},
    ])
    key = planner.observe(authenticated)
    planner.focus_entry_anchor = key
    assert not planner._url_restorable(key)
    planner._observe_document_response({"url": authenticated.url, "status_code": 200})
    assert planner._url_restorable(key)


def test_authenticated_root_backtrack_does_not_issue_unproven_deep_get():
    planner = CoveragePlanner("https://app.test/")
    authenticated = observation("https://app.test/inventory.html", [])
    other = observation("https://app.test/cart.html", [])
    root_key = planner.observe(authenticated)
    other_key = planner.observe(other)
    planner.path = [root_key, other_key]
    planner.backtrack_failures[other_key] = 1
    choice = planner._backtrack_decision()
    assert choice is None or choice[0].action != "navigate"
    assert authenticated.url not in planner.failed_document_urls


def test_known_reverse_transition_is_replayed_before_history_or_get():
    home = observation("https://app.test/home", [
        {"ref": "e0", "tag": "button", "name": "Open product"},
        {"ref": "e1", "tag": "button", "name": "Open second product"},
    ])
    detail = observation("https://app.test/detail/1", [
        {"ref": "e2", "tag": "button", "name": "Back to products"}
    ])
    planner = CoveragePlanner(home.url)
    enter = AgentDecision("click", "open", target="e0")
    back = AgentDecision("click", "return", target="e2")
    planner.record_action(home, enter, result("click", home.url, detail.url), detail)
    planner.record_action(detail, back, result("click", detail.url, home.url), home)
    planner.record_action(home, enter, result("click", home.url, detail.url), detail)
    restore = planner.next_restoration_action(detail)
    assert restore[0].action == "click"
    assert restore[0].target == "e2"
    assert restore[1] == "frontier_restore_replay"


def test_obstructing_menu_is_closed_even_when_close_move_was_already_covered():
    page = observation("https://app.test/cart", [
        {"ref": "e0", "tag": "button", "name": "Continue Shopping", "interactable": False, "obstructed_by_overlay": True},
        {"ref": "e1", "tag": "button", "name": "Close Menu", "interactable": True, "in_overlay": True, "overlay_dismiss": True},
    ])
    planner = CoveragePlanner(page.url)
    state = planner.states[planner.observe(page)]
    state.attempted.update(state.candidates)
    action, reason = planner.next_restoration_action(page)
    assert action.target == "e1"
    assert reason == "obstructing_overlay_dismissed"
    # A dialog with entered fields must never be auto-dismissed by inference.
    unsafe = observation(page.url, [page.interactive_elements[0],
                                  {"ref": "e2", "tag": "button", "name": "Close Dialog", "interactable": True, "in_overlay": True}])
    planner.observe(unsafe)
    assert planner._overlay_dismissal(unsafe) is None


def test_each_required_field_is_tested_empty_before_departing_happy_form_path():
    url = "https://app.test/profile"
    elements = [
        {"ref": f"e{i}", "tag": "input", "type": "text", "name": name,
         "form": "profile", "required": True, "has_value": False}
        for i, name in enumerate(["Given name", "Family name", "Postal code"])
    ] + [{"ref": "e3", "tag": "button", "type": "submit", "name": "Continue", "form": "profile"}]
    current = observation(url, elements)
    planner = CoveragePlanner(url)
    assert planner.next_restoration_action(current) is None
    # First exercise ordinary inputs. Negatives stay hidden until those real
    # attempts, then run before a positive submit can leave this live form.
    for index in range(3):
        decision, _ = planner.reconcile(current, AgentDecision("finish", "cover"))
        assert decision.target == f"e{index}"
        assert not decision.scenario_hint
        updated = observation(url, [
            {**field, "has_value": True} if field["ref"] == decision.target and decision.action == "fill" else field
            for field in current.interactive_elements
        ])
        planner.record_action(current, decision, result(decision.action, url, url), updated)
        current = updated
    assert not planner.form_happy_paths_completed
    assert len(planner.context(current)["current_untried"]) == 4
    probed = []
    for _ in range(12):
        mechanical = planner.next_restoration_action(current)
        if mechanical is None:
            break
        decision, reason = mechanical
        assert reason == "isolated_required_field_probe"
        if decision.action == "click":
            empty = [field["name"] for field in current.interactive_elements
                     if field.get("required") and not field["has_value"]]
            assert len(empty) == 1
            assert decision.scenario_hint["validation_probe"]["name"] == empty[0]
            probed.append(empty[0])
        updated = observation(url, [
            {**field, "has_value": bool(decision.value)}
            if field["ref"] == decision.target and decision.action == "fill" else field
            for field in current.interactive_elements
        ], text="Required field rejected" if decision.action == "click" else "")
        planner.record_action(current, decision, result(decision.action, url, url), updated)
        current = updated
    assert probed == ["Given name", "Family name", "Postal code"]
    assert planner.next_restoration_action(current) is None
    assert not planner.can_finish()
    refill, _ = planner.reconcile(current, AgentDecision("finish", "cover"))
    assert refill.action == "fill" and refill.target == "e2" and refill.value
    restored = observation(url, [{**field, "has_value": True} if field["ref"] == "e2" else field
                                 for field in current.interactive_elements])
    planner.record_action(current, refill, result("fill", url, url), restored)
    submit, _ = planner.reconcile(restored, AgentDecision("finish", "cover"))
    assert submit.action == "click" and submit.target == "e3" and not submit.scenario_hint
    assert not planner.form_happy_paths_completed
    planner.record_action(restored, submit, result("click", url, url), restored)
    assert planner.form_happy_paths_completed == {(url, "profile")}
    assert planner.can_finish()


def test_form_submit_replay_restores_only_generated_synthetic_values():
    form = observation("https://app.test/start", [
        {"ref": "e0", "tag": "input", "type": "text", "name": "Search", "form": "search", "has_value": False},
        {"ref": "e1", "tag": "button", "type": "submit", "name": "Search", "form": "search"},
    ])
    results = observation("https://app.test/results", [{"ref": "e2", "tag": "button", "name": "Inspect results"}])
    planner = CoveragePlanner(form.url)
    fill, _ = planner.reconcile(form, AgentDecision("finish", "cover"))
    filled = observation(form.url, [{**field, "has_value": True} if field["ref"] == "e0" else field for field in form.interactive_elements])
    planner.record_action(form, fill, result("fill", form.url, form.url), filled)
    submit, _ = planner.reconcile(filled, AgentDecision("finish", "cover"))
    planner.record_action(filled, submit, result("click", form.url, results.url), results)
    planner.observe(form)
    planner.path = [planner.state_key(form)]
    planner.restoration_target = planner.state_key(results)
    replay, reason = planner.next_restoration_action(form)
    assert reason == "frontier_restore_form_setup"
    assert replay.action == "fill" and replay.target == "e0"
    assert replay.value == "AutoJev QA search"
    planner.record_action(form, replay, result("fill", form.url, form.url), filled)
    replay, reason = planner.next_restoration_action(filled)
    assert reason == "frontier_restore_replay"
    assert replay.target == "e1"


def test_invalid_fill_precondition_does_not_create_infinite_negative_probe_retry():
    form = observation("https://app.test/form", [
        {"ref": "e0", "tag": "input", "name": "Given name", "form": "f", "required": True, "has_value": False},
        {"ref": "e1", "tag": "input", "name": "Family name", "form": "f", "required": True, "has_value": False},
        {"ref": "e2", "tag": "button", "type": "submit", "name": "Continue", "form": "f"},
    ])
    planner = CoveragePlanner(form.url)
    current = form
    for ref in ["e0", "e1"]:
        decision, _ = planner.reconcile(current, AgentDecision("finish", "cover"))
        assert decision.target == ref
        # The broken application always clears Family name after its fill.
        after = observation(form.url, [
            {**field, "has_value": True} if field["ref"] == ref == "e0" else field
            for field in current.interactive_elements
        ])
        planner.record_action(current, decision, result(decision.action, form.url, form.url), after)
        current = after
    probed = []
    for _ in range(8):
        selected = planner.next_restoration_action(current)
        if selected is None:
            break
        decision, _ = selected
        assert not (decision.action == "fill" and decision.target == "e1" and decision.value)
        if decision.action == "click":
            probed.append(decision.scenario_hint["validation_probe"])
        after = observation(form.url, [
            {**field, "has_value": bool(decision.value)}
            if decision.action == "fill" and field["ref"] == decision.target else field
            for field in current.interactive_elements
        ])
        planner.record_action(current, decision, result(decision.action, form.url, form.url), after)
        current = after
    # A broken peer must not stop testing the intentionally empty field or
    # another target whose feedback remains attributable. Native attribution
    # and masked-probe BLOCKED verdicts are covered by test_form_probe_coverage.
    assert [probe["name"] for probe in probed] == ["Given name", "Family name"]
    assert [probe["strategy"] for probe in probed] == ["blocked_peer_observed", "isolated_required_field"]
    assert planner.drain_restrictions() == []
    assert planner.next_restoration_action(current) is None
    assert not planner.can_finish()
    submit, _ = planner.reconcile(current, AgentDecision("finish", "cover"))
    assert submit.action == "click" and submit.target == "e2" and not submit.scenario_hint
    planner.record_action(current, submit, result("click", form.url, form.url), current)
    assert planner.can_finish() and planner.coverage_complete()


def test_document_404_prevents_repeated_get_recovery_for_any_dom_variant():
    root = observation("https://app.test/spa", [
        {"ref": "e0", "tag": "button", "name": "Root action"}
    ])
    current = observation("https://app.test/other", [])
    planner = CoveragePlanner(root.url)
    root_key = planner.observe(root)
    current_key = planner.observe(current)
    planner.path = [current_key]
    planner.restoration_target = root_key
    restore, reason = planner.next_restoration_action(current)
    assert restore.action == "navigate" and reason == "frontier_restore_navigate"
    not_found = observation(root.url, [], text="Not found")
    outcome = result("navigate", current.url, root.url)
    outcome.evidence["navigation_response"] = {"url": root.url, "status_code": 404}
    planner.record_action(current, restore, outcome, not_found)
    assert planner.next_restoration_action(not_found) is None
    assert root.url in planner.failed_document_urls
    assert not planner._url_restorable(root_key)
    assert planner.can_finish() and not planner.coverage_complete()
    assert planner.restrictions[0]["reason"] == "no_safe_restore_route"


def test_overlay_dismissal_does_not_undo_an_intentional_menu_replay():
    root = observation("https://app.test/", [
        {"ref": "e0", "tag": "button", "name": "Open Menu", "interactable": True},
        {"ref": "e1", "tag": "button", "name": "Background action", "interactable": True},
    ])
    menu = observation(root.url, [
        {"ref": "e1", "tag": "button", "name": "Background action", "interactable": False, "obstructed_by_overlay": True},
        {"ref": "e2", "tag": "button", "name": "Close Menu", "interactable": True, "in_overlay": True, "overlay_dismiss": True},
        {"ref": "e3", "tag": "a", "name": "Open workspace", "href": "https://app.test/workspace", "interactable": True, "in_overlay": True},
    ])
    workspace = observation("https://app.test/workspace", [
        {"ref": "e4", "tag": "button", "name": "Explore workspace"}
    ])
    planner = CoveragePlanner(root.url)
    open_menu = AgentDecision("click", "menu", target="e0")
    open_workspace = AgentDecision("click", "workspace", target="e3")
    planner.record_action(root, open_menu, result("click", root.url, menu.url), menu)
    planner.record_action(menu, open_workspace, result("click", menu.url, workspace.url), workspace)
    planner.observe(root)
    planner.path = [planner.state_key(root)]
    planner.restoration_target = planner.state_key(workspace)
    replay, _ = planner.next_restoration_action(root)
    planner.record_action(root, replay, result("click", root.url, menu.url), menu)
    replay, reason = planner.next_restoration_action(menu)
    assert replay.target == "e3", "Do not close the menu before its safe recipe continuation"
    assert reason == "frontier_restore_replay"


def test_known_404_model_navigation_is_replaced_without_sending_another_get():
    page = observation("https://app.test/cart", [
        {"ref": "e0", "tag": "button", "name": "Safe local action"}
    ])
    planner = CoveragePlanner(page.url)
    planner.observe(page)
    planner.failed_document_urls.add("https://app.test/unsupported")
    decision, reason = planner.reconcile(page, AgentDecision("navigate", "guess", url="https://app.test/unsupported"))
    assert decision.action == "click" and decision.target == "e0"
    assert reason == "unsupported_document_navigation_replaced"


def test_successful_restore_cycles_are_bounded_even_when_steps_reset():
    root = observation("https://app.test/", [
        {"ref": "e0", "tag": "button", "name": "Open panel"}
    ])
    panel = observation(root.url, [{"ref": "e1", "tag": "button", "name": "Open nested"}], title="Panel")
    nested = observation(root.url, [{"ref": "e2", "tag": "button", "name": "Pending action"}], title="Nested")
    planner = CoveragePlanner(root.url)
    enter = AgentDecision("click", "enter", target="e0")
    planner.record_action(root, enter, result("click", root.url, panel.url), panel)
    planner.record_action(panel, AgentDecision("click", "nested", target="e1"), result("click", panel.url, nested.url), nested)
    planner.observe(root)
    target_key = planner.state_key(nested)
    for _ in range(2):
        planner.restoration_target = target_key
        planner.restoration_steps = 0
        replay, reason = planner.next_restoration_action(root)
        assert reason == "frontier_restore_replay"
        planner.record_action(root, replay, result("click", root.url, panel.url), panel)
        # External app interaction closes the intermediate state without
        # reaching the intended nested target. This used to reset the debt.
        planner.observe(root)
        planner.path = [planner.state_key(root)]
    planner.restoration_target = target_key
    planner.restoration_steps = 0
    replay = planner.next_restoration_action(root)
    assert replay is None or replay[0].action != "click"


def test_distinct_product_handlers_with_shared_placeholder_href_are_all_covered():
    inventory = observation("https://app.test/inventory", [
        {"ref": f"e{i}", "tag": "a", "name": f"Product {i}",
         "context": f"Product card {i}", "href": "https://app.test/inventory#"}
        for i in range(6)
    ])
    planner = CoveragePlanner(inventory.url)
    visited = []
    for i in range(6):
        decision, _ = planner.reconcile(inventory, AgentDecision("finish", "cover"))
        assert decision.target == f"e{i}"
        detail = observation(f"https://app.test/detail?id={i}", [])
        planner.record_action(inventory, decision, result("click", inventory.url, detail.url), detail)
        visited.append(detail.url)
        planner.observe(inventory)
        planner.path = [planner.state_key(inventory)]
    assert len(set(visited)) == 6
    assert not planner.global_destinations_attempted
    assert planner.context(inventory)["current_untried"] == []


def test_different_named_entity_links_to_same_real_url_are_not_silently_skipped():
    page = observation("https://app.test/catalog", [
        {"ref": "e0", "tag": "a", "name": "Correct product", "href": "https://app.test/detail/1", "context": "Card A"},
        {"ref": "e1", "tag": "a", "name": "Different product", "href": "https://app.test/detail/1", "context": "Card B"},
    ])
    detail = observation("https://app.test/detail/1", [])
    planner = CoveragePlanner(page.url)
    planner.record_action(page, AgentDecision("click", "exercise first", target="e0"), result("click", page.url, detail.url), detail)
    planner.observe(page)
    assert [move["ref"] for move in planner.context(page)["current_untried"]] == ["e1"]


def test_expanding_navigation_does_not_reinvent_its_existing_controls():
    parent = {"ref": "e0", "tag": "a", "name": "Catalog", "enabled": True,
              "in_navigation": True, "raw_href": "#",
              "href": "https://app.test/cart#", "navigation_owner": "nav:id=main-menu",
              "context": "nav Main menu Products Catalog Reset"}
    reset = {**parent, "ref": "e1", "name": "Reset demo state"}
    base = observation("https://app.test/cart", [parent, reset])
    expanded = observation(base.url, [
        {**parent, "ref": "e8", "context": "nav Main menu Products Catalog Lazy Spinner Reset"},
        {**reset, "ref": "e9", "context": "nav Main menu Products Catalog Lazy Spinner Reset"},
        {**parent, "ref": "e10", "name": "Lazy", "context": "nav Main menu Products Catalog Lazy Spinner Reset"},
        {**parent, "ref": "e11", "name": "Spinner", "context": "nav Main menu Products Catalog Lazy Spinner Reset"},
    ])
    planner = CoveragePlanner(base.url)
    planner.record_action(base, AgentDecision("click", "expand", target="e0"),
                          result("click", base.url, expanded.url), expanded)
    remaining = planner.context(expanded)["current_untried"]
    assert {move["name"] for move in remaining} == {"Lazy", "Spinner"}
    # Existing sibling debt stays at its logical root, not in every expanded
    # submenu snapshot. Newly revealed children must still be visited here.
    assert {move["name"] for _, move in planner._remaining(planner.states[planner.state_key(base)])} == {"Reset demo state"}
    assert semantic_element_fingerprint(parent) == semantic_element_fingerprint(expanded.interactive_elements[0])


def test_placeholder_global_menu_is_stable_across_route_and_cart_variants():
    menu = {"ref": "e0", "tag": "a", "name": "All items", "enabled": True,
            "in_navigation": True, "raw_href": "#", "href": "https://app.test/cart#",
            "navigation_owner": "nav:id=main-menu", "context": "nav All items Catalog Reset"}
    cart = observation("https://app.test/cart", [menu])
    catalog = observation("https://app.test/catalog", [
        {**menu, "ref": "e7", "href": "https://app.test/catalog#",
         "context": "nav All items Catalog Lazy Spinner Slider Reset"}
    ])
    planner = CoveragePlanner(cart.url)
    planner.record_action(cart, AgentDecision("click", "open inventory", target="e0"),
                          result("click", cart.url, catalog.url), catalog)
    assert planner.context(catalog)["current_untried"] == []
    assert planner.can_finish()
    legacy = {key: value for key, value in menu.items() if key not in {"raw_href", "navigation_owner"}}
    other_legacy = {**legacy, "href": "https://app.test/catalog#", "context": "nav All items Catalog Lazy Spinner Reset"}
    assert semantic_element_fingerprint(legacy) == semantic_element_fingerprint(other_legacy)


def test_navigation_stable_owner_and_real_destinations_remain_distinct():
    menu = {"tag": "a", "name": "Details", "in_navigation": True,
            "raw_href": "/orders/1", "href": "https://app.test/orders/1",
            "navigation_owner": "nav:id=main", "context": "nav Details"}
    assert semantic_element_fingerprint(menu) != semantic_element_fingerprint(
        {**menu, "navigation_owner": "nav:id=account"})
    assert semantic_element_fingerprint(menu) != semantic_element_fingerprint(
        {**menu, "raw_href": "/orders/2", "href": "https://app.test/orders/2"})
    assert semantic_element_fingerprint(menu) != semantic_element_fingerprint(
        {**menu, "raw_href": "#/orders/1", "href": "https://app.test/#/orders/1"})
    homonym = {**menu, "href": "https://app.test/#", "raw_href": "#",
               "navigation_control": "a:id=primary-details"}
    assert semantic_element_fingerprint(homonym) != semantic_element_fingerprint(
        {**homonym, "navigation_control": "a:id=secondary-details"})


def test_menu_toggle_identity_does_not_depend_on_header_badge_or_submenu_text():
    toggle = {"ref": "e0", "tag": "button", "name": "Open Menu", "enabled": True,
              "context": "div Header Swag Labs Cart 1"}
    full = {**toggle, "ref": "e9", "context": "div Header Swag Labs Cart 2"}
    assert semantic_element_fingerprint(toggle) == semantic_element_fingerprint(full)
    close = {**toggle, "name": "Close Menu", "overlay_group": "aside:nth-of-type(1)",
             "context": "aside All items Catalog Reset Close Menu"}
    expanded = {**close, "context": "aside All items Catalog Lazy Spinner Slider Reset Close Menu"}
    assert semantic_element_fingerprint(close) == semantic_element_fingerprint(expanded)
    assert semantic_element_fingerprint(close) != semantic_element_fingerprint(
        {**close, "overlay_group": "aside:nth-of-type(2)"})


def test_covered_global_menu_does_not_restore_every_historical_cart_variant():
    planner = CoveragePlanner("https://app.test/cart")

    def menu(url, badge, expanded=False):
        names = ["All items", "Catalog", "Close Menu"]
        if expanded:
            names.extend(["Lazy", "Spinner", "Slider"])
        context = f"nav {badge} " + " ".join(names)
        return observation(url, [
            {"ref": f"e{i}", "tag": "a" if name != "Close Menu" else "button",
             "name": name, "enabled": True, "in_navigation": name != "Close Menu",
             "href": f"{url}#" if name != "Close Menu" else None,
             "raw_href": "#" if name != "Close Menu" else None,
             "navigation_owner": "nav:id=global-menu", "context": context,
             "overlay_group": "aside:nth-of-type(1)"}
            for i, name in enumerate(names)
        ])

    # These observed snapshots used to retain different menu identities solely
    # because expansion, route or cart badge changed. They must not demand a
    # replay of all the add/remove prerequisites once their controls are done.
    variants = [menu(url, count, expanded) for url in
                ["https://app.test/cart", "https://app.test/lazy", "https://app.test/spinner"]
                for count in range(3) for expanded in [False, True]]
    for page in variants:
        planner.observe(page)
    for page in variants:
        for element in page.interactive_elements:
            if element["name"] == "Slider":
                continue
            decision = AgentDecision("click", "exercise semantic control", target=element["ref"])
            planner.record_action(page, decision, result("click", page.url, page.url), page)
    assert {move["name"] for state in planner._frontier_states()
            for _, move in planner._remaining(state)} == {"Slider"}
    # Cover the genuinely new child once, then none of the 18 old snapshots
    # has outstanding coverage. No hard action ceiling or silent abandon.
    page = variants[-1]
    slider = next(item for item in page.interactive_elements if item["name"] == "Slider")
    planner.record_action(page, AgentDecision("click", "exercise child", target=slider["ref"]),
                          result("click", page.url, page.url), page)
    assert planner.can_finish()
    assert not planner._frontier_states()
    assert not planner.restrictions


def shopping_observation(url: str, elements: list[dict], count: int, *, checkout: dict | None = None) -> Observation:
    page = observation(url, elements, title="Checkout" if "checkout" in url else "Products")
    page.page_state = {
        "cart": {"count": count, "items_known": False,
                 "link_refs": [item["ref"] for item in elements if str(item.get("name", "")).startswith("Cart")]},
        "checkout": checkout,
    }
    return page


def test_checkout_journey_identity_distinguishes_empty_from_populated_not_each_basket():
    url = "https://shop.test/cart"
    elements = [{"ref": "e0", "tag": "button", "name": "Checkout"}]
    planner = CoveragePlanner(url)
    empty = shopping_observation(url, elements, 0)
    populated = [shopping_observation(url, elements, count) for count in (1, 2, 6, 99)]
    empty_key = planner.state_key(empty)
    assert all(planner.state_key(page) != empty_key for page in populated)
    assert len({planner.state_key(page) for page in populated}) == 1
    planner.record_action(empty, AgentDecision("click", "empty-cart probe", target="e0"),
                          result("click", url, url), empty)
    assert planner.context(empty)["current_untried"] == []
    assert [item["name"] for item in planner.context(populated[0])["current_untried"]] == ["Checkout"]
    planner.record_action(populated[0], AgentDecision("click", "populated-cart probe", target="e0"),
                          result("click", url, url), populated[0])
    assert all(planner.context(page)["current_untried"] == [] for page in populated)


def test_cart_entry_reopens_populated_lane_after_empty_navigation_was_covered():
    url = "https://shop.test/inventory"
    element = {"ref": "e0", "tag": "a", "name": "Cart, empty", "href": "https://shop.test/cart",
               "in_navigation": True, "navigation_owner": "nav:header"}
    empty = shopping_observation(url, [element], 0)
    cart = shopping_observation(element["href"], [], 0)
    planner = CoveragePlanner(url)
    planner.record_action(empty, AgentDecision("click", "empty cart", target="e0"),
                          result("click", url, cart.url), cart)
    assert cart.url in planner.global_destinations_attempted
    populated = shopping_observation(url, [{**element, "ref": "e9", "name": "Cart, 2 items"}], 2)
    decision, reason = planner.next_restoration_action(populated)
    assert decision.target == "e9"
    assert reason == "populated_checkout_prioritized"


def test_checkout_with_products_is_prioritized_before_removals_reset_or_cancel():
    url = "https://shop.test/cart"
    page = shopping_observation(url, [
        {"ref": "e0", "tag": "button", "name": "Remove", "reversible_cart_action": "remove"},
        {"ref": "e1", "tag": "button", "name": "Reset App State"},
        {"ref": "e2", "tag": "button", "name": "Cancel"},
        {"ref": "e3", "tag": "button", "name": "Checkout"},
    ], 2)
    planner = CoveragePlanner(url)
    decision, reason = planner.reconcile(page, AgentDecision("click", "remove next", target="e0"))
    assert decision.target == "e3"
    assert reason == "populated_checkout_prioritized"
    planner.record_action(page, decision, result("click", url, url), page)
    decision, reason = planner.reconcile(page, AgentDecision("click", "remove next", target="e0"))
    assert decision.target == "e0"  # Covered lane does not block later removal tests.
    assert reason is None


def test_populated_checkout_form_is_new_after_the_empty_form_happy_path():
    url = "https://shop.test/checkout-step-one"
    elements = [
        {"ref": "e0", "tag": "input", "type": "text", "name": "First Name", "form": "checkout", "has_value": False},
        {"ref": "e1", "tag": "button", "type": "submit", "name": "Continue", "form": "checkout"},
    ]
    planner = CoveragePlanner(url)
    empty = shopping_observation(url, elements, 0)
    empty_state = planner.states[planner.observe(empty)]
    for key, move in empty_state.candidates.items():
        empty_state.attempted.add(key)
        planner.global_route_moves_attempted.add((url, key))
        if planner._is_form_submit(move):
            planner.form_happy_paths_completed.add((url, planner._form_group(move)))
    populated = shopping_observation(url, elements, 1)
    assert {item["name"] for item in planner.context(populated)["current_untried"]} == {"First Name", "Continue"}
    decision, reason = planner.next_restoration_action(populated)
    assert decision.target == "e0" and decision.action == "fill"
    assert reason == "populated_checkout_prioritized"
    assert planner._form_probe_action(planner.states[planner.current_key]) is None
    filled = shopping_observation(url, [{**item, "has_value": True} if item["ref"] == "e0" else item for item in elements], 1)
    planner.record_action(populated, decision, result("fill", url, url), filled)
    submit, _ = planner.next_restoration_action(filled)
    assert submit.target == "e1"


def test_nonempty_finish_is_not_inherited_from_an_empty_checkout():
    url = "https://shop.test/checkout-step-two"
    elements = [{"ref": "e0", "tag": "button", "name": "Finish"},
                {"ref": "e1", "tag": "button", "name": "Cancel"}]
    checkout = {"stage": "review", "finish_refs": ["e0"]}
    empty = shopping_observation(url, elements, 0, checkout=checkout)
    populated = shopping_observation(url, elements, 2, checkout=checkout)
    planner = CoveragePlanner(url)
    planner.record_action(empty, AgentDecision("click", "empty cancellation", target="e1"), result("click", url, url), empty)
    planner.record_action(empty, AgentDecision("click", "empty order", target="e0"), result("click", url, url), empty)
    decision, reason = planner.reconcile(populated, AgentDecision("click", "cancel", target="e1"))
    assert decision.target == "e1" and reason == "checkout_cancellation_prioritized"
    assert any(move["name"] == "Finish" for _, move in planner._remaining(planner.states[planner.current_key]))
    planner.record_action(populated, decision, result("click", url, url), populated)
    decision, reason = planner.next_restoration_action(populated)
    assert decision.target == "e0" and reason == "populated_checkout_prioritized"
    planner.record_action(populated, decision, result("click", url, url), populated)
    assert planner.next_restoration_action(populated) is None


def test_checkout_review_cancellation_is_one_actual_test_per_empty_and_populated_lane():
    url = "https://shop.test/checkout-step-two"
    elements = [{"ref": "e0", "tag": "button", "name": "Finish"},
                {"ref": "e1", "tag": "a", "name": "Cancel", "href": "https://shop.test/cart"}]
    checkout = {"stage": "review", "finish_refs": ["e0"]}
    planner = CoveragePlanner(url)
    empty = shopping_observation(url, elements, 0, checkout=checkout)
    first, reason = planner.next_restoration_action(empty)
    assert first.target == "e1" and reason == "checkout_cancellation_prioritized"
    empty_review_key = planner.state_key(empty)
    assert planner.restoration_target == empty_review_key
    assert planner._issued_restore_step is None  # a native test, not mechanical replay
    planner.record_action(empty, first, result("click", url, url), empty)
    assert planner._checkout_review_exit_action(planner.states[planner.observe(empty)]) is None
    assert any(move["name"] == "Finish" for _, move in planner._remaining(planner.states[empty_review_key]))

    populated = shopping_observation(url, elements, 1, checkout=checkout)
    second, reason = planner.next_restoration_action(populated)
    assert second.target == "e1" and reason == "checkout_cancellation_prioritized"
    planner.record_action(populated, second, result("click", url, url), populated)
    for count in (1, 2, 6, 99):
        other_basket = shopping_observation(url, elements, count, checkout=checkout)
        state = planner.states[planner.observe(other_basket)]
        assert planner._checkout_review_exit_action(state) is None
        assert any(move["name"] == "Finish" for _, move in planner._remaining(state))
    assert not planner.restrictions


def test_checkout_review_cancellation_priority_requires_grounded_review_and_cart_lane():
    url = "https://shop.test/checkout"
    controls = [{"ref": "e0", "tag": "button", "name": "Cancel"},
                {"ref": "e1", "tag": "button", "name": "Finish"}]
    planner = CoveragePlanner(url)
    ordinary = shopping_observation(url, controls, 2, checkout={"stage": "information"})
    unknown_cart = observation(url, controls)
    unknown_cart.page_state = {"checkout": {"stage": "review"}}
    for page in (ordinary, unknown_cart):
        state = planner.states[planner.observe(page)]
        assert planner._checkout_review_exit_action(state) is None
        assert all(move.get("_cart_journey_role") != "checkout_review_exit"
                   for move in state.candidates.values())


def test_cart_journey_uses_intent_without_mutating_observations_or_claiming_restore():
    url = "https://shop.test/checkout-step-one"
    elements = [{"ref": "e0", "tag": "input", "type": "text", "name": "First Name", "form": "checkout", "has_value": False},
                {"ref": "e1", "tag": "button", "type": "submit", "name": "Continue", "form": "checkout"}]
    empty = shopping_observation(url, elements, 0)
    populated_intent = shopping_observation(url, elements, 0)
    populated_intent.page_state["cart_expectation"] = {"items_known": True, "items": [{"name": "Backpack"}]}
    planner = CoveragePlanner(url)
    empty_key = planner.observe(empty)
    populated_key = planner.observe(populated_intent)
    assert populated_key != empty_key
    assert not planner._matches_pending_state(empty_key, populated_key)
    assert populated_intent.page_state["cart"]["count"] == 0
    assert populated_intent.page_state["cart_expectation"]["items"] == [{"name": "Backpack"}]
    assert planner._cart_journey_bucket(populated_intent) == "populated"


def test_nonshopping_form_and_global_menu_do_not_gain_cart_variants():
    url = "https://shop.test/account"
    elements = [{"ref": "e0", "tag": "input", "type": "text", "name": "Name", "form": "profile", "has_value": False},
                {"ref": "e1", "tag": "button", "type": "submit", "name": "Save", "form": "profile"},
                {"ref": "e2", "tag": "button", "name": "Open Menu"}]
    planner = CoveragePlanner(url)
    assert len({planner.state_key(shopping_observation(url, elements, count)) for count in (0, 1, 2, 6)}) == 1


def test_unknown_cart_never_invents_a_populated_checkout_lane():
    url = "https://shop.test/cart"
    page = observation(url, [{"ref": "e0", "tag": "button", "name": "Checkout"}])
    planner = CoveragePlanner(url)
    assert planner._cart_journey_bucket(page) is None
    assert planner.next_restoration_action(page) is None


def test_required_checkout_probe_is_scoped_to_each_cart_lane_happy_path():
    url = "https://shop.test/checkout-step-one"
    elements = [{"ref": "e0", "tag": "input", "type": "text", "name": "First Name", "form": "checkout",
                 "required": True, "has_value": True},
                {"ref": "e1", "tag": "button", "type": "submit", "name": "Continue", "form": "checkout"}]
    planner = CoveragePlanner(url)
    empty = shopping_observation(url, elements, 0)
    planner.record_action(empty, AgentDecision("click", "empty form submit", target="e1"),
                          result("click", url, url), empty)
    empty_probe = planner._form_probe_action(planner.states[planner.current_key])
    assert empty_probe.action == "fill" and empty_probe.value == ""
    populated = shopping_observation(url, elements, 1)
    planner.observe(populated)
    assert planner._form_probe_action(planner.states[planner.current_key]) is None
    planner.record_action(populated, AgentDecision("click", "populated form submit", target="e1"),
                          result("click", url, url), populated)
    clear = planner._form_probe_action(planner.states[planner.current_key])
    blank = shopping_observation(url, [{**elements[0], "has_value": False}, elements[1]], 1)
    planner.record_action(populated, clear, result("fill", url, url), blank)
    probe = planner._form_probe_action(planner.states[planner.current_key])
    assert probe.scenario_hint["validation_probe"]["form"] == "checkout::cart_journey:populated"
    planner.record_action(blank, probe, result("click", url, url), blank)
    assert planner._form_probe_action(planner.states[planner.current_key]) is None
    # The distinct empty lane probe was not silently classified by this one.
    planner.observe(empty)
    assert planner._form_probe_action(planner.states[planner.current_key]) is not None
