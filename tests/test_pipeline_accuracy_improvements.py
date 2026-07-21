from __future__ import annotations

import unittest

from src.evaluation.metrics import ExtractionEvaluator
from src.models import ExtractionInput, ExtractionResult, ParsedDocument, SchemaSection, VerticalSchema
from src.pipeline.extractor import LLMExtractor


def private_health_schema() -> VerticalSchema:
    return VerticalSchema(
        vertical="private_health",
        version="test",
        hospital=SchemaSection(
            canonical_categories=["BackNeckSpine"],
            aliases={"BackNeckSpine": ["back, neck and spine"]},
        ),
        extras=SchemaSection(
            canonical_services=["GeneralDental", "MajorDental"],
            aliases={
                "GeneralDental": ["general dental"],
                "MajorDental": ["major dental"],
            },
        ),
    )


class EvaluationMetricTest(unittest.TestCase):
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


class ExtractorContextTest(unittest.TestCase):
    def test_user_prompt_keeps_relevant_late_pages_instead_of_hard_prefix_only(self) -> None:
        schema = private_health_schema()
        extractor = LLMExtractor(schema=schema, client=None, provider="none")
        first_page = "Welcome\n" + ("introductory text " * 1200)
        late_page = "Major dental waiting period 12 months annual limit $800 per person"
        parsed = ParsedDocument(
            path="PDFs/FND/extras/example.pdf",
            pages=3,
            tables=[],
            full_text=f"{first_page}\n{late_page}",
            text_by_page=[first_page, "middle page", late_page],
            has_tables=False,
            table_coverage=0.0,
        )
        prompt = extractor._build_user_prompt(
            ExtractionInput(mode="text_only", text=parsed.full_text, source_document=parsed)
        )

        self.assertIn("Major dental waiting period", prompt)

    def test_extras_heuristic_reads_waiting_period_and_limits_from_tables(self) -> None:
        schema = private_health_schema()
        extractor = LLMExtractor(schema=schema, client=None, provider="none")
        parsed = ParsedDocument(
            path="PDFs/FND/extras/example.pdf",
            pages=1,
            tables=[
                [
                    ["Service", "Waiting period", "Annual limit"],
                    ["General dental", "2 months", "$500 per person"],
                    ["Major dental", "12 months", "$1,000 per membership"],
                ]
            ],
            full_text="Extras product guide",
            text_by_page=["Extras product guide"],
            has_tables=True,
            table_coverage=0.8,
        )

        result = extractor.extract(
            ExtractionInput(
                mode="table_assisted",
                text=parsed.full_text,
                tables=parsed.tables,
                source_document=parsed,
            )
        )

        services = {
            item["service"]: item
            for item in result.data["extras"]["services"]
            if item["covered"]
        }
        self.assertEqual(services["GeneralDental"]["waiting_period"], "2 Month")
        self.assertEqual(services["GeneralDental"]["limit_per_person"], 500.0)
        self.assertEqual(services["MajorDental"]["waiting_period"], "12 Month")
        self.assertEqual(services["MajorDental"]["limit_per_policy"], 1000.0)


if __name__ == "__main__":
    unittest.main()
