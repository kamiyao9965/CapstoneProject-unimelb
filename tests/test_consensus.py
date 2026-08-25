from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.common.json_artifacts import build_success_artifact, read_artifact, write_artifact
from src.common.model_config import ModelSelection
from src.refine.consensus import SchemaConsensusRefinement

BASE_SCHEMA = {
    "vertical": "private_health", "version": "0.1-draft",
    "description": "Schema", "product_types": ["hospital", "extras"],
    "fields": [{"name": "product_type", "type": "enum",
                "description": "Product classification",
                "applies_to": ["hospital", "extras"], "required": True,
                "values": ["hospital", "extras"], "aliases": []},
               {"name": "product_name", "type": "string",
                "description": "Product name", "applies_to": ["hospital", "extras"],
                "required": True, "values": [], "aliases": []}],
    "hospital_categories": [], "extras_services": [], "notes": [],
}

# Model-shaped patch JSON: run 1 and 2 agree on `excess` (different surface
# names, same canonical name after normalization); only run 1 sees `promo_text`.
def patch(name: str, group: str, confidence: float, description: str) -> dict:
    return {
        "patch_type": "add_field", "target_group": group, "field_name": name,
        "canonical_name": "excess" if "excess" in name.lower() else name,
        "type": "number" if "excess" in name.lower() else "string",
        "description": description, "applies_to": ["hospital"],
        "required": False, "values": [], "evidence_documents": [],
        "confidence": confidence, "rationale": "",
    }

PATCH_RUN_1 = {"patches": [
    patch("Excess Amount", "Hospital", 0.9, "Excess payable per admission"),
    patch("promo_text", "marketing", 0.2, "Promotional text"),
]}
PATCH_RUN_2 = {"patches": [
    patch("excess", "hospital", 0.8, "Excess payable per admission"),
]}


class StubDiscovery:
    """Stands in for SchemaDiscovery; returns canned patch JSON per run."""

    def __init__(self, patch_data_per_run: list[dict]) -> None:
        self.patch_data_per_run = patch_data_per_run
        self.calls: list[dict] = []
        self.selection = ModelSelection("openai", "gpt-5", "pdf")

    def discover_patches(
        self, sample_pdfs, current_schema, output_path=None, *, run_id=None,
    ) -> dict:
        self.calls.append(
            {
                "sample_pdfs": list(sample_pdfs),
                "current_schema": current_schema,
                "output_path": output_path,
                "run_id": run_id,
            }
        )
        return self.patch_data_per_run[len(self.calls) - 1]


class SchemaConsensusRefinementTest(unittest.TestCase):
    def run_consensus(self, tmp: str):
        base_path = Path(tmp) / "schema_draft.json"
        artifact = build_success_artifact(
            artifact_type="discovered_schema", contract_version="1.0.0",
            data=BASE_SCHEMA,
            provenance={"run_id": "base", "provider": "openai", "model": "gpt-5",
                        "document_input": "pdf", "source_documents": [], "source_artifacts": []},
            data_contract="private_health/discovered_schema",
        )
        write_artifact(base_path, artifact, data_contract="private_health/discovered_schema")
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
                self.assertIn(
                    "product_name",
                    [field["name"] for field in call["current_schema"]["fields"]],
                )
            self.assertEqual(
                [call["run_id"] for call in discovery.calls],
                ["consensus-001", "consensus-002"],
            )

    def test_writes_all_consensus_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, outputs = self.run_consensus(tmp)
            self.assertTrue((outputs.patch_dir / "run_001.json").exists())
            self.assertTrue((outputs.patch_dir / "run_002.json").exists())
            self.assertTrue(outputs.consensus_schema_path.exists())
            self.assertTrue(outputs.frequency_path.exists())
            self.assertIn("# Schema Consensus Report", outputs.report)
            self.assertTrue(outputs.stability_path.exists())
            self.assertTrue(outputs.queue_path.exists())

    def test_review_queue_and_stability_derive_from_patch_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, outputs = self.run_consensus(tmp)
            queue = read_artifact(
                outputs.queue_path, expected_type="review_queue",
                data_contract="private_health/review_queue",
            )["data"]
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
            self.assertIn("field:promo_text", ids)

            stability = read_artifact(
                outputs.stability_path, expected_type="patch_stability",
                data_contract="private_health/patch_stability",
            )["data"]
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
            schema = read_artifact(
                outputs.consensus_schema_path, expected_type="discovered_schema",
                data_contract="private_health/discovered_schema",
            )["data"]
            names = [field["name"] for field in schema["fields"]]
            self.assertIn("product_name", names)
            self.assertIn("excess", names)

    def test_rejects_non_positive_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base_path = Path(tmp) / "schema_draft.json"
            base_path.touch()
            refinement = SchemaConsensusRefinement(discovery=StubDiscovery([]), log=None)
            with self.assertRaises(ValueError):
                refinement.refine(base_schema_path=base_path, runs=0)

    def test_missing_base_schema_raises(self) -> None:
        refinement = SchemaConsensusRefinement(discovery=StubDiscovery([]), log=None)
        with self.assertRaises(FileNotFoundError):
            refinement.refine(base_schema_path="does/not/exist.json", runs=1)


if __name__ == "__main__":
    unittest.main()
