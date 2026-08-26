"""Validation boundary for model-generated private-health schema contracts."""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Mapping
from typing import TypeAlias

from src.schema.migration import SAMPLE_SOURCE_LOCATION

SUPPORTED_FIELD_TYPES = frozenset(
    {
        "string", "number", "boolean", "enum",
        "list[string]", "list[number]", "list[boolean]", "list[enum]",
        "list[object]",
    }
)
SUPPORTED_ITEM_FIELD_TYPES = frozenset({"string", "number", "boolean", "enum"})
ENUM_REF_REGISTRY = {
    "product_types": "scalar_list",
    "hospital_categories": "canonical_items",
    "extras_services": "canonical_items",
}
SUPPORTED_ENUM_REFS = frozenset(ENUM_REF_REGISTRY)
RESERVED_OUTPUT_FIELD_NAMES = frozenset({"_unfilled", "_notes"})
CANONICAL_ITEM_FIELD_POLICIES: dict[str, dict[str, object]] = {
    "extras_benefits": {
        "legacy_item_names": {"service": "service_name", "name": "service_name"},
        "required_item_fields": {
            "service_name": {
                "type": "enum", "required": True, "values": [],
                "enum_ref": "extras_services",
            },
        },
    },
    "clinical_categories": {
        "required_item_fields": {
            "category": {
                "type": "enum", "required": True, "values": [],
                "enum_ref": "hospital_categories",
            },
            "coverage": {
                "type": "enum", "required": True,
                "values": ["included", "restricted", "excluded"],
                "enum_ref": None,
            },
        },
    },
    "hospital_waiting_periods": {
        "required_item_fields": {
            "service": {
                "type": "string", "required": True, "values": [],
                "enum_ref": None,
            },
        },
    },
    "extras_waiting_periods": {
        "legacy_item_names": {"service": "service_name", "name": "service_name"},
        "required_item_fields": {
            "service_name": {
                "type": "enum", "required": True, "values": [],
                "enum_ref": "extras_services",
            },
        },
    },
    "other_annual_limits": {
        "required_item_fields": {
            "limit_name": {
                "type": "string", "required": True, "values": [],
                "enum_ref": None,
            },
        },
    },
    "adjacent_hospital_benefits": {
        "required_item_fields": {
            "benefit_type": {
                "type": "string", "required": True, "values": [],
                "enum_ref": None,
            },
        },
    },
    "ambulance_coverage": {
        "required_item_fields": {
            "annual_trip_limit_per_person": {
                "type": "number", "required": False, "values": [],
                "enum_ref": None,
            },
            "annual_trip_limit_per_policy": {
                "type": "number", "required": False, "values": [],
                "enum_ref": None,
            },
        },
    },
}
SUPPORTED_PRODUCT_TYPES = frozenset(
    {"hospital", "extras", "generalhealth", "combined"}
)
CORE_REQUIRED_FIELDS = frozenset({"product_type", "product_name", "fund_name", "insurer_name"})
SNAKE_CASE_NAME = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
JSONScalar: TypeAlias = str | int | float | bool


class SchemaValidationError(ValueError):
    """One or more independently detected schema business-rule failures."""

    def __init__(self, errors: list[dict[str, str]]) -> None:
        self.errors = tuple(errors)
        super().__init__("; ".join(error["message"] for error in errors))


class CanonicalItemPolicyError(ValueError):
    """A canonical list-item invariant failed with a deterministic repair."""

    def __init__(self, message: str, repair_hint: str) -> None:
        self.repair_hint = repair_hint
        super().__init__(message)


class ReusableDescriptionError(ValueError):
    """A reusable schema description contains sample-location evidence."""

    def __init__(self, label: str) -> None:
        self.repair_hint = (
            f"Rewrite {label} as a reusable semantic definition without PDF names, "
            "page numbers, page tokens such as p3 or p2_t1, or table_id markers. "
            "Keep useful general examples such as e.g. business concepts. Move "
            "sample source locations to top-level notes for discovery, or to "
            "evidence_documents/rationale for refinement."
        )
        super().__init__(
            f"{label} contains a sample-specific page or table location."
        )


def canonical_item_policy_prompt() -> str:
    """Render model instructions from the same policies used by validation."""
    lines = [
        "Canonical list[object] item invariants are mandatory and override sample wording:"
    ]
    for field_name, policy in CANONICAL_ITEM_FIELD_POLICIES.items():
        for item_name, shape in _policy_required_items(policy).items():
            lines.append(
                f"- {field_name}.{item_name} must use "
                f"{_canonical_shape_text(shape)}."
            )
        legacy_names = _policy_legacy_names(policy)
        if legacy_names:
            replacements = ", ".join(
                f"{old_name}->{new_name}"
                for old_name, new_name in sorted(legacy_names.items())
            )
            lines.append(f"- {field_name} legacy item names are forbidden: {replacements}.")
    return "\n".join(lines)


def validate_schema_mapping(payload: object) -> dict[str, object]:
    """Validate a parsed schema and return it with a precise mapping type."""
    if not isinstance(payload, dict):
        raise ValueError("Schema JSON must be an object.")
    if payload.get("vertical") != "private_health":
        raise ValueError("Schema vertical must be 'private_health'.")
    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("Schema version must be a non-empty string.")
    description = payload.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("Schema description must be a non-empty string.")
    validate_reusable_description(description, "Schema description")

    product_types = payload.get("product_types")
    if not isinstance(product_types, list) or not product_types:
        raise ValueError("Schema product_types must be a non-empty list.")
    if any(not isinstance(value, str) for value in product_types):
        raise ValueError("Schema product_types must contain strings only.")
    unknown_product_types = set(product_types) - SUPPORTED_PRODUCT_TYPES
    if unknown_product_types:
        raise ValueError(
            "Schema contains unknown product types: "
            + ", ".join(sorted(unknown_product_types))
        )
    if len(product_types) != len(set(product_types)):
        raise ValueError("Schema product_types must not contain duplicates.")

    fields = payload.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ValueError("Schema fields must be a non-empty list.")
    errors: list[dict[str, str]] = []
    field_names: set[str] = set()
    allowed_product_types = set(product_types)
    for index, field in enumerate(fields):
        try:
            validate_field_payload(field, allowed_product_types, index=index)
        except ValueError as exc:
            error = {"path": f"$.fields[{index}]", "message": str(exc)}
            repair_hint = getattr(exc, "repair_hint", None)
            if isinstance(repair_hint, str) and repair_hint:
                error["repair_hint"] = repair_hint
            errors.append(error)
        if isinstance(field, dict) and isinstance(field.get("name"), str):
            name = str(field["name"])
            if name in field_names:
                errors.append({
                    "path": f"$.fields[{index}].name",
                    "message": f"Schema contains duplicate field name: {name}",
                })
            field_names.add(name)
    for validator, path in (
        (lambda: _validate_product_type_field(fields, product_types), "$.fields"),
        (lambda: _validate_enum_references(fields, payload), "$.fields"),
        (lambda: _validate_ambulance_single_source(fields, payload), "$.extras_services"),
    ):
        try:
            validator()
        except ValueError as exc:
            errors.append({"path": path, "message": str(exc)})
    if errors:
        raise SchemaValidationError(errors)
    return payload


def _validate_ambulance_single_source(
    fields: list[object], schema: Mapping[str, object]
) -> None:
    field_names = {
        field.get("name") for field in fields if isinstance(field, dict)
    }
    if "ambulance_coverage" not in field_names:
        return
    extras_services = schema.get("extras_services")
    if not isinstance(extras_services, list):
        return
    if any(
        isinstance(item, dict) and item.get("canonical_name") == "ambulance"
        for item in extras_services
    ):
        raise ValueError(
            "Schema with ambulance_coverage must not include 'ambulance' in "
            "extras_services; ambulance has one authoritative representation."
        )


def _validate_enum_references(
    fields: list[object], schema: Mapping[str, object]
) -> None:
    for field in fields:
        if not isinstance(field, dict):
            continue
        candidates = [field, *(field.get("item_fields") or [])]
        for candidate in candidates:
            if not isinstance(candidate, dict) or not candidate.get("enum_ref"):
                continue
            enum_ref = str(candidate["enum_ref"])
            source = schema.get(enum_ref)
            if enum_ref == "product_types":
                values = source if isinstance(source, list) else []
            else:
                values = [
                    item.get("canonical_name")
                    for item in source or []
                    if isinstance(item, dict) and item.get("canonical_name")
                ] if isinstance(source, list) else []
            if not values:
                raise ValueError(
                    f"Schema enum field {candidate.get('name')!r} references empty "
                    f"canonical set {enum_ref!r}."
                )
            if any(not isinstance(value, (str, int, float, bool)) for value in values):
                raise ValueError(
                    f"Schema canonical set {enum_ref!r} must contain scalar values only."
                )
            if len(values) != len(set(values)):
                raise ValueError(
                    f"Schema canonical set {enum_ref!r} must not contain duplicates."
                )


def _validate_product_type_field(
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
    if product_type.get("values") != []:
        raise ValueError(
            "Schema product_type values must be empty; use the top-level "
            "product_types canonical set through enum_ref."
        )
    if product_type.get("enum_ref") != "product_types":
        raise ValueError(
            "Schema product_type enum_ref must reference top-level product_types."
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
    _validate_canonical_name(name, f"Schema {label}")
    field_type = payload.get("type")
    if field_type not in SUPPORTED_FIELD_TYPES:
        raise ValueError(
            f"Schema field {name!r} has unsupported type {field_type!r}."
        )
    description = payload.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"Schema field {name!r} needs a non-empty description.")
    validate_reusable_description(
        description, f"Schema field {name!r} description"
    )

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
    if payload.get("required") is True and name not in CORE_REQUIRED_FIELDS:
        raise ValueError(
            f"Schema field {name!r} must not be marked required; "
            "only core product identity fields may be required."
        )
    values = payload.get("values")
    if not isinstance(values, list):
        raise ValueError(f"Schema field {name!r} values must be a list.")
    enum_ref = payload.get("enum_ref")
    if field_type in {"enum", "list[enum]"} and any(
        not isinstance(value, (str, int, float, bool)) for value in values
    ):
        raise ValueError(f"Schema enum field {name!r} values must be scalar.")
    _validate_enum_source(name, field_type, values, enum_ref)

    item_fields = payload.get("item_fields")
    if field_type == "list[object]":
        if not isinstance(item_fields, list) or not item_fields:
            raise ValueError(
                f"Schema list[object] field {name!r} must declare non-empty item_fields."
            )
        item_names: set[str] = set()
        for item_index, item_field in enumerate(item_fields):
            validated_item = validate_item_field_payload(
                item_field, field_name=name, index=item_index
            )
            item_name = str(validated_item["name"])
            if item_name in item_names:
                raise ValueError(
                    f"Schema field {name!r} contains duplicate item field name: {item_name}"
                )
            item_names.add(item_name)
        _validate_canonical_item_policy(str(name), item_fields)
    elif item_fields not in (None, []):
        raise ValueError(
            f"Schema non-object field {name!r} must not declare item_fields."
        )

    aliases = payload.get("aliases")
    if (
        not isinstance(aliases, list)
        or any(not isinstance(alias, str) or not alias.strip() for alias in aliases)
    ):
        raise ValueError(f"Schema field {name!r} aliases must be a list of strings.")
    unique_items = payload.get("unique_items")
    if not isinstance(unique_items, bool):
        raise ValueError(f"Schema field {name!r} unique_items must be boolean.")
    unique_capable_types = {
        "list[string]", "list[number]", "list[boolean]", "list[enum]"
    }
    if field_type not in unique_capable_types and unique_items:
        raise ValueError(
            f"Schema field {name!r} cannot enable unique_items for type {field_type!r}."
        )
    return payload


def validate_item_field_payload(
    payload: object, *, field_name: str, index: int
) -> Mapping[str, object]:
    """Validate one closed list[object] item field."""
    if not isinstance(payload, dict):
        raise ValueError(
            f"Schema field {field_name!r} item field {index} must be an object."
        )
    name = payload.get("name")
    _validate_canonical_name(
        name, f"Schema field {field_name!r} item field {index}"
    )
    field_type = payload.get("type")
    if field_type not in SUPPORTED_ITEM_FIELD_TYPES:
        raise ValueError(
            f"Schema item field {name!r} has unsupported type {field_type!r}."
        )
    if not isinstance(payload.get("required"), bool):
        raise ValueError(f"Schema item field {name!r} required must be boolean.")
    values = payload.get("values", [])
    if not isinstance(values, list):
        raise ValueError(f"Schema item field {name!r} values must be a list.")
    if any(not isinstance(value, (str, int, float, bool)) for value in values):
        raise ValueError(f"Schema enum item field {name!r} values must be scalar.")
    _validate_enum_source(name, str(field_type), values, payload.get("enum_ref"))
    description = payload.get("description")
    if description is not None and (
        not isinstance(description, str) or not description.strip()
    ):
        raise ValueError(
            f"Schema item field {name!r} description must be a non-empty string."
        )
    if isinstance(description, str):
        validate_reusable_description(
            description,
            f"Schema field {field_name!r} item field {name!r} description",
        )
    return payload


def validate_reusable_description(value: str, label: str) -> None:
    """Reject only explicit sample page/table locations, not general examples."""
    if SAMPLE_SOURCE_LOCATION.search(value):
        raise ReusableDescriptionError(label)


def _validate_enum_source(
    name: str, field_type: str, values: list[object], enum_ref: object
) -> None:
    is_enum = field_type in {"enum", "list[enum]"}
    if not is_enum:
        if values:
            raise ValueError(f"Schema non-enum field {name!r} values must be empty.")
        if enum_ref is not None:
            raise ValueError(f"Schema non-enum field {name!r} must not declare enum_ref.")
        return
    if bool(values) == bool(enum_ref):
        raise ValueError(
            f"Schema enum field {name!r} must declare exactly one of values or enum_ref."
        )
    if enum_ref is not None and enum_ref not in SUPPORTED_ENUM_REFS:
        raise ValueError(
            f"Schema enum field {name!r} references unknown canonical set {enum_ref!r}."
        )
    if len(values) != len(set((type(value).__name__, repr(value)) for value in values)):
        raise ValueError(f"Schema enum field {name!r} values must not contain duplicates.")


def _validate_canonical_item_policy(
    field_name: str, item_fields: object
) -> None:
    """Enforce stable row identifiers and shapes for known list fields."""
    policy = CANONICAL_ITEM_FIELD_POLICIES.get(field_name)
    if policy is None:
        return
    repair_hint = _canonical_item_policy_repair_hint(field_name, policy)
    if not isinstance(item_fields, list):
        raise CanonicalItemPolicyError(
            f"Schema field {field_name!r} must declare canonical item_fields.",
            repair_hint,
        )
    by_name = {
        item.get("name"): item
        for item in item_fields
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    for legacy_name, canonical_name in _policy_legacy_names(policy).items():
        if legacy_name in by_name:
            raise CanonicalItemPolicyError(
                f"Schema field {field_name!r} item field {legacy_name!r} is a "
                f"legacy alias; use {canonical_name!r}.",
                repair_hint,
            )
    for item_name, expected_shape in _policy_required_items(policy).items():
        actual = by_name.get(item_name)
        if actual is None:
            raise CanonicalItemPolicyError(
                f"Schema field {field_name!r} must contain canonical item field "
                f"{item_name!r}.",
                repair_hint,
            )
        mismatches = [
            key for key in ("type", "required", "values", "enum_ref")
            if actual.get(key) != expected_shape.get(key)
        ]
        if mismatches:
            raise CanonicalItemPolicyError(
                f"Schema field {field_name!r} item field {item_name!r} must use "
                f"the canonical shape; mismatched: {', '.join(mismatches)}.",
                repair_hint,
            )


def validate_canonical_item_policy(field_name: str, item_fields: object) -> None:
    """Public policy boundary for refinement and other schema producers."""
    _validate_canonical_item_policy(field_name, item_fields)


def _canonical_item_policy_repair_hint(
    field_name: str, policy: Mapping[str, object]
) -> str:
    required = "; ".join(
        f"{item_name}: {_canonical_shape_text(shape)}"
        for item_name, shape in _policy_required_items(policy).items()
    )
    legacy_names = _policy_legacy_names(policy)
    legacy = ""
    if legacy_names:
        replacements = ", ".join(
            f'"{old_name}" to "{new_name}"'
            for old_name, new_name in sorted(legacy_names.items())
        )
        legacy = f" Rename legacy item names {replacements}."
    return (
        f'In the field named exactly "{field_name}", enforce these canonical '
        f"item shapes: {required}.{legacy} Preserve all other valid item fields."
    )


def _canonical_shape_text(shape: Mapping[str, object]) -> str:
    values = json.dumps(shape.get("values", []), ensure_ascii=False)
    enum_ref = shape.get("enum_ref")
    enum_ref_text = json.dumps(enum_ref, ensure_ascii=False)
    field_type = json.dumps(shape.get("type"), ensure_ascii=False)
    required = "true" if shape.get("required") is True else "false"
    return (
        f"type={field_type}, required={required}, values={values}, "
        f"enum_ref={enum_ref_text}"
    )


def _policy_required_items(
    policy: Mapping[str, object]
) -> Mapping[str, Mapping[str, object]]:
    required = policy.get("required_item_fields")
    return required if isinstance(required, dict) else {}


def _policy_legacy_names(policy: Mapping[str, object]) -> Mapping[str, str]:
    legacy_names = policy.get("legacy_item_names")
    return legacy_names if isinstance(legacy_names, dict) else {}


def _validate_canonical_name(value: object, label: str) -> None:
    if isinstance(value, str) and value in RESERVED_OUTPUT_FIELD_NAMES:
        raise ValueError(f"{label} name {value!r} is reserved for runtime output metadata.")
    if not isinstance(value, str) or not SNAKE_CASE_NAME.fullmatch(value):
        raise ValueError(f"{label} name must be canonical snake_case.")
