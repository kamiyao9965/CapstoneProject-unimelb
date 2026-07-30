"""PDF Silver-layer ingestion for structured text/table extraction."""

from src.PDFingestor.cache import PDFCache
from src.PDFingestor.adapter import (
    build_ingestor,
    ingest_pdfs,
    render_documents_for_prompt,
    render_pdf_paths_for_prompt,
)
from src.PDFingestor.models import ParsedPDF
from src.PDFingestor.parser import PDFIngestor, parse_pdf

__all__ = [
    "PDFCache",
    "PDFIngestor",
    "ParsedPDF",
    "build_ingestor",
    "ingest_pdfs",
    "parse_pdf",
    "render_documents_for_prompt",
    "render_pdf_paths_for_prompt",
]
