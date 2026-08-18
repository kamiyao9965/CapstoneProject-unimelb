"""Bounded metadata/text inspection for already validated PDF files."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PdfInspection:
    parse_status: str
    text: str
    page_count: int | None
    error_code: str | None = None


def inspect_pdf(
    path: str | Path,
    *,
    max_pages: int,
    sample_pages: int,
    max_text_chars: int = 30_000,
) -> PdfInspection:
    """Read only a bounded text sample; PDF content is always untrusted data."""
    try:
        pymupdf = _load_pymupdf()
    except ImportError:
        return PdfInspection("not_attempted", "", None, "pdf_parser_unavailable")
    document: Any | None = None
    try:
        document = pymupdf.open(str(path))
        if bool(getattr(document, "needs_pass", False)):
            return PdfInspection("encrypted", "", len(document), "pdf_encrypted")
        page_count = len(document)
        if page_count > max_pages:
            return PdfInspection(
                "resource_limit",
                "",
                page_count,
                "pdf_page_limit_exceeded",
            )
        chunks: list[str] = []
        remaining = max_text_chars
        for page_number in range(min(page_count, sample_pages)):
            if remaining <= 0:
                break
            page_text = str(document[page_number].get_text("text") or "")
            chunks.append(page_text[:remaining])
            remaining -= len(chunks[-1])
        text = "\n".join(chunks).strip()
        if not text:
            return PdfInspection("no_text_layer", "", page_count, "pdf_no_text_layer")
        return PdfInspection("parsed", text, page_count)
    except Exception:
        return PdfInspection("failed", "", None, "pdf_parse_failed")
    finally:
        if document is not None:
            try:
                document.close()
            except Exception:
                pass


def _load_pymupdf() -> Any:
    try:
        return importlib.import_module("pymupdf")
    except ImportError:
        return importlib.import_module("fitz")
