"""Shared helpers for writing field proposals into schema YAML."""

from __future__ import annotations

from copy import deepcopy

from src.refine.candidates.aggregator import FieldDecision

VALID_PRODUCT_TYPES = ("hospital", "extras", "generalhealth", "combined")


def fields_by_name(fields: object) -> dict[str, dict[str, object]]:
    """Return schema fields keyed by name, ignoring malformed entries."""
    if not isinstance(fields, list):
        return {}
    result: dict[str, dict[str, object]] = {}
    for field in fields:
        if isinstance(field, dict) and field.get("name"):
            result[str(field["name"])] = field
    return result


def applies_to_from_group(target_group: str) -> list[str]:
    """Convert consensus group names to schema product types.

    Group names like `extras_cover` are useful during consensus, but schema
    `applies_to` should only contain product types such as `extras`.
    """
    candidate = target_group.removesuffix("_cover")
    return [candidate] if candidate in VALID_PRODUCT_TYPES else []


def sanitize_field_payload(payload: dict[str, object]) -> dict[str, object]:
    """Return a copy whose applies_to contains only valid product types."""
    sanitized = deepcopy(payload)
    applies_to = sanitized.get("applies_to", [])
    if not isinstance(applies_to, list):
        applies_to = []
    sanitized["applies_to"] = [
        value for value in applies_to if value in VALID_PRODUCT_TYPES
    ]
    return sanitized


def field_payload_from_decision(
    decision: FieldDecision,
    existing_field: dict[str, object] | None = None,
    include_consensus: bool = False,
) -> dict[str, object]:
    """Build the schema field payload implied by one consensus decision."""
    payload = dict(existing_field or {})
    payload.update(
        {
            "name": decision.canonical_name,
            "type": payload.get("type") or decision.field_type,
            "description": payload.get("description") or decision.description,
            "applies_to": payload.get("applies_to")
            or applies_to_from_group(decision.target_group),
            "required": payload.get("required", decision.decision == "core"),
            "values": payload.get("values", []),
        }
    )
    payload = sanitize_field_payload(payload)
    if include_consensus:
        payload["consensus"] = {
            **dict(payload.get("consensus") or {}),
            "decision": decision.decision,
            "frequency": decision.frequency_label,
            "average_confidence": round(decision.average_confidence, 3),
            "aliases": decision.aliases,
            "source_runs": decision.source_runs,
        }
    return payload
