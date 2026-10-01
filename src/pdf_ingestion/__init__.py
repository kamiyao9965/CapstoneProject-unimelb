"""PDF Silver-layer ingestion for structured text/table extraction."""

from src.pdf_ingestion.cache import PDFCache
from src.pdf_ingestion.adapter import (
    DEFAULT_DOCUMENT_PARSER,
    DOCUMENT_PARSERS,
    build_ingestor,
    ingest_pdfs,
    render_documents_for_prompt,
    render_pdf_paths_for_prompt,
)
from src.pdf_ingestion.mineru import MinerUIngestor
from src.pdf_ingestion.models import ParsedPDF
from src.pdf_ingestion.parser import PDFIngestor, parse_pdf

__all__ = [
    "DEFAULT_DOCUMENT_PARSER",
    "DOCUMENT_PARSERS",
    "MinerUIngestor",
    "PDFCache",
    "PDFIngestor",
    "ParsedPDF",
    "build_ingestor",
    "ingest_pdfs",
    "parse_pdf",
    "render_documents_for_prompt",
    "render_pdf_paths_for_prompt",
]
