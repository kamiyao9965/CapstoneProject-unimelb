from __future__ import annotations

import unittest

from src.refine.candidates.aggregator import aggregate_patches
from src.refine.candidates.patch import EvidenceDocument, SchemaPatch


def make_patch(canonical_name: str, source_run: str, **overrides) -> SchemaPatch:
    values = {
        "patch_type": "add_field",
        "target_group": "hospital_cover",
        "field_name": canonical_name,
        "canonical_name": canonical_name,
        "field_type": "string",
        "confidence": 0.8,
        "source_run": source_run,
    }
    values.update(overrides)
    return SchemaPatch(**values)


def patch_across_runs(canonical_name: str, run_count: int, **overrides) -> list[SchemaPatch]:
    return [
        make_patch(canonical_name, f"run_{index:03d}", **overrides)
        for index in range(1, run_count + 1)
    ]


class DecisionThresholdTest(unittest.TestCase):
    def test_classifies_by_frequency_ratio_over_ten_runs(self) -> None:
        patches = (
            patch_across_runs("core_field", 8)
            + patch_across_runs("conditional_field", 5)
            + patch_across_runs("candidate_field", 2)
            + patch_across_runs("noise_field", 1)
        )
        decisions = {d.canonical_name: d for d in aggregate_patches(patches, total_runs=10)}
        self.assertEqual(decisions["core_field"].decision, "core")
        self.assertEqual(decisions["conditional_field"].decision, "conditional")
        self.assertEqual(decisions["candidate_field"].decision, "candidate")
        self.assertEqual(decisions["noise_field"].decision, "noise")

    def test_results_sorted_by_frequency_then_name(self) -> None:
        patches = patch_across_runs("beta", 2) + patch_across_runs("alpha", 2) + patch_across_runs("common", 3)
        names = [d.canonical_name for d in aggregate_patches(patches, total_runs=3)]
        self.assertEqual(names, ["common", "alpha", "beta"])


class AggregationDetailTest(unittest.TestCase):
    def test_reject_field_patches_are_excluded(self) -> None:
        patches = patch_across_runs("keep_me", 3) + [
            make_patch("drop_me", "run_001", patch_type="reject_field")
        ]
        names = [d.canonical_name for d in aggregate_patches(patches, total_runs=3)]
        self.assertEqual(names, ["keep_me"])

    def test_duplicate_patches_from_same_run_count_once(self) -> None:
        patches = [
            make_patch("excess", "run_001"),
            make_patch("excess", "run_001"),
            make_patch("excess", "run_002"),
        ]
        decision = aggregate_patches(patches, total_runs=4)[0]
        self.assertEqual(decision.frequency, 2)
        self.assertEqual(decision.source_runs, ["run_001", "run_002"])

    def test_aliases_collect_diverging_field_names(self) -> None:
        patches = [
            make_patch("annual_limit", "run_001", field_name="annual_limits"),
            make_patch("annual_limit", "run_002", field_name="yearly_limit"),
            make_patch("annual_limit", "run_003"),
        ]
        decision = aggregate_patches(patches, total_runs=3)[0]
        self.assertEqual(decision.aliases, ["annual_limits", "yearly_limit"])

    def test_majority_group_and_type_win(self) -> None:
        patches = [
            make_patch("excess", "run_001", target_group="hospital_cover", field_type="number"),
            make_patch("excess", "run_002", target_group="hospital_cover", field_type="number"),
            make_patch("excess", "run_003", target_group="payments", field_type="string"),
        ]
        decision = aggregate_patches(patches, total_runs=3)[0]
        self.assertEqual(decision.target_group, "hospital_cover")
        self.assertEqual(decision.field_type, "number")
        self.assertTrue(decision.has_conflict)

    def test_average_confidence_ignores_zero_values(self) -> None:
        patches = [
            make_patch("excess", "run_001", confidence=0.6),
            make_patch("excess", "run_002", confidence=1.0),
            make_patch("excess", "run_003", confidence=0.0),
        ]
        decision = aggregate_patches(patches, total_runs=3)[0]
        self.assertAlmostEqual(decision.average_confidence, 0.8)

    def test_reject_votes_attach_without_reducing_frequency(self) -> None:
        patches = patch_across_runs("excess", 8) + [
            make_patch("excess", "run_009", patch_type="reject_field",
                       rationale="Promotional only"),
            make_patch("excess", "run_010", patch_type="reject_field",
                       rationale="Promotional only"),
        ]
        decision = aggregate_patches(patches, total_runs=10)[0]
        self.assertEqual(decision.frequency, 8)
        self.assertEqual(decision.decision, "core")
        self.assertEqual(decision.reject_votes, 2)
        self.assertEqual(decision.reject_votes_label, "2/10")
        self.assertEqual(decision.reject_rationale_samples, ["Promotional only"])

    def test_reject_only_fields_produce_no_decision(self) -> None:
        patches = patch_across_runs("keep_me", 3) + [
            make_patch("never_supported", "run_001", patch_type="reject_field")
        ]
        names = [d.canonical_name for d in aggregate_patches(patches, total_runs=3)]
        self.assertEqual(names, ["keep_me"])

    def test_patch_types_and_rationales_collected(self) -> None:
        patches = [
            make_patch("excess", "run_001", rationale="Seen in all hospital PDFs"),
            make_patch("excess", "run_002", patch_type="update_description",
                       rationale="Wording too vague"),
            make_patch("excess", "run_003", rationale="Seen in all hospital PDFs"),
            make_patch("excess", "run_004", rationale="r3"),
            make_patch("excess", "run_005", rationale="r4"),
        ]
        decision = aggregate_patches(patches, total_runs=5)[0]
        self.assertEqual(decision.patch_types, ["add_field", "update_description"])
        # Deduplicated and capped at 3.
        self.assertEqual(
            decision.rationale_samples,
            ["Seen in all hospital PDFs", "Wording too vague", "r3"],
        )

    def test_evidence_deduplicated_by_path_and_summary(self) -> None:
        shared = EvidenceDocument(path="pdfs/a.pdf", quote_or_summary="Excess $500")
        patches = [
            make_patch("excess", "run_001", evidence_documents=(shared,)),
            make_patch("excess", "run_002", evidence_documents=(shared,)),
            make_patch(
                "excess",
                "run_003",
                evidence_documents=(EvidenceDocument(path="pdfs/b.pdf"),),
            ),
        ]
        decision = aggregate_patches(patches, total_runs=3)[0]
        self.assertEqual(len(decision.evidence_documents), 2)


if __name__ == "__main__":
    unittest.main()
