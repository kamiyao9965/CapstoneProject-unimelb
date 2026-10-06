"""Compile discovered schemas into runtime extraction JSON Schemas."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy

from src.common.json_contracts import validate_contract
from src.schema.validation import normalize_schema, validate_schema_mapping


def compile_extraction_contract(
    schema: Mapping[str, object],
    *,
    data_contract: str | None = None,
    business_validator: Callable[[object], object] | None = None,
    output_cardinality: str | None = None,
    manifest=None,
) -> dict[str, object]:
    from src.verticals.manifest import resolve_manifest

    manifest = manifest or resolve_manifest(vertical=schema.get("vertical"))
    data_contract = data_contract or manifest.contract("discovered_schema")
    output_cardinality = output_cardinality or manifest.documents.output_cardinality
    if output_cardinality != manifest.documents.output_cardinality:
        raise ValueError("Output cardinality conflicts with the selected manifest.")
    validate_contract(schema, data_contract, manifest=manifest)
    if business_validator:
        business_validator(schema)
    else:
        validate_schema_mapping(schema, manifest=manifest)
    product_contract = _compile_product_contract(normalize_schema(dict(schema), manifest))
    if output_cardinality == "single":
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            **product_contract,
        }
    if output_cardinality == "multiple":
        contract = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "required": ["products", "_document_notes"],
            "properties": {
                "products": {
                    "type": "array",
                    "minItems": 1,
                    "items": product_contract,
                },
                "_document_notes": {"type": ["string", "null"]},
            },
        }
        if schema.get('validation_profile') == 'car_insurance.review_v2':
            from src.car_insurance.schema_revision import document_evidence_contract
            contract['properties']['document_evidence'] = document_evidence_contract()
            contract['required'].append('document_evidence')
        elif schema.get('validation_profile') in {'car_insurance.review_v3', 'car_insurance.review_v4', 'car_insurance.review_v5'}:
            contract['$defs'] = deepcopy(schema['$defs'])
            contract['properties']['document_evidence'] = deepcopy(schema['document_evidence_schema'])
            contract['required'].append('document_evidence')
            if schema.get('validation_profile') == 'car_insurance.review_v5':
                from src.car_insurance.schema_revision_v5 import shared_rules_contract
                contract['properties']['shared_policy_rules'] = shared_rules_contract(schema)
                contract['required'].append('shared_policy_rules')
        return contract
    raise ValueError(
        "output_cardinality must be either 'single' or 'multiple'."
    )


def _compile_product_contract(
    schema: Mapping[str, object],
) -> dict[str, object]:
    properties: dict[str, object] = {}
    field_names: list[str] = []
    fields = list(schema["fields"])
    for field in fields:
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
        if 'item_schema' in field:
            return {'type':['array','null'],'items':deepcopy(field['item_schema'])}
        return {
            "oneOf": [
                {"type": "null"},
                {
                    "type": "array",
                    "items": deepcopy(field.get('item_schema', {"type": "object", "additionalProperties": True})),
                },
            ]
        }
    raise ValueError(f"Unsupported extraction field type: {field_type}")
