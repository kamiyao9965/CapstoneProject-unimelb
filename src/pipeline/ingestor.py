from __future__ import annotations

from pathlib import Path

import fitz

from src.models import ParsedDocument

try:
    import pdfplumber
except ImportError:  # pragma: no cover - optional dependency
    pdfplumber = None


class PDFIngestor:
    """
    Dual-path ingestion:
    - PyMuPDF for full-text extraction
    - pdfplumber for table extraction when available
    """

    def ingest(self, pdf_path: str) -> ParsedDocument:
        path = Path(pdf_path)
        with fitz.open(path) as doc:
            text_by_page = [page.get_text("text") for page in doc]
            full_text = "\n".join(text_by_page)
            pages = doc.page_count

        tables = self._extract_tables(path)
        table_characters = sum(
            len(cell or "") for table in tables for row in table for cell in row
        )
        full_length = max(len(full_text), 1)
        table_coverage = min(table_characters / full_length, 1.0)
        return ParsedDocument(
            path=str(path),
            pages=pages,
            tables=tables,
            full_text=full_text,
            text_by_page=text_by_page,
            has_tables=bool(tables),
            table_coverage=table_coverage,
        )

    def _extract_tables(self, path: Path) -> list[list[list[str]]]:
        if pdfplumber is None:
            return []

        extracted: list[list[list[str]]] = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables() or []:
                    rows = [
                        [self._clean_cell(cell) for cell in row]
                        for row in table
                        if any((cell or "").strip() for cell in row)
                    ]
                    if rows:
                        extracted.append(rows)
        return extracted

    @staticmethod
    def _clean_cell(value: str | None) -> str:
        return " ".join((value or "").split())
