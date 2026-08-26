from __future__ import annotations

import unittest

from src.common.json_contracts import validate_inline_contract
from src.schema.contract import compile_extraction_contract
from src.schema.loader import SchemaLoader
from tests.test_json_contracts import VALID_DISCOVERED_SCHEMA


class ExtractionContractTest(unittest.TestCase):
    def test_compiler_is_deterministic_and_rejects_unknown_keys(self) -> None:
        first = compile_extraction_contract(VALID_DISCOVERED_SCHEMA)
        second = compile_extraction_contract(VALID_DISCOVERED_SCHEMA)

        self.assertEqual(first, second)
        with self.assertRaises(ValueError):
            validate_inline_contract(
                {
                    "product_name": "Example", "_unfilled": [], "_notes": None,
                    "invented": True,
                },
                first,
            )

    def test_enum_allows_configured_value_or_null_only(self) -> None:
        schema = dict(VALID_DISCOVERED_SCHEMA)
        schema["fields"] = [{
            "name": "product_type", "type": "enum",
            "description": "Product classification",
            "applies_to": ["hospital"], "required": True,
            "values": [], "aliases": [],
            "enum_ref": "product_types", "item_fields": [], "unique_items": False,
        }, {
            "name": "cover_status", "type": "enum", "description": "Status",
            "applies_to": ["hospital"], "required": False,
            "values": ["included", "excluded"],
            "aliases": [],
            "enum_ref": None, "item_fields": [], "unique_items": False,
        }]
        schema["product_types"] = ["hospital"]
        contract = compile_extraction_contract(schema)

        validate_inline_contract(
            {
                "product_type": "hospital", "cover_status": None,
                "_unfilled": ["cover_status"], "_notes": None,
            },
            contract,
        )
        with self.assertRaises(ValueError):
            validate_inline_contract(
                {
                    "product_type": "hospital", "cover_status": "maybe",
                    "_unfilled": [], "_notes": None,
                },
                contract,
            )

    def test_effective_product_type_compiles_to_const(self) -> None:
        contract = compile_extraction_contract(
            VALID_DISCOVERED_SCHEMA,
            product_type="generalhealth",
        )

        self.assertEqual(
            contract["properties"]["product_type"],
            {"const": "generalhealth"},
        )
        validate_inline_contract(
            {
                "product_type": "generalhealth", "product_name": "Example",
                "_unfilled": [], "_notes": None,
            },
            contract,
        )
        with self.assertRaises(ValueError):
            validate_inline_contract(
                {
                    "product_type": "extras", "product_name": "Example",
                    "_unfilled": [], "_notes": None,
                },
                contract,
            )
        with self.assertRaisesRegex(ValueError, "not declared by the schema"):
            compile_extraction_contract(
                VALID_DISCOVERED_SCHEMA,
                product_type="dental",
            )

    def test_effective_product_type_excludes_inapplicable_fields_from_unfilled(self) -> None:
        schema = dict(VALID_DISCOVERED_SCHEMA)
        schema["fields"] = [*schema["fields"], {
            "name": "cover_variant_notes", "type": "string",
            "description": "Hospital cover variant", "applies_to": ["hospital", "combined"],
            "required": False, "values": [], "aliases": [],
            "enum_ref": None, "item_fields": [], "unique_items": False,
        }]
        contract = compile_extraction_contract(schema, product_type="generalhealth")
        payload = {
            "product_type": "generalhealth", "product_name": "Everyday Extras",
            "cover_variant_notes": None, "_unfilled": [], "_notes": None,
        }

        self.assertEqual(contract["properties"]["cover_variant_notes"], {"type": "null"})
        self.assertNotIn(
            "cover_variant_notes",
            contract["properties"]["_unfilled"]["items"]["enum"],
        )
        validate_inline_contract(payload, contract)

        with self.assertRaises(ValueError):
            validate_inline_contract(
                {**payload, "_unfilled": ["cover_variant_notes"]},
                contract,
            )
        with self.assertRaises(ValueError):
            validate_inline_contract(
                {**payload, "cover_variant_notes": "Flexi-bundle"},
                contract,
            )

        hospital_contract = compile_extraction_contract(schema, product_type="hospital")
        self.assertIn(
            "cover_variant_notes",
            hospital_contract["properties"]["_unfilled"]["items"]["enum"],
        )

    def test_extras_service_name_is_constrained_to_canonical_enum(self) -> None:
        schema = dict(VALID_DISCOVERED_SCHEMA)
        schema["fields"] = [*schema["fields"], {
            "name": "extras_benefits", "type": "list[object]",
            "description": "Canonical extras rows", "applies_to": ["extras"],
            "required": False, "values": [], "aliases": [],
            "enum_ref": None, "unique_items": False,
            "item_fields": [
                {"name": "service_name", "type": "enum", "required": True,
                 "description": None, "values": [], "enum_ref": "extras_services"},
                {"name": "limit", "type": "number", "required": False,
                 "description": None, "values": [], "enum_ref": None},
            ],
        }]
        contract = compile_extraction_contract(schema)
        base = {
            "product_type": "extras", "product_name": "Example",
            "_unfilled": [], "_notes": None,
        }
        validate_inline_contract(
            {**base, "extras_benefits": [{"service_name": "GeneralDental", "limit": 500}]},
            contract,
        )
        with self.assertRaises(ValueError):
            validate_inline_contract(
                {**base, "extras_benefits": [{"service_name": "Per visit benefit"}]},
                contract,
            )

        with self.assertRaises(ValueError):
            validate_inline_contract(
                {**base, "extras_benefits": [{"service": "GeneralDental", "limit": 500}]},
                contract,
            )
        with self.assertRaises(ValueError):
            validate_inline_contract(
                {**base, "extras_benefits": [{"service_name": "GeneralDental"}]},
                contract,
            )
        with self.assertRaises(ValueError):
            validate_inline_contract(
                {**base, "extras_benefits": [{"service_name": "GeneralDental", "limit": None, "invented": 1}]},
                contract,
            )

    def test_item_required_controls_nullability_not_key_presence(self) -> None:
        schema = dict(VALID_DISCOVERED_SCHEMA)
        schema["fields"] = [*schema["fields"], {
            "name": "benefits", "type": "list[object]", "description": "Benefits",
            "applies_to": ["extras"], "required": False, "values": [],
            "aliases": [], "enum_ref": None, "unique_items": False,
            "item_fields": [
                {"name": "label", "type": "string", "required": True,
                 "description": None, "values": [], "enum_ref": None},
                {"name": "amount_raw", "type": "string", "required": False,
                 "description": None, "values": [], "enum_ref": None},
            ],
        }]
        contract = compile_extraction_contract(schema)
        item = contract["properties"]["benefits"]["oneOf"][1]["items"]
        self.assertFalse(item["additionalProperties"])
        self.assertEqual(item["required"], ["label", "amount_raw"])
        self.assertEqual(item["properties"]["label"]["type"], "string")
        self.assertEqual(item["properties"]["amount_raw"]["type"], ["string", "null"])

    def test_scalar_lists_compile_and_loader_uses_same_field_compiler(self) -> None:
        schema = dict(VALID_DISCOVERED_SCHEMA)
        schema["fields"] = [*schema["fields"], {
            "name": "conditions", "type": "list[string]", "description": "Conditions",
            "applies_to": ["hospital"], "required": False, "values": [],
            "aliases": [], "enum_ref": None, "item_fields": [], "unique_items": True,
        }]
        direct = compile_extraction_contract(schema)
        loaded = SchemaLoader()._load_discovered_schema(schema)
        via_loader = SchemaLoader().build_json_schema(loaded)
        self.assertEqual(
            direct["properties"]["conditions"], via_loader["properties"]["conditions"]
        )
        array_contract = direct["properties"]["conditions"]["oneOf"][1]
        self.assertTrue(array_contract["uniqueItems"])
        with self.assertRaises(ValueError):
            validate_inline_contract(
                {"product_type": "hospital", "product_name": "Example",
                 "conditions": ["A", "A"], "_unfilled": [], "_notes": None},
                direct,
            )


if __name__ == "__main__":
    unittest.main()
