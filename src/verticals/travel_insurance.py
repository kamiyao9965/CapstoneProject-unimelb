"""Travel-insurance-specific schema invariants."""

from __future__ import annotations

from src.schema.validation import (
    validate_field_payload,
    validate_product_type_field,
)


SUPPORTED_TRAVEL_PRODUCT_TYPES = frozenset(
    {
        "international_single_trip",
        "international_multi_trip",
        "domestic",
        "inbound",
        "business",
        "cruise",
    }
)


def validate_travel_schema_mapping(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("Schema JSON must be an object.")
    if payload.get("vertical") != "travel_insurance":
        raise ValueError("Schema vertical must be 'travel_insurance'.")

    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("Schema version must be a non-empty string.")

    product_types = payload.get("product_types")
    if not isinstance(product_types, list) or not product_types:
        raise ValueError("Schema product_types must be a non-empty list.")
    if any(not isinstance(value, str) for value in product_types):
        raise ValueError("Schema product_types must contain strings only.")
    unknown = set(product_types) - SUPPORTED_TRAVEL_PRODUCT_TYPES
    if unknown:
        raise ValueError(
            "Schema contains unknown travel product types: "
            + ", ".join(sorted(unknown))
        )
    if len(product_types) != len(set(product_types)):
        raise ValueError("Schema product_types must not contain duplicates.")

    product_type_field = payload.get("product_type_field")
    validate_field_payload(
        product_type_field,
        set(product_types),
    )
    validate_product_type_field([product_type_field], product_types)

    fields = payload.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ValueError("Schema fields must be a non-empty list.")
    field_names: set[str] = set()
    for index, field in enumerate(fields):
        validated = validate_field_payload(field, set(product_types), index=index)
        name = str(validated["name"])
        if name == "product_type":
            raise ValueError(
                "Schema product_type is reserved for product_type_field."
            )
        if name in field_names:
            raise ValueError(f"Schema contains duplicate field name: {name}")
        field_names.add(name)
    _validate_coverage_categories(payload.get("coverage_categories"))
    return payload


def _validate_coverage_categories(payload: object) -> None:
    if not isinstance(payload, list):
        raise ValueError("Schema coverage_categories must be a list.")
    names: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("Each coverage category must be an object.")
        name = item.get("canonical_name")
        if not isinstance(name, str) or not name:
            raise ValueError("Each coverage category needs a canonical_name.")
        if name in names:
            raise ValueError(f"Duplicate coverage category: {name}")
        names.add(name)
