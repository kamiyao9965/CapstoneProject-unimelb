"""Application service for validated PostgreSQL storage operations."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from src.common.json_codec import dumps_json, loads_json
from src.common.json_contracts import validate_contract
from src.models import ExtractionResult
from src.schema.canonical import require_approved_canonical_schema
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
) -> PreparedStorageLoad:
    """Validate source boundaries and compile one deterministic load request."""
    manifest.require_capability("storage")
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
        artifact = loads_json(artifact_bytes.decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"Could not read extraction artifact {artifact_file}: {exc}") from exc
    if not isinstance(artifact, dict):
        raise ValueError("Extraction artifact must contain a JSON object.")

    extraction_data, source_value, provider, model, run_id = _artifact_values(
        artifact,
        artifact_bytes=artifact_bytes,
        expected_vertical=manifest.vertical,
        expected_schema_version=str(approved_schema["version"]),
    )
    source_path, relative_source, pdf_sha256 = _validate_source_document(
        source_value,
        manifest=manifest,
        insurer_code=normalized_insurer,
    )
    document_type = relative_source.parts[1]
    schema_version_id = _content_id(approved_schema)
    plan = compile_canonical_load_plan(approved_schema, extraction_data)

    title_match = _HASHED_FILENAME.fullmatch(source_path.stem)
    document_title = (title_match.group(1) if title_match else source_path.stem).replace(
        "_", " "
    )
    return PreparedStorageLoad(
        vertical=manifest.vertical,
        insurer_code=normalized_insurer,
        document_id=f"sha256:{pdf_sha256}",
        pdf_sha256=pdf_sha256,
        document_type=document_type,
        document_title=document_title,
        source_path=source_path.as_posix(),
        schema_version_id=schema_version_id,
        schema_payload=approved_schema,
        run_id=run_id,
        provider=provider,
        model=model,
        raw_artifact=artifact,
        payload_sha256=_sha256_bytes(artifact_bytes),
        plan=plan,
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


def _artifact_values(
    artifact: dict[str, Any],
    *,
    artifact_bytes: bytes,
    expected_vertical: str,
    expected_schema_version: str,
) -> tuple[dict[str, Any], str, str, str, str]:
    if "artifact_type" in artifact:
        validate_contract(artifact, "artifact_envelope")
        if artifact["artifact_type"] != "extraction_result":
            raise ValueError("Storage load requires an extraction_result artifact.")
        if artifact["status"] != "success":
            raise ValueError("Storage load requires a successful extraction artifact.")
        provenance = artifact["provenance"]
        assert isinstance(provenance, dict)
        source_documents = provenance["source_documents"]
        if not isinstance(source_documents, list) or len(source_documents) != 1:
            raise ValueError("Extraction artifact must reference exactly one source document.")
        data = artifact["data"]
        assert isinstance(data, dict)
        return (
            data,
            _non_empty(source_documents[0], "source document"),
            _non_empty(provenance["provider"], "provider", maximum=64),
            _non_empty(provenance["model"], "model", maximum=128),
            _non_empty(provenance["run_id"], "run ID", maximum=128),
        )

    try:
        legacy = ExtractionResult.model_validate(artifact)
    except Exception as exc:
        raise ValueError("Legacy extraction artifact is invalid.") from exc
    if legacy.vertical != expected_vertical:
        raise ValueError("Extraction artifact vertical does not match the manifest vertical.")
    if legacy.schema_version != expected_schema_version:
        raise ValueError(
            "Extraction artifact schema version does not match the Canonical Schema."
        )
    return (
        legacy.data,
        legacy.source_path,
        _non_empty(legacy.provider, "provider", maximum=64),
        _non_empty(legacy.model, "model", maximum=128),
        f"sha256:{_sha256_bytes(artifact_bytes)}",
    )


def _validate_source_document(
    source_value: str,
    *,
    manifest: VerticalManifest,
    insurer_code: str,
) -> tuple[Path, Path, str]:
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
    if len(relative_source.parts) < 3:
        raise ValueError(
            "Source document must use the <insurer>/<document_type>/<file>.pdf layout."
        )
    if relative_source.parts[0] != insurer_code:
        raise ValueError("Insurer code does not match the source document path.")
    if relative_source.parts[1] not in manifest.documents.document_types:
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
