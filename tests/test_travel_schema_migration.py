from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.common.json_contracts import validate_contract, validate_inline_contract
from src.common.model_config import ModelSelection
from src.common.model_provider import ModelResponse, ProviderRequest
from src.schema.contract import compile_extraction_contract
from src.schema.discovery import SchemaDiscovery
from src.schema_application.extractor import SchemaExtractor
from src.verticals.manifest import PROJECT_ROOT, load_vertical_manifest
from src.verticals.registry import (
    get_prompt,
    get_schema_validator,
)
from tests.test_canonical_schema import approved_travel_schema
from tests.test_canonical_storage import valid_extraction_payload


VALID_TRAVEL_SCHEMA = {
    "vertical": "travel_insurance",
    "version": "0.1-draft",
    "description": "Reusable Australian travel insurance product schema",
    "product_types": [
        "international_single_trip",
        "international_multi_trip",
        "domestic",
    ],
    "product_type_field": {
        "name": "product_type",
        "type": "enum",
        "description": "Product journey and trip-frequency classification",
        "applies_to": [
            "international_single_trip",
            "international_multi_trip",
            "domestic",
        ],
        "required": True,
        "values": [
            "international_single_trip",
            "international_multi_trip",
            "domestic",
        ],
        "aliases": ["plan type"],
    },
    "fields": [
        {
            "name": "product_name",
            "type": "string",
            "description": "Insurer-facing plan or product name",
            "applies_to": [
                "international_single_trip",
                "international_multi_trip",
                "domestic",
            ],
            "required": True,
            "values": [],
            "aliases": ["plan name"],
        },
        {
            "name": "benefits",
            "type": "list[object]",
            "description": "Benefit limits and conditions shown for this plan",
            "applies_to": [
                "international_single_trip",
                "international_multi_trip",
                "domestic",
            ],
            "required": False,
            "values": [],
            "aliases": ["schedule of benefits"],
        },
    ],
    "coverage_categories": [
        {
            "canonical_name": "medical_and_dental",
            "description": "Overseas emergency medical and dental cover",
            "aliases": ["medical expenses"],
        }
    ],
    "notes": [],
}


VALID_TRAVEL_EXTRACTION = {
    "products": [
        {
            "product_type": "international_single_trip",
            "product_name": "Comprehensive",
            "benefits": [{"benefit": "Medical", "limit": "Unlimited"}],
            "_unfilled": [],
            "_notes": None,
        },
        {
            "product_type": "domestic",
            "product_name": "Domestic",
            "benefits": None,
            "_unfilled": ["benefits"],
            "_notes": None,
        },
    ],
    "_document_notes": "One PDS describes two plans.",
}


class TravelSchemaContractTest(unittest.TestCase):
    def test_travel_discovered_schema_passes_contract_and_business_validation(self) -> None:
        validate_contract(VALID_TRAVEL_SCHEMA, "travel_insurance/discovered_schema")
        validator = get_schema_validator("travel_insurance_schema_v1")

        self.assertEqual(validator(VALID_TRAVEL_SCHEMA)["vertical"], "travel_insurance")

    def test_travel_validator_rejects_product_type_mismatch(self) -> None:
        payload = json.loads(json.dumps(VALID_TRAVEL_SCHEMA))
        payload["product_type_field"]["values"] = ["domestic"]

        with self.assertRaisesRegex(ValueError, "values.*product_types"):
            get_schema_validator("travel_insurance_schema_v1")(payload)

    def test_travel_contract_requires_explicit_product_type_field(self) -> None:
        payload = json.loads(json.dumps(VALID_TRAVEL_SCHEMA))
        del payload["product_type_field"]

        with self.assertRaises(ValueError):
            validate_contract(payload, "travel_insurance/discovered_schema")

    def test_travel_validator_rejects_duplicate_product_type_in_fields(self) -> None:
        payload = json.loads(json.dumps(VALID_TRAVEL_SCHEMA))
        payload["fields"].append(payload["product_type_field"])

        with self.assertRaisesRegex(ValueError, "duplicate"):
            get_schema_validator("travel_insurance_schema_v1")(payload)

    def test_travel_manifest_enables_only_migrated_pipeline_stages(self) -> None:
        manifest = load_vertical_manifest(
            PROJECT_ROOT / "configs/travel_insurance/manifest.json"
        )

        self.assertTrue(manifest.supports("discovery"))
        self.assertTrue(manifest.supports("extraction"))
        self.assertTrue(manifest.supports("refinement"))
        self.assertEqual(manifest.documents.categories, ("pds",))


class TravelDiscoveryMigrationTest(unittest.TestCase):
    def test_discovery_uses_travel_prompt_contract_and_validator(self) -> None:
        class RecordingProvider:
            def __init__(self) -> None:
                self.request: ProviderRequest | None = None

            def generate(self, request: ProviderRequest) -> ModelResponse:
                self.request = request
                return ModelResponse(
                    text=json.dumps(VALID_TRAVEL_SCHEMA),
                    provider=request.selection.provider,
                    model=request.selection.model,
                )

        provider = RecordingProvider()
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "sample.pdf"
            pdf_path.touch()
            with mock.patch(
                "src.schema.discovery.render_pdf_paths_for_prompt",
                return_value="# PDF: sample\ntravel policy wording",
            ):
                result = SchemaDiscovery(
                    selection=ModelSelection("openai", "gpt-5", "markdown"),
                    provider=provider,
                    usage_log_path=None,
                    log=None,
                    vertical="travel_insurance",
                    discovery_contract="travel_insurance/discovered_schema",
                    discovery_prompt=get_prompt(load_vertical_manifest(PROJECT_ROOT / "configs/travel_insurance/manifest.json").prompt("discovery")),
                    schema_validator=get_schema_validator(
                        "travel_insurance_schema_v1"
                    ),
                ).discover([str(pdf_path)])

        self.assertEqual(result, VALID_TRAVEL_SCHEMA)
        self.assertIsNotNone(provider.request)
        self.assertIn("travel insurance", provider.request.system_prompt.lower())
        self.assertEqual(
            provider.request.structured_output.schema["properties"]["vertical"],
            {"const": "travel_insurance"},
        )
        properties = provider.request.structured_output.schema["properties"]
        self.assertNotIn("product_type_field", properties)
        self.assertEqual(properties["taxonomies"]["required"], ["coverage_categories"])


class TravelExtractionMigrationTest(unittest.TestCase):
    def test_travel_prompt_matches_closed_extraction_metadata_contract(self) -> None:
        prompt = get_prompt(load_vertical_manifest(PROJECT_ROOT / "configs/travel_insurance/manifest.json").prompt("extraction"))

        self.assertIn('"products"', prompt)
        self.assertIn('"_document_notes"', prompt)
        self.assertIn('"_unfilled"', prompt)
        self.assertIn('"_notes"', prompt)
        self.assertIn('Never output "__typename"', prompt)
        self.assertIn("product_name must be unique", prompt)

    def test_compiler_wraps_multiple_products_in_one_document_result(self) -> None:
        contract = compile_extraction_contract(
            VALID_TRAVEL_SCHEMA,
            data_contract="travel_insurance/discovered_schema",
            business_validator=get_schema_validator("travel_insurance_schema_v1"),
            output_cardinality="multiple",
        )

        validate_inline_contract(VALID_TRAVEL_EXTRACTION, contract)
        product_properties = contract["properties"]["products"]["items"][
            "properties"
        ]
        self.assertIn("product_type", product_properties)
        invalid = json.loads(json.dumps(VALID_TRAVEL_EXTRACTION))
        invalid["products"] = []
        with self.assertRaises(ValueError):
            validate_inline_contract(invalid, contract)

    def test_extractor_returns_all_products_from_one_pds(self) -> None:
        class RecordingProvider:
            def __init__(self) -> None:
                self.request: ProviderRequest | None = None

            def generate(self, request: ProviderRequest) -> ModelResponse:
                self.request = request
                return ModelResponse(
                    text=json.dumps(VALID_TRAVEL_EXTRACTION),
                    provider=request.selection.provider,
                    model=request.selection.model,
                )

        provider = RecordingProvider()
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "travel-pds.pdf"
            pdf_path.touch()
            with mock.patch(
                "src.schema_application.extractor.render_pdf_paths_for_prompt",
                return_value="# PDF: travel-pds\nbenefit tables",
            ):
                result = SchemaExtractor(
                    schema_data=VALID_TRAVEL_SCHEMA,
                    selection=ModelSelection("openai", "gpt-5", "markdown"),
                    provider=provider,
                    usage_log_path=None,
                    log=None,
                    schema_contract="travel_insurance/discovered_schema",
                    schema_validator=get_schema_validator(
                        "travel_insurance_schema_v1"
                    ),
                    output_cardinality="multiple",
                    extraction_prompt=get_prompt(load_vertical_manifest(PROJECT_ROOT / "configs/travel_insurance/manifest.json").prompt("extraction")),
                ).extract_one(pdf_path)

        self.assertEqual(len(result["products"]), 2)
        self.assertIsNotNone(provider.request)
        self.assertIn("every distinct plan", provider.request.system_prompt.lower())

    def test_extractor_accepts_approved_canonical_schema(self) -> None:
        class RecordingProvider:
            def __init__(self) -> None:
                self.request: ProviderRequest | None = None

            def generate(self, request: ProviderRequest) -> ModelResponse:
                self.request = request
                return ModelResponse(
                    text=json.dumps(valid_extraction_payload()),
                    provider=request.selection.provider,
                    model=request.selection.model,
                )

        provider = RecordingProvider()
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "travel-pds.pdf"
            pdf_path.touch()
            with mock.patch(
                "src.schema_application.extractor.render_pdf_paths_for_prompt",
                return_value="# PDF: travel-pds\nbenefit tables",
            ):
                result = SchemaExtractor(
                    schema_data=approved_travel_schema(),
                    selection=ModelSelection("openai", "gpt-5", "markdown"),
                    provider=provider,
                    usage_log_path=None,
                    log=None,
                    schema_contract="travel_insurance/discovered_schema",
                    schema_validator=get_schema_validator(
                        "travel_insurance_schema_v1"
                    ),
                    output_cardinality="multiple",
                    extraction_prompt=get_prompt(load_vertical_manifest(PROJECT_ROOT / "configs/travel_insurance/manifest.json").prompt("extraction")),
                ).extract_one(pdf_path)

        self.assertEqual(result, valid_extraction_payload())
        self.assertIsNotNone(provider.request)
        self.assertIn(
            "geographic_scope",
            provider.request.structured_output.schema["properties"]
            ["products"]["items"]["properties"],
        )
        self.assertIn("Approved Canonical Schema", provider.request.user_text)

    def test_canonical_extractor_repairs_duplicate_product_names(self) -> None:
        invalid = valid_extraction_payload()
        invalid["products"].append(dict(invalid["products"][0]))
        repaired = valid_extraction_payload()
        repaired_product = dict(repaired["products"][0])
        repaired_product["product_name"] = "International Essentials"
        repaired["products"].append(repaired_product)

        class SequenceProvider:
            def __init__(self) -> None:
                self.requests: list[ProviderRequest] = []

            def generate(self, request: ProviderRequest) -> ModelResponse:
                self.requests.append(request)
                payload = invalid if len(self.requests) == 1 else repaired
                return ModelResponse(
                    text=json.dumps(payload),
                    provider=request.selection.provider,
                    model=request.selection.model,
                )

        provider = SequenceProvider()
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "travel-pds.pdf"
            pdf_path.touch()
            with mock.patch(
                "src.schema_application.extractor.render_pdf_paths_for_prompt",
                return_value="# PDF: travel-pds\nbenefit tables",
            ):
                result = SchemaExtractor(
                    schema_data=approved_travel_schema(),
                    selection=ModelSelection("openai", "gpt-5", "markdown"),
                    provider=provider,
                    usage_log_path=None,
                    log=None,
                    schema_contract="travel_insurance/discovered_schema",
                    schema_validator=get_schema_validator(
                        "travel_insurance_schema_v1"
                    ),
                    output_cardinality="multiple",
                    extraction_prompt=get_prompt(load_vertical_manifest(PROJECT_ROOT / "configs/travel_insurance/manifest.json").prompt("extraction")),
                ).extract_one(pdf_path)

        self.assertEqual(result, repaired)
        self.assertEqual(len(provider.requests), 2)
        self.assertIn("must be unique", provider.requests[1].user_text)

    def test_extractor_rejects_unapproved_canonical_schema(self) -> None:
        schema = approved_travel_schema()
        schema["status"] = "candidate"
        schema["review"] = None

        with self.assertRaisesRegex(ValueError, "human-approved"):
            SchemaExtractor(
                schema_data=schema,
                selection=ModelSelection("openai", "gpt-5", "markdown"),
                provider=mock.Mock(),
                usage_log_path=None,
                log=None,
                schema_contract="travel_insurance/discovered_schema",
                schema_validator=get_schema_validator(
                    "travel_insurance_schema_v1"
                ),
                output_cardinality="multiple",
                extraction_prompt=get_prompt(load_vertical_manifest(PROJECT_ROOT / "configs/travel_insurance/manifest.json").prompt("extraction")),
            )

    def test_optional_canonical_scalar_disables_provider_strict_mode(self) -> None:
        schema = approved_travel_schema()
        schema["fields"] = [
            field for field in schema["fields"] if field["name"] != "benefits"
        ]
        schema["fields"][2]["required"] = False

        extractor = SchemaExtractor(
            schema_data=schema,
            selection=ModelSelection("openai", "gpt-5", "markdown"),
            provider=mock.Mock(),
            usage_log_path=None,
            log=None,
            schema_contract="travel_insurance/discovered_schema",
            schema_validator=get_schema_validator(
                "travel_insurance_schema_v1"
            ),
            output_cardinality="multiple",
            extraction_prompt=get_prompt(load_vertical_manifest(PROJECT_ROOT / "configs/travel_insurance/manifest.json").prompt("extraction")),
        )

        self.assertFalse(extractor.structured_output_strict)


if __name__ == "__main__":
    unittest.main()
