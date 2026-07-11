"""Build and load immutable human review queues."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from src.refine.artifacts.schema_fields import (
    decision_requires_manual_edit,
    decision_requires_schema_edit,
    field_payload_from_decision,
    fields_by_name,
)
from src.refine.candidates.aggregator import FieldDecision
from src.refine.candidates.patch import dump_yaml, load_yaml


def build_review_queue(
    decisions: list[FieldDecision],
    base_schema: dict,
    total_runs: int,
    base_schema_path: str | Path,
    generated_at: str | None = None,
    schema_build_samples: Iterable[str | Path] = (),
) -> dict:
    existing_fields = fields_by_name(base_schema.get("fields", []))
    return {
        "metadata": {
            "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
            "consensus_source": "candidate_schema_patches",
            "total_runs": total_runs,
            "base_schema_path": Path(base_schema_path).as_posix(),
            "schema_build_samples": list(
                dict.fromkeys(str(path) for path in schema_build_samples)
            ),
        },
        "updates": [
            _queue_item(decision, existing_fields.get(decision.canonical_name))
            for decision in decisions
        ],
    }


def _queue_item(decision: FieldDecision, existing_field: dict | None) -> dict:
    return {
        "id": f"field:{decision.canonical_name}",
        "patch_types": decision.patch_types,
        "target_group": decision.target_group,
        "canonical_name": decision.canonical_name,
        "field_name": decision.canonical_name,
        "type": decision.field_type,
        "suggested_decision": decision.decision,
        "frequency": decision.frequency_label,
        "reject_votes": decision.reject_votes_label,
        "average_confidence": round(decision.average_confidence, 3),
        "aliases": decision.aliases,
        "evidence_documents": [doc.to_dict() for doc in decision.evidence_documents],
        "rationale_samples": decision.rationale_samples,
        "reject_rationale_samples": decision.reject_rationale_samples,
        "needs_manual_edit": decision_requires_manual_edit(decision),
        "needs_schema_edit": decision_requires_schema_edit(decision),
        "proposed_update": field_payload_from_decision(decision, existing_field),
    }


def write_review_queue(queue: dict, path: str | Path) -> None:
    dump_yaml(queue, path)


def load_review_queue(path: str | Path) -> dict:
    payload = load_yaml(path)
    if not isinstance(payload, dict) or "updates" not in payload:
        raise ValueError(f"Not a review queue file: {path}")
    return payload
