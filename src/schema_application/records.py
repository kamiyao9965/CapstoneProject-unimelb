"""Read both extraction file formats through one identity boundary."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from src.common.json_codec import loads_json
from src.common.json_contracts import validate_contract
from src.models import ExtractionResult


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
        result = ExtractionResult.model_validate(artifact)
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
