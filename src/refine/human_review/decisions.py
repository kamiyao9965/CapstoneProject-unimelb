"""Read, write, and derive state from human review decisions."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.refine.candidates.patch import dump_yaml, load_yaml
from src.refine.human_review.constants import SUPPORTED_ACTIONS


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
    """Replace or append the decision for one queue item."""
    if action not in SUPPORTED_ACTIONS:
        raise ValueError(f"Unsupported action: {action}")
    entry = {
        "id": item_id,
        "action": action,
        "reviewer_notes": reviewer_notes,
        "edited_update": edited_update,
    }
    decisions_payload["decisions"] = [
        existing
        for existing in decisions_payload.get("decisions", [])
        if existing.get("id") != item_id
    ] + [entry]
    return decisions_payload


def clear_decision(decisions_payload: dict, item_id: str) -> dict:
    """Remove a decision so the item goes back to pending."""
    decisions_payload["decisions"] = [
        existing
        for existing in decisions_payload.get("decisions", [])
        if existing.get("id") != item_id
    ]
    return decisions_payload


def decisions_by_id(decisions_payload: dict) -> dict[str, dict]:
    return {
        str(entry.get("id")): entry
        for entry in decisions_payload.get("decisions", [])
        if entry.get("id")
    }


def derive_status(queue: dict, decisions_payload: dict) -> dict[str, str]:
    """id -> pending | accepted | rejected | edited."""
    by_id = decisions_by_id(decisions_payload)
    labels = {"accept": "accepted", "reject": "rejected", "edit": "edited"}
    return {
        item["id"]: labels.get(str(by_id.get(item["id"], {}).get("action")), "pending")
        for item in queue.get("updates", [])
    }
