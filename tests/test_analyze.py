from __future__ import annotations

import unittest

from src.extract.analyze import FieldSpec, analyze, build_feedback


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


if __name__ == "__main__":
    unittest.main()
