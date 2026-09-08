"""Build and load immutable human review queues."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from copy import deepcopy

from src.common.json_codec import dumps_json
from src.schema.validation import normalize_schema
from src.verticals.manifest import default_manifest_path, load_vertical_manifest
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
    manifest=None,
) -> dict:
    manifest = manifest or load_vertical_manifest(default_manifest_path(vertical))
    if base_schema.get("vertical") != manifest.vertical or manifest.vertical != vertical:
        raise ValueError("Review queue vertical does not match base schema.")
    existing_fields = fields_by_name(normalize_schema(base_schema, manifest).get("fields", []))
    queue = {
        "metadata": {
            "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
            "consensus_source": "candidate_schema_patches",
            "total_runs": total_runs,
            "base_schema_path": Path(base_schema_path).as_posix(),
            "schema_build_samples": list(
                dict.fromkeys(str(path) for path in schema_build_samples)
            ),
            "vertical": vertical,
            "schema_version": str(base_schema["version"]),
            "base_schema_hash": schema_hash(base_schema, manifest),
            "manifest_path": str(manifest.source_path),
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
    queue["metadata"]["queue_id"] = queue_id(queue)
    return queue


def schema_hash(schema: dict, manifest=None) -> str:
    return sha256(dumps_json(normalize_schema(schema, manifest), sort_keys=True).encode()).hexdigest()


def queue_id(queue: dict) -> str:
    data = deepcopy(queue)
    data["metadata"].pop("queue_id", None)
    return sha256(dumps_json(data, sort_keys=True).encode()).hexdigest()


def review_identity(queue: dict) -> dict[str, str]:
    metadata = queue.get("metadata", {})
    names = ("queue_id", "vertical", "schema_version")
    if any(not metadata.get(name) for name in names):
        raise ValueError("Historical review queue lacks identity; finish it with its original version or regenerate explicitly.")
    if metadata["queue_id"] != queue_id(queue):
        raise ValueError("Review queue identity changed; the queue must remain immutable.")
    return {name: metadata[name] for name in names}


def review_manifest(queue: dict):
    metadata = queue["metadata"]
    manifest = load_vertical_manifest(metadata.get("manifest_path") or default_manifest_path(metadata["vertical"]))
    if manifest.vertical != metadata["vertical"]:
        raise ValueError("Review manifest vertical does not match the queue.")
    return manifest


def validate_review_identity(queue: dict, decisions: dict, base_schema: dict | None = None) -> None:
    identity = review_identity(queue)
    if any(decisions.get("metadata", {}).get(name) != value for name, value in identity.items()):
        raise ValueError("Review decisions identity does not match this queue/vertical/schema.")
    if base_schema is not None and (
        base_schema.get("vertical") != identity["vertical"]
        or base_schema.get("version") != identity["schema_version"]
        or schema_hash(base_schema, review_manifest(queue)) != queue["metadata"].get("base_schema_hash")
    ):
        raise ValueError("Review base schema identity does not match the queue.")


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
    data_contract: str = "schema_refinement/review_queue",
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
    data_contract: str = "schema_refinement/review_queue",
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
