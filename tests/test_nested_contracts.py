from __future__ import annotations

import copy
import unittest

from collections.abc import Callable, Iterator

from src.common.json_contracts import ContractValidationError, validate_inline_contract
from src.schema.canonical import (
    compile_canonical_extraction_contract,
    validate_canonical_schema,
)
from src.schema.contract import compile_extraction_contract
from src.schema.validation import validate_field_payload, validate_schema_mapping
from tests.test_canonical_schema import approved_travel_schema


def closed_benefit_item_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["benefit_name", "limit", "evidence"],
        "properties": {
            "benefit_name": {"type": ["string", "null"]},
            "limit": {
                "type": ["object", "null"],
                "additionalProperties": False,
                "required": ["amount_aud", "period"],
                "properties": {
                    "amount_aud": {"type": ["number", "null"]},
                    "period": {
                        "type": ["string", "null"],
                        "enum": ["per_year", "per_claim", None],
                    },
                },
            },
            "evidence": {
                "type": ["array", "null"],
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["document_id", "page"],
                    "properties": {
                        "document_id": {"type": "string"},
                        "page": {"type": ["integer", "null"]},
                    },
                },
            },
        },
    }


def discovered_travel_schema() -> dict[str, object]:
    return {
        "vertical": "travel_insurance",
        "version": "nested-regression-draft-v2",
        "description": "Synthetic nested-contract regression fixture.",
        "product_types": ["domestic"],
        "taxonomies": {"coverage_categories": []},
        "notes": [],
        "fields": [
            {
                "name": "product_type",
                "type": "enum",
                "description": "Product classification.",
                "applies_to": ["domestic"],
                "required": True,
                "values": ["domestic"],
            },
            {
                "name": "product_name",
                "type": "string",
                "description": "Published product name.",
                "applies_to": ["domestic"],
                "required": True,
                "values": [],
            },
            {
                "name": "benefits",
                "type": "list[object]",
                "description": "Benefits and their source evidence.",
                "applies_to": ["domestic"],
                "required": False,
                "values": [],
            },
        ],
    }


def canonical_travel_schema() -> dict[str, object]:
    # This is a new in-memory test fixture, never an edit to an approved config.
    schema = approved_travel_schema()
    schema["version"] = "2.0.0-nested-regression-test"
    schema["fields"] = [schema["fields"][0], schema["fields"][1], schema["fields"][-1]]
    return schema


def benefit_record() -> dict[str, object]:
    return {
        "benefit_name": "Medical cover",
        "limit": {"amount_aud": 1000, "period": "per_year"},
        "evidence": [{"document_id": "base_pds", "page": 3}],
    }


def extraction_payload(benefits: object) -> dict[str, object]:
    return {
        "products": [
            {
                "product_name": "Example plan",
                "product_type": "domestic",
                "benefits": benefits,
                "_unfilled": [],
                "_notes": None,
            }
        ],
        "_document_notes": None,
    }


class NestedExtractionContractTests(unittest.TestCase):
    def schema_variants(
        self, *, nested: bool = True,
    ) -> Iterator[tuple[str, dict[str, object], Callable, Callable]]:
        for label, schema, compile_contract, validate_schema in (
            (
                "discovered", discovered_travel_schema(),
                compile_extraction_contract, validate_schema_mapping,
            ),
            (
                "canonical", canonical_travel_schema(),
                compile_canonical_extraction_contract, validate_canonical_schema,
            ),
        ):
            if nested:
                schema["fields"][-1]["item_schema"] = closed_benefit_item_schema()
            yield label, schema, compile_contract, validate_schema

    def test_closed_nested_contract_accepts_valid_values_without_mutating_schema(self) -> None:
        for label, schema, compile_contract, _ in self.schema_variants():
            with self.subTest(schema=label):
                original = copy.deepcopy(schema)
                first = compile_contract(schema)
                self.assertEqual(first, compile_contract(schema))
                self.assertEqual(original, schema)
                validate_inline_contract(extraction_payload([benefit_record()]), first)

    def test_unknown_values_use_null_while_all_nested_keys_remain_present(self) -> None:
        examples = [
            None,
            [],
            [{"benefit_name": None, "limit": None, "evidence": None}],
            [{
                "benefit_name": None,
                "limit": {"amount_aud": None, "period": None},
                "evidence": [{"document_id": "base_pds", "page": None}],
            }],
        ]
        for label, schema, compile_contract, _ in self.schema_variants():
            with self.subTest(schema=label):
                contract = compile_contract(schema)
                for benefits in examples:
                    with self.subTest(benefits=benefits):
                        validate_inline_contract(extraction_payload(benefits), contract)

    def test_rejects_extra_keys_at_each_nested_object_level(self) -> None:
        examples = []
        for location in ("benefit", "limit", "evidence"):
            record = benefit_record()
            target = {
                "benefit": record,
                "limit": record["limit"],
                "evidence": record["evidence"][0],
            }[location]
            target["invented"] = True
            examples.append((location, record))
        for label, schema, compile_contract, _ in self.schema_variants():
            with self.subTest(schema=label):
                contract = compile_contract(schema)
                for location, record in examples:
                    with self.subTest(location=location):
                        with self.assertRaises(ContractValidationError):
                            validate_inline_contract(extraction_payload([record]), contract)

    def test_rejects_missing_nested_keys_including_nullable_ones(self) -> None:
        examples = []
        record = benefit_record()
        record.pop("evidence")
        examples.append(record)
        record = benefit_record()
        record["limit"].pop("amount_aud")
        examples.append(record)
        record = benefit_record()
        record["evidence"][0].pop("page")
        examples.append(record)
        for label, schema, compile_contract, _ in self.schema_variants():
            with self.subTest(schema=label):
                contract = compile_contract(schema)
                for index, record in enumerate(examples):
                    with self.subTest(example=index):
                        with self.assertRaises(ContractValidationError):
                            validate_inline_contract(extraction_payload([record]), contract)

    def test_rejects_wrong_types_and_unknown_nested_enum_values(self) -> None:
        examples = []
        for key, value in (("amount_aud", "1000"), ("period", "sometimes")):
            record = benefit_record()
            record["limit"][key] = value
            examples.append(record)
        record = benefit_record()
        record["evidence"][0]["page"] = 3.5
        examples.append(record)
        record = benefit_record()
        record["evidence"] = ["base_pds"]
        examples.append(record)
        record = benefit_record()
        record["limit"] = []
        examples.append(record)
        for label, schema, compile_contract, _ in self.schema_variants():
            with self.subTest(schema=label):
                contract = compile_contract(schema)
                for index, record in enumerate(examples):
                    with self.subTest(example=index):
                        with self.assertRaises(ContractValidationError):
                            validate_inline_contract(extraction_payload([record]), contract)

    def test_rejects_open_or_incompletely_required_item_schema(self) -> None:
        invalid_schemas = []
        for location in ("benefit", "limit", "evidence"):
            for defect in ("open", "implicit_open", "optional_key"):
                item_schema = closed_benefit_item_schema()
                target = {
                    "benefit": item_schema,
                    "limit": item_schema["properties"]["limit"],
                    "evidence": item_schema["properties"]["evidence"]["items"],
                }[location]
                if defect == "open":
                    target["additionalProperties"] = True
                elif defect == "implicit_open":
                    target.pop("additionalProperties")
                else:
                    target["required"] = target["required"][:-1]
                invalid_schemas.append((location, defect, item_schema))
        for label, schema, _, validate_schema in self.schema_variants():
            with self.subTest(schema=label):
                validate_schema(schema)
                for location, defect, item_schema in invalid_schemas:
                    invalid = copy.deepcopy(schema)
                    invalid["fields"][-1]["item_schema"] = item_schema
                    with self.subTest(location=location, defect=defect):
                        with self.assertRaises(ValueError):
                            validate_schema(invalid)

    def test_field_validator_does_not_ignore_open_nested_schema(self) -> None:
        field = discovered_travel_schema()["fields"][-1]
        field["item_schema"] = closed_benefit_item_schema()
        validate_field_payload(field, ["domestic"])
        field["item_schema"]["properties"]["limit"]["additionalProperties"] = True
        with self.assertRaises(ValueError):
            validate_field_payload(field, ["domestic"])

    def test_scalar_field_cannot_declare_item_schema(self) -> None:
        field = discovered_travel_schema()["fields"][1]
        field["item_schema"] = closed_benefit_item_schema()
        with self.assertRaises(ValueError):
            validate_field_payload(field, ["domestic"])

    def test_top_level_local_definitions_work_for_both_compilers(self) -> None:
        for label, schema, compile_contract, _ in self.schema_variants():
            with self.subTest(schema=label):
                schema["$defs"] = {"Benefit": closed_benefit_item_schema()}
                schema["fields"][-1]["item_schema"] = {"$ref": "#/$defs/Benefit"}
                original = copy.deepcopy(schema)
                contract = compile_contract(schema)
                validate_inline_contract(extraction_payload([benefit_record()]), contract)
                invalid = benefit_record()
                invalid["limit"]["amount_aud"] = "1000"
                with self.assertRaises(ContractValidationError):
                    validate_inline_contract(extraction_payload([invalid]), contract)
                self.assertEqual(original, schema)

    def test_rejects_remote_missing_and_cyclic_refs_without_runtime_resolution(self) -> None:
        invalid_definitions = (
            {"Benefit": {"$ref": "https://example.invalid/benefit.json"}},
            {"Benefit": {"$ref": "#/$defs/Missing"}},
            {"Benefit": {"$ref": "#/$defs/Benefit"}},
            {"Benefit": {"$ref": "#/$defs/Other"}, "Other": {"$ref": "#/$defs/Benefit"}},
        )
        for label, schema, compile_contract, _ in self.schema_variants():
            with self.subTest(schema=label):
                schema["$defs"] = {"Benefit": closed_benefit_item_schema()}
                schema["fields"][-1]["item_schema"] = {"$ref": "#/$defs/Benefit"}
                compile_contract(schema)
                for index, definitions in enumerate(invalid_definitions):
                    invalid = copy.deepcopy(schema)
                    invalid["$defs"] = definitions
                    with self.subTest(reference=index):
                        with self.assertRaises(ValueError):
                            compile_contract(invalid)

    def test_canonical_candidate_accepts_nested_schema_but_still_requires_approval(self) -> None:
        candidate = canonical_travel_schema()
        candidate["fields"][-1]["item_schema"] = closed_benefit_item_schema()
        candidate["status"] = "candidate"
        candidate["review"] = None
        validate_canonical_schema(candidate)
        with self.assertRaisesRegex(ValueError, "human-approved"):
            compile_canonical_extraction_contract(candidate)

    def test_canonical_non_nullable_list_rejects_null(self) -> None:
        schema = canonical_travel_schema()
        schema["fields"][-1]["item_schema"] = closed_benefit_item_schema()
        schema["fields"][-1]["nullable"] = False
        contract = compile_canonical_extraction_contract(schema)
        validate_inline_contract(extraction_payload([]), contract)
        with self.assertRaises(ContractValidationError):
            validate_inline_contract(extraction_payload(None), contract)

    def test_historical_lists_without_item_schema_keep_their_open_shape(self) -> None:
        for label, schema, compile_contract, _ in self.schema_variants(nested=False):
            with self.subTest(schema=label):
                contract = compile_contract(schema)
                historical = [{"historical_key": {"free_form": True}}]
                validate_inline_contract(extraction_payload(historical), contract)
                validate_inline_contract(extraction_payload(None), contract)
                with self.assertRaises(ContractValidationError):
                    validate_inline_contract(extraction_payload(["not_an_object"]), contract)

    def test_legacy_item_fields_are_explicitly_converted_without_changing_source(self) -> None:
        schema = discovered_travel_schema()
        schema["fields"][-1]["item_fields"] = [
            {
                "name": "amount_aud", "type": "number", "required": False,
                "description": "Declared amount.", "values": [],
            },
            {
                "name": "period", "type": "enum", "required": False,
                "description": "Declared period.", "values": ["per_year", "per_claim"],
            },
            {
                "name": "covered", "type": "boolean", "required": True,
                "description": "Whether the benefit is covered.", "values": [],
            },
        ]
        original = copy.deepcopy(schema)
        normalized = validate_schema_mapping(schema)
        self.assertIn("item_schema", normalized["fields"][-1])
        self.assertEqual(original, schema)
        contract = compile_extraction_contract(schema)
        unknown_amount = [{"amount_aud": None, "period": None, "covered": True}]
        validate_inline_contract(extraction_payload(unknown_amount), contract)
        for record in (
            {"amount_aud": "1000", "period": "per_year", "covered": True},
            {"amount_aud": 1000, "period": "unknown", "covered": True},
            {"amount_aud": 1000, "period": "per_year", "covered": None},
            {"amount_aud": 1000, "period": "per_year", "covered": True, "invented": 1},
            {"period": "per_year", "covered": True},
        ):
            with self.subTest(record=record):
                with self.assertRaises(ContractValidationError):
                    validate_inline_contract(extraction_payload([record]), contract)


if __name__ == "__main__":
    unittest.main()
