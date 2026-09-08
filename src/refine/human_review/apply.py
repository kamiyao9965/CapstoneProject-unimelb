"""Apply human review decisions to a base schema."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

from src.refine.artifacts.schema_fields import fields_by_name
from src.common.json_artifacts import (
    build_success_artifact,
    read_artifact,
    write_artifact,
)
from src.refine.human_review.constants import (
    DECISIONS_FILENAME,
    QUEUE_FILENAME,
    REVIEWED_SCHEMA_FILENAME,
    SUPPORTED_ACTIONS,
)
from src.refine.human_review.decisions import decisions_by_id, load_review_decisions
from src.refine.human_review.queue import load_review_queue
from src.schema.validation import validate_field_payload, validate_schema_mapping


def apply_review(
    queue: dict,
    decisions_payload: dict,
    base_schema: dict,
    *,
    schema_validator: Callable[[object], object] = validate_schema_mapping,
    allowed_product_types: set[str] | None = None,
) -> tuple[dict, dict]:
    """Apply accept/edit decisions onto the base schema.

    Rejected and pending items are never applied. Unknown decision ids or
    actions fail loudly because they indicate a queue/decision file mismatch.
    """
    if not isinstance(base_schema, dict):
        raise ValueError("Base schema must be a JSON object.")

    queue_items = queue.get("updates", [])
    queue_ids = {item["id"] for item in queue_items}
    by_id = decisions_by_id(decisions_payload)
    unknown_ids = sorted(set(by_id) - queue_ids)
    if unknown_ids:
        raise ValueError(
            "Decisions reference ids missing from the queue "
            f"(wrong file pairing?): {', '.join(unknown_ids)}"
        )

    reviewed = validate_schema_mapping(base_schema)
    fields = fields_by_name(reviewed.get("fields", []))
    allowed_product_types = allowed_product_types or set(
        reviewed.get("product_types") or []
    )
    summary = {"applied": [], "edited": [], "rejected": [], "pending": []}

    for item in queue_items:
        payload = _payload_from_review_item(
            item,
            by_id.get(item["id"]),
            summary,
            allowed_product_types,
        )
        if payload is not None:
            payload.pop("aliases", None)
            fields[str(payload["name"])] = payload

    reviewed["fields"] = list(fields.values())
    schema_validator(reviewed)
    return reviewed, summary


def _payload_from_review_item(
    item: dict,
    entry: dict | None,
    summary: dict[str, list[str]],
    allowed_product_types: set[str],
) -> dict | None:
    if entry is None:
        summary["pending"].append(item["id"])
        return None

    action = str(entry.get("action"))
    if action not in SUPPORTED_ACTIONS:
        raise ValueError(f"Unsupported action for {item['id']}: {action}")

    if action == "reject":
        summary["rejected"].append(item["id"])
        return None
    if "add_alias" in item.get("patch_types", []):
        raise ValueError("Historical add_alias operations are read-only and cannot be applied.")
    if action == "accept":
        if item.get("needs_manual_edit"):
            raise ValueError(
                f"{item['id']} combines or requires manual patch semantics and "
                "must be edited before it can be applied."
            )
        summary["applied"].append(item["id"])
        payload = deepcopy(item.get("proposed_update") or {})
        validate_field_payload(payload, allowed_product_types)
        return payload

    if item.get("needs_schema_edit"):
        raise ValueError(
            f"{item['id']} uses rename/merge/move semantics and cannot be applied "
            "as a field upsert; edit the base schema contract directly."
        )

    payload = deepcopy(entry.get("edited_update"))
    if not isinstance(payload, dict) or not payload.get("name"):
        raise ValueError(
            f"edit decision for {item['id']} needs an edited_update object "
            "with at least a name"
        )
    summary["edited"].append(item["id"])
    validate_field_payload(payload, allowed_product_types)
    return payload


def apply_review_files(
    consensus_dir: str | Path,
    base_schema_path: str | Path | None = None,
    output_path: str | Path | None = None,
) -> tuple[Path, dict]:
    """Load queue/decisions from disk and write reviewed_schema.json."""
    consensus_dir = Path(consensus_dir)
    queue = load_review_queue(consensus_dir / QUEUE_FILENAME)
    decisions_path = consensus_dir / DECISIONS_FILENAME
    if not decisions_path.exists():
        raise FileNotFoundError(
            f"No decisions file at {decisions_path}. Review first, then apply."
        )

    decisions_payload = load_review_decisions(decisions_path)
    metadata = queue.get("metadata", {})
    schema_contract = str(
        metadata.get("schema_contract") or "private_health/discovered_schema"
    )
    vertical = str(metadata.get("vertical") or "private_health")
    if vertical == "travel_insurance":
        from src.verticals.travel_insurance import (
            SUPPORTED_TRAVEL_PRODUCT_TYPES,
            validate_travel_schema_mapping,
        )

        schema_validator = validate_travel_schema_mapping
        allowed_product_types = set(SUPPORTED_TRAVEL_PRODUCT_TYPES)
    else:
        schema_validator = validate_schema_mapping
        allowed_product_types = None
    resolved_base_schema = Path(base_schema_path or queue["metadata"]["base_schema_path"])
    reviewed, summary = apply_review(
        queue,
        decisions_payload,
        read_artifact(
            resolved_base_schema,
            expected_type="discovered_schema",
            data_contract=schema_contract,
        )["data"],
        schema_validator=schema_validator,
        allowed_product_types=allowed_product_types,
    )

    resolved_output = (
        Path(output_path) if output_path else consensus_dir / REVIEWED_SCHEMA_FILENAME
    )
    artifact = build_success_artifact(
        artifact_type="discovered_schema",
        contract_version="1.0.0",
        data=reviewed,
        provenance={
            "run_id": None, "provider": None, "model": None,
            "document_input": None, "source_documents": [],
            "source_artifacts": [
                (consensus_dir / QUEUE_FILENAME).as_posix(),
                decisions_path.as_posix(),
                resolved_base_schema.as_posix(),
            ],
        },
        data_contract=schema_contract,
    )
    write_artifact(
        resolved_output,
        artifact,
        data_contract=schema_contract,
    )
    return resolved_output, summary
