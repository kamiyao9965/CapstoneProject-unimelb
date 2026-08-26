"""Shared compiler for discovered-schema fields and closed object-list items."""

from __future__ import annotations

from collections.abc import Mapping


def compile_field_contract(
    field: Mapping[str, object], schema: Mapping[str, object]
) -> dict[str, object]:
    """Compile one validated discovery field into its runtime JSON Schema."""
    field_type = str(field["type"])
    business_required = field.get("required") is True
    if field_type in {"string", "number", "boolean"}:
        return _scalar_contract(field_type, required=business_required)
    if field_type == "enum":
        return _enum_contract(field, schema, required=business_required)
    if field_type.startswith("list[") and field_type != "list[object]":
        item_type = field_type[5:-1]
        item_contract = (
            _enum_contract(field, schema, required=True)
            if item_type == "enum"
            else _scalar_contract(item_type, required=True)
        )
        return _nullable_array(item_contract, unique_items=field.get("unique_items") is True)
    if field_type == "list[object]":
        item_fields = field["item_fields"]
        properties = {
            str(item["name"]): compile_item_field_contract(item, schema)
            for item in item_fields
        }
        return _nullable_array({
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            # Keys are structurally required. Business optionality is nullability.
            "required": list(properties),
        }, unique_items=field.get("unique_items") is True)
    raise ValueError(f"Unsupported extraction field type: {field_type}")


def compile_item_field_contract(
    field: Mapping[str, object], schema: Mapping[str, object]
) -> dict[str, object]:
    field_type = str(field["type"])
    business_required = field.get("required") is True
    if field_type == "enum":
        return _enum_contract(field, schema, required=business_required)
    return _scalar_contract(field_type, required=business_required)


def _scalar_contract(field_type: str, *, required: bool) -> dict[str, object]:
    json_type = {"string": "string", "number": "number", "boolean": "boolean"}[field_type]
    return {"type": json_type if required else [json_type, "null"]}


def _enum_contract(
    field: Mapping[str, object], schema: Mapping[str, object], *, required: bool
) -> dict[str, object]:
    values = _enum_values(field, schema)
    return {"enum": values if required else [*values, None]}


def _enum_values(
    field: Mapping[str, object], schema: Mapping[str, object]
) -> list[object]:
    values = list(field.get("values") or [])
    if values:
        return values
    enum_ref = str(field["enum_ref"])
    if enum_ref == "product_types":
        return list(schema.get("product_types") or [])
    return [
        item["canonical_name"]
        for item in schema.get(enum_ref, [])
        if isinstance(item, Mapping) and item.get("canonical_name")
    ]


def _nullable_array(
    items: Mapping[str, object], *, unique_items: bool = False
) -> dict[str, object]:
    array_contract: dict[str, object] = {"type": "array", "items": dict(items)}
    if unique_items:
        array_contract["uniqueItems"] = True
    return {
        "oneOf": [
            {"type": "null"},
            array_contract,
        ]
    }
