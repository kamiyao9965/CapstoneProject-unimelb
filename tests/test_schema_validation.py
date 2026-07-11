from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.common.model_config import ModelSelection
from src.common.model_provider import ModelResponse
from src.schema.discovery import SchemaDiscovery
from src.schema.validation import validate_field_payload, validate_schema_text


VALID_SCHEMA = """
vertical: private_health
version: 0.1-draft
product_types: [hospital, extras]
fields:
  - name: product_name
    type: string
    description: Product name
    applies_to: [hospital, extras]
    required: true
    values: []
""".strip()


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
        payload = validate_schema_text(VALID_SCHEMA)

        self.assertEqual(payload["vertical"], "private_health")
        self.assertEqual(payload["fields"][0]["name"], "product_name")

    def test_rejects_invalid_yaml(self) -> None:
        with self.assertRaisesRegex(ValueError, "valid YAML"):
            validate_schema_text("fields: [")

    def test_rejects_duplicate_field_names(self) -> None:
        duplicate = VALID_SCHEMA + """
  - name: product_name
    type: string
    description: Duplicate
    applies_to: [hospital]
    required: false
    values: []
"""
        with self.assertRaisesRegex(ValueError, "duplicate field name"):
            validate_schema_text(duplicate)

    def test_rejects_enum_without_allowed_values(self) -> None:
        invalid = VALID_SCHEMA.replace("type: string", "type: enum", 1)
        with self.assertRaisesRegex(ValueError, "enum field.*values"):
            validate_schema_text(invalid)

    def test_rejects_non_scalar_enum_values(self) -> None:
        invalid = VALID_SCHEMA.replace("type: string", "type: enum", 1).replace(
            "values: []", "values: [{label: included}]", 1
        )
        with self.assertRaisesRegex(ValueError, "enum field.*scalar"):
            validate_schema_text(invalid)

    def test_rejects_field_with_unknown_product_type(self) -> None:
        invalid = VALID_SCHEMA.replace(
            "applies_to: [hospital, extras]",
            "applies_to: [hospital, dental]",
            1,
        )
        with self.assertRaisesRegex(ValueError, "unknown product types"):
            validate_schema_text(invalid)

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
                },
                {"extras"},
            )

    def test_discovery_rejects_invalid_schema_before_writing_usage_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "sample.pdf"
            pdf_path.write_bytes(b"pdf")
            usage_log = Path(tmp) / "usage.jsonl"

            with self.assertRaisesRegex(ValueError, "valid YAML"):
                SchemaDiscovery(
                    selection=ModelSelection("openai", "gpt-5", "pdf"),
                    provider=StaticProvider("fields: ["),
                    usage_log_path=usage_log,
                    log=None,
                ).discover([str(pdf_path)])

            self.assertFalse(usage_log.exists())


if __name__ == "__main__":
    unittest.main()
