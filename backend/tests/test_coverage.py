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
from autojev.services.coverage import CoveragePlanner, semantic_element_fingerprint


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

    assert [item["ref"] for item in context["current_untried"]] == [
        "e0",
        "e1",
        "e1",
    ]
    assert context["current_untried"][0]["suggested_action"] == "fill"
    assert context["current_untried"][0]["suggested_value"] == "AutoJev QA"
    assert context["current_untried"][1]["suggested_action"] == "select"
    assert context["current_untried"][1]["suggested_value"] == "open"
    assert context["current_untried"][1]["option_label"] == "Em aberto"
    assert context["current_untried"][2]["suggested_value"] == "all"

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
        {**option, "selected": option.get("value") == "status-1"}
        for option in options
    ]
    changed = observation(
        page.url,
        [{**element, "selected_index": 1, "options": changed_options}],
    )
    planner = CoveragePlanner(page.url)

    assert planner.state_key(page) == planner.state_key(changed)
    assert planner.effect_key(page) != planner.effect_key(changed)

    moves = planner.context(page)["current_untried"]
    assert len(moves) == 8
    assert {move["suggested_value"] for move in moves} == {
        f"status-{index}" for index in range(8)
    }
    assert moves[0]["suggested_value"] == "status-1"
    assert moves[-1]["suggested_value"] == "status-0"
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
    assert len(remaining) == 7


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
    retry, _ = planner.reconcile(child, AgentDecision("finish", "cover"))
    assert retry.action == "navigate"
    planner.record_action(
        child,
        retry,
        result("navigate", child.url, child.url, success=False),
        child,
    )
    finish, _ = planner.reconcile(child, AgentDecision("finish", "cover"))

    assert finish.action == "finish"
    assert planner.can_finish() is True
    assert planner.coverage_complete() is False


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
