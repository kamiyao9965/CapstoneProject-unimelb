"""Strict schema-generation descriptors compiled into native extraction schemas.

Business names are values in a flat node table. The transport has fixed keys,
without recursive schemas, provider-specific branches or inferred model data.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from src.common.json_contracts import load_contract, validate_inline_contract, validate_contract
from src.schema.nested import validate_item_schema, validate_value_schema
from src.schema.validation import SNAKE_CASE_NAME, validate_schema_mapping
from src.verticals.manifest import VerticalManifest


NODE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
JSON_TYPES = ["object", "array", "string", "number", "integer", "boolean", "null"]


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties}


def generation_contract(manifest: VerticalManifest, *, patches: bool = False) -> dict[str, Any]:
    """Build one non-recursive strict wire contract from the existing contracts."""
    scalar_values = {"type": "array", "items": {"type": ["string", "number", "boolean"]}}
    node = _object({
        "node_id": {"type": "string"}, "type": {"type": "string", "enum": JSON_TYPES},
        "nullable": {"type": "boolean"}, "description": {"type": "string"},
        "properties": {"type": "array", "items": _object({"name": {"type": "string"}, "node_id": {"type": "string"}})},
        "items_node_id": {"type": "string"}, "enum_values": scalar_values,
        "minimum": {"type": ["number", "null"]}, "maximum": {"type": ["number", "null"]},
        "min_length": {"type": ["integer", "null"]}, "max_length": {"type": ["integer", "null"]},
    })
    nodes = {"type": "array", "maxItems": 512, "items": node}
    if patches:
        result = load_contract(manifest.contract("candidate_patch_set"), manifest=manifest)
        field = result["properties"]["patches"]["items"]
    else:
        result = load_contract(manifest.contract("discovered_schema"), manifest=manifest)
        field = result["$defs"]["field"]
        for key in ("$defs", "document_fields", "validation_rules"):
            result["properties"].pop(key, None)
        result["properties"]["validation_profile"] = {"type": "string"}
        result["properties"]["document_fields"] = {"type": "array", "items": _object({
            "name": {"type": "string"}, "node_id": {"type": "string"}})}
    for key in ("item_schema", "item_fields", "unique_items"):
        field["properties"].pop(key, None)
    field["properties"]["item_node_id"] = {"type": "string"}
    field["required"] = list(field["properties"])
    result["properties"]["schema_nodes"] = nodes
    result["required"] = list(result["properties"])
    return result


class _NodeCompiler:
    def __init__(self, nodes: list[dict[str, Any]]) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.used: set[str] = set()
        self.budget = 20_000
        for node in nodes:
            node_id = node["node_id"]
            if not NODE_ID.fullmatch(node_id) or node_id in self.nodes:
                raise ValueError("Schema node IDs must be valid and unique.")
            self.nodes[node_id] = node

    def compile(self, node_id: str, stack: tuple[str, ...] = ()) -> dict[str, Any]:
        self.budget -= 1
        if self.budget < 0 or len(stack) > 16:
            raise ValueError("Generated schema exceeds the supported size or depth.")
        if node_id not in self.nodes or node_id in stack:
            raise ValueError("Schema node reference is missing or cyclic.")
        self.used.add(node_id)
        node = self.nodes[node_id]
        kind = node["type"]
        if kind != "object" and node["properties"]:
            raise ValueError("Only object nodes may declare properties.")
        if (kind == "array") != bool(node["items_node_id"]):
            raise ValueError("Only array nodes must declare an items_node_id.")
        if kind not in {"number", "integer"} and any(node[key] is not None for key in ("minimum", "maximum")):
            raise ValueError("Numeric bounds require a numeric node.")
        if kind != "string" and any(node[key] is not None for key in ("min_length", "max_length")):
            raise ValueError("String bounds require a string node.")
        if kind in {"object", "array", "null"} and node["enum_values"]:
            raise ValueError("Enums require scalar nodes.")
        result: dict[str, Any] = {"type": [kind, "null"] if node["nullable"] and kind != "null" else kind}
        if node["description"]:
            result["description"] = node["description"]
        for wire, native in (("minimum", "minimum"), ("maximum", "maximum"),
                             ("min_length", "minLength"), ("max_length", "maxLength")):
            if node[wire] is not None:
                result[native] = node[wire]
        if node["enum_values"]:
            for value in node["enum_values"]:
                validate_inline_contract(value, {"type": kind}, "generated_enum_value")
            result["enum"] = [*node["enum_values"], *([None] if node["nullable"] else [])]
        if kind == "object":
            properties = {}
            for member in node["properties"]:
                name = member["name"]
                if not SNAKE_CASE_NAME.fullmatch(name) or name in properties:
                    raise ValueError("Object members require unique snake_case names.")
                properties[name] = self.compile(member["node_id"], (*stack, node_id))
            result.update(properties=properties, required=list(properties), additionalProperties=False)
        elif kind == "array":
            result["items"] = self.compile(node["items_node_id"], (*stack, node_id))
        return validate_value_schema(result)

    def field(self, raw: dict[str, Any]) -> dict[str, Any]:
        field = deepcopy(raw)
        node_id = field.pop("item_node_id")
        if field["type"] == "list[object]":
            if not node_id:
                raise ValueError("list[object] requires a declared item node.")
            field["item_schema"] = validate_item_schema(self.compile(node_id))
        elif node_id:
            raise ValueError("Scalar fields cannot declare an item node.")
        return field

    def finish(self) -> None:
        if self.used != set(self.nodes):
            raise ValueError("Generated schema includes unused nodes.")


def decode_generated_schema(payload: object, manifest: VerticalManifest) -> dict[str, Any]:
    validate_inline_contract(payload, generation_contract(manifest), "schema_generation")
    result = deepcopy(payload)
    compiler = _NodeCompiler(result.pop("schema_nodes"))
    result["fields"] = [compiler.field(field) for field in result["fields"]]
    document_fields = {}
    for field in result.pop("document_fields"):
        name = field["name"]
        if (not SNAKE_CASE_NAME.fullmatch(name) or name == "products" or name in document_fields):
            raise ValueError("Document fields require unique, non-reserved names.")
        document_fields[name] = compiler.compile(field["node_id"])
    if document_fields:
        result["document_fields"] = document_fields
    if not result["validation_profile"]:
        result.pop("validation_profile")
    compiler.finish()
    return validate_schema_mapping(result, manifest=manifest)


def decode_generated_patches(payload: object, manifest: VerticalManifest) -> dict[str, Any]:
    validate_inline_contract(payload, generation_contract(manifest, patches=True), "patch_generation")
    result = deepcopy(payload)
    compiler = _NodeCompiler(result.pop("schema_nodes"))
    result["patches"] = [compiler.field(field) for field in result["patches"]]
    compiler.finish()
    validate_contract(result, manifest.contract("candidate_patch_set"), manifest=manifest)
    from src.refine.candidates.patch import parse_patch_payload
    parse_patch_payload(result, allowed_product_types=set(manifest.product_types))
    return result
