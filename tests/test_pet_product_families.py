from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.common.json_contracts import validate_inline_contract
from src.common.model_provider import ModelResponse, ProviderRequest
from src.schema.contract import (
    compile_pet_extraction_contract,
    compile_pet_product_family_contract,
)
from src.schema_application.extraction_validation import (
    normalize_pet_benefit_categories,
    normalize_pet_unfilled,
    validate_pet_benefit_category_consistency,
    validate_pet_benefit_coverage_evidence,
    validate_pet_product_inventory,
    validate_pet_unfilled_consistency,
)
from src.schema_application.extractor import SchemaExtractor
from src.schema_application.product_families import (
    ProductFamily,
    resolve_product_families,
    select_manifest_product_paths,
)
from src.schema_application.prompts import (
    PET_PRODUCT_FAMILY_PROMPT,
    PET_PRODUCT_FAMILY_PROMPT_VERSION,
)


def _field(
    name: str,
    field_type: str,
    *,
    enum_ref: str | None = None,
    required: bool = False,
    item_fields: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "name": name,
        "type": field_type,
        "description": name.replace("_", " "),
        "applies_to": ["product"],
        "required": required,
        "values": [],
        "aliases": [],
        "enum_ref": enum_ref,
        "item_fields": item_fields or [],
        "unique_items": field_type.startswith("list[") and field_type != "list[object]",
    }


PET_SCHEMA = {
    "vertical": "pet_insurance",
    "version": "test",
    "description": "Pet product schema",
    "cover_scopes": ["accident_only", "accident_and_illness"],
    "document_roles": [
        "pds", "update", "combined_fsg_pds", "supplementary_pds",
        "policy_booklet", "renewal_pds",
    ],
    "benefit_categories": ["accidents"],
    "fields": [
        _field("product_id", "string", required=True),
        _field("product_name", "string", required=True),
        _field("insurer_name", "string", required=True),
        _field("co_payment_percentage", "number"),
        _field("covered_benefit_categories", "list[enum]", enum_ref="benefit_categories"),
        _field("benefit_coverages", "list[object]", item_fields=[
            {
                "name": "benefit_category", "type": "enum", "required": True,
                "description": "Canonical benefit category.", "values": [],
                "enum_ref": "benefit_categories",
            },
            {
                "name": "coverage_status", "type": "enum", "required": True,
                "description": "Included, optional, or excluded.",
                "values": ["included", "optional", "excluded"], "enum_ref": None,
            },
            {
                "name": "source_document_id", "type": "string", "required": True,
                "description": "Evidence document identifier.", "values": [],
                "enum_ref": None,
            },
            {
                "name": "source_block_id", "type": "string", "required": True,
                "description": "Evidence block identifier.", "values": [],
                "enum_ref": None,
            },
            {
                "name": "source_page", "type": "number", "required": True,
                "description": "Evidence page.", "values": [], "enum_ref": None,
            },
            {
                "name": "source_quote", "type": "string", "required": True,
                "description": "Verbatim evidence excerpt.", "values": [],
                "enum_ref": None,
            },
        ]),
        _field("annual_benefit_limit_options_aud", "list[number]"),
        _field("benefit_percentage_options", "list[number]"),
        _field("excess_options_aud", "list[number]"),
        _field("temporary_condition_reinstatement_after_months", "number"),
    ],
    "notes": [],
}


class ProductFamilyTest(unittest.TestCase):
    def test_family_prompt_distinguishes_plan_columns_scope_and_subyear_ages(self) -> None:
        self.assertEqual(PET_PRODUCT_FAMILY_PROMPT_VERSION, "pet-product-family.v7")
        self.assertIn("benefit_coverages", PET_PRODUCT_FAMILY_PROMPT)
        self.assertIn("source_block_id", PET_PRODUCT_FAMILY_PROMPT)
        self.assertIn("source_quote", PET_PRODUCT_FAMILY_PROMPT)
        self.assertIn("silence into excluded", PET_PRODUCT_FAMILY_PROMPT)
        self.assertIn("Never combine two plan", PET_PRODUCT_FAMILY_PROMPT)
        self.assertIn("names into a hybrid product", PET_PRODUCT_FAMILY_PROMPT)
        self.assertIn("specified_conditions_only", PET_PRODUCT_FAMILY_PROMPT)
        self.assertIn("364 days is approximately 0.997 years", PET_PRODUCT_FAMILY_PROMPT)

    def test_groups_base_and_update_and_inventories_multi_product_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "raw"
            raw.mkdir()
            base = raw / "plans.pdf"
            update = raw / "plans-update.pdf"
            base.touch()
            update.touch()
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "source_root": str(raw),
                "documents": [
                    {
                        "document_id": "plans_base",
                        "source_path": str(base),
                        "document_role": "pds",
                        "document_date": "2024-01-01",
                        "document_family_id": "plans",
                        "amends_document_ids": [],
                        "products": [
                            {"product_id": "basic", "product_name_hint": "Basic"},
                            {"product_id": "top", "product_name_hint": "Top"},
                        ],
                    },
                    {
                        "document_id": "plans_update",
                        "source_path": str(update),
                        "document_role": "update",
                        "document_date": "2025-01-01",
                        "document_family_id": "plans",
                        "amends_document_ids": ["plans_base"],
                        "products": [],
                    },
                ],
            }), encoding="utf-8")

            families = resolve_product_families(manifest, [update, base])

            self.assertEqual(len(families), 1)
            self.assertEqual(families[0].pdf_paths, (base.resolve(), update.resolve()))
            self.assertEqual(families[0].expected_product_ids, ["basic", "top"])

            targeted = select_manifest_product_paths(manifest, ["top"])
            self.assertEqual(targeted, [base.resolve(), update.resolve()])

    def test_targeted_product_selection_rejects_unknown_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(json.dumps({"documents": []}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Unknown manifest product_id"):
                select_manifest_product_paths(manifest, ["missing"])

    def test_rejects_pdf_missing_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "unknown.pdf"
            pdf.touch()
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"documents": []}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "every PDF in the manifest"):
                resolve_product_families(manifest, [pdf])

    def test_does_not_attach_update_without_explicit_amends_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "new-base.pdf"
            old_update = root / "old-update.pdf"
            base.touch()
            old_update.touch()
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"documents": [
                {
                    "document_id": "new_base", "source_path": str(base),
                    "document_role": "pds", "document_date": "2025-01-01",
                    "document_family_id": "brand", "amends_document_ids": [],
                    "products": [{"product_id": "brand", "product_name_hint": "Brand"}],
                },
                {
                    "document_id": "old_update", "source_path": str(old_update),
                    "document_role": "update", "document_date": "2024-01-01",
                    "document_family_id": "brand", "amends_document_ids": [],
                    "products": [],
                },
            ]}), encoding="utf-8")

            family = resolve_product_families(manifest, [base, old_update])[0]

            self.assertEqual(family.pdf_paths, (base.resolve(),))
            self.assertEqual(
                [item["document_id"] for item in family.unattached_documents],
                ["old_update"],
            )

    def test_keeps_independent_base_pds_files_separate_within_one_family(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            basic = root / "basic.pdf"
            premium = root / "premium.pdf"
            premium_update = root / "premium-update.pdf"
            for path in (basic, premium, premium_update):
                path.touch()
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"documents": [
                {
                    "document_id": "basic_pds", "source_path": str(basic),
                    "document_role": "pds", "document_family_id": "brand_suite",
                    "amends_document_ids": [],
                    "products": [{"product_id": "basic", "product_name_hint": "Basic"}],
                },
                {
                    "document_id": "premium_pds", "source_path": str(premium),
                    "document_role": "pds", "document_family_id": "brand_suite",
                    "amends_document_ids": [],
                    "products": [{"product_id": "premium", "product_name_hint": "Premium"}],
                },
                {
                    "document_id": "premium_update", "source_path": str(premium_update),
                    "document_role": "update", "document_family_id": "brand_suite",
                    "amends_document_ids": ["premium_pds"], "products": [],
                },
            ]}), encoding="utf-8")

            families = resolve_product_families(manifest, [basic, premium, premium_update])
            by_product = {
                family.expected_product_ids[0]: [path.name for path in family.pdf_paths]
                for family in families
            }

            self.assertEqual(by_product, {
                "basic": ["basic.pdf"],
                "premium": ["premium.pdf", "premium-update.pdf"],
            })

    def test_family_contract_requires_one_item_per_known_product_id(self) -> None:
        contract = compile_pet_product_family_contract(
            PET_SCHEMA,
            document_family_id="plans",
            document_count=2,
            expected_product_ids=["basic", "top"],
        )
        blank_product = {
            "product_id": "basic",
            "product_name": "Basic",
            "insurer_name": "Example Insurer",
            "co_payment_percentage": None,
            "covered_benefit_categories": None,
            "benefit_coverages": None,
            "annual_benefit_limit_options_aud": None,
            "benefit_percentage_options": None,
            "excess_options_aud": None,
            "temporary_condition_reinstatement_after_months": None,
            "_unfilled": [
                "co_payment_percentage", "covered_benefit_categories", "benefit_coverages",
                "annual_benefit_limit_options_aud", "benefit_percentage_options",
                "excess_options_aud", "temporary_condition_reinstatement_after_months",
            ],
            "_notes": None,
        }
        source_documents = [
            {
                "document_id": "base", "document_role": "pds",
                "effective_date": None, "source_path": "base.pdf",
            },
            {
                "document_id": "update", "document_role": "update",
                "effective_date": None, "source_path": "update.pdf",
            },
        ]
        validate_inline_contract({
            "document_family_id": "plans",
            "source_documents": source_documents,
            "products": [blank_product, {**blank_product, "product_id": "top", "product_name": "Top"}],
            "_notes": None,
        }, contract)

        with self.assertRaises(ValueError):
            validate_inline_contract({
                "document_family_id": "plans",
                "source_documents": source_documents,
                "products": [blank_product],
                "_notes": None,
            }, contract)

    def test_document_contract_rejects_empty_or_incomplete_product_inventory(self) -> None:
        one_product = compile_pet_extraction_contract(
            PET_SCHEMA,
            document_role="pds",
            expected_product_ids=["basic"],
        )["properties"]["products"]
        two_products = compile_pet_extraction_contract(
            PET_SCHEMA,
            document_role="pds",
            expected_product_ids=["basic", "top"],
        )["properties"]["products"]
        unknown_inventory = compile_pet_extraction_contract(
            PET_SCHEMA,
            document_role="pds",
        )["properties"]["products"]

        self.assertEqual(one_product["minItems"], 1)
        self.assertEqual(one_product["maxItems"], 1)
        self.assertEqual(two_products["minItems"], 2)
        self.assertEqual(two_products["maxItems"], 2)
        self.assertEqual(unknown_inventory["minItems"], 1)
        self.assertNotIn("maxItems", unknown_inventory)

    def test_family_extraction_reads_all_pdfs_and_returns_effective_products(self) -> None:
        class StaticProvider:
            def generate(self, request: ProviderRequest) -> ModelResponse:
                product = {
                    "product_id": "basic",
                    "product_name": "Basic",
                    "insurer_name": "Example Insurer",
                    "co_payment_percentage": None,
                    "covered_benefit_categories": None,
                    "benefit_coverages": None,
                    "annual_benefit_limit_options_aud": None,
                    "benefit_percentage_options": None,
                    "excess_options_aud": None,
                    "temporary_condition_reinstatement_after_months": None,
                    "_unfilled": [
                        "co_payment_percentage", "covered_benefit_categories", "benefit_coverages",
                        "annual_benefit_limit_options_aud", "benefit_percentage_options",
                        "excess_options_aud",
                        "temporary_condition_reinstatement_after_months",
                    ],
                    "_notes": "Update applied",
                }
                return ModelResponse(
                    text=json.dumps({
                        "document_family_id": "plans",
                        "source_documents": [
                            {"document_id": "base", "document_role": "pds", "effective_date": None, "source_path": "base.pdf"},
                            {"document_id": "update", "document_role": "update", "effective_date": None, "source_path": "update.pdf"},
                        ],
                        "products": [product],
                        "_notes": None,
                    }),
                    provider=request.selection.provider,
                    model=request.selection.model,
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base.pdf"
            update = root / "update.pdf"
            base.touch()
            update.touch()
            family = ProductFamily(
                family_id="plans",
                documents=(
                    {"document_id": "base", "document_role": "pds", "document_date": "2024-01-01", "source_path": "base.pdf"},
                    {"document_id": "update", "document_role": "update", "document_date": "2025-01-01", "source_path": "update.pdf"},
                ),
                pdf_paths=(base, update),
                expected_products=({"product_id": "basic", "product_name_hint": "Basic"},),
            )
            extractor = SchemaExtractor(
                schema_data=PET_SCHEMA,
                provider=StaticProvider(),
                pdf_root=root,
                usage_log_path=None,
                log=None,
            )
            with mock.patch(
                "src.schema_application.extractor.ingest_pdfs", return_value=(),
            ) as ingest, mock.patch(
                "src.schema_application.extractor.document_quality",
                return_value={"hard_failures": [], "has_key_heading": True},
            ), mock.patch(
                "src.schema_application.extractor.render_documents_for_prompt",
                return_value="# base\n# update",
            ), mock.patch(
                "src.schema_application.extractor._pet_evidence_blocks",
                return_value={},
            ):
                result = extractor.extract_product_family(family)

            self.assertEqual(ingest.call_args.args[0], (base, update))
            self.assertEqual([item["product_id"] for item in result["products"]], ["basic"])
            self.assertEqual(
                [item["source_path"] for item in result["source_documents"]],
                ["base.pdf", "update.pdf"],
            )

    def test_pet_unfilled_validator_rejects_null_and_populated_mismatches(self) -> None:
        product = {
            "product_id": "basic", "product_name": "Basic",
            "insurer_name": "Example Insurer",
            "co_payment_percentage": None,
            "covered_benefit_categories": None,
            "benefit_coverages": None,
            "annual_benefit_limit_options_aud": None,
            "benefit_percentage_options": None,
            "excess_options_aud": None,
            "temporary_condition_reinstatement_after_months": None,
            "_unfilled": [], "_notes": None,
        }
        with self.assertRaisesRegex(ValueError, "null field 'co_payment_percentage'"):
            validate_pet_unfilled_consistency({"products": [product]}, PET_SCHEMA)

        valid = {**product, "_unfilled": [
            "co_payment_percentage", "covered_benefit_categories", "benefit_coverages",
            "annual_benefit_limit_options_aud", "benefit_percentage_options",
            "excess_options_aud", "temporary_condition_reinstatement_after_months",
        ]}
        validate_pet_unfilled_consistency({"products": [valid]}, PET_SCHEMA)
        with self.assertRaisesRegex(ValueError, "required field 'product_name'"):
            validate_pet_unfilled_consistency({
                "products": [{**valid, "product_name": "   "}],
            }, PET_SCHEMA)
        with self.assertRaisesRegex(ValueError, "populated field 'product_id'"):
            validate_pet_unfilled_consistency({
                "products": [{**valid, "_unfilled": [*valid["_unfilled"], "product_id"]}],
            }, PET_SCHEMA)

    def test_normalizes_unfilled_bookkeeping_without_changing_policy_values(self) -> None:
        product = {
            "product_id": "basic", "product_name": "Basic",
            "insurer_name": "Example Insurer", "co_payment_percentage": 20,
            "covered_benefit_categories": None, "benefit_coverages": None,
            "annual_benefit_limit_options_aud": None,
            "benefit_percentage_options": [80], "excess_options_aud": None,
            "temporary_condition_reinstatement_after_months": None,
            "_unfilled": [], "_notes": None,
        }
        payload = {"products": [product]}

        normalize_pet_unfilled(payload, PET_SCHEMA)
        validate_pet_unfilled_consistency(payload, PET_SCHEMA)

        self.assertEqual(product["benefit_percentage_options"], [80])
        self.assertIn("covered_benefit_categories", product["_unfilled"])
        self.assertNotIn("co_payment_percentage", product["_unfilled"])

    def test_derives_included_categories_from_authoritative_coverage_rows(self) -> None:
        product = {
            "covered_benefit_categories": [],
            "benefit_coverages": [
                {
                    "benefit_category": "accidents",
                    "coverage_status": "included",
                    "source_document_id": "base",
                    "source_block_id": "p4_text_1",
                    "source_page": 4,
                    "source_quote": "Accidental injury treatment is covered.",
                },
            ],
        }
        payload = {"products": [product]}

        with self.assertRaisesRegex(ValueError, "must equal"):
            validate_pet_benefit_category_consistency(payload)
        normalize_pet_benefit_categories(payload)
        validate_pet_benefit_category_consistency(payload)

        self.assertEqual(product["covered_benefit_categories"], ["accidents"])

    def test_rejects_duplicate_benefit_coverage_categories(self) -> None:
        coverage = {
            "benefit_category": "accidents",
            "coverage_status": "included",
            "source_document_id": "base",
            "source_block_id": "p4_text_1",
            "source_page": 4,
            "source_quote": "Accidental injury treatment is covered.",
        }
        with self.assertRaisesRegex(ValueError, "duplicate categories"):
            validate_pet_benefit_category_consistency({
                "products": [{
                    "covered_benefit_categories": ["accidents"],
                    "benefit_coverages": [coverage, dict(coverage)],
                }],
            })

    def test_coverage_evidence_must_exist_on_the_cited_source_page(self) -> None:
        payload = {"products": [{"benefit_coverages": [{
            "benefit_category": "accidents",
            "coverage_status": "included",
            "source_document_id": "base",
            "source_block_id": "p4_text_1",
            "source_page": 4,
            "source_quote": "Accidental injury treatment is covered.",
        }]}]}
        blocks = {
            "base": {
                "p4_text_1": (4, "Benefits: Accidental injury treatment is covered."),
            },
        }

        validate_pet_benefit_coverage_evidence(payload, blocks)

        payload["products"][0]["benefit_coverages"][0]["source_quote"] = (
            "Holiday cancellation costs are excluded."
        )
        with self.assertRaisesRegex(ValueError, "not supported"):
            validate_pet_benefit_coverage_evidence(payload, blocks)

    def test_coverage_evidence_rejects_wrong_page_or_unknown_block(self) -> None:
        coverage = {
            "benefit_category": "accidents",
            "coverage_status": "included",
            "source_document_id": "base",
            "source_block_id": "p4_text_1",
            "source_page": 3,
            "source_quote": "Accidental injury treatment is covered.",
        }
        payload = {"products": [{"benefit_coverages": [coverage]}]}
        blocks = {"base": {"p4_text_1": (4, "Accidental injury treatment is covered.")}}

        with self.assertRaisesRegex(ValueError, "does not match"):
            validate_pet_benefit_coverage_evidence(payload, blocks)
        coverage["source_page"] = 4
        coverage["source_block_id"] = "invented"
        with self.assertRaisesRegex(ValueError, "source_block_id"):
            validate_pet_benefit_coverage_evidence(payload, blocks)

    def test_coverage_evidence_rejects_irrelevant_third_party_block(self) -> None:
        payload = {"products": [{"benefit_coverages": [{
            "benefit_category": "third_party_property_damage_liability",
            "coverage_status": "excluded",
            "source_document_id": "base",
            "source_block_id": "privacy",
            "source_page": 20,
            "source_quote": "services of ours or a third party",
        }]}]}
        blocks = {
            "base": {"privacy": (20, "marketing information about services of ours or a third party")},
        }

        with self.assertRaisesRegex(ValueError, "does not contain evidence terms"):
            validate_pet_benefit_coverage_evidence(payload, blocks)

    def test_inventory_validator_rejects_duplicate_multi_plan_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly once"):
            validate_pet_product_inventory(
                {"products": [{"product_id": "basic"}, {"product_id": "basic"}]},
                ["basic", "top"],
            )


if __name__ == "__main__":
    unittest.main()
