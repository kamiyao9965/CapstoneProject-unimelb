from __future__ import annotations

import copy
import unittest
from dataclasses import replace
from types import MappingProxyType

from src.schema.canonical import build_canonical_candidate, require_approved_canonical_schema
from src.verticals.manifest import resolve_manifest
from tests.test_json_contracts import VALID_DISCOVERED_SCHEMA


class CanonicalVerticalTests(unittest.TestCase):
    def test_new_vertical_candidate_does_not_require_database_or_approved_template(self):
        manifest = resolve_manifest(vertical="private_health")
        manifest = replace(manifest, capabilities=MappingProxyType({**manifest.capabilities,
            "canonical_review": True, "csv_export": True, "storage": False}))
        source = copy.deepcopy(VALID_DISCOVERED_SCHEMA)
        original = copy.deepcopy(source)
        candidate = build_canonical_candidate(source, manifest)
        self.assertEqual(candidate["status"], "candidate")
        self.assertIsNone(candidate["review"])
        self.assertEqual(candidate["output"]["collection"], "products")
        self.assertEqual(candidate["output"]["cardinality"], "multiple")
        mappings = {f["name"]: f["storage"] for f in candidate["fields"]}
        self.assertEqual(mappings["product_name"]["target"], "products.canonical_name")
        self.assertEqual(mappings["product_type"]["target"], "product_releases.source_product_type")
        self.assertEqual(source, original)
        with self.assertRaisesRegex(ValueError, "human-approved"):
            require_approved_canonical_schema(candidate)

    def test_candidate_preserves_document_level_contracts_and_classification(self):
        manifest = resolve_manifest(vertical="travel_insurance")
        source = copy.deepcopy(VALID_DISCOVERED_SCHEMA)
        source["vertical"] = manifest.vertical
        source["product_types"] = ["domestic"]
        source["taxonomies"] = {"coverage_categories": []}
        source.pop("product_type_field", None)
        source.pop("hospital_categories", None)
        source.pop("extras_services", None)
        for f in source["fields"]:
            f.pop("aliases", None)
            f["applies_to"] = ["domestic"]
            if f["name"] == "product_type": f["values"] = ["domestic"]
        if not any(f["name"] == "product_type" for f in source["fields"]):
            source["fields"].insert(0, dict(name="product_type", type="enum", description="Classification",
                applies_to=["domestic"], required=True, values=["domestic"]))
        source["document_fields"] = {"source_metadata": {"type": "object", "additionalProperties": False,
            "required": ["document_id"], "properties": {"document_id": {"type": "string"}}}}
        candidate = build_canonical_candidate(source, manifest)
        self.assertEqual(candidate["document_fields"], source["document_fields"])
        self.assertEqual(candidate["product_types"], ["domestic"])

    def test_car_candidate_is_unapproved_and_can_compile_closed_only_after_synthetic_review(self):
        from src.schema.canonical import approve_canonical_schema, compile_canonical_extraction_contract
        from src.schema.nested import closed_extraction_contract
        from src.schema.nested import validate_value_schema
        from src.common.model_provider import _project_openai_schema
        from src.verticals.car_insurance.rules import build_candidate_schema
        source = build_candidate_schema()
        candidate = build_canonical_candidate(source, resolve_manifest(vertical="car_insurance"))
        self.assertEqual(candidate["status"], "candidate")
        self.assertEqual(candidate["document_fields"], {
            name: validate_value_schema(value, source["$defs"])
            for name, value in source["document_fields"].items()
        })
        with self.assertRaisesRegex(ValueError, "human-approved"):
            compile_canonical_extraction_contract(candidate)
        # Review exists only in this synthetic in-memory fixture, never in a real file.
        reviewed = approve_canonical_schema(candidate, reviewer="Synthetic fixture", rationale="Offline projection regression")
        contract = closed_extraction_contract(compile_canonical_extraction_contract(reviewed))
        _project_openai_schema(contract, strict=True)
        self.assertEqual(candidate["status"], "candidate")
