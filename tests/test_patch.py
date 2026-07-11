from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.refine.candidates.patch import (
    EvidenceDocument,
    SchemaPatch,
    dump_yaml,
    load_patch_file,
    load_yaml,
    parse_patch_payload,
    parse_yaml_text,
)


def make_patch_dict(**overrides) -> dict:
    payload = {
        "patch_type": "add_field",
        "target_group": "hospital_cover",
        "field_name": "excess_amount",
        "canonical_name": "excess",
        "type": "number",
        "description": "Excess payable per admission",
        "applies_to": ["hospital"],
        "required": False,
        "values": [],
        "evidence_documents": [
            {"path": "pdfs/a.pdf", "quote_or_summary": "Excess $500"},
        ],
        "confidence": 0.9,
        "rationale": "Appears in all hospital PDFs",
    }
    payload.update(overrides)
    return payload


class SchemaPatchFromDictTest(unittest.TestCase):
    def test_parses_full_patch(self) -> None:
        patch = SchemaPatch.from_dict(make_patch_dict(), source_run="run_001")
        self.assertEqual(patch.patch_type, "add_field")
        self.assertEqual(patch.target_group, "hospital_cover")
        self.assertEqual(patch.field_name, "excess_amount")
        self.assertEqual(patch.canonical_name, "excess")
        self.assertEqual(patch.field_type, "number")
        self.assertEqual(patch.confidence, 0.9)
        self.assertEqual(patch.applies_to, ("hospital",))
        self.assertFalse(patch.required)
        self.assertEqual(patch.values, ())
        self.assertEqual(patch.source_run, "run_001")
        self.assertEqual(patch.evidence_documents[0].path, "pdfs/a.pdf")

    def test_canonical_name_falls_back_to_field_name(self) -> None:
        patch = SchemaPatch.from_dict(make_patch_dict(canonical_name=None))
        self.assertEqual(patch.canonical_name, "excess_amount")

    def test_accepts_alternate_keys(self) -> None:
        patch = SchemaPatch.from_dict(
            {"patch_type": "add_field", "name": "tier", "group": "identity", "field_type": "enum"}
        )
        self.assertEqual(patch.field_name, "tier")
        self.assertEqual(patch.target_group, "identity")
        self.assertEqual(patch.field_type, "enum")

    def test_non_numeric_confidence_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "confidence"):
            SchemaPatch.from_dict(make_patch_dict(confidence="high"))

    def test_evidence_from_plain_string(self) -> None:
        document = EvidenceDocument.from_value("pdfs/b.pdf")
        self.assertEqual(document.path, "pdfs/b.pdf")
        self.assertEqual(document.quote_or_summary, "")


class SchemaPatchValidateTest(unittest.TestCase):
    def test_rejects_unsupported_patch_type(self) -> None:
        patch = SchemaPatch.from_dict(make_patch_dict(patch_type="delete_everything"))
        with self.assertRaises(ValueError):
            patch.validate()

    def test_rejects_patch_without_any_name(self) -> None:
        patch = SchemaPatch.from_dict(make_patch_dict(field_name="", canonical_name=""))
        with self.assertRaises(ValueError):
            patch.validate()

    def test_rejects_add_field_enum_without_allowed_values(self) -> None:
        patch = SchemaPatch.from_dict(make_patch_dict(type="enum", values=[]))
        with self.assertRaisesRegex(ValueError, "enum.*values"):
            patch.validate()

    def test_rejects_add_field_with_unknown_product_type(self) -> None:
        patch = SchemaPatch.from_dict(make_patch_dict(applies_to=["dental"]))
        with self.assertRaisesRegex(ValueError, "applies_to"):
            patch.validate()

    def test_rejects_confidence_outside_zero_to_one(self) -> None:
        patch = SchemaPatch.from_dict(make_patch_dict(confidence=1.2))
        with self.assertRaisesRegex(ValueError, "confidence"):
            patch.validate()

    def test_rejects_scalar_applies_to_instead_of_silently_iterating_it(self) -> None:
        with self.assertRaisesRegex(ValueError, "applies_to"):
            SchemaPatch.from_dict(make_patch_dict(applies_to="hospital"))


class ParsePatchPayloadTest(unittest.TestCase):
    def test_parses_dict_with_patches_list(self) -> None:
        patches = parse_patch_payload({"patches": [make_patch_dict()]}, source_run="run_002")
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0].source_run, "run_002")

    def test_parses_bare_list(self) -> None:
        patches = parse_patch_payload([make_patch_dict()])
        self.assertEqual(len(patches), 1)

    def test_rejects_scalar_payload(self) -> None:
        with self.assertRaises(ValueError):
            parse_patch_payload("not patches")

    def test_rejects_non_dict_patch_entry(self) -> None:
        with self.assertRaises(ValueError):
            parse_patch_payload({"patches": ["just a string"]})

    def test_empty_patches_list_is_valid(self) -> None:
        self.assertEqual(parse_patch_payload({"patches": []}), [])


class YamlRoundTripTest(unittest.TestCase):
    def test_dump_load_round_trip_and_source_run_from_stem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "run_007.yaml"
            dump_yaml({"patches": [make_patch_dict()]}, path)
            patches = load_patch_file(path)
            self.assertEqual(len(patches), 1)
            self.assertEqual(patches[0].source_run, "run_007")
            self.assertEqual(load_yaml(path), parse_yaml_text(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
