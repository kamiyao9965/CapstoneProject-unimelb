"""Validation boundary for human-reviewed Canonical Schemas."""

from __future__ import annotations

import re
from copy import deepcopy
from collections.abc import Mapping
from datetime import datetime, timezone

from src.common.json_contracts import validate_contract


_PRODUCT_NAME_TARGET = "products.canonical_name"
_PRODUCT_TYPE_TARGET = "product_releases.source_product_type"
_RFC3339_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
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


def is_canonical_schema(payload: object) -> bool:
    """Identify the versioned Canonical Schema interface before validation."""
    return (
        isinstance(payload, Mapping)
        and payload.get("contract_version") == "1.0.0"
        and "status" in payload
        and "extension" in payload
        and "identity" in payload
    )


def validate_canonical_schema(payload: object) -> dict[str, object]:
    """Validate lifecycle, identity, and storage invariants without approving."""
    validate_contract(payload, "canonical_schema")
    if not isinstance(payload, dict):
        raise ValueError("Canonical Schema must be an object.")

    if payload["status"] == "approved":
        review = payload["review"]
        if not isinstance(review, Mapping):
            raise ValueError("Approved Canonical Schema review must be an object.")
        _validate_review_timestamp(str(review["reviewed_at"]))

    fields = payload["fields"]
    if not isinstance(fields, list):
        raise ValueError("Canonical Schema fields must be a list.")

    field_by_name: dict[str, Mapping[str, object]] = {}
    extension_columns: set[str] = set()
    core_bindings: set[str] = set()
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
        elif strategy == "core_column":
            target = str(storage["target"])
            if target in core_bindings:
                raise ValueError(
                    f"Canonical Schema contains duplicate core binding: {target}"
                )
            core_bindings.add(target)

    identity = payload["identity"]
    if not isinstance(identity, Mapping):
        raise ValueError("Canonical Schema identity must be an object.")
    _validate_identity_binding(
        field_by_name,
        str(identity["product_name_field"]),
        expected_target=_PRODUCT_NAME_TARGET,
        expected_type="string",
        identity_label="product-name",
    )
    _validate_identity_binding(
        field_by_name,
        str(identity["product_type_field"]),
        expected_target=_PRODUCT_TYPE_TARGET,
        expected_type="enum",
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


def approve_canonical_schema(
    payload: object,
    *,
    reviewer: str,
    rationale: str,
    reviewed_at: str | None = None,
) -> dict[str, object]:
    """Create an approved copy after an explicit human mapping decision."""
    candidate = validate_canonical_schema(payload)
    if candidate["status"] != "candidate":
        raise ValueError("Only a Canonical Schema candidate can be approved.")
    reviewer = reviewer.strip()
    rationale = rationale.strip()
    if not reviewer:
        raise ValueError("Canonical Schema approval requires a reviewer.")
    if not rationale:
        raise ValueError("Canonical Schema approval requires a rationale.")
    timestamp = reviewed_at or datetime.now(timezone.utc).isoformat()
    approved = deepcopy(candidate)
    approved["status"] = "approved"
    approved["review"] = {
        "reviewed_by": reviewer,
        "reviewed_at": timestamp,
        "rationale": rationale,
    }
    return validate_canonical_schema(approved)


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


def validate_canonical_extraction_identities(
    schema_payload: object,
    extraction_payload: object,
) -> object:
    """Reject repeated product names before an extraction can be persisted."""
    schema = require_approved_canonical_schema(schema_payload)
    if not isinstance(extraction_payload, Mapping):
        raise ValueError("Canonical extraction payload must be an object.")

    output = schema["output"]
    if not isinstance(output, Mapping):
        raise ValueError("Canonical Schema output must be an object.")
    extracted_products = extraction_payload.get(str(output["collection"]))
    if output["cardinality"] == "multiple":
        if not isinstance(extracted_products, list):
            raise ValueError("Canonical multiple output must contain a product list.")
        products = extracted_products
    else:
        products = [extracted_products]

    identity = schema["identity"]
    if not isinstance(identity, Mapping):
        raise ValueError("Canonical Schema identity must be an object.")
    product_name_field = str(identity["product_name_field"])
    seen: set[str] = set()
    for product in products:
        if not isinstance(product, Mapping):
            raise ValueError("Canonical extracted product must be an object.")
        product_name = product.get(product_name_field)
        if not isinstance(product_name, str) or not product_name.strip():
            raise ValueError("Canonical product-name identity must be non-empty.")
        identity_key = product_name.strip().casefold()
        if identity_key in seen:
            raise ValueError(
                "Canonical extraction contains duplicate product identity: "
                f"{product_name!r}. product_name must be unique within one "
                "document; include the marketed plan tier when several plans "
                "share an umbrella series name."
            )
        seen.add(identity_key)
    return extraction_payload


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
    expected_type: str,
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
    if field.get("type") != expected_type:
        raise ValueError(
            f"Canonical {identity_label} identity field {field_name!r} must use "
            f"type {expected_type!r}."
        )


def _validate_review_timestamp(value: str) -> None:
    if not _RFC3339_TIMESTAMP.fullmatch(value):
        raise ValueError(
            "Canonical Schema review reviewed_at must be a timezone-aware "
            "RFC 3339 timestamp."
        )
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(
            "Canonical Schema review reviewed_at must be a valid timestamp."
        ) from exc
    if parsed.utcoffset() is None:
        raise ValueError(
            "Canonical Schema review reviewed_at must include a timezone offset."
        )


def build_canonical_candidate(payload: object, manifest) -> dict[str, object]:
    """Map reviewed fields using existing approved storage choices; unknowns need review."""
    from copy import deepcopy
    from src.schema.validation import validate_schema_mapping
    from src.common.json_codec import loads_json

    manifest.require_capability("storage")
    schema = validate_schema_mapping(payload, manifest=manifest)
    template = require_approved_canonical_schema(loads_json(manifest.path("canonical_schema").read_text(encoding="utf-8")))
    if template["vertical"] != manifest.vertical or template["output"]["cardinality"] != manifest.documents.output_cardinality:
        raise ValueError("Approved mapping does not match selected manifest.")
    mapped = {field["name"]: field for field in template["fields"]}
    identities = set(template["identity"].values())
    fields = []
    for field in schema["fields"]:
        name = field["name"]
        previous = mapped.get(name)
        compatible = previous and (previous["type"] == field["type"] or {previous["type"], field["type"]} <= {"string", "enum"})
        if name in identities and not compatible:
            raise ValueError(f"Identity field {name!r} conflicts with approved mapping.")
        fields.append({
            "name": name, "type": field["type"], "description": field["description"],
            "required": True if name in identities else field["required"],
            "nullable": name not in identities,
            "values": list(field["values"]) if field["type"] == "enum" else [],
            "aliases": [],
            "storage": deepcopy(previous["storage"]) if compatible else {"strategy": "jsonb"},
        })
    candidate = deepcopy(template)
    candidate.update(version=f"{schema['version']}-canonical-candidate", status="candidate", review=None,
                     description=f"Canonical candidate derived from {schema['version']}: {schema['description']}", fields=fields)
    return validate_canonical_schema(candidate)
