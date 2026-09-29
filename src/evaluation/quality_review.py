"""Queue-bound human decisions for post-extraction quality screening."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from src.common.json_artifacts import build_success_artifact, read_artifact, write_artifact
from src.evaluation.quality import quality_queue_id


DECISIONS = frozenset({"issue_found", "no_issue", "uncertain"})


def load_queue(path: str | Path) -> dict:
    queue = read_artifact(path, expected_type="quality_review_queue", data_contract="quality/review_queue")
    data = queue["data"]
    if quality_queue_id(data) != data["queue_id"]:
        raise ValueError("Quality review queue identity does not match its contents.")
    item_ids = [item["item_id"] for item in data["items"]]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("Quality review queue contains duplicate item identities.")
    return queue


def load_decisions(queue_path: str | Path, decisions_path: str | Path) -> dict:
    queue = load_queue(queue_path)
    path = Path(decisions_path)
    if not path.exists():
        return _empty_decisions(queue, Path(queue_path))
    decisions = read_artifact(path, expected_type="quality_review_decisions",
                              data_contract="quality/review_decisions")
    if decisions["data"]["queue_id"] != queue["data"]["queue_id"]:
        raise ValueError("Review decisions belong to a different queue identity.")
    allowed = {item["item_id"] for item in queue["data"]["items"]}
    ids = [entry["item_id"] for entry in decisions["data"]["decisions"]]
    if len(ids) != len(set(ids)) or set(ids) - allowed:
        raise ValueError("Review decisions contain duplicate or unknown queue items.")
    for entry in decisions["data"]["decisions"]:
        if not entry["reviewer"].strip():
            raise ValueError("Review decision is missing a reviewer.")
        if entry["decision"] in {"issue_found", "uncertain"} and not entry["notes"].strip():
            raise ValueError("Issue and uncertain decisions require notes.")
    return decisions


def save_decision(
    queue_path: str | Path, decisions_path: str | Path, item_id: str,
    decision: str, notes: str, reviewer: str,
) -> Path:
    """Update only human review data, never extraction or database records."""
    queue = load_queue(queue_path)
    if item_id not in {item["item_id"] for item in queue["data"]["items"]}:
        raise ValueError("Unknown quality review queue item.")
    if decision not in DECISIONS:
        raise ValueError("Unknown quality review decision.")
    notes = notes.strip()
    reviewer = reviewer.strip()
    if not reviewer:
        raise ValueError("Reviewer is required.")
    if decision in {"issue_found", "uncertain"} and not notes:
        raise ValueError("Issue and uncertain decisions require notes.")
    latest = load_decisions(queue_path, decisions_path)
    entries = [entry for entry in latest["data"]["decisions"] if entry["item_id"] != item_id]
    entries.append({
        "item_id": item_id, "decision": decision, "notes": notes,
        "reviewer": reviewer,
        "reviewed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    })
    entries.sort(key=lambda entry: entry["item_id"])
    latest["data"]["decisions"] = entries
    return write_artifact(decisions_path, latest, data_contract="quality/review_decisions", overwrite=True)


def quality_metrics(queue: dict, decisions: dict) -> dict[str, int | float]:
    """Operational workload/calibration signals; not an accuracy estimate."""
    data = queue["data"]
    by_id = {item["item_id"]: item for item in data["items"]}
    answers = decisions["data"]["decisions"]
    alerts = [item for item in data["items"] if item["kind"] != "pass_sample"]
    return {
        "audited_documents": data["audited_documents"],
        "judge_alert_documents": data["judge_review_documents"] + data["judge_uncertain_documents"],
        "judge_alert_rate": (
            (data["judge_review_documents"] + data["judge_uncertain_documents"])
            / data["audited_documents"] if data["audited_documents"] else 0.0
        ),
        "review_items": len(data["items"]),
        "alert_items": len(alerts),
        "sampled_pass_items": sum(item["kind"] == "pass_sample" for item in data["items"]),
        "reviewed_items": len(answers),
        "confirmed_alerts": sum(by_id[entry["item_id"]]["kind"] != "pass_sample" and entry["decision"] == "issue_found" for entry in answers),
        "dismissed_alerts": sum(by_id[entry["item_id"]]["kind"] != "pass_sample" and entry["decision"] == "no_issue" for entry in answers),
        "sampled_pass_misses": sum(by_id[entry["item_id"]]["kind"] == "pass_sample" and entry["decision"] == "issue_found" for entry in answers),
        "human_uncertain": sum(entry["decision"] == "uncertain" for entry in answers),
    }


def _empty_decisions(queue: dict, queue_path: Path) -> dict:
    source = queue["provenance"]
    return build_success_artifact(
        artifact_type="quality_review_decisions", contract_version="1.0.0",
        data={"queue_id": queue["data"]["queue_id"], "decisions": []},
        data_contract="quality/review_decisions",
        provenance={
            "run_id": uuid4().hex, "vertical": source["vertical"],
            "schema_version": source["schema_version"],
            "provider": None, "model": None, "document_input": None,
            "source_documents": [], "source_artifacts": [str(queue_path.resolve())],
        },
    )
