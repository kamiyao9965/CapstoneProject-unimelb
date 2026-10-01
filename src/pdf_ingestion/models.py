from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


BBox = tuple[float, float, float, float]


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    block_id: str
    content: str
    bbox: BBox | None = None
    top: float
    reading_order: int = 0
    source_engine: str = "pdfplumber"


class TableBlock(BaseModel):
    type: Literal["table"] = "table"
    block_id: str
    table_id: str
    markdown: str
    raw_rows: list[list[str]] = Field(default_factory=list)
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    bbox: BBox
    top: float
    reading_order: int = 0
    caption_context: dict[str, Any] = Field(default_factory=dict)
    source_engine: str = "pdfplumber"
    confidence: float = 0.0
    extraction_notes: list[str] = Field(default_factory=list)


class VisionBlock(BaseModel):
    type: Literal["vision"] = "vision"
    block_id: str
    content: str
    bbox: BBox | None = None
    top: float = 0.0
    reading_order: int = 0
    source_engine: str = "vision"
    confidence: float = 0.0


class PageRepresentation(BaseModel):
    page_num: int
    width: float
    height: float
    extraction_method: Literal["code", "vision"] = "code"
    vision_reason: str | None = None
    blocks: list[TextBlock | TableBlock | VisionBlock] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ParsedPDF(BaseModel):
    pdf_id: str
    pdf_hash: str
    source_path: str
    pages: list[PageRepresentation] = Field(default_factory=list)
    parser: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def to_llm_markdown(self, *, page_separator: bool = True) -> str:
        """Render the cached structure as ordered Markdown for discovery/extraction prompts."""
        chunks: list[str] = []
        for page in self.pages:
            if page_separator:
                chunks.append(f"\n\n<!-- page {page.page_num} -->\n")
            for block in page.blocks:
                if block.type == "text":
                    chunks.append(block.content)
                elif block.type == "table":
                    chunks.append(block.markdown)
                else:
                    chunks.append(block.content)
        return "\n\n".join(chunk for chunk in chunks if chunk.strip()).strip()
