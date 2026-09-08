from __future__ import annotations

from pathlib import Path
from typing import Iterable

from src.PDFingestor.parser import PDFIngestor
from src.PDFingestor.models import PageRepresentation, ParsedPDF


DEFAULT_CACHE_DIR = Path("outputs/pdfingestor_cache")


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


def render_documents_for_prompt(documents: Iterable[ParsedPDF]) -> str:
    sections: list[str] = []
    for document in documents:
        sections.append(
            "\n".join(
                [
                    f"# PDF: {document.pdf_id}",
                    f"Source path: {document.source_path}",
                    f"PDF hash: {document.pdf_hash}",
                    "",
                    "\n\n".join(render_page(page) for page in document.pages),
                ]
            ).strip()
        )
    return "\n\n---\n\n".join(section for section in sections if section.strip())


def render_pdf_paths_for_prompt(
    pdf_paths: Iterable[str | Path],
    *,
    cache_dir: str | Path | None = None,
    pdf_root: str | Path | None = None,
    camelot_enabled: bool = True,
) -> str:
    return render_documents_for_prompt(
        ingest_pdfs(
            pdf_paths,
            cache_dir=cache_dir,
            pdf_root=pdf_root,
            camelot_enabled=camelot_enabled,
        )
    )


def render_page(page: PageRepresentation) -> str:
    chunks = [f"<!-- page {page.page_num} -->"]
    for block in page.blocks:
        if block.type == "text":
            chunks.append(f"<!-- text block_id={block.block_id} -->")
            chunks.append(block.content)
        elif block.type == "table":
            chunks.append(
                "<!-- "
                f"table table_id={block.table_id} "
                f"source={block.source_engine} "
                f"confidence={block.confidence} "
                f"bbox={block.bbox}"
                " -->"
            )
            context = block.caption_context.get("summary")
            if context:
                chunks.append(f"<!-- table_context: {context} -->")
            chunks.append(block.markdown)
        else:
            chunks.append(block.content)
    return "\n\n".join(chunk for chunk in chunks if chunk.strip()).strip()


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
