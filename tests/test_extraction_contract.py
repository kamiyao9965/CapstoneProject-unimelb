from __future__ import annotations

import unittest

from src.common.json_contracts import validate_inline_contract
from src.schema.contract import compile_extraction_contract
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
            "values": ["hospital"], "aliases": [],
        }, {
            "name": "cover_status", "type": "enum", "description": "Status",
            "applies_to": ["hospital"], "required": False,
            "values": ["included", "excluded"],
            "aliases": [],
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

    def test_extras_service_name_is_constrained_to_canonical_enum(self) -> None:
        schema = dict(VALID_DISCOVERED_SCHEMA)
        schema["fields"] = [*schema["fields"], {
            "name": "extras_benefits", "type": "list[object]",
            "description": "Canonical extras rows", "applies_to": ["extras"],
            "required": False, "values": [], "aliases": [],
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


if __name__ == "__main__":
    unittest.main()
