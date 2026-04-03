from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

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


class PDFLink(BaseModel):
    url: str
    vertical: str
    fund_code: str
    product_type: str
    source_page: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class DownloadResult(BaseModel):
    pdf_link: PDFLink
    local_path: str | None = None
    status: Literal["success", "failed", "duplicate"]
    content_hash: str | None = None
    error: str | None = None


class RateLimitConfig(BaseModel):
    requests_per_second: float = 0.5
    retry_attempts: int = 3
    backoff_seconds: list[int] = Field(default_factory=lambda: [2, 5, 10])


class ScraperSourceConfig(BaseModel):
    fund_code: str
    entry_url: str
    pdf_selector: str = "a[href$='.pdf']"
    crawl_strategy: Literal["static", "dynamic"] | None = None
    wait_selector: str | None = None
    product_type_hints: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScraperConfig(BaseModel):
    vertical: str
    crawl_strategy: Literal["static", "dynamic"] = "static"
    sources: list[ScraperSourceConfig] = Field(default_factory=list)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    respect_robots_txt: bool = True


class ParsedDocument(BaseModel):
    path: str
    pages: int
    tables: list[list[list[str]]] = Field(default_factory=list)
    full_text: str
    text_by_page: list[str] = Field(default_factory=list)
    has_tables: bool
    table_coverage: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractionInput(BaseModel):
    mode: Literal["table_assisted", "text_only"]
    text: str
    tables: list[list[list[str]]] = Field(default_factory=list)
    source_document: ParsedDocument


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
    field_precision: float
    field_recall: float
    normalization_accuracy: float
    coverage: float
    hallucination_rate: float
    matched_fields: int = 0
    extracted_fields: int = 0
    ground_truth_fields: int = 0
    missing_fields: list[str] = Field(default_factory=list)
    incorrect_fields: list[str] = Field(default_factory=list)


class ProductMatch(BaseModel):
    pdf_path: str
    id_master: str
    fund_code: str
    brand_code: str
    name_master: str
    product_type: str
    hospital_tier: str | None = None
    product_item_ids: list[str] = Field(default_factory=list)


class ManifestRecord(BaseModel):
    url: str
    local_path: str
    hash: str
    downloaded_at: str
    fund_code: str
    vertical: str
    product_type: str
    source_page: str
    metadata: dict[str, Any] = Field(default_factory=dict)
