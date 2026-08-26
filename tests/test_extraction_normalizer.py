from __future__ import annotations

import unittest

from src.schema_application.normalizer import normalize_extraction
from src.schema_application.extraction_validation import validate_unfilled_consistency
from src.evaluation.adapter import CompatibilityConflictError, adapt_final_schema_for_evaluation


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

    def test_legacy_alias_conflict_is_not_silently_resolved(self) -> None:
        with self.assertRaisesRegex(CompatibilityConflictError, "Conflicting legacy aliases"):
            adapt_final_schema_for_evaluation({
                "extras_benefits": [{"service_name": "Dental", "service": "Physio"}]
            })
        adapted = adapt_final_schema_for_evaluation({
            "extras_benefits": [{"name": "Dental"}]
        })
        self.assertTrue(adapted["extras"]["services"][0]["service"])
        self.assertNotIn("name", adapted["extras"]["services"][0])

    def test_unfilled_matches_applicable_null_fields_only(self) -> None:
        schema = {"fields": [
            {"name": "hospital_only", "applies_to": ["hospital"]},
            {"name": "extras_only", "applies_to": ["extras"]},
        ]}
        validate_unfilled_consistency(
            {"product_type": "hospital", "hospital_only": None,
             "extras_only": None, "_unfilled": ["hospital_only"]}, schema
        )
        with self.assertRaisesRegex(ValueError, "must be listed"):
            validate_unfilled_consistency(
                {"product_type": "hospital", "hospital_only": None,
                 "extras_only": None, "_unfilled": []}, schema
            )
        with self.assertRaisesRegex(ValueError, "inapplicable"):
            validate_unfilled_consistency(
                {"product_type": "hospital", "hospital_only": "yes",
                 "extras_only": None, "_unfilled": ["extras_only"]}, schema
            )
