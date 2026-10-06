"""Pet-insurance regression coverage for consensus human review."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.common.json_artifacts import read_artifact, write_artifact
from src.common.json_contracts import validate_contract
from src.refine.artifacts.renderer import render_consensus_schema
from src.refine.candidates.aggregator import FieldDecision
from src.refine.human_review.apply import apply_review_files
from src.refine.human_review.decisions import save_review_decision
from src.refine.human_review.queue import build_review_queue, write_review_queue
from src.schema.validation import validate_pet_schema_mapping
from src.schema_application.analyze import (
    analyze,
    build_feedback_data,
    load_field_specs,
    load_records,
)
from src.stability.signature import dimensions_for, signature_from_artifact


def pet_schema() -> dict:
    def field(
        name: str,
        field_type: str,
        *,
        enum_ref: str | None = None,
        required: bool = False,
        item_fields: list[dict] | None = None,
    ) -> dict:
        return {
            "name": name,
            "type": field_type,
            "description": f"Reusable definition for {name}.",
            "applies_to": ["product"],
            "required": required,
            "values": [],
            "aliases": [],
            "enum_ref": enum_ref,
            "item_fields": item_fields or [],
            "unique_items": field_type.startswith("list[") and field_type != "list[object]",
        }

    return {
        "vertical": "pet_insurance",
        "version": "test",
        "description": "Reusable pet insurance schema.",
        "cover_scopes": ["accident_only", "accident_and_illness"],
        "document_roles": [
            "pds", "update", "combined_fsg_pds", "supplementary_pds",
            "policy_booklet", "renewal_pds",
        ],
        "benefit_categories": ["vet_fees_injury"],
        "notes": [],
        "fields": [
            field("product_id", "string", required=True),
            field("product_name", "string", required=True),
            field("insurer_name", "string", required=True),
            field("co_payment_percentage", "number"),
            field("covered_benefit_categories", "list[enum]", enum_ref="benefit_categories"),
            field("benefit_coverages", "list[object]", item_fields=[
                {
                    "name": "benefit_category", "type": "enum", "required": True,
                    "description": "Canonical benefit category.", "values": [],
                    "enum_ref": "benefit_categories",
                },
                {
                    "name": "coverage_status", "type": "enum", "required": True,
                    "description": "Included, optional, or excluded.",
                    "values": ["included", "optional", "excluded"], "enum_ref": None,
                },
                {
                    "name": "source_document_id", "type": "string", "required": True,
                    "description": "Evidence document identifier.", "values": [],
                    "enum_ref": None,
                },
                {
                    "name": "source_block_id", "type": "string", "required": True,
                    "description": "Evidence block identifier.", "values": [],
                    "enum_ref": None,
                },
                {
                    "name": "source_page", "type": "number", "required": True,
                    "description": "Evidence page.", "values": [], "enum_ref": None,
                },
                {
                    "name": "source_quote", "type": "string", "required": True,
                    "description": "Verbatim evidence excerpt.", "values": [],
                    "enum_ref": None,
                },
            ]),
            field("annual_benefit_limit_options_aud", "list[number]"),
            field("benefit_percentage_options", "list[number]"),
            field("excess_options_aud", "list[number]"),
            field("temporary_condition_reinstatement_after_months", "number"),
        ],
    }


class PetHumanReviewTest(unittest.TestCase):
    def test_document_role_must_reference_canonical_roles(self) -> None:
        schema = pet_schema()
        schema["fields"].append({
            "name": "document_role",
            "type": "enum",
            "description": "Role of the source document.",
            "applies_to": ["document"],
            "required": False,
            "values": ["pds", "update"],
            "aliases": [],
            "enum_ref": None,
            "item_fields": [],
            "unique_items": False,
        })

        with self.assertRaisesRegex(ValueError, "document_role.*document_roles"):
            validate_pet_schema_mapping(schema)

    def test_queue_decision_and_apply_use_pet_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_path = root / "schema.json"
            consensus_dir = root / "consensus"
            consensus_dir.mkdir()
            write_artifact(
                base_path,
                {
                    "artifact_type": "discovered_schema",
                    "contract_version": "1.0.0",
                    "status": "success",
                    "created_at": "2026-01-01T00:00:00Z",
                    "provenance": {
                        "run_id": None, "provider": None, "model": None,
                        "document_input": None, "source_documents": [], "source_artifacts": [],
                    },
                    "data": pet_schema(),
                    "error": None,
                },
                data_contract="pet_insurance/discovered_schema",
            )
            decision = FieldDecision(
                "product_name", "product", "string", "Reviewed product name.",
                1, 1, "core", patch_types=["update_description"],
            )
            queue = build_review_queue(
                [decision], pet_schema(), 1, base_path, vertical="pet_insurance"
            )
            write_review_queue(
                queue, consensus_dir / "review_queue.json", vertical="pet_insurance"
            )
            save_review_decision(
                consensus_dir / "review_decisions.json",
                "field:product_name",
                "accept",
                vertical="pet_insurance",
            )
            output, summary = apply_review_files(
                consensus_dir, vertical="pet_insurance"
            )
            reviewed = read_artifact(
                output,
                expected_type="discovered_schema",
                data_contract="pet_insurance/discovered_schema",
            )["data"]
            self.assertEqual(summary["applied"], ["field:product_name"])
            self.assertEqual(reviewed["vertical"], "pet_insurance")
            self.assertEqual(
                next(field for field in reviewed["fields"] if field["name"] == "product_name")["description"],
                "Reviewed product name.",
            )

    def test_auto_merged_pet_consensus_uses_pet_validator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base_path = Path(tmp) / "schema.json"
            output_path = Path(tmp) / "consensus_schema.json"
            write_artifact(
                base_path,
                {
                    "artifact_type": "discovered_schema",
                    "contract_version": "1.0.0",
                    "status": "success",
                    "created_at": "2026-01-01T00:00:00Z",
                    "provenance": {
                        "run_id": None, "provider": None, "model": None,
                        "document_input": None, "source_documents": [], "source_artifacts": [],
                    },
                    "data": pet_schema(),
                    "error": None,
                },
                data_contract="pet_insurance/discovered_schema",
            )
            render_consensus_schema(
                base_path,
                [FieldDecision(
                    "product_name", "product", "string", "Merged product name.",
                    3, 3, "core", patch_types=["update_description"],
                )],
                output_path,
                vertical="pet_insurance",
            )
            reviewed = read_artifact(
                output_path,
                expected_type="discovered_schema",
                data_contract="pet_insurance/discovered_schema",
            )["data"]
            self.assertEqual(
                next(field for field in reviewed["fields"] if field["name"] == "product_name")["description"],
                "Merged product name.",
            )

    def test_product_level_analysis_uses_pet_applicability_and_feedback_contract(self) -> None:
        schema = pet_schema()
        specs = load_field_specs(schema)
        record = {
            "product_name": "Accident cover",
            "co_payment_percentage": 20,
            "covered_benefit_categories": ["vet_fees_injury"],
            "benefit_coverages": [{
                "benefit_category": "vet_fees_injury",
                "coverage_status": "included",
                "source_document_id": "accident_pds",
                "source_block_id": "p4_text_1",
                "source_page": 4,
                "source_quote": "Eligible accidental injury treatment is covered.",
            }],
            "annual_benefit_limit_options_aud": None,
            "benefit_percentage_options": None,
            "excess_options_aud": None,
            "temporary_condition_reinstatement_after_months": None,
            "cover_scope": "accident_only",
            "_unfilled": [
                "annual_benefit_limit_options_aud",
                "benefit_percentage_options",
                "excess_options_aud",
                "temporary_condition_reinstatement_after_months",
            ],
        }
        analysis = analyze([record], specs, vertical="pet_insurance")
        feedback = build_feedback_data(analysis)

        self.assertEqual(analysis.vertical, "pet_insurance")
        self.assertEqual(analysis.documents, 1)
        self.assertEqual(analysis.unclassified_docs, 0)
        self.assertEqual(analysis.fill_rate["co_payment_percentage"], 1.0)
        self.assertEqual(analysis.comparison_fill_rate["reimbursement"], 1.0)
        self.assertEqual(analysis.comparison_missing["annual_benefit_limit"], 1)
        self.assertIn("annual_benefit_limit_options_aud", analysis.model_unfilled)
        validate_contract(feedback, "pet_insurance/refinement_feedback")

    def test_load_records_flattens_pet_document_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_artifact(
                root / "document.json",
                {
                    "artifact_type": "extraction_result",
                    "contract_version": "1.0.0",
                    "status": "success",
                    "created_at": "2026-01-01T00:00:00Z",
                    "provenance": {
                        "run_id": None, "provider": None, "model": None,
                        "document_input": None, "source_documents": [], "source_artifacts": [],
                    },
                    "data": {
                        "document": {"document_role": "pds"},
                        "products": [
                            {"product_name": "One", "_unfilled": []},
                            {"product_name": "Two", "_unfilled": []},
                        ],
                    },
                    "error": None,
                },
                data_contract_schema={"type": "object"},
            )
            records, failures = load_records(
                root,
                {"type": "object"},
                vertical="pet_insurance",
            )
            self.assertEqual(failures, 0)
            self.assertEqual([record["product_name"] for record in records], ["One", "Two"])
            self.assertEqual(records[0]["_document_role"], "pds")

    def test_pet_stability_signature_tracks_pet_dimensions(self) -> None:
        artifact = {
            "artifact_type": "discovered_schema",
            "contract_version": "1.0.0",
            "status": "success",
            "created_at": "2026-01-01T00:00:00Z",
            "provenance": {
                "run_id": None, "provider": None, "model": None,
                "document_input": None, "source_documents": [], "source_artifacts": [],
            },
            "data": pet_schema(),
            "error": None,
        }
        signature = signature_from_artifact(artifact, "pet-schema")
        self.assertEqual(signature.vertical, "pet_insurance")
        self.assertIn("cover_scopes", dimensions_for([signature]))
        self.assertIn("accident_only", signature.cover_scopes)


if __name__ == "__main__":
    unittest.main()
