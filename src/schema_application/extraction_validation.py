"""Business validation for relationships inside one extracted record."""

from __future__ import annotations

from collections.abc import Mapping


def validate_unfilled_consistency(
    payload: Mapping[str, object], schema_data: Mapping[str, object]
) -> None:
    """Ensure null applicable fields and `_unfilled` describe the same set."""
    raw_unfilled = payload.get("_unfilled")
    if not isinstance(raw_unfilled, list) or any(
        not isinstance(name, str) for name in raw_unfilled
    ):
        raise ValueError("Extraction _unfilled must be a list of field names.")
    unfilled = set(raw_unfilled)
    product_type = payload.get("product_type")
    for field in schema_data.get("fields", []):
        if not isinstance(field, Mapping) or not isinstance(field.get("name"), str):
            continue
        name = str(field["name"])
        applies_to = field.get("applies_to") or []
        applicable = not isinstance(product_type, str) or product_type in applies_to
        if not applicable:
            if name in unfilled:
                raise ValueError(
                    f"Extraction _unfilled must not include inapplicable field {name!r}."
                )
            continue
        is_null = payload.get(name) is None
        if is_null and name not in unfilled:
            raise ValueError(
                f"Extraction null field {name!r} must be listed in _unfilled."
            )
        if not is_null and name in unfilled:
            raise ValueError(
                f"Extraction populated field {name!r} must not be listed in _unfilled."
            )
