from __future__ import annotations

import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from src.car_insurance.dataset import build_inventory, COLUMNS
from src.common.json_contracts import validate_contract, validate_inline_contract
from src.common.model_config import ModelSelection
from src.common.model_provider import ModelResponse
from src.schema.contract import compile_extraction_contract
from src.schema.discovery import SchemaDiscovery
from src.schema.validation import validate_schema_mapping, validate_extraction_record
from src.schema_application.extractor import SchemaExtractor
from src.verticals.manifest import discover_manifests, resolve_manifest, ManifestValidationError


def synthetic_schema():
    """Synthetic wiring fixture, not a proposed or approved business schema."""
    manifest = resolve_manifest(vertical="car_insurance")
    product_types = list(manifest.product_types)
    return {
        "vertical": "car_insurance", "version": "synthetic-test-only",
        "description": "Synthetic car insurance schema for offline wiring tests.",
        "product_types": product_types,
        "fields": [
            {"name": "product_type", "type": "enum", "description": "Cover type",
             "applies_to": product_types, "required": True, "values": product_types},
            {"name": "product_name", "type": "string", "description": "Named product",
             "applies_to": product_types, "required": True, "values": []},
            {"name": "benefits", "type": "list[object]", "description": "Benefit rows with coverage and limit_amount_aud",
             "applies_to": product_types, "required": False, "values": []},
        ],
        "taxonomies": {"coverage_categories": [
            {"canonical_name": "theft", "description": "Theft benefit"}
        ]},
        "notes": ["Synthetic test only; no source evidence or human approval."],
    }


def synthetic_products():
    return {"products": [
        {"product_name": "Example Comprehensive", "product_type": "comprehensive",
         "benefits": [{"category": "theft", "status": "included", "limit_amount_aud": None}],
         "_unfilled": [], "_notes": None},
        {"product_name": "Example Third Party", "product_type": "third_party_property_damage",
         "benefits": None, "_unfilled": ["benefits"], "_notes": None},
    ], "_document_notes": None}


class RecordingProvider:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return ModelResponse(text=json.dumps(self.payload), provider=request.selection.provider,
                             model=request.selection.model)


class CarInsuranceRuntimeTest(unittest.TestCase):
    def test_auto_discovery_and_capability_gates(self):
        self.assertIn("car_insurance", discover_manifests())
        manifest = resolve_manifest(vertical="car_insurance", operation="discover")
        self.assertEqual(manifest.documents.output_cardinality, "multiple")
        self.assertTrue(manifest.supports("refinement"))
        self.assertTrue(manifest.manual_only_queue)
        for capability in ("evaluation", "acquisition", "storage"):
            with self.subTest(capability=capability), self.assertRaises(ManifestValidationError):
                manifest.require_capability(capability)

    def test_contracts_accept_car_and_reject_cross_vertical_products(self):
        schema = synthetic_schema()
        manifest = resolve_manifest(vertical="car_insurance")
        validate_contract(schema, "car_insurance/discovered_schema", manifest=manifest)
        validate_schema_mapping(schema, manifest=manifest)
        contract = compile_extraction_contract(schema, manifest=manifest)
        validate_inline_contract(synthetic_products(), contract)
        for invalid_type in ("ctp", "international_single_trip", "hospital"):
            invalid = synthetic_products()
            invalid["products"][0]["product_type"] = invalid_type
            with self.subTest(product_type=invalid_type), self.assertRaises(ValueError):
                validate_inline_contract(invalid, contract)

    def test_duplicate_products_are_rejected(self):
        manifest = resolve_manifest(vertical="car_insurance")
        payload = synthetic_products()
        payload["products"][1]["product_name"] = payload["products"][0]["product_name"]
        with self.assertRaises(ValueError):
            validate_extraction_record(synthetic_schema(), payload, manifest=manifest)

    def test_undeclared_fields_and_unknown_unfilled_names_are_rejected(self):
        contract = compile_extraction_contract(synthetic_schema())
        for mutation in ("extra_field", "unfilled"):
            payload = synthetic_products()
            if mutation == "extra_field":
                payload["products"][0]["unknown_field"] = "unexpected"
            else:
                payload["products"][0]["_unfilled"] = ["unknown_field"]
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_inline_contract(payload, contract)

    def test_discovery_then_extraction_with_injected_provider(self):
        manifest = resolve_manifest(vertical="car_insurance")
        discovery_provider = RecordingProvider(synthetic_schema())
        extraction_provider = RecordingProvider(synthetic_products())
        selection = ModelSelection("openai", "gpt-5", "markdown")
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "pds.pdf"
            pdf.touch()
            with mock.patch("src.schema.discovery.render_pdf_paths_for_prompt", return_value="Synthetic PDS table"):
                schema = SchemaDiscovery(manifest=manifest, provider=discovery_provider,
                                         selection=selection, usage_log_path=None, log=None).discover([str(pdf)])
            with mock.patch("src.schema_application.extractor.render_pdf_paths_for_prompt", return_value="Synthetic PDS table"):
                result = SchemaExtractor(schema_data=schema, manifest=manifest,
                                         provider=extraction_provider, selection=selection,
                                         usage_log_path=None, log=None).extract_one(pdf)
        self.assertEqual(result, synthetic_products())
        self.assertEqual(len(discovery_provider.requests), 1)
        self.assertEqual(len(extraction_provider.requests), 1)
        self.assertIn("car insurance", discovery_provider.requests[0].system_prompt)
        self.assertIn("car insurance", extraction_provider.requests[0].system_prompt)
        self.assertNotIn("hospital", json.dumps(discovery_provider.requests[0].structured_output.schema))


class CarDatasetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.csv = self.root / "intake.csv"

    def row(self, split="development", insurer="example", group="motor", name="pds.pdf", content=b"%PDF-1.7 synthetic"):
        relative = f"{split}/{insurer}/pds/{name}"
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {"relative_path": relative, "split": split, "insurer": insurer,
                "release_group": group, "document_type": "pds",
                "source_url": "https://example.com/pds.pdf", "retrieved_at": "2026-01-01"}

    def inventory(self, rows):
        with self.csv.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=sorted(COLUMNS))
            writer.writeheader()
            writer.writerows(rows)
        return build_inventory(self.root, self.csv)

    def test_hashes_and_splits_are_recorded_deterministically(self):
        rows = [self.row(), self.row("test", "another", content=b"%PDF-1.7 different")]
        result = self.inventory(rows)
        self.assertEqual(result, self.inventory(list(reversed(rows))))
        self.assertEqual(result["summary"]["unique_pdfs"], 2)
        self.assertEqual(result["summary"]["splits"]["test"], 1)
        self.assertEqual(len(result["entries"][0]["sha256"]), 64)

    def test_identical_pdf_across_splits_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "identical PDF leaks"):
            self.inventory([self.row(), self.row("test", "another")])

    def test_related_releases_across_splits_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "release group leaks"):
            self.inventory([self.row(), self.row("holdout", content=b"%PDF-1.7 supplement")])

    def test_missing_invalid_duplicate_and_escaping_files_are_rejected(self):
        original = self.row()
        with self.assertRaisesRegex(ValueError, "duplicate PDF path"):
            self.inventory([original, original])
        invalid = copy.deepcopy(original)
        invalid["relative_path"] = "../outside.pdf"
        with self.assertRaisesRegex(ValueError, "within input root"):
            self.inventory([invalid])
        with self.assertRaises(FileNotFoundError):
            self.inventory([{**original, "relative_path": "development/example/pds/missing.pdf"}])
        with self.assertRaisesRegex(ValueError, "PDF signature"):
            self.inventory([self.row(content=b"<html>not a PDF</html>")])
        with self.assertRaisesRegex(ValueError, "No PDFs registered"):
            self.inventory([])


if __name__ == "__main__":
    unittest.main()
