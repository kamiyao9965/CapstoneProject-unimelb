"""Vertical-specific contracts and validation used by consensus review."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path

from src.schema.validation import (
    PET_FIELD_TARGETS,
    validate_field_payload,
    validate_pet_schema_mapping,
    validate_schema_mapping,
)


SUPPORTED_REVIEW_VERTICALS = frozenset({"private_health", "pet_insurance"})


def ensure_review_vertical(vertical: str) -> str:
    if vertical not in SUPPORTED_REVIEW_VERTICALS:
        raise ValueError(f"Unsupported review vertical: {vertical!r}.")
    return vertical


def contract_name(vertical: str, artifact: str) -> str:
    return f"{ensure_review_vertical(vertical)}/{artifact}"


def default_alias_config(vertical: str) -> Path:
    root = Path(__file__).resolve().parents[2]
    return root / "configs" / ensure_review_vertical(vertical) / "aliases.json"


def schema_field_targets(schema: Mapping[str, object]) -> set[str]:
    vertical = ensure_review_vertical(str(schema.get("vertical") or ""))
    if vertical == "pet_insurance":
        scopes = schema.get("cover_scopes")
        return set(PET_FIELD_TARGETS) | {
            str(scope) for scope in scopes or [] if isinstance(scope, str)
        }
    product_types = schema.get("product_types")
    return {str(value) for value in product_types or [] if isinstance(value, str)}


def core_required_fields(schema: Mapping[str, object]) -> frozenset[str]:
    if ensure_review_vertical(str(schema.get("vertical") or "")) == "pet_insurance":
        return frozenset({"product_id", "product_name", "insurer_name"})
    return frozenset({"product_type", "product_name", "fund_name", "insurer_name"})


def validate_schema(schema: object) -> dict[str, object]:
    if not isinstance(schema, dict):
        raise ValueError("Schema must be an object.")
    if schema.get("vertical") == "pet_insurance":
        return validate_pet_schema_mapping(schema)
    return validate_schema_mapping(schema)


def validate_review_field(payload: object, schema: Mapping[str, object]) -> None:
    vertical = ensure_review_vertical(str(schema.get("vertical") or ""))
    targets: Collection[str] = schema_field_targets(schema)
    validate_field_payload(
        payload,
        targets,
        core_required_fields=core_required_fields(schema),
        applies_to_label=("pet applicability targets" if vertical == "pet_insurance" else "product types"),
    )
