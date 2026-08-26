from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.PDFingestor.models import PageRepresentation, ParsedPDF, TableBlock
from src.evaluation.document_classifier import PhisDocumentClassifier


class PhisDocumentClassifierTest(unittest.TestCase):
    @staticmethod
    def _document(rows: list[list[str]], *, text_id: str = "example.pdf") -> ParsedPDF:
        return ParsedPDF(
            pdf_id=text_id,
            pdf_hash="abc123",
            source_path=text_id,
            pages=[
                PageRepresentation(
                    page_num=1,
                    width=100,
                    height=100,
                    blocks=[
                        TableBlock(
                            block_id="p1_t0",
                            table_id="p1_t0",
                            markdown="\n".join(" | ".join(row) for row in rows),
                            raw_rows=rows,
                            bbox=(0, 0, 100, 100),
                            top=0,
                        )
                    ],
                )
            ],
        )

    @staticmethod
    def _ground_truth() -> dict[str, object]:
        return {
            "hospital": {
                "clinical_categories": [
                    {"category": "BackNeckSpine", "coverage": "Covered"},
                    {"category": "Blood", "coverage": "Covered"},
                ]
            }
        }

    def test_complete_classification_comes_from_source_rows(self) -> None:
        profile = PhisDocumentClassifier(manifest_path=None).classify_documents(
            (self._document([
                ["Clinical category", "Cover"],
                ["Back, neck and spine", "Covered"],
                ["Blood", "Covered"],
            ]),),
            self._ground_truth(),
        )

        self.assertEqual(profile["classification"], "complete_phis")
        self.assertTrue(profile["source_only"])
        self.assertEqual(
            profile["sections"]["hospital"]["visible_canonical_items"],
            ["BackNeckSpine", "Blood"],
        )

    def test_partial_profile_records_only_source_visible_categories(self) -> None:
        profile = PhisDocumentClassifier(manifest_path=None).classify_documents(
            (self._document([
                ["Clinical category", "Cover"],
                ["Back, neck and spine", "Covered"],
            ]),),
            self._ground_truth(),
        )

        self.assertEqual(profile["classification"], "partial")
        self.assertEqual(
            profile["sections"]["hospital"]["visible_canonical_items"],
            ["BackNeckSpine"],
        )

    def test_gold_style_benefit_table_is_non_standard(self) -> None:
        profile = PhisDocumentClassifier(manifest_path=None).classify_documents(
            (self._document([
                ["Service", "Benefit"],
                ["Private hospital accommodation", "100%"],
                ["Theatre fees", "100%"],
            ], text_id="Gold-Deluxe-Hospital-Cover.pdf"),),
            self._ground_truth(),
        )

        self.assertEqual(profile["classification"], "non_standard")
        self.assertEqual(
            profile["sections"]["hospital"]["recognized_item_count"], 0
        )

    def test_one_visible_canonical_item_is_partial_even_below_twenty_percent(self) -> None:
        ground_truth = {"hospital": {"clinical_categories": [
            {"category": name, "coverage": "Covered"}
            for name in (
                "BackNeckSpine", "Blood", "BrainNervousSystem", "BreastSurgery",
                "ChemotherapyRadiotherapyImmunotherapyForCancer", "DentalSurgery",
            )
        ]}}
        profile = PhisDocumentClassifier(manifest_path=None).classify_documents(
            (self._document([["Benefits"], ["Blood"]]),), ground_truth,
        )

        self.assertEqual(profile["classification"], "partial")
        self.assertEqual(profile["sections"]["hospital"]["recognized_item_count"], 1)

    def test_manifest_override_is_applied_by_source_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "classes.json"
            manifest.write_text(json.dumps({
                "documents": {"abc123": {"classification": "non_standard"}}
            }))
            profile = PhisDocumentClassifier(
                manifest_path=manifest
            ).classify_documents(
                (self._document([["Back, neck and spine", "Covered"]]),),
                self._ground_truth(),
            )

        self.assertEqual(profile["method"], "manual_manifest")
        self.assertEqual(
            profile["sections"]["hospital"]["classification"],
            "non_standard",
        )


if __name__ == "__main__":
    unittest.main()
