from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from src.refine.consensus import SchemaConsensusRefinement

BASE_SCHEMA_TEXT = """\
vertical: private_health
version: 0.1-draft
fields:
  - name: product_name
    type: string
    description: Product name
    applies_to: [hospital, extras]
    required: true
    values: []
"""

# Model-shaped patch YAML: run 1 and 2 agree on `excess` (different surface
# names, same canonical name after normalization); only run 1 sees `promo_text`.
PATCH_RUN_1 = """\
patches:
  - patch_type: add_field
    target_group: Hospital
    field_name: Excess Amount
    canonical_name: excess
    type: number
    description: Excess payable per admission
    confidence: 0.9
  - patch_type: add_field
    target_group: marketing
    field_name: promo_text
    type: string
    confidence: 0.2
"""

PATCH_RUN_2 = """\
patches:
  - patch_type: add_field
    target_group: hospital
    field_name: excess
    type: number
    description: Excess payable per admission
    confidence: 0.8
"""


class StubDiscovery:
    """Stands in for SchemaDiscovery; returns canned patch YAML per run."""

    def __init__(self, patch_yaml_per_run: list[str]) -> None:
        self.patch_yaml_per_run = patch_yaml_per_run
        self.calls: list[dict] = []

    def discover_patches(self, sample_pdfs, current_schema, output_path=None) -> str:
        self.calls.append(
            {
                "sample_pdfs": list(sample_pdfs),
                "current_schema": current_schema,
                "output_path": output_path,
            }
        )
        return self.patch_yaml_per_run[len(self.calls) - 1]


class SchemaConsensusRefinementTest(unittest.TestCase):
    def run_consensus(self, tmp: str):
        base_path = Path(tmp) / "schema_draft.yaml"
        base_path.write_text(BASE_SCHEMA_TEXT, encoding="utf-8")
        discovery = StubDiscovery([PATCH_RUN_1, PATCH_RUN_2])
        outputs = SchemaConsensusRefinement(discovery=discovery, log=None).refine(
            base_schema_path=base_path,
            runs=2,
            seed=42,
            samples=["pdfs/a.pdf"],
            base_sample_paths=["pdfs/discovery.pdf"],
            output_dir=Path(tmp) / "consensus",
        )
        return discovery, outputs

    def test_runs_patch_generation_once_per_run_with_base_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            discovery, _ = self.run_consensus(tmp)
            self.assertEqual(len(discovery.calls), 2)
            for call in discovery.calls:
                self.assertEqual(call["sample_pdfs"], ["pdfs/a.pdf"])
                self.assertIn("product_name", call["current_schema"])

    def test_writes_all_consensus_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, outputs = self.run_consensus(tmp)
            self.assertTrue((outputs.patch_dir / "run_001.yaml").exists())
            self.assertTrue((outputs.patch_dir / "run_002.yaml").exists())
            self.assertTrue(outputs.consensus_schema_path.exists())
            self.assertTrue(outputs.frequency_path.exists())
            self.assertTrue(outputs.report_path.exists())
            self.assertTrue(outputs.stability_path.exists())
            self.assertTrue(outputs.queue_path.exists())

    def test_review_queue_and_stability_derive_from_patch_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, outputs = self.run_consensus(tmp)
            queue = yaml.safe_load(outputs.queue_path.read_text(encoding="utf-8"))
            self.assertEqual(
                queue["metadata"]["consensus_source"], "candidate_schema_patches"
            )
            self.assertEqual(queue["metadata"]["total_runs"], 2)
            self.assertEqual(
                queue["metadata"]["schema_build_samples"],
                ["pdfs/discovery.pdf", "pdfs/a.pdf"],
            )
            self.assertEqual(
                outputs.schema_build_samples,
                ("pdfs/discovery.pdf", "pdfs/a.pdf"),
            )
            ids = [item["id"] for item in queue["updates"]]
            self.assertIn("field:excess", ids)

            stability = yaml.safe_load(
                outputs.stability_path.read_text(encoding="utf-8")
            )
            fields = stability["dimensions"]["fields"]
            # excess appears in both runs; promo_text drifts (run 1 only).
            self.assertIn("excess", fields["stable_items"])
            self.assertIn("promo_text", fields["drifting_items"])

    def test_votes_across_runs_after_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, outputs = self.run_consensus(tmp)
            decisions = {d.canonical_name: d for d in outputs.decisions}
            # `Excess Amount` (run 1) and `excess` (run 2) normalize to the same
            # canonical field, so it is seen in 2/2 runs -> core.
            self.assertEqual(decisions["excess"].frequency, 2)
            self.assertEqual(decisions["excess"].decision, "core")
            self.assertEqual(decisions["excess"].target_group, "hospital_cover")
            # promo_text only appears in 1/2 runs -> conditional at 0.5 ratio.
            self.assertEqual(decisions["promo_text"].frequency, 1)

    def test_consensus_schema_promotes_voted_fields_and_keeps_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, outputs = self.run_consensus(tmp)
            schema = yaml.safe_load(
                outputs.consensus_schema_path.read_text(encoding="utf-8")
            )
            names = [field["name"] for field in schema["fields"]]
            self.assertIn("product_name", names)
            self.assertIn("excess", names)

    def test_rejects_non_positive_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base_path = Path(tmp) / "schema_draft.yaml"
            base_path.write_text(BASE_SCHEMA_TEXT, encoding="utf-8")
            refinement = SchemaConsensusRefinement(discovery=StubDiscovery([]), log=None)
            with self.assertRaises(ValueError):
                refinement.refine(base_schema_path=base_path, runs=0)

    def test_missing_base_schema_raises(self) -> None:
        refinement = SchemaConsensusRefinement(discovery=StubDiscovery([]), log=None)
        with self.assertRaises(FileNotFoundError):
            refinement.refine(base_schema_path="does/not/exist.yaml", runs=1)


if __name__ == "__main__":
    unittest.main()
