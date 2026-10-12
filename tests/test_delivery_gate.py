from __future__ import annotations

import copy
import csv
import json
import tempfile
import unittest
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from unittest import mock

from src.evaluation import quality_review
from src.storage.csv_export import export_extraction_artifact, export_extraction_directory
from src.verticals.documents import document_group_id, resolve_document_groups
from src.verticals.manifest import PROJECT_ROOT, resolve_manifest
from tests.test_canonical_schema import approved_travel_schema
from tests.test_canonical_storage import valid_extraction_payload


class DeliveryGateTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir=PROJECT_ROOT)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.input_root = self.root / "pdf"
        self.primary = self.input_root / "cover_more/pds/fixture.pdf"
        self.amendment = self.input_root / "cover_more/spds/amendment.pdf"
        for path in (self.primary, self.amendment):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"%PDF-1.4\nSynthetic {path.stem} delivery fixture\n%%EOF\n".encode())
        manifest = resolve_manifest(vertical="travel_insurance")
        inventory = (
            {"document_id": "A", "source_path": str(self.primary.relative_to(self.input_root)),
             "document_role": "pds", "amends_document_ids": [], "metadata": {}},
            {"document_id": "B", "source_path": str(self.amendment.relative_to(self.input_root)),
             "document_role": "spds", "amends_document_ids": ["A"], "metadata": {}},
        )
        self.manifest = replace(
            manifest, paths=MappingProxyType({"input_root": str(self.input_root)}),
            documents=replace(manifest.documents, inventory=inventory),
        )
        self.group = resolve_document_groups(self.manifest, [self.primary], input_root=self.input_root)[0]
        self.schema = approved_travel_schema()
        self.schema_path = self.root / "canonical_test_fixture.json"
        self.schema_path.write_text(json.dumps(self.schema), encoding="utf-8")
        self.artifact = {
            "vertical": self.manifest.vertical, "schema_version": self.schema["version"],
            "source_path": str(self.group.primary_path), **self.group.provenance(),
            "extracted_at": "2026-10-12T00:00:00Z", "provider": "openai", "model": "gpt-5",
            "document_parser": "pdfingestor", "data": valid_extraction_payload(),
        }
        self.artifact_path = self.root / "results/extraction.json"
        self.artifact_path.parent.mkdir()
        self.write_artifact()
        self.approval_dir = self.root / "delivery_approvals"
        self.approval = self.approval_dir / "extraction.json"
        self.output = self.root / "exports/products.csv"

    def write_artifact(self) -> None:
        self.artifact_path.write_text(json.dumps(self.artifact), encoding="utf-8")

    def approve(self, *, artifact_path=None, output_path=None, manifest=None, **overrides) -> Path:
        kwargs = {
            "manifest": manifest or self.manifest, "schema_path": self.schema_path,
            "artifact_path": artifact_path or self.artifact_path, "output_path": output_path or self.approval,
            "reviewer": "synthetic-test-reviewer", "rationale": "Reviewed the synthetic fixture only.",
            "reviewed_at": "2026-10-12T01:00:00Z",
        }
        kwargs.update(overrides)
        return quality_review.write_delivery_approval(**kwargs)

    def require(self, *, approval_path=None, **overrides) -> dict:
        return quality_review.require_delivery_approval(
            manifest=self.manifest, schema_path=self.schema_path, artifact_path=self.artifact_path,
            approval_path=approval_path, **overrides,
        )

    def export(self, **kwargs):
        return export_extraction_artifact(
            manifest=self.manifest, schema_path=self.schema_path, artifact_path=self.artifact_path,
            insurer_code="cover_more", output_path=self.output, **kwargs,
        )

    def export_directory(self, **kwargs):
        return export_extraction_directory(
            manifest=self.manifest, schema_path=self.schema_path, artifact_dir=self.artifact_path.parent,
            output_path=self.output, **kwargs,
        )

    def read_rows(self) -> list[dict[str, str]]:
        with self.output.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def second_artifact(self) -> Path:
        pdf = self.input_root / "tick/pds/tick.pdf"
        pdf.parent.mkdir(parents=True)
        pdf.write_bytes(b"%PDF-1.4\nSeparate synthetic delivery fixture\n%%EOF\n")
        reference = {
            "document_id": "tick", "source_path": str(pdf), "document_role": "pds",
            "amends_document_ids": [], "sha256": sha256(pdf.read_bytes()).hexdigest(), "metadata": {},
        }
        artifact = copy.deepcopy(self.artifact)
        artifact.update(source_path=str(pdf), source_documents=[str(pdf)], source_document_refs=[reference],
                        document_group_id=document_group_id([reference]))
        path = self.artifact_path.parent / "nested/tick.json"
        path.parent.mkdir()
        path.write_text(json.dumps(artifact), encoding="utf-8")
        inventory = (*self.manifest.documents.inventory, {
            "document_id": "tick", "source_path": str(pdf.relative_to(self.input_root)),
            "document_role": "pds", "amends_document_ids": [], "metadata": {},
        })
        self.manifest = replace(self.manifest, documents=replace(self.manifest.documents, inventory=inventory))
        return path

    def test_existing_single_csv_api_rejects_missing_delivery_proof(self) -> None:
        with self.assertRaises(ValueError):
            self.export()
        self.assertFalse(self.output.exists())

    def test_existing_batch_csv_api_rejects_missing_delivery_proof(self) -> None:
        self.second_artifact()
        with self.assertRaises(ValueError):
            self.export_directory()
        self.assertFalse(self.output.exists())

    def test_require_rejects_when_neither_judge_nor_human_proof_is_given(self) -> None:
        with self.assertRaises(ValueError):
            self.require()

    def test_synthetic_human_confirmation_can_be_checked_without_provider_or_database(self) -> None:
        schema_bytes = self.schema_path.read_bytes()
        artifact_bytes = self.artifact_path.read_bytes()
        with mock.patch("src.common.model_provider.create_provider", side_effect=AssertionError("model call")), \
             mock.patch("src.storage.service.create_engine", side_effect=AssertionError("DB call")):
            written = self.approve()
            proof = self.require(approval_path=written)
        self.assertEqual(Path(written), self.approval)
        self.assertEqual(proof["status"], "human_approved")
        self.assertEqual(Path(proof["proof_path"]).resolve(), self.approval.resolve())
        self.assertEqual(self.schema_path.read_bytes(), schema_bytes)
        self.assertEqual(self.artifact_path.read_bytes(), artifact_bytes)

    def test_confirmation_requires_nonempty_reviewer_and_rationale(self) -> None:
        for name, value in (("reviewer", ""), ("reviewer", "  "), ("rationale", ""), ("rationale", "  ")):
            with self.subTest(field=name, value=value):
                with self.assertRaises(ValueError):
                    self.approve(**{name: value})
                self.assertFalse(self.approval.exists())

    def test_confirmation_writer_preserves_an_existing_record(self) -> None:
        self.approve()
        before = self.approval.read_bytes()
        with self.assertRaises(FileExistsError):
            self.approve(reviewer="other-test-reviewer")
        self.assertEqual(self.approval.read_bytes(), before)

    def test_confirmation_binds_schema_artifact_bytes_and_secondary_pdf_hash(self) -> None:
        schema_bytes = self.schema_path.read_bytes()
        artifact_bytes = self.artifact_path.read_bytes()
        amendment_bytes = self.amendment.read_bytes()
        for change in ("schema", "schema_whitespace", "artifact", "artifact_whitespace", "spds"):
            with self.subTest(change=change):
                self.schema_path.write_bytes(schema_bytes)
                self.artifact_path.write_bytes(artifact_bytes)
                self.amendment.write_bytes(amendment_bytes)
                approval = self.root / f"approval_{change}.json"
                self.approve(output_path=approval)
                proof_bytes = approval.read_bytes()
                if change == "schema":
                    schema = copy.deepcopy(self.schema)
                    schema["description"] = "Changed description with the same schema version."
                    self.schema_path.write_text(json.dumps(schema), encoding="utf-8")
                elif change == "schema_whitespace":
                    self.schema_path.write_text(json.dumps(self.schema, indent=2), encoding="utf-8")
                elif change == "artifact":
                    artifact = copy.deepcopy(self.artifact)
                    artifact["data"]["products"][0]["_notes"] = "Changed extracted content."
                    self.artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
                elif change == "artifact_whitespace":
                    self.artifact_path.write_text(json.dumps(self.artifact, indent=2), encoding="utf-8")
                else:
                    self.amendment.write_bytes(b"%PDF-1.4\nChanged SPDS fixture\n%%EOF\n")
                with self.assertRaises(ValueError):
                    self.require(approval_path=approval)
                self.assertEqual(approval.read_bytes(), proof_bytes)

    def test_cannot_confirm_an_artifact_with_an_incorrect_secondary_source_hash(self) -> None:
        self.artifact["source_document_refs"][1]["sha256"] = "0" * 64
        self.artifact["document_group_id"] = document_group_id(self.artifact["source_document_refs"])
        self.write_artifact()
        with self.assertRaises(ValueError):
            self.approve()
        self.assertFalse(self.approval.exists())

    def test_secondary_source_path_must_stay_inside_the_configured_root(self) -> None:
        outside = self.root / "outside.pdf"
        outside.write_bytes(self.amendment.read_bytes())
        self.artifact["source_documents"][1] = str(outside)
        self.artifact["source_document_refs"][1]["source_path"] = str(outside)
        self.write_artifact()
        with self.assertRaises(ValueError):
            self.approve()
        self.assertFalse(self.approval.exists())

    def test_secondary_source_symlink_cannot_escape_the_configured_root(self) -> None:
        outside = self.root / "outside.pdf"
        outside.write_bytes(self.amendment.read_bytes())
        self.amendment.unlink()
        self.amendment.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.approve()
        self.assertFalse(self.approval.exists())

    def test_confirmed_csv_preserves_complete_document_receipts_and_group_identity(self) -> None:
        self.approve()
        summary = self.export(approval_path=self.approval)
        self.assertEqual(summary.artifacts_exported, 1)
        row, = self.read_rows()
        self.assertEqual(json.loads(row["source_documents"]), self.artifact["source_document_refs"])
        self.assertEqual(row["document_group_id"], self.group.group_id)
        self.assertEqual(row["source_path"], str(self.primary))

    def test_csv_capability_does_not_require_database_storage_capability(self) -> None:
        self.manifest = replace(self.manifest, capabilities=MappingProxyType({
            **self.manifest.capabilities, "csv_export": True, "storage": False,
        }))
        self.approve()
        with mock.patch("src.storage.service.create_engine", side_effect=AssertionError("DB call")):
            summary = self.export(approval_path=self.approval)
        self.assertEqual(summary.products_exported, 1)
        self.assertTrue(self.output.is_file())

    def test_csv_rejects_changed_spds_after_confirmation_without_writing_output(self) -> None:
        self.approve()
        self.amendment.write_bytes(b"%PDF-1.4\nUpdated after confirmation\n%%EOF\n")
        with self.assertRaises(ValueError):
            self.export(approval_path=self.approval)
        self.assertFalse(self.output.exists())

    def test_canonical_candidate_cannot_receive_delivery_confirmation(self) -> None:
        candidate = copy.deepcopy(self.schema)
        candidate.update(status="candidate", review=None)
        self.schema_path.write_text(json.dumps(candidate), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.approve()
        self.assertFalse(self.approval.exists())

    def test_batch_checks_every_confirmation_before_writing_any_csv(self) -> None:
        self.second_artifact()
        self.approve()
        with self.assertRaises(ValueError):
            self.export_directory(approval_dir=self.approval_dir)
        self.assertFalse(self.output.exists())

    def test_batch_uses_matching_nested_approval_paths_and_exports_only_after_all_pass(self) -> None:
        second = self.second_artifact()
        self.approve()
        self.approve(artifact_path=second, output_path=self.approval_dir / "nested/tick.json")
        summary = self.export_directory(approval_dir=self.approval_dir)
        self.assertEqual(summary.artifacts_exported, 2)
        self.assertEqual([row["insurer_code"] for row in self.read_rows()], ["cover_more", "tick"])

    def test_batch_stale_second_confirmation_prevents_partial_csv(self) -> None:
        second = self.second_artifact()
        self.approve()
        self.approve(artifact_path=second, output_path=self.approval_dir / "nested/tick.json")
        second.write_text(json.dumps(json.loads(second.read_text()), indent=2), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.export_directory(approval_dir=self.approval_dir)
        self.assertFalse(self.output.exists())

    def test_batch_relative_input_and_approval_paths_preserve_nested_mapping(self) -> None:
        self.approve()
        second = self.second_artifact()
        self.approve(artifact_path=second, output_path=self.approval_dir / "nested/tick.json")
        summary = export_extraction_directory(
            manifest=self.manifest, schema_path=self.schema_path,
            artifact_dir=self.artifact_path.parent.relative_to(PROJECT_ROOT),
            output_path=self.output, approval_dir=self.approval_dir.relative_to(PROJECT_ROOT),
        )
        self.assertEqual(summary.products_exported, 2)

    def test_batch_input_and_confirmation_directories_cannot_contain_each_other(self) -> None:
        for approval_dir in (self.artifact_path.parent / "approvals", self.root):
            with self.subTest(approval_dir=approval_dir):
                with self.assertRaises(ValueError):
                    self.export_directory(approval_dir=approval_dir)
                self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
