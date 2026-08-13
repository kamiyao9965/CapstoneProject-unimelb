"""Shared helpers for writing field proposals into schema JSON data."""

from __future__ import annotations

from src.refine.candidates.aggregator import FieldDecision
from src.refine.candidates.patch import MANUAL_EDIT_PATCH_TYPES
from src.schema.validation import CORE_REQUIRED_FIELDS

VALID_PRODUCT_TYPES = ("hospital", "extras", "generalhealth", "combined")


def fields_by_name(fields: object) -> dict[str, dict[str, object]]:
    """Return validated schema fields keyed by name without silent repair."""
    if not isinstance(fields, list):
        raise ValueError("Schema fields must be a list.")
    result: dict[str, dict[str, object]] = {}
    for index, field in enumerate(fields):
        if not isinstance(field, dict):
            raise ValueError(f"Schema field {index} must be an object.")
        name = field.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"Schema field {index} must have a non-empty string name.")
        if name in result:
            raise ValueError(f"Schema contains duplicate field name: {name}")
        result[name] = field
    return result


def applies_to_from_group(target_group: str) -> list[str]:
    """Convert consensus group names to schema product types.

    Group names like `extras_cover` are useful during consensus, but schema
    `applies_to` should only contain product types such as `extras`.
    """
    candidate = target_group.removesuffix("_cover")
    return [candidate] if candidate in VALID_PRODUCT_TYPES else []


def field_payload_from_decision(
    decision: FieldDecision,
    existing_field: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build the schema field payload implied by one consensus decision."""
    payload = dict(existing_field or {})
    patch_types = set(decision.patch_types)
    if patch_types == {"update_description"}:
        payload["description"] = decision.description
    elif patch_types == {"add_alias"}:
        payload["aliases"] = sorted(
            set(payload.get("aliases") or []) | set(decision.aliases)
        )
    else:
        payload.update(
            {
                "name": decision.canonical_name,
                "type": payload.get("type") or decision.field_type,
                "description": payload.get("description") or decision.description,
                "applies_to": payload.get("applies_to")
                or decision.applies_to
                or applies_to_from_group(decision.target_group),
                "required": payload.get(
                    "required",
                    decision.required if decision.required is not None else False,
                ),
                "values": payload.get("values", decision.values),
                "aliases": payload.get("aliases", decision.aliases),
            }
        )
    normalize_required_flag(payload)
    return payload


def normalize_required_flag(field: dict[str, object]) -> None:
    """Only identity fields should force a cross-document non-null value."""
    name = field.get("name")
    if isinstance(name, str) and name not in CORE_REQUIRED_FIELDS:
        field["required"] = False


def decision_requires_manual_edit(decision: FieldDecision) -> bool:
    """Return whether collapsed patch semantics are unsafe to apply unattended."""
    patch_types = set(decision.patch_types)
    return (
        len(patch_types) != 1
        or bool(MANUAL_EDIT_PATCH_TYPES.intersection(patch_types))
    )


def decision_requires_schema_edit(decision: FieldDecision) -> bool:
    """Return whether the action cannot be represented as one field upsert."""
    return bool(MANUAL_EDIT_PATCH_TYPES.intersection(decision.patch_types))
