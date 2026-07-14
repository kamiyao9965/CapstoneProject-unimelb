"""Compile a discovered private-health schema into an extraction JSON Schema."""

from __future__ import annotations

from collections.abc import Mapping

from src.common.json_contracts import validate_contract
from src.schema.validation import validate_schema_mapping


def compile_extraction_contract(schema: Mapping[str, object]) -> dict[str, object]:
    validate_contract(schema, "private_health/discovered_schema")
    validate_schema_mapping(schema)
    properties: dict[str, object] = {}
    field_names: list[str] = []
    for field in schema["fields"]:
        name = str(field["name"])
        field_names.append(name)
        properties[name] = _field_contract(field)
    properties["_unfilled"] = {
        "type": "array",
        "items": {"enum": field_names},
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


def _field_contract(field: Mapping[str, object]) -> dict[str, object]:
    field_type = field["type"]
    if field_type == "string":
        return {"type": ["string", "null"]}
    if field_type == "number":
        return {"type": ["number", "null"]}
    if field_type == "boolean":
        return {"type": ["boolean", "null"]}
    if field_type == "enum":
        return {"enum": [*field["values"], None]}
    if field_type == "list[object]":
        return {
            "oneOf": [
                {"type": "null"},
                {
                    "type": "array",
                    "items": {"type": "object", "additionalProperties": True},
                },
            ]
        }
    raise ValueError(f"Unsupported extraction field type: {field_type}")
