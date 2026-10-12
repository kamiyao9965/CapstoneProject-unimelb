"""Export validated PostgreSQL product records as one portable CSV view."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

from src.common.json_artifacts import write_text_output
from src.common.json_codec import dumps_json
from src.evaluation.quality_review import require_delivery_approval
from src.storage.canonical import compile_vertical_storage_metadata
from src.storage.repository import deterministic_product_id, deterministic_release_id
from src.storage.service import (
    PreparedStorageLoad, inspect_extraction_directory, prepare_storage_load,
)
from src.verticals.manifest import VerticalManifest


_PRODUCT_COLUMNS = (
    "product_id", "release_id", "vertical_code", "insurer_code", "canonical_name",
    "source_product_type", "document_id", "document_type", "title", "source_path",
    "sha256", "schema_version_id", "version", "run_id", "provider", "model",
    "source_documents", "document_group_id", "quality_status", "delivery_proof",
    "document_fields",
)


@dataclass(frozen=True)
class CsvExportSummary:
    output_path: Path
    artifacts_exported: int
    products_exported: int


def export_extraction_artifact(
    *,
    manifest: VerticalManifest,
    schema_path: str | Path,
    artifact_path: str | Path,
    insurer_code: str,
    output_path: str | Path,
    quality_results_path: str | Path | None = None,
    approval_path: str | Path | None = None,
) -> CsvExportSummary:
    """Validate one existing extraction and write a new product CSV without a DB."""
    prepared = prepare_storage_load(
        manifest=manifest, schema_path=schema_path,
        artifact_path=artifact_path, insurer_code=insurer_code,
        for_csv=True,
    )
    proof = require_delivery_approval(
        manifest=manifest, schema_path=schema_path, artifact_path=artifact_path,
        quality_results_path=quality_results_path, approval_path=approval_path,
    )
    return _write_product_csv([prepared], output_path, [proof])


def export_extraction_directory(
    *,
    manifest: VerticalManifest,
    schema_path: str | Path,
    artifact_dir: str | Path,
    output_path: str | Path,
    quality_results_path: str | Path | None = None,
    approval_dir: str | Path | None = None,
) -> CsvExportSummary:
    """Validate an entire folder before writing one CSV; failures leave no output."""
    manifest.require_capability("csv_export")
    source = Path(artifact_dir).resolve()
    approvals = Path(approval_dir).resolve() if approval_dir is not None else None
    if approvals is not None:
        if source.is_relative_to(approvals) or approvals.is_relative_to(source):
            raise ValueError("Extraction and delivery approval directories must not overlap.")
    prepared: list[PreparedStorageLoad] = []
    proofs: list[dict] = []
    errors: list[str] = []
    for entry in inspect_extraction_directory(manifest=manifest, artifact_dir=artifact_dir, for_csv=True):
        if entry.error is not None:
            errors.append(f"{entry.artifact_path}: {entry.error}")
            continue
        assert entry.insurer_code is not None
        try:
            prepared.append(prepare_storage_load(
                manifest=manifest, schema_path=schema_path,
                artifact_path=entry.artifact_path, insurer_code=entry.insurer_code,
                for_csv=True,
            ))
            approval = approvals / entry.artifact_path.resolve().relative_to(source) if approvals is not None else None
            proofs.append(require_delivery_approval(
                manifest=manifest, schema_path=schema_path, artifact_path=entry.artifact_path,
                quality_results_path=quality_results_path,
                approval_path=approval if approval is not None and approval.is_file() else None,
            ))
        except (OSError, ValueError) as exc:
            errors.append(f"{entry.artifact_path}: {exc}")
    if errors:
        raise ValueError("CSV export failed; no output was written:\n" + "\n".join(errors))
    return _write_product_csv(prepared, output_path, proofs)


def _write_product_csv(
    prepared_loads: list[PreparedStorageLoad], output_path: str | Path, proofs: list[dict],
) -> CsvExportSummary:
    destination = Path(output_path)
    if destination.suffix.casefold() != ".csv":
        raise ValueError("CSV output path must end in .csv.")
    schema = prepared_loads[0].schema_payload
    extension = compile_vertical_storage_metadata(schema).table
    detail_columns = tuple(name for name in extension.columns.keys() if name != "release_id")
    collisions = set(_PRODUCT_COLUMNS).intersection(detail_columns)
    if collisions:
        raise ValueError(f"CSV detail column conflicts with product columns: {sorted(collisions)}")
    attributes_column = str(schema["extension"]["attributes_column"])
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=(*_PRODUCT_COLUMNS, *detail_columns))
    writer.writeheader()
    release_ids: set[str] = set()
    for prepared, proof in zip(prepared_loads, proofs, strict=True):
        if prepared.schema_version_id != prepared_loads[0].schema_version_id:
            raise ValueError("All CSV artifacts must use the same Canonical Schema.")
        for product in prepared.plan.products:
            product_id = deterministic_product_id(
                prepared.vertical, prepared.insurer_code, product.product_name,
            )
            release_id = deterministic_release_id(product_id, prepared.release_source_id or prepared.document_id)
            if release_id in release_ids:
                raise ValueError(f"Duplicate product release in CSV inputs: {release_id}; keep one result.")
            release_ids.add(release_id)
            row = {
                "product_id": product_id,
                "release_id": release_id,
                "vertical_code": prepared.vertical,
                "insurer_code": prepared.insurer_code,
                "canonical_name": product.product_name.strip(),
                "source_product_type": product.product_type,
                "document_id": prepared.document_id,
                "document_type": prepared.document_type,
                "title": prepared.document_title,
                "source_path": prepared.source_path,
                "sha256": prepared.pdf_sha256,
                "schema_version_id": prepared.schema_version_id,
                "version": prepared.plan.schema_version,
                "run_id": prepared.run_id,
                "provider": prepared.provider,
                "model": prepared.model,
                "source_documents": dumps_json(list(prepared.source_document_refs), separators=(",", ":")),
                "document_group_id": prepared.document_group_id,
                "quality_status": proof["status"], "delivery_proof": proof["proof_path"],
                "document_fields": dumps_json({
                    name: prepared.raw_artifact["data"][name]
                    for name in prepared.schema_payload.get("document_fields", {})
                }, separators=(",", ":")),
                **product.extension_values,
                attributes_column: dumps_json(product.attributes, sort_keys=True, separators=(",", ":")),
            }
            writer.writerow({name: _csv_cell(value, name) for name, value in row.items()})
    write_text_output(destination, "\ufeff" + stream.getvalue())
    return CsvExportSummary(destination, len(prepared_loads), len(release_ids))


def _csv_cell(value: object, column: str) -> str:
    if value is None:
        return ""
    if isinstance(value, (bool, int, float)):
        return dumps_json(value)
    if not isinstance(value, str):
        raise ValueError(f"CSV column {column!r} must contain a scalar value.")
    if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r")):
        raise ValueError(f"CSV column {column!r} contains formula-like text; review the source value.")
    return value
