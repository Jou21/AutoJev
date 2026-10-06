"""Deterministic business assertions grounded in sanitized browser state.

A dispatched click, changed URL or changed DOM is not by itself a business
outcome. Checks below require a declared control intent and concrete facts from
the before/after observations. Missing semantics produce no invented PASS.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any

from autojev.domain.models import (
    ActionResult,
    AgentDecision,
    AssertionResult,
    Observation,
)
from autojev.domain.product_identity import (
    DECLARED_ID_SOURCES, comparable_composition_keys, declared_product_id, normalized_product_name,
    product_comparison_key, same_observed_product,
)


def _assert(
    kind: str, expected: Any, actual: Any, passed: bool, message: str
) -> AssertionResult:
    return AssertionResult(
        kind=kind, expected=expected, actual=actual, passed=passed, message=message
    )


def _items(state: dict[str, Any], key: str) -> list[dict[str, Any]]:
    values = state.get(key, [])
    return (
        [item for item in values if isinstance(item, dict)]
        if isinstance(values, list)
        else []
    )


def _normal(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return " ".join(
        "".join(char for char in text if not unicodedata.combining(char)).split()
    )


def _identity(item: dict[str, Any]) -> str:
    return str(item.get("identity") or item.get("id") or item.get("name") or "")


def _value(item: dict[str, Any]) -> Any:
    # Opaque per-run HMACs are sufficient to detect a field overwriting another.
    return item.get("value_token") if "value_token" in item else item.get("value")


def _same_product(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    return same_observed_product(expected, actual)


def _price(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^\d,.-]", "", str(value or ""))
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _money(value: Any) -> Decimal | None:
    parsed = _price(value)
    if parsed is None:
        return None
    try:
        amount = Decimal(str(parsed))
        return amount if amount.is_finite() else None
    except InvalidOperation:
        return None


def _quantity(item: dict[str, Any]) -> int | None:
    try:
        value = Decimal(str(item.get("quantity", 1)))
        return int(value) if value.is_finite() and value > 0 and value == int(value) else None
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None


def _product_key(item: dict[str, Any]) -> str:
    return product_comparison_key(item)


def _composition(items: list[dict[str, Any]], keys: list[str] | None = None) -> Counter[str] | None:
    contents: Counter[str] = Counter()
    for index, item in enumerate(items):
        identity, quantity = keys[index] if keys is not None else _product_key(item), _quantity(item)
        if not identity or quantity is None:
            return None
        contents[identity] += quantity
    return contents


def _paired_composition(expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> tuple[Counter[str] | None, Counter[str] | None]:
    keys = comparable_composition_keys(expected, actual)
    return (_composition(expected, keys[0]), _composition(actual, keys[1])) if keys is not None else (None, None)


def _cart_expectation(before: Observation, after: Observation) -> dict[str, Any]:
    expectation = after.page_state.get("cart_expectation")
    if isinstance(expectation, dict) and (expectation.get("items_known") or expectation.get("pending_membership")):
        return expectation
    cart = before.page_state.get("cart") or {}
    if cart.get("items_known"):
        return {"items_known": True, "items": _items(cart, "items"), "source": "prior_observation"}
    products = _items(before.page_state, "products")
    selected = [item for item in products if item.get("cart_action") == "remove"]
    if products and isinstance(cart.get("count"), int) and len(selected) == cart["count"]:
        return {"items_known": True, "items": selected, "source": "product_controls"}
    return {}


def _cart_contents_checks(
    expected: dict[str, Any], actual_items: list[dict[str, Any]], *, checkout: bool = False,
) -> list[AssertionResult]:
    checks: list[AssertionResult] = []
    if expected.get("items_known"):
        expected_items = _items(expected, "items")
        wanted, actual = _paired_composition(expected_items, actual_items)
        if wanted is None or actual is None:
            # Without a comparable composition, a shared name also cannot
            # establish which homonymous SKU preserved its advertised price.
            return checks
        if wanted is not None and actual is not None:
            kind = "checkout_items_match_cart" if checkout else "cart_persistence" if expected.get("revision", 0) else "cart_contents_match"
            checks.append(_assert(
                kind, dict(wanted), dict(actual), wanted == actual,
                "The displayed items and quantities must match the cart established by prior product actions.",
            ))
        price_mismatches, compared = [], []
        for wanted_item in expected_items:
            matches = [item for item in actual_items if _same_product(wanted_item, item)]
            wanted_price = _money(wanted_item.get("price"))
            if len(matches) != 1 or wanted_price is None:
                continue
            actual_price = _money(matches[0].get("price"))
            if actual_price is None:
                continue
            compared.append(str(wanted_item.get("name") or wanted_item.get("id")))
            if abs(wanted_price - actual_price) >= Decimal("0.005"):
                price_mismatches.append({
                    "product": compared[-1], "expected": str(wanted_price), "actual": str(actual_price),
                })
        if compared:
            checks.append(_assert(
                "checkout_item_prices" if checkout else "cart_item_prices",
                {"products": compared, "price_mismatches": []}, {"price_mismatches": price_mismatches},
                not price_mismatches,
                "Advertised unit prices must be preserved for the same product in the cart/review.",
            ))
    else:
        pending = _items(expected, "pending_membership")
        keys = comparable_composition_keys(pending, actual_items)
        if keys is None:
            return checks
        pending_keys, actual_keys = keys
        for index, item in enumerate(pending):
            present = pending_keys[index] in actual_keys
            checks.append(_assert(
                "cart_membership", {"product": item.get("name"), "present": item.get("present")},
                {"present": present}, present == item.get("present"),
                "The previously requested cart action must be reflected when the cart is opened.",
            ))
    return checks


def _cart_navigation_checks(before: Observation, decision: AgentDecision, after: Observation) -> list[AssertionResult]:
    cart_before, cart_after = before.page_state.get("cart") or {}, after.page_state.get("cart") or {}
    opening_cart = decision.target in (cart_before.get("link_refs") or [])
    revisiting = decision.action in {"navigate", "go_back", "reload"}
    introduced_items = not cart_before.get("items_known")
    prior_checkout = before.page_state.get("checkout") or {}
    control = next((item for item in before.interactive_elements
                    if item.get("ref") == decision.target), {})
    cancelling_review = (decision.action == "click" and prior_checkout.get("stage") == "review"
                         and re.fullmatch(r"cancel|cancelar", _normal(control.get("name"))) is not None)
    cancelling_form = (decision.action == "click"
                       and re.fullmatch(r"cancel|cancelar", _normal(control.get("name"))) is not None
                       and prior_checkout.get("stage") not in {"review", "complete"}
                       and "checkout" in _normal(f"{before.url} {before.title}")
                       and any(form.get("submit_refs") for form in _items(before.page_state, "forms"))
                       and any(field.get("form") and not field.get("sensitive")
                               and field.get("type") != "password"
                               for field in _items(before.page_state, "fields")))
    if cancelling_review or cancelling_form:
        # The review already exposes a known basket. Returning to another
        # known basket is not merely a URL delta: Cancel must preserve the
        # previously established cart, including quantities and unit prices.
        expected = _cart_expectation(before, before)
        if not expected.get("items_known") and prior_checkout.get("items_known"):
            expected = {"items_known": True, "items": _items(prior_checkout, "items"),
                        "source": "observed_checkout_review"}
        if expected.get("items_known"):
            expected = {**expected, "revision": expected.get("revision") or 1}
        if cart_after.get("items_known") and (after.page_state.get("checkout") or {}).get("stage") != "review":
            return _cart_contents_checks(expected, _items(cart_after, "items"))
        # Some checkouts cancel back to the catalog, not the cart. Verify its
        # actual badge/selection facts without pretending that catalog controls
        # are an opened cart. A later real cart entry still checks persistence.
        checks: list[AssertionResult] = []
        expected_items = _items(expected, "items") if expected.get("items_known") else None
        count = cart_after.get("count")
        prior_count = cart_before.get("count")
        if isinstance(prior_count, int) and not isinstance(prior_count, bool) and isinstance(count, int) and not isinstance(count, bool):
            checks.append(_assert(
                "checkout_cancel_cart_count", prior_count, count, prior_count == count,
                "Cancelling checkout must preserve the observed cart count.",
            ))
        products = _items(after.page_state, "products")
        if expected_items is not None and products and all(item.get("cart_action") in {"add", "remove"} for item in products):
            wanted, selected = _paired_composition(expected_items, [item for item in products if item.get("cart_action") == "remove"])
            if wanted is not None and selected is not None:
                checks.append(_assert(
                    "checkout_cancel_selected_products", sorted(wanted), sorted(selected), set(wanted) == set(selected),
                    "The catalog's observed product selection must be retained after Cancel; this is not an opened-cart contents check.",
                ))
        return checks
    if not cart_after.get("items_known") or (after.page_state.get("checkout") or {}).get("stage") == "review":
        return []
    if not (opening_cart or revisiting or introduced_items):
        return []
    return _cart_contents_checks(_cart_expectation(before, after), _items(cart_after, "items"))


def _checkout_checks(before: Observation, decision: AgentDecision, after: Observation) -> list[AssertionResult]:
    prior, current = before.page_state.get("checkout") or {}, after.page_state.get("checkout") or {}
    finishing = prior.get("stage") == "review" and decision.target in (prior.get("finish_refs") or [])
    review = current if current.get("stage") == "review" else prior if finishing else {}
    checks: list[AssertionResult] = []
    control = next((item for item in before.interactive_elements
                    if item.get("ref") == decision.target), {})
    if prior.get("stage") == "review" and decision.action == "click" and re.fullmatch(
        r"cancel|cancelar", _normal(control.get("name")),
    ):
        exited = current.get("stage") not in {"review", "complete"} and (
            before.url != after.url or (after.page_state.get("cart") or {}).get("items_known") is True
        )
        checks.append(_assert(
            "checkout_cancel_progress", "Review exited without confirming an order",
            {"stage": current.get("stage"), "route_changed": before.url != after.url}, exited,
            "Cancel must leave the review without placing the order; a dispatched click alone is not approval.",
        ))
    elif (decision.action == "click" and re.fullmatch(r"cancel|cancelar", _normal(control.get("name")))
          and prior.get("stage") not in {"review", "complete"}
          and "checkout" in _normal(f"{before.url} {before.title}")
          and any(form.get("submit_refs") for form in _items(before.page_state, "forms"))
          and any(field.get("form") and not field.get("sensitive") and field.get("type") != "password"
                  for field in _items(before.page_state, "fields"))):
        exited = before.url != after.url and current.get("stage") != "complete"
        checks.append(_assert(
            "checkout_form_cancel_progress", "Information form exited without placing an order",
            {"route_changed": before.url != after.url, "stage": current.get("stage")}, exited,
            "Cancel must leave the observed checkout information form without confirming an order.",
        ))
    if review and (finishing or prior.get("stage") != "review" or before.page_state.get("checkout") != after.page_state.get("checkout")):
        items = _items(review, "items")
        if review.get("items_known"):
            # Completion may clear the live cart expectation. The review must
            # still be checked against the pre-submit expectation.
            expected = _cart_expectation(before, before if finishing else after)
            checks.extend(_cart_contents_checks(expected, items, checkout=True))
            parts = [(_money(item.get("price")), _quantity(item)) for item in items]
            subtotal = _money(review.get("subtotal"))
            if subtotal is not None and all(price is not None and quantity is not None for price, quantity in parts):
                calculated = sum((price * quantity for price, quantity in parts), Decimal("0"))
                checks.append(_assert(
                    "checkout_subtotal", str(calculated), str(subtotal), abs(calculated - subtotal) < Decimal("0.005"),
                    "The declared item subtotal must equal the sum of observed unit prices multiplied by quantities.",
                ))
        subtotal, tax, total = (_money(review.get(key)) for key in ("subtotal", "tax", "total"))
        shipping = _money(review.get("shipping")) if review.get("shipping") is not None else Decimal("0")
        discount = _money(review.get("discount")) if review.get("discount") is not None else Decimal("0")
        # Never invent a tax rate or hidden commercial rule. Validate only the
        # explicit arithmetic components that this review declares.
        if all(value is not None for value in (subtotal, tax, total, shipping, discount)) and review.get("arithmetic_scope") == "declared_components_only":
            calculated = subtotal + tax + shipping - discount
            checks.append(_assert(
                "checkout_total", str(calculated), str(total), abs(calculated - total) < Decimal("0.005"),
                "The declared total must equal subtotal + displayed tax + shipping - discount; no tax-rate correctness is inferred.",
            ))
    if finishing:
        checks.append(_assert(
            "order_confirmation", {"confirmed": True},
            {"stage": current.get("stage"), "confirmed": current.get("confirmed") is True, "confirmation_text": current.get("confirmation_text")},
            current.get("stage") == "complete" and current.get("confirmed") is True,
            "The finish action must expose an explicit successful order confirmation, not merely a changed URL.",
        ))
    return checks


def _fill_checks(
    before: Observation,
    decision: AgentDecision,
    result: ActionResult,
    after: Observation,
) -> list[AssertionResult]:
    fields = _items(before.page_state, "fields")
    target = next((item for item in fields if item.get("ref") == decision.target), None)
    if not target or target.get("sensitive") or decision.credential_key:
        return []
    identity = _identity(target)
    updated_fields = {
        _identity(item): item for item in _items(after.page_state, "fields")
    }
    updated = updated_fields.get(identity)
    evidence = result.evidence.get("business_input") or {}
    expected = (
        evidence.get("expected_token")
        if evidence.get("identity") == identity
        else decision.value
    )
    actual = _value(updated) if updated else None
    # For mocks and saved observations without executor tokens, value is usable.
    if expected is None or (
        "value_token" in target and not evidence.get("expected_token")
    ):
        return []
    checks = [
        _assert(
            "field_value_matches",
            {"field": target.get("label") or identity, "value_matches": True},
            {
                "field_present": updated is not None,
                "value_matches": updated is not None and actual == expected,
            },
            updated is not None and actual == expected,
            "The filled field must retain the requested value.",
        )
    ]
    unchanged: list[str] = []
    changed: list[str] = []
    for item in fields:
        other_id = _identity(item)
        if (
            other_id == identity
            or item.get("sensitive")
            or item.get("read_only")
            or item.get("type")
            in {"number", "select-one", "select-multiple", "checkbox", "radio"}
            or item.get("form") != target.get("form")
        ):
            continue
        other = updated_fields.get(other_id)
        if other is None or _value(item) is None or _value(other) is None:
            continue
        label = str(item.get("label") or other_id)
        unchanged.append(label)
        if _value(item) != _value(other):
            changed.append(label)
    if unchanged:
        checks.append(
            _assert(
                "other_fields_unchanged",
                {"fields": unchanged, "changed": []},
                {"changed": changed},
                not changed,
                "Filling one field must preserve the values of other fields in the same form.",
            )
        )
    return checks


def _sort_intent(option_label: str) -> tuple[str, bool] | None:
    label = _normal(option_label)
    if any(word in label for word in ("price", "preco")):
        if re.search(r"low.*high|menor.*maior|\bascending\b|\bcrescente\b", label):
            return "price", False
        if re.search(r"high.*low|maior.*menor|\bdescending\b|\bdecrescente\b", label):
            return "price", True
    if any(word in label for word in ("name", "nome", "alphabet", "alfabet")):
        if re.search(r"a\s*(?:to|a|ate|-|→)\s*z|\bascending\b|\bcrescente\b", label):
            return "name", False
        if re.search(r"z\s*(?:to|a|ate|-|→)\s*a|\bdescending\b|\bdecrescente\b", label):
            return "name", True
    return None


def _select_checks(
    before: Observation,
    decision: AgentDecision,
    result: ActionResult,
    after: Observation,
) -> list[AssertionResult]:
    target = next(
        (
            item
            for item in before.interactive_elements
            if item.get("ref") == decision.target
        ),
        None,
    )
    field = next(
        (
            item
            for item in _items(before.page_state, "fields")
            if item.get("ref") == decision.target
        ),
        None,
    )
    if not target or not field or field.get("sensitive") or decision.value is None:
        return []
    updated = next(
        (
            item
            for item in _items(after.page_state, "fields")
            if _identity(item) == _identity(field)
        ),
        None,
    )
    selected = updated.get("selected_values") if updated else None
    checks: list[AssertionResult] = []
    token_evidence = result.evidence.get("business_input") or {}
    if token_evidence.get("identity") == _identity(field) and token_evidence.get(
        "expected_token"
    ):
        matches = (
            updated is not None and _value(updated) == token_evidence["expected_token"]
        )
        checks.append(
            _assert(
                "selection_matches",
                {"option_matches": True},
                {"option_matches": matches},
                matches,
                "The selected option must match the requested value.",
            )
        )
    elif "value_token" not in field and isinstance(selected, list):
        checks.append(
            _assert(
                "selection_matches",
                [decision.value],
                selected,
                decision.value in selected,
                "The selected option must match the requested value.",
            )
        )
    option = next(
        (
            item
            for item in target.get("options") or []
            if item.get("value") == decision.value
        ),
        {},
    )
    intent = _sort_intent(str(option.get("label") or ""))
    products = _items(before.page_state, "products")
    if not intent or len(products) < 2:
        return checks
    key, reverse = intent
    values = [
        _price(item.get("price")) if key == "price" else _normal(item.get("name"))
        for item in products
    ]
    if any(value is None or value == "" for value in values):
        return checks
    ordered = [
        item
        for _, item in sorted(
            zip(values, products), key=lambda pair: pair[0], reverse=reverse
        )
    ]
    expected = [_identity(item) for item in ordered]
    actual = [_identity(item) for item in _items(after.page_state, "products")]
    # Equal keys are interchangeable; require original membership and monotonic
    # order instead of imposing an arbitrary tie order.
    after_products = _items(after.page_state, "products")
    after_values = [
        _price(item.get("price")) if key == "price" else _normal(item.get("name"))
        for item in after_products
    ]
    valid_values = all(value is not None and value != "" for value in after_values)
    passed = (
        sorted(expected) == sorted(actual)
        and valid_values
        and after_values == sorted(after_values, reverse=reverse)
    )
    checks.append(
        _assert(
            "product_sort_order",
            {"by": key, "descending": reverse, "products": expected},
            {"products": actual, "values": after_values},
            passed,
            "The visible products must preserve membership and follow the declared sort order.",
        )
    )
    return checks


def _product_checks(
    before: Observation, decision: AgentDecision, after: Observation
) -> list[AssertionResult]:
    products = _items(before.page_state, "products")
    detail = before.page_state.get("product_detail")
    cart_before = before.page_state.get("cart") or {}
    candidates = [*products, *_items(cart_before, "items")]
    # Structural DOM identities are not product identities. In particular a
    # shared header must never turn its global Cart link into a product link.
    if isinstance(detail, dict) and detail.get("name"):
        candidates.append(detail)
    expected_product = next(
        (
            item
            for item in candidates
            if decision.target in (item.get("link_refs") or [])
        ),
        None,
    )
    checks: list[AssertionResult] = []
    if expected_product and (
        expected_product.get("name") or expected_product.get("id")
    ):
        actual_detail = after.page_state.get("product_detail")
        actual = actual_detail if isinstance(actual_detail, dict) else {}
        # A detail that exposes no comparable ID can bridge a unique catalog
        # name. It cannot prove which of two declared homonymous SKUs opened.
        comparable_ids = declared_product_id(expected_product) is not None and declared_product_id(actual) is not None
        name = normalized_product_name(expected_product.get("name"))
        ambiguous_name = not comparable_ids and sum(
            normalized_product_name(item.get("name")) == name for item in candidates
        ) > 1
        if not ambiguous_name:
            checks.append(
                _assert(
                    "product_detail_identity",
                    {
                        "name": expected_product.get("name"),
                        "id": expected_product.get("id"),
                    },
                    {"name": actual.get("name"), "id": actual.get("id")},
                    bool(actual) and _same_product(expected_product, actual),
                    "The opened product detail must belong to the selected product.",
                )
            )
        expected_price, actual_price = (
            _price(expected_product.get("price")),
            _price(actual.get("price")),
        )
        if actual and not ambiguous_name and expected_price is not None and actual_price is not None:
            checks.append(
                _assert(
                    "product_detail_price",
                    expected_price,
                    actual_price,
                    abs(expected_price - actual_price) < 0.005,
                    "The selected product must preserve its displayed price in the detail.",
                )
            )
        if not ambiguous_name and actual.get("image_src") and actual.get("image_complete") is True:
            checks.append(
                _assert(
                    "product_image_loaded",
                    {"loaded": True},
                    {"loaded": actual.get("image_loaded")},
                    actual.get("image_loaded") is True,
                    "The displayed product image must finish loading successfully.",
                )
            )
    product = next(
        (
            item
            for item in candidates
            if decision.target in (item.get("action_refs") or [])
        ),
        None,
    )
    if not product or product.get("cart_action") not in {"add", "remove"}:
        return checks
    adding = product["cart_action"] == "add"
    delta = 1 if adding else -1
    cart_after = after.page_state.get("cart") or {}
    before_count, after_count = cart_before.get("count"), cart_after.get("count")
    if (
        isinstance(before_count, int)
        and isinstance(after_count, int)
        and before_count + delta >= 0
    ):
        checks.append(
            _assert(
                "cart_count",
                before_count + delta,
                after_count,
                after_count == before_count + delta,
                "The cart count must reflect the selected product action.",
            )
        )
    post_candidates = [
        *_items(after.page_state, "products"),
        *_items(cart_after, "items"),
    ]
    post_detail = after.page_state.get("product_detail")
    if isinstance(post_detail, dict):
        post_candidates.append(post_detail)
    matching = [item for item in post_candidates if _same_product(product, item)]
    updated = matching[0] if len(matching) == 1 else None
    if len(matching) > 1:
        # Historical observations can contain a detail twice (products and
        # product_detail). Collapse only an exact alias of one declared entity
        # and one physical control, never homonyms or independent buttons.
        first = matching[0]
        entity_id, source, refs = declared_product_id(first), first.get("identity_source"), first.get("action_refs")
        if (entity_id is not None and source in DECLARED_ID_SOURCES
                and isinstance(refs, list) and len(refs) == 1 and isinstance(refs[0], str) and refs[0]
                and all(declared_product_id(item) == entity_id
                        and item.get("identity_source") == source
                        and item.get("action_refs") == refs
                        and item.get("cart_action") == first.get("cart_action")
                        for item in matching)):
            updated = first
    expected_action = "remove" if adding else "add"
    if updated and updated.get("cart_action") is not None:
        checks.append(
            _assert(
                "cart_control_state",
                expected_action,
                updated.get("cart_action"),
                updated.get("cart_action") == expected_action,
                "The selected product control must reflect its cart membership.",
            )
        )
    if cart_after.get("items_known"):
        cart_items = _items(cart_after, "items")
        keys = comparable_composition_keys([product], cart_items)
        if keys is None:
            return checks
        product_keys, cart_keys = keys
        present = product_keys[0] in cart_keys
        checks.append(
            _assert(
                "cart_membership",
                {"product": product.get("name"), "present": adding},
                {"present": present},
                present == adding,
                "The selected product must have the expected cart membership.",
            )
        )
    return checks


def _validation_messages(value: Any) -> list[str]:
    return [str(item) for item in value if item] if isinstance(value, list) else []


def _validation_names_field(message: str, field: dict[str, Any]) -> bool:
    """Use observed field names/markers, never infer a label from page copy.

    Compact matching permits `Last Name` / `last-name`, but very short or
    generic aliases cannot attribute a multi-empty submission to one field.
    """
    aliases = [field.get("label"), field.get("name"), field.get("id")]
    marker = re.search(r"(?:id|name)=([^:]+)$", _identity(field).rsplit("::", 1)[-1])
    if marker:
        aliases.append(marker.group(1))
    copy = re.sub(r"[^a-z0-9]", "", _normal(message))
    return any(
        len(alias) >= 4 and alias not in {"name", "nome", "code", "codigo", "field", "input"}
        and alias in copy
        for value in aliases if value
        for alias in [re.sub(r"[^a-z0-9]", "", _normal(value))]
    )


def _interrupted_form_destination(after: Observation) -> str | None:
    """Recognize explicit authentication/error landings, not arbitrary URLs."""
    controls = [
        *_items(after.page_state, "fields"),
        *after.interactive_elements,
    ]
    surface = _normal(
        f"{after.title} {after.text} {after.url} "
        + " ".join(
            str(item.get("name") or item.get("label") or "") for item in controls
        )
    )
    has_password = any(_normal(item.get("type")) == "password" for item in controls)
    if has_password and re.search(
        r"\blog\s*in\b|\bsign[\s-]*in\b|\bentrar\b|\bacessar\b", surface
    ):
        return "authentication_required"
    title = _normal(after.title)
    if re.fullmatch(
        r"(?:(?:401|403|404|500|502|503)\s*[-:–—]?\s*)?"
        r"(?:error|unauthorized|access denied|forbidden|page not found|not found|"
        r"internal server error|service unavailable|session expired|something went wrong|"
        r"erro|acesso negado|pagina nao encontrada|erro interno do servidor|sessao expirada)"
        r"[.!]?",
        title,
    ):
        return "error_page"
    return None


def _form_checks(
    before: Observation, decision: AgentDecision, after: Observation
) -> list[AssertionResult]:
    control = next((item for item in before.interactive_elements if item.get("ref") == decision.target), {})
    if re.match(r"^(cancel|cancelar|voltar|back|close|fechar|retornar)\b", _normal(control.get("name"))):
        # Also guard legacy/stale form metadata that mislabels a default-type
        # cancellation button as a submit control.
        return []
    form = next(
        (
            item
            for item in _items(before.page_state, "forms")
            if decision.target in (item.get("submit_refs") or [])
        ),
        None,
    )
    target_field = next(
        (
            item
            for item in _items(before.page_state, "fields")
            if item.get("ref") == decision.target
        ),
        None,
    )
    if (
        decision.action == "press"
        and "enter" in (decision.key or "Enter").casefold()
        and target_field
    ):
        form = next(
            (
                item
                for item in _items(before.page_state, "forms")
                if item.get("id") == target_field.get("form")
            ),
            None,
        )
    if not form:
        return []
    fields = [
        item
        for item in _items(before.page_state, "fields")
        if item.get("form") == form.get("id") and item.get("enabled", True)
    ]
    if any(item.get("sensitive") for item in fields):
        return []
    required = [item for item in fields if item.get("required")]
    if not required:
        return []
    missing = [item for item in required if not item.get("has_value")]
    post_fields = {_identity(item): item for item in _items(after.page_state, "fields")}
    still_present = any(_identity(item) in post_fields for item in fields)
    post_form = next(
        (
            item
            for item in _items(after.page_state, "forms")
            if item.get("id") == form.get("id")
        ),
        None,
    )
    if post_form is not None and "validation_errors" in post_form:
        # Current executor metadata scopes feedback to the submitted form.
        # A newsletter/sidebar alert cannot approve or fail checkout.
        errors = _validation_messages(post_form["validation_errors"])
    elif post_form is None or "validation_errors" not in form:
        # Legacy observations, or a form removed by its response, lack live
        # ownership metadata. Only newly appearing, otherwise-unowned feedback
        # is eligible; an existing unrelated alert is not an action outcome.
        previous_errors = {
            _normal(value)
            for value in _validation_messages(
                before.page_state.get("validation_errors")
            )
        }
        other_form_errors = {
            _normal(value)
            for item in _items(after.page_state, "forms")
            if item.get("id") != form.get("id")
            for value in _validation_messages(item.get("validation_errors"))
        }
        errors = [
            value
            for value in _validation_messages(after.page_state.get("validation_errors"))
            if _normal(value) not in previous_errors | other_form_errors
        ]
    else:
        errors = []
    probe = (decision.scenario_hint or {}).get("validation_probe") or {}
    if probe.get("kind") == "required_field":
        target_identity = str(probe.get("field_identity") or "")
        target = next((item for item in required if _identity(item) == target_identity), None)
        peers_missing = [item for item in missing if _identity(item) != target_identity]
        native_target = post_fields.get(target_identity, {})
        native_message = str(native_target.get("validation_message") or "")
        attributable = bool(
            target and not target.get("has_value") and (
                native_target.get("valid") is False and native_message
                or any(_validation_names_field(message, target) for message in errors)
                or not peers_missing and errors
            )
        )
        prepared = bool(target and not target.get("has_value"))
        # A broken peer can mask later validation. Actually dispatch the
        # bounded probe, then retain its evidence as BLOCKED, not a guessed
        # isolated-field PASS or an invented validation defect.
        masked = not prepared or bool(peers_missing and still_present and not attributable)
        precondition = _assert(
            "form_validation_probe_precondition",
            {"target_field": probe.get("name") or target_identity,
             "target_empty": True, "isolated_or_attributable_feedback": True},
            {"target_empty": prepared, "isolation_established": prepared and not peers_missing,
             "target_feedback_attributed": attributable,
             "blocking_fields": [item.get("label") or _identity(item) for item in peers_missing],
             "validation_errors": errors, "status": "masked" if masked else "observable"},
            not masked,
            "A required-field probe needs an empty target and populated peers, or feedback attributable to that specific field.",
        )
        if masked:
            return [precondition]
        return [precondition, _assert(
            "form_required_validation",
            {"missing_fields": [target.get("label") or target_identity], "validation_required": True,
             "target_field": probe.get("name") or target_identity},
            {"form_retained": still_present, "validation_errors": [*errors, *([native_message] if native_message else [])],
             "target_feedback_attributed": attributable},
            still_present and attributable,
            "The application must reject this empty required field with attributable validation, without advancing the form.",
        )]
    if missing:
        native_errors = [
            str(post_fields.get(_identity(item), {}).get("validation_message") or "")
            for item in missing
            if post_fields.get(_identity(item), {}).get("valid") is False
        ]
        observable_errors = [str(value) for value in [*errors, *native_errors] if value]
        passed = still_present and bool(observable_errors)
        return [
            _assert(
                "form_required_validation",
                {
                    "missing_fields": [
                        item.get("label") or _identity(item) for item in missing
                    ],
                    "validation_required": True,
                },
                {
                    "form_retained": still_present,
                    "validation_errors": observable_errors,
                },
                passed,
                "Submitting missing required fields must retain the form and expose validation.",
            )
        ]
    target = next(
        (
            item
            for item in before.interactive_elements
            if item.get("ref") == decision.target
        ),
        {},
    )
    advance = bool(
        re.search(
            r"\bcontinue\b|\bcontinuar\b|\bnext\b|\bproximo\b|\bproceed\b|\bavancar\b",
            _normal(target.get("name")),
        )
    )
    if not advance:
        return []
    interruption = _interrupted_form_destination(after)
    advancement_observed = (
        before.url != after.url
        or _normal(before.title) != _normal(after.title)
        or _normal(before.text) != _normal(after.text)
        or bool(set(post_fields) - {_identity(item) for item in fields})
    )
    passed = (
        not still_present
        and not errors
        and advancement_observed
        and interruption is None
    )
    return [
        _assert(
            "form_progress",
            {"required_fields_filled": True, "advanced": True},
            {
                "form_retained": still_present,
                "validation_errors": errors,
                "advancement_observed": advancement_observed,
                "interrupted_destination": interruption,
            },
            passed,
            "A completed required step must advance when its continue control is used.",
        )
    ]


def _validation_dismiss_checks(
    before: Observation, decision: AgentDecision, after: Observation,
) -> list[AssertionResult]:
    control = next((item for item in before.interactive_elements
                    if item.get("ref") == decision.target), {})
    if control.get("validation_dismiss") is not True:
        return []
    form_id = control.get("validation_form")
    prior = next((form for form in _items(before.page_state, "forms")
                  if form.get("id") == form_id), None)
    if not prior or not prior.get("validation_errors"):
        return []  # No observed feedback means no invented verification.
    current = next((form for form in _items(after.page_state, "forms")
                    if form.get("id") == form_id), None)
    errors = current.get("validation_errors") if current else None
    retained = before.url == after.url and current is not None
    checks = [_assert(
        "form_validation_feedback_dismissed", {"form_retained": True, "validation_errors": []},
        {"form_retained": retained, "validation_errors": errors}, retained and errors == [],
        "Dismissing inline validation feedback must clear the observed error without leaving the form.",
    )]
    old_fields = {str(field.get("identity")): field for field in _items(before.page_state, "fields")
                  if field.get("form") == form_id}
    new_fields = {str(field.get("identity")): field for field in _items(after.page_state, "fields")
                  if field.get("form") == form_id}
    # Compare opaque observed tokens, not the presence of text or raw secrets.
    if old_fields and all("value_token" in field or "value" in field for field in old_fields.values()):
        unchanged = retained and old_fields.keys() == new_fields.keys() and all(
            _value(field) == _value(new_fields[identity]) for identity, field in old_fields.items()
        )
        checks.append(_assert(
            "form_fields_preserved_on_dismiss", "All observed field values preserved", unchanged, unchanged,
            "Closing a validation message must not clear or overwrite the form's input values.",
        ))
    return checks


def check_business_outcomes(
    before: Observation,
    decision: AgentDecision,
    result: ActionResult,
    after: Observation,
) -> list[AssertionResult]:
    """Return authoritative checks only for successfully dispatched safe actions."""
    if (
        not result.success
        or (result.evidence.get("business_settle") or {}).get("status")
        == "api_wait_timed_out"
    ):
        return []
    if decision.action == "fill":
        return _fill_checks(before, decision, result, after)
    if decision.action == "select":
        return _select_checks(before, decision, result, after)
    if decision.action in {"click", "press"}:
        checks = [*_form_checks(before, decision, after),
                  *(_validation_dismiss_checks(before, decision, after) if decision.action == "click" else [])]
        cart_intent = result.evidence.get("business_cart_intent") or {}
        if cart_intent.get("kind") == "explicit_cart_reset":
            cart = after.page_state.get("cart") or {}
            count = cart.get("count")
            known_items = _items(cart, "items") if cart.get("items_known") else None
            if isinstance(count, int) or known_items is not None:
                checks.append(_assert(
                    "cart_reset", {"count": 0, "items": []},
                    {"count": count, "items": [_product_key(item) for item in known_items] if known_items is not None else None},
                    (count is None or count == 0) and (known_items is None or not known_items),
                    "An explicitly requested cart reset must expose an empty cart; its new lifecycle replaces prior membership expectations.",
                ))
        if decision.action == "click":
            checks.extend(_product_checks(before, decision, after))
        checks.extend(_cart_navigation_checks(before, decision, after))
        checks.extend(_checkout_checks(before, decision, after))
        return checks
    if decision.action in {"navigate", "go_back", "reload"}:
        return [*_cart_navigation_checks(before, decision, after), *_checkout_checks(before, decision, after)]
    return []
