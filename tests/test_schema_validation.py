from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest import mock

from src.common.model_config import ModelSelection
from src.common.model_provider import ModelResponse
from src.schema.discovery import SchemaDiscovery
from src.schema.validation import (
    validate_field_payload,
    validate_item_field_payload,
    validate_schema_mapping,
)
from src.schema.loader import SchemaLoader


VALID_SCHEMA_MAPPING = {
    "vertical": "private_health", "version": "0.1-draft",
    "description": "Schema", "product_types": ["hospital", "extras"],
    "fields": [{
        "name": "product_name", "type": "string", "description": "Product name",
        "applies_to": ["hospital", "extras"], "required": True, "values": [],
        "aliases": [],
        "enum_ref": None, "item_fields": [], "unique_items": False,
    }, {
        "name": "product_type", "type": "enum", "description": "Product classification",
        "applies_to": ["hospital", "extras"], "required": True,
        "values": [], "aliases": [],
        "enum_ref": "product_types", "item_fields": [], "unique_items": False,
    }],
    "hospital_categories": [], "extras_services": [], "notes": [],
}
VALID_SCHEMA = json.dumps(VALID_SCHEMA_MAPPING)


class StaticProvider:
    def __init__(self, text: str) -> None:
        self.text = text

    def generate(self, request) -> ModelResponse:
        return ModelResponse(
            text=self.text,
            provider=request.selection.provider,
            model=request.selection.model,
        )


class SchemaValidationTest(unittest.TestCase):
    def test_accepts_the_documented_schema_contract(self) -> None:
        payload = validate_schema_mapping(json.loads(VALID_SCHEMA))

        self.assertEqual(payload["vertical"], "private_health")
        self.assertEqual(payload["fields"][0]["name"], "product_name")

    def test_rejects_duplicate_field_names(self) -> None:
        duplicate_payload = json.loads(VALID_SCHEMA)
        duplicate_payload["fields"].append({
            "name": "product_name", "type": "string", "description": "Duplicate",
            "applies_to": ["hospital"], "required": False, "values": [],
            "aliases": [],
            "enum_ref": None, "item_fields": [], "unique_items": False,
        })
        duplicate = json.dumps(duplicate_payload)
        with self.assertRaisesRegex(ValueError, "duplicate field name"):
            validate_schema_mapping(json.loads(duplicate))

    def test_rejects_ambulance_in_extras_when_dedicated_field_exists(self) -> None:
        payload = json.loads(VALID_SCHEMA)
        payload["extras_services"] = [{
            "canonical_name": "ambulance",
            "description": "Ambulance",
            "aliases": [],
        }]
        payload["fields"].append({
            "name": "ambulance_coverage", "type": "list[object]",
            "description": "Dedicated ambulance coverage",
            "applies_to": ["hospital", "extras"], "required": False,
            "values": [], "aliases": [], "enum_ref": None,
            "item_fields": [{
                "name": "annual_trip_limit_per_person", "type": "number",
                "description": "Annual trips per person", "required": False,
                "values": [], "enum_ref": None,
            }, {
                "name": "annual_trip_limit_per_policy", "type": "number",
                "description": "Annual trips per policy", "required": False,
                "values": [], "enum_ref": None,
            }],
            "unique_items": False,
        })

        with self.assertRaisesRegex(ValueError, "one authoritative representation"):
            validate_schema_mapping(payload)

    def test_rejects_schema_without_product_type_field(self) -> None:
        payload = json.loads(VALID_SCHEMA)
        payload["fields"] = [
            field for field in payload["fields"] if field["name"] != "product_type"
        ]
        with self.assertRaisesRegex(ValueError, "product_type"):
            validate_schema_mapping(payload)

    def test_rejects_inline_product_type_values(self) -> None:
        payload = json.loads(VALID_SCHEMA)
        product_type = next(
            field for field in payload["fields"] if field["name"] == "product_type"
        )
        product_type["values"] = ["hospital", "extras"]
        product_type["enum_ref"] = None

        with self.assertRaisesRegex(ValueError, "values must be empty"):
            validate_schema_mapping(payload)

    def test_rejects_product_type_without_top_level_enum_reference(self) -> None:
        payload = json.loads(VALID_SCHEMA)
        product_type = next(
            field for field in payload["fields"] if field["name"] == "product_type"
        )
        product_type["enum_ref"] = "hospital_categories"

        with self.assertRaisesRegex(ValueError, "enum_ref must reference"):
            validate_schema_mapping(payload)

    def test_rejects_enum_without_allowed_values(self) -> None:
        payload = json.loads(VALID_SCHEMA)
        payload["fields"][0]["type"] = "enum"
        invalid = json.dumps(payload)
        with self.assertRaisesRegex(ValueError, "enum field.*values"):
            validate_schema_mapping(json.loads(invalid))

    def test_rejects_non_scalar_enum_values(self) -> None:
        payload = json.loads(VALID_SCHEMA)
        payload["fields"][0]["type"] = "enum"
        payload["fields"][0]["values"] = [{"label": "included"}]
        invalid = json.dumps(payload)
        with self.assertRaisesRegex(ValueError, "enum field.*scalar"):
            validate_schema_mapping(json.loads(invalid))

    def test_rejects_field_with_unknown_product_type(self) -> None:
        payload = json.loads(VALID_SCHEMA)
        payload["fields"][0]["applies_to"] = ["hospital", "dental"]
        invalid = json.dumps(payload)
        with self.assertRaisesRegex(ValueError, "unknown product types"):
            validate_schema_mapping(json.loads(invalid))

    def test_field_payload_validation_does_not_silently_filter_invalid_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown product types"):
            validate_field_payload(
                {
                    "name": "annual_limit",
                    "type": "number",
                    "description": "Annual limit",
                    "applies_to": ["extras_cover", "extras"],
                    "required": False,
                    "values": [],
                    "aliases": [],
                },
                {"extras"},
            )

    def test_rejects_non_identity_required_fields(self) -> None:
        payload = json.loads(VALID_SCHEMA)
        payload["fields"].append({
            "name": "waiting_periods",
            "type": "list[object]",
            "description": "Waiting periods by service or condition.",
            "applies_to": ["hospital", "extras"],
            "required": True,
            "values": [],
            "aliases": [],
        })

        with self.assertRaisesRegex(ValueError, "must not be marked required"):
            validate_schema_mapping(payload)

    def test_list_object_requires_non_empty_unique_valid_item_fields(self) -> None:
        payload = json.loads(VALID_SCHEMA)
        field = {
            "name": "benefits", "type": "list[object]", "description": "Benefits",
            "applies_to": ["extras"], "required": False, "values": [],
            "aliases": [], "enum_ref": None, "item_fields": [], "unique_items": False,
        }
        payload["fields"].append(field)
        with self.assertRaisesRegex(ValueError, "non-empty item_fields"):
            validate_schema_mapping(payload)

        field["item_fields"] = [
            {"name": "label", "type": "string", "required": True,
             "description": None, "values": [], "enum_ref": None},
            {"name": "label", "type": "string", "required": False,
             "description": None, "values": [], "enum_ref": None},
        ]
        with self.assertRaisesRegex(ValueError, "duplicate item field name"):
            validate_schema_mapping(payload)

    def test_non_object_list_rejects_item_fields_and_enum_ref_is_checked(self) -> None:
        payload = json.loads(VALID_SCHEMA)
        payload["fields"].append({
            "name": "conditions", "type": "list[string]", "description": "Conditions",
            "applies_to": ["hospital"], "required": False, "values": [],
            "aliases": [], "enum_ref": None, "unique_items": False,
            "item_fields": [{"name": "value", "type": "string", "required": True}],
        })
        with self.assertRaisesRegex(ValueError, "must not declare item_fields"):
            validate_schema_mapping(payload)

        payload["fields"][-1]["item_fields"] = []
        payload["fields"][-1]["enum_ref"] = "extras_services"
        with self.assertRaisesRegex(ValueError, "must not declare enum_ref"):
            validate_schema_mapping(payload)

    def test_rejects_reserved_names_and_legacy_item_aliases_by_policy(self) -> None:
        with self.assertRaisesRegex(ValueError, "reserved"):
            validate_field_payload({"name": "_notes"}, {"extras"})
        payload = json.loads(VALID_SCHEMA)
        payload["extras_services"] = [
            {"canonical_name": "GeneralDental", "description": "Dental", "aliases": []}
        ]
        payload["fields"].append({
            "name": "extras_benefits", "type": "list[object]", "description": "Benefits",
            "applies_to": ["extras"], "required": False, "values": [],
            "aliases": [], "enum_ref": None, "unique_items": False,
            "item_fields": [{"name": "service", "type": "enum", "required": True,
                             "description": None, "values": [], "enum_ref": "extras_services"}],
        })
        with self.assertRaisesRegex(ValueError, "legacy alias"):
            validate_schema_mapping(payload)

    def test_canonical_item_policy_requires_exact_identifier_shape(self) -> None:
        payload = json.loads(VALID_SCHEMA)
        payload["extras_services"] = [
            {"canonical_name": "GeneralDental", "description": "Dental", "aliases": []}
        ]
        payload["fields"].append({
            "name": "extras_benefits", "type": "list[object]",
            "description": "Benefits", "applies_to": ["extras"],
            "required": False, "values": [], "aliases": [], "enum_ref": None,
            "unique_items": False,
            "item_fields": [{
                "name": "service_name", "type": "enum", "required": False,
                "description": None, "values": [], "enum_ref": "extras_services",
            }],
        })

        with self.assertRaisesRegex(ValueError, "canonical shape") as caught:
            validate_schema_mapping(payload)

        self.assertIn("required=true", caught.exception.errors[0]["repair_hint"])
        payload["fields"][-1]["item_fields"][0]["required"] = True
        self.assertIs(validate_schema_mapping(payload), payload)

    def test_extras_waiting_period_policy_rejects_free_text_service(self) -> None:
        payload = json.loads(VALID_SCHEMA)
        payload["fields"].append({
            "name": "extras_waiting_periods", "type": "list[object]",
            "description": "Waiting periods", "applies_to": ["extras"],
            "required": False, "values": [], "aliases": [], "enum_ref": None,
            "unique_items": False,
            "item_fields": [{
                "name": "service", "type": "string", "required": True,
                "description": None, "values": [], "enum_ref": None,
            }],
        })

        with self.assertRaisesRegex(ValueError, "legacy alias"):
            validate_schema_mapping(payload)

    def test_reports_multiple_independent_field_errors_together(self) -> None:
        payload = json.loads(VALID_SCHEMA)
        payload["fields"][0]["required"] = "yes"
        payload["fields"][1]["required"] = False

        with self.assertRaises(ValueError) as caught:
            validate_schema_mapping(payload)

        errors = caught.exception.errors
        self.assertGreaterEqual(len(errors), 2)
        self.assertTrue(any(error["path"] == "$.fields[0]" for error in errors))
        self.assertTrue(any("product_type field must be required" in error["message"]
                            for error in errors))

    def test_descriptions_reject_source_locations_but_allow_general_examples(self) -> None:
        payload = json.loads(VALID_SCHEMA)
        payload["fields"][0]["description"] = (
            "Published product name, e.g. the consumer-facing plan title."
        )
        self.assertIs(validate_schema_mapping(payload), payload)

        payload["fields"][0]["description"] = "Published name; see page 4."
        with self.assertRaisesRegex(ValueError, "sample-specific page") as caught:
            validate_schema_mapping(payload)
        self.assertIn("top-level notes", caught.exception.errors[0]["repair_hint"])

        with self.assertRaisesRegex(ValueError, "sample-specific page"):
            validate_item_field_payload(
                {
                    "name": "amount", "type": "number", "required": False,
                    "description": "Example amount from p2_t1.",
                    "values": [], "enum_ref": None,
                },
                field_name="benefits",
                index=0,
            )

    def test_entity_enum_ref_is_exact_and_fail_closed(self) -> None:
        resolver = SchemaLoader._resolve_enum_values
        with self.assertRaisesRegex(ValueError, "dotted"):
            resolver("canonical_values.extras_services", {"extras_services": ["Dental"]})
        with self.assertRaisesRegex(ValueError, "non-empty"):
            resolver("extras_services", {"extras_services": []})

    def test_discovery_rejects_invalid_schema_before_writing_usage_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "sample.pdf"
            pdf_path.write_bytes(b"pdf")
            usage_log = Path(tmp) / "usage.jsonl"

            discovery = SchemaDiscovery(
                    selection=ModelSelection("openai", "gpt-5", "markdown"),
                    provider=StaticProvider(json.dumps({"fields": []})),
                    usage_log_path=usage_log,
                    log=None,
                )
            with mock.patch(
                "src.schema.discovery.render_pdf_paths_for_prompt",
                return_value="# PDF: sample\nstructured content",
            ):
                with self.assertRaisesRegex(RuntimeError, "remained invalid"):
                    discovery.discover([str(pdf_path)])

            attempts = [json.loads(line) for line in usage_log.read_text().splitlines()]
            self.assertEqual(len(attempts), 3)
            self.assertEqual([item["attempt_number"] for item in attempts], [1, 2, 3])
            self.assertTrue(all(not item["validation_succeeded"] for item in attempts))

    def test_discovery_failure_writes_an_audit_artifact_when_output_is_known(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf_path = root / "sample.pdf"
            pdf_path.write_bytes(b"pdf")
            output_path = root / "schema.json"

            discovery = SchemaDiscovery(
                    selection=ModelSelection("openai", "gpt-5", "markdown"),
                    provider=StaticProvider(json.dumps({"fields": []})),
                    usage_log_path=None,
                    log=None,
                )
            with mock.patch(
                "src.schema.discovery.render_pdf_paths_for_prompt",
                return_value="# PDF: sample\nstructured content",
            ):
                with self.assertRaises(RuntimeError):
                    discovery.discover(
                        [str(pdf_path)], output_path=output_path, run_id="run-123"
                    )

            failure_path = root / "errors" / "schema_discovery" / "run-123.json"
            self.assertTrue(failure_path.exists())
            failure = json.loads(failure_path.read_text(encoding="utf-8"))
            self.assertEqual(failure["status"], "failed")
            self.assertEqual(failure["provenance"]["run_id"], "run-123")


if __name__ == "__main__":
    unittest.main()
