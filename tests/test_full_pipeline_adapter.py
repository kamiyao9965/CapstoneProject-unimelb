from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from src.common.json_artifacts import build_success_artifact, write_artifact
from src.run import build_parser
from src.schema.loader import SchemaLoader
from src.schema.validator import SchemaValidator
from tests.test_json_contracts import VALID_DISCOVERED_SCHEMA


class FullPipelineAdapterTest(unittest.TestCase):
    def test_loader_accepts_feat_discovered_schema_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            schema_path = Path(tmp) / "schema.json"
            artifact = build_success_artifact(
                artifact_type="discovered_schema",
                contract_version="1.0.0",
                data=VALID_DISCOVERED_SCHEMA,
                provenance={
                    "run_id": None,
                    "provider": None,
                    "model": None,
                    "document_input": None,
                    "source_documents": [],
                    "source_artifacts": [],
                },
                data_contract="private_health/discovered_schema",
            )
            write_artifact(
                schema_path,
                artifact,
                data_contract="private_health/discovered_schema",
            )

            schema = SchemaLoader().load(schema_path)

        self.assertEqual(schema.vertical, "private_health")
        self.assertEqual(schema.version, VALID_DISCOVERED_SCHEMA["version"])
        self.assertEqual(schema.metadata["schema_style"], "discovered_json")
        self.assertEqual(schema.hospital.canonical_categories, ["BackNeckSpine"])
        self.assertEqual(schema.extras.canonical_services, ["GeneralDental"])
        self.assertEqual([field.name for field in schema.hospital.fields], [
            "product_type",
            "product_name",
        ])
        self.assertEqual(SchemaValidator().validate(schema), [])

    def test_discovered_schema_builds_flat_extraction_contract(self) -> None:
        schema_data = json.loads(json.dumps(VALID_DISCOVERED_SCHEMA))
        schema_data["fields"].append(
            {
                "name": "hospital_tier",
                "type": "enum",
                "description": "Hospital cover tier.",
                "applies_to": ["hospital", "combined"],
                "required": False,
                "values": ["Gold", "Silver", "Bronze", "Basic"],
                "aliases": ["tier"],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            schema_path = Path(tmp) / "schema.json"
            artifact = build_success_artifact(
                artifact_type="discovered_schema",
                contract_version="1.0.0",
                data=schema_data,
                provenance={
                    "run_id": None,
                    "provider": None,
                    "model": None,
                    "document_input": None,
                    "source_documents": [],
                    "source_artifacts": [],
                },
                data_contract="private_health/discovered_schema",
            )
            write_artifact(
                schema_path,
                artifact,
                data_contract="private_health/discovered_schema",
            )
            schema = SchemaLoader().load(schema_path)

        contract = SchemaLoader().build_json_schema(schema)

        self.assertIn("product_type", contract["properties"])
        self.assertIn("product_name", contract["properties"])
        self.assertIn("_unfilled", contract["properties"])
        self.assertNotIn("hospital", contract["properties"])
        self.assertNotIn("extras", contract["properties"])
        extras_rule = next(
            rule for rule in contract["allOf"]
            if rule["if"]["properties"]["product_type"]["const"] == "extras"
        )
        self.assertEqual(
            extras_rule["then"]["properties"]["hospital_tier"],
            {"type": "null"},
        )

    def test_unified_cli_exposes_full_pipeline_commands(self) -> None:
        parser = build_parser()

        for command in ("discover", "extract", "batch"):
            with self.subTest(command=command):
                help_text = parser.format_help()
                self.assertIn(command, help_text)

    def test_no_fallback_flag_is_available_for_extraction_commands(self) -> None:
        parser = build_parser()

        extract_args = parser.parse_args([
            "extract",
            "--pdf", "example.pdf",
            "--schema", "schema.json",
            "--no-fallback",
        ])
        batch_args = parser.parse_args([
            "batch",
            "--schema", "schema.json",
            "--no-fallback",
        ])

        self.assertTrue(extract_args.no_fallback)
        self.assertTrue(batch_args.no_fallback)


if __name__ == "__main__":
    unittest.main()
