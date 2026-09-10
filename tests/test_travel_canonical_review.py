from __future__ import annotations

import unittest

from src.schema.canonical import approve_canonical_schema
from src.storage.canonical import preview_vertical_storage_metadata
from src.schema.canonical import build_canonical_candidate
from src.verticals.manifest import resolve_manifest


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
        candidate = build_canonical_candidate(reviewed_travel_schema(), resolve_manifest(vertical="travel_insurance"))

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
        candidate = build_canonical_candidate(reviewed_travel_schema(), resolve_manifest(vertical="travel_insurance"))

        compiled = preview_vertical_storage_metadata(candidate)
        self.assertEqual(compiled.table.name, "travel_product_details")

    def test_explicit_approval_requires_reviewer_and_rationale(self) -> None:
        candidate = build_canonical_candidate(reviewed_travel_schema(), resolve_manifest(vertical="travel_insurance"))

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

    def test_approved_contract_is_unchanged_and_unknown_fields_need_mapping_review(self):
        manifest = resolve_manifest(vertical="travel_insurance")
        before = manifest.path("canonical_schema").read_bytes()
        schema = reviewed_travel_schema()
        schema["fields"].append({"name":"new_benefit", "type":"number", "description":"New benefit",
                                 "applies_to":schema["product_types"], "required":False,"values":[],"aliases":[]})
        candidate = build_canonical_candidate(schema, manifest)
        self.assertEqual(manifest.path("canonical_schema").read_bytes(), before)
        self.assertEqual(next(f for f in candidate["fields"] if f["name"] == "new_benefit")["storage"], {"strategy":"jsonb"})
        self.assertEqual(candidate["status"], "candidate")

    def test_mapping_ui_resets_approval_when_source_or_output_changes(self):
        import json
        import tempfile
        from pathlib import Path
        from streamlit.testing.v1 import AppTest
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "schema.json"
            source.write_text(json.dumps(reviewed_travel_schema()))
            at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "src/canonical_review_app.py"), default_timeout=20).run()
            at.sidebar.text_input[0].set_value(str(source)).run()
            at.sidebar.text_input[1].set_value(str(root / "approved.json")).run()
            self.assertFalse(at.exception)
            next(f for f in at.text_input if f.label == "Reviewer").set_value("fixture reviewer").run()
            at.text_area[0].set_value("Reviewed fixture fields and mappings").run()
            at.checkbox[0].check().run()
            self.assertFalse(at.button[0].disabled)
            at.sidebar.text_input[1].set_value(str(root / "different.json")).run()
            self.assertTrue(at.button[0].disabled)
            self.assertFalse(at.checkbox[0].value)
            self.assertFalse((root / "approved.json").exists())
            source.write_text(json.dumps({**reviewed_travel_schema(), "vertical":"private_health"}))
            at.run()
            self.assertFalse(at.exception)
            self.assertTrue(at.error)
            self.assertFalse(at.button)


if __name__ == "__main__":
    unittest.main()
