from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from src.schema_application.extractor import SchemaExtractor
from tests.test_nested_contracts import closed_benefit_item_schema, discovered_travel_schema, canonical_travel_schema


class StrictExtractionTests(unittest.TestCase):
    def test_open_legacy_lists_stop_before_provider_creation(self) -> None:
        for schema in (discovered_travel_schema(), canonical_travel_schema()):
            with self.subTest(schema=schema.get("status", "discovered")), \
                 patch("src.schema_application.extractor.create_provider") as factory:
                with self.assertRaisesRegex(ValueError, "closed|item_schema"):
                    SchemaExtractor(schema, log=None)
                factory.assert_not_called()

    def test_closed_schemas_use_strict_contracts_with_every_key_required(self) -> None:
        for schema in (discovered_travel_schema(), canonical_travel_schema()):
            schema["fields"][-1]["item_schema"] = closed_benefit_item_schema()
            original = copy.deepcopy(schema)
            with self.subTest(schema=schema.get("status", "discovered")):
                extractor = SchemaExtractor(schema, provider=object(), log=None)
                self.assertTrue(extractor.structured_output_strict)
                product = extractor.extraction_contract["properties"]["products"]["items"]
                self.assertEqual(set(product["required"]), set(product["properties"]))
                self.assertEqual(original, schema)


if __name__ == "__main__":
    unittest.main()
