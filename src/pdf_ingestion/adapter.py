from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Iterable, Mapping

from src.common.data_paths import PROJECT_ROOT
from src.common.json_artifacts import write_text_output
from src.pdf_ingestion.mineru import MinerUIngestor
from src.pdf_ingestion.parser import PDFIngestor
from src.pdf_ingestion.models import PageRepresentation, ParsedPDF


DEFAULT_CACHE_DIR = PROJECT_ROOT / ".cache/pdf/shared"
DOCUMENT_PARSERS = ("pdfingestor", "mineru")
DEFAULT_DOCUMENT_PARSER = "pdfingestor"


def build_ingestor(
    cache_dir: str | Path | None = None,
    *,
    camelot_enabled: bool = True,
) -> PDFIngestor:
    return PDFIngestor(
        cache_dir=cache_dir or DEFAULT_CACHE_DIR,
        camelot_enabled=camelot_enabled,
    )


def require_document_parser(document_parser: str) -> str:
    if document_parser not in DOCUMENT_PARSERS:
        raise ValueError(
            f"Unsupported document parser {document_parser!r}; "
            f"choose one of: {', '.join(DOCUMENT_PARSERS)}."
        )
    return document_parser


def ingest_pdfs(
    pdf_paths: Iterable[str | Path],
    *,
    cache_dir: str | Path | None = None,
    pdf_root: str | Path | None = None,
    camelot_enabled: bool = True,
    document_parser: str = DEFAULT_DOCUMENT_PARSER,
) -> tuple[ParsedPDF, ...]:
    # Both parsers cache by PDF hash plus parser configuration, so they can
    # share one cache directory without reusing each other's results.
    if require_document_parser(document_parser) == "mineru":
        ingestor: PDFIngestor | MinerUIngestor = MinerUIngestor(cache_dir or DEFAULT_CACHE_DIR)
    else:
        ingestor = build_ingestor(cache_dir, camelot_enabled=camelot_enabled)
    return tuple(
        ingestor.ingest(path)
        for path in resolve_pdf_paths(pdf_paths, pdf_root=pdf_root)
    )


def render_documents_for_prompt(
    documents: Iterable[ParsedPDF], *, document_refs: Iterable[Mapping[str, object]] = (),
) -> str:
    sections: list[str] = []
    refs = {str(Path(str(ref["source_path"])).resolve()): ref for ref in document_refs}
    for document in documents:
        ref = refs.get(str(Path(document.source_path).resolve()))
        sections.append(
            "\n".join(
                [
                    f"# PDF: {document.pdf_id}",
                    f"Source path: {document.source_path}",
                    f"PDF hash: {document.pdf_hash}",
                    *([f"Document ID: {ref['document_id']}", f"Document role: {ref['document_role']}",
                       f"Amends document IDs: {list(ref['amends_document_ids'])}"] if ref else []),
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
    document_parser: str = DEFAULT_DOCUMENT_PARSER,
    markdown_dir: str | Path | None = None,
    document_refs: Iterable[Mapping[str, object]] = (),
    parser_quality_gate: bool = False,
    parser_review_path: str | Path | None = None,
    source_index: dict[str, dict] | None = None,
) -> str:
    documents = ingest_pdfs(
        pdf_paths,
        cache_dir=cache_dir,
        pdf_root=pdf_root,
        camelot_enabled=camelot_enabled,
        document_parser=document_parser,
    )
    refs = tuple(document_refs)
    if refs:
        if len(refs) != len(documents) or any(
            Path(str(reference["source_path"])).resolve() != Path(document.source_path).resolve()
            or reference["sha256"] != document.pdf_hash
            for reference, document in zip(refs, documents)
        ):
            raise ValueError("Parsed PDFs must match the source document references and hashes.")
    if source_index is not None:
        if len(refs) != len(documents):
            raise ValueError("Source index requires an explicit reference for each parsed PDF.")
        for reference, document in zip(refs, documents):
            blocks, pages, ordered_blocks = {}, {}, []
            for page in document.pages:
                texts = []
                for block in page.blocks:
                    text = block.markdown if block.type == "table" else block.content
                    block_id = block.table_id if block.type == "table" else block.block_id
                    if block_id in blocks:
                        raise ValueError("Parsed source block IDs must be unique within each PDF.")
                    blocks[block_id] = {"page": page.page_num, "text": text}
                    texts.append(text)
                    if block.type == "table":
                        for row_index, row in enumerate(line.strip() for line in text.splitlines() if line.strip().startswith("|")):
                            if not re.fullmatch(r"[|:\s-]+", row):
                                ordered_blocks.append((page.page_num, f"{block_id}_row_{row_index}", row))
                    elif text.strip():
                        ordered_blocks.append((page.page_num, block_id, text))
                pages[page.page_num] = "\n".join(texts)
            source_index[str(reference["document_id"])] = {
                "pages": pages, "blocks": blocks, "ordered_blocks": ordered_blocks,
                "prompt_text": render_documents_for_prompt([document], document_refs=[reference]),
            }
    if markdown_dir is not None:
        for document in documents:
            save_document_markdown(
                document,
                markdown_dir,
                document_parser=document_parser,
                pdf_root=pdf_root,
            )
    if parser_quality_gate:
        from src.pdf_ingestion.quality import require_quality
        require_quality(documents, parser_review_path)
    return render_documents_for_prompt(documents, document_refs=refs)


def document_markdown_path(
    markdown_dir: str | Path,
    document_parser: str,
    source_path: str | Path,
    pdf_root: str | Path | None = None,
) -> Path:
    """Mirror the PDF's location below the input root inside one folder per parser."""
    source = Path(source_path).resolve()
    root = Path(pdf_root).resolve() if pdf_root is not None else None
    if root is not None and source.is_relative_to(root):
        relative = source.relative_to(root)
    else:
        # PDFs outside the input root keep distinct names without mirroring their location.
        identity = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:12]
        relative = Path(f"{source.stem}_{identity}.pdf")
    return Path(markdown_dir) / require_document_parser(document_parser) / relative.with_suffix(".md")


def save_document_markdown(
    document: ParsedPDF,
    markdown_dir: str | Path,
    *,
    document_parser: str,
    pdf_root: str | Path | None = None,
) -> Path:
    """Save the text sent to the model for one PDF so parser routes can be compared."""
    path = document_markdown_path(markdown_dir, document_parser, document.source_path, pdf_root)
    text = render_documents_for_prompt([document]) + "\n"
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        write_text_output(path, text, overwrite=True)
    return path


def render_page(page: PageRepresentation) -> str:
    chunks = [f"<!-- page {page.page_num} -->"]
    chunks.extend(f"[Parser warning: {warning}]" for warning in page.warnings)
    chunks.extend(f"[Continuation evidence: {link}]" for link in page.continuations)
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
