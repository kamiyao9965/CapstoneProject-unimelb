"""Build and load immutable human review queues."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from src.refine.artifacts.schema_fields import (
    decision_requires_manual_edit,
    decision_requires_schema_edit,
    decision_is_auto_promotable,
    field_payload_from_decision,
    fields_by_name,
)
from src.refine.candidates.aggregator import FieldDecision
from src.common.json_artifacts import build_success_artifact, read_artifact, write_artifact


def build_review_queue(
    decisions: list[FieldDecision],
    base_schema: dict,
    total_runs: int,
    base_schema_path: str | Path,
    generated_at: str | None = None,
    schema_build_samples: Iterable[str | Path] = (),
    manual_only: bool = False,
    vertical: str = "private_health",
    schema_contract: str = "private_health/discovered_schema",
    valid_product_types: tuple[str, ...] = ("hospital", "extras", "generalhealth", "combined"),
    promoted_decisions: frozenset[str] = frozenset({"core", "conditional"}),
    protected_fields: frozenset[str] = frozenset(),
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
            "vertical": vertical,
            "schema_contract": schema_contract,
        },
        "updates": [
            _queue_item(
                decision,
                existing_fields.get(decision.canonical_name),
                valid_product_types,
            )
            for decision in decisions
            if not manual_only
            or not decision_is_auto_promotable(
                decision, promoted_decisions, protected_fields
            )
        ],
    }


def _queue_item(
    decision: FieldDecision,
    existing_field: dict | None,
    valid_product_types: tuple[str, ...],
) -> dict:
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
        "has_conflict": decision.has_conflict,
        "proposed_update": field_payload_from_decision(
            decision, existing_field, valid_product_types
        ),
    }


def write_review_queue(
    queue: dict,
    path: str | Path,
    *,
    provenance: dict[str, object] | None = None,
    data_contract: str = "private_health/review_queue",
) -> None:
    artifact = build_success_artifact(
        artifact_type="review_queue",
        contract_version="1.0.0",
        data=queue,
        provenance=provenance or _local_provenance(),
        data_contract=data_contract,
    )
    write_artifact(path, artifact, data_contract=data_contract)


def load_review_queue(
    path: str | Path,
    *,
    data_contract: str = "private_health/review_queue",
) -> dict:
    payload = read_artifact(
        path,
        expected_type="review_queue",
        data_contract=data_contract,
    )["data"]
    if not isinstance(payload, dict) or "updates" not in payload:
        raise ValueError(f"Not a review queue file: {path}")
    return payload


def _local_provenance() -> dict[str, object]:
    return {
        "run_id": None, "provider": None, "model": None,
        "document_input": None, "source_documents": [], "source_artifacts": [],
    }
