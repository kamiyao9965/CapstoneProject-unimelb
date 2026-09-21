"""Opt-in MinerU route that produces the same ParsedPDF structure as PDFingestor.

MinerU runs locally with the pipeline backend in a separate Python process. Its
``*_content_list.json`` output keeps page numbers, so each item becomes a text
block or a Markdown table block and the shared prompt renderer stays unchanged.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from html.parser import HTMLParser
from importlib import metadata
from pathlib import Path
from typing import Any

from src.PDFingestor.cache import PDFCache, sha256_file, stable_config_hash
from src.PDFingestor.models import BBox, PageRepresentation, ParsedPDF, TableBlock, TextBlock
from src.PDFingestor.parser import (
    clean_rows,
    estimate_table_confidence,
    split_headers,
    table_to_markdown,
)


MINERU_ADAPTER_VERSION = "mineru-adapter.v1"
MINERU_BACKEND = "pipeline"
MINERU_METHOD = "auto"
# MinerU's "ch" OCR model covers Chinese, English and Latin text.
MINERU_LANGUAGE = "ch"
DEFAULT_TIMEOUT_SECONDS = 3600.0
# content_list bounding boxes are normalised to a 0-1000 page coordinate space.
NORMALIZED_PAGE_SIZE = 1000.0
MAX_CELL_SPAN = 100
WORKER_MINERU_MISSING_EXIT_CODE = 3

# The worker keeps MinerU's models out of the calling process and calls MinerU's
# documented do_parse() directly. The MinerU 3.x CLI instead starts a temporary
# HTTP service whose status polling fails during CPU-heavy post-processing.
_WORKER_CODE = """
import sys
try:
    from mineru.cli.common import do_parse
except ModuleNotFoundError as exc:
    if exc.name != "mineru":
        raise
    sys.exit(3)
pdf_path, output_dir, backend, method, lang = sys.argv[1:6]
with open(pdf_path, "rb") as handle:
    pdf_bytes = handle.read()
do_parse(
    output_dir, ["document"], [pdf_bytes], [lang],
    backend=backend, parse_method=method,
    f_draw_layout_bbox=False, f_draw_span_bbox=False, f_dump_md=False,
    f_dump_middle_json=False, f_dump_model_output=False, f_dump_orig_pdf=False,
    f_dump_content_list=True,
)
"""

Runner = Callable[..., subprocess.CompletedProcess[str]]


class MinerUIngestor:
    """Parse one PDF with local MinerU in a worker process and cache the shared structure."""

    def __init__(
        self,
        cache_dir: str | Path | None = None,
        *,
        runner: Runner = subprocess.run,
        python_executable: str | Path | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.cache = PDFCache(cache_dir) if cache_dir is not None else None
        self.runner = runner
        self.python_executable = str(python_executable or sys.executable)
        self.timeout_seconds = timeout_seconds

    def ingest(self, pdf_path: str | Path, *, use_cache: bool = True) -> ParsedPDF:
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(path)

        pdf_hash = sha256_file(path)
        parser_config = self._parser_config()
        parser_config_hash = stable_config_hash(parser_config)
        if use_cache and self.cache is not None:
            cached = self.cache.load(pdf_hash, parser_config_hash)
            if cached is not None:
                return ParsedPDF.model_validate(cached)

        pages = content_list_to_pages(self._run_mineru(path))
        if not any(page.blocks for page in pages):
            raise RuntimeError(
                f"MinerU produced no text or table content for {path.name}; "
                "the model provider was not called."
            )
        stat = path.stat()
        parsed = ParsedPDF(
            pdf_id=path.name,
            pdf_hash=pdf_hash,
            source_path=str(path),
            pages=pages,
            parser={
                **parser_config,
                "parser_config_hash": parser_config_hash,
                "source_path": str(path),
                "source_mtime": stat.st_mtime,
                "source_size": stat.st_size,
            },
        )
        if self.cache is not None:
            self.cache.write(pdf_hash, parser_config_hash, parsed.model_dump(mode="json"))
        return parsed

    def _run_mineru(self, path: Path) -> list[Any]:
        with tempfile.TemporaryDirectory(prefix="mineru-") as temporary_directory:
            output_root = Path(temporary_directory)
            command = [
                self.python_executable,
                "-c", _WORKER_CODE,
                str(path),
                str(output_root),
                MINERU_BACKEND,
                MINERU_METHOD,
                MINERU_LANGUAGE,
            ]
            try:
                self.runner(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                )
            except FileNotFoundError as exc:
                raise RuntimeError(
                    f"Cannot start the MinerU worker with {self.python_executable}."
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"MinerU timed out after {self.timeout_seconds:g} seconds for {path.name}."
                ) from exc
            except subprocess.CalledProcessError as exc:
                if exc.returncode == WORKER_MINERU_MISSING_EXIT_CODE:
                    raise RuntimeError(
                        f"MinerU is not installed for {self.python_executable}; "
                        "run pip install -r requirements.txt."
                    ) from exc
                raise RuntimeError(
                    f"MinerU failed for {path.name} with exit code {exc.returncode}"
                    f"{_stderr_hint(exc.stderr)}."
                ) from exc

            content_lists = sorted(output_root.rglob("*_content_list.json"))
            if len(content_lists) != 1:
                raise RuntimeError(
                    f"MinerU produced {len(content_lists)} content lists for {path.name}; "
                    "expected exactly one."
                )
            payload = json.loads(content_lists[0].read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise RuntimeError(f"MinerU content list for {path.name} is not a JSON array.")
        return payload

    def _parser_config(self) -> dict[str, Any]:
        return {
            "engine": "mineru",
            "adapter_version": MINERU_ADAPTER_VERSION,
            "mineru_version": _installed_mineru_version(),
            "backend": MINERU_BACKEND,
            "method": MINERU_METHOD,
            "lang": MINERU_LANGUAGE,
        }


def content_list_to_pages(items: Sequence[Any]) -> list[PageRepresentation]:
    """Convert MinerU content_list items into ordered page representations."""
    pages: dict[int, PageRepresentation] = {}
    table_counts: dict[int, int] = {}
    for item in items:
        if not isinstance(item, Mapping):
            raise RuntimeError("MinerU content list items must be JSON objects.")
        page_idx = item.get("page_idx")
        if not isinstance(page_idx, int) or isinstance(page_idx, bool) or page_idx < 0:
            raise RuntimeError("MinerU content list item has no valid page_idx.")
        page_num = page_idx + 1
        page = pages.setdefault(
            page_num,
            PageRepresentation(
                page_num=page_num,
                width=NORMALIZED_PAGE_SIZE,
                height=NORMALIZED_PAGE_SIZE,
            ),
        )
        bbox = _bbox(item.get("bbox"))
        if item.get("type") == "table":
            table_counts[page_num] = table_counts.get(page_num, 0) + 1
            _append_table(page, item, bbox, table_counts[page_num])
        else:
            _append_text(page, _item_text(item), bbox)
    return [pages[number] for number in sorted(pages)]


def html_table_rows(markup: str) -> list[list[str]]:
    """Expand an HTML table into rows, repeating text across rowspan/colspan cells."""
    parser = _TableHTMLParser()
    parser.feed(markup)
    parser.close()
    grid: list[list[str]] = []
    pending: dict[int, tuple[str, int]] = {}
    for row_cells in parser.rows:
        row: list[str] = []
        cells = iter(row_cells)
        column = 0
        while True:
            if column in pending:
                text, remaining = pending.pop(column)
                row.append(text)
                if remaining > 1:
                    pending[column] = (text, remaining - 1)
                column += 1
                continue
            cell = next(cells, None)
            if cell is None:
                if not any(key > column for key in pending):
                    break
                row.append("")
                column += 1
                continue
            text, rowspan, colspan = cell
            for _ in range(colspan):
                row.append(text)
                if rowspan > 1:
                    pending[column] = (text, rowspan - 1)
                column += 1
        grid.append(row)
    return clean_rows(grid)


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[tuple[str, int, int]]] = []
        self._row: list[tuple[str, int, int]] | None = None
        self._cell_parts: list[str] | None = None
        self._cell_spans = (1, 1)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower_tag = tag.lower()
        if lower_tag == "tr":
            self._row = []
        elif lower_tag in {"td", "th"}:
            values = dict(attrs)
            self._cell_parts = []
            self._cell_spans = (_span(values.get("rowspan")), _span(values.get("colspan")))
        elif lower_tag == "br" and self._cell_parts is not None:
            self._cell_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        lower_tag = tag.lower()
        if lower_tag in {"td", "th"} and self._cell_parts is not None:
            if self._row is None:
                self._row = []
            text = " ".join("".join(self._cell_parts).split())
            self._row.append((text, *self._cell_spans))
            self._cell_parts = None
        elif lower_tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)


def _append_table(
    page: PageRepresentation,
    item: Mapping[str, Any],
    bbox: BBox | None,
    table_number: int,
) -> None:
    captions = _text_list(item.get("table_caption"))
    footnotes = _text_list(item.get("table_footnote"))
    body = item.get("table_body")
    rows = html_table_rows(body) if isinstance(body, str) else []
    if not rows:
        page.warnings.append(f"MinerU table {table_number} has no recognised cells.")
        _append_text(page, "\n".join([*captions, *footnotes]), bbox)
        return
    headers, data_rows, notes = split_headers(rows)
    table_bbox = bbox or (0.0, 0.0, 0.0, 0.0)
    page.blocks.append(
        TableBlock(
            block_id=f"p{page.page_num}-b{len(page.blocks) + 1}",
            table_id=f"p{page.page_num}-t{table_number}",
            markdown=table_to_markdown(headers, data_rows),
            raw_rows=rows,
            headers=headers,
            rows=data_rows,
            bbox=table_bbox,
            top=table_bbox[1],
            reading_order=len(page.blocks),
            caption_context={"summary": " ".join(captions)} if captions else {},
            source_engine="mineru",
            confidence=estimate_table_confidence(rows, table_bbox),
            extraction_notes=notes,
        )
    )
    _append_text(page, "\n".join(footnotes), bbox)


def _append_text(page: PageRepresentation, text: str, bbox: BBox | None) -> None:
    if not text.strip():
        return
    reading_order = len(page.blocks)
    page.blocks.append(
        TextBlock(
            block_id=f"p{page.page_num}-b{reading_order + 1}",
            content=text.strip(),
            bbox=bbox,
            top=bbox[1] if bbox else float(reading_order),
            reading_order=reading_order,
            source_engine="mineru",
        )
    )


def _item_text(item: Mapping[str, Any]) -> str:
    item_type = item.get("type")
    if item_type in {"image", "chart"}:
        return "\n".join(
            [
                *_text_list(item.get(f"{item_type}_caption")),
                *_text_list(item.get("content")),
                *_text_list(item.get(f"{item_type}_footnote")),
            ]
        )
    if item_type == "list":
        return "\n".join(f"- {entry}" for entry in _text_list(item.get("list_items")))
    if item_type == "code":
        return "\n".join(
            [
                *_text_list(item.get("code_caption")),
                *_text_list(item.get("code_body")),
                *_text_list(item.get("code_footnote")),
            ]
        )
    text = item.get("text")
    if not isinstance(text, str):
        return ""
    level = item.get("text_level")
    if isinstance(level, int) and not isinstance(level, bool) and level > 0 and text.strip():
        return f"{'#' * min(level, 6)} {text.strip()}"
    return text


def _text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return []


def _bbox(value: Any) -> BBox | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    if not all(isinstance(number, (int, float)) and not isinstance(number, bool) for number in value):
        return None
    return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))


def _span(value: str | None) -> int:
    try:
        parsed = int(value) if value is not None else 1
    except ValueError:
        return 1
    return max(1, min(parsed, MAX_CELL_SPAN))


def _stderr_hint(stderr: object) -> str:
    # MinerU writes progress bars to stderr, so prefer the last error line.
    lines = [line.strip() for line in re.split(r"[\r\n]+", str(stderr or "")) if line.strip()]
    errors = [line for line in lines if "Error" in line or "Exception" in line]
    candidates = errors or lines
    return f": {candidates[-1][:300]}" if candidates else ""


def _installed_mineru_version() -> str:
    try:
        return metadata.version("mineru")
    except metadata.PackageNotFoundError:
        return "not-installed"
