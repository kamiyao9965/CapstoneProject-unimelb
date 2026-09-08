"""Deterministic spelling normalization; no synonym or alias grouping."""
from dataclasses import replace
import re

from src.refine.candidates.patch import SchemaPatch


def canonical_field_name(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z]+", "_", value.strip())
    cleaned = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", cleaned)
    return re.sub(r"_+", "_", cleaned).strip("_").lower()


def canonical_group_name(value: str) -> str:
    return canonical_field_name(value)


def normalize_patch(patch: SchemaPatch) -> SchemaPatch:
    return replace(patch,
        field_name=canonical_field_name(patch.field_name),
        canonical_name=canonical_field_name(patch.canonical_name or patch.field_name),
        target_group=canonical_group_name(patch.target_group))


def normalize_patches(patches: list[SchemaPatch]) -> list[SchemaPatch]:
    return [normalize_patch(patch) for patch in patches]
