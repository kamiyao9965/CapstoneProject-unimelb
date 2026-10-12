"""Validated artifact preparation and PostgreSQL storage operations."""

from __future__ import annotations

import hashlib
import os
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from src.common.json_codec import dumps_json, loads_json
from src.schema.canonical import require_approved_canonical_schema
from src.schema_application.records import parse_extraction_artifact, resolve_extraction_sources
from src.storage.canonical import (
    CanonicalLoadPlan,
    compile_canonical_load_plan,
    compile_vertical_storage_metadata,
)
from src.storage.schema import storage_metadata
from src.verticals.manifest import PROJECT_ROOT, VerticalManifest

if TYPE_CHECKING:
    from src.storage.repository import StorageLoadSummary


_HASHED_FILENAME = re.compile(r"^[0-9a-f]{64}[_-](.+)$", re.IGNORECASE)
_DATABASE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
_MAX_PDF_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class PreparedSourceDocument:
    """One verified source PDF in a prepared release."""

    document_id: str
    pdf_sha256: str
    document_type: str
    document_title: str
    source_path: str
    metadata: dict[str, object]


@dataclass(frozen=True)
class PreparedStorageLoad:
    """Validated values needed for one atomic repository write."""

    vertical: str
    insurer_code: str
    document_id: str
    pdf_sha256: str
    document_type: str
    document_title: str
    source_path: str
    schema_version_id: str
    schema_payload: dict[str, object]
    run_id: str
    provider: str
    model: str
    raw_artifact: dict[str, Any]
    payload_sha256: str
    plan: CanonicalLoadPlan
    source_documents: tuple[PreparedSourceDocument, ...] = ()
    source_document_refs: tuple[dict[str, object], ...] = ()
    document_group_id: str | None = None
    release_source_id: str | None = None


@dataclass(frozen=True)
class DirectoryLoadResult:
    """Outcome of one artifact in a folder load; exactly one of summary/error is set."""

    artifact_path: Path
    insurer_code: str | None
    summary: StorageLoadSummary | None
    error: str | None


@dataclass(frozen=True)
class DirectoryArtifact:
    """One discovered extraction source, or its source/duplicate validation error."""

    artifact_path: Path
    insurer_code: str | None
    error: str | None


def resolve_database_url(environment_name: str = "KONKRD_DATABASE_URL") -> str:
    """Read a database URL from the environment without exposing its value."""
    if not _ENVIRONMENT_NAME.fullmatch(environment_name):
        raise ValueError("Database URL environment-variable name is invalid.")
    database_url = os.getenv(environment_name)
    if not database_url or not database_url.strip():
        raise RuntimeError(
            f"PostgreSQL connection is not configured; set {environment_name}."
        )
    return require_postgresql_url(database_url.strip())


def require_postgresql_url(database_url: str) -> str:
    """Reject non-PostgreSQL URLs before an engine can be created."""
    try:
        url = make_url(database_url)
    except Exception as exc:
        raise ValueError("Database URL must be a valid PostgreSQL URL.") from exc
    if url.get_backend_name() != "postgresql":
        raise ValueError("Canonical storage requires PostgreSQL; SQLite is unsupported.")
    return database_url


def prepare_storage_load(
    *,
    manifest: VerticalManifest,
    schema_path: str | Path,
    artifact_path: str | Path,
    insurer_code: str,
    for_csv: bool = False,
) -> PreparedStorageLoad:
    """Validate source boundaries and compile one deterministic load request."""
    manifest.require_capability("csv_export" if for_csv else "storage")
    normalized_insurer = insurer_code.strip()
    if not _DATABASE_IDENTIFIER.fullmatch(normalized_insurer):
        raise ValueError(
            "Insurer code must be a lowercase snake_case identifier up to 64 characters."
        )

    schema = _read_object(Path(schema_path), "Canonical Schema")
    approved_schema = require_approved_canonical_schema(schema)
    schema_vertical = str(approved_schema["vertical"])
    if schema_vertical != manifest.vertical:
        raise ValueError(
            "Canonical Schema vertical does not match the selected manifest vertical."
        )

    artifact_file = Path(artifact_path)
    try:
        artifact_bytes = _read_limited_bytes(
            artifact_file,
            label="Extraction artifact",
            maximum_bytes=_MAX_ARTIFACT_BYTES,
        )
        extraction = parse_extraction_artifact(
            artifact_bytes, vertical=manifest.vertical,
            schema_version=str(approved_schema["version"]), require_identity=True,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"Could not read extraction artifact {artifact_file}: {exc}") from exc
    provider = _non_empty(extraction.provider, "provider", maximum=64)
    model = _non_empty(extraction.model, "model", maximum=128)
    run_id = _non_empty(extraction.run_id, "run ID", maximum=128)
    group = resolve_extraction_sources(extraction, manifest)
    source_documents = []
    for reference in group.documents:
        source_path, relative_source, pdf_sha256 = _validate_source_document(
            reference.source_path, manifest=manifest, insurer_code=normalized_insurer,
            for_csv=for_csv, metadata=dict(reference.metadata),
        )
        document_type = reference.document_role if manifest.documents.inventory or len(relative_source.parts) < 3 else relative_source.parts[1]
        title_match = _HASHED_FILENAME.fullmatch(source_path.stem)
        document_title = (title_match.group(1) if title_match else source_path.stem).replace("_", " ")
        source_documents.append(PreparedSourceDocument(
            document_id=f"sha256:{pdf_sha256}", pdf_sha256=pdf_sha256,
            document_type=document_type, document_title=document_title, source_path=str(source_path),
            metadata={**dict(reference.metadata), "inventory_document_id": reference.document_id,
                      "amends_document_ids": list(reference.amends_document_ids)},
        ))
    primary = source_documents[0]
    schema_version_id = _content_id(approved_schema)
    plan = compile_canonical_load_plan(approved_schema, extraction.data)

    refs = tuple(ref.to_dict() for ref in group.documents)
    release_source_id = primary.document_id if len(refs) == 1 else _content_id([
        {"document_id": ref["document_id"], "sha256": ref["sha256"],
         "amends_document_ids": ref["amends_document_ids"]} for ref in refs
    ])
    return PreparedStorageLoad(
        vertical=manifest.vertical,
        insurer_code=normalized_insurer,
        document_id=primary.document_id,
        pdf_sha256=primary.pdf_sha256,
        document_type=primary.document_type,
        document_title=primary.document_title,
        source_path=primary.source_path,
        schema_version_id=schema_version_id,
        schema_payload=approved_schema,
        run_id=run_id,
        provider=provider,
        model=model,
        raw_artifact=extraction.artifact,
        payload_sha256=_sha256_bytes(artifact_bytes),
        plan=plan,
        source_documents=tuple(source_documents), source_document_refs=refs,
        document_group_id=group.group_id, release_source_id=release_source_id,
    )


def initialize_storage(
    *,
    database_url: str,
    manifest: VerticalManifest,
    schema_path: str | Path,
) -> None:
    """Create missing PostgreSQL core and approved vertical tables atomically."""
    manifest.require_capability("storage")
    schema = require_approved_canonical_schema(
        _read_object(Path(schema_path), "Canonical Schema")
    )
    if schema["vertical"] != manifest.vertical:
        raise ValueError(
            "Canonical Schema vertical does not match the selected manifest vertical."
        )
    compiled = compile_vertical_storage_metadata(schema)
    engine = create_engine(require_postgresql_url(database_url))
    try:
        with engine.begin() as connection:
            storage_metadata.create_all(connection, checkfirst=True)
            compiled.create(connection)
    finally:
        engine.dispose()


def load_extraction_artifact(
    *,
    database_url: str,
    manifest: VerticalManifest,
    schema_path: str | Path,
    artifact_path: str | Path,
    insurer_code: str,
) -> StorageLoadSummary:
    """Validate and load one artifact in a single PostgreSQL transaction."""
    from src.storage.repository import write_prepared_load

    prepared = prepare_storage_load(
        manifest=manifest,
        schema_path=schema_path,
        artifact_path=artifact_path,
        insurer_code=insurer_code,
    )
    compiled = compile_vertical_storage_metadata(prepared.schema_payload)
    engine = create_engine(require_postgresql_url(database_url))
    try:
        with engine.begin() as connection:
            return write_prepared_load(connection, prepared, compiled.table)
    finally:
        engine.dispose()


def load_extraction_directory(
    *,
    database_url: str,
    manifest: VerticalManifest,
    schema_path: str | Path,
    artifact_dir: str | Path,
    load_one: Callable[..., StorageLoadSummary] | None = None,
) -> list[DirectoryLoadResult]:
    """Load every extraction artifact below a folder, one transaction per artifact.

    Each insurer code comes from the artifact's source PDF path below the manifest
    input root. Artifacts that name the same source PDF are all rejected, so one
    PDF cannot load conflicting product rows. ``errors/`` folders are skipped.
    """
    load = load_one or load_extraction_artifact
    results: list[DirectoryLoadResult] = []
    for entry in inspect_extraction_directory(manifest=manifest, artifact_dir=artifact_dir):
        if entry.error is not None:
            results.append(DirectoryLoadResult(entry.artifact_path, entry.insurer_code, None, entry.error))
            continue
        try:
            summary = load(
                database_url=database_url,
                manifest=manifest,
                schema_path=schema_path,
                artifact_path=entry.artifact_path,
                insurer_code=entry.insurer_code,
            )
        except Exception as exc:
            results.append(
                DirectoryLoadResult(entry.artifact_path, entry.insurer_code, None, str(exc) or type(exc).__name__)
            )
            continue
        results.append(DirectoryLoadResult(entry.artifact_path, entry.insurer_code, summary, None))
    return results


def inspect_extraction_directory(
    *, manifest: VerticalManifest, artifact_dir: str | Path, for_csv: bool = False,
) -> list[DirectoryArtifact]:
    """Discover ordered inputs and reject competing source results for DB/CSV loads."""
    directory = Path(artifact_dir)
    if not directory.is_dir():
        raise ValueError(f"Extraction results folder does not exist: {directory}")
    artifact_paths = sorted(
        path
        for path in directory.rglob("*.json")
        if "errors" not in path.relative_to(directory).parts
    )
    if not artifact_paths:
        raise ValueError(f"No extraction artifacts found under {directory}.")

    sources: dict[Path, tuple[str, str]] = {}
    errors: dict[Path, str] = {}
    for artifact_path in artifact_paths:
        try:
            extraction = parse_extraction_artifact(
                _read_limited_bytes(artifact_path, label="Extraction artifact", maximum_bytes=_MAX_ARTIFACT_BYTES),
                vertical=manifest.vertical,
            )
            group = resolve_extraction_sources(extraction, manifest)
            _, relative_source = _source_below_input_root(extraction.source_document, manifest)
            insurer = group.documents[0].metadata.get("insurer_code") if for_csv else None
            if not insurer:
                if len(relative_source.parts) < (2 if for_csv else 3):
                    raise ValueError("Source layout requires an insurer folder or inventory insurer_code for CSV.")
                insurer = relative_source.parts[0]
            sources[artifact_path] = (group.group_id, str(insurer))
        except ValueError as exc:
            errors[artifact_path] = f"Could not read extraction artifact: {exc}"
    source_counts = Counter(value[0] for value in sources.values())

    results: list[DirectoryArtifact] = []
    for artifact_path in artifact_paths:
        if artifact_path in errors:
            results.append(DirectoryArtifact(artifact_path, None, errors[artifact_path]))
            continue
        source_id, insurer_code = sources[artifact_path]
        if source_counts[source_id] > 1:
            results.append(
                DirectoryArtifact(
                    artifact_path,
                    insurer_code,
                    f"{source_counts[source_id]} results in this folder use "
                    f"the same document group; keep one and retry.",
                )
            )
            continue
        results.append(DirectoryArtifact(artifact_path, insurer_code, None))
    return results


def _artifact_source_document(artifact_path: Path, manifest: VerticalManifest) -> Path:
    """Return an artifact's source PDF path relative to the manifest input root."""
    artifact_bytes = _read_limited_bytes(
        artifact_path,
        label="Extraction artifact",
        maximum_bytes=_MAX_ARTIFACT_BYTES,
    )
    try:
        extraction = parse_extraction_artifact(artifact_bytes, vertical=manifest.vertical)
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"Could not read extraction artifact: {exc}") from exc
    _, relative_source = _source_below_input_root(extraction.source_document, manifest)
    if len(relative_source.parts) < 3:
        raise ValueError(
            "Source document must use the <insurer>/<document_type>/<file>.pdf layout."
        )
    return relative_source


def _source_below_input_root(
    source_value: str,
    manifest: VerticalManifest,
) -> tuple[Path, Path]:
    source = Path(source_value).expanduser()
    resolved_source = (
        source.resolve()
        if source.is_absolute()
        else (PROJECT_ROOT / source).resolve()
    )
    input_root = manifest.path("input_root").resolve()
    try:
        relative_source = resolved_source.relative_to(input_root)
    except ValueError as exc:
        raise ValueError(
            "Extraction source document must stay inside the manifest input root."
        ) from exc
    return resolved_source, relative_source


def _validate_source_document(
    source_value: str,
    *,
    manifest: VerticalManifest,
    insurer_code: str,
    for_csv: bool = False,
    metadata: dict[str, object] | None = None,
) -> tuple[Path, Path, str]:
    resolved_source, relative_source = _source_below_input_root(source_value, manifest)
    if not for_csv and len(relative_source.parts) < 3:
        raise ValueError(
            "Source document must use the <insurer>/<document_type>/<file>.pdf layout."
        )
    configured_insurer = (metadata or {}).get("insurer_code")
    path_insurer = relative_source.parts[0] if len(relative_source.parts) >= 2 else None
    if (configured_insurer or path_insurer) and (configured_insurer or path_insurer) != insurer_code:
        raise ValueError("Insurer code does not match the source document path.")
    if not for_csv and relative_source.parts[1] not in manifest.documents.document_types:
        raise ValueError("Source document type is not allowed by the vertical manifest.")
    if resolved_source.suffix.casefold() != ".pdf":
        raise ValueError("Storage source document must be a PDF.")
    if not resolved_source.is_file():
        raise ValueError("Storage source PDF does not exist or is not a regular file.")
    pdf_bytes = _read_limited_bytes(
        resolved_source,
        label="Storage source PDF",
        maximum_bytes=_MAX_PDF_BYTES,
    )
    if b"%PDF-" not in pdf_bytes[:1024]:
        raise ValueError("Storage source file does not contain a valid PDF signature.")
    return resolved_source, relative_source, _sha256_bytes(pdf_bytes)


def _read_object(path: Path, label: str) -> dict[str, object]:
    try:
        payload = loads_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"Could not read {label} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object.")
    return payload


def _content_id(payload: object) -> str:
    serialized = dumps_json(payload, sort_keys=True, separators=(",", ":"))
    return f"sha256:{_sha256_bytes(serialized.encode('utf-8'))}"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _non_empty(value: object, label: str, *, maximum: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Extraction artifact {label} must be non-empty.")
    normalized = value.strip()
    if maximum is not None and len(normalized) > maximum:
        raise ValueError(
            f"Extraction artifact {label} exceeds the {maximum}-character limit."
        )
    return normalized


def _read_limited_bytes(path: Path, *, label: str, maximum_bytes: int) -> bytes:
    try:
        with path.open("rb") as handle:
            payload = handle.read(maximum_bytes + 1)
    except OSError as exc:
        raise ValueError(f"Could not read {label} {path}: {exc}") from exc
    if len(payload) > maximum_bytes:
        raise ValueError(f"{label} exceeds the {maximum_bytes}-byte safety limit.")
    return payload
