"""Export validated PostgreSQL product records as one portable CSV view."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

from src.common.json_artifacts import write_text_output
from src.common.json_codec import dumps_json
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
) -> CsvExportSummary:
    """Validate one existing extraction and write a new product CSV without a DB."""
    prepared = prepare_storage_load(
        manifest=manifest, schema_path=schema_path,
        artifact_path=artifact_path, insurer_code=insurer_code,
    )
    return _write_product_csv([prepared], output_path)


def export_extraction_directory(
    *,
    manifest: VerticalManifest,
    schema_path: str | Path,
    artifact_dir: str | Path,
    output_path: str | Path,
) -> CsvExportSummary:
    """Validate an entire folder before writing one CSV; failures leave no output."""
    manifest.require_capability("storage")
    prepared: list[PreparedStorageLoad] = []
    errors: list[str] = []
    for entry in inspect_extraction_directory(manifest=manifest, artifact_dir=artifact_dir):
        if entry.error is not None:
            errors.append(f"{entry.artifact_path}: {entry.error}")
            continue
        assert entry.insurer_code is not None
        try:
            prepared.append(prepare_storage_load(
                manifest=manifest, schema_path=schema_path,
                artifact_path=entry.artifact_path, insurer_code=entry.insurer_code,
            ))
        except (OSError, ValueError) as exc:
            errors.append(f"{entry.artifact_path}: {exc}")
    if errors:
        raise ValueError("CSV export failed; no output was written:\n" + "\n".join(errors))
    return _write_product_csv(prepared, output_path)


def _write_product_csv(
    prepared_loads: list[PreparedStorageLoad], output_path: str | Path,
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
    for prepared in prepared_loads:
        if prepared.schema_version_id != prepared_loads[0].schema_version_id:
            raise ValueError("All CSV artifacts must use the same Canonical Schema.")
        for product in prepared.plan.products:
            product_id = deterministic_product_id(
                prepared.vertical, prepared.insurer_code, product.product_name,
            )
            release_id = deterministic_release_id(product_id, prepared.document_id)
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
