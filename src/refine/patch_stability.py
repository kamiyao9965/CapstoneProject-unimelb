"""Patch-level stability: field drift across the SAME candidate patch runs.

Distinct from src/stability/ (which measures full-schema drift and costs an
extra N-run API sweep). Here the N patch runs the consensus stage already paid
for double as the stability sample.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from src.refine.patch import SchemaPatch, dump_yaml


def compute_patch_stability(patches: list[SchemaPatch], total_runs: int) -> dict:
    """stable_core = fields proposed in every run; stability = |core| / |union|.

    A run's field set counts canonical names with at least one non-reject patch
    in that run; reject-only mentions are not presence. A run that proposed no
    fields at all contributes an empty set, which empties the stable core.
    """
    run_fields: dict[str, set[str]] = defaultdict(set)
    for patch in patches:
        if patch.patch_type == "reject_field":
            continue
        if patch.canonical_name:
            run_fields[patch.source_run or "unknown"].add(patch.canonical_name)

    union: set[str] = set()
    for fields in run_fields.values():
        union |= fields

    if run_fields and len(run_fields) >= total_runs:
        stable = set.intersection(*run_fields.values())
    else:
        # Fewer contributing runs than total_runs means at least one run
        # proposed nothing; the intersection over all runs is empty.
        stable = set()

    drifting = union - stable
    stability = len(stable) / len(union) if union else 0.0

    return {
        "metadata": {
            "total_runs": total_runs,
            "stability_source": "candidate_schema_patches",
        },
        "dimensions": {
            "fields": {
                "stable_count": len(stable),
                "drift_count": len(drifting),
                "union_count": len(union),
                "stability": round(stability, 3),
                "stable_items": sorted(stable),
                "drifting_items": sorted(drifting),
            }
        },
    }


def write_patch_stability(payload: dict, path: str | Path) -> None:
    dump_yaml(payload, path)
