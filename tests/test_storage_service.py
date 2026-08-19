from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType
from unittest import mock

from src.verticals.manifest import DocumentModel, PROJECT_ROOT, VerticalManifest
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
        self.manifest = VerticalManifest(
            manifest_version="1.0",
            vertical="travel_insurance",
            capabilities=MappingProxyType({"storage": True}),
            documents=DocumentModel(
                categories=("pds",),
                document_types=("pds", "spds", "brochure", "tmd", "fsg"),
                extraction_unit="product_release",
                output_cardinality="multiple",
            ),
            paths=MappingProxyType({"input_root": str(self.input_root)}),
            path_environment=MappingProxyType({}),
            contracts=MappingProxyType({}),
            prompts=MappingProxyType({}),
            adapters=MappingProxyType({}),
            source_path=self.root / "manifest.json",
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
