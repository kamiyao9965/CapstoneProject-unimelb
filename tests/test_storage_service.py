from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType
from dataclasses import replace
from unittest import mock

from src.verticals.manifest import PROJECT_ROOT, load_vertical_manifest
from tests.test_canonical_schema import approved_travel_schema
from tests.test_canonical_storage import valid_extraction_payload


class StoragePreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=PROJECT_ROOT)
        self.root = Path(self.temporary.name)
        self.input_root = self.root / "data" / "travel_insurance" / "raw" / "PDFs"
        self.pdf_path = self.input_root / "cover_more" / "pds" / "fixture.pdf"
        self.pdf_path.parent.mkdir(parents=True)
        self.pdf_path.write_bytes(b"%PDF-1.4\ntravel fixture\n%%EOF\n")

        self.schema_path = self.root / "canonical_schema.json"
        self.schema_path.write_text(
            json.dumps(approved_travel_schema()),
            encoding="utf-8",
        )
        self.artifact_path = self.root / "extraction.json"
        self.artifact_path.write_text(
            json.dumps(
                {
                    "vertical": "travel_insurance",
                    "schema_version": "1.0.0",
                    "source_path": str(self.pdf_path),
                    "extracted_at": "2026-08-19T00:00:00+00:00",
                    "provider": "openai",
                    "model": "gpt-5",
                    "data": valid_extraction_payload(),
                    "evidences": {},
                    "normalized_names": [],
                    "warnings": [],
                }
            ),
            encoding="utf-8",
        )
        self.manifest = replace(
            load_vertical_manifest(PROJECT_ROOT / "configs/travel_insurance/manifest.json"),
            paths=MappingProxyType({"input_root": str(self.input_root)}),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_prepares_deterministic_postgresql_records_from_extraction(self) -> None:
        from src.storage.service import prepare_storage_load

        first = prepare_storage_load(
            manifest=self.manifest,
            schema_path=self.schema_path,
            artifact_path=self.artifact_path,
            insurer_code="cover_more",
        )
        second = prepare_storage_load(
            manifest=self.manifest,
            schema_path=self.schema_path,
            artifact_path=self.artifact_path,
            insurer_code="cover_more",
        )

        self.assertEqual(first, second)
        self.assertTrue(first.document_id.startswith("sha256:"))
        self.assertTrue(first.schema_version_id.startswith("sha256:"))
        self.assertTrue(first.run_id.startswith("sha256:"))
        self.assertEqual(first.document_type, "pds")
        self.assertEqual(first.insurer_code, "cover_more")
        self.assertEqual(first.plan.products[0].product_name, "International Comprehensive")
        self.assertEqual(first.raw_artifact["provider"], "openai")
        self.assertEqual(len(first.pdf_sha256), 64)

    def test_directory_load_derives_insurer_codes_and_rejects_duplicate_sources(self) -> None:
        from src.storage.service import load_extraction_directory

        results_dir = self.root / "run20"
        artifact = json.loads(self.artifact_path.read_text(encoding="utf-8"))
        for name in ("fixture.json", "fixture_1.json"):
            duplicate = results_dir / "cover_more" / "pds" / name
            duplicate.parent.mkdir(parents=True, exist_ok=True)
            duplicate.write_text(json.dumps(artifact), encoding="utf-8")
        tick_pdf = self.input_root / "tick" / "pds" / "single.pdf"
        tick_pdf.parent.mkdir(parents=True)
        tick_pdf.write_bytes(b"%PDF-1.4\ntick fixture\n%%EOF\n")
        tick_result = results_dir / "tick" / "pds" / "single.json"
        tick_result.parent.mkdir(parents=True)
        tick_result.write_text(json.dumps({**artifact, "source_path": str(tick_pdf)}), encoding="utf-8")
        (results_dir / "broken.json").write_text("not json", encoding="utf-8")
        ignored = results_dir / "errors" / "extraction" / "failed.json"
        ignored.parent.mkdir(parents=True)
        ignored.write_text("{}", encoding="utf-8")
        calls: list[dict] = []

        def fake_load(**kwargs):
            calls.append(kwargs)
            return "summary"

        results = load_extraction_directory(
            database_url="postgresql+psycopg:///unused",
            manifest=self.manifest,
            schema_path=self.schema_path,
            artifact_dir=results_dir,
            load_one=fake_load,
        )

        by_name = {result.artifact_path.relative_to(results_dir).as_posix(): result for result in results}
        self.assertEqual(
            sorted(by_name),
            ["broken.json", "cover_more/pds/fixture.json", "cover_more/pds/fixture_1.json", "tick/pds/single.json"],
        )
        self.assertEqual([call["insurer_code"] for call in calls], ["tick"])
        self.assertEqual(by_name["tick/pds/single.json"].summary, "summary")
        for name in ("cover_more/pds/fixture.json", "cover_more/pds/fixture_1.json"):
            self.assertIsNone(by_name[name].summary)
            self.assertIn("keep one and retry", by_name[name].error)
        self.assertIn("Could not read extraction artifact", by_name["broken.json"].error)

    def test_directory_load_reports_loader_failures_and_requires_results(self) -> None:
        from src.storage.service import load_extraction_directory

        results_dir = self.root / "run20"
        result = results_dir / "cover_more" / "pds" / "fixture.json"
        result.parent.mkdir(parents=True)
        result.write_text(self.artifact_path.read_text(encoding="utf-8"), encoding="utf-8")
        options = {
            "database_url": "postgresql+psycopg:///unused",
            "manifest": self.manifest,
            "schema_path": self.schema_path,
        }

        results = load_extraction_directory(
            artifact_dir=results_dir,
            load_one=mock.Mock(side_effect=ValueError("schema version collision")),
            **options,
        )
        self.assertEqual(results[0].insurer_code, "cover_more")
        self.assertEqual(results[0].error, "schema version collision")

        empty = self.root / "empty"
        empty.mkdir()
        with self.assertRaisesRegex(ValueError, "No extraction artifacts"):
            load_extraction_directory(artifact_dir=empty, load_one=mock.Mock(), **options)
        with self.assertRaisesRegex(ValueError, "does not exist"):
            load_extraction_directory(artifact_dir=self.root / "missing", load_one=mock.Mock(), **options)

    def test_rejects_source_document_outside_manifest_input_root(self) -> None:
        from src.storage.service import prepare_storage_load

        artifact = json.loads(self.artifact_path.read_text(encoding="utf-8"))
        artifact["source_path"] = str(self.root / "outside.pdf")
        (self.root / "outside.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
        self.artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "input root"):
            prepare_storage_load(
                manifest=self.manifest,
                schema_path=self.schema_path,
                artifact_path=self.artifact_path,
                insurer_code="cover_more",
            )

    def test_rejects_artifact_vertical_mismatch(self) -> None:
        from src.storage.service import prepare_storage_load

        artifact = json.loads(self.artifact_path.read_text(encoding="utf-8"))
        artifact["vertical"] = "private_health"
        self.artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "vertical"):
            prepare_storage_load(
                manifest=self.manifest,
                schema_path=self.schema_path,
                artifact_path=self.artifact_path,
                insurer_code="cover_more",
            )

    def envelope(self) -> dict:
        from src.common.json_artifacts import build_success_artifact
        from src.schema.canonical import compile_canonical_extraction_contract

        return build_success_artifact(
            artifact_type="extraction_result",
            contract_version="1.0.0",
            data=valid_extraction_payload(),
            provenance={
                "vertical": "travel_insurance", "schema_version": "1.0.0",
                "run_id": "fixture-run", "provider": "openai", "model": "gpt-5",
                "document_input": "markdown", "source_documents": [str(self.pdf_path)],
                "source_artifacts": [],
            },
            data_contract_schema=compile_canonical_extraction_contract(approved_travel_schema()),
        )

    def test_envelope_and_legacy_prepare_the_same_business_records(self) -> None:
        from src.storage.service import prepare_storage_load

        def prepare():
            return prepare_storage_load(
                manifest=self.manifest, schema_path=self.schema_path,
                artifact_path=self.artifact_path, insurer_code="cover_more",
            )

        legacy = prepare()
        self.artifact_path.write_text(json.dumps(self.envelope()), encoding="utf-8")
        envelope = prepare()
        self.assertEqual(legacy.plan, envelope.plan)
        self.assertEqual(legacy.document_id, envelope.document_id)
        self.assertEqual(legacy.schema_version_id, envelope.schema_version_id)
        self.assertEqual(envelope.run_id, "fixture-run")
        self.assertTrue(legacy.run_id.startswith("sha256:"))

    def test_envelope_requires_matching_vertical_and_schema_version(self) -> None:
        from src.storage.service import prepare_storage_load

        for field, value in (
            ("vertical", "private_health"), ("schema_version", "different-version"),
            ("vertical", None), ("schema_version", None),
        ):
            with self.subTest(field=field, value=value):
                artifact = self.envelope()
                if value is None:
                    artifact["provenance"].pop(field)
                else:
                    artifact["provenance"][field] = value
                self.artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "vertical|schema version"):
                    prepare_storage_load(
                        manifest=self.manifest, schema_path=self.schema_path,
                        artifact_path=self.artifact_path, insurer_code="cover_more",
                    )

    def test_malformed_legacy_artifact_error_does_not_expose_document_content(self) -> None:
        from src.storage.service import prepare_storage_load

        artifact = json.loads(self.artifact_path.read_text())
        artifact["data"] = ["private-document-content"]
        self.artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
        with self.assertRaises(ValueError) as raised:
            prepare_storage_load(
                manifest=self.manifest, schema_path=self.schema_path,
                artifact_path=self.artifact_path, insurer_code="cover_more",
            )
        self.assertNotIn("private-document-content", str(raised.exception))

    def test_rejects_unsafe_insurer_code_before_database_write(self) -> None:
        from src.storage.service import prepare_storage_load

        with self.assertRaisesRegex(ValueError, "Insurer code"):
            prepare_storage_load(
                manifest=self.manifest,
                schema_path=self.schema_path,
                artifact_path=self.artifact_path,
                insurer_code="cover_more'; drop table products; --",
            )

    def test_rejects_non_pdf_content_even_with_pdf_extension(self) -> None:
        from src.storage.service import prepare_storage_load

        self.pdf_path.write_bytes(b"not a pdf")

        with self.assertRaisesRegex(ValueError, "PDF signature"):
            prepare_storage_load(
                manifest=self.manifest,
                schema_path=self.schema_path,
                artifact_path=self.artifact_path,
                insurer_code="cover_more",
            )

    def test_initialization_uses_one_transaction_for_core_and_extension_ddl(self) -> None:
        from src.storage.service import initialize_storage

        connection = mock.Mock()
        engine = mock.MagicMock()
        engine.begin.return_value.__enter__.return_value = connection
        compiled = mock.Mock()

        with mock.patch("src.storage.service.create_engine", return_value=engine), \
             mock.patch("src.storage.service.storage_metadata.create_all") as create_core, \
             mock.patch(
                 "src.storage.service.compile_vertical_storage_metadata",
                 return_value=compiled,
             ):
            initialize_storage(
                database_url="postgresql+psycopg://user:secret@localhost/db",
                manifest=self.manifest,
                schema_path=self.schema_path,
            )

        create_core.assert_called_once_with(connection, checkfirst=True)
        compiled.create.assert_called_once_with(connection)
        engine.begin.assert_called_once_with()
        engine.dispose.assert_called_once_with()

    def test_load_failure_leaves_transaction_rollback_to_engine_context(self) -> None:
        from src.storage.service import load_extraction_artifact

        engine = mock.MagicMock()
        transaction = engine.begin.return_value
        transaction.__enter__.return_value = mock.Mock()
        transaction.__exit__.return_value = False

        with mock.patch("src.storage.service.create_engine", return_value=engine), \
             mock.patch(
                 "src.storage.repository.write_prepared_load",
                 side_effect=RuntimeError("database write failed"),
             ):
            with self.assertRaisesRegex(RuntimeError, "database write failed"):
                load_extraction_artifact(
                    database_url="postgresql+psycopg://user:secret@localhost/db",
                    manifest=self.manifest,
                    schema_path=self.schema_path,
                    artifact_path=self.artifact_path,
                    insurer_code="cover_more",
                )

        transaction.__exit__.assert_called_once()
        self.assertIs(transaction.__exit__.call_args.args[0], RuntimeError)
        engine.dispose.assert_called_once_with()


class PostgreSQLConfigurationTests(unittest.TestCase):
    def test_database_url_environment_name_must_be_safe(self) -> None:
        from src.storage.service import resolve_database_url

        with self.assertRaisesRegex(ValueError, "environment-variable name"):
            resolve_database_url("BAD\nNAME")

    def test_database_url_is_required_without_printing_its_value(self) -> None:
        from src.storage.service import resolve_database_url

        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "KONKRD_DATABASE_URL"):
                resolve_database_url("KONKRD_DATABASE_URL")

    def test_non_postgresql_url_is_rejected(self) -> None:
        from src.storage.service import require_postgresql_url

        with self.assertRaisesRegex(ValueError, "PostgreSQL"):
            require_postgresql_url("sqlite+pysqlite:///:memory:")

    def test_postgresql_psycopg_url_is_accepted(self) -> None:
        from src.storage.service import require_postgresql_url

        self.assertEqual(
            require_postgresql_url("postgresql+psycopg://user:secret@localhost/db"),
            "postgresql+psycopg://user:secret@localhost/db",
        )


if __name__ == "__main__":
    unittest.main()
