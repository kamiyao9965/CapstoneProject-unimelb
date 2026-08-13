from __future__ import annotations

import unittest

from src.PDFingestor.adapter import document_quality
from src.PDFingestor.models import PageRepresentation, ParsedPDF, TextBlock


class PDFIngestorQualityTest(unittest.TestCase):
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
