"""Compile discovered schemas into runtime extraction JSON Schemas."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256

from src.common.json_contracts import validate_contract
from src.common.json_codec import dumps_json
from src.schema.migration import migrate_legacy_discovered_schema
from src.schema.validation import validate_pet_schema_mapping, validate_schema_mapping
from src.schema.field_contract import compile_field_contract


def schema_hash(schema_data: Mapping[str, object]) -> str:
    """Return the stable identity used by extraction caches and feedback."""
    return sha256(
        dumps_json(
            schema_data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def compile_extraction_contract(
    schema: Mapping[str, object], *, product_type: str | None = None
) -> dict[str, object]:
    if schema.get("vertical") == "pet_insurance":
        if product_type is not None:
            raise ValueError("Pet-insurance extraction is document-role based; product_type is unsupported.")
        return compile_pet_extraction_contract(schema)
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


def compile_pet_extraction_contract(
    schema: Mapping[str, object],
    *,
    document_role: str | None = None,
    expected_product_ids: list[str] | None = None,
) -> dict[str, object]:
    """Compile a pet schema into a document envelope with products/amendments."""
    validate_contract(schema, "pet_insurance/discovered_schema")
    validate_pet_schema_mapping(schema)
    role = document_role.strip().lower() if isinstance(document_role, str) else None
    roles = [str(value) for value in schema.get("document_roles") or []]
    if role is not None and role not in roles:
        raise ValueError(f"Document role {role!r} is not declared by the schema.")

    cover_scopes = {str(value) for value in schema.get("cover_scopes") or []}
    fields = [field for field in schema.get("fields", []) if isinstance(field, Mapping)]

    document_properties = {
        "document_id": {"type": "string"},
        "document_role": {"type": "string", "enum": roles},
        "effective_date": {"type": ["string", "null"]},
        "source_path": {"type": "string"},
        "document_family_id": {"type": ["string", "null"]},
    }
    # Discovery fields tagged as document metadata belong in the document
    # envelope. Keep the stable envelope fields above authoritative when a
    # discovered schema happens to use one of their names.
    for field in fields:
        name = str(field["name"])
        applies_to = {str(value) for value in field.get("applies_to", [])}
        if "document" in applies_to and name not in document_properties:
            document_properties[name] = compile_field_contract(field, schema)
    document_contract: dict[str, object] = {
        "type": "object",
        "additionalProperties": False,
        "properties": document_properties,
        "required": list(document_properties),
    }

    product_contract = _compile_pet_product_contract(schema, fields, cover_scopes)
    expected_ids = list(dict.fromkeys(expected_product_ids or []))
    product_properties = product_contract.get("properties")
    if expected_ids and isinstance(product_properties, dict) and "product_id" in product_properties:
        product_properties["product_id"] = {"type": "string", "enum": expected_ids}
    amendment_properties = {
        "affected_product": {"type": "string"},
        "affected_field": {"type": "string"},
        "operation": {"type": "string", "enum": ["replace", "add", "remove"]},
        "new_value_raw": {"type": ["string", "null"]},
        "effective_date": {"type": ["string", "null"]},
        "notes": {"type": ["string", "null"]},
    }
    amendment_contract: dict[str, object] = {
        "type": "object", "additionalProperties": False,
        "properties": amendment_properties,
        "required": list(amendment_properties),
    }
    is_amendment = role in {"update", "supplementary_pds"}
    properties: dict[str, object] = {
        "document": document_contract,
        "_notes": {"type": ["string", "null"]},
    }
    required = ["document", "_notes"]
    if is_amendment:
        properties["amendments"] = {"type": "array", "items": amendment_contract}
        required.append("amendments")
    else:
        products: dict[str, object] = {
            "type": "array",
            "items": product_contract,
            "minItems": len(expected_ids) if expected_ids else 1,
        }
        if expected_ids:
            products["maxItems"] = len(expected_ids)
        properties["products"] = products
        required.append("products")
    if role is not None:
        document_contract["properties"]["document_role"] = {"const": role}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object", "additionalProperties": False,
        "properties": properties, "required": required,
    }


def compile_pet_product_family_contract(
    schema: Mapping[str, object],
    *,
    document_family_id: str,
    document_count: int,
    expected_product_ids: list[str] | None = None,
) -> dict[str, object]:
    """Compile the final, product-oriented contract for a document family.

    A family may contain one multi-plan PDS, several plan-specific PDS files,
    and/or later Update/SPDS files.  The resulting products array represents
    the effective products after all documents have been read together.
    """
    validate_contract(schema, "pet_insurance/discovered_schema")
    validate_pet_schema_mapping(schema)
    if document_count <= 0:
        raise ValueError("A product family must contain at least one document.")

    fields = [field for field in schema.get("fields", []) if isinstance(field, Mapping)]
    cover_scopes = {str(value) for value in schema.get("cover_scopes") or []}
    product_contract = _compile_pet_product_contract(schema, fields, cover_scopes)
    expected_ids = list(dict.fromkeys(expected_product_ids or []))
    product_properties = product_contract.get("properties")
    if expected_ids and isinstance(product_properties, dict) and "product_id" in product_properties:
        product_properties["product_id"] = {"type": "string", "enum": expected_ids}

    roles = [str(value) for value in schema.get("document_roles") or []]
    source_document = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "document_id": {"type": "string"},
            "document_role": {"type": "string", "enum": roles},
            "effective_date": {"type": ["string", "null"]},
            "source_path": {"type": "string"},
        },
        "required": ["document_id", "document_role", "effective_date", "source_path"],
    }
    products: dict[str, object] = {
        "type": "array",
        "items": product_contract,
        "minItems": len(expected_ids) if expected_ids else 1,
    }
    if expected_ids:
        products["maxItems"] = len(expected_ids)

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "document_family_id": {"const": document_family_id},
            "source_documents": {
                "type": "array",
                "items": source_document,
                "minItems": document_count,
                "maxItems": document_count,
            },
            "products": products,
            "_notes": {"type": ["string", "null"]},
        },
        "required": ["document_family_id", "source_documents", "products", "_notes"],
    }


def _compile_pet_product_contract(
    schema: Mapping[str, object],
    fields: list[Mapping[str, object]],
    cover_scopes: set[str],
) -> dict[str, object]:
    product_fields = []
    for field in fields:
        applies_to = {str(value) for value in field.get("applies_to", [])}
        if "product" in applies_to or applies_to.intersection(cover_scopes):
            product_fields.append(field)
    field_names = [str(field["name"]) for field in product_fields]
    product_properties = {
        name: compile_field_contract(field, schema)
        for name, field in ((str(field["name"]), field) for field in product_fields)
    }
    product_properties["_unfilled"] = {
        "type": "array", "items": {"enum": field_names}, "uniqueItems": True,
    }
    product_properties["_notes"] = {"type": ["string", "null"]}
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": product_properties,
        "required": [*field_names, "_unfilled", "_notes"],
    }
