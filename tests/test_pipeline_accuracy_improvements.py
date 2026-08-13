from __future__ import annotations

import unittest

from src.evaluation.metrics import ExtractionEvaluator, PrivateHealthGroundTruthStore
from src.evaluation.taxonomy import canonical_extras_services
from src.models import ExtractionResult


class EvaluationMetricTest(unittest.TestCase):
    @staticmethod
    def _result(data: dict[str, object]) -> ExtractionResult:
        return ExtractionResult(
            vertical="private_health",
            schema_version="test",
            source_path="example.pdf",
            data=data,
        )

    def test_non_applicable_sections_are_none_and_skipped_during_aggregation(self) -> None:
        evaluator = ExtractionEvaluator()
        hospital = evaluator.evaluate(
            self._result(
                {
                    "hospital": {
                        "clinical_categories": [
                            {"category": "BackNeckSpine", "coverage": "Covered"},
                        ],
                    },
                }
            ),
            {
                "hospital": {
                    "clinical_categories": [
                        {"category": "BackNeckSpine", "coverage": "Covered"},
                    ],
                },
            },
        )
        extras = evaluator.evaluate(
            self._result(
                {
                    "extras": {
                        "services": [
                            {"service": "GeneralDental", "covered": True},
                        ],
                    },
                }
            ),
            {
                "extras": {
                    "services": [
                        {"service": "GeneralDental", "covered": True},
                    ],
                },
            },
        )

        self.assertIsNone(hospital.section_metrics["extras"])
        self.assertIsNone(extras.section_metrics["hospital"])
        summary = evaluator.aggregate([hospital, extras])
        self.assertEqual(summary["hospital_category_recall"], 1.0)
        self.assertEqual(summary["hospital_coverage_accuracy"], 1.0)
        self.assertEqual(summary["extras_service_recall"], 1.0)
        self.assertEqual(summary["extras_service_precision"], 1.0)

    def test_unknown_extras_label_is_not_preserved_as_a_service(self) -> None:
        self.assertEqual(canonical_extras_services("Per visit benefit"), [])
        self.assertEqual(canonical_extras_services("Invented service"), [])
        self.assertEqual(canonical_extras_services("Glasses - Frames"), ["Optical"])
        self.assertEqual(canonical_extras_services("Major Dental - Surgery"), ["DentalMajor"])
        self.assertEqual(
            canonical_extras_services("Chiro/Osteo X-rays"),
            ["Chiropractic", "Osteopathy"],
        )

    def test_missing_hospital_category_surface_is_non_comparable(self) -> None:
        report = ExtractionEvaluator().evaluate(
            self._result({
                "hospital": {"product_name": "Basic Hospital"},
                "_notes": "Hospital clinical categories are not explicitly listed.",
            }),
            {
                "hospital": {
                    "product_name": "Basic Hospital",
                    "clinical_categories": [
                        {"category": "BackNeckSpine", "coverage": "Restricted"},
                    ],
                },
            },
        )

        self.assertEqual(report.field_recall, 1.0)
        self.assertEqual(report.ground_truth_fields, 1)
        self.assertEqual(
            report.section_metrics["hospital"]["non_comparable_reason"],
            "no_extracted_clinical_category_surface",
        )
        self.assertNotIn(
            "hospital.clinical_categories.backneckspine.coverage",
            report.missing_fields,
        )

    def test_empty_categories_without_absence_evidence_remain_comparable(self) -> None:
        report = ExtractionEvaluator().evaluate(
            self._result({"_notes": "No PDF representation was provided."}),
            {
                "hospital": {
                    "clinical_categories": [
                        {"category": "BackNeckSpine", "coverage": "Restricted"},
                    ],
                },
            },
        )

        self.assertEqual(report.field_recall, 0.0)
        self.assertEqual(report.section_metrics["hospital"]["category_recall"], 0.0)

    def test_final_schema_is_adapted_to_ground_truth_sections(self) -> None:
        extracted = ExtractionResult(
            vertical="private_health",
            schema_version="test",
            source_path="example.pdf",
            data={
                "product_name": "Example Cover",
                "product_tier": "Silver",
                "hospital_clinical_categories": [
                    {"category_name": "BackNeckSpine", "status": "covered"},
                ],
                "extras_benefits": [
                    {"service_name": "GeneralDental"},
                ],
                "waiting_periods": [
                    {"service": "General Dental", "period": "2 Month"},
                ],
                "annual_limits": [
                    {
                        "service": "General Dental",
                        "limit_amount": 500.0,
                        "per": "person per year",
                    },
                ],
            },
        )
        ground_truth = {
            "hospital": {
                "product_name": "Example Cover",
                "hospital_tier": "Silver",
                "clinical_categories": [
                    {"category": "BackNeckSpine", "coverage": "Covered"},
                ],
            },
            "extras": {
                "product_name": "Example Cover",
                "services": [
                    {
                        "service": "GeneralDental",
                        "covered": True,
                        "waiting_period": "2 Month",
                        "limit_per_person": 500.0,
                    },
                ],
            },
        }

        report = ExtractionEvaluator().evaluate(extracted, ground_truth)

        self.assertEqual(report.field_precision, 1.0)
        self.assertEqual(report.field_recall, 1.0)
        self.assertIsNone(report.hallucination_rate)
        self.assertEqual(report.section_metrics["hospital"]["coverage_accuracy"], 1.0)
        self.assertEqual(report.section_metrics["extras"]["waiting_period_accuracy"], 1.0)
        self.assertEqual(report.section_metrics["extras"]["limit_accuracy"], 1.0)

    def test_fields_outside_ground_truth_are_unscored_not_hallucinations(self) -> None:
        extracted = ExtractionResult(
            vertical="private_health",
            schema_version="test",
            source_path="example.pdf",
            data={
                "extras": {
                    "services": [
                        {"service": "GeneralDental", "covered": True},
                        {"service": "InventedService", "covered": True},
                    ]
                }
            },
        )
        ground_truth = {
            "extras": {
                "services": [
                    {"service": "GeneralDental", "covered": True},
                    {"service": "MajorDental", "covered": True},
                ]
            }
        }

        report = ExtractionEvaluator().evaluate(extracted, ground_truth)

        self.assertEqual(report.matched_fields, 1)
        self.assertEqual(report.comparable_fields, 1)
        self.assertAlmostEqual(report.coverage, 0.5)
        self.assertAlmostEqual(report.field_presence_recall, 0.5)
        self.assertAlmostEqual(report.value_accuracy, 1.0)
        self.assertAlmostEqual(report.field_precision, 1.0)
        self.assertIsNone(report.hallucination_rate)
        self.assertFalse(report.hallucination_evaluated)
        self.assertEqual(report.hallucinations_by_section, {})
        self.assertAlmostEqual(report.section_metrics["extras"]["service_precision"], 1.0)
        self.assertAlmostEqual(report.section_metrics["extras"]["service_recall"], 0.5)

    def test_wrong_value_in_ground_truth_scope_is_incorrect_but_not_hallucination(self) -> None:
        extracted = ExtractionResult(
            vertical="private_health",
            schema_version="test",
            source_path="example.pdf",
            data={
                "hospital": {
                    "clinical_categories": [
                        {"category": "BackNeckSpine", "coverage": "Restricted"},
                    ],
                },
            },
        )
        ground_truth = {
            "hospital": {
                "clinical_categories": [
                    {"category": "BackNeckSpine", "coverage": "Covered"},
                ],
            },
        }

        report = ExtractionEvaluator().evaluate(extracted, ground_truth)

        self.assertEqual(report.field_precision, 0.0)
        self.assertEqual(
            report.incorrect_fields,
            ["hospital.clinical_categories.backneckspine.coverage"],
        )
        self.assertIsNone(report.hallucination_rate)

    def test_final_schema_limits_and_combined_services_are_canonicalized(self) -> None:
        report = ExtractionEvaluator().evaluate(
            self._result(
                {
                    "extras_benefits": [
                        {
                            "service_name": "Physiotherapy - Standard Treatment",
                            "limit": "$300 combined",
                            "per": "person",
                            "combined_limits": "Combined with Chiropractic/Osteopathy & Podiatry",
                        },
                        {
                            "service_name": "Dental - Crowns & bridges",
                            "limit": "$700 ($400 sub limit)",
                            "per": "person",
                        },
                    ],
                }
            ),
            {
                "extras": {
                    "services": [
                        {
                            "service": "Physiotherapy",
                            "covered": True,
                            "limit_per_person": 300,
                            "shared_with": ["Chiropractic", "Osteopathy", "Podiatry"],
                        },
                        {"service": "DentalMajor", "covered": True, "limit_per_person": 700},
                    ]
                }
            },
        )

        self.assertEqual(report.section_metrics["extras"]["service_recall"], 1.0)
        self.assertEqual(report.section_metrics["extras"]["limit_accuracy"], 1.0)
        self.assertEqual(report.field_recall, 1.0)

    def test_semantically_equivalent_strings_and_amounts_compare_equal(self) -> None:
        evaluator = ExtractionEvaluator()

        equivalent_pairs = [
            ("2 Month", "2 months"),
            ("NotCovered", "Not covered"),
            ("BackNeckSpine", "Back, neck and spine"),
            ("$1,200.00", 1200),
            ("AUD 500", 500.0),
        ]

        for extracted_value, gt_value in equivalent_pairs:
            with self.subTest(extracted=extracted_value, ground_truth=gt_value):
                self.assertTrue(evaluator._values_equal(extracted_value, gt_value))

    def test_category_aliases_match_in_flat_and_section_metrics(self) -> None:
        extracted = ExtractionResult(
            vertical="private_health",
            schema_version="test",
            source_path="example.pdf",
            data={
                "hospital": {
                    "clinical_categories": [
                        {"category": "Back, neck and spine", "coverage": "Not covered"},
                    ],
                },
            },
        )
        ground_truth = {
            "hospital": {
                "clinical_categories": [
                    {"category": "BackNeckSpine", "coverage": "NotCovered"},
                ],
            },
        }

        report = ExtractionEvaluator().evaluate(extracted, ground_truth)

        self.assertEqual(report.field_precision, 1.0)
        self.assertEqual(report.field_recall, 1.0)
        self.assertEqual(report.section_metrics["hospital"]["category_recall"], 1.0)
        self.assertEqual(report.section_metrics["hospital"]["coverage_accuracy"], 1.0)

    def test_section_metrics_separate_hospital_and_extras_value_accuracy(self) -> None:
        extracted = ExtractionResult(
            vertical="private_health",
            schema_version="test",
            source_path="example.pdf",
            data={
                "hospital": {
                    "product_name": "Example Cover",
                    "hospital_tier": "Silver",
                    "clinical_categories": [
                        {"category": "BackNeckSpine", "coverage": "Restricted"},
                    ],
                },
                "extras": {
                    "services": [
                        {
                            "service": "GeneralDental",
                            "covered": True,
                            "waiting_period": "2 Month",
                            "limit_per_person": 500.0,
                            "limit_per_policy": None,
                        },
                    ]
                },
            },
        )
        ground_truth = {
            "hospital": {
                "product_name": "Example Cover",
                "hospital_tier": "Silver",
                "clinical_categories": [
                    {"category": "BackNeckSpine", "coverage": "Covered"},
                ],
            },
            "extras": {
                "services": [
                    {
                        "service": "GeneralDental",
                        "covered": True,
                        "waiting_period": "2 Month",
                        "limit_per_person": 500.0,
                        "limit_per_policy": None,
                    },
                ]
            },
        }

        report = ExtractionEvaluator().evaluate(extracted, ground_truth)

        self.assertAlmostEqual(report.section_metrics["product"]["accuracy"], 1.0)
        self.assertAlmostEqual(report.section_metrics["hospital"]["category_recall"], 1.0)
        self.assertAlmostEqual(report.section_metrics["hospital"]["coverage_accuracy"], 0.0)
        self.assertAlmostEqual(report.section_metrics["extras"]["service_recall"], 1.0)
        self.assertAlmostEqual(report.section_metrics["extras"]["waiting_period_accuracy"], 1.0)
        self.assertAlmostEqual(report.section_metrics["extras"]["limit_accuracy"], 1.0)

    def test_aggregate_exposes_macro_micro_and_document_classes(self) -> None:
        evaluator = ExtractionEvaluator()
        complete = evaluator.evaluate(
            self._result({"extras": {"services": [{"service": "Optical", "covered": True}]}}),
            {"extras": {"services": [{"service": "Optical", "covered": True}]}},
        )
        partial = evaluator.evaluate(
            self._result({"extras": {"services": [{"service": "Optical", "covered": True}]}}),
            {
                "extras": {
                    "services": [
                        {"service": "Optical", "covered": True},
                        {"service": "Podiatry", "covered": True},
                    ]
                }
            },
        )

        summary = evaluator.aggregate([complete, partial])

        self.assertIn("macro", summary)
        self.assertIn("micro", summary)
        self.assertEqual(summary["document_classes"], {"complete_phis": 1, "partial": 1})
        self.assertAlmostEqual(summary["macro"]["field_recall"], 0.75)
        self.assertAlmostEqual(summary["micro"]["field_recall"], 2 / 3)
        self.assertEqual(summary["by_document_class"]["complete_phis"]["documents"], 1)

    def test_non_standard_surface_is_reported_with_evidence(self) -> None:
        report = ExtractionEvaluator().evaluate(
            self._result(
                {
                    "hospital": {
                        "clinical_categories": [
                            {"category": "Shared room in a public hospital", "coverage": "Covered"},
                            {"category": "Theatre fees", "coverage": "NotCovered"},
                        ]
                    }
                }
            ),
            {
                "hospital": {
                    "clinical_categories": [
                        {"category": "BackNeckSpine", "coverage": "Covered"},
                        {"category": "Blood", "coverage": "Covered"},
                    ]
                }
            },
        )

        self.assertEqual(report.phis_document_class, "non_standard")
        evidence = report.phis_classification["sections"]["hospital"]
        self.assertEqual(evidence["raw_item_count"], 2)
        self.assertEqual(evidence["recognized_item_count"], 0)


class GroundTruthProductMatchingTest(unittest.TestCase):
    @staticmethod
    def _store(products: list[dict[str, str]]) -> PrivateHealthGroundTruthStore:
        store = PrivateHealthGroundTruthStore.__new__(PrivateHealthGroundTruthStore)
        store.min_match_score = 0.0
        store.products = products
        store.variants_by_master = {row["ID Master"]: [] for row in products}
        return store

    @staticmethod
    def _row(
        id_master: str,
        name: str,
        *,
        pdf_path: str = "",
        tier: str = "Silver",
    ) -> dict[str, str]:
        return {
            "ID Master": id_master,
            "Name Master": name,
            "FundCode": "FND",
            "BrandCode": "FND",
            "ProductType": "Hospital",
            "HospitalTier": tier,
            "Pdf Filepath": pdf_path,
        }

    def test_name_containment_does_not_force_score_to_point_97(self) -> None:
        store = self._store([self._row("1", "Silver Hospital")])

        candidate = store.rank_pdf_candidates(
            "/tmp/PDFs/FND/hospital/Silver-Hospital-Plus.pdf"
        )[0]

        self.assertLess(candidate["match_evidence"]["name_similarity"], 0.97)
        self.assertFalse(candidate["match_evidence"]["exact_pdf_filepath"])

    def test_excess_disambiguates_otherwise_similar_products(self) -> None:
        store = self._store(
            [
                self._row("500", "Silver Hospital $500 excess"),
                self._row("750", "Silver Hospital $750 excess"),
            ]
        )

        candidates = store.rank_pdf_candidates(
            "/tmp/PDFs/FND/hospital/Silver-Hospital-750-excess.pdf"
        )

        self.assertEqual(candidates[0]["id_master"], "750")
        self.assertTrue(candidates[0]["match_evidence"]["excess_match"])
        self.assertFalse(candidates[1]["match_evidence"]["excess_match"])

    def test_exact_labelled_pdf_filepath_has_highest_priority(self) -> None:
        store = self._store(
            [
                self._row("name", "Target Product"),
                self._row(
                    "path",
                    "Different Legacy Name",
                    pdf_path="PDFs/FND/hospital/target-product.pdf",
                ),
            ]
        )

        candidates = store.rank_pdf_candidates(
            "/dataset/PDFs/FND/hospital/target-product.pdf"
        )

        self.assertEqual(candidates[0]["id_master"], "path")
        self.assertEqual(candidates[0]["score"], 1.0)
        self.assertTrue(candidates[0]["match_evidence"]["exact_pdf_filepath"])

    def test_product_item_id_disambiguates_same_named_variants(self) -> None:
        store = self._store(
            [
                self._row("old", "Silver Hospital"),
                self._row("current", "Silver Hospital"),
            ]
        )
        store.variants_by_master = {
            "old": ["PID-100"],
            "current": ["PID-200"],
        }

        candidates = store.rank_pdf_candidates(
            "/tmp/PDFs/FND/hospital/Silver-Hospital-PID-200.pdf"
        )

        self.assertEqual(candidates[0]["id_master"], "current")
        self.assertTrue(candidates[0]["match_evidence"]["variant_match"])
        self.assertFalse(candidates[1]["match_evidence"]["variant_match"])

if __name__ == "__main__":
    unittest.main()
