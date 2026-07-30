from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class SchemaField(BaseModel):
    name: str
    type: str
    description: str | None = None
    values: list[str] = Field(default_factory=list)
    item_type: str | None = None


class SchemaSection(BaseModel):
    fields: list[SchemaField] = Field(default_factory=list)
    canonical_categories: list[str] = Field(default_factory=list)
    canonical_services: list[str] = Field(default_factory=list)
    aliases: dict[str, list[str]] = Field(default_factory=dict)
    descriptions: dict[str, str] = Field(default_factory=dict)

    @property
    def canonical_names(self) -> list[str]:
        if self.canonical_categories:
            return self.canonical_categories
        if self.canonical_services:
            return self.canonical_services
        return []


class VerticalSchema(BaseModel):
    vertical: str
    version: str
    hospital: SchemaSection | None = None
    extras: SchemaSection | None = None
    coverage: SchemaSection | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def active_sections(self) -> dict[str, SchemaSection]:
        return {
            key: value
            for key, value in {
                "hospital": self.hospital,
                "extras": self.extras,
                "coverage": self.coverage,
            }.items()
            if value is not None
        }


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
    extracted_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    provider: str = "heuristic"
    model: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    evidences: dict[str, list[Evidence]] = Field(default_factory=dict)
    normalized_names: list[NormalizationResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def write_json(self, output_path: str | Path) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path


class EvaluationReport(BaseModel):
    source_path: str
    product_key: str | None = None
    extraction_provider: str | None = None
    extraction_model: str | None = None
    field_precision: float
    field_recall: float
    field_presence_recall: float = 0.0
    value_accuracy: float = 0.0
    normalization_accuracy: float
    coverage: float
    hallucination_rate: float
    matched_fields: int = 0
    comparable_fields: int = 0
    extracted_fields: int = 0
    ground_truth_fields: int = 0
    missing_fields: list[str] = Field(default_factory=list)
    incorrect_fields: list[str] = Field(default_factory=list)
    section_metrics: dict[str, Any] = Field(default_factory=dict)
    hallucinations_by_section: dict[str, int] = Field(default_factory=dict)
    match_score: float | None = None
    low_confidence_match: bool = False


class ProductMatch(BaseModel):
    pdf_path: str
    id_master: str
    fund_code: str
    brand_code: str
    name_master: str
    product_type: str
    hospital_tier: str | None = None
    product_item_ids: list[str] = Field(default_factory=list)
    match_score: float = 0.0
    low_confidence_match: bool = False
    candidate_matches: list[dict[str, Any]] = Field(default_factory=list)
    ambiguous_match: bool = False
