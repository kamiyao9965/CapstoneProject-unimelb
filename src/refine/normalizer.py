"""Canonical field/group names for consensus refinement.

Ported from the `testing` branch (src/schema/normalizer.py) per
docs/BRANCH_FUSION_PLAN.md. Alias maps are injectable; the private_health
defaults live in configs/private_health/aliases.yaml so future verticals can
supply their own file without code changes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

from src.refine.patch import SchemaPatch, load_yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ALIAS_CONFIG = PROJECT_ROOT / "configs" / "private_health" / "aliases.yaml"

# In-code fallbacks, used only when the config file is missing. Keep these in
# sync with configs/private_health/aliases.yaml.
DEFAULT_FIELD_ALIASES: Mapping[str, str] = {
    "annual_benefit_limit": "annual_limit",
    "annual_limits": "annual_limit",
    "yearly_limit": "annual_limit",
    "fund": "fund_name",
    "company": "fund_name",
    "company_name": "fund_name",
    "provider": "provider_name",
    "product": "product_name",
}

DEFAULT_GROUP_ALIASES: Mapping[str, str] = {
    "extras": "extras_cover",
    "extra_cover": "extras_cover",
    "hospital": "hospital_cover",
    "general_health": "generalhealth_cover",
    "generalhealth": "generalhealth_cover",
    "source": "source_and_evidence",
    "evidence": "source_and_evidence",
}


def load_alias_config(
    path: str | Path | None = None,
) -> tuple[Mapping[str, str], Mapping[str, str]]:
    """Load (field_aliases, group_aliases) from a YAML config.

    Falls back to the in-code defaults when no path is given and the default
    config file does not exist, so the pipeline works on a bare checkout.
    """
    resolved = Path(path) if path else DEFAULT_ALIAS_CONFIG
    if not resolved.exists():
        if path:
            raise FileNotFoundError(resolved)
        return DEFAULT_FIELD_ALIASES, DEFAULT_GROUP_ALIASES

    payload = load_yaml(resolved) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Alias config must be a YAML object: {resolved}")
    return (
        _string_map(payload.get("field_aliases"), resolved),
        _string_map(payload.get("group_aliases"), resolved),
    )


def _string_map(value: object, source: Path) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Alias sections must be YAML mappings: {source}")
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
        field_name=canonical_field_name(patch.field_name, field_aliases),
        canonical_name=canonical_name,
        field_type=patch.field_type,
        description=patch.description,
        evidence_documents=patch.evidence_documents,
        confidence=patch.confidence,
        rationale=patch.rationale,
        source_run=patch.source_run,
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
    resolved_aliases = DEFAULT_FIELD_ALIASES if aliases is None else aliases
    return resolved_aliases.get(name, name)


def canonical_group_name(
    value: str, aliases: Mapping[str, str] | None = None
) -> str:
    name = _snake_case(value)
    resolved_aliases = DEFAULT_GROUP_ALIASES if aliases is None else aliases
    return resolved_aliases.get(name, name)


def _snake_case(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z]+", "_", value.strip())
    cleaned = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_").lower()
    return cleaned
