from __future__ import annotations

import unittest

from src.PDFingestor.models import PageRepresentation, ParsedPDF, TableBlock, TextBlock
from src.evaluation.document_classifier import PhisDocumentClassifier
from src.evaluation.metrics import ExtractionEvaluator, PrivateHealthGroundTruthStore
from src.evaluation.taxonomy import canonical_extras_services, canonical_product_name
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

    def test_product_fields_survive_without_category_or_service_lists(self) -> None:
        report = ExtractionEvaluator().evaluate(
            self._result({
                "product_name": "Basic Hospital Plus Elevate",
                "tier": "Basic Plus",
                "clinical_categories": None,
            }),
            {
                "hospital": {
                    "product_name": "Basic Hospital Plus Elevate",
                    "hospital_tier": "BasicPlus",
                }
            },
        )

        self.assertEqual(report.section_metrics["product"]["accuracy"], 1.0)
        self.assertEqual(report.section_metrics["product"]["presence_recall"], 1.0)
        self.assertEqual(report.field_recall, 1.0)

    def test_partial_surface_unscored_fields_do_not_reduce_precision(self) -> None:
        report = ExtractionEvaluator().evaluate(
            self._result({
                "extras": {
                    "services": [
                        {"service": "Optical", "covered": True, "waiting_period": "6 Month"},
                        {"service": "Physiotherapy", "covered": True},
                    ]
                }
            }),
            {
                "extras": {
                    "services": [
                        {"service": "Optical", "covered": True, "waiting_period": "6 Month"},
                        {"service": "Physiotherapy", "covered": True},
                    ]
                }
            },
            document_classification={
                "classification": "partial",
                "source_only": True,
                "sections": {
                    "extras": {
                        "classification": "partial",
                        "visible_canonical_items": ["Optical"],
                        "visible_fields": {"Optical": ["service", "covered"]},
                    }
                },
            },
        )

        self.assertEqual(report.field_precision, 1.0)
        self.assertEqual(report.section_metrics["extras"]["waiting_period_comparable"], 0)
        self.assertTrue(any("waiting_period" in key for key in report.unscored_extracted_fields))
        self.assertTrue(any("physiotherapy" in key for key in report.unscored_extracted_fields))

    def test_claim_evidence_reports_source_scoped_contradiction(self) -> None:
        document = ParsedPDF(
            pdf_id="pdf",
            pdf_hash="hash",
            source_path="example.pdf",
            pages=[PageRepresentation(
                page_num=1,
                width=100,
                height=100,
                blocks=[TextBlock(
                    block_id="p1-b1",
                    content="Example Gold Hospital",
                    top=0,
                )],
            )],
        )
        report = ExtractionEvaluator().evaluate(
            self._result({"product_name": "Wrong Hospital"}),
            {"hospital": {"product_name": "Example Gold Hospital"}},
            source_documents=[document],
        )

        self.assertTrue(report.hallucination_evaluated)
        self.assertEqual(report.contradicted_claims, 1)
        self.assertEqual(report.hallucination_rate, 1.0)

    def test_uncovered_services_and_empty_shared_limits_are_not_recall_targets(self) -> None:
        report = ExtractionEvaluator().evaluate(
            self._result({
                "extras": {
                    "services": [
                        {"service": "GeneralDental", "covered": True},
                    ],
                },
            }),
            {
                "extras": {
                    "services": [
                        {
                            "service": "GeneralDental",
                            "covered": True,
                            "shared_with": [],
                        },
                        {
                            "service": "Vaccinations",
                            "covered": False,
                            "shared_with": [],
                        },
                    ],
                },
            },
        )

        self.assertEqual(report.field_recall, 1.0)
        self.assertEqual(report.ground_truth_fields, 1)
        self.assertEqual(report.section_metrics["extras"]["ground_truth_services"], 1)
        self.assertEqual(report.section_metrics["extras"]["service_recall"], 1.0)

    def test_unknown_extras_label_is_not_preserved_as_a_service(self) -> None:
        self.assertEqual(canonical_extras_services("Per visit benefit"), [])
        self.assertEqual(canonical_extras_services("Invented service"), [])
        self.assertEqual(canonical_extras_services("Glasses - Frames"), ["Optical"])
        self.assertEqual(canonical_extras_services("Major Dental - Surgery"), ["DentalMajor"])
        self.assertEqual(
            canonical_extras_services("Chiro/Osteo X-rays"),
            ["Chiropractic", "Osteopathy"],
        )
        self.assertEqual(canonical_extras_services("Root canal"), ["Endodontic"])
        self.assertEqual(
            canonical_extras_services("Dental - root canal treatment"),
            ["Endodontic"],
        )

    def test_product_name_comparison_separates_brand_and_variant_decorators(self) -> None:
        equivalent_pairs = [
            ("Basic Extras", "Basic Extras BSE"),
            ("Frank Lots Extras 80% Back", "Lots Extras 80"),
            ("GMHBA Bronze Plus Advantage Hospital $750", "Bronze Plus Advantage Hospital"),
            ("Core Families Extras", "Core Family Extras"),
            ("Priceline Premium Extras", "TAL Premium Extras"),
            ("Top Hospital 500 Gold", "Top Hospital Gold"),
        ]

        for left, right in equivalent_pairs:
            with self.subTest(left=left, right=right):
                self.assertEqual(canonical_product_name(left), canonical_product_name(right))

        self.assertNotEqual(
            canonical_product_name("Basic Plus Hospital"),
            canonical_product_name("Gold Hospital"),
        )

    def test_product_aliases_are_used_by_flat_and_section_metrics(self) -> None:
        report = ExtractionEvaluator().evaluate(
            self._result({"product_name": "Frank Some Extras 80% Back"}),
            {"extras": {"product_name": "Some Extras 80"}},
        )

        self.assertEqual(report.field_recall, 1.0)
        self.assertEqual(report.section_metrics["product"]["accuracy"], 1.0)

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
            document_classification={
                "classification": "partial",
                "source_only": True,
                "sections": {
                    "hospital": {
                        "classification": "partial",
                        "visible_canonical_items": [],
                    }
                },
            },
        )

        self.assertEqual(report.field_recall, 1.0)
        self.assertEqual(report.ground_truth_fields, 1)
        self.assertEqual(
            report.section_metrics["hospital"]["non_comparable_reason"],
            "no_source_visible_canonical_items",
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

    def test_current_final_schema_names_are_adapted_to_ground_truth(self) -> None:
        extracted = self._result({
            "product_name": "Example Bronze Plus",
            "tier": "Bronze Plus",
            "clinical_categories": [
                {"category": "Back, neck and spine", "coverage": "included", "notes": None},
            ],
            "extras_benefits": [
                {
                    "service_name": "physiotherapy",
                    "annual_limit_amount_aud": None,
                    "shared_limit_group": "therapies",
                },
                {
                    "service_name": "chiropractic",
                    "annual_limit_amount_aud": None,
                    "shared_limit_group": "therapies",
                },
            ],
            "extras_waiting_periods": [
                {"service_name": "physiotherapy", "wait_months": 2, "wait_days": None},
            ],
            "extras_shared_limits": [
                {
                    "shared_limit_group": "therapies",
                    "amount_aud": 500,
                    "limit_scope": "per_person",
                },
            ],
        })
        ground_truth = {
            "hospital": {
                "product_name": "Example Bronze Plus",
                "hospital_tier": "BronzePlus",
                "clinical_categories": [
                    {"category": "BackNeckSpine", "coverage": "Covered"},
                ],
            },
            "extras": {
                "product_name": "Example Bronze Plus",
                "services": [
                    {
                        "service": "Physiotherapy", "covered": True,
                        "waiting_period": "2 Month", "limit_per_person": 500,
                        "shared_with": ["Chiropractic"],
                    },
                    {
                        "service": "Chiropractic", "covered": True,
                        "limit_per_person": 500,
                        "shared_with": ["Physiotherapy"],
                    },
                ],
            },
        }

        report = ExtractionEvaluator().evaluate(extracted, ground_truth)

        self.assertEqual(report.field_recall, 1.0)
        self.assertEqual(report.section_metrics["hospital"]["coverage_accuracy"], 1.0)
        self.assertEqual(report.section_metrics["extras"]["waiting_period_accuracy"], 1.0)
        self.assertEqual(report.section_metrics["extras"]["limit_accuracy"], 1.0)

    def test_current_schema_preserves_independent_and_dual_shared_limit_scopes(self) -> None:
        report = ExtractionEvaluator().evaluate(
            self._result({
                "extras_benefits": [
                    {
                        "service_name": "optical",
                        "annual_limit_amount_aud": 250,
                        "annual_limit_scope": "per_membership",
                    },
                    {
                        "service_name": "physiotherapy",
                        "annual_limit_amount_aud": None,
                        "shared_limit_group": "therapies",
                    },
                    {
                        "service_name": "chiropractic",
                        "annual_limit_amount_aud": None,
                        "shared_limit_group": "therapies",
                    },
                ],
                "extras_shared_limits": [
                    {
                        "shared_limit_group": "therapies",
                        "amount_aud": 400,
                        "limit_scope": "per_person",
                    },
                    {
                        "shared_limit_group": "therapies",
                        "amount_aud": 800,
                        "limit_scope": "per_membership",
                    },
                ],
            }),
            {
                "extras": {
                    "services": [
                        {"service": "Optical", "covered": True, "limit_per_policy": 250},
                        {
                            "service": "Physiotherapy",
                            "covered": True,
                            "limit_per_person": 400,
                            "limit_per_policy": 800,
                            "shared_with": ["Chiropractic"],
                        },
                        {
                            "service": "Chiropractic",
                            "covered": True,
                            "limit_per_person": 400,
                            "limit_per_policy": 800,
                            "shared_with": ["Physiotherapy"],
                        },
                    ],
                },
            },
        )

        self.assertEqual(report.field_recall, 1.0)
        self.assertEqual(report.section_metrics["extras"]["limit_accuracy"], 1.0)

    def test_explicit_zero_month_extras_wait_is_preserved(self) -> None:
        report = ExtractionEvaluator().evaluate(
            self._result({
                "extras_benefits": [{"service_name": "general_dental"}],
                "extras_waiting_periods": [
                    {"service_name": "general_dental", "wait_months": 0},
                ],
            }),
            {
                "extras": {
                    "services": [
                        {
                            "service": "DentalGeneral",
                            "covered": True,
                            "waiting_period": "0 Month",
                        },
                    ],
                },
            },
        )

        self.assertEqual(report.field_recall, 1.0)
        self.assertEqual(report.section_metrics["extras"]["waiting_period_accuracy"], 1.0)

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

    def test_adapter_maps_notes_none_to_zero_waiting_period(self) -> None:
        report = ExtractionEvaluator().evaluate(
            self._result({
                "extras_benefits": [{"service_name": "Optical"}],
                "waiting_periods": [{"service_name": "Optical", "notes": "None"}],
            }),
            {"extras": {"services": [{
                "service": "Optical", "covered": True, "waiting_period": "0 Month",
            }]}},
        )

        self.assertEqual(report.section_metrics["extras"]["waiting_period_accuracy"], 1.0)

    def test_adapter_maps_unlimited_annual_limit_to_legacy_zero(self) -> None:
        report = ExtractionEvaluator().evaluate(
            self._result({"extras_benefits": [{
                "service_name": "Optical", "annual_limit_type": "unlimited",
                "annual_limit_amount_aud": None,
            }]}),
            {"extras": {"services": [{
                "service": "Optical", "covered": True, "limit_per_person": 0,
            }]}},
        )

        self.assertEqual(report.section_metrics["extras"]["limit_accuracy"], 1.0)

    def test_adapter_recovers_tiered_annual_limits_from_notes(self) -> None:
        report = ExtractionEvaluator().evaluate(
            self._result({"extras_benefits": [{
                "service_name": "Physiotherapy",
                "notes": "Years insured: <5 yrs $300; 5+ yrs $400; 10+ yrs $500",
            }]}),
            {"extras": {"services": [{
                "service": "Physiotherapy", "covered": True, "limit_per_person": 300,
            }]}},
        )

        self.assertEqual(report.section_metrics["extras"]["limit_accuracy"], 1.0)

    def test_shared_total_is_not_compared_with_individual_limit(self) -> None:
        report = ExtractionEvaluator().evaluate(
            self._result({
                "extras_benefits": [
                    {
                        "service_name": "Physiotherapy",
                        "shared_limit_group": "therapies",
                    },
                    {
                        "service_name": "Chiropractic",
                        "shared_limit_group": "therapies",
                    },
                ],
                "extras_shared_limits": [{
                    "shared_limit_group": "therapies",
                    "amount_aud": 1000,
                    "limit_scope": "per_person",
                }],
            }),
            {"extras": {"services": [
                {
                    "service": "Physiotherapy",
                    "covered": True,
                    "limit_per_person": 500,
                },
                {
                    "service": "Chiropractic",
                    "covered": True,
                    "limit_per_person": 400,
                },
            ]}},
        )

        self.assertEqual(report.section_metrics["extras"]["limit_comparable"], 0)
        self.assertFalse(any("limit_amount" in key for key in report.incorrect_fields))

    def test_multiple_independent_limit_scopes_require_the_same_amount_set(self) -> None:
        evaluator = ExtractionEvaluator()

        self.assertTrue(evaluator._limit_values_equal([400, 800], [800, 400]))
        self.assertTrue(evaluator._limit_values_equal([400, 800], 400))
        self.assertFalse(evaluator._limit_values_equal([400, 800], [400, 900]))

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
            document_classification={
                "classification": "partial",
                "source_only": True,
                "sections": {
                    "extras": {
                        "classification": "partial",
                        "visible_canonical_items": ["Optical"],
                    }
                },
            },
        )

        summary = evaluator.aggregate([complete, partial])

        self.assertIn("macro", summary)
        self.assertIn("micro", summary)
        self.assertEqual(summary["document_classes"], {"complete_phis": 1, "partial": 1})
        self.assertAlmostEqual(summary["macro"]["field_recall"], 1.0)
        self.assertAlmostEqual(summary["micro"]["field_recall"], 1.0)
        self.assertEqual(summary["by_document_class"]["complete_phis"]["documents"], 1)

    def test_aggregate_excludes_duplicate_pdf_content(self) -> None:
        evaluator = ExtractionEvaluator()
        report = evaluator.evaluate(
            self._result({"extras": {"services": [{"service": "Optical", "covered": True}]}}),
            {"extras": {"services": [{"service": "Optical", "covered": True}]}},
        ).model_copy(update={"source_sha256": "same-pdf-content"})
        duplicate = report.model_copy(update={"source_path": "copied-name.pdf"})

        summary = evaluator.aggregate([report, duplicate], total_documents=2)

        self.assertEqual(summary["matched_documents"], 1)
        self.assertEqual(summary["total_documents"], 1)
        self.assertEqual(summary["duplicate_source_documents"], 1)

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
            document_classification={
                "classification": "non_standard",
                "source_only": True,
                "sections": {
                    "hospital": {
                        "classification": "non_standard",
                        "raw_item_count": 2,
                        "recognized_item_count": 0,
                        "visible_canonical_items": [],
                    }
                },
            },
        )

        self.assertEqual(report.phis_document_class, "non_standard")
        evidence = report.phis_classification["sections"]["hospital"]
        self.assertEqual(evidence["raw_item_count"], 2)
        self.assertEqual(evidence["recognized_item_count"], 0)

    def test_classifier_marks_only_nonempty_waiting_and_limit_cells_visible(self) -> None:
        document = ParsedPDF(
            pdf_id="pdf",
            pdf_hash="hash",
            source_path="extras.pdf",
            pages=[PageRepresentation(
                page_num=1,
                width=100,
                height=100,
                blocks=[TableBlock(
                    block_id="p1-t1",
                    table_id="t1",
                    markdown="",
                    headers=["Service", "Waiting Period", "Annual Limit"],
                    raw_rows=[
                        ["Optical", "", "$250"],
                        ["Physiotherapy", "2 months", "$500"],
                    ],
                    bbox=(0, 0, 100, 100),
                    top=0,
                )],
            )],
        )
        classification = PhisDocumentClassifier(
            manifest_path=None
        ).classify_documents(
            [document],
            {
                "extras": {
                    "services": [
                        {"service": "Optical", "covered": True},
                        {"service": "Physiotherapy", "covered": True},
                    ]
                }
            },
        )
        visible = classification["sections"]["extras"]["visible_fields"]

        self.assertNotIn("waiting_period", visible["Optical"])
        self.assertIn("limit_amount", visible["Optical"])
        self.assertIn("waiting_period", visible["Physiotherapy"])


class GroundTruthProductMatchingTest(unittest.TestCase):
    @staticmethod
    def _store(products: list[dict[str, str]]) -> PrivateHealthGroundTruthStore:
        store = PrivateHealthGroundTruthStore.__new__(PrivateHealthGroundTruthStore)
        store.min_match_score = 0.0
        store.low_confidence_threshold = 0.85
        store.products = products
        store.products_by_master = {row["ID Master"]: row for row in products}
        store.variants_by_master = {row["ID Master"]: [] for row in products}
        store.hospital_by_master = {}
        store.extras_by_master = {}
        store.limit_groups_by_product_item = {}
        store.variant_rows = []
        return store

    @staticmethod
    def _row(
        id_master: str,
        name: str,
        *,
        pdf_path: str = "",
        tier: str = "Silver",
        product_type: str = "Hospital",
    ) -> dict[str, str]:
        return {
            "ID Master": id_master,
            "Name Master": name,
            "FundCode": "FND",
            "BrandCode": "FND",
            "ProductType": product_type,
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

    def test_random_sample_path_without_pdfs_still_uses_fund_directory(self) -> None:
        ahm = self._row("ahm", "Super Extras")
        ahm["FundCode"] = "AHM"
        ahm["BrandCode"] = "AHM"
        ahm["ProductType"] = "GeneralHealth"
        mbp = self._row("mbp", "Super Extras")
        mbp["FundCode"] = "MBP"
        mbp["BrandCode"] = "MBP"
        mbp["ProductType"] = "GeneralHealth"
        store = self._store([ahm, mbp])

        candidates = store.rank_pdf_candidates(
            "/tmp/private-health-random-50.abc/AHM/extras/super-extras.pdf"
        )

        self.assertEqual([item["id_master"] for item in candidates], ["ahm"])

    def test_random_root_and_pdfs_prefix_still_exact_match(self) -> None:
        store = self._store([
            self._row(
                "exact", "Legacy Name",
                pdf_path="latest-pdfs/FND/combined/family-package.pdf",
            ),
        ])

        candidate = store.rank_pdf_candidates(
            "/tmp/random/FND/combined/family-package.pdf"
        )[0]

        self.assertEqual(candidate["id_master"], "exact")
        self.assertTrue(candidate["match_evidence"]["exact_pdf_filepath"])

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
        self.assertGreaterEqual(candidates[0]["score"], 0.85)
        self.assertTrue(candidates[0]["match_evidence"]["exact_pdf_filepath"])
        self.assertTrue(candidates[0]["match_evidence"]["strict_pdf_filepath"])

    def test_strict_path_retains_year_below_fund_directory(self) -> None:
        store = self._store([
            self._row(
                "current",
                "Silver Hospital",
                pdf_path="pdfs/FND/2025/08/hospital/silver-hospital.pdf",
            )
        ])

        candidate = store.rank_pdf_candidates(
            "/dataset/PDFs/FND/2025/07/hospital/silver-hospital.pdf"
        )[0]

        self.assertTrue(candidate["match_evidence"]["exact_pdf_filepath"])
        self.assertFalse(candidate["match_evidence"]["strict_pdf_filepath"])

    def test_fuzzy_runner_up_does_not_make_exact_path_match_ambiguous(self) -> None:
        store = self._store([
            self._row(
                "exact",
                "Choosable Teeth Wellbeing Muscle Bone",
                pdf_path="PDFs/FND/hospital/choosable-teeth-wellbeing-muscle-bone.pdf",
            ),
            self._row(
                "fuzzy",
                "Choosable Teeth Wellbeing Muscle",
            ),
        ])

        match = store.match_pdf(
            "/dataset/PDFs/FND/hospital/choosable-teeth-wellbeing-muscle-bone.pdf"
        )

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.id_master, "exact")
        self.assertFalse(match.ambiguous_match)
        self.assertFalse(match.low_confidence_match)

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

    def test_variant_disambiguates_rows_sharing_an_exact_filepath(self) -> None:
        shared_path = "PDFs/FND/hospital/silver-hospital-PID-200.pdf"
        store = self._store([
            self._row("old", "Silver Hospital", pdf_path=shared_path),
            self._row("current", "Silver Hospital", pdf_path=shared_path),
        ])
        store.variants_by_master = {"old": ["PID-100"], "current": ["PID-200"]}

        candidates = store.rank_pdf_candidates(f"/dataset/{shared_path}")

        self.assertEqual(candidates[0]["id_master"], "current")
        self.assertTrue(candidates[0]["match_evidence"]["exact_pdf_filepath"])
        self.assertTrue(candidates[0]["match_evidence"]["variant_match"])
        self.assertFalse(candidates[1]["match_evidence"]["variant_match"])

    def test_category_words_inside_combined_filename_are_not_fake_path_parts(self) -> None:
        row = self._row(
            "extras", "HCF Mid Extras",
            pdf_path="PDFs/FND/extras/HCF-Mid-Extras.pdf",
            product_type="GeneralHealth",
        )
        store = self._store([row])

        candidate = store.rank_pdf_candidates(
            "/tmp/PDFs/FND/combined/HCF-Hospital-Gold-and-HCF-Mid-Extras.pdf"
        )[0]

        self.assertFalse(candidate["match_evidence"]["exact_pdf_filepath"])

    def test_combined_pdf_can_merge_separate_hospital_and_extras_ground_truth(self) -> None:
        hospital = self._row("h", "HCF Hospital Optimal Gold", tier="Gold")
        extras = self._row(
            "e", "HCF Mid Extras", product_type="GeneralHealth", tier="",
        )
        store = self._store([hospital, extras])
        store.hospital_by_master = {
            "h": [{"Title": "Blood", "Cover": "Covered"}],
        }
        store.extras_by_master = {
            "e": [{
                "Title": "Optical", "Covered": "true", "WaitingPeriod": "",
                "WaitingPeriodUnit": "", "LimitPerPerson": "200",
                "LimitPerPolicy": "",
            }],
        }

        match, ground_truth = store.load_ground_truth(
            "/tmp/PDFs/FND/combined/HCF-Hospital-Optimal-Gold-and-HCF-Mid-Extras.pdf"
        )

        self.assertIsNotNone(match)
        assert match is not None
        self.assertTrue(match.composite_match)
        self.assertEqual(match.component_id_masters, ["h", "e"])
        self.assertIn("hospital", ground_truth)
        self.assertIn("extras", ground_truth)
        self.assertNotIn("product_name", ground_truth["hospital"])

if __name__ == "__main__":
    unittest.main()
