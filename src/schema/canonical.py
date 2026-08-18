"""Validation boundary for human-reviewed Canonical Schemas."""

from __future__ import annotations

from collections.abc import Mapping

from src.common.json_contracts import validate_contract


_PRODUCT_NAME_TARGET = "products.canonical_name"
_PRODUCT_TYPE_TARGET = "product_releases.source_product_type"
_RESERVED_TABLES = frozenset(
    {
        "verticals",
        "insurers",
        "documents",
        "schema_versions",
        "extraction_runs",
        "raw_extractions",
        "products",
        "product_releases",
        "product_release_documents",
    }
)


def validate_canonical_schema(payload: object) -> dict[str, object]:
    """Validate lifecycle, identity, and storage invariants without approving."""
    validate_contract(payload, "canonical_schema")
    if not isinstance(payload, dict):
        raise ValueError("Canonical Schema must be an object.")

    fields = payload["fields"]
    if not isinstance(fields, list):
        raise ValueError("Canonical Schema fields must be a list.")

    field_by_name: dict[str, Mapping[str, object]] = {}
    extension_columns: set[str] = set()
    extension = payload["extension"]
    if not isinstance(extension, Mapping):
        raise ValueError("Canonical Schema extension must be an object.")
    table_name = str(extension["table"])
    if table_name in _RESERVED_TABLES:
        raise ValueError(
            f"Canonical Schema extension table {table_name!r} is reserved."
        )
    attributes_column = str(extension["attributes_column"])
    reserved_extension_columns = {"release_id", attributes_column}

    for field in fields:
        if not isinstance(field, Mapping):
            raise ValueError("Canonical Schema field must be an object.")
        name = str(field["name"])
        if name in field_by_name:
            raise ValueError(f"Canonical Schema contains duplicate field name: {name}")
        field_by_name[name] = field

        field_type = str(field["type"])
        values = list(field["values"])
        if field_type == "enum" and not values:
            raise ValueError(f"Canonical enum field {name!r} requires values.")
        if field_type != "enum" and values:
            raise ValueError(
                f"Canonical non-enum field {name!r} must not declare values."
            )
        if field.get("required") is False and field.get("nullable") is False:
            raise ValueError(
                f"Optional canonical field {name!r} must allow null values."
            )

        storage = field["storage"]
        if not isinstance(storage, Mapping):
            raise ValueError(f"Canonical field {name!r} storage must be an object.")
        strategy = storage["strategy"]
        if field_type == "list[object]" and strategy != "jsonb":
            raise ValueError(
                f"Canonical list[object] field {name!r} must use jsonb storage."
            )
        if strategy == "extension_column":
            column = str(storage["column"])
            if column in reserved_extension_columns:
                raise ValueError(
                    f"Canonical extension column {column!r} is reserved."
                )
            if column in extension_columns:
                raise ValueError(
                    f"Canonical Schema contains duplicate extension column: {column}"
                )
            extension_columns.add(column)

    identity = payload["identity"]
    if not isinstance(identity, Mapping):
        raise ValueError("Canonical Schema identity must be an object.")
    _validate_identity_binding(
        field_by_name,
        str(identity["product_name_field"]),
        expected_target=_PRODUCT_NAME_TARGET,
        identity_label="product-name",
    )
    _validate_identity_binding(
        field_by_name,
        str(identity["product_type_field"]),
        expected_target=_PRODUCT_TYPE_TARGET,
        identity_label="product-type",
    )
    return payload


def require_approved_canonical_schema(payload: object) -> dict[str, object]:
    """Return a valid approved schema or fail before any compilation occurs."""
    schema = validate_canonical_schema(payload)
    if schema["status"] != "approved":
        raise ValueError(
            "Canonical Schema must be human-approved before it can be compiled."
        )
    return schema


def compile_canonical_extraction_contract(
    payload: object,
) -> dict[str, object]:
    """Compile one approved business contract into extraction JSON Schema."""
    schema = require_approved_canonical_schema(payload)
    fields = schema["fields"]
    if not isinstance(fields, list):
        raise ValueError("Canonical Schema fields must be a list.")

    product_properties: dict[str, object] = {}
    required_fields: list[str] = []
    field_names: list[str] = []
    for field in fields:
        if not isinstance(field, Mapping):
            raise ValueError("Canonical Schema field must be an object.")
        name = str(field["name"])
        field_names.append(name)
        product_properties[name] = _canonical_field_contract(field)
        if field["required"] is True:
            required_fields.append(name)

    product_properties["_unfilled"] = {
        "type": "array",
        "items": {"enum": field_names},
        "uniqueItems": True,
    }
    product_properties["_notes"] = {"type": ["string", "null"]}
    product_contract: dict[str, object] = {
        "type": "object",
        "additionalProperties": False,
        "required": [*required_fields, "_unfilled", "_notes"],
        "properties": product_properties,
    }

    output = schema["output"]
    if not isinstance(output, Mapping):
        raise ValueError("Canonical Schema output must be an object.")
    collection = str(output["collection"])
    document_notes_field = str(output["document_notes_field"])
    collection_contract: object
    if output["cardinality"] == "multiple":
        collection_contract = {
            "type": "array",
            "minItems": 1,
            "items": product_contract,
        }
    else:
        collection_contract = product_contract

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [collection, document_notes_field],
        "properties": {
            collection: collection_contract,
            document_notes_field: {"type": ["string", "null"]},
        },
    }


def _canonical_field_contract(field: Mapping[str, object]) -> dict[str, object]:
    field_type = str(field["type"])
    nullable = field["nullable"] is True
    if field_type == "enum":
        values = list(field["values"])
        return {"enum": [*values, None] if nullable else values}

    json_type = {
        "string": "string",
        "number": "number",
        "boolean": "boolean",
    }.get(field_type)
    if json_type is not None:
        return {"type": [json_type, "null"] if nullable else json_type}

    array_contract: dict[str, object] = {
        "type": "array",
        "items": {"type": "object", "additionalProperties": True},
    }
    if nullable:
        return {"oneOf": [{"type": "null"}, array_contract]}
    return array_contract


def _validate_identity_binding(
    fields: Mapping[str, Mapping[str, object]],
    field_name: str,
    *,
    expected_target: str,
    identity_label: str,
) -> None:
    field = fields.get(field_name)
    if field is None:
        raise ValueError(
            f"Canonical {identity_label} identity field {field_name!r} does not exist."
        )
    storage = field["storage"]
    if not isinstance(storage, Mapping) or storage.get("target") != expected_target:
        raise ValueError(
            f"Canonical {identity_label} identity field {field_name!r} must bind "
            f"to {expected_target!r}."
        )
    if field.get("required") is not True or field.get("nullable") is not False:
        raise ValueError(
            f"Canonical {identity_label} identity field {field_name!r} must be "
            "required and non-nullable."
        )
