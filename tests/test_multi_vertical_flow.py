"""Offline acceptance across the real shared engine; only PDF/model boundaries are faked."""
from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from src.common.json_artifacts import build_success_artifact, write_artifact
from src.common.json_contracts import load_contract
from src.common.model_provider import ModelResponse
from src.models import ExtractionResult
from src.refine.consensus import SchemaConsensusRefinement
from src.refine.human_review import apply_review_files, load_review_queue, save_review_decision
from src.run import default_output_path
from src.schema.contract import compile_extraction_contract
from src.schema.discovery import SchemaDiscovery
from src.schema.loader import load_schema_data
from src.schema.validation import normalize_schema
from src.schema_application.extractor import SchemaExtractor
from src.schema_application.analyze import load_records, load_field_specs, analyze
from src.stability.signature import signature_from_artifact
from src.verticals.manifest import resolve_manifest
from tests.test_json_contracts import VALID_DISCOVERED_SCHEMA
from tests.test_travel_consensus import travel_schema


class FixtureProvider:
    def __init__(self, schema):
        self.schema = schema
        self.requests = []
        self.patch_calls = 0

    def generate(self, request):
        self.requests.append(request)
        name = request.structured_output.name
        if name == "discovered_schema":
            payload = self.schema
        elif name == "candidate_patch_set":
            self.patch_calls += 1
            proposal = {"patch_type": "add_field", "target_group": "cover", "field_name": "annual_limit",
                        "canonical_name": "annual_limit", "type": "number", "description": "Annual benefit limit",
                        "applies_to": self.schema["product_types"], "required": False, "values": [],
                        "evidence_documents": [], "confidence": 0.9, "rationale": "Document evidence"}
            payload = {"patches": [proposal] if self.patch_calls <= 3 else []}
        else:
            fields = request.structured_output.schema["properties"]
            product_contract = fields["products"]["items"] if "products" in fields else request.structured_output.schema
            def product(index):
                record = {name: None for name in product_contract["properties"]}
                record.update(product_type=self.schema["product_types"][0], product_name=f"Plan {index}",
                              annual_limit=1000, _unfilled=[], _notes=None)
                return record
            payload = {"products": [product(1), product(2)], "_document_notes": None} if "products" in fields else product(1)
        return ModelResponse(text=json.dumps(payload), provider=request.selection.provider, model=request.selection.model)


class MultiVerticalFlowTests(unittest.TestCase):
    def test_discover_consensus_review_extract_analyze_both_verticals(self):
        for vertical, legacy in (("private_health", VALID_DISCOVERED_SCHEMA), ("travel_insurance", travel_schema())):
            with self.subTest(vertical=vertical), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                manifest = resolve_manifest(vertical=vertical)
                schema = normalize_schema(deepcopy(legacy), manifest)
                provider = FixtureProvider(schema)
                pdf = root / ("hospital" if vertical == "private_health" else "pds") / "sample.pdf"
                pdf.parent.mkdir(); pdf.write_bytes(b"offline fixture, not a real PDF")
                discovery = SchemaDiscovery(manifest=manifest, provider=provider, log=None)
                with patch("src.schema.discovery.render_pdf_paths_for_prompt", return_value="Fixture document text"):
                    discovered = discovery.discover([str(pdf)])
                    base_path = root / "base.json"
                    artifact = build_success_artifact(artifact_type="discovered_schema", contract_version="1.0.0", data=discovered,
                        provenance={"run_id":"fixture", "provider":None, "model":None, "document_input":None,
                                    "source_documents":[str(pdf)], "source_artifacts":[]},
                        data_contract_schema=load_contract(manifest.contract("discovered_schema"), manifest=manifest))
                    write_artifact(base_path, artifact, data_contract_schema=load_contract(manifest.contract("discovered_schema"), manifest=manifest))
                    original = base_path.read_bytes()
                    outputs = SchemaConsensusRefinement(discovery, log=None).refine(base_path, samples=[str(pdf)], output_dir=root / "consensus")
                queue = load_review_queue(outputs.queue_path)
                self.assertEqual(queue["metadata"]["vertical"], vertical)
                self.assertEqual(provider.patch_calls, manifest.consensus_runs)
                self.assertEqual(len(queue["updates"]), 1)
                save_review_decision(root / "consensus/review_decisions.json", "field:annual_limit", "accept", "fixture review")
                reviewed_path, summary = apply_review_files(root / "consensus")
                self.assertEqual(summary["applied"], ["field:annual_limit"])
                self.assertEqual(base_path.read_bytes(), original)
                reviewed = load_schema_data(reviewed_path, manifest)
                extractor = SchemaExtractor(schema_data=reviewed, manifest=manifest, provider=provider, log=None)
                with patch("src.schema_application.extractor.render_pdf_paths_for_prompt", return_value="Fixture document text"):
                    written = extractor.extract_many([pdf], root / "extractions")
                records, failures = load_records(root / "extractions", extractor.extraction_contract, manifest)
                self.assertEqual(failures, 0)
                self.assertEqual(len(records), 1 if manifest.documents.output_cardinality == "single" else 2)
                self.assertEqual(records[0].data["annual_limit"], 1000)
                self.assertEqual(json.loads(written[0].read_text())["status"], "success")
                report = analyze(records, load_field_specs(reviewed, manifest))
                if not manifest.documents.category_product_types:
                    self.assertIsNone(report.product_type_accuracy)
                other = resolve_manifest(vertical="travel_insurance" if vertical == "private_health" else "private_health")
                with self.assertRaises(ValueError):
                    SchemaExtractor(schema_data=reviewed, manifest=other, provider=provider, log=None)

    def test_analysis_reads_cli_results_and_rejects_wrong_identity(self):
        from tests.test_extractor import VALID_RECORD
        manifest = resolve_manifest(vertical="private_health")
        contract = compile_extraction_contract(VALID_DISCOVERED_SCHEMA, manifest=manifest)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = ExtractionResult(vertical=manifest.vertical, schema_version="v1",
                source_path="data/FUND/hospital/a.pdf", data=VALID_RECORD)
            result.write_json(root / "nested/result.json")
            records, failures = load_records(root, contract, manifest, schema_version="v1")
            self.assertEqual((len(records), failures), (1, 0))
            records, failures = load_records(root, contract, manifest, schema_version="v2")
            self.assertEqual((len(records), failures), (0, 1))
            result.model_copy(update={"vertical":"travel_insurance"}).write_json(root / "wrong.json")
            records, failures = load_records(root, contract, manifest, schema_version="v1")
            self.assertEqual((len(records), failures), (1, 1))

    def test_feedback_from_other_vertical_cannot_seed_a_round(self):
        from argparse import Namespace
        from src.refine.pipeline.cli import _load_feedback
        from src.schema_application.analyze import build_feedback_data
        manifest = resolve_manifest(vertical="private_health")
        feedback = build_feedback_data(analyze([], []))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feedback.json"
            artifact = build_success_artifact(artifact_type="refinement_feedback", contract_version="1.0.0", data=feedback,
                provenance={"vertical":"travel_insurance", "schema_version":"v1", "run_id":None,"provider":None,
                            "model":None,"document_input":None,"source_documents":[],"source_artifacts":[]},
                data_contract=manifest.contract("refinement_feedback"))
            write_artifact(path, artifact, data_contract=manifest.contract("refinement_feedback"))
            with self.assertRaisesRegex(ValueError, "Feedback vertical"):
                _load_feedback(Namespace(resume_feedback=path, vertical_manifest=manifest), 1)

    def test_custom_taxonomy_reaches_compiler_and_stability(self):
        manifest = replace(resolve_manifest(vertical="private_health"), taxonomies=("benefit_groups",))
        schema = normalize_schema(deepcopy(VALID_DISCOVERED_SCHEMA))
        schema["taxonomies"] = {"benefit_groups": [{"canonical_name":"dental", "description":"Dental services"}]}
        self.assertIn("product_type", compile_extraction_contract(schema, manifest=manifest)["properties"])
        artifact = build_success_artifact(artifact_type="discovered_schema", contract_version="1.0.0", data=schema,
            provenance={"run_id":None,"provider":None,"model":None,"document_input":None,"source_documents":[],"source_artifacts":[]},
            data_contract_schema=load_contract(manifest.contract("discovered_schema"), manifest=manifest))
        self.assertEqual(signature_from_artifact(artifact, "custom", manifest=manifest).taxonomies["benefit_groups"], {"dental"})

    def test_cli_output_uses_manifest_and_preserves_existing_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = replace(resolve_manifest(vertical="private_health"), paths={"input_root":".", "output_root":str(root)},
                path_environment={"output_root":{"variable":"CAPSTONE_FIXTURE_ROOT", "suffix":""}})
            with patch.dict("os.environ", {"CAPSTONE_FIXTURE_ROOT":str(root)}):
                first = default_output_path(manifest, root / "one/sample.pdf")
                result = ExtractionResult(vertical=manifest.vertical, schema_version="v1", source_path="fixture")
                result.write_json(first)
                second = default_output_path(manifest, root / "one/sample.pdf")
                other = default_output_path(manifest, root / "two/sample.pdf")
                self.assertEqual(len({first, second, other}), 3)
                self.assertTrue(first.is_relative_to(root.resolve()))
                with self.assertRaises(FileExistsError):
                    result.write_json(first)


if __name__ == "__main__":
    unittest.main()
