"""Shared helpers for writing field proposals into schema JSON data."""

from __future__ import annotations

from src.refine.candidates.aggregator import FieldDecision
from src.refine.candidates.patch import MANUAL_EDIT_PATCH_TYPES

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


def applies_to_from_group(
    target_group: str,
    valid_product_types: tuple[str, ...] = VALID_PRODUCT_TYPES,
) -> list[str]:
    """Convert consensus group names to schema product types.

    Group names like `extras_cover` are useful during consensus, but schema
    `applies_to` should only contain product types such as `extras`.
    """
    candidate = target_group.removesuffix("_cover")
    return [candidate] if candidate in valid_product_types else []


def field_payload_from_decision(
    decision: FieldDecision,
    existing_field: dict[str, object] | None = None,
    valid_product_types: tuple[str, ...] = VALID_PRODUCT_TYPES,
) -> dict[str, object]:
    """Build the schema field payload implied by one consensus decision."""
    payload = dict(existing_field or {})
    patch_types = set(decision.patch_types)
    if patch_types == {"update_description"}:
        payload["description"] = decision.description
    elif "add_alias" in patch_types:
        raise ValueError("Historical add_alias operations are read-only and cannot be applied.")
    else:
        payload.update(
            {
                "name": decision.canonical_name,
                "type": payload.get("type") or decision.field_type,
                "description": payload.get("description") or decision.description,
                "applies_to": payload.get("applies_to")
                or decision.applies_to
                or applies_to_from_group(decision.target_group, valid_product_types),
                "required": payload.get(
                    "required",
                    decision.required if decision.required is not None else False,
                ),
                # Existing contracts carry these keys even when the arrays are
                # empty, so get(key, default) would discard consensus votes.
                "values": payload.get("values") or decision.values,
            }
        )
    payload.pop("aliases", None)
    return payload


def decision_requires_manual_edit(decision: FieldDecision) -> bool:
    """Return whether collapsed patch semantics are unsafe to apply unattended."""
    patch_types = set(decision.patch_types)
    return (
        len(patch_types) != 1
        or "add_alias" in patch_types
        or bool(MANUAL_EDIT_PATCH_TYPES.intersection(patch_types))
    )


def decision_requires_schema_edit(decision: FieldDecision) -> bool:
    """Return whether the action cannot be represented as one field upsert."""
    return bool(MANUAL_EDIT_PATCH_TYPES.intersection(decision.patch_types))


def decision_is_auto_promotable(
    decision: FieldDecision,
    promoted_decisions: frozenset[str] = frozenset({"core", "conditional"}),
    protected_fields: frozenset[str] = frozenset(),
) -> bool:
    """Return whether configured support and safety rules allow auto-promotion."""
    return (
        decision.decision in promoted_decisions
        and decision.reject_votes == 0
        and not decision.has_conflict
        and decision.canonical_name not in protected_fields
        and not decision_requires_manual_edit(decision)
    )
