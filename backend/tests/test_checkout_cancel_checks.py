"""Cancellation verifies the basket, not just a navigational state delta."""

from copy import deepcopy

import pytest

from autojev.domain.models import ActionResult, AgentDecision, Observation, utc_now
from autojev.services.business_checks import check_business_outcomes


@pytest.mark.parametrize("populated", [False, True])
@pytest.mark.parametrize("mutation", [None, "lost_item", "price_changed", "quantity_changed"])
def test_review_cancel_preserves_actual_observed_cart(populated, mutation):
    items = [{"id": "sku", "name": "Desk Lamp", "price": "29.99", "quantity": 2}] if populated else []
    before = Observation("https://app.test/review", "Review", "", [
        {"ref": "cancel", "tag": "a", "name": "Cancel", "href": "https://app.test/cart"},
    ], page_state={
        "cart": {"items_known": True, "items": items, "count": 2 if populated else 0},
        "cart_expectation": {"items_known": True, "items": items, "revision": 2},
        "checkout": {"stage": "review", "items_known": True, "items": items},
    })
    after = Observation("https://app.test/cart", "Cart", "", [], page_state={
        "cart": deepcopy(before.page_state["cart"]),
    })
    if populated and mutation == "lost_item":
        after.page_state["cart"]["items"] = []
    elif populated and mutation == "price_changed":
        after.page_state["cart"]["items"][0]["price"] = "39.99"
    elif populated and mutation == "quantity_changed":
        after.page_state["cart"]["items"][0]["quantity"] = 1
    decision = AgentDecision("click", "cancel", target="cancel")
    result = ActionResult("click", True, utc_now(), utc_now(), before.url, after.url)
    checks = check_business_outcomes(before, decision, result, after)
    assert "cart_persistence" in {check.kind for check in checks}
    assert all(check.passed for check in checks) is (not populated or mutation is None)


def test_cancel_without_observed_basket_does_not_manufacture_business_pass():
    before = Observation("https://app.test/review", "Review", "", [
        {"ref": "cancel", "tag": "a", "name": "Cancel"},
    ], page_state={"checkout": {"stage": "review"}, "cart": {"items_known": False}})
    after = Observation("https://app.test/cart", "Cart", "", [],
                        page_state={"cart": {"items_known": True, "items": []}})
    decision = AgentDecision("click", "cancel", target="cancel")
    result = ActionResult("click", True, utc_now(), utc_now(), before.url, after.url)
    checks = check_business_outcomes(before, decision, result, after)
    assert {check.kind for check in checks} == {"checkout_cancel_progress"}


@pytest.mark.parametrize("lost_selection", [False, True])
def test_cancel_returning_to_catalog_checks_selection_without_claiming_open_cart(lost_selection):
    before = Observation("https://app.test/review", "Review", "", [
        {"ref": "cancel", "tag": "button", "name": "Cancel"},
    ], page_state={"cart": {"count": 1}, "checkout": {"stage": "review", "items_known": True, "items": [
        {"name": "Desk Lamp", "price": "29.99", "quantity": 1},
    ]}})
    after = Observation("https://app.test/catalog", "Catalog", "", [], page_state={
        "cart": {"count": 0 if lost_selection else 1, "items_known": False},
        "products": [{"name": "Desk Lamp", "price": "29.99",
                      "cart_action": "add" if lost_selection else "remove"}],
    })
    decision = AgentDecision("click", "cancel", target="cancel")
    result = ActionResult("click", True, utc_now(), utc_now(), before.url, after.url)
    checks = check_business_outcomes(before, decision, result, after)
    assert {check.kind for check in checks} == {
        "checkout_cancel_progress", "checkout_cancel_cart_count", "checkout_cancel_selected_products",
    }
    assert all(check.passed for check in checks) is (not lost_selection)


def test_catalog_selection_does_not_invent_per_product_quantity_or_badge_rule():
    before = Observation("https://app.test/review", "Review", "", [
        {"ref": "cancel", "tag": "button", "name": "Cancel"},
    ], page_state={"cart": {"count": 1}, "checkout": {"stage": "review", "items_known": True,
                    "items": [{"name": "Desk Lamp", "price": "29.99", "quantity": 2}]}})
    after = Observation("https://app.test/catalog", "Catalog", "", [], page_state={
        "cart": {"count": 1, "items_known": False},
        "products": [{"name": "Desk Lamp", "cart_action": "remove"}],
    })
    decision = AgentDecision("click", "cancel", target="cancel")
    result = ActionResult("click", True, utc_now(), utc_now(), before.url, after.url)
    assert all(check.passed for check in check_business_outcomes(before, decision, result, after))


@pytest.mark.parametrize("stage", ["review", "complete"])
def test_noop_or_order_placement_is_not_a_successful_cancel(stage):
    before = Observation("https://app.test/review", "Review", "", [
        {"ref": "cancel", "tag": "button", "name": "Cancel"},
    ], page_state={"checkout": {"stage": "review"}})
    after = deepcopy(before)
    after.page_state["checkout"]["stage"] = stage
    decision = AgentDecision("click", "cancel", target="cancel")
    result = ActionResult("click", True, utc_now(), utc_now(), before.url, after.url)
    checks = check_business_outcomes(before, decision, result, after)
    assert any(check.kind == "checkout_cancel_progress" and not check.passed for check in checks)
