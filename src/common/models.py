from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    text: str
    page: int | None = None
    field_path: str | None = None


class NormalizationResult(BaseModel):
    raw: str
    canonical: str | None = None
    confidence: float
    requires_review: bool = False


class ExtractionResult(BaseModel):
    vertical: str
    schema_version: str
    source_path: str
    source_documents: list[str] = Field(default_factory=list)
    source_document_refs: list[dict[str, Any]] = Field(default_factory=list)
    document_group_id: str | None = None
    schema_sha256: str | None = None
    prompt_sha256: str | None = None
    validation_checks: dict[str, Any] | None = None
    extracted_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    provider: str = "heuristic"
    model: str | None = None
    document_parser: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    evidences: dict[str, list[Evidence]] = Field(default_factory=dict)
    normalized_names: list[NormalizationResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def write_json(self, output_path: str | Path) -> Path:
        from src.common.json_artifacts import write_text_output

        return write_text_output(output_path, self.model_dump_json(indent=2))
