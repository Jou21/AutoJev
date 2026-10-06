"""Observed product identity shared by assertions and the action-intent ledger.

Declared item/product IDs distinguish homonymous SKUs. DOM selectors and
name-derived IDs never acquire that authority; unique names can still bridge
surfaces where the application does not expose a comparable entity ID.
"""

import json
import re
import unicodedata
from collections import Counter
from typing import Any


DECLARED_ID_SOURCES = frozenset({"declared_product_id", "declared_item_id"})


def normalized_product_name(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


def declared_product_id(product: dict[str, Any]) -> str | None:
    value = product.get("id")
    if not isinstance(value, str) or not value or any(
        marker in value for marker in ("[REDACTED]", "[FIELD VALUE]")
    ):
        return None
    source = product.get("identity_source")
    if source in DECLARED_ID_SOURCES:
        return value
    if source is not None:
        return None
    # Compatibility with saved/native fixtures predating source metadata.
    # Reject all executor name/structural fallbacks, not just one known tag.
    if value.startswith("name:") or ":nth-" in value or " > " in value or re.match(
        r"^[a-z][\w-]*:(?:id|name|data-[\w-]+)=", value, re.IGNORECASE,
    ) or re.fullmatch(r"(?:html|body|main|div|section|article|header|footer|nav|aside)", value, re.IGNORECASE):
        return None
    return value


def product_ledger_key(product: dict[str, Any]) -> str:
    declared = declared_product_id(product)
    return f"id:{declared}" if declared is not None else normalized_product_name(product.get("name"))


def same_observed_product(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    wanted_id, actual_id = declared_product_id(expected), declared_product_id(actual)
    wanted_name = normalized_product_name(expected.get("name"))
    actual_name = normalized_product_name(actual.get("name"))
    if wanted_id is not None and actual_id is not None:
        return wanted_id == actual_id and (not wanted_name or not actual_name or wanted_name == actual_name)
    return bool(wanted_name and actual_name and wanted_name == actual_name)


def matching_product_indexes(
    product: dict[str, Any], items: list[dict[str, Any]],
) -> list[int]:
    """Match declared IDs first; names bridge only missing ID provenance.

    Callers must require a single match before transferring a ledger entry or
    approving a selected control. Homonymous name-only observations cannot
    choose among two distinct declared SKUs.
    """
    wanted_id = declared_product_id(product)
    matches = []
    for index, item in enumerate(items):
        actual_id = declared_product_id(item)
        # The intent ledger follows the entity, not mutable presentation text.
        # A renamed SKU can still be removed; business assertions separately
        # report a mismatched observed name for the same declared entity.
        if wanted_id is not None and actual_id is not None:
            matched = wanted_id == actual_id
        else:
            matched = same_observed_product(product, item)
        if matched:
            matches.append(index)
    return matches


def product_comparison_key(product: dict[str, Any]) -> str:
    declared = declared_product_id(product)
    name = normalized_product_name(product.get("name"))
    return json.dumps(["id", declared, name], ensure_ascii=False) if declared is not None else name


def comparable_composition_keys(
    expected: list[dict[str, Any]], actual: list[dict[str, Any]],
) -> tuple[list[str], list[str]] | None:
    """Choose shared identity keys without merging declared homonymous SKUs.

    A catalog can declare IDs while the review exposes only names. Bridge a
    name only when unique on both sides. Ambiguous cross-surface identities
    yield no identity assertion, never a fabricated approval or mismatch.
    """
    if all(declared_product_id(item) is not None for item in [*expected, *actual]):
        return ([product_comparison_key(item) for item in expected],
                [product_comparison_key(item) for item in actual])
    names = lambda items: Counter(normalized_product_name(item.get("name")) for item in items)
    expected_names, actual_names = names(expected), names(actual)
    any_declared = any(declared_product_id(item) is not None for item in [*expected, *actual])
    if any_declared and any(count > 1 for counts in (expected_names, actual_names) for count in counts.values()):
        return None

    def key(item: dict[str, Any], other: list[dict[str, Any]]) -> str:
        name = normalized_product_name(item.get("name"))
        counterpart = next((candidate for candidate in other
                            if normalized_product_name(candidate.get("name")) == name), None)
        if counterpart is not None and (
            declared_product_id(item) is None or declared_product_id(counterpart) is None
        ):
            return name
        return product_comparison_key(item)

    return ([key(item, actual) for item in expected], [key(item, expected) for item in actual])
