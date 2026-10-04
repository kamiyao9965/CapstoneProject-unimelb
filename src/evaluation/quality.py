"""Optional, non-mutating LLM quality screen for approved Canonical extractions."""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from src.pdf_ingestion.adapter import (
    DEFAULT_DOCUMENT_PARSER,
    ingest_pdfs,
    render_documents_for_prompt,
    render_page,
    require_document_parser,
)
from src.common.json_artifacts import build_success_artifact, read_artifact, write_artifact
from src.common.json_codec import dumps_json, loads_json
from src.common.json_contracts import load_contract, validate_contract, validate_inline_contract
from src.common.model_config import ModelSelection, require_structured_output_capability
from src.common.model_provider import ModelProvider, ProviderRequest, StructuredOutputSpec
from src.common.openai_run import append_jsonl
from src.common.structured_output import (
    StructuredBusinessValidationError,
    StructuredOutputFailure,
    run_structured_output,
)
from src.schema.canonical import compile_canonical_extraction_contract, require_approved_canonical_schema
from src.schema.sampler import sample_quality_passes
from src.schema_application.records import parse_extraction_artifact
from src.verticals.manifest import VerticalManifest
from src.verticals.registry import get_shared_prompt


MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_DOCUMENT_CHARS = 120_000
DEFAULT_MAX_EXTRACTION_CHARS = 60_000
QUALITY_SHARED_PROMPTS = (
    "quality_audit_request", "structured_repair", "deepseek_json_schema",
)
# Shared prompt text that produced reports before the optional bundle hash existed.
LEGACY_QUALITY_SHARED_SHA256 = "4663c66a3594cfda06fb0f5480ab8adf765eb1c154177b6c418b2e1d9244a743"


def _shared_quality_prompt_sha256() -> str:
    text = "\0".join(get_shared_prompt(name) for name in QUALITY_SHARED_PROMPTS)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _prompt_bundle_sha256(prompt_sha: str, shared_sha: str) -> str:
    return hashlib.sha256(f"{prompt_sha}\0{shared_sha}".encode("ascii")).hexdigest()


@dataclass(frozen=True)
class QualityAuditRun:
    results_path: Path
    queue_path: Path | None
    completed_documents: int
    failed_documents: int
    pending_documents: int


def audit_one(
    *,
    manifest: VerticalManifest,
    schema_path: str | Path,
    artifact_path: str | Path,
    source_root: str | Path,
    selection: ModelSelection,
    provider: ModelProvider,
    usage_log_path: str | Path | None = None,
    document_parser: str | None = None,
    max_document_chars: int = DEFAULT_MAX_DOCUMENT_CHARS,
    max_extraction_chars: int = DEFAULT_MAX_EXTRACTION_CHARS,
) -> dict:
    """Judge one extraction against its original PDF; return an unwritten report."""
    manifest.require_capability("quality_audit")
    if selection.document_input != "markdown":
        raise ValueError("Quality audit requires markdown document input; no PDF upload is used.")
    require_structured_output_capability(selection)
    if max_document_chars <= 0 or max_extraction_chars <= 0:
        raise ValueError("Context maximum lengths must be positive.")

    schema_file = Path(schema_path).resolve()
    schema_bytes = schema_file.read_bytes()
    schema = require_approved_canonical_schema(loads_json(schema_bytes.decode("utf-8")))
    if schema["vertical"] != manifest.vertical:
        raise ValueError("Canonical Schema vertical does not match the manifest.")
    source_file = Path(artifact_path).resolve()
    if source_file.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ValueError("Extraction artifact exceeds the quality audit size limit.")
    artifact_bytes = source_file.read_bytes()
    parsed = parse_extraction_artifact(
        artifact_bytes, vertical=manifest.vertical,
        schema_version=str(schema["version"]), require_identity=True,
    )
    extraction_contract = compile_canonical_extraction_contract(schema)
    validate_inline_contract(parsed.data, extraction_contract, "canonical_extraction")
    extraction_text = dumps_json(parsed.data, ensure_ascii=False, sort_keys=True)
    if len(extraction_text) > max_extraction_chars:
        raise ValueError("Extraction exceeds the configured maximum context length.")

    root = Path(source_root).resolve()
    pdf_path = Path(parsed.source_document)
    pdf_path = (pdf_path if pdf_path.is_absolute() else Path.cwd() / pdf_path).resolve()
    if not pdf_path.is_relative_to(root):
        raise ValueError("Extraction source PDF escapes the configured source root.")
    if pdf_path.suffix.lower() != ".pdf" or not pdf_path.is_file():
        raise ValueError("Extraction source must be an existing PDF under the source root.")
    if pdf_path.stat().st_size > MAX_PDF_BYTES:
        raise ValueError("Source PDF exceeds the quality audit size limit.")
    parser = require_document_parser(
        document_parser or parsed.artifact.get("provenance", {}).get("document_parser")
        or parsed.artifact.get("document_parser")
        or DEFAULT_DOCUMENT_PARSER
    )
    documents = ingest_pdfs(
        (pdf_path,), pdf_root=root,
        cache_dir=manifest.path("cache_root"),
        document_parser=parser,
    )
    if len(documents) != 1 or not documents[0].pages:
        raise ValueError("Source PDF yielded no parseable pages.")
    document = documents[0]
    pdf_sha = _file_sha256(pdf_path)
    if document.pdf_hash != pdf_sha:
        raise ValueError("Parsed PDF hash differs from the source PDF bytes.")
    document_text = render_documents_for_prompt(documents)
    if not document_text.strip() or len(document_text) > max_document_chars:
        raise ValueError("Parsed PDF is empty or exceeds the configured maximum context length.")

    prompt = Path(manifest.prompt("quality_audit")).read_text(encoding="utf-8")
    fields = [
        {"name": field["name"], "description": field["description"],
         "type": field["type"], "values": field["values"]}
        for field in schema["fields"]
    ]
    user_text = get_shared_prompt("quality_audit_request").format(
        fields_json=dumps_json(fields, ensure_ascii=False),
        extraction_text=extraction_text,
        document_text=document_text,
    )
    field_names = {field["name"] for field in schema["fields"]}
    output = schema["output"]
    collection = parsed.data[output["collection"]]
    products = collection if isinstance(collection, list) else [collection]
    page_numbers = {page.page_num for page in document.pages}

    def validate_judgement(value: object) -> object:
        assert isinstance(value, dict)
        findings = value["findings"]
        if value["verdict"] == "pass" and findings:
            raise StructuredBusinessValidationError("pass_has_findings", "A pass verdict cannot contain findings.")
        if value["verdict"] == "pass" and (
            value["correctness"] != "supported"
            or value["evidence_support"] != "supported"
            or value["uncertainty"] != "low"
        ):
            raise StructuredBusinessValidationError("pass_not_supported", "A pass verdict requires supported evidence and low uncertainty.")
        if value["verdict"] == "review" and not findings:
            raise StructuredBusinessValidationError("review_without_findings", "A review verdict requires at least one finding.")
        for index, finding in enumerate(findings):
            product_index = finding["product_index"]
            field_name = finding["field_name"]
            if (product_index is None) != (field_name is None):
                raise StructuredBusinessValidationError("incomplete_field_identity", f"Finding {index} must identify both product and field, or neither.")
            if product_index is not None and product_index >= len(products):
                raise StructuredBusinessValidationError("invalid_product_index", f"Finding {index} product index is out of range.")
            if field_name is not None and field_name not in field_names:
                raise StructuredBusinessValidationError("invalid_field_name", f"Finding {index} field name is not in the Canonical Schema.")
            if finding["source_page"] is not None and finding["source_page"] not in page_numbers:
                raise StructuredBusinessValidationError("invalid_source_page", f"Finding {index} page is not in the source PDF.")
            if (finding["source_page"] is None) != (finding["source_quote"] is None):
                raise StructuredBusinessValidationError("incomplete_citation", f"Finding {index} requires both a page and quote, or neither.")
        return value

    run_id = uuid4().hex
    request = ProviderRequest(
        selection=selection, system_prompt=prompt, user_text=user_text,
        document_paths=(), timeout_seconds=600.0, cleanup_documents=True,
        request_params={}, background=False, poll_interval=5.0,
        structured_output=StructuredOutputSpec(
            name="quality_judge_result", schema=load_contract("quality/judge_result"),
            strict=True,
        ),
    )
    started = time.perf_counter()
    try:
        result = run_structured_output(
            provider, request, data_contract="quality/judge_result",
            business_validator=validate_judgement,
        )
    except StructuredOutputFailure as exc:
        _log_attempts(usage_log_path, exc.result.attempts, run_id, time.perf_counter() - started)
        raise
    _log_attempts(usage_log_path, result.attempts, run_id, time.perf_counter() - started)
    assert result.data is not None
    judged = result.data
    page_text = {page.page_num: render_page(page) for page in document.pages}
    findings = []
    for finding in judged["findings"]:
        page = finding["source_page"]
        quote = finding["source_quote"]
        verified = bool(page is not None and quote and _normalise(quote) in _normalise(page_text[page]))
        product_index, field_name = finding["product_index"], finding["field_name"]
        extracted_value = products[product_index].get(field_name) if product_index is not None else None
        findings.append({**finding, "citation_verified": verified, "extracted_value": extracted_value})

    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    report_data = {
        "source_artifact_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
        "schema_sha256": hashlib.sha256(schema_bytes).hexdigest(),
        "pdf_sha256": pdf_sha,
        "prompt_sha256": prompt_sha,
        "prompt_bundle_sha256": _prompt_bundle_sha256(
            prompt_sha, _shared_quality_prompt_sha256()
        ),
        **{key: judged[key] for key in (
            "verdict", "correctness", "evidence_support", "uncertainty", "summary"
        )},
        "findings": findings,
    }
    return build_success_artifact(
        artifact_type="quality_audit", contract_version="1.0.0",
        data=report_data, data_contract="quality/audit_report",
        provenance={
            "run_id": run_id, "vertical": manifest.vertical,
            "schema_version": str(schema["version"]), "provider": selection.provider,
            "model": selection.model, "document_input": "markdown",
            "document_parser": parser, "source_documents": [str(pdf_path)],
            "source_artifacts": [str(source_file), str(schema_file)],
        },
    )


def run_quality_audit(
    *, manifest: VerticalManifest, schema_path: str | Path,
    artifact_dir: str | Path, source_root: str | Path, output_dir: str | Path,
    selection: ModelSelection, provider: ModelProvider | None,
    sample_rate: float = 0.05, seed: int = 42,
    document_parser: str | None = None,
    max_document_chars: int = DEFAULT_MAX_DOCUMENT_CHARS,
    max_extraction_chars: int = DEFAULT_MAX_EXTRACTION_CHARS,
    resume: bool = False, summary_only: bool = False,
    max_failures: int = 3,
) -> QualityAuditRun:
    """Audit a batch, preserving per-document progress and a readable JSON overview."""
    manifest.require_capability("quality_audit")
    source_dir = Path(artifact_dir).resolve()
    destination = Path(output_dir).resolve()
    if not source_dir.is_dir():
        raise ValueError("Extraction artifact directory does not exist.")
    if destination.is_relative_to(source_dir) or source_dir.is_relative_to(destination):
        raise ValueError("Quality output directory must not overlap extraction artifacts.")
    if resume or summary_only:
        if not destination.is_dir():
            raise FileNotFoundError("Existing quality output directory is required for resume or summary-only mode.")
    elif destination.exists():
        raise FileExistsError("Quality output directory must be new; use --resume to reuse one.")
    artifacts = sorted(path for path in source_dir.rglob("*.json") if "errors" not in path.relative_to(source_dir).parts)
    if not artifacts:
        raise ValueError("No extraction artifacts found in the folder.")
    if not 0 <= sample_rate <= 1:
        raise ValueError("Pass sample rate must be between 0 and 1.")
    if max_failures <= 0:
        raise ValueError("Maximum document failures must be positive.")
    if not summary_only and provider is None:
        raise ValueError("A model provider is required unless --summary-only is selected.")
    schema_file = Path(schema_path).resolve()
    schema_bytes = schema_file.read_bytes()
    schema = require_approved_canonical_schema(loads_json(schema_bytes.decode("utf-8")))
    if schema["vertical"] != manifest.vertical:
        raise ValueError("Canonical Schema vertical does not match the manifest.")
    schema_sha = hashlib.sha256(schema_bytes).hexdigest()
    prompt_sha = hashlib.sha256(
        Path(manifest.prompt("quality_audit")).read_text(encoding="utf-8").encode("utf-8")
    ).hexdigest()
    shared_prompt_sha = _shared_quality_prompt_sha256()
    prompt_bundle_sha = _prompt_bundle_sha256(prompt_sha, shared_prompt_sha)
    results_path = destination / "results.json"
    batch_run_id, prior_by_path = _load_prior_entries(
        results_path, artifacts,
        expected_identity=(manifest.vertical, str(schema["version"]), schema_sha,
                           prompt_sha, selection.provider, selection.model),
        prompt_bundle_sha=prompt_bundle_sha, shared_prompt_sha=shared_prompt_sha,
    )
    existing_report_paths = set((destination / "reports").rglob("*.json"))
    expected_report_paths = {destination / "reports" / path.relative_to(source_dir) for path in artifacts}
    if existing_report_paths - expected_report_paths:
        raise ValueError("Quality output contains reports outside the selected extraction batch.")

    entries: list[dict] = []
    for artifact_path in artifacts:
        artifact_sha = _file_sha256(artifact_path)
        prior_entry = prior_by_path.get(str(artifact_path))
        if prior_entry and prior_entry["source_artifact_sha256"] != artifact_sha:
            raise ValueError("An extraction artifact changed since the quality run began.")
        report_path = destination / "reports" / artifact_path.relative_to(source_dir)
        if report_path.exists():
            report = read_artifact(report_path, expected_type="quality_audit",
                                   data_contract="quality/audit_report")
            _verify_reusable_report(
                report, artifact_path=artifact_path, artifact_sha=artifact_sha,
                schema_file=schema_file, schema_sha=schema_sha, prompt_sha=prompt_sha,
                prompt_bundle_sha=prompt_bundle_sha,
                shared_prompt_sha=shared_prompt_sha,
                selection=selection, source_root=Path(source_root), check_pdf=not summary_only,
                vertical=manifest.vertical, schema_version=str(schema["version"]),
                document_parser=document_parser,
            )
            entries.append({
                "source_artifact": str(artifact_path),
                "source_artifact_sha256": artifact_sha,
                "source_document": report["provenance"]["source_documents"][0],
                "status": "completed", "report_path": str(report_path),
                "report": report["data"], "failure": None,
            })
            continue
        source_document = _source_document_for_summary(artifact_path, manifest.vertical, str(schema["version"]))
        failure = prior_entry["failure"] if prior_entry and prior_entry["status"] == "failed" else None
        entries.append({
            "source_artifact": str(artifact_path),
            "source_artifact_sha256": artifact_sha,
            "source_document": source_document,
            "status": "failed" if failure else "pending",
            "report_path": None, "report": None, "failure": failure,
        })

    def persist_results() -> Path:
        counts = {status: sum(item["status"] == status for item in entries)
                  for status in ("completed", "failed", "pending")}
        data = {
            "vertical": manifest.vertical, "schema_version": str(schema["version"]),
            "schema_sha256": schema_sha, "prompt_sha256": prompt_sha,
            "prompt_bundle_sha256": prompt_bundle_sha,
            "provider": selection.provider, "model": selection.model,
            "status": "complete" if counts["completed"] == len(entries) else "partial",
            "total_documents": len(entries),
            "completed_documents": counts["completed"],
            "failed_documents": counts["failed"],
            "pending_documents": counts["pending"],
            "documents": entries,
        }
        result = build_success_artifact(
            artifact_type="quality_batch_results", contract_version="1.0.0",
            data=data, data_contract="quality/batch_results",
            provenance={
                "run_id": batch_run_id, "vertical": manifest.vertical,
                "schema_version": str(schema["version"]),
                "provider": selection.provider, "model": selection.model,
                "document_input": "markdown",
                "source_documents": [item["source_document"] for item in entries if item["source_document"]],
                "source_artifacts": [str(path) for path in artifacts],
            },
        )
        return write_artifact(results_path, result, data_contract="quality/batch_results", overwrite=True)

    persist_results()
    if summary_only:
        return _quality_run_result(results_path)

    assert provider is not None
    new_failures = 0
    for entry in entries:
        if entry["status"] == "completed":
            continue
        artifact_path = Path(entry["source_artifact"])
        fatal_failure = False
        try:
            report = audit_one(
                manifest=manifest, schema_path=schema_file,
                artifact_path=artifact_path, source_root=source_root,
                selection=selection, provider=provider,
                usage_log_path=destination / "quality_usage.jsonl",
                document_parser=document_parser,
                max_document_chars=max_document_chars,
                max_extraction_chars=max_extraction_chars,
            )
        except Exception as exc:
            entry["status"] = "failed"
            entry["failure"] = _safe_quality_failure(exc)
            new_failures += 1
            # Unknown provider/runtime failures may reflect a shared outage;
            # do not launch another potentially billable request.
            fatal_failure = not isinstance(exc, (StructuredOutputFailure, ValueError, FileNotFoundError))
        else:
            report_path = destination / "reports" / artifact_path.relative_to(source_dir)
            write_artifact(report_path, report, data_contract="quality/audit_report")
            entry.update(
                status="completed", source_document=report["provenance"]["source_documents"][0],
                report_path=str(report_path), report=report["data"], failure=None,
            )
        persist_results()
        if fatal_failure or new_failures >= max_failures:
            break

    queue_path: Path | None = None
    if all(entry["status"] == "completed" for entry in entries):
        reports = [read_artifact(entry["report_path"], expected_type="quality_audit",
                                 data_contract="quality/audit_report") for entry in entries]
        queue = build_review_queue(reports, sample_rate=sample_rate, seed=seed)
        candidate = destination / "review_queue.json"
        if candidate.exists():
            existing = read_artifact(candidate, expected_type="quality_review_queue",
                                     data_contract="quality/review_queue")
            if existing["data"]["queue_id"] != queue["data"]["queue_id"]:
                raise ValueError("Existing quality review queue does not match this completed batch.")
        else:
            write_artifact(candidate, queue, data_contract="quality/review_queue")
        queue_path = candidate
    return _quality_run_result(results_path, queue_path)


def load_quality_results(path: str | Path) -> dict:
    """Validate one aggregate JSON, including its embedded per-PDF judge reports."""
    result = read_artifact(path, expected_type="quality_batch_results",
                           data_contract="quality/batch_results")
    data = result["data"]
    entries = data["documents"]
    if len(entries) != data["total_documents"]:
        raise ValueError("Quality results total does not match its document list.")
    if len({entry["source_artifact"] for entry in entries}) != len(entries):
        raise ValueError("Quality results contain duplicate extraction artifacts.")
    for status, count_key in (("completed", "completed_documents"),
                              ("failed", "failed_documents"), ("pending", "pending_documents")):
        if sum(entry["status"] == status for entry in entries) != data[count_key]:
            raise ValueError("Quality results document counts are inconsistent.")
    if (data["status"] == "complete") != (data["completed_documents"] == len(entries)):
        raise ValueError("Quality results completion status is inconsistent.")
    for entry in entries:
        status = entry["status"]
        if status == "completed":
            if entry["report"] is None or entry["report_path"] is None or entry["failure"] is not None:
                raise ValueError("Completed quality result is missing its report.")
            validate_contract(entry["report"], "quality/audit_report")
            if entry["report"]["source_artifact_sha256"] != entry["source_artifact_sha256"]:
                raise ValueError("Quality result report does not match its extraction artifact.")
            if (entry["report"]["schema_sha256"] != data["schema_sha256"]
                    or entry["report"]["prompt_sha256"] != data["prompt_sha256"]):
                raise ValueError("Quality result report does not match its schema or judge prompt.")
            batch_bundle = data.get("prompt_bundle_sha256")
            report_bundle = entry["report"].get("prompt_bundle_sha256")
            if batch_bundle is not None and report_bundle is not None and report_bundle != batch_bundle:
                raise ValueError("Quality result report used different shared prompt templates.")
            if batch_bundle is not None and report_bundle is None and batch_bundle != _prompt_bundle_sha256(
                data["prompt_sha256"], LEGACY_QUALITY_SHARED_SHA256
            ):
                raise ValueError("Legacy quality report cannot belong to this prompt bundle.")
        elif status == "failed":
            if entry["failure"] is None or entry["report"] is not None or entry["report_path"] is not None:
                raise ValueError("Failed quality result has inconsistent report/failure data.")
        elif entry["report"] is not None or entry["failure"] is not None or entry["report_path"] is not None:
            raise ValueError("Pending quality result has report/failure data.")
    return result


def _load_prior_entries(
    results_path: Path, artifacts: Sequence[Path], *,
    expected_identity: tuple[str, str, str, str, str, str],
    prompt_bundle_sha: str, shared_prompt_sha: str,
) -> tuple[str, dict[str, dict]]:
    """Load resumable progress only when the batch and judge inputs still match."""
    batch_run_id = uuid4().hex
    if not results_path.exists():
        return batch_run_id, {}
    prior_results = load_quality_results(results_path)
    prior = prior_results["data"]
    batch_run_id = prior_results["provenance"]["run_id"]
    actual = (prior["vertical"], prior["schema_version"], prior["schema_sha256"],
              prior["prompt_sha256"], prior["provider"], prior["model"])
    if actual != expected_identity:
        raise ValueError("Existing quality results belong to a different schema, prompt or judge model.")
    prior_bundle_sha = prior.get("prompt_bundle_sha256")
    if prior_bundle_sha is not None and prior_bundle_sha != prompt_bundle_sha:
        raise ValueError("Existing quality results used different shared prompt templates.")
    if prior_bundle_sha is None and shared_prompt_sha != LEGACY_QUALITY_SHARED_SHA256:
        raise ValueError("Legacy quality results cannot resume after shared prompt changes.")
    prior_by_path = {item["source_artifact"]: item for item in prior["documents"]}
    if set(prior_by_path) != {str(path) for path in artifacts}:
        raise ValueError("Existing quality results have a different extraction artifact set.")
    return batch_run_id, prior_by_path


def _quality_run_result(results_path: Path, queue_path: Path | None = None) -> QualityAuditRun:
    final = load_quality_results(results_path)["data"]
    return QualityAuditRun(
        results_path, queue_path, final["completed_documents"],
        final["failed_documents"], final["pending_documents"],
    )


def _source_document_for_summary(path: Path, vertical: str, schema_version: str) -> str | None:
    try:
        parsed = parse_extraction_artifact(path.read_bytes(), vertical=vertical,
                                           schema_version=schema_version, require_identity=True)
    except (OSError, ValueError, UnicodeError):
        return None
    return parsed.source_document


def _verify_reusable_report(
    report: Mapping, *, artifact_path: Path, artifact_sha: str,
    schema_file: Path, schema_sha: str, prompt_sha: str,
    prompt_bundle_sha: str, shared_prompt_sha: str,
    selection: ModelSelection, source_root: Path, check_pdf: bool,
    vertical: str, schema_version: str, document_parser: str | None,
) -> None:
    source = report["provenance"]
    data = report["data"]
    if source["source_artifacts"] != [str(artifact_path), str(schema_file)]:
        raise ValueError("Existing quality report is bound to different input files.")
    if (source.get("vertical") != vertical or source.get("schema_version") != schema_version
            or source.get("document_input") != "markdown"):
        raise ValueError("Existing quality report has an incompatible provenance identity.")
    if source["provider"] != selection.provider or source["model"] != selection.model:
        raise ValueError("Existing quality report used a different judge model.")
    if document_parser is not None and source.get("document_parser") != document_parser:
        raise ValueError("Existing quality report used a different PDF parser route.")
    if data["source_artifact_sha256"] != artifact_sha or data["schema_sha256"] != schema_sha:
        raise ValueError("Existing quality report input hashes do not match.")
    if data["prompt_sha256"] != prompt_sha:
        raise ValueError("Existing quality report used a different judge prompt.")
    report_bundle_sha = data.get("prompt_bundle_sha256")
    if report_bundle_sha is not None and report_bundle_sha != prompt_bundle_sha:
        raise ValueError("Existing quality report used different shared prompt templates.")
    if report_bundle_sha is None and shared_prompt_sha != LEGACY_QUALITY_SHARED_SHA256:
        raise ValueError("Legacy quality report cannot resume after shared prompt changes.")
    if len(source["source_documents"]) != 1:
        raise ValueError("Existing quality report must identify exactly one source PDF.")
    parsed = parse_extraction_artifact(
        artifact_path.read_bytes(), vertical=vertical,
        schema_version=schema_version, require_identity=True,
    )
    expected_pdf = Path(parsed.source_document)
    if not expected_pdf.is_absolute():
        expected_pdf = Path.cwd() / expected_pdf
    if Path(source["source_documents"][0]).resolve() != expected_pdf.resolve():
        raise ValueError("Existing quality report source PDF does not match its extraction artifact.")
    if check_pdf:
        pdf_path = Path(source["source_documents"][0]).resolve()
        if not pdf_path.is_relative_to(source_root.resolve()) or not pdf_path.is_file():
            raise ValueError("Existing quality report source PDF is outside the configured root or missing.")
        if _file_sha256(pdf_path) != data["pdf_sha256"]:
            raise ValueError("Existing quality report source PDF changed since judging.")


_SAFE_JUDGE_PATH = re.compile(
    r"\$(?:\.(?:verdict|correctness|evidence_support|uncertainty|summary|findings|"
    r"product_index|field_name|issue_type|reason|source_page|source_quote)|\[\d{1,2}\])*"
)


def _safe_quality_failure(exc: Exception) -> dict:
    if isinstance(exc, StructuredOutputFailure):
        last = exc.result.attempts[-1]
        kind = last.failure_kind or "structured_output_invalid"
        paths = [item["path"] if _SAFE_JUDGE_PATH.fullmatch(item["path"]) else "$"
                 for item in last.errors[:5]]
        return {
            "kind": kind, "code": last.error_code or kind,
            "attempts": len(exc.result.attempts), "paths": paths,
        }
    kind = "input_validation" if isinstance(exc, (ValueError, FileNotFoundError)) else "runtime_error"
    return {"kind": kind, "code": kind, "attempts": 0, "paths": []}


def build_review_queue(reports: Sequence[Mapping], *, sample_rate: float, seed: int) -> dict:
    """Queue all alerts/unknowns plus a stable document-level pass sample."""
    if not reports:
        raise ValueError("Cannot build a quality queue without reports.")
    if not 0 <= sample_rate <= 1:
        raise ValueError("Pass sample rate must be between 0 and 1.")
    for report in reports:
        if report.get("status") != "success" or report.get("artifact_type") != "quality_audit":
            raise ValueError("Review queue requires successful quality audit reports.")
        validate_contract(report["data"], "quality/audit_report")
    schema_ids = {(r["provenance"]["vertical"], r["provenance"]["schema_version"], r["data"]["schema_sha256"]) for r in reports}
    if len(schema_ids) != 1:
        raise ValueError("Quality reports must use the same vertical and exact schema bytes.")
    judge_ids = {
        (
            r["provenance"]["provider"], r["provenance"]["model"],
            r["data"].get("prompt_bundle_sha256") or _prompt_bundle_sha256(
                r["data"]["prompt_sha256"], LEGACY_QUALITY_SHARED_SHA256
            ),
        )
        for r in reports
    }
    if len(judge_ids) != 1:
        raise ValueError("Quality reports must use the same judge model and prompt bytes.")
    vertical, version, schema_sha = next(iter(schema_ids))
    ordered = sorted(reports, key=lambda r: r["provenance"]["source_artifacts"][0])
    paths = [r["provenance"]["source_artifacts"][0] for r in ordered]
    if len(paths) != len(set(paths)):
        raise ValueError("Quality reports contain duplicate extraction artifact paths.")
    passes = [r["provenance"]["source_artifacts"][0] for r in ordered if r["data"]["verdict"] == "pass"]
    sampled = set(sample_quality_passes(passes, rate=sample_rate, seed=seed))
    items = []
    for report in ordered:
        data = report["data"]
        source = report["provenance"]
        common = {
            "source_document": source["source_documents"][0],
            "source_artifact": source["source_artifacts"][0],
        }
        if data["verdict"] == "pass" and common["source_artifact"] in sampled:
            items.append(_queue_item(
                data, common, "pass_sample", -1, None,
                f"Seeded spot check of a judge pass. Verify material fields and omissions. Judge summary: {data['summary']}",
            ))
        elif data["verdict"] == "uncertain" and not data["findings"]:
            items.append(_queue_item(data, common, "uncertain", -1, None, data["summary"]))
        for index, finding in enumerate(data["findings"]):
            items.append(_queue_item(data, common, "finding", index, finding, finding["reason"]))
    payload = {
        "vertical": vertical, "schema_version": version, "schema_sha256": schema_sha,
        "seed": seed, "pass_sample_rate": sample_rate,
        "audited_documents": len(ordered),
        "judge_review_documents": sum(r["data"]["verdict"] == "review" for r in ordered),
        "judge_uncertain_documents": sum(r["data"]["verdict"] == "uncertain" for r in ordered),
        "items": items,
    }
    payload["queue_id"] = quality_queue_id(payload)
    return build_success_artifact(
        artifact_type="quality_review_queue", contract_version="1.0.0",
        data=payload, data_contract="quality/review_queue",
        provenance={
            "run_id": uuid4().hex, "vertical": vertical, "schema_version": version,
            "provider": ordered[0]["provenance"]["provider"],
            "model": ordered[0]["provenance"]["model"], "document_input": "markdown",
            "source_documents": [r["provenance"]["source_documents"][0] for r in ordered],
            "source_artifacts": [r["provenance"]["source_artifacts"][0] for r in ordered],
        },
    )


def quality_queue_id(payload: Mapping) -> str:
    stable = {key: value for key, value in payload.items() if key != "queue_id"}
    return hashlib.sha256(dumps_json(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _queue_item(data: Mapping, common: Mapping, kind: str, index: int, finding: Mapping | None, reason: str) -> dict:
    identity = f"{data['source_artifact_sha256']}:{common['source_artifact']}:{kind}:{index}"
    return {
        "item_id": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "kind": kind, **common,
        "product_index": finding["product_index"] if finding else None,
        "field_name": finding["field_name"] if finding else None,
        "issue_type": finding["issue_type"] if finding else None,
        "reason": reason,
        "source_page": finding["source_page"] if finding else None,
        "source_quote": finding["source_quote"] if finding else None,
        "citation_verified": finding["citation_verified"] if finding else False,
        "extracted_value": finding["extracted_value"] if finding else None,
    }


def _normalise(value: str) -> str:
    return " ".join(value.casefold().split())


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _log_attempts(path: str | Path | None, attempts: Sequence, run_id: str, duration: float) -> None:
    for attempt in attempts:
        usage = attempt.response.usage
        append_jsonl(
            Path(path) if path is not None else None,
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "quality_audit", "run_id": run_id,
                "provider": attempt.response.provider, "model": attempt.response.model,
                "attempt_number": attempt.number, "validation_succeeded": not attempt.errors,
                "failure_kind": attempt.failure_kind, "error_code": attempt.error_code,
                "duration_seconds": round(duration, 3),
                "input_tokens": usage.input_tokens if usage else None,
                "output_tokens": usage.output_tokens if usage else None,
                "total_tokens": usage.total_tokens if usage else None,
            },
            error_label="quality audit usage log",
        )
