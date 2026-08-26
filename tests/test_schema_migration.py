from __future__ import annotations

import json
import unittest

from src.schema.contract import compile_extraction_contract
from src.schema.migration import migrate_legacy_discovered_schema
from src.schema.validation import validate_schema_mapping
from tests.test_json_contracts import VALID_DISCOVERED_SCHEMA


class LegacyDiscoveredSchemaMigrationTest(unittest.TestCase):
    def test_migrates_old_extras_waiting_period_shape_without_mutating_input(self) -> None:
        legacy = json.loads(json.dumps(VALID_DISCOVERED_SCHEMA))
        legacy["fields"].append({
            "name": "extras_waiting_periods", "type": "list[object]",
            "description": "Waiting periods from p3_t1.",
            "applies_to": ["extras", "generalhealth"], "required": False,
            "values": [], "aliases": [], "enum_ref": None,
            "unique_items": False,
            "item_fields": [{
                "name": "service", "type": "string", "required": False,
                "description": "Service label shown on page 3.",
                "values": [], "enum_ref": None,
            }],
        })

        migrated = migrate_legacy_discovered_schema(legacy)
        item = migrated["fields"][-1]["item_fields"][0]

        self.assertEqual(legacy["fields"][-1]["item_fields"][0]["name"], "service")
        self.assertEqual(item["name"], "service_name")
        self.assertEqual(item["type"], "enum")
        self.assertTrue(item["required"])
        self.assertEqual(item["values"], [])
        self.assertEqual(item["enum_ref"], "extras_services")
        self.assertNotIn("p3_t1", migrated["fields"][-1]["description"])
        self.assertNotIn("page 3", item["description"])
        self.assertEqual(migrate_legacy_discovered_schema(migrated), migrated)
        self.assertIs(validate_schema_mapping(migrated), migrated)

        contract = compile_extraction_contract(legacy)
        migrated_item = contract["properties"]["extras_waiting_periods"]
        self.assertIn("oneOf", migrated_item)

    def test_adds_required_extras_taxonomy_and_reassigns_root_canal(self) -> None:
        legacy = json.loads(json.dumps(VALID_DISCOVERED_SCHEMA))
        legacy["extras_services"] = [{
            "canonical_name": "major_dental",
            "aliases": ["crowns", "root canal", "endodontic"],
            "description": "Complex dental.",
        }]
        legacy["fields"].append({
            "name": "extras_benefits", "type": "list[object]",
            "description": "Covered extras.", "applies_to": ["extras"],
            "required": False, "values": [], "aliases": [], "enum_ref": None,
            "unique_items": False,
            "item_fields": [{
                "name": "service_name", "type": "enum", "required": True,
                "description": "Canonical service.", "values": [],
                "enum_ref": "extras_services",
            }],
        })

        migrated = migrate_legacy_discovered_schema(legacy)
        services = {
            item["canonical_name"]: item for item in migrated["extras_services"]
        }

        self.assertEqual(
            {"endodontic", "vaccinations", "glucose_monitor"} - set(services),
            set(),
        )
        self.assertNotIn("root canal", services["major_dental"]["aliases"])
        self.assertNotIn("endodontic", services["major_dental"]["aliases"])
        self.assertIn("root canal", services["endodontic"]["aliases"])
        self.assertEqual(migrate_legacy_discovered_schema(migrated), migrated)


if __name__ == "__main__":
    unittest.main()
