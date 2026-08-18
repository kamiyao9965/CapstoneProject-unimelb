from __future__ import annotations

import unittest

from sqlalchemy import create_engine, inspect
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
    "travel_product_details",
}


class StorageSchemaTest(unittest.TestCase):
    def test_create_storage_schema_is_idempotent_on_empty_database(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")

        create_storage_schema(engine)
        create_storage_schema(engine)

        self.assertEqual(set(inspect(engine).get_table_names()), EXPECTED_TABLES)

    def test_core_tables_have_primary_foreign_and_unique_constraints(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        create_storage_schema(engine)
        inspector = inspect(engine)

        document_primary_key = inspector.get_pk_constraint("documents")
        self.assertEqual(document_primary_key["constrained_columns"], ["document_id"])
        release_foreign_keys = inspector.get_foreign_keys("product_releases")
        self.assertEqual(
            {foreign_key["referred_table"] for foreign_key in release_foreign_keys},
            {"documents", "products", "schema_versions"},
        )
        product_uniques = inspector.get_unique_constraints("products")
        self.assertIn(
            ["vertical_code", "insurer_code", "canonical_name"],
            [constraint["column_names"] for constraint in product_uniques],
        )

    def test_postgresql_schema_uses_jsonb_for_flexible_payloads(self) -> None:
        dialect = postgresql.dialect()
        raw_ddl = str(
            CreateTable(storage_metadata.tables["raw_extractions"]).compile(
                dialect=dialect
            )
        )
        travel_ddl = str(
            CreateTable(storage_metadata.tables["travel_product_details"]).compile(
                dialect=dialect
            )
        )

        self.assertIn("artifact JSONB", raw_ddl)
        self.assertIn("attributes JSONB", travel_ddl)


if __name__ == "__main__":
    unittest.main()
