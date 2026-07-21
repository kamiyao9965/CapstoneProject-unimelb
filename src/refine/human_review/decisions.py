"""Read, write, and derive state from human review decisions."""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from src.common.json_artifacts import build_success_artifact, read_artifact, write_artifact
from src.common.json_contracts import validate_contract
from src.refine.human_review.constants import SUPPORTED_ACTIONS

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


def empty_decisions(reviewer: str = "") -> dict:
    return {
        "metadata": {
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "reviewer": reviewer,
        },
        "decisions": [],
    }


def load_review_decisions(path: str | Path) -> dict:
    payload = read_artifact(
        path,
        expected_type="review_decisions",
        data_contract="private_health/review_decisions",
    )["data"]
    if not isinstance(payload, dict) or "decisions" not in payload:
        raise ValueError(f"Not a review decisions file: {path}")
    return payload


def write_review_decisions(decisions_payload: dict, path: str | Path) -> None:
    decisions_payload.setdefault("metadata", {})["reviewed_at"] = datetime.now(
        timezone.utc
    ).isoformat()
    validate_contract(decisions_payload, "private_health/review_decisions")
    artifact = build_success_artifact(
        artifact_type="review_decisions",
        contract_version="1.0.0",
        data=decisions_payload,
        provenance={
            "run_id": None, "provider": None, "model": None,
            "document_input": None, "source_documents": [], "source_artifacts": [],
        },
        data_contract="private_health/review_decisions",
    )
    write_artifact(
        path,
        artifact,
        data_contract="private_health/review_decisions",
        overwrite=True,
    )


def save_review_decision(
    path: str | Path,
    item_id: str,
    action: str,
    reviewer_notes: str = "",
    edited_update: dict | None = None,
) -> dict:
    """Atomically merge one decision with the latest persisted file state."""
    return _update_decisions_file(
        Path(path),
        lambda payload: upsert_decision(
            payload, item_id, action, reviewer_notes, edited_update
        ),
    )


def remove_review_decision(path: str | Path, item_id: str) -> dict:
    """Atomically remove one decision from the latest persisted file state."""
    return _update_decisions_file(
        Path(path),
        lambda payload: clear_decision(payload, item_id),
    )


def _update_decisions_file(
    path: Path,
    update: Callable[[dict], dict],
) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    with os.fdopen(descriptor, "r+") as lock:
        with _exclusive_lock(lock):
            payload = load_review_decisions(path) if path.exists() else empty_decisions()
            updated = update(payload)
            write_review_decisions(updated, path)
            return updated


@contextmanager
def _exclusive_lock(lock):
    if fcntl is None:
        yield
        return
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


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
    validate_contract(
        {"metadata": decisions_payload.get("metadata", {}), "decisions": [entry]},
        "private_health/review_decisions",
    )
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
