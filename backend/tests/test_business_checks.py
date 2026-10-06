"""Local regressions for intentional sample-app-web mutations (no LLM calls).

Reference: saucelabs/sample-app-web InventoryListItem, Inventory,
CheckOutStepOne. Fixtures exercise the observable contracts, not a user-name
special case: the same checks work for ordinary semantic product/forms DOM.
"""

from copy import deepcopy

import pytest
from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.domain.models import (
    ActionResult,
    AgentDecision,
    ExplorationOptions,
    Observation,
    ScenarioStatus,
    utc_now,
)
from autojev.services.business_checks import check_business_outcomes
from autojev.services.coverage import CoveragePlanner
from autojev.services.exploration import ExplorationManager


def observed(state, elements=None, url="https://shop.test/inventory"):
    return Observation(
        url=url,
        title="Shop",
        text="Products",
        interactive_elements=elements or [],
        page_state=state,
    )


def executed(before, after, decision, **kwargs):
    return ActionResult(
        decision.action,
        True,
        utc_now(),
        utc_now(),
        before.url,
        after.url,
        target=decision.target,
        **kwargs,
    )


def verdict(before, after, decision, result=None):
    result = result or executed(before, after, decision)
    result.assertions.extend(check_business_outcomes(before, decision, result, after))
    return result, ExplorationManager._scenario_from_evidence(
        after, decision, result, 1
    )


@pytest.mark.parametrize("name", ["Cancel", "Cancelar", "Back", "Voltar", "Close", "Fechar"])
def test_cancel_never_requires_form_validation_even_with_legacy_submit_metadata(name):
    before = observed({
        "fields": [{"identity": "first", "form": "checkout", "required": True, "has_value": False}],
        "forms": [{"id": "checkout", "submit_refs": ["exit"]}],
    }, [{"ref": "exit", "name": name, "type": "submit"}])
    after = observed({"forms": [], "fields": []}, url="https://shop.test/cart")
    decision = AgentDecision("click", "Leave the form", target="exit")
    checks = check_business_outcomes(before, decision, executed(before, after, decision), after)
    assert not any(check.kind.startswith("form_") for check in checks)


def test_legacy_header_identity_does_not_treat_global_cart_link_as_product_detail():
    before = observed({"product_detail": {"id": "header:id=header_container", "name": "", "link_refs": ["cart"]}})
    after = observed({"cart": {"count": 1}}, url="https://shop.test/cart")
    decision = AgentDecision("click", "Open Cart", target="cart")
    checks = check_business_outcomes(before, decision, executed(before, after, decision), after)
    assert not any(check.kind.startswith("product_detail_") for check in checks)


@pytest.mark.parametrize("action", ["add", "remove"])
@pytest.mark.parametrize("mutated", [False, True])
def test_cart_noop_is_fail_even_when_playwright_click_succeeds(action, mutated):
    product = {
        "id": "4",
        "name": "Backpack",
        "action_refs": ["e1"],
        "cart_action": action,
    }
    count = 0 if action == "add" else 1
    before = observed({"products": [product], "cart": {"count": count}})
    after_product = {
        **product,
        "cart_action": action if mutated else "remove" if action == "add" else "add",
    }
    after = observed(
        {
            "products": [after_product],
            "cart": {
                "count": count if mutated else count + (1 if action == "add" else -1)
            },
        }
    )
    result, ct = verdict(
        before, after, AgentDecision("click", "cart action", target="e1")
    )
    assert result.success is True
    assert {a.kind for a in result.assertions} == {"cart_count", "cart_control_state"}
    assert ct.status == (ScenarioStatus.FAIL if mutated else ScenarioStatus.PASS)


@pytest.mark.parametrize("mutated", [False, True])
def test_wrong_product_link_fails_despite_successful_navigation(mutated):
    before = observed(
        {
            "products": [
                {"id": "4", "name": "Backpack", "price": "$29.99", "link_refs": ["e3"]}
            ]
        }
    )
    after = observed(
        {
            "product_detail": {
                "id": "5" if mutated else "4",
                "name": "Jacket" if mutated else "Backpack",
                "price": "$29.99",
            }
        },
        url="https://shop.test/detail?id=5"
        if mutated
        else "https://shop.test/detail?id=4",
    )
    result, ct = verdict(
        before, after, AgentDecision("click", "open product", target="e3")
    )
    assert next(
        a for a in result.assertions if a.kind == "product_detail_identity"
    ).passed is (not mutated)
    assert ct.status == (ScenarioStatus.FAIL if mutated else ScenarioStatus.PASS)


def test_price_changes_between_list_and_detail_are_not_a_pass():
    before = observed(
        {
            "products": [
                {"id": "4", "name": "Backpack", "price": "$29.99", "link_refs": ["e3"]}
            ]
        }
    )
    after = observed(
        {"product_detail": {"id": "4", "name": "Backpack", "price": "$99.99"}}
    )
    result, ct = verdict(
        before, after, AgentDecision("click", "open product", target="e3")
    )
    assert (
        next(a for a in result.assertions if a.kind == "product_detail_price").passed
        is False
    )
    assert ct.status == ScenarioStatus.FAIL


@pytest.mark.parametrize("mutation", [None, "wrong_item", "lost_item", "wrong_quantity", "wrong_price"])
def test_opening_cart_verifies_requested_membership_price_and_quantity(mutation):
    product = {"id": "4", "name": "Backpack", "price": "$29.99", "quantity": "1"}
    expected = {"items_known": True, "items": [product], "revision": 1, "source": "product_cart_intent"}
    before = observed({"cart": {"count": 1, "link_refs": ["cart"]}, "cart_expectation": expected})
    actual = deepcopy(product)
    if mutation == "wrong_item":
        actual.update(id="0", name="Bike Light")
    elif mutation == "wrong_quantity":
        actual["quantity"] = "2"
    elif mutation == "wrong_price":
        actual["price"] = "$99.99"
    after = observed({"cart": {"items_known": True, "items": [] if mutation == "lost_item" else [actual]}, "cart_expectation": expected}, url="https://shop.test/cart")
    result, ct = verdict(before, after, AgentDecision("click", "open cart", target="cart"))
    assert "cart_persistence" in {item.kind for item in result.assertions}
    assert ct.status == (ScenarioStatus.PASS if mutation is None else ScenarioStatus.FAIL)


@pytest.mark.parametrize("mutation", [None, "wrong_item", "wrong_subtotal", "wrong_total", "wrong_unit_price", "wrong_quantity"])
def test_checkout_checks_contents_and_declared_arithmetic_without_inventing_tax_rate(mutation):
    product = {"id": "4", "name": "Backpack", "price": "$29.99", "quantity": "2"}
    before = observed({"cart_expectation": {"items_known": True, "items": [product]}, "cart": {"count": 2}})
    item = deepcopy(product)
    review = {"stage": "review", "items_known": True, "items": [item], "subtotal": "Item total: $59.98", "tax": "Tax: $7.77", "total": "Total: $67.75", "arithmetic_scope": "declared_components_only", "finish_refs": ["finish"]}
    if mutation == "wrong_item":
        item.update(id="0", name="Bike Light")
    elif mutation == "wrong_unit_price":
        item["price"] = "$99.99"
    elif mutation == "wrong_quantity":
        item["quantity"] = "1"
    elif mutation == "wrong_subtotal":
        review["subtotal"] = "$79.98"
    elif mutation == "wrong_total":
        review["total"] = "$99.99"
    after = observed({"checkout": review, "cart_expectation": before.page_state["cart_expectation"]}, url="https://shop.test/review")
    result, ct = verdict(before, after, AgentDecision("click", "continue checkout", target="continue"))
    assert {item.kind for item in result.assertions} >= {"checkout_items_match_cart", "checkout_subtotal", "checkout_total"}
    assert not any(item.kind == "tax_rate" for item in result.assertions)
    assert ct.status == (ScenarioStatus.PASS if mutation is None else ScenarioStatus.FAIL)


@pytest.mark.parametrize("destination", ["confirmed", "blank", "login", "generic_success"])
def test_finish_requires_explicit_order_confirmation_not_changed_url(destination):
    before = observed({"checkout": {"stage": "review", "finish_refs": ["finish"]}})
    checkout = {"stage": "complete", "confirmed": True, "confirmation_text": "Thank you for your order!"} if destination == "confirmed" else {"stage": "complete", "confirmed": False} if destination == "generic_success" else None
    after = observed({"checkout": checkout}, url=f"https://shop.test/{destination}")
    result, ct = verdict(before, after, AgentDecision("click", "complete order", target="finish"))
    assert result.assertions[0].kind == "order_confirmation"
    assert ct.status == (ScenarioStatus.PASS if destination == "confirmed" else ScenarioStatus.FAIL)


def test_unknown_cart_contents_and_tax_rate_are_not_fabricated_passes():
    before = observed({"cart": {"count": 8, "link_refs": ["cart"]}})
    after = observed({"cart": {"items_known": True, "items": [{"name": "Backpack"}]}})
    assert check_business_outcomes(before, AgentDecision("click", "open", target="cart"), executed(before, after, AgentDecision("click", "open", target="cart")), after) == []
    after.page_state["checkout"] = {"stage": "review", "subtotal": "$29.99", "tax": None, "total": "$35.00", "arithmetic_scope": "declared_components_only"}
    checks = check_business_outcomes(before, AgentDecision("click", "continue", target="next"), executed(before, after, AgentDecision("click", "continue", target="next")), after)
    assert not checks


@pytest.mark.parametrize("count", [0, 1])
def test_confirmation_does_not_assume_every_shop_clears_the_cart(tmp_path, count):
    executor = PlaywrightExecutor(tmp_path, ExplorationOptions())
    product = {"name": "Backpack", "price": "$29.99"}
    executor._cart_expectation.update(items=[product], items_known=True, source="product_cart_intent")
    state = {"checkout": {"stage": "complete", "confirmed": True}, "cart": {"count": count}}
    executor._observe_cart_expectation(state)
    assert state["cart_expectation"]["items"] == ([] if count == 0 else [product])


@pytest.mark.parametrize("operation", ["add", "remove"])
def test_proven_noop_keeps_previous_cart_baseline_without_mutating_observed_facts(tmp_path, operation):
    executor = PlaywrightExecutor(tmp_path, ExplorationOptions())
    product = {"name": "Backpack", "price": "$29.99"}
    before = {"cart": {"count": 0 if operation == "add" else 1},
              "products": [{**product, "cart_action": operation}]}
    prior = {"items": [] if operation == "add" else [product], "items_known": True,
             "revision": 0, "source": "initial_product_controls", "pending_membership": []}
    intended = {**deepcopy(prior), "items": [product] if operation == "add" else [],
                "revision": 1, "source": "product_cart_intent"}
    executor._cart_expectation = deepcopy(intended)
    after = {**deepcopy(before), "cart_expectation": deepcopy(intended)}
    executor._last_page_state = deepcopy(after)
    intent = {"kind": "product_cart_action", "operation": operation, "product": product}
    before_copy = deepcopy(before)
    assert executor._retain_cart_baseline_for_noop(intent, prior, before, after) is True
    assert executor._cart_expectation == after["cart_expectation"] == executor._last_page_state["cart_expectation"] == prior
    assert intent["outcome"] == "observed_no_op"
    assert intent["ledger_update"] == "previous_baseline_preserved"
    assert before == before_copy
    assert after["cart"] == before["cart"] and after["products"] == before["products"]
    assert CoveragePlanner._cart_journey_bucket(observed(after)) == ("empty" if operation == "add" else "populated")


@pytest.mark.parametrize("uncertainty", ["missing_count", "boolean_count", "changed_count", "changed_control", "missing_product", "other_product_changed", "changed_product_set", "replaced_cart_item"])
def test_partial_or_unknown_cart_effect_does_not_overwrite_intent(tmp_path, uncertainty):
    executor = PlaywrightExecutor(tmp_path, ExplorationOptions())
    product = {"name": "Backpack", "price": "$29.99"}
    other = {"name": "Bike Light", "price": "$9.99"}
    before = {"cart": {"count": 1}, "products": [
        {**product, "cart_action": "add"}, {**other, "cart_action": "remove"},
    ]}
    prior = {"items": [other], "items_known": True, "revision": 1, "pending_membership": []}
    intended = {**deepcopy(prior), "items": [other, product], "revision": 2, "source": "product_cart_intent"}
    executor._cart_expectation = deepcopy(intended)
    after = {**deepcopy(before), "cart_expectation": deepcopy(intended)}
    if uncertainty == "missing_count":
        after["cart"].pop("count")
    elif uncertainty == "boolean_count":
        after["cart"]["count"] = True
    elif uncertainty == "changed_count":
        after["cart"]["count"] = 2
    elif uncertainty == "changed_control":
        after["products"][0]["cart_action"] = "remove"
    elif uncertainty == "missing_product":
        after["products"] = [after["products"][1]]
    elif uncertainty == "other_product_changed":
        after["products"][1]["cart_action"] = "add"
    elif uncertainty == "changed_product_set":
        after["products"] = [after["products"][0]]
    else:
        before["cart"].update(items_known=True, items=[other])
        after["cart"].update(items_known=True, items=[product])
    executor._last_page_state = deepcopy(after)
    intent = {"kind": "product_cart_action", "operation": "add", "product": product}
    before_copy, after_copy = deepcopy(before), deepcopy(after)
    assert executor._retain_cart_baseline_for_noop(intent, prior, before, after) is False
    assert executor._cart_expectation == intended
    assert before == before_copy and after == after_copy
    assert "ledger_update" not in intent


@pytest.mark.parametrize("operation", ["add", "remove"])
async def test_real_noop_add_remove_fails_primary_action_not_future_cart_persistence(tmp_path, operation):
    from playwright.async_api import async_playwright
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.route("**/*", lambda route: route.fulfill(content_type="text/html", body="<html></html>"))
            await page.goto("https://shop.test/inventory")
            await page.set_content('<a class="shopping_cart_link" href="#cart">Cart</a><main></main>')
            await page.evaluate("""operation => {
              const present=operation==='remove';
              const cart=document.querySelector('.shopping_cart_link'), main=document.querySelector('main');
              if(present)cart.innerHTML+=' <span class="shopping_cart_badge">1</span>';
              main.innerHTML=`<div class="inventory_item"><span class="inventory_item_name">Backpack</span><span class="inventory_item_price">$29.99</span><button>${present?'Remove':'Add to cart'}</button></div>`;
              // A dispatched click succeeds but the application's membership
              // implementation is deliberately broken and does nothing.
              main.querySelector('button').onclick=()=>{};
              cart.onclick=event=>{
                event.preventDefault();
                main.innerHTML=`<div class="cart_list">${present?'<div class="cart_item"><span class="inventory_item_name">Backpack</span><span class="cart_quantity">1</span><span class="inventory_item_price">$29.99</span><button>Remove</button></div>':''}</div>`;
              };
            }""", operation)
            executor = PlaywrightExecutor(tmp_path, ExplorationOptions(capture_screenshots=False))
            executor._page, executor._browser, executor._context = page, browser, page.context
            executor._allowed_origin = executor._origin(page.url)
            before = await executor.observe()
            baseline = deepcopy(before.page_state["cart_expectation"])
            target = next(item for item in before.interactive_elements if item.get("name") == ("Add to cart" if operation == "add" else "Remove"))
            decision = AgentDecision("click", "Exercise cart membership", target=target["ref"])
            result, after = await executor.execute(decision, {})
            assert result.success, result.message
            result, ct = verdict(before, after, decision, result)
            assert ct.status == ScenarioStatus.FAIL
            assert {check.kind for check in result.assertions if not check.passed} >= {"cart_count", "cart_control_state"}
            assert after.page_state["cart_expectation"] == baseline
            assert executor._cart_expectation == baseline
            assert result.evidence["business_cart_intent"]["outcome"] == "observed_no_op"
            assert CoveragePlanner._cart_journey_bucket(after) == ("empty" if operation == "add" else "populated")

            before = after
            target = next(item for item in before.interactive_elements if item.get("name", "").startswith("Cart"))
            decision = AgentDecision("click", "Inspect actual cart", target=target["ref"])
            result, after = await executor.execute(decision, {})
            result, ct = verdict(before, after, decision, result)
            assert ct.status == ScenarioStatus.PASS
            cart_checks = [check for check in result.assertions if check.kind in {"cart_contents_match", "cart_persistence"}]
            assert cart_checks and all(check.passed for check in cart_checks)
            assert after.page_state["cart_expectation"] == baseline
            assert len(after.page_state["cart"]["items"]) == (0 if operation == "add" else 1)
        finally:
            await browser.close()


@pytest.mark.parametrize("noop", [False, True])
async def test_explicit_demo_reset_starts_new_cart_lifecycle_without_masking_noop(tmp_path, noop):
    from playwright.async_api import async_playwright
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.route("**/*", lambda route: route.fulfill(content_type="text/html", body="<html></html>"))
            await page.goto("https://shop.test/inventory")
            await page.set_content('''<a class="shopping_cart_link">Cart<span class="shopping_cart_badge">1</span></a>
              <button id="reset">Reset App State</button><main><div class="inventory_item"><span class="inventory_item_name">Backpack</span><span class="inventory_item_price">$29.99</span><button>Remove</button></div></main>''')
            await page.evaluate("""noop => {
              let present=true;
              document.querySelector('#reset').onclick=()=>{
                if(noop)return;present=false;
                document.querySelector('.shopping_cart_badge').textContent='';
                document.querySelector('main button').textContent='Add to cart';
              };
              document.querySelector('.shopping_cart_link').onclick=()=>{
                document.querySelector('main').innerHTML=`<div class="cart_list">${present?'<div class="inventory_item"><span class="inventory_item_name">Backpack</span><span class="inventory_item_price">$29.99</span></div>':''}</div>`;
              };
            }""", noop)
            executor = PlaywrightExecutor(tmp_path, ExplorationOptions(capture_screenshots=False))
            executor._page, executor._browser, executor._context = page, browser, page.context
            executor._allowed_origin = executor._origin(page.url)
            observation = await executor.observe()
            assert observation.page_state["cart_expectation"]["items"]
            for label in ["Reset App State", "Cart"]:
                before = observation
                target = next(item for item in before.interactive_elements if item.get("name", "").startswith(label))
                decision = AgentDecision("click", label, target=target["ref"])
                result, observation = await executor.execute(decision, {})
                assert result.success, result.message
                checks = check_business_outcomes(before, decision, result, observation)
                assert checks
                assert all(check.passed for check in checks) is (not noop)
                if label == "Reset App State":
                    assert any(check.kind == "cart_reset" for check in checks)
                    assert observation.page_state["cart_expectation"]["source"] == "explicit_cart_reset"
                else:
                    assert observation.page_state["cart"]["items_known"] is True
                    assert not observation.page_state["products"]
        finally:
            await browser.close()


@pytest.mark.parametrize("mutation", [None, "wrong_product", "wrong_price", "lost_on_return"])
async def test_real_cart_dom_keeps_action_intent_when_opening_and_returning(tmp_path, mutation):
    from playwright.async_api import async_playwright
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.route("**/*", lambda route: route.fulfill(content_type="text/html", body="<html></html>"))
            await page.goto("https://shop.test/inventory")
            await page.set_content('<header><a class="shopping_cart_link" href="#cart">Cart</a></header><main></main>')
            await page.evaluate("""mutation => {
              const products=[{id:'4',name:'Backpack',price:'29.99'},{id:'0',name:'Bike Light',price:'9.99'}];
              let selected=false, cart=[], visits=0;
              const main=document.querySelector('main'), link=document.querySelector('.shopping_cart_link');
              const badge=()=>{link.innerHTML='Cart'+(cart.length?`<span class="shopping_cart_badge">${cart.length}</span>`:'')};
              const inventory=()=>{
                main.innerHTML=`<div class="inventory_item" data-product-id="4"><span class="inventory_item_name">Backpack</span><span class="inventory_item_price">$29.99</span><button>${selected?'Remove':'Add to cart'}</button></div>`;
                main.querySelector('button').onclick=()=>{selected=!selected;cart=selected?[{...products[mutation==='wrong_product'?1:0]}]:[];inventory();badge()};
              };
              link.onclick=event=>{
                event.preventDefault();visits++;
                if(mutation==='lost_on_return' && visits>1){cart=[];badge()}
                main.innerHTML=`<div class="cart_list">${cart.map(item=>`<div class="cart_item" data-product-id="${item.id}"><span class="inventory_item_name">${item.name}</span><span class="cart_quantity">1</span><span class="inventory_item_price">$${mutation==='wrong_price'?'99.99':item.price}</span></div>`).join('')}</div><button>Continue Shopping</button>`;
                main.querySelector('button').onclick=inventory;
              };
              inventory();badge();
            }""", mutation)
            executor = PlaywrightExecutor(tmp_path, ExplorationOptions(capture_screenshots=False))
            executor._page, executor._browser, executor._context = page, browser, page.context
            executor._allowed_origin = executor._origin(page.url)
            observation = await executor.observe()
            assert observation.page_state["cart_expectation"]["items_known"] is True
            for phase in ["add", "open", "back", "reopen"]:
                before = observation
                name = "Add to cart" if phase == "add" else "Continue Shopping" if phase == "back" else "Cart"
                target = next(item for item in before.interactive_elements if item.get("name", "").startswith(name))
                decision = AgentDecision("click", phase, target=target["ref"])
                result, observation = await executor.execute(decision, {})
                assert result.success, result.message
                checks = check_business_outcomes(before, decision, result, observation)
                if phase == "add":
                    assert all(check.passed for check in checks)
                elif phase in {"open", "reopen"}:
                    assert "cart_persistence" in {check.kind for check in checks}
                    failing = mutation in {"wrong_product", "wrong_price"} or mutation == "lost_on_return" and phase == "reopen"
                    assert all(check.passed for check in checks) is (not failing)
        finally:
            await browser.close()


@pytest.mark.parametrize("mutation", [None, "wrong_subtotal", "wrong_total", "wrong_price", "wrong_confirmation"])
async def test_real_checkout_review_dom_extracts_and_verifies_order_composition(tmp_path, mutation):
    from playwright.async_api import async_playwright
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.route("**/*", lambda route: route.fulfill(content_type="text/html", body="<html></html>"))
            await page.goto("https://shop.test/checkout")
            await page.set_content('''
              <header><a class="shopping_cart_link">Cart<span class="shopping_cart_badge">1</span></a></header>
              <main><div class="inventory_item" data-product-id="4"><span class="inventory_item_name">Backpack</span><span class="inventory_item_price">$29.99</span><button>Remove</button></div><button id="review">Review order</button></main>
            ''')
            await page.evaluate("""mutation => {
              document.querySelector('#review').onclick=()=>{
                const main=document.querySelector('main');
                main.innerHTML=`<div class="cart_list"><div class="cart_item" data-product-id="4"><span class="inventory_item_name">Backpack</span><span class="cart_quantity">1</span><span class="inventory_item_price">$${mutation==='wrong_price'?'99.99':'29.99'}</span></div></div><div class="summary_info"><div class="summary_subtotal_label">Item total: $${mutation==='wrong_subtotal'?'39.99':'29.99'}</div><div class="summary_tax_label">Tax: $2.40</div><div class="summary_total_label">Total: $${mutation==='wrong_total'?'99.99':'32.39'}</div><button>Finish</button></div>`;
                main.querySelector('button').onclick=()=>{
                  main.innerHTML=`<div class="checkout_complete_container"><h2 class="complete-header">${mutation==='wrong_confirmation'?'Your cart was updated':'Thank you for your order!'}</h2></div>`;
                };
              };
            }""", mutation)
            executor = PlaywrightExecutor(tmp_path, ExplorationOptions(capture_screenshots=False))
            executor._page, executor._browser, executor._context = page, browser, page.context
            executor._allowed_origin = executor._origin(page.url)
            observation = await executor.observe()
            for label in ["Review order", "Finish"]:
                before = observation
                target = next(item for item in observation.interactive_elements if item.get("name") == label)
                decision = AgentDecision("click", label, target=target["ref"])
                result, observation = await executor.execute(decision, {})
                assert result.success, result.message
                checks = check_business_outcomes(before, decision, result, observation)
                assert {check.kind for check in checks} >= {"checkout_items_match_cart", "checkout_subtotal", "checkout_total"}
                failing = mutation in {"wrong_subtotal", "wrong_total", "wrong_price"} or label == "Finish" and mutation == "wrong_confirmation"
                assert all(check.passed for check in checks) is (not failing)
                if label == "Finish":
                    confirmation = next(check for check in checks if check.kind == "order_confirmation")
                    assert confirmation.passed is (mutation != "wrong_confirmation")
        finally:
            await browser.close()


@pytest.mark.parametrize("mutated", [False, True])
def test_sort_checks_the_products_not_just_the_select_value(mutated):
    field = {"identity": "sort", "ref": "e0", "selected_values": ["az"]}
    products = [
        {"id": "a", "name": "Backpack", "price": "$29.99"},
        {"id": "b", "name": "Bike Light", "price": "$9.99"},
    ]
    before = observed(
        {"fields": [field], "products": products},
        [
            {
                "ref": "e0",
                "tag": "select",
                "options": [{"value": "lohi", "label": "Price (low to high)"}],
            }
        ],
    )
    after = observed(
        {
            "fields": [{**field, "selected_values": ["lohi"]}],
            "products": products if mutated else list(reversed(products)),
        }
    )
    result, ct = verdict(
        before,
        after,
        AgentDecision("select", "sort by price", target="e0", value="lohi"),
    )
    assert result.assertions[0].passed is True  # the control accepted the value
    assert next(
        a for a in result.assertions if a.kind == "product_sort_order"
    ).passed is (not mutated)
    assert ct.status == (ScenarioStatus.FAIL if mutated else ScenarioStatus.PASS)


def test_sort_equal_prices_do_not_impose_arbitrary_tie_order():
    products = [{"id": "a", "price": "$9.99"}, {"id": "b", "price": "$9.99"}]
    fields = [{"identity": "sort", "ref": "e0", "selected_values": ["lohi"]}]
    before = observed(
        {"fields": fields, "products": products},
        [{"ref": "e0", "options": [{"value": "lohi", "label": "Price (low to high)"}]}],
    )
    _, ct = verdict(
        before,
        observed({"fields": fields, "products": list(reversed(products))}),
        AgentDecision("select", "sort", target="e0", value="lohi"),
    )
    assert ct.status == ScenarioStatus.PASS


@pytest.mark.parametrize(
    ("label", "key", "descending"),
    [
        ("Preço crescente", "price", False),
        ("Preço decrescente", "price", True),
        ("Nome crescente", "name", False),
        ("Nome decrescente", "name", True),
    ],
)
@pytest.mark.parametrize("wrong_direction", [False, True])
def test_portuguese_sort_direction_checks_declared_intent(
    label, key, descending, wrong_direction
):
    products = [
        {"id": "a", "name": "Backpack", "price": "$29.99"},
        {"id": "b", "name": "Bike Light", "price": "$9.99"},
    ]
    field = {"identity": "sort", "ref": "e0", "selected_values": ["default"]}
    before = observed(
        {"fields": [field], "products": products},
        [{"ref": "e0", "options": [{"value": "ordered", "label": label}]}],
    )
    actual_order = sorted(
        products,
        key=lambda item: (
            float(item["price"].lstrip("$")) if key == "price" else item["name"]
        ),
        reverse=descending != wrong_direction,
    )
    result, ct = verdict(
        before,
        observed(
            {
                "fields": [{**field, "selected_values": ["ordered"]}],
                "products": actual_order,
            }
        ),
        AgentDecision("select", "sort", target="e0", value="ordered"),
    )
    assertion = next(
        item for item in result.assertions if item.kind == "product_sort_order"
    )
    assert assertion.expected["descending"] is descending
    assert assertion.passed is (not wrong_direction)
    assert ct.status == (
        ScenarioStatus.FAIL if wrong_direction else ScenarioStatus.PASS
    )


@pytest.mark.parametrize("mutated", [False, True])
def test_lastname_overwriting_firstname_is_a_failed_result(mutated):
    fields = [
        {
            "identity": "first",
            "ref": "e0",
            "form": "checkout",
            "label": "First Name",
            "value_token": "alice",
        },
        {
            "identity": "last",
            "ref": "e1",
            "form": "checkout",
            "label": "Last Name",
            "value_token": "empty",
        },
    ]
    after_fields = deepcopy(fields)
    after_fields[0]["value_token"] = "smith" if mutated else "alice"
    after_fields[1]["value_token"] = "empty" if mutated else "smith"
    before, after = observed({"fields": fields}), observed({"fields": after_fields})
    decision = AgentDecision(
        "fill", "fill surname", target="e1", value="synthetic surname"
    )
    result, ct = verdict(
        before,
        after,
        decision,
        executed(
            before,
            after,
            decision,
            evidence={
                "business_input": {"identity": "last", "expected_token": "smith"}
            },
        ),
    )
    assert {a.kind for a in result.assertions} == {
        "field_value_matches",
        "other_fields_unchanged",
    }
    assert ct.status == (ScenarioStatus.FAIL if mutated else ScenarioStatus.PASS)
    assert "synthetic surname" not in str(ct.evidence)


def test_no_assertion_is_invented_for_opaque_or_credential_fields():
    before = observed(
        {"fields": [{"identity": "password", "ref": "e0", "sensitive": True}]}
    )
    assert (
        check_business_outcomes(
            before,
            AgentDecision("fill", "login", target="e0", credential_key="password"),
            executed(before, before, AgentDecision("fill", "login")),
            before,
        )
        == []
    )


def test_sanitizer_preserves_opaque_tokens_when_private_text_matches_hash(
    tmp_path, monkeypatch
):
    executor = PlaywrightExecutor(tmp_path, ExplorationOptions())
    token = "bad" + "a" * 61
    monkeypatch.setattr(executor, "_field_value_token", lambda _value: token)
    state = executor._sanitize_page_state(
        {
            "fields": [
                {
                    "identity": "form::field",
                    "ref": "e0",
                    "sensitive": False,
                    "_raw_value": "bad",
                }
            ]
        }
    )
    assert state["fields"][0]["value_token"] == token
    assert "_raw_value" not in state["fields"][0]
    assert "value" not in state["fields"][0]


def test_derived_readonly_field_update_is_not_an_overwrite_failure():
    before = observed(
        {
            "fields": [
                {
                    "identity": "name",
                    "ref": "e0",
                    "form": "f",
                    "label": "Name",
                    "value": "",
                },
                {"identity": "derived", "form": "f", "read_only": True, "value": ""},
            ]
        }
    )
    after = observed(
        {
            "fields": [
                {"identity": "name", "ref": "e0", "form": "f", "value": "QA"},
                {
                    "identity": "derived",
                    "form": "f",
                    "read_only": True,
                    "value": "Derived QA",
                },
            ]
        }
    )
    result, ct = verdict(
        before, after, AgentDecision("fill", "name", target="e0", value="QA")
    )
    assert ct.status == ScenarioStatus.PASS
    assert [a.kind for a in result.assertions] == ["field_value_matches"]


@pytest.mark.parametrize(
    "retained,errors,expected",
    [(False, [], True), (True, ["First Name is required"], False), (True, [], False)],
)
def test_complete_required_step_must_advance(retained, errors, expected):
    fields = [
        {
            "identity": name,
            "ref": f"e{i}",
            "form": "checkout",
            "label": name,
            "required": True,
            "has_value": True,
        }
        for i, name in enumerate(["First Name", "Last Name", "Postal Code"])
    ]
    forms = [{"id": "checkout", "submit_refs": ["e3"]}]
    before = observed(
        {"fields": fields, "forms": forms},
        [{"ref": "e3", "name": "Continue", "type": "submit"}],
    )
    after = observed(
        {
            "fields": fields if retained else [],
            "forms": forms if retained else [],
            "validation_errors": errors,
        }
    )
    if not retained:
        after.text = "Review order"
    result, ct = verdict(
        before, after, AgentDecision("click", "submit checkout", target="e3")
    )
    assert (
        next(a for a in result.assertions if a.kind == "form_progress").passed
        is expected
    )
    assert ct.status == (ScenarioStatus.PASS if expected else ScenarioStatus.FAIL)


def test_expected_required_validation_is_a_passing_negative_test():
    fields = [
        {
            "identity": "first",
            "ref": "e0",
            "form": "checkout",
            "required": True,
            "has_value": False,
        }
    ]
    forms = [{"id": "checkout", "submit_refs": ["e1"]}]
    before = observed({"fields": fields, "forms": forms})
    after = observed(
        {
            "fields": fields,
            "forms": forms,
            "validation_errors": ["First Name is required"],
        }
    )
    _, ct = verdict(
        before, after, AgentDecision("click", "test validation", target="e1")
    )
    assert ct.status == ScenarioStatus.PASS


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("missing", [False, True])
def test_existing_other_form_alert_is_not_checkout_validation(scoped, missing):
    fields = [
        {
            "identity": "checkout:zip",
            "ref": "e0",
            "form": "checkout",
            "required": True,
            "has_value": not missing,
        }
    ]
    forms = [{"id": "checkout", "submit_refs": ["e1"]}]
    if scoped:
        forms[0]["validation_errors"] = []
    unrelated = "Newsletter subscription is invalid"
    before = observed(
        {"fields": fields, "forms": forms, "validation_errors": [unrelated]},
        [{"ref": "e1", "name": "Continue", "type": "submit"}],
    )
    after = observed(
        {
            "fields": fields if missing else [],
            "forms": [
                *(forms if missing else []),
                {"id": "newsletter", "validation_errors": [unrelated]},
            ],
            "validation_errors": [unrelated],
        },
        url=before.url if missing else "https://shop.test/review",
    )
    result, ct = verdict(before, after, AgentDecision("click", "checkout", target="e1"))
    check = result.assertions[0]
    assert check.actual["validation_errors"] == []
    assert check.passed is (not missing)
    assert ct.status == (ScenarioStatus.FAIL if missing else ScenarioStatus.PASS)


@pytest.mark.parametrize("submitted_form_has_metadata", [False, True])
def test_new_other_form_alert_is_excluded_when_ownership_is_known(
    submitted_form_has_metadata,
):
    field = {
        "identity": "zip",
        "form": "checkout",
        "required": True,
        "has_value": False,
    }
    forms = [{"id": "checkout", "submit_refs": ["e1"]}]
    if submitted_form_has_metadata:
        forms[0]["validation_errors"] = []
    before = observed({"fields": [field], "forms": forms})
    after = observed(
        {
            "fields": [field],
            "forms": [*forms, {"id": "newsletter", "validation_errors": ["Bad email"]}],
            "validation_errors": ["Bad email"],
        }
    )
    result, ct = verdict(before, after, AgentDecision("click", "checkout", target="e1"))
    assert result.assertions[0].actual["validation_errors"] == []
    assert ct.status == ScenarioStatus.FAIL


def test_current_form_validation_is_observable_even_if_already_present():
    field = {
        "identity": "zip",
        "form": "checkout",
        "required": True,
        "has_value": False,
    }
    forms = [
        {
            "id": "checkout",
            "submit_refs": ["e1"],
            "validation_errors": ["Zip is required"],
        }
    ]
    before = observed({"fields": [field], "forms": forms})
    _, ct = verdict(before, before, AgentDecision("click", "checkout", target="e1"))
    assert ct.status == ScenarioStatus.PASS


@pytest.mark.parametrize("owned_by_other_form", [False, True])
def test_new_global_error_after_form_disappears_requires_action_attribution(
    owned_by_other_form,
):
    before = observed(
        {
            "fields": [
                {
                    "identity": "zip",
                    "form": "checkout",
                    "required": True,
                    "has_value": True,
                }
            ],
            "forms": [
                {"id": "checkout", "submit_refs": ["e1"], "validation_errors": []}
            ],
        },
        [{"ref": "e1", "name": "Continue", "type": "submit"}],
    )
    error = "Checkout could not be completed"
    after = observed(
        {
            "fields": [],
            "forms": [{"id": "newsletter", "validation_errors": [error]}]
            if owned_by_other_form
            else [],
            "validation_errors": [error],
        },
        url="https://shop.test/review",
    )
    result, ct = verdict(before, after, AgentDecision("click", "checkout", target="e1"))
    assertion = next(item for item in result.assertions if item.kind == "form_progress")
    assert assertion.actual["validation_errors"] == (
        [] if owned_by_other_form else [error]
    )
    assert assertion.passed is owned_by_other_form
    assert ct.status == (
        ScenarioStatus.PASS if owned_by_other_form else ScenarioStatus.FAIL
    )


@pytest.mark.parametrize("destination", ["login", "error", "empty"])
def test_form_disappearing_is_not_alone_a_successful_business_step(destination):
    before = observed(
        {
            "fields": [
                {
                    "identity": "zip",
                    "form": "checkout",
                    "required": True,
                    "has_value": True,
                }
            ],
            "forms": [
                {"id": "checkout", "submit_refs": ["e1"], "validation_errors": []}
            ],
        },
        [{"ref": "e1", "name": "Continue", "type": "submit"}],
    )
    after = observed({"fields": [], "forms": []})
    if destination == "login":
        after.url = "https://shop.test/login"
        after.text = "Login Username Password"
        after.interactive_elements = [
            {"ref": "e0", "type": "password", "name": "Password"}
        ]
    elif destination == "error":
        after.url = "https://shop.test/error"
        after.title = "Internal Server Error"
    result, ct = verdict(before, after, AgentDecision("click", "checkout", target="e1"))
    assertion = next(item for item in result.assertions if item.kind == "form_progress")
    assert assertion.passed is False
    assert ct.status == ScenarioStatus.FAIL
    if destination == "empty":
        assert assertion.actual["advancement_observed"] is False
    else:
        assert assertion.actual["interrupted_destination"] is not None


def test_hydrated_spa_404_does_not_by_itself_fail_form_advancement():
    before = observed(
        {
            "fields": [
                {
                    "identity": "zip",
                    "form": "checkout",
                    "required": True,
                    "has_value": True,
                }
            ],
            "forms": [
                {"id": "checkout", "submit_refs": ["e1"], "validation_errors": []}
            ],
        },
        [{"ref": "e1", "name": "Continue", "type": "submit"}],
    )
    after = observed(
        {"fields": [], "forms": [], "navigation_response": {"status": 404}},
        url="https://shop.test/review",
    )
    after.text = "Review order"
    _, ct = verdict(before, after, AgentDecision("click", "checkout", target="e1"))
    assert ct.status == ScenarioStatus.PASS


@pytest.mark.parametrize("mutation", ["standard", "problem", "error"])
async def test_real_checkout_dom_plans_fields_and_detects_sample_app_mutations(
    tmp_path, mutation
):
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.route(
                "**/*",
                lambda route: route.fulfill(
                    status=404, content_type="text/html", body="<html></html>"
                ),
            )
            await page.goto("https://shop.test/checkout")
            await page.set_content("""
                <a href="#cart" aria-label="Cart, empty">Cart</a>
                <div class="checkout_info"><form id="checkout">
                <input id="first-name" data-test="firstName" placeholder="First Name">
                <input id="last-name" data-test="lastName" placeholder="Last Name">
                <input id="postal-code" data-test="postalCode" placeholder="Zip/Postal Code">
                <button onclick="event.preventDefault()">Cancel</button><input type="submit" value="Continue">
                </form></div>
            """)
            await page.evaluate(
                """mutation => {
                const first = document.querySelector('#first-name');
                const last = document.querySelector('#last-name');
                if (mutation !== 'standard') last.addEventListener('input', () => {
                    if (mutation === 'problem') { first.value = last.value; last.value = ''; }
                    else { last.value = ''; throw new TypeError('Last Name cannot be set'); }
                });
                document.querySelector('form').addEventListener('submit', event => {
                    event.preventDefault();
                    const fields = [...document.querySelectorAll('input:not([type=submit])')];
                    if (fields.every(field => field.value)) document.querySelector('.checkout_info').remove();
                    else { const error = document.createElement('p'); error.setAttribute('role','alert'); error.innerText='Required field missing'; document.querySelector('form').append(error); }
                });
            }""",
                mutation,
            )
            executor = PlaywrightExecutor(
                tmp_path, ExplorationOptions(capture_screenshots=False)
            )
            executor._browser, executor._page = browser, page
            executor._context = page.context
            executor._allowed_origin = executor._origin(page.url)
            planner = CoveragePlanner(page.url)
            observation = await executor.observe()
            assert len(observation.page_state["fields"]) == 3
            submit = next(
                el
                for el in observation.interactive_elements
                if el.get("name") == "Continue"
            )
            cancel = next(
                el
                for el in observation.interactive_elements
                if el.get("name") == "Cancel"
            )
            assert await page.locator('button').evaluate("node => node.type") == "submit"
            assert submit["ref"] in observation.page_state["forms"][0]["submit_refs"]
            assert (
                cancel["ref"] not in observation.page_state["forms"][0]["submit_refs"]
            )
            statuses, probes, ordinary_submit = [], [], False
            for index in range(30):
                decision, _ = planner.reconcile(
                    observation, AgentDecision("click", "premature cart", target="e0")
                )
                before = observation
                if index < 3:
                    target = next(item for item in before.interactive_elements
                                  if item.get("ref") == decision.target)
                    assert decision.action == "fill" and decision.value
                    assert target["name"] == ["First Name", "Last Name", "Zip/Postal Code"][index]
                result, observation = await executor.execute(decision, {})
                assert result.success, (decision, result.message)
                result.assertions.extend(
                    check_business_outcomes(before, decision, result, observation)
                )
                planner.record_action(before, decision, result, observation)
                scenario = ExplorationManager._scenario_from_evidence(observation, decision, result, index + 1)
                statuses.append(scenario.status)
                probe = (decision.scenario_hint or {}).get("validation_probe")
                if probe:
                    probes.append((probe, scenario.status))
                if decision.action == "click" and decision.target == submit["ref"] and not probe:
                    ordinary_submit = True
                    break
            assert index < 29 and ordinary_submit
            assert statuses[:3] == (
                [ScenarioStatus.PASS] * 3
                if mutation == "standard"
                else [
                    ScenarioStatus.PASS,
                    ScenarioStatus.FAIL,
                    ScenarioStatus.PASS,
                ]
            )
            assert [probe["name"] for probe, _ in probes] == ["First Name", "Last Name", "Zip/Postal Code"]
            if mutation == "standard":
                assert all(status == ScenarioStatus.PASS for status in statuses)
                assert all(probe["isolation_established"] for probe, _ in probes)
                assert not observation.page_state["fields"]  # reached the next step
            else:
                # Generic feedback from the broken Last Name peer cannot
                # approve isolated First Name or Postal Code validation.
                assert [status for _, status in probes] == [ScenarioStatus.BLOCKED, ScenarioStatus.PASS,
                                                            ScenarioStatus.BLOCKED]
                assert len(observation.page_state["fields"]) == 3
            assert not any(
                    item["suggested_action"] == "fill"
                    for item in planner.context(observation)["current_untried"]
                )
            # Values from a real user never appear in persisted semantic facts.
            if mutation != "standard":
                await page.fill("#postal-code", "private postal")
                state = (await executor.observe()).page_state
                assert "private postal" not in str(state)
        finally:
            await browser.close()


@pytest.mark.parametrize("mutated", [False, True])
async def test_real_inventory_dom_verifies_add_remove_sort_and_product_identity(
    tmp_path, mutated
):
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.route(
                "**/*",
                lambda route: route.fulfill(
                    content_type="text/html", body="<html></html>"
                ),
            )
            await page.goto("https://shop.test/inventory")
            await page.set_content("""
                <header id="header_container" class="inventory_details">
                  <a class="shopping_cart_link" href="#cart" aria-label="Cart, empty"></a>
                </header>
                <select aria-label="Sort products"><option value="az">Name (A to Z)</option><option value="lohi">Price (low to high)</option></select>
                <div id="products"></div>
                <button id="unsafe">Remove account</button>
            """)
            await page.evaluate(
                """mutated => {
                const items = [{name:'Sauce Labs Backpack',price:29.99,inCart:false}, {name:'Sauce Labs Bike Light',price:9.99,inCart:false}];
                const list = document.querySelector('#products');
                const render = () => {
                    list.replaceChildren();
                    items.forEach((item, index) => {
                        const card = document.createElement('div'); card.className='inventory_item';
                        card.innerHTML=`<a href="#detail"><span class="inventory_item_name">${item.name}</span></a><span class="inventory_item_price">$${item.price}</span><button>${item.inCart ? 'Remove' : 'Add to cart'}</button>`;
                        card.querySelector('button').onclick=() => { if (mutated) return; item.inCart=!item.inCart; render(); };
                        card.querySelector('a').onclick=event => {
                            event.preventDefault(); const selected = mutated ? items[1-index] : item;
                            list.innerHTML=`<div class="inventory_details"><span class="inventory_details_name">${selected.name}</span><span class="inventory_details_price">$${selected.price}</span></div>`;
                        };
                        list.append(card);
                    });
                    const cart = document.querySelector('.shopping_cart_link');
                    const count = items.filter(item=>item.inCart).length;
                    cart.innerHTML='Cart' + (count ? `<span class="shopping_cart_badge">${count}</span>` : '');
                    cart.setAttribute('aria-label',count ? `Cart, ${count} items` : 'Cart, empty');
                };
                document.querySelector('select').onchange=() => { if (!mutated) { items.sort((a,b)=>a.price-b.price); render(); } };
                render();
            }""",
                mutated,
            )
            executor = PlaywrightExecutor(
                tmp_path, ExplorationOptions(capture_screenshots=False)
            )
            executor._browser, executor._page, executor._context = (
                browser,
                page,
                page.context,
            )
            executor._allowed_origin = executor._origin(page.url)
            observation = await executor.observe()
            assert len(observation.page_state["products"]) == 2
            assert observation.page_state.get("product_detail") is None
            assert observation.page_state["cart"]["count"] == 0
            phases = ["add"] if mutated else ["add", "remove"]
            phases += ["sort", "detail"]
            for phase in phases:
                before = observation
                if phase == "sort":
                    target = next(
                        el
                        for el in before.interactive_elements
                        if el["tag"] == "select"
                    )
                    decision = AgentDecision(
                        "select", "price order", target=target["ref"], value="lohi"
                    )
                else:
                    product = before.page_state["products"][0]
                    target_ref = (
                        product["link_refs"][0]
                        if phase == "detail"
                        else product["action_refs"][0]
                    )
                    target = next(
                        el
                        for el in before.interactive_elements
                        if el["ref"] == target_ref
                    )
                    if phase == "remove":
                        assert target["reversible_cart_action"] == "remove"
                        assert CoveragePlanner._safe_candidate(
                            target, ("https", "shop.test")
                        )
                    decision = AgentDecision("click", phase, target=target_ref)
                result, observation = await executor.execute(decision, {})
                assert result.success, result.message
                result, ct = verdict(before, observation, decision, result)
                assert ct.status == (
                    ScenarioStatus.FAIL if mutated else ScenarioStatus.PASS
                ), (phase, ct.evidence)
                if phase == "detail":
                    assert observation.page_state["product_detail"]["name"]
                    cart_ref = next(el["ref"] for el in observation.interactive_elements if el.get("tag") == "a" and "cart" in el.get("href", ""))
                    assert cart_ref not in observation.page_state["product_detail"]["link_refs"]
            unsafe = page.locator("#unsafe")
            with pytest.raises(PermissionError, match="PolicyBlocked"):
                await executor._enforce_safe_interaction(unsafe)
            assert not CoveragePlanner._safe_candidate(
                {
                    "ref": "e99",
                    "tag": "button",
                    "name": "Remove account",
                    "reversible_cart_action": "remove",
                },
                ("https", "shop.test"),
            )
        finally:
            await browser.close()
