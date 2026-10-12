"""Complete source identity and citations at the real judge/delivery boundary."""
from __future__ import annotations

import copy
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from src.common.model_config import ModelSelection
from src.common.structured_output import StructuredOutputFailure
from src.pdf_ingestion.models import ParsedPDF, PageRepresentation, TextBlock
from src.evaluation.quality import audit_one, build_review_queue, run_quality_audit
from src.evaluation.quality_review import require_delivery_approval, load_queue
from src.common.json_artifacts import write_artifact
from tests import test_delivery_gate as fixtures
from tests.test_quality_audit import FakeProvider, judge_response


class GroupedQualityTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.DeliveryGateTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        fixture.manifest = replace(fixture.manifest, paths={**fixture.manifest.paths, "cache_root": str(fixture.root / "cache")})
        self.selection = ModelSelection("openai", "gpt-5", "markdown")
        self.documents = [ParsedPDF(
            pdf_id=ref.document_id, pdf_hash=ref.sha256, source_path=ref.source_path,
            pages=[PageRepresentation(page_num=1, width=100, height=100,
                    blocks=[TextBlock(block_id="same_block_id", content=text, top=1)])],
        ) for ref, text in zip(fixture.group.documents, ("Primary product wording", "Amendment changes the benefit"))]

    def response(self, *, document_id="B", quote="Amendment changes the benefit"):
        response = judge_response(quote=quote)
        response["findings"][0].update(field_name="benefits", source_document_id=document_id)
        return response

    def audit(self, provider):
        f = self.fixture
        with patch("src.evaluation.quality.ingest_pdfs", return_value=self.documents) as ingest:
            report = audit_one(manifest=f.manifest, schema_path=f.schema_path, artifact_path=f.artifact_path,
                source_root=f.input_root, selection=self.selection, provider=provider)
        self.assertEqual(ingest.call_args.args[0], f.group.pdf_paths)
        return report

    def test_judge_sees_all_sources_and_qualifies_citations(self):
        provider = FakeProvider(self.response())
        before = self.fixture.artifact_path.read_bytes()
        report = self.audit(provider)
        self.assertTrue(report["data"]["findings"][0]["citation_verified"])
        self.assertEqual(report["data"]["source_document_refs"], self.fixture.artifact["source_document_refs"])
        request = provider.requests[0]
        self.assertIn("Document ID: A", request.user_text)
        self.assertIn("Document ID: B", request.user_text)
        item = request.structured_output.schema["properties"]["findings"]["items"]
        self.assertIn("source_document_id", item["required"])
        self.assertEqual(item["properties"]["source_document_id"]["enum"], ["A", "B", None])
        self.assertEqual(self.fixture.artifact_path.read_bytes(), before)

    def test_quote_from_another_document_is_not_verified_on_the_same_page_number(self):
        report = self.audit(FakeProvider(self.response(document_id="A")))
        self.assertFalse(report["data"]["findings"][0]["citation_verified"])

    def test_missing_or_unknown_document_id_enters_bounded_repairs(self):
        missing = self.response()
        missing["findings"][0].pop("source_document_id")
        for response in (missing, self.response(document_id="outside")):
            with self.subTest(response=response):
                provider = FakeProvider(response)
                with self.assertRaises(StructuredOutputFailure):
                    self.audit(provider)
                self.assertEqual(len(provider.requests), 3)

    def test_secondary_changes_stop_before_model_or_parser(self):
        self.fixture.amendment.write_bytes(b"%PDF-1.4\nchanged SPDS\n")
        provider = FakeProvider(judge_response(verdict="pass"))
        with patch("src.evaluation.quality.ingest_pdfs", side_effect=AssertionError("parser called")):
            with self.assertRaises(ValueError):
                audit_one(manifest=self.fixture.manifest, schema_path=self.fixture.schema_path,
                    artifact_path=self.fixture.artifact_path, source_root=self.fixture.input_root,
                    selection=self.selection, provider=provider)
        self.assertEqual(provider.requests, [])

    def test_review_queue_opens_the_cited_secondary_pdf_and_keeps_group_identity(self):
        queue = build_review_queue([self.audit(FakeProvider(self.response()))], sample_rate=0, seed=42)
        self.assertEqual(queue["data"]["items"][0]["source_document"], str(self.fixture.amendment))
        path = self.fixture.root / "queue.json"
        write_artifact(path, queue, data_contract="quality/review_queue")
        self.assertEqual(load_queue(path)["data"]["audited_inputs"][0]["document_group_id"], self.fixture.group.group_id)

    def test_pass_delivery_and_resume_both_reject_changed_secondary_bytes(self):
        f = self.fixture
        destination = f.root / "judge"
        provider = FakeProvider(judge_response(verdict="pass"))
        with patch("src.evaluation.quality.ingest_pdfs", return_value=self.documents):
            run = run_quality_audit(manifest=f.manifest, schema_path=f.schema_path,
                artifact_dir=f.artifact_path.parent, source_root=f.input_root, output_dir=destination,
                selection=self.selection, provider=provider)
        proof = require_delivery_approval(manifest=f.manifest, schema_path=f.schema_path,
            artifact_path=f.artifact_path, quality_results_path=run.results_path)
        self.assertEqual(proof["status"], "judge_pass")
        f.amendment.write_bytes(b"%PDF-1.4\nChanged after audit\n")
        with self.assertRaises(ValueError):
            require_delivery_approval(manifest=f.manifest, schema_path=f.schema_path,
                artifact_path=f.artifact_path, quality_results_path=run.results_path)
        with self.assertRaises(ValueError):
            run_quality_audit(manifest=f.manifest, schema_path=f.schema_path,
                artifact_dir=f.artifact_path.parent, source_root=f.input_root, output_dir=destination,
                selection=self.selection, provider=provider, resume=True)
        self.assertEqual(len(provider.requests), 1)
