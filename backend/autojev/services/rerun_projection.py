"""Keep private browser replay recipes out of API/report presentation."""
from __future__ import annotations

from typing import Any


def without_rerun_recipes(value: Any) -> Any:
    """Copy presentation data, excluding execution-only recipe metadata."""
    if isinstance(value, dict):
        return {key: without_rerun_recipes(item) for key, item in value.items()
                if key != "rerun_recipe"}
    if isinstance(value, list):
        return [without_rerun_recipes(item) for item in value]
    return value
