"""Whole explorer regressions with real Chromium, isolated DB and fake LLMs.

Every browser request is fulfilled in process. No user provider, API key,
application history or external site is touched by these tests.
"""

import asyncio
import json

import pytest
from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.adapters.report import StandaloneHtmlReport
from autojev.adapters.repository import SQLiteRepository
from autojev.adapters.secrets import EphemeralCredentialVault, SecretCipher
from autojev.config import Settings
from autojev.domain.models import (
    ActionResult,
    AgentDecision,
    ExplorationOptions,
    ExplorationRequest,
    ModelSelection,
    Observation,
    ProviderModel,
    ScenarioStatus,
    utc_now,
)
from autojev.services.business_checks import check_business_outcomes
from autojev.services.coverage import CoveragePlanner
from autojev.services.exploration import ExplorationManager

SHOP = """<!doctype html><html><head><title>Local QA Shop</title>
<style>body{font:16px sans-serif;margin:24px}header{display:flex;gap:32px}
.inventory_item{border:1px solid #ddd;padding:20px;margin:16px;max-width:560px}
input,button,select{padding:8px;margin:8px}label{display:block}
img{width:64px;height:64px}#menu-panel{border:1px solid #ddd;padding:12px}
nav a,nav button{display:block}[hidden]{display:none!important}</style></head><body><header id="header_container" class="inventory_details">
<button id="react-burger-menu-btn" aria-label="Open Menu" aria-controls="menu-panel" onclick="setMenu(true)">Open Menu</button>
<a id="products-header-link" href="/inventory.html">Products</a>
<a class="shopping_cart_link" href="/cart.html">Cart <span class="shopping_cart_badge"></span></a>
</header><aside id="menu-panel" class="bm-menu" hidden>
 <button id="react-burger-cross-btn" aria-label="Close Menu" onclick="setMenu(false)">Close Menu</button>
 <nav id="main-menu" aria-label="Main navigation">
  <a id="inventory_sidebar_link" href="#" onclick="event.preventDefault();go('/inventory.html')">All Items</a>
  <button id="catalog-toggle" aria-controls="catalog-menu" aria-expanded="false" onclick="setCatalog(!catalogOpen)">Dynamic Catalog</button>
  <div id="catalog-menu" hidden>
   <a id="catalog-backpacks" href="#" onclick="event.preventDefault();go('/inventory.html?category=backpacks')">Backpacks</a>
   <a id="catalog-lights" href="#" onclick="event.preventDefault();go('/inventory.html?category=lights')">Lights</a>
  </div>
  <a id="reset_sidebar_link" href="#" onclick="event.preventDefault();resetCart()">Reset App State</a>
 </nav>
</aside><main></main><script>
const MUTATED = __MUTATED__;
window.fixtureDocumentId = Math.random().toString(36).slice(2);
const products = [{id:'4',name:'Sauce Labs Backpack',price:29.99},
                  {id:'0',name:'Sauce Labs Bike Light',price:9.99}];
let cart=JSON.parse(sessionStorage.getItem('cart') || '[]');
let order=sessionStorage.getItem('sort') || 'az';
let menuOpen=false, catalogOpen=false;
const main=document.querySelector('main');
function go(url){history.pushState({},'',url);render();}
window.addEventListener('popstate',render);
document.addEventListener('click',event=>{
 const link=event.target.closest('a');if(!link||link.getAttribute('href')==='#')return;
 const url=new URL(link.href,location.href);if(url.origin!==location.origin)return;
 event.preventDefault();go(url.pathname+url.search);
});
function setMenu(open){menuOpen=open;document.querySelector('#menu-panel').hidden=!open;
 document.querySelector('#react-burger-menu-btn').setAttribute('aria-expanded',String(open));}
function setCatalog(open){catalogOpen=open;document.querySelector('#catalog-menu').hidden=!open;
 document.querySelector('#catalog-toggle').setAttribute('aria-expanded',String(open));}
function resetCart(){cart=[];sessionStorage.setItem('cart','[]');render();}
function badge(){document.querySelector('.shopping_cart_badge').textContent=cart.length || '';}
function toggle(id){
  if (MUTATED && id==='0' && !cart.includes(id)) return;
  cart=cart.includes(id)?cart.filter(x=>x!==id):[...cart,id];
  sessionStorage.setItem('cart',JSON.stringify(cart));render();
}
function item(p,detail=false){return `<article class="${detail?'inventory_details':'inventory_item'}" data-product-id="${p.id}">
 <img alt="${p.name}" src="/product.svg">
 <div class="${detail?'inventory_details_name':'inventory_item_name'}">${detail?p.name:`<a href="/inventory_item.html?id=${p.id}">${p.name}</a>`}</div>
 <p class="inventory_item_desc">QA product description</p>
 <div class="${detail?'inventory_details_price':'inventory_item_price'}">$${p.price.toFixed(2)}</div>
 <button id="${cart.includes(p.id)?'remove':'add-to-cart'}-${p.id}" onclick="toggle('${p.id}')">${cart.includes(p.id)?'Remove':'Add to cart'}</button></article>`;}
function render(){
 badge();let path=location.pathname;
 if(path==='/inventory.html'){
  const category=new URLSearchParams(location.search).get('category');
  let sorted=products.filter(p=>!category||(category==='backpacks'?p.id==='4':p.id==='0'));
  if(order==='lohi')sorted.sort((a,b)=>a.price-b.price);
  main.innerHTML=`<h1>Products</h1><select data-test="product-sort-container" aria-label="Product sort">
   <option value="az">Name (A to Z)</option><option value="lohi">Price (low to high)</option>
   </select>`+sorted.map(p=>item(p)).join('');
  const sort=main.querySelector('select');sort.value=order;
  sort.onchange=()=>{order=sort.value;sessionStorage.setItem('sort',order);render();};
 }else if(path==='/inventory_item.html'){
  main.innerHTML='<a href="/inventory.html">Back to products</a>'+item(products.find(p=>p.id===new URLSearchParams(location.search).get('id')),true);
 }else if(path==='/cart.html'){
  main.innerHTML='<h1>Your Cart</h1><div class="cart_list">'+products.filter(p=>cart.includes(p.id)).map(p=>item(p)).join('')+'</div><a href="/inventory.html">Continue Shopping</a><a href="/checkout-step-one.html">Checkout</a>';
 }else if(path==='/checkout-step-one.html'){
  main.innerHTML=`<h1>Checkout: Your Information</h1><form class="checkout_info">
   <label>First Name<input data-test="firstName" id="first-name" placeholder="First Name"></label>
   <label>Last Name<input data-test="lastName" id="last-name" placeholder="Last Name"></label>
   <label>Zip/Postal Code<input data-test="postalCode" id="postal-code" placeholder="Zip/Postal Code"></label>
   <button type="button" id="cancel" onclick="go('/cart.html')">Cancel</button>
   <input type="submit" value="Continue"></form>`;
  const f=main.querySelector('form');
  if(MUTATED) f.querySelector('#last-name').oninput=()=>{
   f.querySelector('#first-name').value=f.querySelector('#last-name').value;
   f.querySelector('#last-name').value='';};
  f.onsubmit=e=>{e.preventDefault();
   if([...f.querySelectorAll('input:not([type=submit])')].some(i=>!i.value)){
    let error=f.querySelector('[role=alert]');if(!error){error=document.createElement('p');error.setAttribute('role','alert');f.append(error);}
    error.textContent='Error: All checkout fields are required';
    const dismiss=document.createElement('button');dismiss.type='button';dismiss.setAttribute('aria-label','Dismiss error');
    dismiss.onclick=()=>error.remove();error.append(dismiss);return;}
   go('/checkout-step-two.html');};
 }else if(path==='/checkout-step-two.html'){
  const chosen=products.filter(p=>cart.includes(p.id));
  const subtotal=chosen.reduce((sum,p)=>sum+p.price,0), tax=subtotal*0.08;
  main.innerHTML='<h1>Checkout: Overview</h1><div class="cart_list">'+chosen.map(p=>
   `<article class="cart_item"><div class="cart_quantity">1</div><div class="inventory_item_name">${p.name}</div>
    <div class="inventory_item_price">$${p.price.toFixed(2)}</div></article>`).join('')+
   `</div><section class="summary_info"><p class="summary_subtotal_label">Item total: $${subtotal.toFixed(2)}</p>
    <p class="summary_tax_label">Tax: $${tax.toFixed(2)}</p><p class="summary_total_label">Total: $${(subtotal+tax).toFixed(2)}</p></section>
    <button id="finish">Finish</button><button id="cancel" onclick="go('/inventory.html')">Cancel</button>`;
  document.querySelector('#finish').onclick=()=>{
   cart=[];sessionStorage.setItem('cart','[]');go('/checkout-complete.html');};
 }else if(path==='/checkout-complete.html'){
  main.innerHTML='<section class="checkout_complete_container"><h2 class="complete-header">Thank you for your order!</h2></section><a href="/inventory.html">Back Home</a>';
 }
}
render();</script></body></html>"""


class FixtureClient:
    def __init__(self, repository, run_id):
        self.repository, self.run_id = repository, run_id
        self.calls = []
        self.calls_while_running = 0

    async def chat_json(self, _model, _system, content, **_kwargs):
        self.calls.append(content)
        if self.repository.get_exploration(self.run_id, False)["status"] == "running":
            self.calls_while_running += 1
        await asyncio.sleep(0.01)  # yield so CT work overlaps browser work
        return {
            "title": "Documented local contract",
            "visual_assessment": {"findings": []},
        }

    def drain_usage(self):
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost": 0,
            "cost_known": False,
        }


async def test_spa_fixture_placeholder_routes_and_review_cancellation(tmp_path):
    """The whole-explorer oracle must resemble the app's actual navigation.

    This independently checks the fixture itself: real DOM interactions use a
    single SPA document, menu placeholders navigate only through their handler,
    and review cancellation preserves a selected product in the catalog and in
    the subsequently opened cart. No manager or planner can redefine the oracle.
    """

    class Executor(PlaywrightExecutor):
        async def _prepare_page(self, page):
            await super()._prepare_page(page)

            async def serve(route):
                if route.request.url.endswith(".svg"):
                    await route.fulfill(
                        content_type="image/svg+xml",
                        body='<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"/>',
                    )
                else:
                    await route.fulfill(
                        content_type="text/html",
                        body=SHOP.replace("__MUTATED__", "false"),
                    )

            await page.route("**/*", serve)

    executor = Executor(tmp_path, ExplorationOptions(capture_screenshots=False))
    try:
        await executor.start("http://shop.fixture.test/inventory.html")
        page = executor._page
        document_id = await page.evaluate("window.fixtureDocumentId")
        await page.get_by_role("button", name="Open Menu", exact=True).click()
        await page.get_by_role("button", name="Dynamic Catalog", exact=True).click()
        backpacks = page.get_by_role("link", name="Backpacks", exact=True)
        assert await backpacks.get_attribute("href") == "#"
        await backpacks.click()
        assert page.url.endswith("/inventory.html?category=backpacks")
        assert await page.locator(".inventory_item").count() == 1
        await page.get_by_role("link", name="All Items", exact=True).click()
        assert page.url.endswith("/inventory.html")
        assert await page.locator(".inventory_item").count() == 2
        await page.get_by_role("button", name="Close Menu", exact=True).click()
        await page.locator("#add-to-cart-4").click()
        await page.locator(".shopping_cart_link").click()
        await page.get_by_role("link", name="Checkout", exact=True).click()
        for field, value in (("#first-name", "Alex"), ("#last-name", "Taylor"), ("#postal-code", "12345")):
            await page.locator(field).fill(value)
        await page.get_by_role("button", name="Continue", exact=True).click()
        assert page.url.endswith("/checkout-step-two.html")
        assert await page.locator(".summary_subtotal_label").inner_text() == "Item total: $29.99"
        await page.get_by_role("button", name="Cancel", exact=True).click()
        assert page.url.endswith("/inventory.html")
        assert await page.locator(".shopping_cart_badge").inner_text() == "1"
        assert await page.locator("#remove-4").inner_text() == "Remove"
        await page.locator(".shopping_cart_link").click()
        assert await page.locator(".cart_list .inventory_item_name").all_inner_texts() == ["Sauce Labs Backpack"]
        assert await page.evaluate("window.fixtureDocumentId") == document_id
    finally:
        await executor.close()


@pytest.mark.parametrize("evidence_mode", ["text", "visual"])
@pytest.mark.parametrize("mutated", [False, True])
async def test_full_exploration_finishes_and_preserves_business_failures(
    tmp_path,
    monkeypatch,
    evidence_mode,
    mutated,
):
    repo = SQLiteRepository(
        tmp_path / "fixture.sqlite3", SecretCipher(tmp_path, "fixture")
    )
    repo.initialize()
    provider = repo.create_provider(
        "Fixture only",
        "http://fixture.invalid",
        None,
        True,
        [
            ProviderModel("fixture-agent", "Agent"),
            ProviderModel("fixture-aux", "Aux", supports_vision=True),
        ],
    )
    selection = lambda model: ModelSelection(provider.id, model)
    run_id = repo.create_exploration(
        ExplorationRequest(
            "http://shop.fixture.test/inventory.html",
            selection("fixture-agent"),
            selection("fixture-aux"),
            ExplorationOptions(auxiliary_evidence_mode=evidence_mode),
        )
    )
    client = FixtureClient(repo, run_id)
    settings = Settings(
        tmp_path,
        tmp_path / "fixture.sqlite3",
        tmp_path / "artifacts",
        None,
        (),
        True,
        "chromium",
        1,
        # Keep this fixture's explicit allowance finite and independent of the
        # product default (300). Check every final visual revision without
        # silently increasing its 200-call cap; these fake calls cost nothing.
        auxiliary_max_requests=200,
    )
    observed_routes, snapshots, planners = set(), [], []
    dispatched_controls, spa_transitions = [], []

    def fixture_planner(*args):
        planner = CoveragePlanner(*args)
        planners.append(planner)
        return planner

    class FixtureExecutor(PlaywrightExecutor):
        async def _prepare_page(self, page):
            await super()._prepare_page(page)

            async def serve(route):
                if not route.request.url.startswith("http://shop.fixture.test/"):
                    await route.abort()
                elif route.request.url.endswith(".svg"):
                    await route.fulfill(
                        content_type="image/svg+xml",
                        body='<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" fill="green"/></svg>',
                    )
                else:
                    await route.fulfill(
                        status=200,
                        content_type="text/html",
                        body=SHOP.replace("__MUTATED__", str(mutated).lower()),
                    )

            await page.route("**/*", serve)

        async def observe(self, label="observation"):
            observation = await super().observe(label)
            observed_routes.add(observation.url)
            snapshots.append(observation)
            return observation

        async def execute(self, decision, credentials):
            element = next((item for item in snapshots[-1].interactive_elements
                            if item.get("ref") == decision.target), {})
            before_url = self._page.url
            document_id = await self._page.evaluate("window.fixtureDocumentId")
            result, after = await super().execute(decision, credentials)
            dispatched_controls.append((decision.action, element.get("name"), result.success))
            if decision.action == "click" and before_url != after.url:
                spa_transitions.append((
                    before_url, after.url,
                    document_id == await self._page.evaluate("window.fixtureDocumentId"),
                ))
            return result, after

    class FixtureAgent:
        calls = 0

        def __init__(self, *_args):
            pass

        async def decide(self, _observation, _history, _credentials, coverage):
            FixtureAgent.calls += 1
            assert FixtureAgent.calls <= 20, (
                "Fixture planner kept consulting without progress"
            )
            candidates = coverage.get("current_untried", [])
            if not candidates:
                return AgentDecision("finish", "No local fixture candidate")
            candidate = candidates[0]
            return AgentDecision(
                candidate["suggested_action"],
                "Fixture decision",
                target=candidate["ref"],
                value=candidate.get("suggested_value"),
            )

    manager = ExplorationManager(
        repo,
        EphemeralCredentialVault(),
        StandaloneHtmlReport(settings.artifact_dir),
        settings,
    )
    monkeypatch.setattr(manager, "_client_for", lambda _id: client)
    monkeypatch.setattr(
        "autojev.services.exploration.PlaywrightExecutor", FixtureExecutor
    )
    monkeypatch.setattr("autojev.services.exploration.OpenAIJevAgent", FixtureAgent)
    monkeypatch.setattr("autojev.services.exploration.CoveragePlanner", fixture_planner)
    # This SPA now has three distinct catalog routes, a finite nested menu,
    # two purchase lanes and both form/review cancellations. The former small
    # fixture's <150/180s contract predated those additional observed routes.
    # Keep independently bounded actions, time and agent consultations; neither
    # extra time nor a COMPLETED status can waive the zero-gap healthy oracle.
    await asyncio.wait_for(manager._run(run_id), timeout=240)
    run = repo.get_exploration(run_id)
    summary = [
        (a["step_number"], a["decision"], a["result"]["message"])
        for a in run["actions"]
    ]
    planner = planners[0]
    terminal_context = planner.context(snapshots[-1])
    repo.add_event(run_id, "info", "fixture_terminal_diagnostic", "Isolated SPA fixture frontier diagnostic", {
        "context": terminal_context,
        "dispatched_controls": dispatched_controls,
        "spa_transitions": spa_transitions,
        "path": list(planner.path),
        "frontier": [
            {"key": state.key, "url": state.url,
             "moves": [
                 {"fingerprint": key, "name": item.get("name"),
                  "ref": item.get("ref"), "action": planner._move_action(item),
                  "href": item.get("href"), "in_navigation": item.get("in_navigation"),
                  "journey_role": item.get("_cart_journey_role"),
                  "setup_required": key in state.form_setup_required}
                 for key, item in planner._remaining(state)
             ]}
            for state in planner._frontier_states()
        ],
        "recipes": [
            {"parent_key": recipe.parent_key, "child_key": recipe.child_key,
             "fingerprint": recipe.fingerprint, "action": recipe.action,
             "setup_fields": [field[0] for field in recipe.setup],
             "checkout_submit_verified": recipe.checkout_submit_verified}
            for recipes in planner.transitions.values() for recipe in recipes
        ],
    })
    assert run["status"] == "completed", (
        run["error"],
        summary,
        [
            (
                state.url,
                [
                    (m, e.get("name"), m in state.form_setup_required)
                    for m, e in planners[0]._remaining(state)
                ],
            )
            for state in planners[0]._frontier_states()
        ],
    )
    assert run["steps_completed"] < 300, (
        summary,
        planners[0].context(snapshots[-1]),
        [(state.url, [(key, item.get("name")) for key, item in planners[0]._remaining(state)])
         for state in planners[0]._frontier_states()],
    )  # fixture is small, NOT a product limit
    if not mutated:
        # COMPLETED only means the runner finished. A healthy, entirely local
        # fixture must actually exercise its observed frontier, not quietly
        # terminate with an inaccessible Cancel/cart/form branch.
        completion = [event for event in run["events"]
                      if event["type"] in {"coverage_complete", "coverage_complete_with_restrictions"}]
        assert completion and completion[-1]["type"] == "coverage_complete", completion
        terminal = completion[-1]["payload"]
        assert terminal["coverage_complete"] is True, terminal
        assert terminal["restriction_count"] == 0 and not terminal["restrictions"], terminal
        assert terminal["frontier_states"] == 0 and terminal["frontier_candidates"] == 0, terminal
        assert not any(ct["status"] in {"FAIL", "BLOCKED"} for ct in run["scenarios"]), run["scenarios"]
    assert any("checkout-step-one.html" in route for route in observed_routes), summary
    # Menu links deliberately expose React-style href="#" placeholders. Their
    # actual JS routing must be learned by executing the controls, not by
    # inventing a destination or changing their identity on every source URL.
    menu_controls = {name for action, name, success in dispatched_controls
                     if action == "click" and success}
    assert {"Open Menu", "Close Menu", "All Items", "Dynamic Catalog",
            "Backpacks", "Lights", "Reset App State"} <= menu_controls, (
        menu_controls, summary,
    )
    assert any("category=backpacks" in route for route in observed_routes), observed_routes
    assert any("category=lights" in route for route in observed_routes), observed_routes
    assert spa_transitions and all(preserved for _, _, preserved in spa_transitions), spa_transitions
    assertions = [
        (ct, a) for ct in run["scenarios"] for a in ct["evidence"]["assertions"]
    ]
    kinds = {a["kind"] for _, a in assertions}
    assert {"cart_count", "product_sort_order", "field_value_matches"} <= kinds, (
        kinds,
        summary,
    )
    failures = [(ct, a) for ct, a in assertions if not a["passed"]]
    if mutated:
        business_failures = [(ct, a) for ct, a in failures
                             if a["kind"] != "form_validation_probe_precondition"]
        masked_checks = [(ct, a) for ct, a in failures
                         if a["kind"] == "form_validation_probe_precondition"]
        assert {"cart_count", "field_value_matches", "other_fields_unchanged"} <= {
            a["kind"] for _, a in business_failures
        }
        # A known input defect can mask a later target's negative validation.
        # This is an executed BLOCKED dependency check, not another confirmed
        # application failure. Actual cart/input business failures remain FAIL.
        assert all(ct["status"] == "FAIL" for ct, _ in business_failures)
        assert masked_checks
        assert all(ct["status"] == "BLOCKED" and a["actual"]["status"] == "masked"
                   for ct, a in masked_checks)
        first_failure = min(ct["evidence"]["action_step"] for ct, _ in business_failures)
        assert any(
            ct["status"] == "PASS" and ct["evidence"]["action_step"] > first_failure
            for ct in run["scenarios"]
        )
        assert "form_required_validation" in kinds
    else:
        assert not failures, failures
        assert "form_progress" in kinds
        assert {"form_validation_feedback_dismissed", "form_fields_preserved_on_dismiss"} <= kinds
        assert any("checkout-step-two.html" in route for route in observed_routes), (
            summary
        )
        review_cancellations = [
            ct for ct in run["scenarios"]
            if "/checkout-step-two.html" in ct["evidence"].get("before_url", "")
            and any(a["kind"] == "checkout_cancel_progress" and a["passed"]
                    for a in ct["evidence"]["assertions"])
        ]
        # SauceDemo's review Cancel returns to the catalog, not to the cart.
        # Check its actual count/selection in both finite lanes without claiming
        # that a catalog observation proves the contents of an opened basket.
        assert any(any(a["kind"] == "checkout_cancel_cart_count" and a["expected"] > 0 and a["passed"]
                       for a in ct["evidence"]["assertions"])
                   for ct in review_cancellations), summary
        assert any(any(a["kind"] == "checkout_cancel_cart_count" and a["expected"] == 0 and a["passed"]
                       for a in ct["evidence"]["assertions"])
                   for ct in review_cancellations), summary
        for ct in review_cancellations:
            checks = {a["kind"]: a for a in ct["evidence"]["assertions"]}
            assert "cart_persistence" not in checks
            assert checks["checkout_cancel_selected_products"]["passed"]
            assert "/inventory.html" in ct["evidence"]["after_url"]
            populated = bool(checks["checkout_cancel_cart_count"]["expected"])
            # A later genuine cart entry, before finishing that lane, must
            # verify the same empty/populated basket against known contents.
            cancellation_step = ct["evidence"]["action_step"]
            next_finish = min(
                order["evidence"]["action_step"] for order in run["scenarios"]
                if order["evidence"]["action_step"] > cancellation_step
                and any(a["kind"] == "order_confirmation" and a["passed"]
                        for a in order["evidence"]["assertions"])
            )
            assert any(
                cancellation_step < cart_ct["evidence"]["action_step"] < next_finish
                and "/cart.html" in cart_ct["evidence"].get("after_url", "")
                and any(a["kind"] in {"cart_persistence", "cart_contents_match"}
                        and bool(a["expected"]) == populated and a["passed"]
                        for a in cart_ct["evidence"]["assertions"])
                for cart_ct in run["scenarios"]
            ), (ct, summary)
        form_cancellations = [
            ct for ct in run["scenarios"]
            if "/checkout-step-one.html" in ct["evidence"].get("before_url", "")
            and "/cart.html" in ct["evidence"].get("after_url", "")
            and any(a["kind"] == "checkout_form_cancel_progress" and a["passed"]
                    for a in ct["evidence"]["assertions"])
        ]
        for populated in (False, True):
            assert any(
                any(a["kind"] in {"cart_persistence", "cart_contents_match"}
                    and bool(a["expected"]) == populated and a["passed"]
                    for a in ct["evidence"]["assertions"])
                for ct in form_cancellations
            ), ("The actual checkout form Cancel was not verified in both basket lanes", summary)
        # A cart persistence check followed by a zero-value empty checkout is
        # not evidence of a populated purchase. Assert the same Finish CT
        # checks concrete selected items, nonzero subtotal/total and an actual
        # order confirmation in the real browser, for text AND visual modes.
        populated_orders = [
            ct for ct in run["scenarios"]
            if any(a["kind"] == "order_confirmation" and a["passed"]
                   for a in ct["evidence"]["assertions"])
            and any(a["kind"] == "checkout_items_match_cart" and bool(a["expected"]) and a["passed"]
                    for a in ct["evidence"]["assertions"])
        ]
        assert populated_orders, summary
        assert any(any(a["kind"] == "order_confirmation" and a["passed"]
                       for a in ct["evidence"]["assertions"])
                   and any(a["kind"] == "checkout_items_match_cart" and a["expected"] == {} and a["passed"]
                           for a in ct["evidence"]["assertions"])
                   for ct in run["scenarios"]), summary
        for ct in populated_orders:
            checks = {a["kind"]: a for a in ct["evidence"]["assertions"]}
            assert float(checks["checkout_subtotal"]["expected"]) > 0
            assert float(checks["checkout_total"]["expected"]) > 0
            assert checks["checkout_subtotal"]["passed"] and checks["checkout_total"]["passed"]
    assert client.calls_while_running > 0
    events = run["events"]
    assert any(e["type"] == "scenario_draft_created" for e in events)
    assert not any(
        e["type"] in {"coverage_stalled", "loop_prevented", "limit_reached"}
        for e in events
    )
    if evidence_mode == "text":
        assert all(isinstance(content, str) for content in client.calls)
    else:
        assert any(isinstance(content, list) for content in client.calls)
        assert all(
            ct["evidence"]["visual_assessment"]["status"] == "assessed"
            for ct in run["scenarios"]
        )
    assert run["usage"]["total_tokens"] == 0
    html = (settings.artifact_dir / run_id / "report.html").read_text()
    assert "ct-timeline-v1" in html
    assert all(ct["id"] in html for ct in run["scenarios"])
    # The real snapshot must retain no raw conventional form input in its facts.
    assert "AutoJev QA first name" not in json.dumps([o.page_state for o in snapshots])


async def test_delayed_cart_api_is_observed_before_business_verdict(tmp_path):
    """A >450ms API response is not a cart no-op or a reason to call JEV."""

    class Executor(PlaywrightExecutor):
        async def _prepare_page(self, page):
            await super()._prepare_page(page)

            async def serve(route):
                if route.request.url.endswith("/cart-update"):
                    await asyncio.sleep(0.9)
                    await route.fulfill(content_type="application/json", body="{}")
                elif route.request.url.endswith(".svg"):
                    await route.fulfill(
                        content_type="image/svg+xml",
                        body='<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"/>',
                    )
                else:
                    html = SHOP.replace("__MUTATED__", "false").replace(
                        "function toggle(id){",
                        "async function toggle(id){ await fetch('/cart-update');",
                    )
                    await route.fulfill(content_type="text/html", body=html)

            await page.route("**/*", serve)

    executor = Executor(tmp_path, ExplorationOptions(capture_screenshots=False))
    try:
        before = await executor.start("http://shop.fixture.test/inventory.html")
        product = before.page_state["products"][0]
        decision = AgentDecision(
            "click", "Add first product", target=product["action_refs"][0]
        )
        result, after = await executor.execute(decision, {})
        checks = check_business_outcomes(before, decision, result, after)
        assert result.success
        assert after.page_state["cart"]["count"] == 1
        assert {check.kind for check in checks} >= {"cart_count", "cart_control_state"}
        assert all(check.passed for check in checks)
        assert "business_settle" not in result.evidence
        assert not executor._pending_app_requests
    finally:
        await executor.close()


def test_pending_api_result_is_not_a_business_failure_or_false_pass():
    before = Observation(
        "http://shop.fixture.test/cart",
        "Cart",
        "Cart",
        [],
        page_state={
            "products": [
                {
                    "id": "4",
                    "name": "Backpack",
                    "cart_action": "add",
                    "action_refs": ["e1"],
                }
            ],
            "cart": {"count": 0},
        },
    )
    result = ActionResult(
        "click",
        True,
        utc_now(),
        utc_now(),
        before.url,
        before.url,
        evidence={"business_settle": {"status": "api_wait_timed_out"}},
    )
    decision = AgentDecision("click", "Add product", target="e1")
    assert not check_business_outcomes(before, decision, result, before)
    ct = ExplorationManager._scenario_from_evidence(before, decision, result, 1)
    assert ct.status == ScenarioStatus.BLOCKED
    assert ExplorationManager._scenario_capture_policy(
        decision_source="local_policy",
        is_restoration_action=False,
        result=result,
    ) == (True, None)


async def test_api_wait_is_bounded_and_ignores_old_and_third_party_requests(tmp_path):
    import time

    executor = PlaywrightExecutor(tmp_path, ExplorationOptions())
    executor._allowed_origin = executor._origin("http://shop.fixture.test")

    class Page:
        def is_closed(self):
            return False

        async def wait_for_timeout(self, milliseconds):
            await asyncio.sleep(milliseconds / 1000)

    executor._page = Page()

    # Hashable doubles deliberately expose no request content/credentials.
    class Request:
        resource_type = "fetch"

        def __init__(self, url):
            self.url = url

    old = Request("http://shop.fixture.test/stream")
    executor._on_request_started(old)
    since = time.monotonic()
    foreign = Request("http://analytics.fixture.test/events")
    executor._on_request_started(foreign)
    assert foreign not in executor._pending_app_requests
    assert await executor._wait_for_action_requests(since, timeout_ms=100)
    live = Request("http://shop.fixture.test/api/cart")
    executor._on_request_started(live)
    assert not await executor._wait_for_action_requests(since, timeout_ms=100)
    executor._on_request_settled(live)
    assert await executor._wait_for_action_requests(since, timeout_ms=100)
