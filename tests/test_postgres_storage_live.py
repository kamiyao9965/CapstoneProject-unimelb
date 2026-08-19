"""Opt-in integration checks against a real PostgreSQL database."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, func, select

from src.common.json_artifacts import build_success_artifact
from src.common.json_codec import dumps_json, loads_json
from src.schema.canonical import compile_canonical_extraction_contract
from src.storage.canonical import compile_vertical_storage_metadata
from src.storage.schema import storage_metadata
from src.storage.service import (
    initialize_storage,
    load_extraction_artifact,
    require_postgresql_url,
)
from src.verticals.manifest import PROJECT_ROOT, load_vertical_manifest


DATABASE_URL = os.getenv("KONKRD_TEST_DATABASE_URL")
MANIFEST_PATH = PROJECT_ROOT / "configs/travel_insurance/manifest.json"
SCHEMA_PATH = PROJECT_ROOT / "configs/travel_insurance/canonical_schema_v1.json"
ARTIFACT_PATH = (
    PROJECT_ROOT
    / "outputs/travel_insurance/extractions/cover_more_business_canonical_v1.json"
)


@unittest.skipUnless(DATABASE_URL, "KONKRD_TEST_DATABASE_URL is not configured")
class LivePostgreSQLStorageTest(unittest.TestCase):
    def test_real_postgresql_idempotency_jsonb_and_rollback(self) -> None:
        assert DATABASE_URL is not None
        database_url = require_postgresql_url(DATABASE_URL)
        if not ARTIFACT_PATH.is_file():
            self.skipTest("Local Cover-More extraction artifact is unavailable")

        manifest = load_vertical_manifest(MANIFEST_PATH)
        schema = loads_json(SCHEMA_PATH.read_text(encoding="utf-8"))
        legacy_artifact = loads_json(ARTIFACT_PATH.read_text(encoding="utf-8"))
        assert isinstance(schema, dict)
        assert isinstance(legacy_artifact, dict)
        extension = compile_vertical_storage_metadata(schema)

        initialize_storage(
            database_url=database_url,
            manifest=manifest,
            schema_path=SCHEMA_PATH,
        )
        first = load_extraction_artifact(
            database_url=database_url,
            manifest=manifest,
            schema_path=SCHEMA_PATH,
            artifact_path=ARTIFACT_PATH,
            insurer_code="cover_more",
        )
        first_counts = _row_counts(database_url, extension.table)
        second = load_extraction_artifact(
            database_url=database_url,
            manifest=manifest,
            schema_path=SCHEMA_PATH,
            artifact_path=ARTIFACT_PATH,
            insurer_code="cover_more",
        )
        second_counts = _row_counts(database_url, extension.table)

        self.assertEqual(first, second)
        self.assertEqual(first_counts, second_counts)
        self.assertTrue(_columns_are_jsonb(database_url, extension.table))

        collision = build_success_artifact(
            artifact_type="extraction_result",
            contract_version="1.0.0",
            data=legacy_artifact["data"],
            provenance={
                "run_id": first.run_id,
                "provider": legacy_artifact["provider"],
                "model": legacy_artifact["model"],
                "document_input": "markdown",
                "source_documents": [legacy_artifact["source_path"]],
                "source_artifacts": [ARTIFACT_PATH.as_posix()],
            },
            data_contract_schema=compile_canonical_extraction_contract(schema),
            created_at="2026-08-19T00:00:00Z",
        )
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary:
            collision_path = Path(temporary) / "collision.json"
            collision_path.write_text(
                dumps_json(collision, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "run ID collision"):
                load_extraction_artifact(
                    database_url=database_url,
                    manifest=manifest,
                    schema_path=SCHEMA_PATH,
                    artifact_path=collision_path,
                    insurer_code="cover_more",
                )

        self.assertEqual(second_counts, _row_counts(database_url, extension.table))


def _row_counts(database_url: str, extension_table) -> tuple[int, ...]:
    tables = [*storage_metadata.sorted_tables, extension_table]
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return tuple(
                connection.scalar(select(func.count()).select_from(table)) or 0
                for table in tables
            )
    finally:
        engine.dispose()


def _columns_are_jsonb(database_url: str, extension_table) -> bool:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            raw_type = connection.scalar(
                select(func.pg_typeof(storage_metadata.tables["raw_extractions"].c.artifact))
                .select_from(storage_metadata.tables["raw_extractions"])
                .limit(1)
            )
            extension_type = connection.scalar(
                select(func.pg_typeof(extension_table.c.attributes))
                .select_from(extension_table)
                .limit(1)
            )
    finally:
        engine.dispose()
    return str(raw_type) == "jsonb" and str(extension_type) == "jsonb"


if __name__ == "__main__":
    unittest.main()
