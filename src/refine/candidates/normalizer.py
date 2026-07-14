"""Canonical field/group names for consensus refinement.

Alias maps are injectable; the private_health defaults live in
configs/private_health/aliases.json so future verticals can supply their own
file without code changes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

from src.common.json_codec import loads_json
from src.common.json_contracts import validate_contract
from src.refine.candidates.patch import SchemaPatch

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ALIAS_CONFIG = PROJECT_ROOT / "configs" / "private_health" / "aliases.json"

def load_alias_config(
    path: str | Path | None = None,
) -> tuple[Mapping[str, str], Mapping[str, str]]:
    """Load (field_aliases, group_aliases) from a JSON config.

    The tracked JSON file is the single authoritative default alias source.
    """
    resolved = Path(path) if path else DEFAULT_ALIAS_CONFIG
    if not resolved.exists():
        raise FileNotFoundError(resolved)

    try:
        payload = loads_json(resolved.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"Alias config is not valid JSON: {resolved}: {exc}") from exc
    validate_contract(payload, "private_health/aliases")
    if not isinstance(payload, dict):
        raise ValueError(f"Alias config must be a JSON object: {resolved}")
    return (
        _string_map(payload.get("field_aliases"), resolved),
        _string_map(payload.get("group_aliases"), resolved),
    )


def _string_map(value: object, source: Path) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Alias sections must be JSON objects: {source}")
    return {str(key): str(val) for key, val in value.items()}


def normalize_patch(
    patch: SchemaPatch,
    field_aliases: Mapping[str, str] | None = None,
    group_aliases: Mapping[str, str] | None = None,
) -> SchemaPatch:
    canonical_name = canonical_field_name(
        patch.canonical_name or patch.field_name, field_aliases
    )
    return SchemaPatch(
        patch_type=patch.patch_type,
        target_group=canonical_group_name(patch.target_group, group_aliases),
        field_name=_snake_case(patch.field_name),
        canonical_name=canonical_name,
        field_type=patch.field_type,
        description=patch.description,
        evidence_documents=patch.evidence_documents,
        confidence=patch.confidence,
        rationale=patch.rationale,
        source_run=patch.source_run,
        applies_to=patch.applies_to,
        required=patch.required,
        values=patch.values,
    )


def normalize_patches(
    patches: list[SchemaPatch],
    field_aliases: Mapping[str, str] | None = None,
    group_aliases: Mapping[str, str] | None = None,
) -> list[SchemaPatch]:
    return [normalize_patch(patch, field_aliases, group_aliases) for patch in patches]


def canonical_field_name(
    value: str, aliases: Mapping[str, str] | None = None
) -> str:
    name = _snake_case(value)
    resolved_aliases = load_alias_config()[0] if aliases is None else aliases
    return resolved_aliases.get(name, name)


def canonical_group_name(
    value: str, aliases: Mapping[str, str] | None = None
) -> str:
    name = _snake_case(value)
    resolved_aliases = load_alias_config()[1] if aliases is None else aliases
    return resolved_aliases.get(name, name)


def _snake_case(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z]+", "_", value.strip())
    cleaned = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_").lower()
    return cleaned
