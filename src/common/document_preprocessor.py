"""Map source PDFs to their ignored Markdown mirror and create missing files."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Protocol, Sequence

from src.common.model_config import ModelSelection


class MarkdownPreprocessor(Protocol):
    def convert(self, source_pdf: Path, output_markdown: Path) -> None: ...


class MinerUPreprocessor:
    """Run the installed MinerU CLI and retain its Markdown output only."""

    def __init__(self, run: Callable = subprocess.run) -> None:
        self.run = run

    def convert(self, source_pdf: Path, output_markdown: Path) -> None:
        executable = Path(sys.executable).with_name("mineru")
        with tempfile.TemporaryDirectory(prefix="mineru-") as temporary_directory:
            output_root = Path(temporary_directory)
            self.run(
                [str(executable), "-p", str(source_pdf), "-o", str(output_root), "-b", "pipeline"],
                check=True,
                capture_output=True,
                text=True,
            )
            markdown_files = sorted(output_root.rglob("*.md"))
            if not markdown_files:
                raise RuntimeError(f"MinerU produced no Markdown for {source_pdf}.")
            output_markdown.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(markdown_files[0], output_markdown)


def markdown_path(source_pdf: Path, pdf_root: Path) -> Path:
    """Return the Markdown mirror of a PDF below the configured PDF root."""
    if source_pdf.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a PDF source path, got {source_pdf}")
    resolved_source = source_pdf.resolve(strict=False)
    resolved_root = pdf_root.resolve(strict=False)
    try:
        relative = resolved_source.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(
            f"PDF source must be below the configured PDF root: {source_pdf}"
        ) from exc
    output = (pdf_root.parent / "Markdown" / relative).with_suffix(".md")
    _reject_markdown_symlinks(output, pdf_root.parent / "Markdown")
    return output


def ensure_markdown(
    source_pdf: Path,
    pdf_root: Path,
    preprocessor: MarkdownPreprocessor,
) -> Path:
    """Reuse the mirrored Markdown file or require the preprocessor to create it."""
    if not source_pdf.exists():
        raise FileNotFoundError(source_pdf)
    output_markdown = markdown_path(source_pdf, pdf_root)
    if output_markdown.is_symlink():
        raise ValueError(f"Markdown mirror must not be a symbolic link: {output_markdown}")
    if output_markdown.exists():
        return output_markdown

    output_markdown.parent.mkdir(parents=True, exist_ok=True)
    preprocessor.convert(source_pdf, output_markdown)
    _reject_markdown_symlinks(output_markdown, pdf_root.parent / "Markdown")
    if not output_markdown.exists():
        raise RuntimeError(
            f"Markdown preprocessor did not create {output_markdown} from {source_pdf}."
        )
    return output_markdown


def _reject_markdown_symlinks(output: Path, mirror_root: Path) -> None:
    """Reject symlink components so Markdown writes cannot escape the mirror."""
    try:
        relative = output.relative_to(mirror_root)
    except ValueError as exc:
        raise ValueError(f"Markdown output escapes its mirror root: {output}") from exc
    current = mirror_root
    if current.is_symlink():
        raise ValueError(f"Markdown mirror must not use a symbolic link: {current}")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Markdown mirror must not use a symbolic link: {current}")


def prepare_documents(
    selection: ModelSelection,
    source_pdfs: Sequence[str | Path],
    pdf_root: str | Path | None,
    preprocessor: MarkdownPreprocessor | None = None,
) -> tuple[Path, ...]:
    """Resolve the provider document paths for the selected document-input mode.

    PDF mode passes the sampled sources through untouched. Markdown mode maps
    each source below ``pdf_root`` to its mirrored ``Markdown/`` path via
    ``ensure_markdown`` and fails closed if a mirror cannot be produced. The
    caller keeps the original PDF paths for sampling identity, holdout
    exclusion, and usage logging.
    """
    sources = tuple(Path(path) for path in source_pdfs)
    if selection.document_input != "markdown":
        return sources
    if pdf_root is None:
        raise ValueError(
            "Markdown document input requires the PDF input root so sampled "
            "PDFs can be mapped to their Markdown mirrors."
        )
    resolved_preprocessor = preprocessor or MinerUPreprocessor()
    return tuple(
        ensure_markdown(source, Path(pdf_root), resolved_preprocessor) for source in sources
    )
