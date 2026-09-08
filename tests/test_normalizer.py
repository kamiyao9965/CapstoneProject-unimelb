import unittest
from unittest import mock
from src.refine.candidates.normalizer import canonical_field_name, normalize_patches
from src.refine.candidates.patch import SchemaPatch
from src.refine.candidates.aggregator import aggregate_patches

class NormalizerTest(unittest.TestCase):
    def test_spelling_normalization_never_reads_alias_files(self):
        with mock.patch("pathlib.Path.read_text", side_effect=AssertionError("unexpected file read")):
            self.assertEqual(canonical_field_name("WaitingPeriod"), "waiting_period")
            self.assertEqual(canonical_field_name("Annual Benefit Limit"), "annual_benefit_limit")
            self.assertEqual(canonical_field_name("company_name"), "company_name")

    def test_distinct_names_have_separate_votes(self):
        patches = [SchemaPatch("add_field", "hospital", name, name, source_run=str(i))
                   for i, name in enumerate(("annual_limit", "yearly_limit"))]
        decisions = aggregate_patches(normalize_patches(patches), total_runs=2)
        self.assertEqual(len(decisions), 2)
        self.assertTrue(all(d.frequency_label == "1/2" for d in decisions))
        self.assertTrue(all(d.aliases == [] for d in decisions))

    def test_alias_patch_is_not_executable(self):
        with self.assertRaisesRegex(ValueError, "Unsupported patch_type"):
            SchemaPatch("add_alias", "hospital", "name", "product_name").validate()
