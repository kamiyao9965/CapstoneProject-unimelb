from __future__ import annotations

import unittest

from src.extract.analyze import FieldSpec, analyze, build_feedback, load_field_specs


class AllExtractionFailuresTest(unittest.TestCase):
    def test_all_failures_do_not_mark_optional_fields_as_weak(self) -> None:
        analysis = analyze(
            [{"_error": "timeout"}, {"_parse_error": "invalid JSON"}],
            [FieldSpec(name="annual_limit")],
        )

        self.assertEqual(analysis.documents, 0)
        self.assertEqual(analysis.error_docs, 2)
        self.assertEqual(analysis.weak_fields, [])

    def test_all_failures_stop_schema_refinement_feedback(self) -> None:
        analysis = analyze(
            [{"_error": "timeout"}],
            [FieldSpec(name="product_name", required=True)],
        )

        feedback = build_feedback(analysis)

        self.assertIn("No documents were successfully extracted", feedback)
        self.assertIn("before refining the schema", feedback)
        self.assertNotIn("No systematic extraction failures", feedback)


class ProductApplicabilityTest(unittest.TestCase):
    def test_loads_field_product_applicability(self) -> None:
        specs = load_field_specs(
            """
vertical: private_health
version: 0.1-draft
product_types: [hospital, combined]
fields:
  - name: hospital_excess
    type: number
    description: Hospital excess
    applies_to: [hospital, combined]
    required: false
    values: []
"""
        )

        self.assertEqual(specs[0].applies_to, ("hospital", "combined"))

    def test_load_field_specs_rejects_invalid_schema(self) -> None:
        with self.assertRaisesRegex(ValueError, "valid YAML"):
            load_field_specs("fields: [")

    def test_fill_rate_uses_only_applicable_product_documents(self) -> None:
        analysis = analyze(
            [
                {"product_type": "hospital", "hospital_excess": 500},
                {"product_type": "extras", "annual_limit": None},
            ],
            [
                FieldSpec("hospital_excess", applies_to=("hospital", "combined")),
                FieldSpec("annual_limit", applies_to=("extras", "combined")),
            ],
        )

        self.assertEqual(analysis.fill_rate["hospital_excess"], 1.0)
        self.assertEqual(analysis.fill_rate["annual_limit"], 0.0)
        self.assertEqual(analysis.evaluated_documents["hospital_excess"], 1)
        self.assertEqual(analysis.evaluated_documents["annual_limit"], 1)

    def test_unclassified_records_are_not_used_as_evaluation_denominators(self) -> None:
        analysis = analyze(
            [{"product_name": "Unknown product"}],
            [FieldSpec("product_name", required=True, applies_to=("hospital",))],
        )

        self.assertEqual(analysis.documents, 0)
        self.assertEqual(analysis.unclassified_docs, 1)
        self.assertEqual(analysis.missing_required, {})


if __name__ == "__main__":
    unittest.main()
