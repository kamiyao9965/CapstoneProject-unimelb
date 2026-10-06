from __future__ import annotations

import copy
import csv
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from unittest import mock

from src.common.json_artifacts import build_success_artifact
from src.schema.canonical import compile_canonical_extraction_contract
from src.storage.repository import deterministic_product_id, deterministic_release_id
from src.storage.service import prepare_storage_load
from src.verticals.manifest import PROJECT_ROOT, load_vertical_manifest
from tests.test_canonical_schema import approved_travel_schema
from tests.test_canonical_storage import valid_extraction_payload


class CsvExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=PROJECT_ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.input_root = self.root / "pdf"
        self.pdf_path = self.input_root / "cover_more/pds/fixture.pdf"
        self.pdf_path.parent.mkdir(parents=True)
        self.pdf_path.write_bytes(b"%PDF-1.4\nsynthetic CSV fixture\n%%EOF\n")
        self.schema = approved_travel_schema()
        self.schema_path = self.root / "canonical.json"
        self.schema_path.write_text(json.dumps(self.schema), encoding="utf-8")
        self.artifact = {
            "vertical": "travel_insurance",
            "schema_version": "1.0.0",
            "source_path": str(self.pdf_path),
            "extracted_at": "2026-10-07T00:00:00+00:00",
            "provider": "openai",
            "model": "gpt-5",
            "data": valid_extraction_payload(),
            "evidences": {},
            "normalized_names": [],
            "warnings": [],
        }
        self.artifact_path = self.root / "results/extraction.json"
        self._write_artifact()
        self.output = self.root / "exports/products.csv"
        self.manifest = replace(
            load_vertical_manifest(PROJECT_ROOT / "configs/travel_insurance/manifest.json"),
            paths=MappingProxyType({"input_root": str(self.input_root)}),
        )

    def _write_artifact(self) -> None:
        self.artifact_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_path.write_text(json.dumps(self.artifact), encoding="utf-8")

    def _export(self):
        from src.storage.csv_export import export_extraction_artifact

        return export_extraction_artifact(
            manifest=self.manifest, schema_path=self.schema_path,
            artifact_path=self.artifact_path, insurer_code="cover_more",
            output_path=self.output,
        )

    def _read_rows(self) -> list[dict[str, str]]:
        with self.output.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def test_joins_products_releases_details_and_provenance_without_database(self) -> None:
        prepared = prepare_storage_load(
            manifest=self.manifest, schema_path=self.schema_path,
            artifact_path=self.artifact_path, insurer_code="cover_more",
        )
        with mock.patch("src.storage.service.create_engine", side_effect=AssertionError("DB call")):
            summary = self._export()
        row, = self._read_rows()
        product_id = deterministic_product_id(
            prepared.vertical, prepared.insurer_code, prepared.plan.products[0].product_name,
        )
        self.assertEqual(summary.output_path, self.output)
        self.assertEqual(summary.artifacts_exported, 1)
        self.assertEqual(summary.products_exported, 1)
        self.assertEqual(row["product_id"], product_id)
        self.assertEqual(row["release_id"], deterministic_release_id(product_id, prepared.document_id))
        self.assertEqual(row["canonical_name"], prepared.plan.products[0].core_values["products.canonical_name"])
        self.assertEqual(row["source_product_type"], prepared.plan.products[0].product_type)
        self.assertEqual(row["geographic_scope"], "international")
        self.assertEqual(row["cruise_cover_available"], "true")
        self.assertEqual(json.loads(row["attributes"]), prepared.plan.products[0].attributes)
        self.assertEqual(row["document_id"], prepared.document_id)
        self.assertEqual(row["source_path"], prepared.source_path)
        self.assertEqual(row["sha256"], prepared.pdf_sha256)
        self.assertEqual(row["schema_version_id"], prepared.schema_version_id)
        self.assertEqual(row["version"], prepared.plan.schema_version)
        self.assertEqual(row["run_id"], prepared.run_id)
        self.assertEqual(row["provider"], prepared.provider)
        self.assertEqual(row["model"], prepared.model)
        self.assertTrue(self.output.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_multiple_products_round_trip_quotes_unicode_newlines_and_nulls(self) -> None:
        product = self.artifact["data"]["products"][0]
        product["product_name"] = '旅行, "Premium"\nMulti line'
        product["geographic_scope"] = None
        product["cruise_cover_available"] = False
        product["benefits"] = [{"name": "医疗", "limit": 'A, "B"\nC', "details": [None, False, 42.5]}]
        second = copy.deepcopy(product)
        second["product_name"] = "Domestic"
        self.artifact["data"]["products"].append(second)
        self._write_artifact()

        summary = self._export()

        self.assertEqual(summary.products_exported, 2)
        rows = self._read_rows()
        self.assertEqual(rows[0]["canonical_name"], product["product_name"])
        self.assertEqual(rows[0]["geographic_scope"], "")
        self.assertEqual(rows[0]["cruise_cover_available"], "false")
        self.assertEqual(json.loads(rows[0]["attributes"]), {"benefits": product["benefits"]})
        self.assertNotEqual(rows[0]["release_id"], rows[1]["release_id"])

    def test_envelope_and_legacy_have_identical_business_rows(self) -> None:
        self._export()
        legacy = self._read_rows()[0]
        envelope = build_success_artifact(
            artifact_type="extraction_result", contract_version="1.0.0",
            data=self.artifact["data"],
            provenance={
                "vertical": "travel_insurance", "schema_version": "1.0.0",
                "run_id": "fixture-run", "provider": "openai", "model": "gpt-5",
                "document_input": "markdown", "source_documents": [str(self.pdf_path)],
                "source_artifacts": [],
            },
            data_contract_schema=compile_canonical_extraction_contract(self.schema),
        )
        self.artifact = envelope
        self._write_artifact()
        self.output = self.root / "envelope.csv"
        self._export()
        current = self._read_rows()[0]
        legacy.pop("run_id")
        self.assertEqual(current.pop("run_id"), "fixture-run")
        self.assertEqual(current, legacy)

    def test_invalid_schema_or_identity_does_not_create_output(self) -> None:
        for change in ("candidate", "wrong_version", "wrong_source"):
            with self.subTest(change=change):
                schema = copy.deepcopy(self.schema)
                artifact = copy.deepcopy(self.artifact)
                if change == "candidate":
                    schema.update(status="candidate", review=None)
                elif change == "wrong_version":
                    artifact["schema_version"] = "99.0.0"
                else:
                    artifact["source_path"] = str(self.root / "outside.pdf")
                self.schema_path.write_text(json.dumps(schema), encoding="utf-8")
                self.artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
                with self.assertRaises(ValueError):
                    self._export()
                self.assertFalse(self.output.exists())

    def test_export_never_overwrites_existing_output(self) -> None:
        self.output.parent.mkdir()
        self.output.write_text("original", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self._export()
        self.assertEqual(self.output.read_text(encoding="utf-8"), "original")

    def test_rejects_formula_text_without_silently_changing_values(self) -> None:
        self.artifact["data"]["products"][0]["product_name"] = '=HYPERLINK("https://example.test")'
        self._write_artifact()
        with self.assertRaisesRegex(ValueError, "formula"):
            self._export()
        self.assertFalse(self.output.exists())

    def test_rejects_detail_column_that_would_shadow_product_identity(self) -> None:
        self.schema["fields"][2]["storage"]["column"] = "product_id"
        self.schema_path.write_text(json.dumps(self.schema), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "column.*product_id"):
            self._export()
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
