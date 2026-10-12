"""Closed nested extraction schemas and explicit historical field conversion."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from jsonschema import Draft202012Validator


def resolve_local_refs(schema: object, definitions: object = None) -> dict[str, Any]:
    """Expand named local definitions without network access or recursive refs."""
    definitions = {} if definitions is None else definitions
    if not isinstance(definitions, Mapping):
        raise ValueError("Local schema definitions must be an object.")
    budget = 20_000

    def expand(node: Any, stack: tuple[str, ...] = (), depth: int = 0) -> Any:
        nonlocal budget
        budget -= 1
        if budget < 0 or depth > 32:
            raise ValueError("Nested schema reference expansion is too large or deep.")
        if isinstance(node, list):
            return [expand(item, stack, depth + 1) for item in node]
        if not isinstance(node, Mapping):
            return deepcopy(node)
        if "$ref" in node:
            ref = node["$ref"]
            if set(node) != {"$ref"} or not isinstance(ref, str) or not ref.startswith("#/$defs/"):
                raise ValueError("Only standalone local #/$defs/name references are allowed.")
            name = ref.removeprefix("#/$defs/")
            if "/" in name or "~" in name or name not in definitions or name in stack:
                raise ValueError("Missing, invalid or cyclic local schema reference.")
            return expand(definitions[name], (*stack, name), depth + 1)
        return {key: expand(value, stack, depth + 1) for key, value in node.items()}

    result = expand(schema)
    if not isinstance(result, dict):
        raise ValueError("Nested schema must be an object.")
    return result


def validate_item_schema(schema: object, definitions: object = None) -> dict[str, Any]:
    """Validate the bounded JSON Schema dialect and return an expanded copy."""
    schema = resolve_local_refs(schema, definitions)
    allowed = {"type", "properties", "required", "additionalProperties", "items", "enum",
               "minimum", "maximum", "minLength", "maxLength", "description", "title"}

    def walk(node: object, depth: int = 0) -> None:
        if depth > 16 or not isinstance(node, dict) or set(node) - allowed:
            raise ValueError("Unsupported nested schema shape or keyword.")
        types = node.get("type")
        types = types if isinstance(types, list) else [types]
        if not types or any(t not in {"object", "array", "string", "number", "integer", "boolean", "null"} for t in types):
            raise ValueError("Nested schemas require explicit JSON types.")
        if "object" in types:
            properties = node.get("properties")
            required = node.get("required")
            if (not isinstance(properties, dict) or node.get("additionalProperties") is not False
                    or not isinstance(required, list) or len(required) != len(set(required))
                    or set(required) != set(properties)):
                raise ValueError("Nested objects must declare and require every key; use nullable values for unknowns.")
            for value in properties.values():
                walk(value, depth + 1)
        if "array" in types:
            walk(node.get("items"), depth + 1)

    Draft202012Validator.check_schema(schema)
    if schema.get("type") != "object":
        raise ValueError("item_schema must describe an object.")
    walk(schema)
    return schema


def item_fields_to_schema(fields: object, schema: Mapping[str, Any]) -> dict[str, Any]:
    """Convert declared legacy scalar/enum item fields; never infer missing keys."""
    if not isinstance(fields, list) or not fields:
        raise ValueError("Legacy item_fields must be a non-empty list.")
    properties: dict[str, Any] = {}
    for field in fields:
        if not isinstance(field, Mapping):
            raise ValueError("Legacy item field must be an object.")
        name, field_type = field.get("name"), field.get("type")
        if not isinstance(name, str) or not name or name in properties:
            raise ValueError("Legacy item field names must be non-empty and unique.")
        if not isinstance(field.get("required"), bool):
            raise ValueError("Legacy item field required must be boolean.")
        required = field["required"]
        if field_type == "enum":
            values = list(field.get("values") or [])
            if not values and field.get("enum_ref"):
                ref = field["enum_ref"]
                entries = schema.get(ref, schema.get("taxonomies", {}).get(ref))
                if not isinstance(entries, list):
                    raise ValueError("Legacy enum_ref must reference a declared taxonomy.")
                values = [item["canonical_name"] if isinstance(item, Mapping) else item for item in entries]
            if not values:
                raise ValueError("Legacy enum item fields must declare allowed values.")
            json_types = {"boolean" if isinstance(v, bool) else "number" if isinstance(v, (int, float)) else "string" for v in values}
            types = sorted(json_types) + ([] if required else ["null"])
            contract = {"type": types[0] if len(types) == 1 else types,
                        "enum": values if required else [*values, None]}
        elif field_type in {"string", "number", "boolean"}:
            contract = {"type": field_type if required else [field_type, "null"]}
        else:
            raise ValueError("Legacy item_fields support scalar and enum fields only.")
        if field.get("description"):
            contract["description"] = field["description"]
        properties[name] = contract
    return validate_item_schema({"type": "object", "properties": properties,
                                 "required": list(properties), "additionalProperties": False})


def closed_extraction_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Require closed objects and all keys for new requests, preserving nullability."""
    result = deepcopy(dict(contract))

    def walk(node: object) -> None:
        if not isinstance(node, dict):
            return
        types = node.get("type", [])
        types = types if isinstance(types, list) else [types]
        if "object" in types:
            if node.get("additionalProperties") is not False or not isinstance(node.get("properties"), dict):
                raise ValueError("New extraction requires closed objects; declare item_schema and review a new schema version.")
            node["required"] = list(node["properties"])
            for value in node["properties"].values():
                walk(value)
        if "items" in node:
            walk(node["items"])
        for key in ("oneOf", "anyOf", "allOf"):
            for value in node.get(key, []):
                walk(value)
        if "$ref" in node:
            raise ValueError("Extraction references must be resolved into a closed item_schema.")

    walk(result)
    return result
