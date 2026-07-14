"""Validation boundary for model-generated private-health schema contracts."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from typing import TypeAlias

SUPPORTED_FIELD_TYPES = frozenset(
    {"string", "number", "boolean", "enum", "list[object]"}
)
SUPPORTED_PRODUCT_TYPES = frozenset(
    {"hospital", "extras", "generalhealth", "combined"}
)
SNAKE_CASE_NAME = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
JSONScalar: TypeAlias = str | int | float | bool


def validate_schema_mapping(payload: object) -> dict[str, object]:
    """Validate a parsed schema and return it with a precise mapping type."""
    if not isinstance(payload, dict):
        raise ValueError("Schema JSON must be an object.")
    if payload.get("vertical") != "private_health":
        raise ValueError("Schema vertical must be 'private_health'.")
    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("Schema version must be a non-empty string.")

    product_types = payload.get("product_types")
    if not isinstance(product_types, list) or not product_types:
        raise ValueError("Schema product_types must be a non-empty list.")
    if any(not isinstance(value, str) for value in product_types):
        raise ValueError("Schema product_types must contain strings only.")
    unknown_product_types = set(product_types) - SUPPORTED_PRODUCT_TYPES
    if unknown_product_types:
        raise ValueError(
            "Schema contains unknown product types: "
            + ", ".join(sorted(unknown_product_types))
        )
    if len(product_types) != len(set(product_types)):
        raise ValueError("Schema product_types must not contain duplicates.")

    fields = payload.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ValueError("Schema fields must be a non-empty list.")
    field_names: set[str] = set()
    allowed_product_types = set(product_types)
    for index, field in enumerate(fields):
        validated = validate_field_payload(field, allowed_product_types, index=index)
        name = str(validated["name"])
        if name in field_names:
            raise ValueError(f"Schema contains duplicate field name: {name}")
        field_names.add(name)
    _validate_product_type_field(fields, product_types)
    return payload


def _validate_product_type_field(
    fields: list[object],
    product_types: list[str],
) -> None:
    matches = [
        field for field in fields
        if isinstance(field, dict) and field.get("name") == "product_type"
    ]
    if len(matches) != 1:
        raise ValueError(
            "Schema must contain exactly one product_type field for holdout classification."
        )
    product_type = matches[0]
    if product_type.get("type") != "enum":
        raise ValueError("Schema product_type field must use type 'enum'.")
    values = product_type.get("values")
    if (
        not isinstance(values, list)
        or any(not isinstance(value, str) for value in values)
        or len(values) != len(product_types)
        or set(values) != set(product_types)
    ):
        raise ValueError(
            "Schema product_type values must match top-level product_types exactly."
        )
    if set(product_type.get("applies_to") or []) != set(product_types):
        raise ValueError(
            "Schema product_type applies_to must cover every top-level product_type."
        )
    if product_type.get("required") is not True:
        raise ValueError("Schema product_type field must be required.")


def validate_field_payload(
    payload: object,
    allowed_product_types: Collection[str],
    *,
    index: int | None = None,
) -> Mapping[str, object]:
    """Validate one schema field without silently repairing external data."""
    label = f"field {index}" if index is not None else "field"
    if not isinstance(payload, dict):
        raise ValueError(f"Schema {label} must be an object.")

    name = payload.get("name")
    if not isinstance(name, str) or not SNAKE_CASE_NAME.fullmatch(name):
        raise ValueError(f"Schema {label} name must be canonical snake_case.")
    field_type = payload.get("type")
    if field_type not in SUPPORTED_FIELD_TYPES:
        raise ValueError(
            f"Schema field {name!r} has unsupported type {field_type!r}."
        )
    description = payload.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"Schema field {name!r} needs a non-empty description.")

    applies_to = payload.get("applies_to")
    if not isinstance(applies_to, list) or not applies_to:
        raise ValueError(f"Schema field {name!r} applies_to must be a non-empty list.")
    if any(not isinstance(value, str) for value in applies_to):
        raise ValueError(f"Schema field {name!r} applies_to must contain strings only.")
    unknown = set(applies_to) - set(allowed_product_types)
    if unknown:
        raise ValueError(
            f"Schema field {name!r} contains unknown product types: "
            + ", ".join(sorted(unknown))
        )
    if len(applies_to) != len(set(applies_to)):
        raise ValueError(f"Schema field {name!r} applies_to must not contain duplicates.")

    if not isinstance(payload.get("required"), bool):
        raise ValueError(f"Schema field {name!r} required must be boolean.")
    values = payload.get("values")
    if not isinstance(values, list):
        raise ValueError(f"Schema field {name!r} values must be a list.")
    if field_type == "enum" and not values:
        raise ValueError(f"Schema enum field {name!r} must declare allowed values.")
    if field_type == "enum" and any(
        not isinstance(value, (str, int, float, bool)) for value in values
    ):
        raise ValueError(f"Schema enum field {name!r} values must be scalar.")

    aliases = payload.get("aliases")
    if (
        not isinstance(aliases, list)
        or any(not isinstance(alias, str) or not alias.strip() for alias in aliases)
    ):
        raise ValueError(f"Schema field {name!r} aliases must be a list of strings.")
    return payload
