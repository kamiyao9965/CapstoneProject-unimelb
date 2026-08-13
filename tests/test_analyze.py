from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from src.common.json_artifacts import (
    build_failure_artifact,
    build_success_artifact,
    write_artifact,
)
from src.schema_application.analyze import (
    FieldSpec,
    analyze,
    build_feedback,
    build_feedback_data,
    load_field_specs,
    load_records,
)
from src.schema.contract import compile_extraction_contract
from tests.test_json_contracts import VALID_DISCOVERED_SCHEMA


class AllExtractionFailuresTest(unittest.TestCase):
    def test_failed_artifact_is_counted_but_never_returned_as_record(self) -> None:
        provenance = {
            "run_id": "test", "provider": "openai", "model": "gpt-5",
            "document_input": "pdf", "source_documents": [], "source_artifacts": [],
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

            self.assertEqual(records[0]["product_type"], "hospital")
            self.assertEqual(records[0]["_product_type_evidence"]["model"], "hospital")
            self.assertEqual(failures, 1)

    def test_retried_failure_is_not_counted_after_same_schema_pdf_succeeds(self) -> None:
        provenance = {
            "run_id": "success", "provider": "deepseek", "model": "deepseek-v4-pro",
            "document_input": "markdown", "source_documents": ["sample.pdf"],
            "source_artifacts": ["schema_sha256:schema", "pdf_sha256:pdf"],
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
                provenance={**provenance, "run_id": "failure"},
                error_code="failed", message="empty response",
            )
            write_artifact(
                root / "success.json", success,
                data_contract_schema={"type": "object"},
            )
            write_artifact(
                root / "errors" / "extraction" / "failure.json", failure,
            )

            records, failures = load_records(root, {"type": "object"})

            self.assertEqual(len(records), 1)
            self.assertEqual(failures, 0)

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
            [{"product_type": "hospital", "cover_status": "unknown"}],
            [
                FieldSpec(
                    "cover_status", type="enum", values=["included"],
                    applies_to=("hospital",),
                )
            ],
        )

        data = build_feedback_data(analysis)

        self.assertEqual(data["analysis"]["enum_violations"], {"cover_status": ["unknown"]})
        self.assertTrue(
            any("cover_status" in instruction for instruction in data["instructions"])
        )
        self.assertIn("business_fidelity", data["analysis"])
        self.assertIn("rule_summary", data["analysis"]["business_fidelity"])

    def test_feedback_flags_fields_filled_outside_applies_to(self) -> None:
        analysis = analyze(
            [
                {
                    "product_type": "combined",
                    "excess_options": [{"amount": "$750"}],
                    "extras_services": [{"service": "General Dental"}],
                },
                {
                    "product_type": "extras",
                    "excess_options": None,
                    "extras_services": [{"service": "Optical"}],
                },
            ],
            [
                FieldSpec("excess_options", applies_to=("hospital",)),
                FieldSpec("extras_services", applies_to=("extras",)),
            ],
        )

        data = build_feedback_data(analysis)

        self.assertEqual(
            analysis.applies_to_mismatches,
            {
                "excess_options": {"combined": 1},
                "extras_services": {"combined": 1},
            },
        )
        self.assertEqual(
            data["analysis"]["applies_to_mismatches"],
            analysis.applies_to_mismatches,
        )
        self.assertTrue(
            any("applies_to" in instruction for instruction in data["instructions"])
        )
        self.assertTrue(
            any(
                "excess_options: add combined" in instruction
                for instruction in data["instructions"]
            )
        )
        self.assertIn(
            "excess_options",
            data["analysis"]["business_fidelity"]["protected_fields"],
        )

    def test_business_fidelity_warns_against_generic_replacement(self) -> None:
        analysis = analyze(
            [{"product_type": "hospital", "coverage_status": []}],
            [
                FieldSpec("coverage_status", type="list[object]", applies_to=("hospital",)),
            ],
        )

        data = build_feedback_data(analysis)

        self.assertTrue(
            any(
                "coverage_status is generic" in risk
                for risk in data["analysis"]["business_fidelity"]["replacement_risks"]
            )
        )
        self.assertIn("Business fidelity guardrail", data["instructions"][0])

    def test_business_fidelity_treats_ambulance_cover_as_protected_alias(self) -> None:
        analysis = analyze(
            [{"product_type": "hospital", "exclusions": [], "ambulance_cover": "Emergency"}],
            [
                FieldSpec("exclusions", type="list[object]", applies_to=("hospital",)),
                FieldSpec("ambulance_cover", applies_to=("hospital",)),
            ],
        )

        fidelity = build_feedback_data(analysis)["analysis"]["business_fidelity"]

        self.assertIn("ambulance_benefit", fidelity["protected_fields"])
        self.assertNotIn(
            "exclusions is generic and must not replace ambulance_benefit.",
            fidelity["replacement_risks"],
        )

    def test_business_fidelity_protects_annual_limits(self) -> None:
        analysis = analyze(
            [{"product_type": "extras", "annual_limits": [{"amount": "$500"}]}],
            [FieldSpec("annual_limits", type="list[object]", applies_to=("extras",))],
        )

        fidelity = build_feedback_data(analysis)["analysis"]["business_fidelity"]

        self.assertIn("annual_limits", fidelity["protected_fields"])

    def test_override_product_type_takes_precedence_for_analysis(self) -> None:
        analysis = analyze(
            [
                {
                    "product_type": "hospital",
                    "ambulance_benefit": "Emergency ambulance",
                    "_product_type_evidence": {
                        "directory": "hospital",
                        "override": "extras",
                        "effective": "extras",
                        "model": "hospital",
                        "conflict": True,
                    },
                }
            ],
            [FieldSpec("ambulance_benefit", applies_to=("extras",))],
        )

        self.assertEqual(analysis.evaluated_documents["ambulance_benefit"], 1)
        self.assertEqual(analysis.fill_rate["ambulance_benefit"], 1.0)


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
