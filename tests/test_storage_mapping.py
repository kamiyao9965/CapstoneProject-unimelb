from __future__ import annotations

import json
import unittest

from src.common.json_contracts import validate_contract
from src.verticals.manifest import PROJECT_ROOT


class StorageMappingContractTest(unittest.TestCase):
    def test_travel_storage_mapping_is_valid_and_complete(self) -> None:
        path = PROJECT_ROOT / "configs/travel_insurance/storage_mapping.json"
        mapping = json.loads(path.read_text(encoding="utf-8"))

        validate_contract(mapping, "storage_mapping")

        self.assertEqual(mapping["vertical"], "travel_insurance")
        self.assertEqual(mapping["source_collection"], "products")
        self.assertEqual(mapping["identity"]["product_name_field"], "product_name")
        self.assertEqual(mapping["identity"]["product_type_field"], "product_type")
        self.assertEqual(
            set(mapping["product_type_dimensions"]),
            {
                "international_single_trip",
                "international_multi_trip",
                "domestic",
                "inbound",
                "business",
                "cruise",
            },
        )


if __name__ == "__main__":
    unittest.main()
