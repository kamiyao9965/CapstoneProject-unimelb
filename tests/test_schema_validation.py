from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest import mock

from src.common.model_config import ModelSelection
from src.common.model_provider import ModelResponse
from src.schema.discovery import SchemaDiscovery
from src.schema.validation import validate_field_payload, validate_schema_mapping


VALID_SCHEMA_MAPPING = {
    "vertical": "private_health", "version": "0.1-draft",
    "description": "Schema", "product_types": ["hospital", "extras"],
    "fields": [{
        "name": "product_name", "type": "string", "description": "Product name",
        "applies_to": ["hospital", "extras"], "required": True, "values": [],
        "aliases": [],
    }, {
        "name": "product_type", "type": "enum", "description": "Product classification",
        "applies_to": ["hospital", "extras"], "required": True,
        "values": ["hospital", "extras"], "aliases": [],
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
    def test_both_legacy_formats_enter_one_model_without_mutating_source(self) -> None:
        from copy import deepcopy
        from tests.test_travel_schema_migration import VALID_TRAVEL_SCHEMA
        from src.schema.validation import normalize_schema
        for source in (VALID_SCHEMA_MAPPING, VALID_TRAVEL_SCHEMA):
            with self.subTest(vertical=source["vertical"]):
                before = deepcopy(source)
                schema = normalize_schema(source)
                self.assertIn("taxonomies", schema)
                self.assertNotIn("product_type_field", schema)
                self.assertEqual(sum(f["name"] == "product_type" for f in schema["fields"]), 1)
                self.assertEqual(source, before)

    def test_shared_schema_rejects_wrong_vertical_and_unknown_taxonomy(self) -> None:
        from src.schema.validation import normalize_schema
        from src.verticals.manifest import default_manifest_path, load_vertical_manifest
        health = load_vertical_manifest(default_manifest_path("private_health"))
        schema = normalize_schema(VALID_SCHEMA_MAPPING)
        schema["taxonomies"]["coverage_categories"] = []
        with self.assertRaisesRegex(ValueError, "taxonom"):
            validate_schema_mapping(schema, manifest=health)
        schema["vertical"] = "travel_insurance"
        with self.assertRaisesRegex(ValueError, "vertical"):
            validate_schema_mapping(schema, manifest=health)

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
        })
        duplicate = json.dumps(duplicate_payload)
        with self.assertRaisesRegex(ValueError, "duplicate field name"):
            validate_schema_mapping(json.loads(duplicate))

    def test_rejects_schema_without_product_type_field(self) -> None:
        payload = json.loads(VALID_SCHEMA)
        payload["fields"] = [
            field for field in payload["fields"] if field["name"] != "product_type"
        ]
        with self.assertRaisesRegex(ValueError, "product_type"):
            validate_schema_mapping(payload)

    def test_rejects_product_type_values_that_do_not_match_top_level_types(self) -> None:
        payload = json.loads(VALID_SCHEMA)
        product_type = next(
            field for field in payload["fields"] if field["name"] == "product_type"
        )
        product_type["values"] = ["hospital"]

        with self.assertRaisesRegex(ValueError, "values.*product_types"):
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
