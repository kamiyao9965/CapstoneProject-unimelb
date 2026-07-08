"""Human review of consensus proposals: queue, decisions, and application.

Workflow (see CLAUDE_PATCH_REVIEW_UI_REQUIREMENTS.md):

    aggregated FieldDecisions -> review_queue.yaml   (immutable after write)
    human decisions           -> review_decisions.yaml
    apply                     -> reviewed_schema.yaml

Review status is never stored in the queue; it is derived by joining the
decisions file (no entry = pending). Pending items are never applied.
"""

from __future__ import annotations

import argparse
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.refine.aggregator import FieldDecision
from src.refine.patch import dump_yaml, load_yaml
from src.refine.schema_fields import (
    field_payload_from_decision,
    fields_by_name,
    sanitize_field_payload,
)

# Patch types whose real semantics (delete old field, fold sources, move
# groups) are not implemented in v1; accept applies a plain upsert, so the
# reviewer is expected to Edit then Accept.
MANUAL_EDIT_PATCH_TYPES = {"rename_field", "merge_fields", "move_field_group"}
SUPPORTED_ACTIONS = {"accept", "reject", "edit"}

QUEUE_FILENAME = "review_queue.yaml"
DECISIONS_FILENAME = "review_decisions.yaml"
REVIEWED_SCHEMA_FILENAME = "reviewed_schema.yaml"


# ---------------------------------------------------------------- queue build

def build_review_queue(
    decisions: list[FieldDecision],
    base_schema: dict,
    total_runs: int,
    base_schema_path: str | Path,
    generated_at: str | None = None,
) -> dict:
    existing_fields = fields_by_name(base_schema.get("fields", []))
    updates = [
        _queue_item(decision, existing_fields.get(decision.canonical_name))
        for decision in decisions
    ]
    return {
        "metadata": {
            "generated_at": generated_at
            or datetime.now(timezone.utc).isoformat(),
            "consensus_source": "candidate_schema_patches",
            "total_runs": total_runs,
            "base_schema_path": Path(base_schema_path).as_posix(),
        },
        "updates": updates,
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
        "needs_manual_edit": bool(
            MANUAL_EDIT_PATCH_TYPES.intersection(decision.patch_types)
        ),
        "proposed_update": field_payload_from_decision(decision, existing_field),
    }


# ------------------------------------------------------------------- file IO

def write_review_queue(queue: dict, path: str | Path) -> None:
    dump_yaml(queue, path)


def load_review_queue(path: str | Path) -> dict:
    payload = load_yaml(path)
    if not isinstance(payload, dict) or "updates" not in payload:
        raise ValueError(f"Not a review queue file: {path}")
    return payload


def empty_decisions(reviewer: str = "") -> dict:
    return {
        "metadata": {
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "reviewer": reviewer,
        },
        "decisions": [],
    }


def load_review_decisions(path: str | Path) -> dict:
    payload = load_yaml(path)
    if not isinstance(payload, dict) or "decisions" not in payload:
        raise ValueError(f"Not a review decisions file: {path}")
    return payload


def write_review_decisions(decisions_payload: dict, path: str | Path) -> None:
    decisions_payload.setdefault("metadata", {})["reviewed_at"] = datetime.now(
        timezone.utc
    ).isoformat()
    dump_yaml(decisions_payload, path)


def upsert_decision(
    decisions_payload: dict,
    item_id: str,
    action: str,
    reviewer_notes: str = "",
    edited_update: dict | None = None,
) -> dict:
    """Replace or append the decision for one queue item (used by the UI)."""
    if action not in SUPPORTED_ACTIONS:
        raise ValueError(f"Unsupported action: {action}")
    entry = {
        "id": item_id,
        "action": action,
        "reviewer_notes": reviewer_notes,
        "edited_update": edited_update,
    }
    decisions = [
        existing
        for existing in decisions_payload.get("decisions", [])
        if existing.get("id") != item_id
    ]
    decisions.append(entry)
    decisions_payload["decisions"] = decisions
    return decisions_payload


def clear_decision(decisions_payload: dict, item_id: str) -> dict:
    """Remove a decision so the item goes back to pending (used by the UI)."""
    decisions_payload["decisions"] = [
        existing
        for existing in decisions_payload.get("decisions", [])
        if existing.get("id") != item_id
    ]
    return decisions_payload


# ------------------------------------------------------------- status / apply

def decisions_by_id(decisions_payload: dict) -> dict[str, dict]:
    return {
        str(entry.get("id")): entry
        for entry in decisions_payload.get("decisions", [])
        if entry.get("id")
    }


def derive_status(queue: dict, decisions_payload: dict) -> dict[str, str]:
    """id -> pending | accepted | rejected | edited. The queue is never rewritten."""
    by_id = decisions_by_id(decisions_payload)
    status: dict[str, str] = {}
    for item in queue.get("updates", []):
        entry = by_id.get(item["id"])
        if entry is None:
            status[item["id"]] = "pending"
        else:
            action = str(entry.get("action"))
            status[item["id"]] = {
                "accept": "accepted",
                "reject": "rejected",
                "edit": "edited",
            }.get(action, "pending")
    return status


def apply_review(
    queue: dict,
    decisions_payload: dict,
    base_schema: dict,
) -> tuple[dict, dict]:
    """Apply accept/edit decisions onto the base schema.

    Returns (reviewed_schema, summary). Rejected and pending items are never
    applied; pending ids are reported in the summary so nothing happens
    silently. Unknown decision ids or actions fail loudly - they mean the
    decisions file does not match the queue.
    """
    if not isinstance(base_schema, dict):
        raise ValueError("Base schema must be a YAML object.")

    queue_ids = {item["id"] for item in queue.get("updates", [])}
    by_id = decisions_by_id(decisions_payload)

    unknown = sorted(set(by_id) - queue_ids)
    if unknown:
        raise ValueError(
            "Decisions reference ids missing from the queue "
            f"(wrong file pairing?): {', '.join(unknown)}"
        )

    reviewed = deepcopy(base_schema)
    fields = fields_by_name(reviewed.get("fields", []))
    summary = {"applied": [], "edited": [], "rejected": [], "pending": []}

    for item in queue.get("updates", []):
        entry = by_id.get(item["id"])
        if entry is None:
            summary["pending"].append(item["id"])
            continue

        action = str(entry.get("action"))
        if action not in SUPPORTED_ACTIONS:
            raise ValueError(f"Unsupported action for {item['id']}: {action}")

        if action == "reject":
            summary["rejected"].append(item["id"])
            continue

        if action == "accept":
            payload = deepcopy(item.get("proposed_update") or {})
            summary["applied"].append(item["id"])
        else:  # edit
            payload = deepcopy(entry.get("edited_update"))
            if not isinstance(payload, dict) or not payload.get("name"):
                raise ValueError(
                    f"edit decision for {item['id']} needs an edited_update "
                    "object with at least a name"
                )
            summary["edited"].append(item["id"])

        payload = sanitize_field_payload(payload)
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

# ------------------------------------------------------------------------ CLI

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply human review decisions to produce reviewed_schema.yaml"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    apply_cmd = sub.add_parser("apply", help="Apply review_decisions.yaml to the queue")
    apply_cmd.add_argument(
        "--consensus-dir",
        required=True,
        help="Directory containing review_queue.yaml and review_decisions.yaml",
    )
    apply_cmd.add_argument(
        "--base-schema",
        help="Base schema YAML (default: base_schema_path from the queue metadata)",
    )
    apply_cmd.add_argument(
        "--out",
        help=f"Output path (default: <consensus-dir>/{REVIEWED_SCHEMA_FILENAME})",
    )
    return parser


def run_apply(args) -> int:
    consensus_dir = Path(args.consensus_dir)
    queue = load_review_queue(consensus_dir / QUEUE_FILENAME)
    decisions_path = consensus_dir / DECISIONS_FILENAME
    if not decisions_path.exists():
        print(f"No decisions file at {decisions_path}. Review first, then apply.")
        return 1
    decisions_payload = load_review_decisions(decisions_path)

    base_schema_path = Path(
        args.base_schema or queue["metadata"]["base_schema_path"]
    )
    base_schema = load_yaml(base_schema_path)

    reviewed, summary = apply_review(queue, decisions_payload, base_schema)
    out_path = Path(args.out) if args.out else consensus_dir / REVIEWED_SCHEMA_FILENAME
    dump_yaml(reviewed, out_path)

    print(f"Wrote {out_path}")
    print(
        f"applied={len(summary['applied'])} edited={len(summary['edited'])} "
        f"rejected={len(summary['rejected'])} pending={len(summary['pending'])}"
    )
    if summary["pending"]:
        print(
            "PENDING (not applied): "
            + ", ".join(summary["pending"])
        )
    return 0


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "apply":
        return run_apply(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
