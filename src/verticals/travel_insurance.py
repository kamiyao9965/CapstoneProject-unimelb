"""Travel-insurance-specific schema invariants."""

from __future__ import annotations

from collections.abc import Mapping

from src.schema.canonical import validate_canonical_schema
from src.schema.validation import (
    validate_field_payload,
    validate_product_type_field,
)


SUPPORTED_TRAVEL_PRODUCT_TYPES = frozenset(
    {
        "international_single_trip",
        "international_multi_trip",
        "domestic",
        "inbound",
        "business",
        "cruise",
    }
)

TRAVEL_EXTENSION_COLUMNS = frozenset(
    {
        "plan_name",
        "geographic_scope",
        "trip_frequency",
        "customer_segment",
        "cruise_cover_available",
        "max_trip_duration_days",
        "overall_policy_duration_days",
        "age_eligibility_min",
        "age_eligibility_max",
        "rental_vehicle_excess_limit_aud",
        "personal_liability_limit_aud",
        "baggage_overall_limit_aud",
    }
)


def build_travel_canonical_candidate(payload: object) -> dict[str, object]:
    """Mechanically map a reviewed Travel schema to a candidate DB contract."""
    schema = validate_travel_schema_mapping(payload)
    fields = schema["fields"]
    if not isinstance(fields, list):
        raise ValueError("Travel schema fields must be a list.")
    by_name = {
        str(field["name"]): field
        for field in fields
        if isinstance(field, Mapping)
    }
    product_name = by_name.get("product_name")
    if product_name is None:
        raise ValueError("Travel Canonical Schema requires a product_name field.")
    product_type = schema["product_type_field"]
    if not isinstance(product_type, Mapping):
        raise ValueError("Travel product_type_field must be an object.")

    canonical_fields = [
        _canonical_field(product_name, identity="product_name"),
        _canonical_field(product_type, identity="product_type"),
    ]
    canonical_fields.extend(
        _canonical_field(field)
        for field in fields
        if isinstance(field, Mapping) and field.get("name") != "product_name"
    )
    candidate = {
        "contract_version": "1.0.0",
        "vertical": "travel_insurance",
        "version": f"{schema['version']}-canonical-candidate",
        "status": "candidate",
        "description": f"Canonical candidate derived from {schema['version']}: {schema['description']}",
        "review": None,
        "output": {
            "collection": "products",
            "cardinality": "multiple",
            "document_notes_field": "_document_notes",
        },
        "identity": {
            "product_name_field": "product_name",
            "product_type_field": "product_type",
        },
        "extension": {
            "entity": "travel_product_details",
            "table": "travel_product_details",
            "attributes_column": "attributes",
        },
        "fields": canonical_fields,
    }
    return validate_canonical_schema(candidate)


def _canonical_field(
    field: Mapping[str, object],
    *,
    identity: str | None = None,
) -> dict[str, object]:
    name = str(field["name"])
    field_type = str(field["type"])
    values = list(field.get("values") or []) if field_type == "enum" else []
    if any(not isinstance(value, str) for value in values):
        raise ValueError(f"Canonical enum field {name!r} values must be strings.")
    if identity == "product_name":
        storage = {"strategy": "core_column", "target": "products.canonical_name"}
    elif identity == "product_type":
        storage = {
            "strategy": "core_column",
            "target": "product_releases.source_product_type",
        }
    elif name in TRAVEL_EXTENSION_COLUMNS and field_type != "list[object]":
        storage = {"strategy": "extension_column", "column": name}
    else:
        storage = {"strategy": "jsonb"}
    return {
        "name": name,
        "type": field_type,
        "description": str(field["description"]),
        "required": True if identity else bool(field.get("required")),
        "nullable": False if identity else True,
        "values": values,
        "aliases": list(field.get("aliases") or []),
        "storage": storage,
    }


def validate_travel_schema_mapping(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("Schema JSON must be an object.")
    if payload.get("vertical") != "travel_insurance":
        raise ValueError("Schema vertical must be 'travel_insurance'.")

    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("Schema version must be a non-empty string.")

    product_types = payload.get("product_types")
    if not isinstance(product_types, list) or not product_types:
        raise ValueError("Schema product_types must be a non-empty list.")
    if any(not isinstance(value, str) for value in product_types):
        raise ValueError("Schema product_types must contain strings only.")
    unknown = set(product_types) - SUPPORTED_TRAVEL_PRODUCT_TYPES
    if unknown:
        raise ValueError(
            "Schema contains unknown travel product types: "
            + ", ".join(sorted(unknown))
        )
    if len(product_types) != len(set(product_types)):
        raise ValueError("Schema product_types must not contain duplicates.")

    product_type_field = payload.get("product_type_field")
    validate_field_payload(
        product_type_field,
        set(product_types),
    )
    validate_product_type_field([product_type_field], product_types)

    fields = payload.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ValueError("Schema fields must be a non-empty list.")
    field_names: set[str] = set()
    for index, field in enumerate(fields):
        validated = validate_field_payload(field, set(product_types), index=index)
        name = str(validated["name"])
        if name == "product_type":
            raise ValueError(
                "Schema product_type is reserved for product_type_field."
            )
        if name in field_names:
            raise ValueError(f"Schema contains duplicate field name: {name}")
        field_names.add(name)
    _validate_coverage_categories(payload.get("coverage_categories"))
    return payload


def _validate_coverage_categories(payload: object) -> None:
    if not isinstance(payload, list):
        raise ValueError("Schema coverage_categories must be a list.")
    names: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("Each coverage category must be an object.")
        name = item.get("canonical_name")
        if not isinstance(name, str) or not name:
            raise ValueError("Each coverage category needs a canonical_name.")
        if name in names:
            raise ValueError(f"Duplicate coverage category: {name}")
        names.add(name)
