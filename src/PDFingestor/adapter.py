from __future__ import annotations

import csv
import io
import re
from collections import Counter
from pathlib import Path
from typing import Iterable, Literal, Sequence

from src.PDFingestor.models import (
    PageRepresentation,
    ParsedPDF,
    TableBlock,
    TextBlock,
    VisionBlock,
)
from src.PDFingestor.parser import PDFIngestor


DEFAULT_CACHE_DIR = Path("outputs/private_health/pdfingestor_cache")
TableFormat = Literal["markdown", "tsv", "csv"]
CommentLevel = Literal["full", "lite", "none"]


def document_quality(documents: Iterable[ParsedPDF]) -> dict[str, object]:
    """Summarize whether parsed PDFs contain meaningful model input."""
    documents = tuple(documents)
    pages = [page for document in documents for page in document.pages]
    blocks = [block for page in pages for block in page.blocks]
    tables = [block for block in blocks if block.type == "table"]
    content = "\n".join(
        block.markdown if block.type == "table" else block.content
        for block in blocks
    )
    lowered = content.casefold()
    headings = tuple(
        heading for heading in ("hospital cover", "what's covered", "what’s covered", "what is covered")
        if heading in lowered
    )
    non_whitespace_characters = sum(not character.isspace() for character in content)
    hard_failures = []
    if not pages:
        hard_failures.append("no_pages")
    if not blocks:
        hard_failures.append("no_blocks")
    if not non_whitespace_characters:
        hard_failures.append("no_non_whitespace_content")
    return {
        "documents": len(documents),
        "pages": len(pages),
        "blocks": len(blocks),
        "tables": len(tables),
        "non_whitespace_characters": non_whitespace_characters,
        "key_headings": list(headings),
        "has_key_heading": bool(headings),
        "hard_failures": hard_failures,
    }


def build_ingestor(
    cache_dir: str | Path | None = None,
    *,
    camelot_enabled: bool = True,
) -> PDFIngestor:
    return PDFIngestor(
        cache_dir=cache_dir or DEFAULT_CACHE_DIR,
        camelot_enabled=camelot_enabled,
    )


def ingest_pdfs(
    pdf_paths: Iterable[str | Path],
    *,
    cache_dir: str | Path | None = None,
    pdf_root: str | Path | None = None,
    camelot_enabled: bool = True,
) -> tuple[ParsedPDF, ...]:
    ingestor = build_ingestor(cache_dir, camelot_enabled=camelot_enabled)
    return tuple(
        ingestor.ingest(path)
        for path in resolve_pdf_paths(pdf_paths, pdf_root=pdf_root)
    )


def render_documents_for_prompt(
    documents: Iterable[ParsedPDF],
    *,
    table_format: TableFormat = "markdown",
    comment_level: CommentLevel = "full",
    include_document_metadata: bool = True,
    include_document_title: bool = True,
    conservative_filter: bool = False,
) -> str:
    sections: list[str] = []
    for index, document in enumerate(documents, 1):
        repeated_marginal_text = (
            _repeated_marginal_text(document) if conservative_filter else set()
        )
        title = f"# PDF: {document.pdf_id}" if include_document_title else f"# Document {index}"
        chunks = [title]
        if include_document_metadata:
            chunks.extend(
                [
                    f"Source path: {document.source_path}",
                    f"PDF hash: {document.pdf_hash}",
                ]
            )
        chunks.append(
            "\n\n".join(
                render_page(
                    page,
                    table_format=table_format,
                    comment_level=comment_level,
                    repeated_marginal_text=repeated_marginal_text,
                )
                for page in document.pages
            )
        )
        sections.append("\n".join(chunks).strip())
    return "\n\n---\n\n".join(section for section in sections if section.strip())


def render_pdf_paths_for_prompt(
    pdf_paths: Iterable[str | Path],
    *,
    cache_dir: str | Path | None = None,
    pdf_root: str | Path | None = None,
    camelot_enabled: bool = True,
    table_format: TableFormat = "markdown",
    comment_level: CommentLevel = "full",
    include_document_metadata: bool = True,
    include_document_title: bool = True,
    conservative_filter: bool = False,
) -> str:
    return render_documents_for_prompt(
        ingest_pdfs(
            pdf_paths,
            cache_dir=cache_dir,
            pdf_root=pdf_root,
            camelot_enabled=camelot_enabled,
        ),
        table_format=table_format,
        comment_level=comment_level,
        include_document_metadata=include_document_metadata,
        include_document_title=include_document_title,
        conservative_filter=conservative_filter,
    )


def render_page(
    page: PageRepresentation,
    *,
    table_format: TableFormat = "markdown",
    comment_level: CommentLevel = "full",
    repeated_marginal_text: set[str] | None = None,
) -> str:
    chunks = [_page_marker(page.page_num, comment_level)]
    for block in page.blocks:
        if block.type == "text":
            if _drop_text_block(block, page, repeated_marginal_text or set()):
                continue
            if comment_level == "full":
                chunks.append(f"<!-- text block_id={block.block_id} -->")
            chunks.append(block.content)
        elif block.type == "table":
            marker = _table_marker(block, page.page_num, comment_level)
            if marker:
                chunks.append(marker)
            context = block.caption_context.get("summary")
            if context and comment_level != "none":
                chunks.append(f"table_context: {context}")
            chunks.append(render_table(block, table_format=table_format))
        else:
            chunks.append(block.content)
    return "\n\n".join(chunk for chunk in chunks if chunk.strip()).strip()


_PURE_URL = re.compile(r"^(?:https?://|www\.)\S+/?$", re.IGNORECASE)
_PURE_PAGE_NUMBER = re.compile(
    r"^(?:page\s*)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?$",
    re.IGNORECASE,
)


def _drop_text_block(
    block: TextBlock | VisionBlock,
    page: PageRepresentation,
    repeated_marginal_text: set[str],
) -> bool:
    text = _normalize_text(block.content)
    if not text:
        return True
    if _PURE_URL.fullmatch(text) or _PURE_PAGE_NUMBER.fullmatch(text):
        return True
    return text in repeated_marginal_text and _is_marginal(block, page)


def _repeated_marginal_text(document: ParsedPDF) -> set[str]:
    counts: Counter[str] = Counter()
    for page in document.pages:
        for block in page.blocks:
            if block.type != "text" or not _is_marginal(block, page):
                continue
            text = _normalize_text(block.content)
            if text and len(text) <= 160:
                counts[text] += 1
    return {text for text, count in counts.items() if count >= 3}


def _is_marginal(block: TextBlock | VisionBlock, page: PageRepresentation) -> bool:
    if page.height <= 0:
        return False
    return block.top <= page.height * 0.12 or block.top >= page.height * 0.88


def _normalize_text(value: str) -> str:
    return " ".join(value.strip().split())


def render_table(block: TableBlock, *, table_format: TableFormat = "markdown") -> str:
    if table_format == "markdown":
        return block.markdown
    rows = table_rows(block)
    if table_format == "tsv":
        return "\n".join(
            "\t".join(cell for cell in row).rstrip()
            for row in rows
            if any(cell.strip() for cell in row)
        )
    if table_format == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        for row in rows:
            if any(cell.strip() for cell in row):
                writer.writerow(row)
        return buffer.getvalue().strip()
    raise ValueError(f"Unsupported table format: {table_format}")


def table_rows(block: TableBlock) -> Sequence[Sequence[str]]:
    if block.headers or block.rows:
        return [block.headers, *block.rows]
    return block.raw_rows


def _page_marker(page_num: int, comment_level: CommentLevel) -> str:
    if comment_level == "full":
        return f"<!-- page {page_num} -->"
    if comment_level == "lite":
        return f"[page {page_num}]"
    return ""


def _table_marker(
    block: TableBlock,
    page_num: int,
    comment_level: CommentLevel,
) -> str:
    if comment_level == "full":
        return (
            "<!-- "
            f"table table_id={block.table_id} "
            f"source={block.source_engine} "
            f"confidence={block.confidence} "
            f"bbox={block.bbox}"
            " -->"
        )
    if comment_level == "lite":
        return f"[table {block.table_id} page={page_num} confidence={block.confidence}]"
    return ""


def resolve_pdf_paths(
    pdf_paths: Iterable[str | Path],
    *,
    pdf_root: str | Path | None = None,
) -> tuple[Path, ...]:
    root = Path(pdf_root) if pdf_root else None
    resolved: list[Path] = []
    for value in pdf_paths:
        path = Path(value)
        if path.exists() or path.is_absolute() or root is None:
            resolved.append(path)
            continue
        rooted = root / path
        resolved.append(rooted if rooted.exists() else path)
    return tuple(resolved)
