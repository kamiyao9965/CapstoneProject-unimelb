from __future__ import annotations

import unittest

from src.schema.canonical import approve_canonical_schema
from src.storage.canonical import preview_vertical_storage_metadata
from src.verticals.travel_insurance import build_travel_canonical_candidate


def reviewed_travel_schema() -> dict[str, object]:
    product_types = ["international_single_trip", "domestic"]
    return {
        "vertical": "travel_insurance",
        "version": "0.2-reviewed",
        "description": "Reviewed Travel schema.",
        "product_types": product_types,
        "product_type_field": {
            "name": "product_type",
            "type": "enum",
            "description": "Travel product classification.",
            "applies_to": product_types,
            "required": True,
            "values": product_types,
            "aliases": [],
        },
        "fields": [
            {
                "name": "product_name",
                "type": "string",
                "description": "Insurer-issued product or plan name.",
                "applies_to": product_types,
                "required": True,
                "values": [],
                "aliases": ["plan name"],
            },
            {
                "name": "geographic_scope",
                "type": "enum",
                "description": "Geographic scope of cover.",
                "applies_to": product_types,
                "required": False,
                "values": ["international", "domestic"],
                "aliases": [],
            },
            {
                "name": "benefits",
                "type": "list[object]",
                "description": "Benefit and limit records.",
                "applies_to": product_types,
                "required": False,
                "values": [],
                "aliases": [],
            },
        ],
        "coverage_categories": [],
        "notes": [],
    }


class TravelCanonicalReviewTests(unittest.TestCase):
    def test_builds_deterministic_candidate_mapping_without_approval(self) -> None:
        candidate = build_travel_canonical_candidate(reviewed_travel_schema())

        self.assertEqual(candidate["status"], "candidate")
        self.assertIsNone(candidate["review"])
        fields = {field["name"]: field for field in candidate["fields"]}
        self.assertEqual(
            fields["product_name"]["storage"],
            {"strategy": "core_column", "target": "products.canonical_name"},
        )
        self.assertEqual(
            fields["product_type"]["storage"],
            {
                "strategy": "core_column",
                "target": "product_releases.source_product_type",
            },
        )
        self.assertEqual(
            fields["geographic_scope"]["storage"],
            {"strategy": "extension_column", "column": "geographic_scope"},
        )
        self.assertEqual(fields["benefits"]["storage"], {"strategy": "jsonb"})

    def test_candidate_can_preview_ddl_but_not_cross_production_gate(self) -> None:
        candidate = build_travel_canonical_candidate(reviewed_travel_schema())

        compiled = preview_vertical_storage_metadata(candidate)
        self.assertEqual(compiled.table.name, "travel_product_details")

    def test_explicit_approval_requires_reviewer_and_rationale(self) -> None:
        candidate = build_travel_canonical_candidate(reviewed_travel_schema())

        with self.assertRaisesRegex(ValueError, "reviewer"):
            approve_canonical_schema(candidate, reviewer="", rationale="checked")
        approved = approve_canonical_schema(
            candidate,
            reviewer="schema-owner",
            rationale="Reviewed field meaning and deterministic storage mapping.",
            reviewed_at="2026-08-24T10:00:00+10:00",
        )
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["review"]["reviewed_by"], "schema-owner")
        self.assertEqual(candidate["status"], "candidate")


if __name__ == "__main__":
    unittest.main()
