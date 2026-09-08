from __future__ import annotations

import unittest

from src.evaluation.metrics import ExtractionEvaluator
from src.models import ExtractionResult


class EvaluationMetricTest(unittest.TestCase):
    def test_dynamic_field_named_like_section_does_not_crash_metrics(self) -> None:
        extracted = ExtractionResult(vertical="private_health", schema_version="test",
                                     source_path="example.pdf",
                                     data={"hospital": "included", "extras": []})
        report = ExtractionEvaluator().evaluate(extracted, {
            "hospital": {"product_name": "Example"}})
        self.assertEqual(report.matched_fields, 0)
        self.assertEqual(report.section_metrics["hospital"]["matched_categories"], 0)

    def test_flat_discovered_fields_match_labelled_sections_without_aliases(self) -> None:
        data = {"product_name": "Example", "hospital_tier": "Silver",
                "product_type": "hospital", "_unfilled": [], "_notes": None}
        extracted = ExtractionResult(vertical="private_health", schema_version="test",
                                     source_path="hospital/example.pdf", data=data)
        report = ExtractionEvaluator().evaluate(extracted, {
            "hospital": {"product_name": "Example", "hospital_tier": "Silver"}})
        self.assertEqual(report.matched_fields, 2)
        self.assertEqual(report.hallucination_rate, 0)
        self.assertEqual(report.section_metrics["product"]["accuracy"], 1)
        self.assertEqual(extracted.data, data)

    def test_coverage_counts_ground_truth_presence_not_extra_hallucinations(self) -> None:
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
        self.assertAlmostEqual(report.hallucination_rate, 0.5)
        self.assertEqual(report.hallucinations_by_section, {"extras": 1})
        self.assertAlmostEqual(report.section_metrics["extras"]["service_precision"], 0.5)
        self.assertAlmostEqual(report.section_metrics["extras"]["service_recall"], 0.5)

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

if __name__ == "__main__":
    unittest.main()
