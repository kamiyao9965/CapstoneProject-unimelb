"""Apply human review decisions to a base schema."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from src.refine.artifacts.schema_fields import fields_by_name, sanitize_field_payload
from src.refine.candidates.patch import dump_yaml, load_yaml
from src.refine.human_review.constants import (
    DECISIONS_FILENAME,
    QUEUE_FILENAME,
    REVIEWED_SCHEMA_FILENAME,
    SUPPORTED_ACTIONS,
)
from src.refine.human_review.decisions import decisions_by_id, load_review_decisions
from src.refine.human_review.queue import load_review_queue


def apply_review(
    queue: dict,
    decisions_payload: dict,
    base_schema: dict,
) -> tuple[dict, dict]:
    """Apply accept/edit decisions onto the base schema.

    Rejected and pending items are never applied. Unknown decision ids or
    actions fail loudly because they indicate a queue/decision file mismatch.
    """
    if not isinstance(base_schema, dict):
        raise ValueError("Base schema must be a YAML object.")

    queue_items = queue.get("updates", [])
    queue_ids = {item["id"] for item in queue_items}
    by_id = decisions_by_id(decisions_payload)
    unknown_ids = sorted(set(by_id) - queue_ids)
    if unknown_ids:
        raise ValueError(
            "Decisions reference ids missing from the queue "
            f"(wrong file pairing?): {', '.join(unknown_ids)}"
        )

    reviewed = deepcopy(base_schema)
    fields = fields_by_name(reviewed.get("fields", []))
    summary = {"applied": [], "edited": [], "rejected": [], "pending": []}

    for item in queue_items:
        payload = _payload_from_review_item(item, by_id.get(item["id"]), summary)
        if payload is not None:
            fields[str(payload["name"])] = payload

    reviewed["fields"] = list(fields.values())
    reviewed["review"] = {
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "source_queue_generated_at": queue.get("metadata", {}).get("generated_at"),
        "applied_count": len(summary["applied"]) + len(summary["edited"]),
        "rejected_count": len(summary["rejected"]),
        "pending_count": len(summary["pending"]),
    }
    return reviewed, summary


def _payload_from_review_item(
    item: dict,
    entry: dict | None,
    summary: dict[str, list[str]],
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
    if action == "accept":
        summary["applied"].append(item["id"])
        return sanitize_field_payload(deepcopy(item.get("proposed_update") or {}))

    payload = deepcopy(entry.get("edited_update"))
    if not isinstance(payload, dict) or not payload.get("name"):
        raise ValueError(
            f"edit decision for {item['id']} needs an edited_update object "
            "with at least a name"
        )
    summary["edited"].append(item["id"])
    return sanitize_field_payload(payload)


def apply_review_files(
    consensus_dir: str | Path,
    base_schema_path: str | Path | None = None,
    output_path: str | Path | None = None,
) -> tuple[Path, dict]:
    """Load queue/decisions from disk and write reviewed_schema.yaml."""
    consensus_dir = Path(consensus_dir)
    queue = load_review_queue(consensus_dir / QUEUE_FILENAME)
    decisions_path = consensus_dir / DECISIONS_FILENAME
    if not decisions_path.exists():
        raise FileNotFoundError(
            f"No decisions file at {decisions_path}. Review first, then apply."
        )

    decisions_payload = load_review_decisions(decisions_path)
    resolved_base_schema = Path(base_schema_path or queue["metadata"]["base_schema_path"])
    reviewed, summary = apply_review(
        queue,
        decisions_payload,
        load_yaml(resolved_base_schema),
    )

    resolved_output = (
        Path(output_path) if output_path else consensus_dir / REVIEWED_SCHEMA_FILENAME
    )
    dump_yaml(reviewed, resolved_output)
    return resolved_output, summary
