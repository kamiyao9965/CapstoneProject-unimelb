from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from src.refine.aggregator import FieldDecision
from src.refine.renderer import (
    render_consensus_schema,
    render_frequency_yaml,
    render_report,
)

BASE_SCHEMA = {
    "vertical": "private_health",
    "version": "0.1-draft",
    "fields": [
        {
            "name": "product_name",
            "type": "string",
            "description": "Existing description",
            "applies_to": ["hospital", "extras"],
            "required": True,
            "values": [],
        }
    ],
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
    }
    values.update(overrides)
    return FieldDecision(**values)


class RenderConsensusSchemaTest(unittest.TestCase):
    def render(self, decisions: list[FieldDecision]) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            base_path = Path(tmp) / "base.yaml"
            base_path.write_text(yaml.safe_dump(BASE_SCHEMA), encoding="utf-8")
            out_path = Path(tmp) / "consensus_schema.yaml"
            render_consensus_schema(base_path, decisions, out_path)
            return yaml.safe_load(out_path.read_text(encoding="utf-8"))

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

    def test_new_core_field_is_required_and_annotated(self) -> None:
        schema = self.render([make_decision("excess", "core")])
        field = next(f for f in schema["fields"] if f["name"] == "excess")
        self.assertTrue(field["required"])
        self.assertEqual(field["consensus"]["decision"], "core")
        self.assertEqual(field["consensus"]["frequency"], "4/5")

    def test_new_field_maps_group_to_product_type(self) -> None:
        schema = self.render(
            [make_decision("annual_limit", "core", target_group="extras_cover")]
        )
        field = next(f for f in schema["fields"] if f["name"] == "annual_limit")
        self.assertEqual(field["applies_to"], ["extras"])

    def test_new_conditional_field_is_not_required(self) -> None:
        schema = self.render([make_decision("ambulance_cover", "conditional")])
        field = next(f for f in schema["fields"] if f["name"] == "ambulance_cover")
        self.assertFalse(field["required"])

    def test_schema_level_consensus_metadata_written(self) -> None:
        schema = self.render([make_decision("excess", "core")])
        self.assertEqual(schema["consensus"]["promoted_decisions"], ["conditional", "core"])
        self.assertIn("generated_at", schema["consensus"])


class RenderFrequencyAndReportTest(unittest.TestCase):
    def test_frequency_yaml_lists_all_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "field_frequency.yaml"
            render_frequency_yaml(
                [make_decision("excess", "core"), make_decision("promo_text", "noise")],
                out_path,
            )
            payload = yaml.safe_load(out_path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["fields"]), 2)
            self.assertEqual(payload["fields"][0]["field"], "excess")
            self.assertEqual(payload["fields"][0]["frequency"], "4/5")

    def test_report_contains_summary_table_and_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "consensus_report.md"
            render_report([make_decision("excess", "core")], out_path)
            text = out_path.read_text(encoding="utf-8")
            self.assertIn("# Schema Consensus Report", text)
            self.assertIn("| excess | hospital_cover | 4/5 | core | 0.750 |", text)
            self.assertIn("### excess", text)


if __name__ == "__main__":
    unittest.main()
