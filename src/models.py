from __future__ import annotations

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


class ParsedDocument(BaseModel):
    path: str
    pages: int
    tables: list[list[list[str]]] = Field(default_factory=list)
    full_text: str
    text_by_page: list[str] = Field(default_factory=list)
    has_tables: bool
    table_coverage: float
    metadata: dict[str, Any] = Field(default_factory=dict)
