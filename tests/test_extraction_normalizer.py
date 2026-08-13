from __future__ import annotations

import unittest

from src.schema_application.normalizer import normalize_extraction


class ExtractionNormalizerTest(unittest.TestCase):
    def test_expands_explicit_global_hospital_category_row(self) -> None:
        result = normalize_extraction(
            {
                "hospital_clinical_categories": [
                    {
                        "category_name": "All clinical categories",
                        "status": "restricted",
                        "notes": "Public hospital shared room only",
                    }
                ]
            },
            {
                "hospital_categories": [
                    {"canonical_name": "BackNeckSpine"},
                    {"canonical_name": "Blood"},
                ]
            },
        )

        self.assertEqual(
            [item["category_name"] for item in result["hospital_clinical_categories"]],
            ["BackNeckSpine", "Blood"],
        )
        self.assertTrue(all(
            item["status"] == "restricted"
            for item in result["hospital_clinical_categories"]
        ))

    def test_does_not_expand_from_tier_without_global_evidence(self) -> None:
        payload = {"product_tier": "Gold", "hospital_clinical_categories": None}
        self.assertEqual(
            normalize_extraction(payload, {"hospital_categories": [{"canonical_name": "Blood"}]}),
            payload,
        )

    def test_filters_noncanonical_extras_rows(self) -> None:
        result = normalize_extraction(
            {
                "extras_benefits": [
                    {"service_name": "GeneralDental", "limit": 500},
                    {"service_name": "Per visit benefit", "limit": 40},
                    {"description": "Provider rules"},
                ]
            },
            {"extras_services": [{"canonical_name": "GeneralDental"}]},
        )
        self.assertEqual(
            result["extras_benefits"],
            [{"service_name": "GeneralDental", "limit": 500}],
        )
