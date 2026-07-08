from __future__ import annotations

import unittest

from src.refine.patch import SchemaPatch
from src.refine.patch_stability import compute_patch_stability


def patch(canonical_name: str, source_run: str, patch_type: str = "add_field") -> SchemaPatch:
    return SchemaPatch(
        patch_type=patch_type,
        target_group="hospital_cover",
        field_name=canonical_name,
        canonical_name=canonical_name,
        source_run=source_run,
    )


class ComputePatchStabilityTest(unittest.TestCase):
    def test_intersection_over_union_matches_doc_example(self) -> None:
        patches = [
            patch("annual_limit", "run_001"), patch("excess", "run_001"), patch("waiting_period", "run_001"),
            patch("annual_limit", "run_002"), patch("excess", "run_002"), patch("provider_network", "run_002"),
            patch("annual_limit", "run_003"), patch("excess", "run_003"), patch("waiting_period", "run_003"),
        ]
        result = compute_patch_stability(patches, total_runs=3)
        fields = result["dimensions"]["fields"]
        self.assertEqual(fields["stable_items"], ["annual_limit", "excess"])
        self.assertEqual(fields["drifting_items"], ["provider_network", "waiting_period"])
        self.assertEqual(fields["union_count"], 4)
        self.assertEqual(fields["stability"], 0.5)
        self.assertEqual(result["metadata"]["stability_source"], "candidate_schema_patches")

    def test_reject_only_mentions_are_not_presence(self) -> None:
        patches = [
            patch("excess", "run_001"),
            patch("excess", "run_002", patch_type="reject_field"),
        ]
        result = compute_patch_stability(patches, total_runs=2)
        fields = result["dimensions"]["fields"]
        # run_002 contributed nothing (its only mention was a reject), so the
        # stable core is empty even though excess appears in the union.
        self.assertEqual(fields["stable_items"], [])
        self.assertEqual(fields["drifting_items"], ["excess"])

    def test_run_without_patches_empties_stable_core(self) -> None:
        patches = [patch("excess", "run_001"), patch("excess", "run_002")]
        result = compute_patch_stability(patches, total_runs=3)
        self.assertEqual(result["dimensions"]["fields"]["stable_items"], [])
        self.assertEqual(result["dimensions"]["fields"]["union_count"], 1)

    def test_empty_input(self) -> None:
        result = compute_patch_stability([], total_runs=2)
        fields = result["dimensions"]["fields"]
        self.assertEqual(fields["union_count"], 0)
        self.assertEqual(fields["stability"], 0.0)


if __name__ == "__main__":
    unittest.main()
