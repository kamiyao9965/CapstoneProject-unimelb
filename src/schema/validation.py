"""Shared validation for discovered schemas and extraction records."""

from __future__ import annotations

import re
from copy import deepcopy

from src.common.json_contracts import validate_contract
from src.verticals.manifest import VerticalManifest, default_manifest_path, load_vertical_manifest
from collections.abc import Collection, Mapping
from typing import TypeAlias

SUPPORTED_FIELD_TYPES = frozenset(
    {"string", "number", "boolean", "enum", "list[object]"}
)
SNAKE_CASE_NAME = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
JSONScalar: TypeAlias = str | int | float | bool


def normalize_schema(payload: object, manifest: VerticalManifest | None = None) -> dict[str, object]:
    """Validate and copy either actual legacy JSON shape into the common model."""
    if not isinstance(payload, dict):
        raise ValueError("Schema JSON must be an object.")
    manifest = manifest or load_vertical_manifest(default_manifest_path(str(payload.get("vertical"))))
    if payload.get("vertical") != manifest.vertical:
        raise ValueError(f"Schema vertical must be {manifest.vertical!r}.")
    validate_contract(payload, manifest.contract("discovered_schema"), manifest=manifest)
    schema = deepcopy(payload)
    if "taxonomies" not in schema:
        schema["taxonomies"] = {name: schema.pop(name) for name in manifest.taxonomies}
        classifier = schema.pop("product_type_field", None)
        if classifier is not None:
            schema["fields"].insert(0, classifier)
        # Legacy aliases remain in the source artifact, never in engine decisions.
        for field in schema["fields"]:
            field.pop("aliases", None)
        for entries in schema["taxonomies"].values():
            for entry in entries:
                entry.pop("aliases", None)
    return schema


def validate_schema_mapping(payload: object, *, manifest: VerticalManifest | None = None) -> dict[str, object]:
    """One set of field/classification invariants for configured verticals."""
    if not isinstance(payload, dict):
        raise ValueError("Schema JSON must be an object.")
    manifest = manifest or load_vertical_manifest(default_manifest_path(str(payload.get("vertical"))))
    if payload.get("vertical") != manifest.vertical:
        raise ValueError(f"Schema vertical must be {manifest.vertical!r}.")
    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("Schema version must be a non-empty string.")
    product_types = payload.get("product_types")
    if not isinstance(product_types, list) or not product_types or any(not isinstance(v, str) for v in product_types):
        raise ValueError("Schema product_types must be a non-empty list of strings.")
    unknown = set(product_types) - set(manifest.product_types)
    if unknown:
        raise ValueError("Schema contains unknown product types: " + ", ".join(sorted(unknown)))
    if len(product_types) != len(set(product_types)):
        raise ValueError("Schema product_types must not contain duplicates.")
    fields = payload.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ValueError("Schema fields must be a non-empty list.")
    fields = [*([payload["product_type_field"]] if "product_type_field" in payload else []), *fields]
    by_name = {}
    for index, field in enumerate(fields):
        validated = validate_field_payload(field, product_types, index=index)
        name = str(validated["name"])
        if name in by_name:
            raise ValueError(f"Schema contains duplicate field name: {name}")
        by_name[name] = field
    validate_product_type_field(fields, product_types)
    for name in manifest.identity_fields:
        if name not in by_name or by_name[name]["type"] != "string":
            raise ValueError(f"Schema identity field {name!r} must reference a string field.")
    taxonomies = payload.get("taxonomies", {name: payload.get(name) for name in manifest.taxonomies})
    if not isinstance(taxonomies, dict) or set(taxonomies) != set(manifest.taxonomies):
        raise ValueError("Schema taxonomy sets must match the manifest.")
    for name, entries in taxonomies.items():
        if not isinstance(entries, list):
            raise ValueError(f"Schema taxonomy {name!r} must be a list.")
        names = [item.get("canonical_name") for item in entries if isinstance(item, dict)]
        if len(names) != len(entries) or any(not isinstance(v, str) or not v for v in names):
            raise ValueError(f"Schema taxonomy {name!r} entries need canonical_name.")
        if len(names) != len(set(names)):
            raise ValueError(f"Duplicate taxonomy category in {name!r}.")
    return normalize_schema(payload, manifest)


def validate_product_type_field(
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

    aliases = payload.get("aliases", [])
    if (
        not isinstance(aliases, list)
        or any(not isinstance(alias, str) or not alias.strip() for alias in aliases)
    ):
        raise ValueError(f"Schema field {name!r} aliases must be a list of strings.")
    return payload


def validate_extraction_record(schema: dict[str, object], payload: object, *, manifest: VerticalManifest) -> None:
    """Check applicability and document-local product identities after JSON validation."""
    records = payload["products"] if manifest.documents.output_cardinality == "multiple" else [payload]
    identities = set()
    for record in records:
        product_type = record.get("product_type")
        for field in schema["fields"]:
            if product_type is not None and product_type not in field["applies_to"] and record.get(field["name"]) is not None:
                raise ValueError(f"Field {field['name']!r} is not applicable to product_type {product_type!r}.")
        if manifest.identity_fields:
            identity = tuple(record.get(name) for name in manifest.identity_fields)
            if any(not isinstance(value, str) or not value.strip() for value in identity):
                raise ValueError("Product identity fields must have non-empty values.")
            identity = tuple(value.strip().casefold() for value in identity)
            if identity in identities:
                raise ValueError("Duplicate product identity within one document.")
            identities.add(identity)
