from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Iterable, Literal, Sequence

from src.PDFingestor.parser import PDFIngestor
from src.PDFingestor.models import PageRepresentation, ParsedPDF, TableBlock


DEFAULT_CACHE_DIR = Path("outputs/private_health/pdfingestor_cache")
TableFormat = Literal["markdown", "tsv", "csv"]
CommentLevel = Literal["full", "lite", "none"]


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
) -> str:
    sections: list[str] = []
    for index, document in enumerate(documents, 1):
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
    )


def render_page(
    page: PageRepresentation,
    *,
    table_format: TableFormat = "markdown",
    comment_level: CommentLevel = "full",
) -> str:
    chunks = [_page_marker(page.page_num, comment_level)]
    for block in page.blocks:
        if block.type == "text":
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
