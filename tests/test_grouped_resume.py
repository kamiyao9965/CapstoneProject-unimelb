from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.common.models import ExtractionResult
from src.schema_application.records import load_cached_extraction
from src.verticals.documents import resolve_document_groups
from tests.test_grouped_extraction import GROUPED_RECORD, GROUPED_SELECTION, grouped_fixture


class GroupedResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.input_root = self.root / "inputs"
        self.manifest, self.paths, self.schema = grouped_fixture(self.input_root)
        self.group = resolve_document_groups(self.manifest, [self.paths["A"]], input_root=self.input_root)[0]
        self.cached = self.root / "cached.json"

    def native_artifact(self) -> dict[str, object]:
        return {
            "vertical": self.manifest.vertical,
            "schema_version": str(self.schema["version"]),
            "source_path": str(self.group.primary_path),
            "source_documents": [str(path) for path in self.group.pdf_paths],
            "source_document_refs": [document.to_dict() for document in self.group.documents],
            "document_group_id": self.group.group_id,
            "extracted_at": "2026-10-12T00:00:00Z",
            "provider": GROUPED_SELECTION.provider,
            "model": GROUPED_SELECTION.model,
            "document_parser": "pdfingestor",
            "data": copy.deepcopy(GROUPED_RECORD),
        }

    def envelope_artifact(self) -> dict[str, object]:
        native = self.native_artifact()
        provenance = {
            name: value for name, value in native.items()
            if name not in {"data", "source_path", "extracted_at"}
        }
        provenance.update(run_id="synthetic_group_run", document_input="markdown", source_artifacts=[])
        return {
            "artifact_type": "extraction_result", "contract_version": "1.0.0", "status": "success",
            "created_at": native["extracted_at"], "provenance": provenance, "data": native["data"], "error": None,
        }

    def write(self, artifact: dict[str, object]) -> None:
        self.cached.write_text(json.dumps(artifact), encoding="utf-8")

    def load(self, *, group=None, pass_group: bool = True) -> ExtractionResult:
        kwargs = {"document_group": group or self.group} if pass_group else {}
        return load_cached_extraction(
            self.cached, pdf_path=self.group.primary_path, schema=self.schema,
            manifest=self.manifest, selection=GROUPED_SELECTION, document_parser="pdfingestor", **kwargs,
        )

    def test_matching_native_and_enveloped_groups_resume_without_provider(self) -> None:
        for format_name, artifact in (("native", self.native_artifact()), ("envelope", self.envelope_artifact())):
            with self.subTest(format=format_name):
                self.write(artifact)
                before = self.cached.read_bytes()
                with mock.patch("src.common.model_provider.create_provider") as builder:
                    result = self.load()
                builder.assert_not_called()
                self.assertEqual(result.source_path, str(self.group.primary_path))
                self.assertEqual(result.source_documents, [str(path) for path in self.group.pdf_paths])
                self.assertEqual(result.source_document_refs, [document.to_dict() for document in self.group.documents])
                self.assertEqual(result.document_group_id, self.group.group_id)
                self.assertEqual(self.cached.read_bytes(), before)

    def test_updated_spds_rejects_cached_result_for_new_group_identity(self) -> None:
        self.write(self.native_artifact())
        self.paths["B"].write_bytes(b"%PDF-1.4\nChanged synthetic amendment\n%%EOF\n")
        updated = resolve_document_groups(self.manifest, [self.paths["A"]], input_root=self.input_root)[0]
        self.assertNotEqual(updated.group_id, self.group.group_id)
        with self.assertRaises(ValueError):
            self.load(group=updated)

    def test_updated_spds_is_detected_even_if_caller_passes_old_group_snapshot(self) -> None:
        self.write(self.native_artifact())
        self.paths["B"].write_bytes(b"%PDF-1.4\nChanged after group resolution\n%%EOF\n")
        with self.assertRaises(ValueError):
            self.load(group=self.group)

    def test_missing_member_pdf_rejects_cached_group(self) -> None:
        self.write(self.native_artifact())
        self.paths["B"].unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.load()

    def test_every_cached_document_path_id_hash_and_group_id_must_match(self) -> None:
        for defect in ("group_id", "missing_path", "wrong_path", "document_id", "hash", "missing_ref"):
            with self.subTest(defect=defect):
                artifact = self.native_artifact()
                if defect == "group_id":
                    artifact["document_group_id"] = "other_group"
                elif defect == "missing_path":
                    artifact["source_documents"] = artifact["source_documents"][:1]
                elif defect == "wrong_path":
                    artifact["source_documents"][1] = str(self.paths["C"])
                elif defect == "document_id":
                    artifact["source_document_refs"][1]["document_id"] = "unrelated_id"
                elif defect == "hash":
                    artifact["source_document_refs"][1]["sha256"] = "0" * 64
                else:
                    artifact["source_document_refs"] = artifact["source_document_refs"][:1]
                self.write(artifact)
                before = self.cached.read_bytes()
                with self.assertRaises(ValueError):
                    self.load()
                self.assertEqual(self.cached.read_bytes(), before)

    def test_legacy_multisource_artifact_without_trusted_refs_is_rejected(self) -> None:
        for format_name, artifact in (("native", self.native_artifact()), ("envelope", self.envelope_artifact())):
            with self.subTest(format=format_name):
                identity = artifact if format_name == "native" else artifact["provenance"]
                identity.pop("source_document_refs")
                identity.pop("document_group_id")
                self.write(artifact)
                with self.assertRaises(ValueError):
                    self.load()

    def test_untrusted_native_multisource_file_is_rejected_even_without_group_context(self) -> None:
        artifact = self.native_artifact()
        artifact.pop("source_document_refs")
        artifact.pop("document_group_id")
        self.write(artifact)
        with self.assertRaises(ValueError):
            self.load(pass_group=False)

    def test_legacy_single_source_result_cannot_resume_a_new_multidocument_group(self) -> None:
        artifact = self.native_artifact()
        for name in ("source_documents", "source_document_refs", "document_group_id"):
            artifact.pop(name)
        self.write(artifact)
        with self.assertRaises(ValueError):
            self.load()

    def test_legacy_single_pdf_without_group_metadata_remains_readable(self) -> None:
        artifact = self.native_artifact()
        for name in ("source_documents", "source_document_refs", "document_group_id"):
            artifact.pop(name)
        self.write(artifact)
        result = self.load(pass_group=False)
        self.assertEqual(result.source_path, str(self.paths["A"]))
        self.assertEqual(result.data, GROUPED_RECORD)


if __name__ == "__main__":
    unittest.main()
