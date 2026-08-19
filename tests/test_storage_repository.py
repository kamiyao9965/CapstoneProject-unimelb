from __future__ import annotations

import unittest

from sqlalchemy.dialects import postgresql

from src.storage.canonical import (
    compile_canonical_load_plan,
    compile_vertical_storage_metadata,
)
from src.storage.service import PreparedStorageLoad
from tests.test_canonical_schema import approved_travel_schema
from tests.test_canonical_storage import valid_extraction_payload


class _MappingResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _RecordingConnection:
    def __init__(self, *, select_overrides=None):
        self.operations: list[tuple[str, str, str]] = []
        self.rows: dict[str, dict[str, object]] = {}
        self.select_overrides = select_overrides or {}

    def execute(self, statement):
        dialect = postgresql.dialect()
        if statement.is_insert:
            table_name = statement.table.name
            compiled = statement.compile(dialect=dialect)
            self.operations.append(("insert", table_name, str(compiled)))
            self.rows[table_name] = dict(compiled.params)
            return _MappingResult(None)
        if statement.is_select:
            table_name = statement.get_final_froms()[0].name
            self.operations.append(
                ("select", table_name, str(statement.compile(dialect=dialect)))
            )
            row = dict(self.rows[table_name])
            row.update(self.select_overrides.get(table_name, {}))
            return _MappingResult(row)
        raise AssertionError(f"Unexpected SQL statement: {statement}")


def prepared_load() -> PreparedStorageLoad:
    schema = approved_travel_schema()
    return PreparedStorageLoad(
        vertical="travel_insurance",
        insurer_code="cover_more",
        document_id="sha256:" + "d" * 64,
        pdf_sha256="d" * 64,
        document_type="pds",
        document_title="Travel PDS",
        source_path="/project/data/travel_insurance/raw/PDFs/cover_more/pds/travel.pdf",
        schema_version_id="sha256:" + "s" * 64,
        schema_payload=schema,
        run_id="run-1",
        provider="openai",
        model="gpt-5",
        raw_artifact={"status": "success", "data": valid_extraction_payload()},
        payload_sha256="a" * 64,
        plan=compile_canonical_load_plan(schema, valid_extraction_payload()),
    )


class DeterministicIdentityTests(unittest.TestCase):
    def test_product_identity_normalizes_case_and_surrounding_space(self) -> None:
        from src.storage.repository import deterministic_product_id

        first = deterministic_product_id(
            "travel_insurance", "cover_more", "International Comprehensive"
        )
        second = deterministic_product_id(
            "travel_insurance", "cover_more", "  INTERNATIONAL COMPREHENSIVE  "
        )

        self.assertEqual(first, second)
        self.assertTrue(first.startswith("sha256:"))

    def test_release_identity_is_stable_for_product_and_document(self) -> None:
        from src.storage.repository import deterministic_release_id

        first = deterministic_release_id("product-1", "document-1")
        second = deterministic_release_id("product-1", "document-1")

        self.assertEqual(first, second)
        self.assertNotEqual(first, deterministic_release_id("product-1", "document-2"))


class PostgreSQLRepositoryTests(unittest.TestCase):
    def test_raw_artifact_is_persisted_before_normalized_products(self) -> None:
        from src.storage.repository import write_prepared_load

        connection = _RecordingConnection()
        prepared = prepared_load()
        extension = compile_vertical_storage_metadata(prepared.schema_payload)

        summary = write_prepared_load(connection, prepared, extension.table)

        inserted_tables = [
            table for operation, table, _sql in connection.operations if operation == "insert"
        ]
        self.assertLess(
            inserted_tables.index("raw_extractions"),
            inserted_tables.index("products"),
        )
        self.assertTrue(
            all(
                "ON CONFLICT" in sql
                for operation, _table, sql in connection.operations
                if operation == "insert"
            )
        )
        release_sql = next(
            sql
            for operation, table, sql in connection.operations
            if operation == "insert" and table == "product_releases"
        )
        self.assertIn("DO UPDATE", release_sql)
        self.assertEqual(summary.products_loaded, 1)
        self.assertEqual(summary.run_id, "run-1")

    def test_uses_canonical_attributes_column_instead_of_hardcoding_travel_name(self) -> None:
        from src.storage.repository import write_prepared_load

        prepared = prepared_load()
        prepared.schema_payload["extension"]["attributes_column"] = "details_json"
        extension = compile_vertical_storage_metadata(prepared.schema_payload)
        connection = _RecordingConnection()

        write_prepared_load(connection, prepared, extension.table)

        self.assertIn("details_json", connection.rows[extension.table.name])
        self.assertNotIn("attributes", connection.rows[extension.table.name])

    def test_existing_run_with_different_payload_fails_closed(self) -> None:
        from src.storage.repository import write_prepared_load

        connection = _RecordingConnection(
            select_overrides={"raw_extractions": {"payload_sha256": "different"}}
        )
        prepared = prepared_load()
        extension = compile_vertical_storage_metadata(prepared.schema_payload)

        with self.assertRaisesRegex(ValueError, "run ID collision"):
            write_prepared_load(connection, prepared, extension.table)


if __name__ == "__main__":
    unittest.main()
