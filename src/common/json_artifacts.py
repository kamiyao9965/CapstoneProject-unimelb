"""Shared, fail-closed JSON artifact construction and persistence."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.common.json_contracts import validate_contract, validate_inline_contract
from src.common.json_codec import StrictJSONError, dumps_json, loads_json


_SAFE_COMPONENT = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD))\s*=\s*[^\s,;]+"
)
_BEARER_SECRET = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_OPENAI_STYLE_SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}")


class ArtifactError(ValueError):
    """Raised when a persisted artifact cannot be trusted by a consumer."""


def build_success_artifact(
    *,
    artifact_type: str,
    contract_version: str,
    data: Mapping[str, Any],
    provenance: Mapping[str, Any],
    data_contract: str | None = None,
    data_contract_schema: Mapping[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build and validate a successful stage artifact."""
    _validate_data(data, data_contract, data_contract_schema)
    artifact = {
        "artifact_type": artifact_type,
        "contract_version": contract_version,
        "status": "success",
        "created_at": created_at or _utc_timestamp(),
        "provenance": dict(provenance),
        "data": dict(data),
        "error": None,
    }
    validate_contract(artifact, "artifact_envelope")
    return artifact


def build_failure_artifact(
    *,
    artifact_type: str,
    contract_version: str,
    provenance: Mapping[str, Any],
    error_code: str,
    message: str,
    details: Sequence[Mapping[str, str]] = (),
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build a redacted failure artifact; raw model/document content is excluded."""
    artifact = {
        "artifact_type": artifact_type,
        "contract_version": contract_version,
        "status": "failed",
        "created_at": created_at or _utc_timestamp(),
        "provenance": dict(provenance),
        "data": None,
        "error": {
            "code": error_code,
            "message": _redact_failure_text(message),
            "details": [
                {
                    "path": str(detail.get("path", "$")),
                    "message": _redact_failure_text(str(detail.get("message", "Error"))),
                }
                for detail in details
            ],
        },
    }
    validate_contract(artifact, "artifact_envelope")
    return artifact


def _redact_failure_text(value: str) -> str:
    redacted = _SECRET_ASSIGNMENT.sub(r"\1=[REDACTED]", value)
    redacted = _BEARER_SECRET.sub("Bearer [REDACTED]", redacted)
    return _OPENAI_STYLE_SECRET.sub("[REDACTED]", redacted) or "[REDACTED]"


def write_artifact(
    path: str | Path,
    artifact: Mapping[str, Any],
    *,
    data_contract: str | None = None,
    data_contract_schema: Mapping[str, Any] | None = None,
    overwrite: bool = False,
) -> Path:
    """Validate then atomically persist one deterministic UTF-8 JSON artifact."""
    validate_contract(artifact, "artifact_envelope")
    if artifact["status"] == "success":
        _validate_data(artifact["data"], data_contract, data_contract_schema)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    serialized = dumps_json(artifact, ensure_ascii=False, indent=2) + "\n"
    _write_text_atomic(destination, serialized, overwrite=overwrite)
    return destination


def read_artifact(
    path: str | Path,
    *,
    expected_type: str | None = None,
    data_contract: str | None = None,
    data_contract_schema: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read a successful artifact and reject invalid/failed data at the boundary."""
    source = Path(path)
    try:
        artifact = loads_json(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, StrictJSONError) as exc:
        raise ArtifactError(f"Cannot read valid JSON artifact {source}: {exc}") from exc
    try:
        validate_contract(artifact, "artifact_envelope")
    except ValueError as exc:
        raise ArtifactError(str(exc)) from exc
    if artifact["status"] != "success":
        raise ArtifactError(f"{source} is not a successful artifact.")
    if expected_type is not None and artifact["artifact_type"] != expected_type:
        raise ArtifactError(
            f"Expected artifact type {expected_type!r}, got {artifact['artifact_type']!r}."
        )
    if data_contract is not None or data_contract_schema is not None:
        try:
            _validate_data(artifact["data"], data_contract, data_contract_schema)
        except ValueError as exc:
            raise ArtifactError(str(exc)) from exc
    return artifact


def write_failure_artifact(
    output_root: str | Path,
    stage: str,
    run_id: str,
    artifact: Mapping[str, Any],
    *,
    overwrite: bool = False,
) -> Path:
    """Persist failures only below ``errors/<stage>/``."""
    _validate_component(stage)
    _validate_component(run_id)
    if artifact.get("status") != "failed":
        raise ArtifactError("Failure artifact writer requires status 'failed'.")
    path = Path(output_root) / "errors" / stage / f"{run_id}.json"
    return write_artifact(path, artifact, overwrite=overwrite)


def write_text_output(
    path: str | Path,
    text: str,
    *,
    overwrite: bool = False,
) -> Path:
    """Atomically write a non-artifact text output without silent overwrite."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(destination, text, overwrite=overwrite)
    return destination


def _validate_component(value: str) -> None:
    if not _SAFE_COMPONENT.fullmatch(value):
        raise ArtifactError(f"{value!r} is not a safe path component.")


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_data(
    data: Any,
    contract_name: str | None,
    contract_schema: Mapping[str, Any] | None,
) -> None:
    if (contract_name is None) == (contract_schema is None):
        raise ValueError(
            "Provide exactly one of data_contract or data_contract_schema for success data."
        )
    if contract_name is not None:
        validate_contract(data, contract_name)
    else:
        assert contract_schema is not None
        validate_inline_contract(data, contract_schema, "runtime_extraction_result")


def _write_text_atomic(destination: Path, text: str, *, overwrite: bool) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temporary, destination)
        else:
            try:
                os.link(temporary, destination)
            except FileExistsError as exc:
                raise FileExistsError(
                    f"Refusing to overwrite existing artifact: {destination}"
                ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def next_available_path(path: Path, reserved: set[Path] | None = None) -> Path:
    """Choose a new output name; atomic writers still enforce no-overwrite."""
    candidate = path
    index = 1
    while candidate.exists() or candidate in (reserved or ()):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        index += 1
    return candidate
