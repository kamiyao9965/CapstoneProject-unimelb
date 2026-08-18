from __future__ import annotations

import copy
import unittest

from src.common.json_contracts import ContractValidationError
from src.common.json_contracts import validate_inline_contract
from src.schema.canonical import (
    compile_canonical_extraction_contract,
    require_approved_canonical_schema,
    validate_canonical_schema,
)


def approved_travel_schema() -> dict[str, object]:
    return {
        "contract_version": "1.0.0",
        "vertical": "travel_insurance",
        "version": "1.0.0",
        "status": "approved",
        "description": "Reviewed Travel insurance product contract.",
        "review": {
            "reviewed_by": "project-reviewer",
            "reviewed_at": "2026-08-18T05:00:00Z",
            "rationale": "Approved for the first relational Travel slice.",
        },
        "output": {
            "collection": "products",
            "cardinality": "multiple",
            "document_notes_field": "_document_notes",
        },
        "identity": {
            "product_name_field": "product_name",
            "product_type_field": "product_type",
        },
        "extension": {
            "entity": "travel_product_details",
            "table": "travel_product_details",
            "attributes_column": "attributes",
        },
        "fields": [
            {
                "name": "product_name",
                "type": "string",
                "description": "Insurer-issued product or plan name.",
                "required": True,
                "nullable": False,
                "values": [],
                "aliases": ["plan name"],
                "storage": {
                    "strategy": "core_column",
                    "target": "products.canonical_name",
                },
            },
            {
                "name": "product_type",
                "type": "enum",
                "description": "Reviewed product classification.",
                "required": True,
                "nullable": False,
                "values": ["international_single_trip", "domestic"],
                "aliases": [],
                "storage": {
                    "strategy": "core_column",
                    "target": "product_releases.source_product_type",
                },
            },
            {
                "name": "geographic_scope",
                "type": "enum",
                "description": "Geographic scope of cover.",
                "required": True,
                "nullable": True,
                "values": ["international", "domestic", "inbound"],
                "aliases": [],
                "storage": {
                    "strategy": "extension_column",
                    "column": "geographic_scope",
                },
            },
            {
                "name": "cruise_cover_available",
                "type": "boolean",
                "description": "Whether cruise cover can be selected.",
                "required": True,
                "nullable": True,
                "values": [],
                "aliases": ["cruise cover"],
                "storage": {
                    "strategy": "extension_column",
                    "column": "cruise_cover_available",
                },
            },
            {
                "name": "benefits",
                "type": "list[object]",
                "description": "Open benefit objects retained losslessly.",
                "required": True,
                "nullable": True,
                "values": [],
                "aliases": [],
                "storage": {"strategy": "jsonb"},
            },
        ],
    }


class CanonicalSchemaLifecycleTests(unittest.TestCase):
    def test_accepts_reviewed_approved_schema(self) -> None:
        schema = approved_travel_schema()

        self.assertIs(require_approved_canonical_schema(schema), schema)

    def test_candidate_is_valid_but_cannot_cross_approval_boundary(self) -> None:
        schema = approved_travel_schema()
        schema["status"] = "candidate"
        schema["review"] = None

        self.assertIs(validate_canonical_schema(schema), schema)
        with self.assertRaisesRegex(ValueError, "human-approved"):
            require_approved_canonical_schema(schema)

    def test_approved_schema_requires_review_record(self) -> None:
        schema = approved_travel_schema()
        schema["review"] = None

        with self.assertRaises(ContractValidationError):
            validate_canonical_schema(schema)

    def test_approved_schema_rejects_placeholder_review_timestamp(self) -> None:
        schema = approved_travel_schema()
        schema["review"]["reviewed_at"] = "ISO-8601时间"

        with self.assertRaisesRegex(ValueError, "reviewed_at"):
            validate_canonical_schema(schema)

    def test_rejects_unknown_core_binding(self) -> None:
        schema = approved_travel_schema()
        schema["fields"][0]["storage"]["target"] = "documents.source_path"

        with self.assertRaises(ContractValidationError):
            validate_canonical_schema(schema)

    def test_rejects_duplicate_extension_columns(self) -> None:
        schema = approved_travel_schema()
        duplicate = copy.deepcopy(schema["fields"][2])
        duplicate["name"] = "destination_scope"
        schema["fields"].append(duplicate)

        with self.assertRaisesRegex(ValueError, "duplicate extension column"):
            validate_canonical_schema(schema)

    def test_open_object_list_must_use_jsonb(self) -> None:
        schema = approved_travel_schema()
        schema["fields"][-1]["storage"] = {
            "strategy": "extension_column",
            "column": "benefits",
        }

        with self.assertRaisesRegex(ValueError, r"list\[object\].*jsonb"):
            validate_canonical_schema(schema)

    def test_identity_fields_must_bind_to_expected_core_columns(self) -> None:
        schema = approved_travel_schema()
        schema["identity"]["product_name_field"] = "geographic_scope"

        with self.assertRaisesRegex(ValueError, "product-name identity"):
            validate_canonical_schema(schema)

    def test_rejects_multiple_fields_bound_to_one_core_column(self) -> None:
        schema = approved_travel_schema()
        duplicate = copy.deepcopy(schema["fields"][0])
        duplicate["name"] = "alternate_product_name"
        schema["fields"].append(duplicate)

        with self.assertRaisesRegex(ValueError, "duplicate core binding"):
            validate_canonical_schema(schema)

    def test_product_name_identity_must_be_a_string(self) -> None:
        schema = approved_travel_schema()
        schema["fields"][0]["type"] = "boolean"

        with self.assertRaisesRegex(ValueError, "product-name.*string"):
            validate_canonical_schema(schema)

    def test_product_type_identity_must_be_an_enum(self) -> None:
        schema = approved_travel_schema()
        schema["fields"][1]["type"] = "string"
        schema["fields"][1]["values"] = []

        with self.assertRaisesRegex(ValueError, "product-type.*enum"):
            validate_canonical_schema(schema)


class CanonicalExtractionCompilerTests(unittest.TestCase):
    def test_compiles_reviewed_fields_into_multiple_product_contract(self) -> None:
        contract = compile_canonical_extraction_contract(approved_travel_schema())

        product_contract = contract["properties"]["products"]["items"]
        self.assertFalse(product_contract["additionalProperties"])
        self.assertEqual(
            product_contract["properties"]["product_type"]["enum"],
            ["international_single_trip", "domestic"],
        )
        self.assertEqual(
            product_contract["properties"]["geographic_scope"]["enum"],
            ["international", "domestic", "inbound", None],
        )

    def test_generated_contract_validates_a_complete_extraction(self) -> None:
        contract = compile_canonical_extraction_contract(approved_travel_schema())
        payload = {
            "products": [
                {
                    "product_name": "International Comprehensive",
                    "product_type": "international_single_trip",
                    "geographic_scope": "international",
                    "cruise_cover_available": True,
                    "benefits": [{"name": "medical", "limit": "Unlimited"}],
                    "_unfilled": [],
                    "_notes": None,
                }
            ],
            "_document_notes": None,
        }

        self.assertIs(validate_inline_contract(payload, contract), payload)

    def test_generated_contract_rejects_unknown_enum_value(self) -> None:
        contract = compile_canonical_extraction_contract(approved_travel_schema())
        payload = {
            "products": [
                {
                    "product_name": "International Comprehensive",
                    "product_type": "unknown_type",
                    "geographic_scope": "international",
                    "cruise_cover_available": True,
                    "benefits": [],
                    "_unfilled": [],
                    "_notes": None,
                }
            ],
            "_document_notes": None,
        }

        with self.assertRaises(ContractValidationError):
            validate_inline_contract(payload, contract)

    def test_optional_field_is_not_required_but_remains_closed(self) -> None:
        schema = approved_travel_schema()
        schema["fields"][2]["required"] = False
        contract = compile_canonical_extraction_contract(schema)
        product_contract = contract["properties"]["products"]["items"]

        self.assertNotIn("geographic_scope", product_contract["required"])
        self.assertIn("geographic_scope", product_contract["properties"])

    def test_candidate_cannot_compile_extraction_contract(self) -> None:
        schema = approved_travel_schema()
        schema["status"] = "candidate"
        schema["review"] = None

        with self.assertRaisesRegex(ValueError, "human-approved"):
            compile_canonical_extraction_contract(schema)


if __name__ == "__main__":
    unittest.main()
