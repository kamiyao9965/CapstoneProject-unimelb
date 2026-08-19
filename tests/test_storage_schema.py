from __future__ import annotations

import unittest
from unittest import mock

from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from src.storage.schema import create_storage_schema, storage_metadata


EXPECTED_TABLES = {
    "verticals",
    "insurers",
    "documents",
    "schema_versions",
    "extraction_runs",
    "raw_extractions",
    "products",
    "product_releases",
    "product_release_documents",
}


class StorageSchemaTest(unittest.TestCase):
    def test_create_storage_schema_requests_idempotent_ddl(self) -> None:
        connection = mock.Mock()

        with mock.patch.object(storage_metadata, "create_all") as create_all:
            create_storage_schema(connection)
            create_storage_schema(connection)

        self.assertEqual(set(storage_metadata.tables), EXPECTED_TABLES)
        self.assertEqual(create_all.call_count, 2)
        create_all.assert_called_with(connection, checkfirst=True)

    def test_core_tables_have_primary_foreign_and_unique_constraints(self) -> None:
        documents = storage_metadata.tables["documents"]
        releases = storage_metadata.tables["product_releases"]
        product_table = storage_metadata.tables["products"]

        self.assertEqual(list(documents.primary_key.columns.keys()), ["document_id"])
        self.assertEqual(
            {foreign_key.column.table.name for foreign_key in releases.foreign_keys},
            {"documents", "products", "schema_versions"},
        )
        product_uniques = [
            list(constraint.columns.keys())
            for constraint in product_table.constraints
            if isinstance(constraint, UniqueConstraint)
        ]
        self.assertIn(
            ["vertical_code", "insurer_code", "canonical_name"],
            product_uniques,
        )

    def test_fixed_postgresql_schema_uses_jsonb_for_raw_payloads_only(self) -> None:
        dialect = postgresql.dialect()
        raw_ddl = str(
            CreateTable(storage_metadata.tables["raw_extractions"]).compile(
                dialect=dialect
            )
        )

        self.assertIn("artifact JSONB", raw_ddl)
        self.assertNotIn("travel_product_details", storage_metadata.tables)


if __name__ == "__main__":
    unittest.main()
