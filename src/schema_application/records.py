"""Read both extraction file formats through one identity boundary."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from src.common.json_codec import loads_json
from src.common.json_contracts import validate_contract, validate_inline_contract
from src.common.model_config import ModelSelection
from src.common.models import ExtractionResult
from src.schema.canonical import (
    compile_canonical_extraction_contract, is_canonical_schema,
    validate_canonical_extraction_identities,
)
from src.schema.contract import compile_extraction_contract
from src.schema.validation import validate_extraction_record, validate_schema_mapping
from src.verticals.manifest import VerticalManifest


@dataclass(frozen=True)
class ParsedExtraction:
    artifact: dict[str, Any]
    data: dict[str, Any]
    source_document: str
    vertical: str | None
    schema_version: str | None
    provider: str | None
    model: str | None
    run_id: str | None


def parse_extraction_artifact(
    raw: bytes,
    *,
    vertical: str,
    schema_version: str | None = None,
    require_identity: bool = False,
) -> ParsedExtraction:
    """Validate the wrapper and identity; callers validate data against their schema.

    Analysis may read historical envelopes without identity. Storage requires an
    explicit identity. Legacy run IDs retain the hash of the original file bytes.
    """
    artifact = loads_json(raw.decode("utf-8"))
    if not isinstance(artifact, dict):
        raise ValueError("Extraction must be a JSON object.")
    if "artifact_type" in artifact:
        validate_contract(artifact, "artifact_envelope")
        if artifact["status"] != "success" or artifact["artifact_type"] != "extraction_result":
            raise ValueError("Not a successful extraction result artifact.")
        identity = artifact["provenance"]
        source_documents = identity["source_documents"]
        data = artifact["data"]
    else:
        try:
            result = ExtractionResult.model_validate(artifact)
        except ValidationError as exc:
            raise ValueError("Legacy extraction artifact is invalid.") from exc
        identity = result.model_dump()
        identity["run_id"] = f"sha256:{hashlib.sha256(raw).hexdigest()}"
        source_documents = [result.source_path]
        data = result.data

    if len(source_documents) != 1 or not source_documents[0].strip():
        raise ValueError("Extraction must identify exactly one non-empty source document.")
    for field, expected in (("vertical", vertical), ("schema_version", schema_version)):
        label = field.replace("_", " ")
        if require_identity and not identity.get(field):
            raise ValueError(f"Extraction {label} is missing; regenerate before storage.")
        if expected is not None and field in identity and identity[field] != expected:
            raise ValueError(f"Extraction {label} does not match the selected schema.")

    return ParsedExtraction(
        artifact=artifact, data=data, source_document=source_documents[0],
        vertical=identity.get("vertical"), schema_version=identity.get("schema_version"),
        provider=identity.get("provider"), model=identity.get("model"),
        run_id=identity.get("run_id"),
    )


def load_cached_extraction(
    path: Path, *, pdf_path: Path, schema: dict[str, object],
    manifest: VerticalManifest, selection: ModelSelection, document_parser: str,
) -> ExtractionResult:
    """Validate a batch resume result without creating a provider or changing it."""
    parsed = parse_extraction_artifact(
        path.read_bytes(), vertical=manifest.vertical,
        schema_version=str(schema["version"]), require_identity=True,
    )
    if Path(parsed.source_document).resolve() != pdf_path.resolve():
        raise ValueError("Existing extraction source does not match the selected PDF.")
    identity = parsed.artifact.get("provenance", parsed.artifact)
    if parsed.provider != selection.provider or parsed.model != selection.model:
        raise ValueError("Existing extraction used a different provider or model.")
    if identity.get("document_parser") != document_parser:
        raise ValueError("Existing extraction used a different or unrecorded PDF parser.")
    if is_canonical_schema(schema):
        if schema["output"]["cardinality"] != manifest.documents.output_cardinality:
            raise ValueError("Canonical Schema output cardinality does not match the manifest.")
        contract = compile_canonical_extraction_contract(schema)
        validate_inline_contract(parsed.data, contract, "cached_extraction")
        validate_canonical_extraction_identities(schema, parsed.data)
    else:
        normalized = validate_schema_mapping(dict(schema), manifest=manifest)
        contract = compile_extraction_contract(normalized, manifest=manifest)
        validate_inline_contract(parsed.data, contract, "cached_extraction")
        validate_extraction_record(normalized, parsed.data, manifest=manifest)
    if "artifact_type" not in parsed.artifact:
        return ExtractionResult.model_validate(parsed.artifact)
    return ExtractionResult(
        vertical=parsed.vertical, schema_version=parsed.schema_version,
        source_path=parsed.source_document, extracted_at=parsed.artifact["created_at"],
        provider=parsed.provider, model=parsed.model, document_parser=document_parser,
        data=parsed.data,
    )
