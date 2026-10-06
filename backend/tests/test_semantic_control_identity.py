"""Declared DOM identity without mutable cart/form sibling text.

Real-browser fixtures are local only; no providers, credentials or external
application history are used. Ambiguous markers never approve other rows.
"""

from contextlib import asynccontextmanager

import pytest
from playwright.async_api import async_playwright

from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.domain.models import ExplorationOptions, Observation
from autojev.services.coverage import CoveragePlanner, semantic_element_fingerprint


@asynccontextmanager
async def observed_page(tmp_path, html):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.route("**/*", lambda route: route.fulfill(
                content_type="text/html", body=html))
            await page.goto("https://identity.fixture.test/cart")
            executor = PlaywrightExecutor(
                tmp_path, ExplorationOptions(capture_screenshots=False))
            executor._browser, executor._page, executor._context = (
                browser, page, page.context)
            executor._allowed_origin = executor._origin(page.url)
            yield executor, page
        finally:
            await browser.close()


def control(**changes):
    return {"ref": "e0", "tag": "button", "name": "Continue Shopping",
            "context": "main Cart Backpack $29.99 Remove Continue Shopping",
            "semantic_identity": 'control:["button","id","continue-shopping"]',
            **changes}


def test_declared_unique_control_ignores_mutable_context_and_refs():
    first = control()
    other = control(ref="e99", context="main Empty cart Continue Shopping")
    assert semantic_element_fingerprint(first) == semantic_element_fingerprint(other)


@pytest.mark.parametrize("change", [
    {"name": "Remove"}, {"href": "https://identity.fixture.test/other"},
    {"tag": "a"}, {"role": "link"}, {"type": "submit"},
    {"aria_haspopup": "menu"},
    {"semantic_identity": 'control:["button","id","other-shopping"]'},
])
def test_declared_identity_preserves_distinct_control_semantics(change):
    assert semantic_element_fingerprint(control()) != semantic_element_fingerprint(control(**change))


def test_unique_marker_still_disambiguates_homonymous_navigation_and_menu_controls():
    for metadata in ({"in_navigation": True, "navigation_owner": "nav#primary"},
                     {"name": "Open Menu", "overlay_group": ""}):
        first = control(**metadata)
        second = {**first, "semantic_identity": 'control:["button","id","secondary-control"]'}
        assert semantic_element_fingerprint(first) != semantic_element_fingerprint(second)


def test_unique_control_keeps_empty_and_populated_purchase_moves_distinct():
    element = control(name="Checkout")
    planner = CoveragePlanner("https://identity.fixture.test/cart")
    def observation(count):
        return Observation(planner.start_url, "Cart", "Cart", [element],
                           page_state={"cart": {"count": count}})
    empty = planner._candidate_map(observation(0))
    populated = planner._candidate_map(observation(2))
    assert set(empty).isdisjoint(populated)
    assert {item["_cart_journey"] for item in empty.values()} == {"empty"}
    assert {item["_cart_journey"] for item in populated.values()} == {"populated"}


def test_cart_inverse_dependency_keeps_structure_when_toggle_marker_changes():
    planner = CoveragePlanner("https://identity.fixture.test/catalog")
    def candidates(action):
        element = control(
            name="Add to cart" if action == "add" else "Remove",
            reversible_cart_action="remove" if action == "remove" else None,
            semantic_identity=f'control:["button","id","{action}-sku-a"]')
        observation = Observation(planner.start_url, "Catalog", "", [element],
                                  page_state={"products": [{
                                      "id": "sku-a", "name": "Desk Lamp",
                                      "cart_action": action, "action_refs": ["e0"],
                                  }]})
        return next(iter(planner._candidate_map(observation).values()))
    add, remove = candidates("add"), candidates("remove")
    assert add["_element_fingerprint"] != remove["_element_fingerprint"]
    assert add["_cart_entity"]["control_identity"] == remove["_cart_entity"]["control_identity"]


async def test_real_unique_control_marker_survives_cart_and_feedback_changes(tmp_path):
    html = '<main><p id="feedback">Cart Backpack Remove</p><button data-qa="continue-shopping">Continue Shopping</button></main>'
    async with observed_page(tmp_path, html) as (executor, page):
        before = await executor.observe()
        button = next(el for el in before.interactive_elements if el["name"] == "Continue Shopping")
        assert button["semantic_identity"] == 'control:["button","data-qa","continue-shopping"]'
        await page.locator("#feedback").evaluate("node => node.textContent='Error: cart is empty'")
        after = await executor.observe()
        changed = next(el for el in after.interactive_elements if el["name"] == "Continue Shopping")
        assert button["context"] != changed["context"]
        assert semantic_element_fingerprint(button) == semantic_element_fingerprint(changed)


@pytest.mark.parametrize("marker", ["id", "data-test", "data-testid", "data-qa"])
async def test_real_duplicate_control_markers_use_distinct_unique_entities(tmp_path, marker):
    html = f'''<main>
      <article data-product-id="sku-a"><h2>Desk Lamp</h2><button {marker}="toggle">Add to cart</button></article>
      <article data-product-id="sku-b"><h2>Desk Lamp</h2><button {marker}="toggle">Add to cart</button></article>
    </main>'''
    async with observed_page(tmp_path, html) as (executor, _page):
        observation = await executor.observe()
        buttons = [el for el in observation.interactive_elements if el["name"] == "Add to cart"]
        assert len(buttons) == 2
        assert all(el["semantic_identity"].startswith("owner:") for el in buttons)
        assert len({semantic_element_fingerprint(el) for el in buttons}) == 2
        assert len(CoveragePlanner(observation.url)._candidate_map(observation)) == 2


async def test_real_entity_link_identity_survives_toggle_and_row_reordering(tmp_path):
    html = '''<main>
      <article data-product-id="sku-a"><a href="/item?id=a">Desk Lamp</a><button>Add to cart</button></article>
      <article data-product-id="sku-b"><a href="/item?id=b">Desk Lamp</a><button>Add to cart</button></article>
    </main>'''
    async with observed_page(tmp_path, html) as (executor, page):
        before = await executor.observe()
        before_links = {el["href"]: el for el in before.interactive_elements if el["tag"] == "a"}
        await page.evaluate("""() => {
            const first=document.querySelector('article');
            first.querySelector('button').textContent='Remove';
            document.querySelector('main').append(first);
        }""")
        after = await executor.observe()
        after_links = {el["href"]: el for el in after.interactive_elements if el["tag"] == "a"}
        assert all(el["semantic_identity"].startswith("owner:") for el in before_links.values())
        for href, link in before_links.items():
            assert semantic_element_fingerprint(link) == semantic_element_fingerprint(after_links[href])
        assert len({semantic_element_fingerprint(el) for el in before_links.values()}) == 2
        removed = next(el for el in after.interactive_elements if el["name"] == "Remove")
        original = next(el for el in before.interactive_elements
                        if el["name"] == "Add to cart" and el["semantic_identity"] == removed["semantic_identity"])
        assert semantic_element_fingerprint(original) != semantic_element_fingerprint(removed)


async def test_real_duplicate_markers_and_ambiguous_owner_do_not_collapse_rows(tmp_path):
    html = '''<main id="orders">
      <article data-product-id="duplicate"><h2>Order Alice</h2><button data-test="details">Details</button></article>
      <article data-product-id="duplicate"><h2>Order Bob</h2><button data-test="details">Details</button></article>
    </main>'''
    async with observed_page(tmp_path, html) as (executor, _page):
        observation = await executor.observe()
        buttons = [el for el in observation.interactive_elements if el["name"] == "Details"]
        assert len(buttons) == 2
        assert all(el["semantic_identity"] is None for el in buttons)
        assert len({semantic_element_fingerprint(el) for el in buttons}) == 2
        assert len(CoveragePlanner(observation.url)._candidate_map(observation)) == 2


async def test_real_shared_control_id_does_not_transfer_coverage_to_another_entity(tmp_path):
    html = '<article data-item-id="order-a"><h2>Order</h2><button id="edit">Edit</button></article>'
    async with observed_page(tmp_path, html) as (executor, page):
        before = (await executor.observe()).interactive_elements[0]
        await page.locator("article").evaluate("node => node.setAttribute('data-item-id','order-b')")
        after = (await executor.observe()).interactive_elements[0]
        assert before["context"] == after["context"]
        assert before["semantic_identity"] != after["semantic_identity"]
        assert semantic_element_fingerprint(before) != semantic_element_fingerprint(after)


async def test_real_unique_control_id_cannot_ignore_an_ambiguous_declared_entity(tmp_path):
    html = '''<main id="orders">
      <article data-item-id="duplicate"><h2>Order Alice</h2><button id="edit-alice">Edit</button></article>
      <article data-item-id="duplicate"><h2>Order Bob</h2><button id="edit-bob">Edit</button></article>
    </main>'''
    async with observed_page(tmp_path, html) as (executor, _page):
        buttons = (await executor.observe()).interactive_elements
        assert len(buttons) == 2
        assert all(el["semantic_identity"] is None for el in buttons)
        assert len({semantic_element_fingerprint(el) for el in buttons}) == 2


async def test_real_unmarked_global_anchor_identity_survives_changing_review_cart(tmp_path):
    html = '<main><p>Review cart empty</p><a href="/cart">Cancel</a></main>'
    async with observed_page(tmp_path, html) as (executor, page):
        before = (await executor.observe()).interactive_elements[0]
        assert before["semantic_identity"] == "navigation:unique-declared-href"
        await page.locator("p").evaluate("node => node.textContent='Review cart Backpack $29.99'")
        after = (await executor.observe()).interactive_elements[0]
        assert before["context"] != after["context"]
        assert semantic_element_fingerprint(before) == semantic_element_fingerprint(after)


@pytest.mark.parametrize("fragment", [
    '<main><a href="#">Cancel</a></main>',
    '<main><a href="#local-tab">Cancel</a></main>',
    '<main><a href="https://external.fixture.test/cart">Cancel</a></main>',
    '<main><a href="/cart" onclick="event.preventDefault()">Cancel</a></main>',
    '<main><a href="/cart" download>Cancel</a></main>',
    '<main><div><a href="/cart">Cancel</a></div></main>',
    '<main><article><a href="/cart">Cancel</a></article></main>',
    '<main><form><a href="/cart">Cancel</a></form></main>',
    '<main><div role="dialog"><a href="/cart">Cancel</a></div></main>',
    '<main><ul><li><a href="/cart">Cancel</a></li></ul></main>',
])
async def test_real_unmarked_anchor_identity_does_not_generalize_to_ambiguous_scopes(tmp_path, fragment):
    async with observed_page(tmp_path, fragment) as (executor, _page):
        anchors = [el for el in (await executor.observe()).interactive_elements if el["tag"] == "a"]
        assert len(anchors) == 1
        assert anchors[0]["semantic_identity"] is None


@pytest.mark.parametrize("parent", ['<main>', '<main id="scope">'])
async def test_real_hidden_duplicate_anchor_prevents_global_or_owner_uniqueness(tmp_path, parent):
    html = parent + '<a href="/cart">Cancel</a><a href="/cart" style="display:none">Cancel</a></main>'
    async with observed_page(tmp_path, html) as (executor, _page):
        anchors = [el for el in (await executor.observe()).interactive_elements if el["tag"] == "a"]
        assert len(anchors) == 1
        assert anchors[0]["semantic_identity"] is None


async def test_real_same_label_different_global_destination_remains_separate(tmp_path):
    html = '<main><a href="/cart">Cancel</a><a href="/orders">Cancel</a></main>'
    async with observed_page(tmp_path, html) as (executor, _page):
        observation = await executor.observe()
        anchors = [el for el in observation.interactive_elements if el["tag"] == "a"]
        assert len(anchors) == 2
        assert all(el["semantic_identity"] == "navigation:unique-declared-href" for el in anchors)
        assert len({semantic_element_fingerprint(el) for el in anchors}) == 2
        assert len(CoveragePlanner(observation.url)._candidate_map(observation)) == 2


async def test_real_navigation_identity_does_not_copy_url_credentials_into_metadata(tmp_path):
    html = '<main><a href="/cart?token=fixture-private-token">Cancel</a></main>'
    async with observed_page(tmp_path, html) as (executor, _page):
        anchor = (await executor.observe()).interactive_elements[0]
        assert anchor["semantic_identity"] == "navigation:unique-declared-href"
        assert "fixture-private-token" not in str(anchor)
