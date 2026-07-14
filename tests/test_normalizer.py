from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from src.refine.candidates.aggregator import aggregate_patches
from src.refine.candidates.normalizer import (
    DEFAULT_ALIAS_CONFIG,
    canonical_field_name,
    canonical_group_name,
    load_alias_config,
    normalize_patch,
)
from src.refine.candidates.patch import SchemaPatch


def make_patch(**overrides) -> SchemaPatch:
    values = {
        "patch_type": "add_field",
        "target_group": "Hospital",
        "field_name": "Annual Benefit Limit",
        "canonical_name": "",
        "field_type": "number",
    }
    values.update(overrides)
    return SchemaPatch(**values)


class CanonicalFieldNameTest(unittest.TestCase):
    def test_snake_cases_spaces_and_camel_case(self) -> None:
        self.assertEqual(canonical_field_name("Waiting Period"), "waiting_period")
        self.assertEqual(canonical_field_name("waitingPeriod"), "waiting_period")
        self.assertEqual(canonical_field_name("  waiting--period  "), "waiting_period")

    def test_applies_default_aliases_after_snake_casing(self) -> None:
        self.assertEqual(canonical_field_name("Annual Benefit Limit"), "annual_limit")
        self.assertEqual(canonical_field_name("company_name"), "fund_name")

    def test_custom_aliases_replace_defaults(self) -> None:
        aliases = {"tariff": "premium"}
        self.assertEqual(canonical_field_name("Tariff", aliases), "premium")
        # Default aliases must not leak through when a custom map is supplied.
        self.assertEqual(canonical_field_name("company_name", aliases), "company_name")


class CanonicalGroupNameTest(unittest.TestCase):
    def test_applies_default_group_aliases(self) -> None:
        self.assertEqual(canonical_group_name("Hospital"), "hospital_cover")
        self.assertEqual(canonical_group_name("general_health"), "generalhealth_cover")

    def test_unknown_group_is_snake_cased_only(self) -> None:
        self.assertEqual(canonical_group_name("Member Services"), "member_services")


class NormalizePatchTest(unittest.TestCase):
    def test_normalizes_names_and_fills_canonical_from_field_name(self) -> None:
        normalized = normalize_patch(make_patch())
        self.assertEqual(normalized.field_name, "annual_benefit_limit")
        self.assertEqual(normalized.canonical_name, "annual_limit")
        self.assertEqual(normalized.target_group, "hospital_cover")

    def test_preserves_non_name_attributes(self) -> None:
        patch = make_patch(confidence=0.7, rationale="seen everywhere", source_run="run_003")
        normalized = normalize_patch(patch)
        self.assertEqual(normalized.confidence, 0.7)
        self.assertEqual(normalized.rationale, "seen everywhere")
        self.assertEqual(normalized.source_run, "run_003")
        self.assertEqual(normalized.patch_type, "add_field")

    def test_explicit_canonical_name_is_still_normalized(self) -> None:
        normalized = normalize_patch(make_patch(canonical_name="Yearly Limit"))
        self.assertEqual(normalized.canonical_name, "annual_limit")

    def test_observed_alias_survives_normalization_and_aggregation(self) -> None:
        normalized = normalize_patch(make_patch(source_run="run_001"))

        decision = aggregate_patches([normalized], total_runs=1)[0]

        self.assertEqual(normalized.field_name, "annual_benefit_limit")
        self.assertEqual(decision.canonical_name, "annual_limit")
        self.assertEqual(decision.aliases, ["annual_benefit_limit"])


class LoadAliasConfigTest(unittest.TestCase):
    def test_default_config_file_is_the_authoritative_default(self) -> None:
        self.assertTrue(DEFAULT_ALIAS_CONFIG.exists())
        field_aliases, group_aliases = load_alias_config()
        self.assertEqual(field_aliases["company_name"], "fund_name")
        self.assertEqual(group_aliases["hospital"], "hospital_cover")

    def test_loaded_aliases_drive_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "aliases.json"
            config.write_text(json.dumps({
                "field_aliases": {"tariff": "premium"},
                "group_aliases": {"bills": "billing"},
            }), encoding="utf-8")
            field_aliases, group_aliases = load_alias_config(config)
            self.assertEqual(canonical_field_name("Tariff", field_aliases), "premium")
            self.assertEqual(canonical_group_name("Bills", group_aliases), "billing")

    def test_missing_explicit_path_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_alias_config("does/not/exist.json")

    def test_missing_sections_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "aliases.json"
            config.write_text(json.dumps({"field_aliases": {"a": "b"}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_alias_config(config)


if __name__ == "__main__":
    unittest.main()
