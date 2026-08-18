from __future__ import annotations

import unittest

from sqlalchemy import Column, MetaData, String, Table, create_engine, inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from src.common.json_contracts import ContractValidationError
from src.storage.canonical import (
    compile_canonical_load_plan,
    compile_vertical_storage_metadata,
)
from tests.test_canonical_schema import approved_travel_schema


def valid_extraction_payload() -> dict[str, object]:
    return {
        "products": [
            {
                "product_name": "International Comprehensive",
                "product_type": "international_single_trip",
                "geographic_scope": "international",
                "cruise_cover_available": True,
                "benefits": [
                    {"name": "medical", "limit": "Unlimited"},
                ],
                "_unfilled": [],
                "_notes": None,
            }
        ],
        "_document_notes": None,
    }


class CanonicalStorageMetadataTests(unittest.TestCase):
    def test_compiles_extension_columns_from_approved_schema(self) -> None:
        compiled = compile_vertical_storage_metadata(approved_travel_schema())

        self.assertEqual(compiled.table.name, "travel_product_details")
        self.assertEqual(
            list(compiled.table.columns.keys()),
            [
                "release_id",
                "geographic_scope",
                "cruise_cover_available",
                "attributes",
            ],
        )
        self.assertFalse(compiled.table.c.release_id.nullable)
        self.assertTrue(compiled.table.c.geographic_scope.nullable)
        self.assertNotIn("benefits", compiled.table.c)

    def test_generated_table_uses_jsonb_and_enum_check_on_postgresql(self) -> None:
        compiled = compile_vertical_storage_metadata(approved_travel_schema())

        ddl = str(
            CreateTable(compiled.table).compile(dialect=postgresql.dialect())
        )

        self.assertIn("attributes JSONB NOT NULL", ddl)
        self.assertIn("CHECK", ddl)
        self.assertIn("international", ddl)
        self.assertIn("FOREIGN KEY(release_id)", ddl)

    def test_generated_table_can_be_created_after_core_release_table(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        core = MetaData()
        Table(
            "product_releases",
            core,
            Column("release_id", String(71), primary_key=True),
        )
        core.create_all(engine)
        compiled = compile_vertical_storage_metadata(approved_travel_schema())

        compiled.create(engine)
        compiled.create(engine)

        self.assertIn("travel_product_details", inspect(engine).get_table_names())

    def test_candidate_cannot_compile_storage_metadata(self) -> None:
        schema = approved_travel_schema()
        schema["status"] = "candidate"
        schema["review"] = None

        with self.assertRaisesRegex(ValueError, "human-approved"):
            compile_vertical_storage_metadata(schema)


class CanonicalLoadPlanTests(unittest.TestCase):
    def test_compiles_core_extension_and_jsonb_values(self) -> None:
        plan = compile_canonical_load_plan(
            approved_travel_schema(),
            valid_extraction_payload(),
        )

        self.assertEqual(plan.vertical, "travel_insurance")
        self.assertEqual(plan.schema_version, "1.0.0")
        self.assertEqual(plan.extension_table, "travel_product_details")
        self.assertEqual(len(plan.products), 1)
        product = plan.products[0]
        self.assertEqual(product.product_name, "International Comprehensive")
        self.assertEqual(product.product_type, "international_single_trip")
        self.assertEqual(
            product.core_values,
            {
                "products.canonical_name": "International Comprehensive",
                "product_releases.source_product_type": (
                    "international_single_trip"
                ),
            },
        )
        self.assertEqual(
            product.extension_values,
            {
                "geographic_scope": "international",
                "cruise_cover_available": True,
            },
        )
        self.assertEqual(
            product.attributes,
            {"benefits": [{"name": "medical", "limit": "Unlimited"}]},
        )

    def test_same_payload_produces_equal_load_plan(self) -> None:
        schema = approved_travel_schema()
        payload = valid_extraction_payload()

        first = compile_canonical_load_plan(schema, payload)
        second = compile_canonical_load_plan(schema, payload)

        self.assertEqual(first, second)

    def test_invalid_extraction_fails_before_plan_is_produced(self) -> None:
        payload = valid_extraction_payload()
        payload["products"][0]["product_type"] = "unreviewed_type"

        with self.assertRaises(ContractValidationError):
            compile_canonical_load_plan(approved_travel_schema(), payload)

    def test_duplicate_product_names_fail_closed(self) -> None:
        payload = valid_extraction_payload()
        payload["products"].append(dict(payload["products"][0]))

        with self.assertRaisesRegex(ValueError, "duplicate product identity"):
            compile_canonical_load_plan(approved_travel_schema(), payload)


if __name__ == "__main__":
    unittest.main()
