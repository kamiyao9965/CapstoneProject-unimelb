"""Synthetic domain checks through the shared schema and strict-output owners."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.common.model_config import ModelSelection
from src.common.model_provider import ModelResponse
from src.common.structured_output import BusinessDiagnostics, StructuredOutputFailure
from src.schema.validation import validate_schema_mapping, validate_extraction_record
from src.schema.contract import compile_extraction_contract
from src.schema.nested import closed_extraction_contract, validate_value_schema
from src.schema_application.extractor import SchemaExtractor
from src.verticals.manifest import resolve_manifest
from src.verticals import pet_insurance
from src.verticals.car_insurance import rules as car


def car_dynamic_schema():
    schema = car.build_candidate_schema()
    schema["fields"] = [field for field in schema["fields"] if field["name"] in {"product_name", "product_type"}]
    schema.pop("document_fields")
    schema.pop("$defs")
    schema["fields"].append({"name": "new_benefits", "type": "list[object]", "description": "Dynamically discovered benefits",
        "required": False, "applies_to": schema["product_types"], "values": [], "item_schema": {
            "type": "object", "additionalProperties": False, "required": ["source_document_id", "pdf_page", "quote"],
            "properties": {"source_document_id": {"type": "string"}, "pdf_page": {"type": "integer", "minimum": 1}, "quote": {"type": "string"}},
        }})
    return schema


def pet_schema():
    manifest = resolve_manifest(vertical="pet_insurance")
    types = list(manifest.product_types)
    fields = [{"name": name, "type": "string", "description": name, "applies_to": types,
               "required": True, "values": []} for name in manifest.identity_fields]
    fields.append({"name": "product_type", "type": "enum", "description": "Cover scope",
                   "applies_to": types, "required": True, "values": types})
    for name, kind in pet_insurance.COMPARISON_FIELDS.items():
        fields.append({"name": name, "type": kind, "description": name, "applies_to": types,
                       "required": False, "values": ["accident", "illness"] if kind == "list[enum]" else []})
    props = {"benefit_category": {"type": "string", "enum": ["accident", "illness"]},
             "coverage_status": {"type": "string", "enum": ["included", "optional", "excluded"]},
             **{name: {"type": "string"} for name in ("source_document_id", "source_block_id", "source_quote")},
             "source_page": {"type": "integer", "minimum": 1}}
    fields[-1]["item_schema"] = {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}
    return {"vertical": manifest.vertical, "version": "synthetic.v1", "description": "Synthetic Pet schema",
            "product_types": types, "taxonomies": {"benefit_categories": [
                {"canonical_name": name, "description": name} for name in ("accident", "illness")]}, "fields": fields, "notes": []}


class VerticalBusinessTests(unittest.TestCase):
    def test_car_can_remove_and_generate_fields_without_copying_the_37_field_reference(self):
        schema = validate_schema_mapping(car_dynamic_schema())
        self.assertEqual(len(schema["fields"]), 3)
        closed_extraction_contract(compile_extraction_contract(schema))
        summary = car.validation_summary(schema)
        self.assertIn("document_qualified_citations", summary["applied_rules"])
        self.assertIn("car_v5_cross_field_semantics", {entry["rule"] for entry in summary["not_applicable_rules"]})

    def test_full_car_candidate_applies_text_checks_only_for_single_pdf(self):
        schema = car.build_candidate_schema()
        single, grouped = car.validation_summary(schema), car.validation_summary(schema, document_count=2)
        self.assertIn("car_source_completeness", single["applied_rules"])
        self.assertNotIn("car_source_completeness", grouped["applied_rules"])
        self.assertIn("car_v3_relationship_semantics", grouped["applied_rules"])
        self.assertEqual({entry["reason"] for entry in grouped["not_applicable_rules"]}, {"multi_document_amendments_require_judge"})
        source = {"A": {"ordered_blocks": []}, "B": {"ordered_blocks": []}}
        prompt, context = car.source_context(schema, source)
        self.assertFalse(context["enforce_completeness"])
        self.assertIn("superseded base clauses need not be represented", prompt)
        self.assertNotIn("SOURCE COMPLETENESS CHECKLIST", prompt)

    def test_partial_car_prompt_does_not_require_the_full_reference_layout(self):
        schema = car_dynamic_schema()
        engine = SchemaExtractor(schema, selection=ModelSelection("openai", "gpt-5", "markdown"), provider=object(), log=None)
        self.assertNotIn("Return products, _document_notes, document_evidence and shared_policy_rules", engine.extraction_prompt)
        self.assertNotIn("Runtime diagnostics revision", engine.extraction_prompt)
        self.assertIn("Runtime diagnostics revision", car.extraction_guidance(car.build_candidate_schema()))

    def test_grouped_car_checks_reference_membership_without_source_completeness(self):
        schema = car.build_candidate_schema()
        source = {"A": {"ordered_blocks": [], "prompt_text": "old"},
                  "B": {"ordered_blocks": [], "prompt_text": "new"}}
        _, context = car.source_context(schema, source)
        car.validate_sources(schema, {"products": []}, source, context)
        with self.assertRaisesRegex(Exception, "source_clause_ids"):
            car.validate_sources(schema, {"products": [{"source_clause_ids": ["invented"]}]}, source, context)

    def test_dynamic_car_citations_still_require_document_identity(self):
        schema = car_dynamic_schema()
        item = schema["fields"][-1]["item_schema"]
        item["properties"].pop("source_document_id")
        item["required"].remove("source_document_id")
        with self.assertRaisesRegex(ValueError, "source_document_id"):
            validate_schema_mapping(schema)

    def test_generated_evidence_types_fail_schema_validation_before_extraction(self):
        car_schema = car_dynamic_schema()
        car_schema["fields"][-1]["item_schema"]["properties"]["source_document_id"] = {"type": "array", "items": {"type": "string"}}
        pet = pet_schema()
        pet["fields"][-1]["item_schema"]["properties"]["source_quote"] = {"type": "number"}
        for schema in (car_schema, pet):
            with self.subTest(vertical=schema["vertical"]), self.assertRaisesRegex(ValueError, "must have type"):
                validate_schema_mapping(schema)

    def test_dynamic_car_money_structure_retains_supported_amount_checks(self):
        schema = car_dynamic_schema()
        original = car.build_candidate_schema()
        schema["fields"][-1]["item_schema"] = validate_value_schema(original["$defs"]["amount_term"], original["$defs"])
        schema = validate_schema_mapping(schema)
        self.assertIn("amount_term", car.validation_summary(schema)["applied_rules"])
        term = {"amount_kind": "unlimited", "amount_aud": 10, "percentage": None, "percentage_of": None, "reference_text": None}
        payload = {"products": [{"product_name": "Synthetic", "product_type": "comprehensive", "new_benefits": [term],
                                  "_unfilled": [], "_notes": None}], "_document_notes": None}
        with self.assertRaisesRegex(ValueError, "amount_terms"):
            validate_extraction_record(schema, payload, manifest=resolve_manifest(vertical="car_insurance"))

    def test_dynamic_car_citation_validation_enters_repairs_and_records_applied_rules(self):
        class FakeProvider:
            def __init__(self, payload): self.payload, self.requests = payload, []
            def generate(self, request):
                self.requests.append(request)
                return ModelResponse(text=json.dumps(self.payload), provider="openai", model="gpt-5")
        schema = car_dynamic_schema()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "car.pdf"; path.write_bytes(b"%PDF-1.4\nSynthetic\n")
            engine = SchemaExtractor(schema, selection=ModelSelection("openai", "gpt-5", "markdown"), provider=FakeProvider({}), log=None)
            group = engine._groups([path])[0]
            document_id = group.documents[0].document_id
            payload = {"products": [{"product_name": "Synthetic", "product_type": "comprehensive",
                "new_benefits": [{"source_document_id": document_id, "pdf_page": 1, "quote": "Made up evidence"}],
                "_unfilled": [], "_notes": None}], "_document_notes": None}
            engine.provider.payload = payload
            def render(*args, source_index, **kwargs):
                source_index[document_id] = {"pages": {1: "Real synthetic wording"}, "blocks": {},
                                             "ordered_blocks": [], "prompt_text": "Real synthetic wording"}
                return "Real synthetic wording"
            with patch("src.schema_application.extractor.render_pdf_paths_for_prompt", side_effect=render):
                with self.assertRaises(StructuredOutputFailure): engine.extract_group(group)
                self.assertEqual(len(engine.provider.requests), 3)
                payload["products"][0]["new_benefits"][0]["quote"] = "Real synthetic wording"
                written = engine.extract_many([path], Path(tmp) / "results")[0]
            checks = json.loads(written.read_text())["provenance"]["validation_checks"]
            self.assertIn("document_qualified_citations", checks["applied_rules"])
            self.assertIn("car_source_completeness", {entry["rule"] for entry in checks["not_applicable_rules"]})

    def test_cached_car_result_revalidates_source_evidence_without_a_provider(self):
        from src.schema_application.records import load_cached_extraction
        from src.verticals.documents import resolve_document_groups
        schema = validate_schema_mapping(car_dynamic_schema())
        selection = ModelSelection("openai", "gpt-5", "markdown")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            pdf = root / "car.pdf"
            pdf.write_bytes(b"%PDF-1.4\nSynthetic\n")
            manifest = resolve_manifest(vertical="car_insurance")
            group = resolve_document_groups(manifest, [pdf], input_root=root)[0]
            document_id = group.documents[0].document_id
            payload = {"products": [{"product_name": "Synthetic", "product_type": "comprehensive",
                "new_benefits": [{"source_document_id": document_id, "pdf_page": 1, "quote": "Invented quote"}],
                "_unfilled": [], "_notes": None}], "_document_notes": None}
            path = root / "cached.json"
            path.write_text(json.dumps({"vertical": "car_insurance", "schema_version": schema["version"],
                "source_path": str(pdf), **group.provenance(), "extracted_at": "2026-10-12T00:00:00Z",
                "provider": "openai", "model": "gpt-5", "document_parser": "pdfingestor", "data": payload}))
            before = path.read_bytes()
            def render(*args, source_index, **kwargs):
                source_index[document_id] = {"pages": {1: "Actual source"}, "blocks": {},
                    "ordered_blocks": [], "prompt_text": "Actual source"}
                return "Actual source"
            with patch("src.pdf_ingestion.adapter.render_pdf_paths_for_prompt", side_effect=render), \
                 patch("src.common.model_provider.create_provider") as provider:
                with self.assertRaises(BusinessDiagnostics):
                    load_cached_extraction(path, pdf_path=pdf, schema=schema, manifest=manifest,
                                           selection=selection, document_parser="pdfingestor", document_group=group, input_root=root)
            provider.assert_not_called()
            self.assertEqual(path.read_bytes(), before)

    def test_renderer_rejects_a_parsed_pdf_bound_to_the_wrong_source_receipt(self):
        from src.pdf_ingestion.adapter import render_pdf_paths_for_prompt
        from src.pdf_ingestion.models import ParsedPDF
        document = ParsedPDF(pdf_id="synthetic", pdf_hash="1" * 64, source_path="/tmp/synthetic.pdf")
        reference = {"document_id": "A", "source_path": "/tmp/synthetic.pdf", "sha256": "2" * 64,
                     "document_role": "pds", "amends_document_ids": []}
        with patch("src.pdf_ingestion.adapter.ingest_pdfs", return_value=(document,)):
            with self.assertRaisesRegex(ValueError, "source document references and hashes"):
                render_pdf_paths_for_prompt(["/tmp/synthetic.pdf"], document_refs=[reference], source_index={})

    def test_pet_classification_and_comparison_types_survive_shared_compilation(self):
        schema = validate_schema_mapping(pet_schema())
        contract = closed_extraction_contract(compile_extraction_contract(schema))
        product = contract["properties"]["products"]["items"]["properties"]
        self.assertIn("product_type", product)
        self.assertNotIn("cover_scope", product)
        self.assertEqual(product["benefit_percentage_options"]["items"], {"type": "number"})

    def test_pet_mismatched_included_categories_fail_without_mutating_values(self):
        schema = pet_schema()
        product = {field["name"]: None for field in schema["fields"]}
        product.update(product_id="synthetic", product_name="Synthetic", insurer_name="Synthetic",
                       product_type="accident_only", covered_benefit_categories=["illness"],
                       benefit_coverages=[{"benefit_category": "accident", "coverage_status": "included",
                         "source_document_id": "A", "source_block_id": "block", "source_page": 1, "source_quote": "Accident cover"}], _notes=None)
        product["_unfilled"] = [field["name"] for field in schema["fields"] if product[field["name"]] is None]
        payload = {"products": [product], "_document_notes": None}
        before = copy.deepcopy(payload)
        with self.assertRaisesRegex(ValueError, "match included"):
            validate_extraction_record(schema, payload, manifest=resolve_manifest(vertical="pet_insurance"))
        self.assertEqual(payload, before)

    def test_pet_block_page_and_document_all_bind_to_the_actual_source(self):
        item = {"source_document_id": "A", "source_block_id": "same", "source_page": 1, "source_quote": "Accident cover"}
        payload = {"products": [{"benefit_coverages": [item]}]}
        sources = {"A": {"blocks": {"same": {"page": 1, "text": "Accident cover"}}},
                   "B": {"blocks": {"same": {"page": 1, "text": "Other cover"}}}}
        pet_insurance.validate_sources(pet_schema(), payload, sources, None)
        for name, value in (("source_document_id", "B"), ("source_page", 2), ("source_block_id", "unknown")):
            with self.subTest(name=name), self.assertRaises(BusinessDiagnostics):
                changed = copy.deepcopy(payload); changed["products"][0]["benefit_coverages"][0][name] = value
                pet_insurance.validate_sources(pet_schema(), changed, sources, None)
