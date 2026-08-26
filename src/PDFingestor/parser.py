from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from src.PDFingestor.cache import PDFCache, sha256_file, stable_config_hash
from src.PDFingestor.models import (
    BBox,
    PageRepresentation,
    ParsedPDF,
    TableBlock,
    TextBlock,
    VisionBlock,
)

try:
    import pdfplumber
except ImportError:  # pragma: no cover - dependency guard
    pdfplumber = None

try:
    import camelot
except ImportError:  # pragma: no cover - optional fallback
    camelot = None


VisionPageExtractor = Callable[[Path, int], str]


DEFAULT_TABLE_SETTINGS: dict[str, Any] = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance": 3,
    "join_tolerance": 3,
    "intersection_tolerance": 3,
}

INGEST_VERSION = "pdfingestor.v7"


class PDFIngestor:
    """Parse a PDF once into cached, ordered text/table blocks.

    The code path uses pdfplumber for table detection and word coordinates. If
    a page cannot be parsed well enough, an optional vision callback can own the
    entire page so downstream consumers never mix duplicate sources.
    """

    def __init__(
        self,
        cache_dir: str | Path | None = None,
        *,
        table_settings: dict[str, Any] | None = None,
        camelot_enabled: bool = True,
        vision_page_extractor: VisionPageExtractor | None = None,
        table_margin: float = 1.5,
        min_word_table_overlap: float = 0.5,
        line_y_tolerance: float = 3.0,
        ingest_version: str = INGEST_VERSION,
    ) -> None:
        self.cache = PDFCache(cache_dir) if cache_dir is not None else None
        self.table_settings = table_settings or DEFAULT_TABLE_SETTINGS
        self.camelot_enabled = camelot_enabled
        self.vision_page_extractor = vision_page_extractor
        self.table_margin = table_margin
        self.min_word_table_overlap = min_word_table_overlap
        self.line_y_tolerance = line_y_tolerance
        self.ingest_version = ingest_version

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

        parsed = self._parse_pdf(path, pdf_hash, parser_config, parser_config_hash)
        if self.cache is not None:
            self.cache.write(
                pdf_hash,
                parser_config_hash,
                parsed.model_dump(mode="json"),
            )
        return parsed

    def _parse_pdf(
        self,
        path: Path,
        pdf_hash: str,
        parser_config: dict[str, Any],
        parser_config_hash: str,
    ) -> ParsedPDF:
        if pdfplumber is None:
            raise RuntimeError("pdfplumber is required for PDFingestor parsing.")

        stat = path.stat()
        pages: list[PageRepresentation] = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                pages.append(self._parse_page(path, page))

        return ParsedPDF(
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
                "engines": {
                    "pdfplumber": getattr(pdfplumber, "__version__", "unknown"),
                    "camelot": getattr(camelot, "__version__", None)
                    if camelot is not None
                    else None,
                },
            },
        )

    def _parse_page(self, pdf_path: Path, page: Any) -> PageRepresentation:
        page_num = int(page.page_number)
        warnings: list[str] = []
        try:
            table_objects = page.find_tables(table_settings=self.table_settings)
        except TypeError:
            table_objects = page.find_tables(self.table_settings)
        except Exception as exc:  # pragma: no cover - pdf-specific edge cases
            table_objects = []
            warnings.append(f"pdfplumber table detection failed: {exc}")

        tables = self._extract_pdfplumber_tables(page_num, page, table_objects, warnings)
        if self._should_try_camelot(tables, page):
            tables.extend(
                self._extract_camelot_tables(
                    pdf_path,
                    page_num,
                    float(page.height),
                    warnings,
                )
            )

        if self._should_use_vision(tables, warnings):
            return self._parse_page_with_vision(pdf_path, page, warnings)

        text_blocks = self._extract_text_blocks(page, [table.bbox for table in tables])
        ordered_blocks = self._with_reading_order(
            [*text_blocks, *tables],
            page_width=float(page.width),
        )
        self._attach_caption_context(ordered_blocks)
        return PageRepresentation(
            page_num=page_num,
            width=float(page.width),
            height=float(page.height),
            extraction_method="code",
            blocks=ordered_blocks,
            warnings=warnings,
        )

    def _extract_pdfplumber_tables(
        self,
        page_num: int,
        page: Any,
        table_objects: Sequence[Any],
        warnings: list[str],
    ) -> list[TableBlock]:
        tables: list[TableBlock] = []
        for index, table in enumerate(table_objects):
            table_id = f"p{page_num}_t{index}"
            try:
                raw_rows = clean_rows(table.extract() or [])
            except Exception as exc:  # pragma: no cover - pdf-specific edge cases
                warnings.append(f"{table_id} extraction failed: {exc}")
                continue
            if not raw_rows:
                continue
            raw_rows, recovered = enrich_graphical_coverage(page, table, raw_rows)
            if recovered:
                warnings.append(
                    f"{table_id} recovered {recovered} graphical coverage markers"
                )
            headers, rows, notes = split_headers(raw_rows)
            tables.append(
                TableBlock(
                    block_id=table_id,
                    table_id=table_id,
                    markdown=table_to_markdown(headers, rows),
                    raw_rows=raw_rows,
                    headers=headers,
                    rows=rows,
                    bbox=normalize_bbox(table.bbox),
                    top=float(table.bbox[1]),
                    source_engine="pdfplumber",
                    confidence=estimate_table_confidence(raw_rows, table.bbox),
                    extraction_notes=notes,
                )
            )
        return tables

    def _extract_camelot_tables(
        self,
        pdf_path: Path,
        page_num: int,
        page_height: float,
        warnings: list[str],
    ) -> list[TableBlock]:
        if not self.camelot_enabled or camelot is None:
            return []
        extracted: list[TableBlock] = []
        for flavor in ("lattice", "stream"):
            try:
                camelot_tables = camelot.read_pdf(
                    str(pdf_path),
                    pages=str(page_num),
                    flavor=flavor,
                )
            except Exception as exc:  # pragma: no cover - optional dependency
                warnings.append(f"camelot {flavor} failed on page {page_num}: {exc}")
                continue
            for table in camelot_tables:
                raw_rows = clean_rows(table.df.values.tolist())
                if not raw_rows:
                    continue
                headers, rows, notes = split_headers(raw_rows)
                bbox = camelot_bbox_to_pdfplumber(table._bbox, page_height)  # noqa: SLF001
                table_id = f"p{page_num}_camelot_{flavor}_{len(extracted)}"
                extracted.append(
                    TableBlock(
                        block_id=table_id,
                        table_id=table_id,
                        markdown=table_to_markdown(headers, rows),
                        raw_rows=raw_rows,
                        headers=headers,
                        rows=rows,
                        bbox=bbox,
                        top=bbox[1],
                        source_engine=f"camelot:{flavor}",
                        confidence=estimate_table_confidence(raw_rows, bbox),
                        extraction_notes=notes,
                    )
                )
            if extracted:
                break
        return extracted

    def _extract_text_blocks(self, page: Any, table_bboxes: Sequence[BBox]) -> list[TextBlock]:
        words = page.extract_words(
            x_tolerance=1,
            y_tolerance=3,
            keep_blank_chars=False,
            use_text_flow=True,
        ) or []
        text_words = [
            word
            for word in words
            if not self._word_overlaps_any_table(word, table_bboxes)
        ]
        lines = group_words_into_lines(
            text_words,
            self.line_y_tolerance,
            page_width=float(page.width),
        )
        return [
            TextBlock(
                block_id=f"p{page.page_number}_text_{index}",
                content=line["text"],
                bbox=line["bbox"],
                top=line["top"],
            )
            for index, line in enumerate(lines)
            if line["text"].strip()
        ]

    def _word_overlaps_any_table(self, word: dict[str, Any], table_bboxes: Sequence[BBox]) -> bool:
        word_bbox = normalize_bbox(
            (word["x0"], word["top"], word["x1"], word["bottom"])
        )
        for table_bbox in table_bboxes:
            expanded = expand_bbox(table_bbox, self.table_margin)
            if bbox_overlap_ratio(word_bbox, expanded) >= self.min_word_table_overlap:
                return True
        return False

    def _parse_page_with_vision(
        self,
        pdf_path: Path,
        page: Any,
        warnings: list[str],
    ) -> PageRepresentation:
        reason = "; ".join(warnings) or "code extraction deemed unreliable"
        content = self.vision_page_extractor(pdf_path, int(page.page_number)) if self.vision_page_extractor else ""
        blocks: list[VisionBlock] = []
        if content:
            blocks.append(
                VisionBlock(
                    block_id=f"p{page.page_number}_vision_0",
                    content=content,
                    bbox=(0.0, 0.0, float(page.width), float(page.height)),
                    confidence=0.5,
                )
            )
        return PageRepresentation(
            page_num=int(page.page_number),
            width=float(page.width),
            height=float(page.height),
            extraction_method="vision",
            vision_reason=reason,
            blocks=blocks,
            warnings=warnings,
        )

    def _should_try_camelot(self, tables: Sequence[TableBlock], page: Any) -> bool:
        if tables or not self.camelot_enabled or camelot is None:
            return False
        text = page.extract_text() or ""
        return looks_tabular(text)

    def _should_use_vision(self, tables: Sequence[TableBlock], warnings: Sequence[str]) -> bool:
        return self.vision_page_extractor is not None and not tables and bool(warnings)

    def _with_reading_order(
        self,
        blocks: list[TextBlock | TableBlock],
        *,
        page_width: float,
    ) -> list[TextBlock | TableBlock]:
        if _looks_like_two_column_page(blocks, page_width):
            ordered = _order_two_column_blocks(blocks, page_width)
        else:
            ordered = sorted(
                blocks,
                key=lambda block: (block.top, block.bbox[0] if block.bbox else 0.0),
            )
        for index, block in enumerate(ordered):
            block.reading_order = index
        return ordered

    def _attach_caption_context(self, blocks: list[TextBlock | TableBlock]) -> None:
        for index, block in enumerate(blocks):
            if block.type != "table":
                continue
            before = [
                item.content
                for item in blocks[max(0, index - 3) : index]
                if item.type == "text" and item.content.strip()
            ]
            after = [
                item.content
                for item in blocks[index + 1 : index + 3]
                if item.type == "text" and item.content.strip()
            ]
            block.caption_context = {
                "before_blocks": before,
                "after_blocks": after,
                "summary": "\n".join([*before[-2:], *after[:1]]).strip(),
            }

    def _parser_config(self) -> dict[str, Any]:
        return {
            "ingest_version": self.ingest_version,
            "table_settings": self.table_settings,
            "camelot_enabled": self.camelot_enabled,
            "vision_enabled": self.vision_page_extractor is not None,
            "table_margin": self.table_margin,
            "min_word_table_overlap": self.min_word_table_overlap,
            "line_y_tolerance": self.line_y_tolerance,
        }


def parse_pdf(
    pdf_path: str | Path,
    *,
    cache_dir: str | Path | None = None,
    use_cache: bool = True,
) -> ParsedPDF:
    return PDFIngestor(cache_dir=cache_dir).ingest(pdf_path, use_cache=use_cache)


def clean_rows(rows: Sequence[Sequence[Any]]) -> list[list[str]]:
    cleaned: list[list[str]] = []
    for row in rows:
        cells = [" ".join(str(cell or "").split()) for cell in row]
        if any(cells):
            cleaned.append(cells)
    return cleaned


def enrich_graphical_coverage(
    page: Any,
    table: Any,
    raw_rows: list[list[str]],
) -> tuple[list[list[str]], int]:
    """Translate legend-backed PDF marks into text table coverage values.

    Some PHIS tables draw Included/Excluded/Restricted as coloured vector icons,
    check marks, or symbol-font glyphs.  pdfplumber correctly extracts the row
    names but leaves the coverage cells empty.  This routine uses only a legend
    on the same page, so it does not infer product coverage from tier defaults.
    """
    if len(raw_rows) < 5 or max(map(len, raw_rows), default=0) < 2:
        return raw_rows, 0
    table_rows = list(getattr(table, "rows", ()) or ())
    if len(table_rows) != len(raw_rows):
        return raw_rows, 0

    column = max(map(len, raw_rows)) - 1
    bbox = normalize_bbox(table.bbox)
    words = page.extract_words(keep_blank_chars=False) or []
    curves = [
        curve for curve in (getattr(page, "curves", ()) or ())
        if float(curve.get("width") or 0) <= 30
        and float(curve.get("height") or 0) <= 30
    ]
    chars = list(getattr(page, "chars", ()) or ())
    curve_status: dict[str, str] = {}
    glyph_status: dict[str, str] = {}
    legend_tops: list[float] = []

    for word in words:
        status = _coverage_status(word.get("text"))
        if status is None:
            continue
        word_box = normalize_bbox((word["x0"], word["top"], word["x1"], word["bottom"]))
        # Ignore prose mentions inside the table's vertical span.  A usable
        # legend is either to the right of the table or beneath it.
        if word_box[0] < bbox[2] and word_box[1] < bbox[3]:
            continue
        if word_box[1] >= bbox[3]:
            legend_tops.append(word_box[1])
        for curve in curves:
            if _marker_precedes_label(curve, word_box):
                curve_status[_color_key(curve.get("non_stroking_color"))] = status
        for char in chars:
            if _marker_precedes_label(char, word_box) and _is_symbol_glyph(char):
                glyph_status[str(char.get("fontname") or "")] = status

    if not curve_status and not glyph_status:
        return raw_rows, 0

    has_header = any(
        token in _normalized_cell(cell)
        for cell in raw_rows[0]
        for token in ("coverage", "cover", "status", "clinicalcategory", "treatmentcategory")
    )
    result = [list(row) for row in raw_rows]
    recovered = 0
    row_offset = 0
    if not has_header and _coverage_status(result[0][column]) is not None:
        result.insert(0, ["Hospital treatment categories", "Coverage"])
        row_offset = 1

    for table_index, pdf_row in enumerate(table_rows):
        output_index = table_index + row_offset
        result[output_index] = pad_row(result[output_index], column + 1)
        explicit = _coverage_status(result[output_index][column])
        if explicit is not None:
            if result[output_index][column] != explicit:
                result[output_index][column] = explicit
                recovered += 1
            continue
        cells = list(getattr(pdf_row, "cells", ()) or ())
        if column >= len(cells) or cells[column] is None:
            continue
        status = _status_in_bbox(
            normalize_bbox(cells[column]), curves, chars, curve_status, glyph_status
        )
        if status is not None:
            result[output_index][column] = status
            recovered += 1

    # A table detector may stop at the last ruled row while the same marker
    # column continues immediately below it.  Recover those legend-backed rows
    # by aligning markers with words in the first column.
    first_cells = list(getattr(table_rows[0], "cells", ()) or ())
    last_cells = list(getattr(table_rows[-1], "cells", ()) or ())
    if first_cells and last_cells and first_cells[0] and last_cells[column]:
        label_x0, _, label_x1, _ = normalize_bbox(first_cells[0])
        status_x0, _, status_x1, _ = normalize_bbox(last_cells[column])
        cutoff = min(legend_tops) if legend_tops else float(page.height)
        markers = _coverage_markers(
            curves, chars, curve_status, glyph_status,
            x0=status_x0, x1=status_x1, top=bbox[3], bottom=cutoff,
        )
        existing = {_normalized_cell(row[0]) for row in result if row}
        for marker_top, marker_bottom, status in markers:
            label_words = [
                word for word in words
                if float(word["x0"]) >= label_x0 - 2
                and float(word["x1"]) <= label_x1 + 2
                and float(word["bottom"]) >= marker_top - 2
                and float(word["top"]) <= marker_bottom + 2
            ]
            label = " ".join(
                str(word["text"]) for word in sorted(label_words, key=lambda item: float(item["x0"]))
            ).strip()
            key = _normalized_cell(label)
            if label and key not in existing:
                result.append([label, status])
                existing.add(key)
                recovered += 1
    return result, recovered


def _coverage_status(value: Any) -> str | None:
    token = _normalized_cell(value)
    return {
        "r": "Restricted",
        "restricted": "Restricted",
        "restrictedservice": "Restricted",
        "minimumbenefits": "Restricted",
        "minimumbenefit": "Restricted",
        "defaultbenefits": "Restricted",
        "defaultbenefit": "Restricted",
        "publichospitalbenefits": "Restricted",
        "publichospitalonly": "Restricted",
        "limitedbenefits": "Restricted",
        "included": "Included",
        "includedservice": "Included",
        "covered": "Included",
        "excluded": "Excluded",
        "excludedservice": "Excluded",
        "notcovered": "Excluded",
    }.get(token)


def _normalized_cell(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _color_key(value: Any) -> str:
    return repr(value)


def _center(box: Sequence[float]) -> tuple[float, float]:
    normalized = normalize_bbox(box)
    return ((normalized[0] + normalized[2]) / 2, (normalized[1] + normalized[3]) / 2)


def _object_bbox(item: dict[str, Any]) -> BBox:
    return normalize_bbox((item["x0"], item["top"], item["x1"], item["bottom"]))


def _marker_precedes_label(marker: dict[str, Any], label_box: BBox) -> bool:
    marker_box = _object_bbox(marker)
    _, marker_y = _center(marker_box)
    _, label_y = _center(label_box)
    gap = label_box[0] - marker_box[2]
    return -2 <= gap <= 30 and abs(marker_y - label_y) <= 10


def _is_symbol_glyph(char: dict[str, Any]) -> bool:
    text = str(char.get("text") or "")
    font = str(char.get("fontname") or "").casefold()
    return any(ord(value) < 32 for value in text) or "symbol" in font


def _status_in_bbox(
    bbox: BBox,
    curves: Sequence[dict[str, Any]],
    chars: Sequence[dict[str, Any]],
    curve_status: dict[str, str],
    glyph_status: dict[str, str],
) -> str | None:
    for curve in curves:
        x, y = _center(_object_bbox(curve))
        status = curve_status.get(_color_key(curve.get("non_stroking_color")))
        if status and bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]:
            return status
    for char in chars:
        x, y = _center(_object_bbox(char))
        status = glyph_status.get(str(char.get("fontname") or ""))
        if status and bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]:
            return status
    return None


def _coverage_markers(
    curves: Sequence[dict[str, Any]],
    chars: Sequence[dict[str, Any]],
    curve_status: dict[str, str],
    glyph_status: dict[str, str],
    *,
    x0: float,
    x1: float,
    top: float,
    bottom: float,
) -> list[tuple[float, float, str]]:
    found: list[tuple[float, float, str]] = []
    for item, status in (
        *(
            (curve, curve_status.get(_color_key(curve.get("non_stroking_color"))))
            for curve in curves
        ),
        *(
            (char, glyph_status.get(str(char.get("fontname") or "")))
            for char in chars
        ),
    ):
        if status is None:
            continue
        item_box = _object_bbox(item)
        x, y = _center(item_box)
        if x0 <= x <= x1 and top < y < bottom:
            found.append((item_box[1], item_box[3], status))
    return sorted(found)


def split_headers(raw_rows: list[list[str]]) -> tuple[list[str], list[list[str]], list[str]]:
    if not raw_rows:
        return [], [], []
    notes: list[str] = []
    headers = raw_rows[0]
    rows = raw_rows[1:]
    if len(raw_rows) > 1 and any("\n" in cell for cell in raw_rows[0]):
        notes.append("first row contains multiline header cells")
    if len(raw_rows) > 2 and sum(bool(cell) for cell in raw_rows[0]) <= 1:
        notes.append("first row may be a table title rather than headers")
    return headers, rows, notes


def table_to_markdown(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    column_count = max([len(headers), *(len(row) for row in rows)] or [0])
    if column_count == 0:
        return ""
    normalized_headers = pad_row(headers, column_count)
    normalized_rows = [pad_row(row, column_count) for row in rows]
    lines = [
        "| " + " | ".join(escape_markdown_cell(cell) for cell in normalized_headers) + " |",
        "| " + " | ".join("---" for _ in range(column_count)) + " |",
    ]
    lines.extend(
        "| " + " | ".join(escape_markdown_cell(cell) for cell in row) + " |"
        for row in normalized_rows
    )
    return "\n".join(lines)


def pad_row(row: Sequence[str], length: int) -> list[str]:
    return [str(cell or "") for cell in row] + [""] * max(0, length - len(row))


def escape_markdown_cell(cell: str) -> str:
    return str(cell).replace("|", "\\|")


def group_words_into_lines(
    words: Sequence[dict[str, Any]],
    y_tolerance: float = 3.0,
    *,
    page_width: float | None = None,
) -> list[dict[str, Any]]:
    sorted_words = sorted(words, key=lambda word: (float(word["top"]), float(word["x0"])))
    lines: list[list[dict[str, Any]]] = []
    for word in sorted_words:
        if not lines:
            lines.append([word])
            continue
        current_top = average_top(lines[-1])
        if abs(float(word["top"]) - current_top) <= y_tolerance:
            lines[-1].append(word)
        else:
            lines.append([word])

    text_lines: list[dict[str, Any]] = []
    for line in lines:
        ordered = sorted(line, key=lambda word: float(word["x0"]))
        for segment in split_line_on_column_gap(ordered, page_width):
            text = " ".join(str(word["text"]) for word in segment)
            bbox = union_bboxes(
                normalize_bbox((word["x0"], word["top"], word["x1"], word["bottom"]))
                for word in segment
            )
            text_lines.append({"text": text, "bbox": bbox, "top": bbox[1]})
    return merge_nearby_lines(text_lines, page_width=page_width)


def split_line_on_column_gap(
    words: Sequence[dict[str, Any]],
    page_width: float | None,
) -> list[list[dict[str, Any]]]:
    if len(words) <= 1 or not page_width:
        return [list(words)]

    gap_threshold = max(26.0, page_width * 0.045)
    segments: list[list[dict[str, Any]]] = [[words[0]]]
    previous = words[0]
    for word in words[1:]:
        gap = float(word["x0"]) - float(previous["x1"])
        if gap > gap_threshold:
            segments.append([word])
        else:
            segments[-1].append(word)
        previous = word
    return segments


def merge_nearby_lines(
    lines: Sequence[dict[str, Any]],
    *,
    page_width: float | None = None,
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for line in lines:
        if not blocks:
            blocks.append(dict(line))
            continue
        previous = blocks[-1]
        vertical_gap = float(line["top"]) - float(previous["bbox"][3])
        if (
            0 <= vertical_gap <= 8
            and _same_text_column(previous["bbox"], line["bbox"], page_width)
        ):
            previous["text"] = f'{previous["text"]} {line["text"]}'.strip()
            previous["bbox"] = union_bboxes([previous["bbox"], line["bbox"]])
            previous["top"] = previous["bbox"][1]
        else:
            blocks.append(dict(line))
    return blocks


def _same_text_column(
    first: BBox,
    second: BBox,
    page_width: float | None,
) -> bool:
    overlap = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    narrower_width = max(1.0, min(first[2] - first[0], second[2] - second[0]))
    if overlap / narrower_width >= 0.25:
        return True
    if not page_width:
        return False
    midpoint = page_width / 2.0
    first_center = (first[0] + first[2]) / 2.0
    second_center = (second[0] + second[2]) / 2.0
    return (first_center < midpoint and second_center < midpoint) or (
        first_center >= midpoint and second_center >= midpoint
    )


def _looks_like_two_column_page(
    blocks: Sequence[TextBlock | TableBlock],
    page_width: float,
) -> bool:
    midpoint = page_width / 2.0
    left = 0
    right = 0
    for block in blocks:
        if not block.bbox or _is_full_width_block(block, page_width):
            continue
        center = (block.bbox[0] + block.bbox[2]) / 2.0
        if center < midpoint:
            left += 1
        else:
            right += 1
    return left >= 2 and right >= 2


def _order_two_column_blocks(
    blocks: Sequence[TextBlock | TableBlock],
    page_width: float,
) -> list[TextBlock | TableBlock]:
    ordered: list[TextBlock | TableBlock] = []
    pending_columns: list[TextBlock | TableBlock] = []

    for block in sorted(
        blocks,
        key=lambda item: (item.top, item.bbox[0] if item.bbox else 0.0),
    ):
        if _is_full_width_block(block, page_width):
            ordered.extend(_order_column_run(pending_columns, page_width))
            pending_columns = []
            ordered.append(block)
        else:
            pending_columns.append(block)

    ordered.extend(_order_column_run(pending_columns, page_width))
    return ordered


def _order_column_run(
    blocks: Sequence[TextBlock | TableBlock],
    page_width: float,
) -> list[TextBlock | TableBlock]:
    if not blocks:
        return []
    midpoint = page_width / 2.0
    left = [
        block
        for block in blocks
        if block.bbox and (block.bbox[0] + block.bbox[2]) / 2.0 < midpoint
    ]
    right = [block for block in blocks if block not in left]
    return [
        *sorted(left, key=lambda item: (item.top, item.bbox[0] if item.bbox else 0.0)),
        *sorted(right, key=lambda item: (item.top, item.bbox[0] if item.bbox else 0.0)),
    ]


def _is_full_width_block(block: TextBlock | TableBlock, page_width: float) -> bool:
    if not block.bbox:
        return False
    width = block.bbox[2] - block.bbox[0]
    midpoint = page_width / 2.0
    crosses_midpoint = block.bbox[0] < midpoint < block.bbox[2]
    return width >= page_width * 0.72 or (
        crosses_midpoint and width >= page_width * 0.55
    )


def average_top(words: Sequence[dict[str, Any]]) -> float:
    return sum(float(word["top"]) for word in words) / max(len(words), 1)


def normalize_bbox(bbox: Sequence[Any]) -> BBox:
    x0, top, x1, bottom = (float(value) for value in bbox)
    return (x0, top, x1, bottom)


def expand_bbox(bbox: BBox, margin: float) -> BBox:
    x0, top, x1, bottom = bbox
    return (x0 - margin, top - margin, x1 + margin, bottom + margin)


def bbox_overlap_ratio(inner: BBox, outer: BBox) -> float:
    ix0, itop, ix1, ibottom = inner
    ox0, otop, ox1, obottom = outer
    overlap_x = max(0.0, min(ix1, ox1) - max(ix0, ox0))
    overlap_y = max(0.0, min(ibottom, obottom) - max(itop, otop))
    inner_area = max((ix1 - ix0) * (ibottom - itop), 1e-6)
    return (overlap_x * overlap_y) / inner_area


def union_bboxes(bboxes: Sequence[BBox] | Any) -> BBox:
    materialized = list(bboxes)
    return (
        min(bbox[0] for bbox in materialized),
        min(bbox[1] for bbox in materialized),
        max(bbox[2] for bbox in materialized),
        max(bbox[3] for bbox in materialized),
    )


def estimate_table_confidence(rows: Sequence[Sequence[str]], bbox: BBox) -> float:
    if not rows:
        return 0.0
    column_counts = [len(row) for row in rows if row]
    if not column_counts:
        return 0.0
    majority_columns = max(set(column_counts), key=column_counts.count)
    consistency = column_counts.count(majority_columns) / len(column_counts)
    populated = sum(bool(cell.strip()) for row in rows for cell in row)
    total = sum(len(row) for row in rows) or 1
    density = populated / total
    area_bonus = 0.1 if (bbox[2] - bbox[0]) > 100 and (bbox[3] - bbox[1]) > 30 else 0.0
    return round(min(0.2 + 0.45 * consistency + 0.35 * density + area_bonus, 1.0), 3)


def looks_tabular(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        return False
    repeated_money = sum(1 for line in lines if len(re.findall(r"\$\s?\d+", line)) >= 2)
    spaced_columns = sum(1 for line in lines if re.search(r"\S\s{2,}\S\s{2,}\S", line))
    return repeated_money >= 2 or spaced_columns >= 3


def camelot_bbox_to_pdfplumber(camelot_bbox: Sequence[float], page_height: float) -> BBox:
    x0, y0, x1, y1 = (float(value) for value in camelot_bbox)
    if page_height <= 0:
        return (x0, y0, x1, y1)
    return (x0, page_height - y1, x1, page_height - y0)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Parse PDFs into structured JSON cache.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/pdfingestor"))
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    parsed = parse_pdf(args.pdf, cache_dir=args.cache_dir, use_cache=not args.no_cache)
    payload = parsed.model_dump(mode="json")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
