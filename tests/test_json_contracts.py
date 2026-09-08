from __future__ import annotations

import unittest

from src.common.json_contracts import (
    ContractValidationError,
    load_contract,
    validate_contract,
    validate_inline_contract,
)
from src.schema.validation import validate_schema_mapping


VALID_DISCOVERED_SCHEMA = {
    "vertical": "private_health",
    "version": "0.1-draft",
    "description": "Cross-fund private health insurance extraction schema.",
    "product_types": ["hospital", "extras", "generalhealth", "combined"],
    "fields": [
        {
            "name": "product_type",
            "type": "enum",
            "description": "Canonical private-health product classification.",
            "applies_to": ["hospital", "extras", "generalhealth", "combined"],
            "required": True,
            "values": ["hospital", "extras", "generalhealth", "combined"],
            "aliases": ["cover type"],
        },
        {
            "name": "product_name",
            "type": "string",
            "description": "Published product name.",
            "applies_to": ["hospital", "extras", "generalhealth", "combined"],
            "required": True,
            "values": [],
            "aliases": ["cover name"],
        }
    ],
    "hospital_categories": [
        {
            "canonical_name": "BackNeckSpine",
            "description": "Hospital treatment for the back, neck and spine.",
            "aliases": ["back, neck and spine"],
        }
    ],
    "extras_services": [
        {
            "canonical_name": "GeneralDental",
            "description": "General dental services.",
            "aliases": ["general dental"],
        }
    ],
    "notes": ["Use null when source evidence is absent."],
}


class JsonContractTest(unittest.TestCase):
    def test_documented_discovered_schema_passes_structural_and_business_validation(self) -> None:
        validated = validate_contract(
            VALID_DISCOVERED_SCHEMA,
            "private_health/discovered_schema",
        )

        self.assertIs(validated, VALID_DISCOVERED_SCHEMA)
        self.assertEqual(validate_schema_mapping(validated)["vertical"], "private_health")
        self.assertIn("taxonomies", validate_schema_mapping(validated))

    def test_rejects_missing_required_property(self) -> None:
        invalid = dict(VALID_DISCOVERED_SCHEMA)
        invalid.pop("fields")

        with self.assertRaisesRegex(ContractValidationError, r"\$.*fields.*required"):
            validate_contract(invalid, "private_health/discovered_schema")

    def test_rejects_unexpected_property(self) -> None:
        invalid = dict(VALID_DISCOVERED_SCHEMA)
        invalid["invented"] = True

        with self.assertRaisesRegex(ContractValidationError, "invented"):
            validate_contract(invalid, "private_health/discovered_schema")

    def test_rejects_values_when_not_an_array(self) -> None:
        invalid = dict(VALID_DISCOVERED_SCHEMA)
        invalid["fields"] = [dict(VALID_DISCOVERED_SCHEMA["fields"][0])]
        invalid["fields"][0]["values"] = "product name"

        with self.assertRaisesRegex(ContractValidationError, r"fields\[0\]\.values"):
            validate_contract(invalid, "private_health/discovered_schema")

    def test_contract_names_are_allowlisted(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown JSON contract"):
            load_contract("../../.env")

    def test_loaded_contract_is_an_independent_copy(self) -> None:
        first = load_contract("artifact_envelope")
        first["title"] = "mutated"

        second = load_contract("artifact_envelope")

        self.assertNotEqual(second.get("title"), "mutated")

    def test_validation_errors_do_not_expose_invalid_instance_values(self) -> None:
        sensitive_value = "SENSITIVE_DOCUMENT_VALUE"

        with self.assertRaises(ContractValidationError) as caught:
            validate_inline_contract(
                {"amount": sensitive_value},
                {
                    "type": "object",
                    "required": ["amount"],
                    "properties": {"amount": {"type": "number"}},
                },
            )

        self.assertNotIn(sensitive_value, str(caught.exception))
        self.assertNotIn(sensitive_value, repr(caught.exception.errors))
        self.assertIn("number", caught.exception.errors[0]["message"])

    def test_field_frequency_rejects_malformed_nested_fields(self) -> None:
        with self.assertRaises(ContractValidationError):
            validate_contract(
                {"fields": [{"field": "excess", "frequency": 4}]},
                "private_health/field_frequency",
            )

    def test_patch_stability_rejects_unknown_nested_dimensions(self) -> None:
        payload = {
            "metadata": {
                "total_runs": 3,
                "stability_source": "candidate_schema_patches",
            },
            "dimensions": {"invented": {}},
        }
        with self.assertRaises(ContractValidationError):
            validate_contract(payload, "private_health/patch_stability")

    def test_review_queue_rejects_incomplete_proposed_field_shape(self) -> None:
        payload = {
            "metadata": {
                "generated_at": "2026-07-14T00:00:00+00:00",
                "consensus_source": "candidate_schema_patches",
                "total_runs": 3,
                "base_schema_path": "schema.json",
                "schema_build_samples": [],
            },
            "updates": [{"id": "field:excess", "proposed_update": {"invented": 1}}],
        }
        with self.assertRaises(ContractValidationError):
            validate_contract(payload, "private_health/review_queue")

    def test_review_decisions_require_an_edit_payload_for_edit_action(self) -> None:
        payload = {
            "metadata": {
                "reviewed_at": "2026-07-14T00:00:00+00:00",
                "reviewer": "reviewer",
            },
            "decisions": [{
                "id": "field:excess",
                "action": "edit",
                "reviewer_notes": "",
                "edited_update": None,
            }],
        }
        with self.assertRaises(ContractValidationError):
            validate_contract(payload, "private_health/review_decisions")


if __name__ == "__main__":
    unittest.main()
