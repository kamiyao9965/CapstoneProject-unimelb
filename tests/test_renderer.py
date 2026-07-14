from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.common.json_artifacts import build_success_artifact, read_artifact, write_artifact
from src.refine.artifacts.renderer import (
    render_consensus_schema,
    render_frequency_json,
    render_report,
)
from src.refine.candidates.aggregator import FieldDecision

BASE_SCHEMA = {
    "vertical": "private_health",
    "version": "0.1-draft",
    "description": "Schema",
    "product_types": ["hospital", "extras", "combined"],
    "fields": [
        {
            "name": "product_type",
            "type": "enum",
            "description": "Product classification",
            "applies_to": ["hospital", "extras", "combined"],
            "required": True,
            "values": ["hospital", "extras", "combined"],
            "aliases": [],
        },
        {
            "name": "product_name",
            "type": "string",
            "description": "Existing description",
            "applies_to": ["hospital", "extras"],
            "required": True,
            "values": [],
            "aliases": [],
        }
    ],
    "hospital_categories": [],
    "extras_services": [],
    "notes": [],
}

PROVENANCE = {
    "run_id": "test", "provider": "openai", "model": "gpt-5",
    "document_input": "pdf", "source_documents": [], "source_artifacts": [],
}


def make_decision(name: str, decision: str, **overrides) -> FieldDecision:
    values = {
        "canonical_name": name,
        "target_group": "hospital_cover",
        "field_type": "number",
        "description": f"{name} description",
        "frequency": 4,
        "total_runs": 5,
        "decision": decision,
        "aliases": [],
        "source_runs": ["run_001"],
        "average_confidence": 0.75,
        "patch_types": ["add_field"],
        "applies_to": ["hospital"],
        "required": False,
        "values": [],
    }
    values.update(overrides)
    return FieldDecision(**values)


class RenderConsensusSchemaTest(unittest.TestCase):
    def render(self, decisions: list[FieldDecision]) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            base_path = Path(tmp) / "base.json"
            base_artifact = build_success_artifact(
                artifact_type="discovered_schema", contract_version="1.0.0",
                data=BASE_SCHEMA, provenance=PROVENANCE,
                data_contract="private_health/discovered_schema",
            )
            write_artifact(
                base_path, base_artifact, data_contract="private_health/discovered_schema"
            )
            out_path = Path(tmp) / "consensus_schema.json"
            render_consensus_schema(base_path, decisions, out_path)
            return read_artifact(
                out_path, expected_type="discovered_schema",
                data_contract="private_health/discovered_schema",
            )["data"]

    def test_promotes_core_and_conditional_only(self) -> None:
        schema = self.render(
            [
                make_decision("excess", "core"),
                make_decision("ambulance_cover", "conditional"),
                make_decision("wellness_bonus", "candidate"),
                make_decision("promo_text", "noise"),
            ]
        )
        names = [field["name"] for field in schema["fields"]]
        self.assertIn("excess", names)
        self.assertIn("ambulance_cover", names)
        self.assertNotIn("wellness_bonus", names)
        self.assertNotIn("promo_text", names)

    def test_preserves_existing_base_fields_and_metadata(self) -> None:
        schema = self.render(
            [make_decision("product_name", "core", description="New description")]
        )
        field = next(f for f in schema["fields"] if f["name"] == "product_name")
        # Existing type/description/applies_to win over the decision's values.
        self.assertEqual(field["type"], "string")
        self.assertEqual(field["description"], "Existing description")
        self.assertEqual(field["applies_to"], ["hospital", "extras"])
        self.assertTrue(field["required"])

    def test_new_core_field_is_not_assumed_required(self) -> None:
        schema = self.render([make_decision("excess", "core")])
        field = next(f for f in schema["fields"] if f["name"] == "excess")
        self.assertFalse(field["required"])
        self.assertEqual(field["aliases"], [])

    def test_new_field_maps_group_to_product_type(self) -> None:
        schema = self.render(
            [
                make_decision(
                    "annual_limit",
                    "core",
                    target_group="extras_cover",
                    applies_to=[],
                )
            ]
        )
        field = next(f for f in schema["fields"] if f["name"] == "annual_limit")
        self.assertEqual(field["applies_to"], ["extras"])

    def test_new_conditional_field_is_not_required(self) -> None:
        schema = self.render([make_decision("ambulance_cover", "conditional")])
        field = next(f for f in schema["fields"] if f["name"] == "ambulance_cover")
        self.assertFalse(field["required"])

    def test_new_enum_field_preserves_allowed_values(self) -> None:
        schema = self.render(
            [
                make_decision(
                    "cover_status",
                    "core",
                    field_type="enum",
                    values=["included", "excluded"],
                )
            ]
        )
        field = next(f for f in schema["fields"] if f["name"] == "cover_status")
        self.assertEqual(field["values"], ["included", "excluded"])

    def test_update_description_changes_existing_field_description(self) -> None:
        schema = self.render(
            [
                make_decision(
                    "product_name",
                    "core",
                    patch_types=["update_description"],
                    description="Clarified product name",
                    applies_to=["hospital", "extras"],
                )
            ]
        )
        field = next(f for f in schema["fields"] if f["name"] == "product_name")
        self.assertEqual(field["description"], "Clarified product name")

    def test_add_alias_merges_aliases_into_existing_field(self) -> None:
        schema = self.render(
            [
                make_decision(
                    "product_name",
                    "core",
                    patch_types=["add_alias"],
                    aliases=["plan_name"],
                    applies_to=["hospital", "extras"],
                )
            ]
        )
        field = next(f for f in schema["fields"] if f["name"] == "product_name")
        self.assertEqual(field["aliases"], ["plan_name"])

    def test_unknown_group_is_not_auto_promoted(self) -> None:
        schema = self.render(
            [
                make_decision(
                    "member_note",
                    "core",
                    target_group="member_services",
                    applies_to=[],
                )
            ]
        )
        self.assertNotIn("member_note", [field["name"] for field in schema["fields"]])

    def test_reject_votes_prevent_unattended_auto_merge(self) -> None:
        schema = self.render([make_decision("excess", "core", reject_votes=1)])
        self.assertNotIn("excess", [field["name"] for field in schema["fields"]])

    def test_mixed_patch_actions_are_not_auto_promoted(self) -> None:
        schema = self.render(
            [
                make_decision(
                    "excess",
                    "core",
                    patch_types=["add_field", "update_description"],
                )
            ]
        )
        self.assertNotIn("excess", [field["name"] for field in schema["fields"]])

    def test_consensus_metadata_stays_in_frequency_artifact_not_schema(self) -> None:
        schema = self.render([make_decision("excess", "core")])
        self.assertNotIn("consensus", schema)

    def test_manual_edit_patch_types_are_not_auto_promoted(self) -> None:
        schema = self.render(
            [
                make_decision("renamed_field", "core", patch_types=["rename_field"]),
                make_decision("merged_field", "core", patch_types=["merge_fields"]),
                make_decision("moved_field", "core", patch_types=["move_field_group"]),
            ]
        )

        names = [field["name"] for field in schema["fields"]]
        self.assertNotIn("renamed_field", names)
        self.assertNotIn("merged_field", names)
        self.assertNotIn("moved_field", names)


class RenderFrequencyAndReportTest(unittest.TestCase):
    def test_frequency_json_lists_all_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "field_frequency.json"
            render_frequency_json(
                [make_decision("excess", "core"), make_decision("promo_text", "noise")],
                out_path,
            )
            payload = read_artifact(
                out_path, expected_type="field_frequency",
                data_contract="private_health/field_frequency",
            )["data"]
            self.assertEqual(len(payload["fields"]), 2)
            self.assertEqual(payload["fields"][0]["field"], "excess")
            self.assertEqual(payload["fields"][0]["frequency"], "4/5")

    def test_report_contains_summary_table_and_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            text = render_report([make_decision("excess", "core")])
            self.assertIn("# Schema Consensus Report", text)
            self.assertIn("| excess | hospital_cover | 4/5 | core | 0.750 |", text)
            self.assertIn("### excess", text)


if __name__ == "__main__":
    unittest.main()
