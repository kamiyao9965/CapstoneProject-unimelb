from __future__ import annotations

import io
import unittest
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from src.common.json_artifacts import (
    build_failure_artifact,
    build_success_artifact,
    write_artifact,
)
from src.schema_application.analyze import (
    ExtractionRecord,
    FieldSpec,
    analyze,
    build_feedback,
    build_feedback_data,
    load_field_specs,
    load_records,
    print_report,
)
from src.schema.contract import compile_extraction_contract
from tests.test_json_contracts import VALID_DISCOVERED_SCHEMA


class AllExtractionFailuresTest(unittest.TestCase):
    def test_failed_artifact_is_counted_but_never_returned_as_record(self) -> None:
        provenance = {
            "run_id": "test", "provider": "openai", "model": "gpt-5",
            "document_input": "pdf",
            "source_documents": ["data/FUND/hospital/product.pdf"],
            "source_artifacts": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            success = build_success_artifact(
                artifact_type="extraction_result", contract_version="1.0.0",
                data={"product_type": "hospital"}, provenance=provenance,
                data_contract_schema={"type": "object"},
            )
            failure = build_failure_artifact(
                artifact_type="extraction_error", contract_version="1.0.0",
                provenance=provenance, error_code="failed", message="failed",
            )
            write_artifact(root / "success.json", success, data_contract_schema={"type": "object"})
            error_path = root / "errors" / "extraction" / "failure.json"
            error_path.parent.mkdir(parents=True)
            write_artifact(error_path, failure)

            records, failures = load_records(root, {"type": "object"})

            self.assertEqual(
                records,
                [
                    ExtractionRecord(
                        data={"product_type": "hospital"},
                        source_document="data/FUND/hospital/product.pdf",
                        source_category="hospital",
                    )
                ],
            )
            self.assertEqual(failures, 1)

    def test_success_artifact_without_authoritative_source_category_is_a_failure(self) -> None:
        provenance = {
            "run_id": "test", "provider": "openai", "model": "gpt-5",
            "document_input": "pdf",
            "source_documents": ["data/FUND/unknown/product.pdf"],
            "source_artifacts": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = build_success_artifact(
                artifact_type="extraction_result", contract_version="1.0.0",
                data={"product_type": "hospital"}, provenance=provenance,
                data_contract_schema={"type": "object"},
            )
            write_artifact(
                root / "unlabelled.json",
                artifact,
                data_contract_schema={"type": "object"},
            )

            records, failures = load_records(root, {"type": "object"})

            self.assertEqual(records, [])
            self.assertEqual(failures, 1)

    def test_success_artifact_that_violates_runtime_contract_is_a_failure(self) -> None:
        provenance = {
            "run_id": "test", "provider": "openai", "model": "gpt-5",
            "document_input": "pdf", "source_documents": [], "source_artifacts": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = build_success_artifact(
                artifact_type="extraction_result", contract_version="1.0.0",
                data={"product_name": "Missing classification"},
                provenance=provenance,
                data_contract_schema={"type": "object"},
            )
            write_artifact(
                root / "invalid.json", artifact,
                data_contract_schema={"type": "object"},
            )

            records, failures = load_records(
                root,
                compile_extraction_contract(VALID_DISCOVERED_SCHEMA),
            )

            self.assertEqual(records, [])
            self.assertEqual(failures, 1)

    def test_all_failures_do_not_mark_optional_fields_as_weak(self) -> None:
        analysis = analyze(
            [],
            [FieldSpec(name="annual_limit")],
            failed_artifacts=2,
        )

        self.assertEqual(analysis.documents, 0)
        self.assertEqual(analysis.error_docs, 2)
        self.assertEqual(analysis.weak_fields, [])

    def test_all_failures_stop_schema_refinement_feedback(self) -> None:
        analysis = analyze(
            [],
            [FieldSpec(name="product_name", required=True)],
            failed_artifacts=1,
        )

        feedback = build_feedback(analysis)

        self.assertIn("No documents were successfully extracted", feedback)
        self.assertIn("before refining the schema", feedback)
        self.assertNotIn("No systematic extraction failures", feedback)

    def test_feedback_data_is_built_from_structured_signals(self) -> None:
        analysis = analyze(
            [
                ExtractionRecord(
                    data={"product_type": "hospital", "cover_status": "unknown"},
                    source_document="data/FUND/hospital/product.pdf",
                    source_category="hospital",
                )
            ],
            [
                FieldSpec(
                    "cover_status", type="enum", values=["included"],
                    applies_to=("hospital",),
                )
            ],
        )

        data = build_feedback_data(analysis)

        self.assertEqual(data["analysis"]["enum_violations"], {"cover_status": ["unknown"]})
        self.assertEqual(len(data["instructions"]), 1)
        self.assertIn("cover_status", data["instructions"][0])


class ProductApplicabilityTest(unittest.TestCase):
    def test_loads_field_product_applicability(self) -> None:
        schema = dict(VALID_DISCOVERED_SCHEMA)
        schema["product_types"] = ["hospital", "combined"]
        product_type = dict(VALID_DISCOVERED_SCHEMA["fields"][0])
        product_type["applies_to"] = ["hospital", "combined"]
        product_type["values"] = ["hospital", "combined"]
        schema["fields"] = [product_type, {
            "name": "hospital_excess", "type": "number",
            "description": "Hospital excess", "applies_to": ["hospital", "combined"],
            "required": False, "values": [],
            "aliases": [],
        }]
        specs = load_field_specs(schema)
        hospital_excess = next(spec for spec in specs if spec.name == "hospital_excess")

        self.assertEqual(hospital_excess.applies_to, ("hospital", "combined"))

    def test_load_field_specs_rejects_invalid_schema(self) -> None:
        with self.assertRaises(ValueError):
            load_field_specs({"fields": []})

    def test_load_field_specs_preserves_scalar_enum_values(self) -> None:
        schema = dict(VALID_DISCOVERED_SCHEMA)
        schema["fields"] = [*VALID_DISCOVERED_SCHEMA["fields"], {
            "name": "numeric_tier", "type": "enum", "description": "Tier",
            "applies_to": list(schema["product_types"]), "required": False,
            "values": [1, 2, True], "aliases": [],
        }]

        specs = load_field_specs(schema)
        numeric_tier = next(spec for spec in specs if spec.name == "numeric_tier")

        self.assertEqual(numeric_tier.values, [1, 2, True])

    def test_fill_rate_uses_source_category_not_model_classification(self) -> None:
        analysis = analyze(
            [
                ExtractionRecord(
                    data={
                        "product_type": "extras",
                        "hospital_excess": 500,
                        "annual_limit": None,
                    },
                    source_document="data/FUND/hospital/hospital.pdf",
                    source_category="hospital",
                ),
                ExtractionRecord(
                    data={
                        "product_type": "hospital",
                        "hospital_excess": None,
                        "annual_limit": 600,
                    },
                    source_document="data/FUND/extras/extras.pdf",
                    source_category="extras",
                ),
            ],
            [
                FieldSpec("hospital_excess", applies_to=("hospital", "combined")),
                FieldSpec("annual_limit", applies_to=("extras", "combined")),
            ],
        )

        self.assertEqual(analysis.fill_rate["hospital_excess"], 1.0)
        self.assertEqual(analysis.fill_rate["annual_limit"], 1.0)
        self.assertEqual(analysis.evaluated_documents["hospital_excess"], 1)
        self.assertEqual(analysis.evaluated_documents["annual_limit"], 1)
        self.assertEqual(analysis.product_type_accuracy, 0.0)
        self.assertEqual(
            analysis.product_type_mismatches,
            {"extras -> hospital": 1, "hospital -> extras": 1},
        )

    def test_unclassified_model_result_still_uses_source_category_denominator(self) -> None:
        analysis = analyze(
            [
                ExtractionRecord(
                    data={"product_name": "Unknown product"},
                    source_document="data/FUND/hospital/product.pdf",
                    source_category="hospital",
                )
            ],
            [FieldSpec("product_name", required=True, applies_to=("hospital",))],
        )

        self.assertEqual(analysis.documents, 1)
        self.assertEqual(analysis.product_type_unclassified, 1)
        self.assertEqual(analysis.evaluated_documents["product_name"], 1)
        self.assertEqual(analysis.missing_required, {})
        self.assertEqual(analysis.product_type_accuracy, 0.0)

    def test_report_renders_zero_denominator_as_not_applicable(self) -> None:
        analysis = analyze(
            [
                ExtractionRecord(
                    data={"product_type": "extras", "hospital_excess": None},
                    source_document="data/FUND/extras/product.pdf",
                    source_category="extras",
                )
            ],
            [FieldSpec("hospital_excess", applies_to=("hospital",))],
        )
        output = io.StringIO()

        with redirect_stdout(output):
            print_report(analysis)

        self.assertIn("Source category coverage: extras=1", output.getvalue())
        self.assertIn("N/A  hospital_excess", output.getvalue())


if __name__ == "__main__":
    unittest.main()
