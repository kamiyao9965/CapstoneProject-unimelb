"""Queue-bound human decisions for post-extraction quality screening."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from hashlib import sha256

from src.common.json_artifacts import build_success_artifact, read_artifact, write_artifact
from src.evaluation.quality import QUALITY_QUEUE_VERSION, quality_queue_id
from src.verticals.manifest import VerticalManifest


DECISIONS = frozenset({"issue_found", "no_issue", "uncertain"})


def load_queue(path: str | Path) -> dict:
    queue = read_artifact(path, expected_type="quality_review_queue", data_contract="quality/review_queue")
    data = queue["data"]
    if queue["contract_version"] not in {"1.0.0", QUALITY_QUEUE_VERSION}:
        raise ValueError("Unsupported quality review queue contract version.")
    if quality_queue_id(data) != data["queue_id"]:
        raise ValueError("Quality review queue identity does not match its contents.")
    item_ids = [item["item_id"] for item in data["items"]]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("Quality review queue contains duplicate item identities.")
    if not queue_is_read_only(queue):
        inputs = data.get("audited_inputs", [])
        by_path = {entry["source_artifact"]: entry for entry in inputs}
        if not inputs or len(inputs) != data["audited_documents"] or len(by_path) != len(inputs):
            raise ValueError("Quality review queue requires complete, unique audited inputs.")
        source = queue["provenance"]
        if (source.get("vertical") != data["vertical"]
                or source.get("schema_version") != data["schema_version"]
                or source["source_artifacts"] != [entry["source_artifact"] for entry in inputs]
                or source["source_documents"] != [path for entry in inputs for path in (
                    [ref["source_path"] for ref in entry["source_document_refs"]]
                    if entry.get("source_document_refs") else [entry["source_document"]])]
                or any((entry["provider"], entry["model"]) != (source["provider"], source["model"])
                       for entry in inputs)):
            raise ValueError("Quality review queue provenance does not match its audited inputs.")
        for item in data["items"]:
            entry = by_path.get(item["source_artifact"])
            expected_source = entry and entry["source_document"]
            if entry and item.get("source_document_id"):
                reference = next((ref for ref in entry.get("source_document_refs", []) if ref["document_id"] == item["source_document_id"]), None)
                expected_source = reference and reference["source_path"]
            if entry is None or item["source_document"] != expected_source:
                raise ValueError("Quality review item does not match an audited input.")
    return queue


def queue_is_read_only(queue: dict) -> bool:
    """Legacy queues can be inspected but lack bindings needed for new decisions."""
    return queue["contract_version"] == "1.0.0"


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
    if queue_is_read_only(queue):
        raise ValueError("Legacy quality review queues are read-only; regenerate from verified reports in a new output directory.")
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


def _delivery_identity(
    manifest: VerticalManifest, schema_path: str | Path, artifact_path: str | Path,
) -> dict:
    from src.common.json_codec import loads_json
    from src.common.json_contracts import validate_inline_contract
    from src.schema.canonical import (
        require_approved_canonical_schema, compile_canonical_extraction_contract,
        validate_canonical_extraction_identities,
    )
    from src.schema_application.records import parse_extraction_artifact, resolve_extraction_sources

    schema_bytes = Path(schema_path).read_bytes()
    schema = require_approved_canonical_schema(loads_json(schema_bytes.decode("utf-8")))
    if schema["vertical"] != manifest.vertical:
        raise ValueError("Delivery schema vertical does not match the selected manifest.")
    artifact_bytes = Path(artifact_path).read_bytes()
    if len(artifact_bytes) > 16 * 1024 * 1024:
        raise ValueError("Delivery extraction exceeds the artifact size limit.")
    parsed = parse_extraction_artifact(
        artifact_bytes, vertical=manifest.vertical, schema_version=str(schema["version"]), require_identity=True,
    )
    validate_inline_contract(parsed.data, compile_canonical_extraction_contract(schema), "delivery_extraction")
    validate_canonical_extraction_identities(schema, parsed.data)
    group = resolve_extraction_sources(parsed, manifest)
    return {
        "vertical": manifest.vertical, "schema_version": str(schema["version"]),
        "schema_sha256": sha256(schema_bytes).hexdigest(),
        "source_artifact_sha256": sha256(artifact_bytes).hexdigest(),
        "document_group_id": group.group_id,
        "source_document_refs": [ref.to_dict() for ref in group.documents],
    }


def write_delivery_approval(
    *, manifest: VerticalManifest, schema_path: str | Path, artifact_path: str | Path,
    output_path: str | Path, reviewer: str, rationale: str, reviewed_at: str | None = None,
) -> Path:
    """Record an explicit human delivery decision without changing schema or extraction."""
    if not reviewer.strip() or not rationale.strip():
        raise ValueError("Delivery approval requires a reviewer and rationale.")
    timestamp = reviewed_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        parsed_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed_time.tzinfo is None:
            raise ValueError("Missing timezone")
    except (ValueError, TypeError) as exc:
        raise ValueError("Delivery approval timestamp must be an ISO datetime with timezone.") from exc
    identity = _delivery_identity(manifest, schema_path, artifact_path)
    artifact = build_success_artifact(
        artifact_type="delivery_approval", contract_version="1.0.0",
        data={**identity, "decision": "approved", "reviewer": reviewer.strip(),
              "rationale": rationale.strip(), "reviewed_at": timestamp},
        data_contract="quality/delivery_approval",
        provenance={
            "run_id": uuid4().hex, "vertical": manifest.vertical,
            "schema_version": identity["schema_version"], "provider": None, "model": None,
            "document_input": None,
            "source_documents": [ref["source_path"] for ref in identity["source_document_refs"]],
            "source_artifacts": [str(Path(artifact_path).resolve()), str(Path(schema_path).resolve())],
        },
    )
    return write_artifact(output_path, artifact, data_contract="quality/delivery_approval")


def require_delivery_approval(
    *, manifest: VerticalManifest, schema_path: str | Path, artifact_path: str | Path,
    quality_results_path: str | Path | None = None, approval_path: str | Path | None = None,
) -> dict:
    """Require a current judge pass or an immutable human approval for this exact result."""
    identity = _delivery_identity(manifest, schema_path, artifact_path)
    artifact_file, schema_file = Path(artifact_path).resolve(), Path(schema_path).resolve()
    if approval_path is not None:
        approval = read_artifact(approval_path, expected_type="delivery_approval", data_contract="quality/delivery_approval")
        if any(approval["data"].get(key) != value for key, value in identity.items()):
            raise ValueError("Delivery approval no longer matches the schema, extraction or complete PDF group.")
        if approval["provenance"]["source_artifacts"] != [str(artifact_file), str(schema_file)]:
            raise ValueError("Delivery approval belongs to different input files.")
        return {"status": "human_approved", "proof_path": str(Path(approval_path).resolve())}
    if quality_results_path is None:
        raise ValueError("CSV delivery requires a judge pass or explicit human delivery approval.")
    from src.common.model_config import ModelSelection
    from src.evaluation.quality import (
        load_quality_results, _verify_reusable_report, _prompt_bundle_sha256, _shared_quality_prompt_sha256,
    )
    batch = load_quality_results(quality_results_path)["data"]
    entry = next((entry for entry in batch["documents"] if Path(entry["source_artifact"]).resolve() == artifact_file), None)
    if entry is None or entry["status"] != "completed":
        raise ValueError("This extraction has no completed judge report.")
    report = read_artifact(entry["report_path"], expected_type="quality_audit", data_contract="quality/audit_report")
    if report["data"] != entry["report"]:
        raise ValueError("Judge report differs from the batch delivery proof.")
    data = report["data"]
    if (data["verdict"] != "pass" or data["findings"] or data["correctness"] != "supported"
            or data["evidence_support"] != "supported" or data["uncertainty"] != "low"):
        raise ValueError("Judge did not pass this extraction; explicit human delivery approval is required.")
    prompt_sha = sha256(Path(manifest.prompt("quality_audit")).read_bytes()).hexdigest()
    shared_sha = _shared_quality_prompt_sha256()
    _verify_reusable_report(
        report, artifact_path=artifact_file, artifact_sha=identity["source_artifact_sha256"],
        schema_file=schema_file, schema_sha=identity["schema_sha256"], prompt_sha=prompt_sha,
        prompt_bundle_sha=_prompt_bundle_sha256(prompt_sha, shared_sha), shared_prompt_sha=shared_sha,
        selection=ModelSelection(provider=batch["provider"], model=batch["model"], document_input="markdown"),
        source_root=manifest.path("input_root"), check_pdf=True,
        vertical=manifest.vertical, schema_version=identity["schema_version"], document_parser=None, manifest=manifest,
    )
    return {"status": "judge_pass", "proof_path": str(Path(quality_results_path).resolve())}
