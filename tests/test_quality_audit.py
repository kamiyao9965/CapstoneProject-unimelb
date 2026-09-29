from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.PDFingestor.models import PageRepresentation, ParsedPDF, TextBlock
from src.common.json_artifacts import build_success_artifact, write_artifact
from src.common.json_codec import loads_json
from src.common.model_config import ModelSelection
from src.common.model_provider import ModelResponse, ModelUsage
from src.evaluation.quality import audit_one, build_review_queue, run_quality_audit
from src.evaluation.quality_review import load_decisions, quality_metrics, save_decision
from src.models import ExtractionResult
from src.schema.canonical import compile_canonical_extraction_contract
from src.verticals.manifest import resolve_manifest


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "configs/travel_insurance/canonical_schema_v1.json"


class FakeProvider:
    def __init__(self, response: dict):
        self.response = response
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return ModelResponse(
            text=json.dumps(self.response), provider="openai", model="gpt-5",
            usage=ModelUsage(input_tokens=100, output_tokens=50, total_tokens=150),
        )


def product_for_schema() -> dict:
    schema = loads_json(SCHEMA_PATH.read_text(encoding="utf-8"))
    product = {}
    for field in schema["fields"]:
        if not field["required"]:
            continue
        if field["nullable"]:
            value = None
        elif field["type"] == "enum":
            value = field["values"][0]
        elif field["type"] == "list[object]":
            value = []
        elif field["type"] == "number":
            value = 0
        elif field["type"] == "boolean":
            value = False
        else:
            value = "Fixture"
        product[field["name"]] = value
    product["product_name"] = "Fixture Basic"
    product["_unfilled"] = []
    product["_notes"] = None
    return product


def judge_response(*, verdict="review", quote="Basic cover is excluded") -> dict:
    return {
        "verdict": verdict,
        "correctness": "mixed" if verdict == "review" else "supported",
        "evidence_support": "mixed" if verdict == "review" else "supported",
        "uncertainty": "medium" if verdict == "review" else "low",
        "summary": "Plan name may not match the source.",
        "findings": ([{
            "product_index": 0, "field_name": "plan_name",
            "issue_type": "wrong_value", "reason": "The plan is Basic.",
            "source_page": 1, "source_quote": quote,
        }] if verdict == "review" else []),
    }


class QualityAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source_root = self.root / "source"
        self.source_root.mkdir()
        self.pdf = self.source_root / "fixture.pdf"
        self.pdf.write_bytes(b"%PDF synthetic fixture")
        self.artifact = self.root / "extractions" / "fixture.json"
        self.artifact.parent.mkdir()
        self.manifest = resolve_manifest(vertical="travel_insurance")
        self.selection = ModelSelection("openai", "gpt-5", "markdown")
        self.schema = loads_json(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.data = {"products": [product_for_schema()], "_document_notes": None}
        self.document = ParsedPDF(
            pdf_id="fixture", pdf_hash=hashlib.sha256(self.pdf.read_bytes()).hexdigest(),
            source_path=str(self.pdf), pages=[PageRepresentation(
                page_num=1, width=100, height=100,
                blocks=[TextBlock(block_id="1", content="Basic cover is excluded",
                                  top=1)],
            )],
        )
        self.write_extraction()

    def write_extraction(self, *, source=None, schema_version="1.0.0", data=None):
        artifact = build_success_artifact(
            artifact_type="extraction_result", contract_version="1.0.0",
            data=data or self.data,
            provenance={
                "run_id": "fixture-run", "vertical": "travel_insurance",
                "schema_version": schema_version, "provider": "openai",
                "model": "gpt-5", "document_input": "markdown",
                "document_parser": "pdfingestor",
                "source_documents": [str(source or self.pdf)], "source_artifacts": [],
            },
            data_contract_schema=compile_canonical_extraction_contract(self.schema),
        )
        write_artifact(self.artifact, artifact,
                       data_contract_schema=compile_canonical_extraction_contract(self.schema),
                       overwrite=True)

    def audit(self, provider, **kwargs):
        with patch("src.evaluation.quality.ingest_pdfs", return_value=(self.document,)):
            return audit_one(
                manifest=self.manifest, schema_path=SCHEMA_PATH,
                artifact_path=self.artifact, source_root=self.source_root,
                selection=self.selection, provider=provider,
                usage_log_path=self.root / "usage.jsonl", **kwargs,
            )

    def test_judge_uses_pdf_and_schema_and_records_verified_quote(self):
        provider = FakeProvider(judge_response())
        report = self.audit(provider)
        self.assertEqual(report["artifact_type"], "quality_audit")
        self.assertEqual(report["data"]["verdict"], "review")
        self.assertTrue(report["data"]["findings"][0]["citation_verified"])
        self.assertEqual(report["data"]["findings"][0]["extracted_value"], None)
        self.assertIn("Basic cover is excluded", provider.requests[0].user_text)
        self.assertIn("Official full plan-level name", provider.requests[0].user_text)
        self.assertIn("Fixture Basic", provider.requests[0].user_text)
        self.assertEqual(provider.requests[0].document_paths, ())
        self.assertEqual(len((self.root / "usage.jsonl").read_text().splitlines()), 1)

    def test_unsupported_citation_is_marked_not_accepted(self):
        report = self.audit(FakeProvider(judge_response(quote="invented evidence")))
        self.assertFalse(report["data"]["findings"][0]["citation_verified"])

    def test_main_cli_extraction_result_format_is_accepted(self):
        self.artifact.unlink()
        ExtractionResult(
            vertical="travel_insurance", schema_version="1.0.0",
            source_path=str(self.pdf), provider="openai", model="gpt-5",
            document_parser="pdfingestor", data=self.data,
        ).write_json(self.artifact)
        report = self.audit(FakeProvider(judge_response()))
        self.assertEqual(report["data"]["verdict"], "review")
        self.assertEqual(report["provenance"]["document_parser"], "pdfingestor")

    def test_bad_identity_and_outside_source_stop_before_model_call(self):
        provider = FakeProvider(judge_response())
        self.write_extraction(schema_version="0.0.0")
        with self.assertRaisesRegex(ValueError, "schema version"):
            self.audit(provider)
        self.write_extraction(source=self.root / "elsewhere.pdf")
        with self.assertRaisesRegex(ValueError, "source root"):
            self.audit(provider)
        self.assertFalse(provider.requests)

    def test_unapproved_schema_stops_before_model_call(self):
        draft = dict(self.schema)
        draft["status"] = "candidate"
        draft["review"] = None
        draft_path = self.root / "candidate.json"
        draft_path.write_text(json.dumps(draft), encoding="utf-8")
        provider = FakeProvider(judge_response())
        with self.assertRaisesRegex(ValueError, "human-approved"):
            with patch("src.evaluation.quality.ingest_pdfs", return_value=(self.document,)):
                audit_one(
                    manifest=self.manifest, schema_path=draft_path,
                    artifact_path=self.artifact, source_root=self.source_root,
                    selection=self.selection, provider=provider,
                )
        self.assertFalse(provider.requests)

    def test_invalid_field_is_bounded_repair_then_fails(self):
        bad = judge_response()
        bad["findings"][0]["field_name"] = "imaginary_field"
        provider = FakeProvider(bad)
        with self.assertRaisesRegex(Exception, "3 attempt"):
            self.audit(provider)
        self.assertEqual(len(provider.requests), 3)
        self.assertEqual(len((self.root / "usage.jsonl").read_text().splitlines()), 3)

    def test_context_limit_fails_without_silent_truncation(self):
        provider = FakeProvider(judge_response())
        with self.assertRaisesRegex(ValueError, "maximum"):
            self.audit(provider, max_document_chars=10)
        self.assertFalse(provider.requests)

    def test_pass_cannot_hide_uncertainty(self):
        result = judge_response(verdict="pass")
        result["uncertainty"] = "high"
        provider = FakeProvider(result)
        with self.assertRaisesRegex(Exception, "3 attempt"):
            self.audit(provider)
        self.assertEqual(len(provider.requests), 3)

    def test_uncertain_document_without_field_finding_still_enters_queue(self):
        report = self.audit(FakeProvider(judge_response(verdict="uncertain")))
        queue = build_review_queue([report], sample_rate=0.05, seed=7)
        self.assertEqual(len(queue["data"]["items"]), 1)
        self.assertEqual(queue["data"]["items"][0]["kind"], "uncertain")

    def test_queue_and_decisions_are_bound_and_metrics_are_not_accuracy(self):
        finding_report = self.audit(FakeProvider(judge_response()))
        pass_report = self.audit(FakeProvider(judge_response(verdict="pass")))
        pass_report["data"]["source_artifact_sha256"] = "b" * 64
        pass_report["provenance"]["source_artifacts"][0] = str(self.root / "other.json")
        queue = build_review_queue([finding_report, pass_report], sample_rate=0.1, seed=7)
        kinds = {item["kind"] for item in queue["data"]["items"]}
        self.assertEqual(kinds, {"finding", "pass_sample"})
        self.assertEqual(queue["data"]["queue_id"],
                         build_review_queue([pass_report, finding_report], sample_rate=0.1, seed=7)["data"]["queue_id"])
        queue_path = self.root / "review_queue.json"
        decision_path = self.root / "review_decisions.json"
        write_artifact(queue_path, queue, data_contract="quality/review_queue")
        finding_item = next(item for item in queue["data"]["items"] if item["kind"] == "finding")
        pass_item = next(item for item in queue["data"]["items"] if item["kind"] == "pass_sample")
        save_decision(queue_path, decision_path, finding_item["item_id"],
                      "no_issue", "False alarm", "Tester")
        save_decision(queue_path, decision_path, pass_item["item_id"],
                      "issue_found", "Missed exclusion", "Tester")
        decisions = load_decisions(queue_path, decision_path)
        metrics = quality_metrics(queue, decisions)
        self.assertEqual(metrics["dismissed_alerts"], 1)
        self.assertEqual(metrics["sampled_pass_misses"], 1)
        self.assertNotIn("accuracy", metrics)
        queue["data"]["items"].pop()
        write_artifact(queue_path, queue, data_contract="quality/review_queue", overwrite=True)
        with self.assertRaisesRegex(ValueError, "queue identity"):
            save_decision(queue_path, decision_path, finding_item["item_id"],
                          "no_issue", "again", "Tester")

    def test_directory_run_writes_reports_and_queue_without_changing_extraction(self):
        before = self.artifact.read_bytes()
        provider = FakeProvider(judge_response())
        out = self.root / "quality"
        with patch("src.evaluation.quality.ingest_pdfs", return_value=(self.document,)):
            queue_path = run_quality_audit(
                manifest=self.manifest, schema_path=SCHEMA_PATH,
                artifact_dir=self.artifact.parent, source_root=self.source_root,
                output_dir=out, selection=self.selection, provider=provider,
                sample_rate=0.1, seed=2,
            )
        self.assertTrue(queue_path.is_file())
        self.assertTrue((out / "reports" / "fixture.json").is_file())
        self.assertEqual(before, self.artifact.read_bytes())
        self.assertEqual(len(provider.requests), 1)


if __name__ == "__main__":
    unittest.main()
