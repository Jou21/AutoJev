"""Declared SKU identity must survive cart intents and business comparisons."""

from copy import deepcopy

import pytest
from playwright.async_api import async_playwright

from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.domain.models import ActionResult, AgentDecision, ExplorationOptions, Observation, utc_now
from autojev.domain.product_identity import (
    comparable_composition_keys, declared_product_id, product_ledger_key,
    same_observed_product,
)
from autojev.services.business_checks import check_business_outcomes


def product(sku="sku-a", **changes):
    return {"id": sku, "identity_source": "declared_product_id", "name": "Desk Lamp",
            "price": "$10.00", "quantity": "1", **changes}


def checks(before_state, after_state, target="e0"):
    before = Observation("https://shop.test/catalog", "Shop", "", [], page_state=before_state)
    after = Observation("https://shop.test/cart", "Shop", "", [], page_state=after_state)
    decision = AgentDecision("click", "Inspect observed product", target=target)
    result = ActionResult("click", True, utc_now(), utc_now(), before.url, after.url, target=target)
    return check_business_outcomes(before, decision, result, after)


def test_same_name_different_declared_skus_are_not_the_same_product():
    assert not same_observed_product(product("sku-a"), product("sku-b"))
    assert product_ledger_key(product("sku-a")) != product_ledger_key(product("sku-b"))
    wanted, actual = comparable_composition_keys([product("sku-a")], [product("sku-b")])
    assert wanted != actual


@pytest.mark.parametrize("source", ["observed_name_id", "dom_structure"])
def test_name_and_dom_fallback_ids_do_not_claim_declared_provenance(source):
    fallback = product("name:Desk Lamp" if source == "observed_name_id" else "div:nth-of-type(2)", identity_source=source)
    assert declared_product_id(fallback) is None
    assert same_observed_product(product("sku-a"), fallback)


@pytest.mark.parametrize("identity", ["name:Desk Lamp", "div:nth-child(2)", "section > div", "header:id=banner", "main", "[REDACTED]", "sku-[FIELD VALUE]"])
def test_legacy_structural_or_redacted_ids_are_not_comparable(identity):
    assert declared_product_id({"id": identity, "name": "Desk Lamp"}) is None


def test_legacy_explicit_ids_remain_comparable():
    assert declared_product_id({"id": "sku-a", "name": "Desk Lamp"}) == "sku-a"
    assert not same_observed_product({"id": "sku-a", "name": "Desk Lamp"},
                                     {"id": "sku-b", "name": "Desk Lamp"})


def test_unique_name_can_bridge_catalog_id_to_name_only_review():
    review = product("name:Desk Lamp", identity_source="observed_name_id")
    wanted, actual = comparable_composition_keys([product()], [review])
    assert wanted == actual == ["desk lamp"]
    outcome = checks({"products": [product(link_refs=["e0"])]}, {"product_detail": review})
    assert next(item for item in outcome if item.kind == "product_detail_identity").passed


def test_ambiguous_name_only_review_does_not_approve_two_declared_skus():
    expected = [product("sku-a"), product("sku-b")]
    actual = [product("name:Desk Lamp", identity_source="observed_name_id")]
    assert comparable_composition_keys(expected, actual) is None
    outcome = checks(
        {"cart": {"link_refs": ["e0"]}},
        {"cart": {"items_known": True, "items": actual},
         "cart_expectation": {"items_known": True, "items": expected, "revision": 1}},
    )
    assert not any(item.kind == "cart_persistence" for item in outcome)
    assert not any(item.kind == "cart_item_prices" for item in outcome)


def test_wrong_homonymous_detail_and_cart_membership_fail_with_declared_ids():
    detail = checks({"products": [product("sku-a", link_refs=["e0"])]},
                    {"product_detail": product("sku-b")})
    assert not next(item for item in detail if item.kind == "product_detail_identity").passed
    membership = checks(
        {"products": [product("sku-a", action_refs=["e0"], cart_action="add")], "cart": {"count": 0}},
        {"cart": {"items_known": True, "items": [product("sku-b")], "count": 1}},
    )
    assert next(item for item in membership if item.kind == "cart_count").passed
    assert not next(item for item in membership if item.kind == "cart_membership").passed


def test_name_only_detail_cannot_approve_which_homonymous_catalog_sku_opened():
    outcome = checks(
        {"products": [product("sku-a", link_refs=["e0"]), product("sku-b", link_refs=["e1"])]},
        {"product_detail": product("name:Desk Lamp", identity_source="observed_name_id")},
    )
    assert not any(item.kind.startswith("product_detail_") for item in outcome)


def test_pending_membership_keeps_declared_homonymous_skus_separate():
    outcome = checks(
        {"cart": {"link_refs": ["e0"]}},
        {"cart": {"items_known": True, "items": [product("sku-b")]},
         "cart_expectation": {"items_known": False, "pending_membership": [product("sku-a", present=True)]}},
    )
    assert not next(item for item in outcome if item.kind == "cart_membership").passed


@pytest.mark.parametrize("changed", [True, False])
def test_same_declared_entity_and_physical_control_alias_retains_native_check(changed):
    prior = product(action_refs=["e0"], cart_action="add")
    current = product(action_refs=["e1"], cart_action="remove" if changed else "add")
    outcome = checks(
        {"product_detail": prior, "products": [deepcopy(prior)], "cart": {"count": 0}},
        {"product_detail": current, "products": [deepcopy(current)], "cart": {"count": int(changed)}},
    )
    assert next(item for item in outcome if item.kind == "cart_control_state").passed is changed


@pytest.mark.parametrize("ambiguity", ["another_ref", "another_source", "name_only", "disagreeing_state"])
def test_separate_or_unproven_product_controls_are_not_aliases(ambiguity):
    first = product(action_refs=["e1"], cart_action="remove")
    other = deepcopy(first)
    if ambiguity == "another_ref":
        other["action_refs"] = ["e2"]
    elif ambiguity == "another_source":
        other["identity_source"] = "declared_item_id"
    elif ambiguity == "name_only":
        first.update(id="name:Desk Lamp", identity_source="observed_name_id")
        other.update(id="name:Desk Lamp", identity_source="observed_name_id")
    else:
        other["cart_action"] = "add"
    outcome = checks(
        {"product_detail": product(action_refs=["e0"], cart_action="add"), "cart": {"count": 0}},
        {"product_detail": first, "products": [other], "cart": {"count": 1}},
    )
    assert not any(item.kind == "cart_control_state" for item in outcome)


@pytest.mark.parametrize("items_known", [True, False])
def test_cart_intent_ledger_does_not_merge_homonymous_declared_skus(tmp_path, items_known):
    executor = PlaywrightExecutor(tmp_path, ExplorationOptions())
    executor._cart_expectation["items_known"] = items_known
    for sku in ("sku-a", "sku-b"):
        executor._last_page_state = {"products": [product(sku, action_refs=["e0"], cart_action="add")]}
        intent = executor._remember_cart_action(AgentDecision("click", "Add this SKU", target="e0"))
        assert intent["product"]["identity_source"] == "declared_product_id"
    key = "items" if items_known else "pending_membership"
    assert [item["id"] for item in executor._cart_expectation[key]] == ["sku-a", "sku-b"]
    executor._last_page_state = {"products": [product("sku-a", action_refs=["e0"], cart_action="remove")]}
    executor._remember_cart_action(AgentDecision("click", "Remove this SKU", target="e0"))
    if items_known:
        assert [item["id"] for item in executor._cart_expectation["items"]] == ["sku-b"]
    else:
        assert [(item["id"], item["present"]) for item in executor._cart_expectation[key]] == [("sku-b", True), ("sku-a", False)]


def test_ledger_removes_same_declared_sku_even_if_its_display_name_changed(tmp_path):
    executor = PlaywrightExecutor(tmp_path, ExplorationOptions())
    executor._cart_expectation.update(items_known=True, items=[product()])
    executor._last_page_state = {"products": [product(name="Wrong label", action_refs=["e0"], cart_action="remove")]}
    executor._remember_cart_action(AgentDecision("click", "Remove this SKU", target="e0"))
    assert executor._cart_expectation["items"] == []
    assert not same_observed_product(product(), product(name="Wrong label"))


def test_name_only_cart_remove_bridges_unique_declared_catalog_ledger(tmp_path):
    executor = PlaywrightExecutor(tmp_path, ExplorationOptions())
    executor._cart_expectation.update(items_known=True, items=[product()])
    executor._last_page_state = {"cart": {"items": [product("name:Desk Lamp", identity_source="observed_name_id", action_refs=["e0"], cart_action="remove")]}}
    intent = executor._remember_cart_action(AgentDecision("click", "Remove item", target="e0"))
    assert executor._cart_expectation["items"] == []
    assert intent["product"]["id"] == "sku-a"
    assert intent["product"]["identity_source"] == "declared_product_id"


def test_name_only_remove_cannot_delete_both_homonymous_declared_skus(tmp_path):
    executor = PlaywrightExecutor(tmp_path, ExplorationOptions())
    executor._cart_expectation.update(items_known=True, items=[product("sku-a"), product("sku-b")])
    baseline = deepcopy(executor._cart_expectation)
    executor._last_page_state = {"cart": {"items": [product("name:Desk Lamp", identity_source="observed_name_id", action_refs=["e0"], cart_action="remove")]}}
    intent = executor._remember_cart_action(AgentDecision("click", "Remove item", target="e0"))
    assert intent["outcome"] == "identity_ambiguous"
    assert executor._cart_expectation == baseline


@pytest.mark.parametrize("declared", [False, True])
def test_noop_baseline_requires_unambiguous_product_keys(tmp_path, declared):
    executor = PlaywrightExecutor(tmp_path, ExplorationOptions())
    item = product(cart_action="add") if declared else product("name:Desk Lamp", identity_source="observed_name_id", cart_action="add")
    before = {"products": [item], "product_detail": deepcopy(item), "cart": {"count": 0}}
    previous = {"items": [], "items_known": True, "revision": 0, "pending_membership": []}
    executor._cart_expectation = {**deepcopy(previous), "items": [item], "revision": 1}
    after = deepcopy(before)
    executor._last_page_state = after
    intent = {"kind": "product_cart_action", "operation": "add", "product": item}
    assert executor._retain_cart_baseline_for_noop(intent, previous, before, after) is declared


def test_checkout_product_urls_are_sanitized_and_declared_secret_id_is_not_comparable(tmp_path):
    executor = PlaywrightExecutor(tmp_path, ExplorationOptions(), secret_values=["fixture-secret"])
    sanitized = executor._sanitize_page_state({"checkout": {"items": [
        product("fixture-secret", image_src="https://shop.test/image?token=private&sku=a"),
    ]}})
    item = sanitized["checkout"]["items"][0]
    assert "private" not in item["image_src"]
    assert "fixture-secret" not in item["id"]
    assert declared_product_id(item) is None


@pytest.mark.parametrize("marker_on_parent", [False, True])
async def test_real_declared_homonymous_sku_misdirection_is_not_business_pass(tmp_path, marker_on_parent):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            html = '''<main>
              <a class="shopping_cart_link" href="#cart">Cart</a>
              <div class="inventory_item" data-product-id="sku-a"><a href="#detail"><span class="inventory_item_name">Desk Lamp</span></a><span class="inventory_item_price">$10.00</span><button>Add to cart</button></div>
              <article data-item-id="sku-b"><span class="product-name">Desk Lamp</span><span class="product-price">$10.00</span><button>Add to cart</button></article>
            </main>'''
            await page.route("**/*", lambda route: route.fulfill(content_type="text/html", body=html))
            await page.goto("https://shop.test/catalog")
            await page.locator('[data-product-id="sku-a"] a').evaluate('''(node, markerOnParent) => node.onclick = event => {
                event.preventDefault();
                const detail = '<div class="inventory_details" ' + (markerOnParent ? '' : 'data-product-id="sku-b"') + '><span class="inventory_details_name">Desk Lamp</span><span class="inventory_details_price">$10.00</span></div>';
                document.querySelector('main').innerHTML = markerOnParent ? '<article data-product-id="sku-b">' + detail + '</article>' : detail;
            }''', marker_on_parent)
            executor = PlaywrightExecutor(tmp_path, ExplorationOptions(capture_screenshots=False))
            executor._browser, executor._page, executor._context = browser, page, page.context
            executor._allowed_origin = executor._origin(page.url)
            before = await executor.observe()
            assert [item["identity_source"] for item in before.page_state["products"]] == ["declared_product_id", "declared_item_id"]
            decision = AgentDecision("click", "Open first SKU", target=before.page_state["products"][0]["link_refs"][0])
            result, after = await executor.execute(decision, {})
            assert result.success
            assert after.page_state["products"] == []
            outcomes = check_business_outcomes(before, decision, result, after)
            assert not next(item for item in outcomes if item.kind == "product_detail_identity").passed
        finally:
            await browser.close()


@pytest.mark.parametrize("marker_on_parent", [False, True])
async def test_real_detail_cart_control_is_reported_once_and_keeps_native_check(tmp_path, marker_on_parent):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            detail = '<section class="inventory_details" ' + ('' if marker_on_parent else 'data-product-id="sku-a"') + '><span class="inventory_details_name">Desk Lamp</span><span class="inventory_details_price">$10.00</span><button id="toggle-detail">Add to cart</button></section>'
            if marker_on_parent:
                detail = '<article data-product-id="sku-a">' + detail + '</article>'
            html = '<header><a class="shopping_cart_link" href="#cart">Cart<span class="shopping_cart_badge">0</span></a></header><main>' + detail + '''
              <div class="cart_list"><article class="cart_item" data-product-id="sku-b"><span class="inventory_item_name">Other lamp</span><span class="inventory_item_price">$20.00</span><button>Remove</button></article></div>
            </main>'''
            await page.route("**/*", lambda route: route.fulfill(content_type="text/html", body=html))
            await page.goto("https://shop.test/detail")
            await page.locator('#toggle-detail').evaluate('''node => node.onclick = () => {
                node.textContent = 'Remove'; document.querySelector('.shopping_cart_badge').textContent = '1';
            }''')
            executor = PlaywrightExecutor(tmp_path, ExplorationOptions(capture_screenshots=False))
            executor._browser, executor._page, executor._context = browser, page, page.context
            executor._allowed_origin = executor._origin(page.url)
            before = await executor.observe()
            assert before.page_state["products"] == []
            assert before.page_state["product_detail"]["id"] == "sku-a"
            assert [item["id"] for item in before.page_state["cart"]["items"]] == ["sku-b"]
            decision = AgentDecision("click", "Add this detail SKU", target=before.page_state["product_detail"]["action_refs"][0])
            result, after = await executor.execute(decision, {})
            assert result.success
            assert after.page_state["products"] == []
            outcome = check_business_outcomes(before, decision, result, after)
            assert next(item for item in outcome if item.kind == "cart_control_state").passed
        finally:
            await browser.close()
