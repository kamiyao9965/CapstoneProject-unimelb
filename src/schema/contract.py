"""Compile discovered schemas into runtime extraction JSON Schemas."""

from __future__ import annotations

from collections.abc import Mapping

from src.common.json_contracts import validate_contract
from src.schema.migration import migrate_legacy_discovered_schema
from src.schema.validation import validate_schema_mapping
from src.schema.field_contract import compile_field_contract


def compile_extraction_contract(
    schema: Mapping[str, object], *, product_type: str | None = None
) -> dict[str, object]:
    validate_contract(schema, "private_health/discovered_schema")
    schema = migrate_legacy_discovered_schema(schema)
    validate_schema_mapping(schema)
    if product_type is not None:
        supported = schema.get("product_types") or []
        if product_type not in supported:
            raise ValueError(
                f"Effective product type {product_type!r} is not declared by the schema."
            )

    properties: dict[str, object] = {}
    field_names: list[str] = []
    applicable_field_names: list[str] = []
    for field in schema["fields"]:
        name = str(field["name"])
        field_names.append(name)
        applicable = (
            product_type is None
            or product_type in (field.get("applies_to") or [])
        )
        properties[name] = (
            compile_field_contract(field, schema)
            if applicable or name == "product_type"
            else {"type": "null"}
        )
        if applicable:
            applicable_field_names.append(name)

    if product_type is not None:
        properties["product_type"] = {"const": product_type}
    properties["_unfilled"] = {
        "type": "array",
        "items": {"enum": applicable_field_names},
        "uniqueItems": True,
    }
    properties["_notes"] = {"type": ["string", "null"]}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [*field_names, "_unfilled", "_notes"],
        "properties": properties,
    }
