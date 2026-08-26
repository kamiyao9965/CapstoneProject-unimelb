from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.PDFingestor.adapter import document_quality
from src.PDFingestor.models import PageRepresentation, ParsedPDF, TextBlock
from src.PDFingestor.parser import _coverage_status, enrich_graphical_coverage


class PDFIngestorQualityTest(unittest.TestCase):
    def test_restricted_coverage_legend_phrases_are_preserved(self) -> None:
        for label in (
            "R", "Restricted", "Minimum benefits", "Default benefits",
            "Public hospital only", "Limited benefits",
        ):
            with self.subTest(label=label):
                self.assertEqual(_coverage_status(label), "Restricted")

    def test_empty_document_has_hard_failures(self) -> None:
        quality = document_quality((ParsedPDF(pdf_id="empty.pdf", pdf_hash="x", source_path="empty.pdf"),))
        self.assertIn("no_pages", quality["hard_failures"])
        self.assertIn("no_blocks", quality["hard_failures"])

    def test_counts_content_and_key_heading(self) -> None:
        document = ParsedPDF(
            pdf_id="cover.pdf", pdf_hash="x", source_path="cover.pdf",
            pages=[PageRepresentation(
                page_num=1, width=100, height=100,
                blocks=[TextBlock(block_id="b1", content="Hospital Cover - What's covered", top=1)],
            )],
        )
        quality = document_quality((document,))
        self.assertEqual(quality["pages"], 1)
        self.assertEqual(quality["blocks"], 1)
        self.assertTrue(quality["has_key_heading"])
        self.assertEqual(quality["hard_failures"], [])

    def test_legend_backed_vector_marks_fill_blank_coverage_cells(self) -> None:
        rows = [
            SimpleNamespace(cells=[(0, y, 100, y + 10), (100, y, 200, y + 10)])
            for y in range(0, 60, 10)
        ]
        table = SimpleNamespace(bbox=(0, 0, 200, 60), rows=rows)
        green = (0.0, 0.7, 0.6)
        curves = [
            {"x0": 210, "x1": 220, "top": 10, "bottom": 20,
             "width": 10, "height": 10, "non_stroking_color": green},
            *[
                {"x0": 140, "x1": 150, "top": y + 2, "bottom": y + 8,
                 "width": 10, "height": 6, "non_stroking_color": green}
                for y in range(10, 60, 10)
            ],
        ]
        page = SimpleNamespace(
            height=100,
            curves=curves,
            chars=[],
            extract_words=lambda **_: [{
                "text": "Included", "x0": 225, "x1": 270,
                "top": 10, "bottom": 20,
            }],
        )

        enriched, recovered = enrich_graphical_coverage(
            page,
            table,
            [["Hospital treatment categories", "Coverage"], *[
                [f"Category {index}", ""] for index in range(1, 6)
            ]],
        )

        self.assertEqual(recovered, 5)
        self.assertEqual([row[1] for row in enriched[1:]], ["Included"] * 5)
