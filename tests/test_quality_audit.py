from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.pdf_ingestion.models import PageRepresentation, ParsedPDF, TextBlock
from src.common.json_artifacts import build_success_artifact, read_artifact, write_artifact
from src.common.json_codec import loads_json
from src.common.model_config import ModelSelection
from src.common.model_provider import ModelResponse, ModelUsage
from src.evaluation.quality import audit_one, build_review_queue, load_quality_results, quality_queue_id, run_quality_audit
from src.evaluation.quality_review import load_decisions, load_queue, quality_metrics, save_decision
from src.common.models import ExtractionResult
from src.schema.canonical import compile_canonical_extraction_contract
from src.verticals.manifest import resolve_manifest


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "configs/travel_insurance/canonical_schema_v1.json"


class FakeProvider:
    def __init__(self, response: dict | list[dict]):
        self.responses = response if isinstance(response, list) else [response]
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        response = self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]
        return ModelResponse(
            text=json.dumps(response), provider="openai", model="gpt-5",
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
        self.assertTrue(provider.requests[0].structured_output.strict)
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
        usage = [json.loads(line) for line in (self.root / "usage.jsonl").read_text().splitlines()]
        self.assertEqual(usage[-1]["failure_kind"], "business_validation")
        self.assertEqual(usage[-1]["error_code"], "invalid_field_name")

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

    def test_review_queue_rejects_mixed_shared_prompt_versions(self):
        first = self.audit(FakeProvider(judge_response()))
        second = self.audit(FakeProvider(judge_response(verdict="pass")))
        second["data"]["prompt_bundle_sha256"] = "0" * 64
        second["provenance"]["source_artifacts"][0] = str(self.root / "other.json")
        with self.assertRaisesRegex(ValueError, "same judge model and prompt"):
            build_review_queue([first, second], sample_rate=0.05, seed=42)

    def test_changed_audit_inputs_reject_prior_human_decisions(self):
        report = self.audit(FakeProvider(judge_response()))
        original = build_review_queue([report], sample_rate=0.05, seed=42)
        queue_path = self.root / "original_queue.json"
        decisions_path = self.root / "decisions.json"
        write_artifact(queue_path, original, data_contract="quality/review_queue")
        save_decision(queue_path, decisions_path, original["data"]["items"][0]["item_id"],
                      "no_issue", "Checked the original PDF", "Tester")
        before = decisions_path.read_bytes()
        changes = (
            ("data", "pdf_sha256", "e" * 64),
            ("data", "source_artifact_sha256", "f" * 64),
            ("data", "prompt_bundle_sha256", "0" * 64),
            ("provenance", "provider", "anthropic"),
            ("provenance", "model", "gpt-5-mini"),
            ("provenance", "document_parser", "mineru"),
        )
        for section, key, value in changes:
            with self.subTest(changed=key):
                changed = copy.deepcopy(report)
                changed[section][key] = value
                queue = build_review_queue([changed], sample_rate=0.05, seed=42)
                self.assertNotEqual(original["data"]["queue_id"], queue["data"]["queue_id"])
                changed_path = self.root / f"{key}_queue.json"
                write_artifact(changed_path, queue, data_contract="quality/review_queue")
                with self.assertRaisesRegex(ValueError, "different queue identity"):
                    load_decisions(changed_path, decisions_path)
                self.assertEqual(decisions_path.read_bytes(), before)

    def test_unsampled_pass_inputs_are_bound_to_the_queue(self):
        report = self.audit(FakeProvider(judge_response(verdict="pass")))
        original = build_review_queue([report], sample_rate=0, seed=42)
        self.assertEqual(original["data"]["items"], [])
        for key in ("pdf_sha256", "source_artifact_sha256"):
            with self.subTest(changed=key):
                changed = copy.deepcopy(report)
                changed["data"][key] = "0" * 64
                queue = build_review_queue([changed], sample_rate=0, seed=42)
                self.assertNotEqual(original["data"]["queue_id"], queue["data"]["queue_id"])

    def test_legacy_quality_queue_remains_readable_but_cannot_record_decisions(self):
        report = self.audit(FakeProvider(judge_response()))
        queue = build_review_queue([report], sample_rate=0.05, seed=42)
        queue_path = self.root / "queue.json"
        decisions_path = self.root / "decisions.json"
        write_artifact(queue_path, queue, data_contract="quality/review_queue")
        item_id = queue["data"]["items"][0]["item_id"]
        save_decision(queue_path, decisions_path, item_id, "no_issue", "Checked", "Tester")
        decisions = load_decisions(queue_path, decisions_path)
        queue["contract_version"] = "1.0.0"
        queue["data"].pop("audited_inputs", None)
        queue["data"]["queue_id"] = quality_queue_id(queue["data"])
        decisions["data"]["queue_id"] = queue["data"]["queue_id"]
        write_artifact(queue_path, queue, data_contract="quality/review_queue", overwrite=True)
        write_artifact(decisions_path, decisions, data_contract="quality/review_decisions", overwrite=True)
        before = decisions_path.read_bytes()
        self.assertEqual(load_decisions(queue_path, decisions_path)["data"], decisions["data"])
        for target in (decisions_path, self.root / "new_decisions.json"):
            with self.subTest(target=target.name), self.assertRaisesRegex(ValueError, "read-only"):
                save_decision(queue_path, target, item_id, "no_issue", "Changed", "Tester")
        self.assertEqual(decisions_path.read_bytes(), before)
        self.assertFalse((self.root / "new_decisions.json").exists())

    def test_new_queue_requires_complete_audit_bindings_and_known_version(self):
        report = self.audit(FakeProvider(judge_response()))
        original = build_review_queue([report], sample_rate=0.05, seed=42)
        for mismatch in ("missing_inputs", "count", "unknown_version", "duplicate_inputs",
                         "provenance_model", "item_source"):
            with self.subTest(mismatch=mismatch):
                queue = copy.deepcopy(original)
                queue["contract_version"] = "2.0.0"
                if mismatch == "missing_inputs":
                    queue["data"].pop("audited_inputs", None)
                elif mismatch == "count":
                    queue["data"]["audited_documents"] += 1
                elif mismatch == "unknown_version":
                    queue["contract_version"] = "99.0.0"
                elif mismatch == "duplicate_inputs":
                    queue["data"]["audited_inputs"] *= 2
                    queue["data"]["audited_documents"] = 2
                elif mismatch == "provenance_model":
                    queue["provenance"]["model"] = "gpt-5-mini"
                else:
                    queue["data"]["items"][0]["source_document"] = str(self.root / "other.pdf")
                queue["data"]["queue_id"] = quality_queue_id(queue["data"])
                path = self.root / f"{mismatch}.json"
                write_artifact(path, queue, data_contract="quality/review_queue")
                with self.assertRaises(ValueError):
                    load_queue(path)

    def test_legacy_queue_resume_preserves_files_and_can_regenerate_from_reports(self):
        out = self.root / "quality"
        with patch("src.evaluation.quality.ingest_pdfs", return_value=(self.document,)):
            initial = run_quality_audit(
                manifest=self.manifest, schema_path=SCHEMA_PATH,
                artifact_dir=self.artifact.parent, source_root=self.source_root,
                output_dir=out, selection=self.selection, provider=FakeProvider(judge_response()),
            )
        queue = load_queue(initial.queue_path)
        queue["contract_version"] = "1.0.0"
        queue["data"].pop("audited_inputs", None)
        queue["data"]["queue_id"] = quality_queue_id(queue["data"])
        write_artifact(initial.queue_path, queue, data_contract="quality/review_queue", overwrite=True)
        before_results = initial.results_path.read_bytes()
        before_queue = initial.queue_path.read_bytes()
        provider = FakeProvider(judge_response())
        with self.assertRaisesRegex(ValueError, "read-only"):
            run_quality_audit(
                manifest=self.manifest, schema_path=SCHEMA_PATH,
                artifact_dir=self.artifact.parent, source_root=self.source_root,
                output_dir=out, selection=self.selection, provider=provider, resume=True,
            )
        self.assertEqual(initial.results_path.read_bytes(), before_results)
        self.assertEqual(initial.queue_path.read_bytes(), before_queue)
        summary = run_quality_audit(
            manifest=self.manifest, schema_path=SCHEMA_PATH,
            artifact_dir=self.artifact.parent, source_root=self.source_root,
            output_dir=out, selection=self.selection, provider=None, summary_only=True,
        )
        self.assertIsNone(summary.queue_path)
        fresh = self.root / "new_quality"
        shutil.copytree(out / "reports", fresh / "reports")
        regenerated = run_quality_audit(
            manifest=self.manifest, schema_path=SCHEMA_PATH,
            artifact_dir=self.artifact.parent, source_root=self.source_root,
            output_dir=fresh, selection=self.selection, provider=provider, resume=True,
        )
        self.assertEqual(load_queue(regenerated.queue_path)["contract_version"], "2.0.0")
        self.assertFalse(provider.requests)
        self.assertEqual(initial.queue_path.read_bytes(), before_queue)

    def test_directory_run_writes_reports_and_queue_without_changing_extraction(self):
        before = self.artifact.read_bytes()
        provider = FakeProvider(judge_response())
        out = self.root / "quality"
        with patch("src.evaluation.quality.ingest_pdfs", return_value=(self.document,)):
            result = run_quality_audit(
                manifest=self.manifest, schema_path=SCHEMA_PATH,
                artifact_dir=self.artifact.parent, source_root=self.source_root,
                output_dir=out, selection=self.selection, provider=provider,
                sample_rate=0.1, seed=2,
            )
        self.assertTrue(result.queue_path.is_file())
        self.assertTrue(result.results_path.is_file())
        self.assertEqual(load_quality_results(result.results_path)["data"]["status"], "complete")
        self.assertTrue((out / "reports" / "fixture.json").is_file())
        self.assertEqual(before, self.artifact.read_bytes())
        self.assertEqual(len(provider.requests), 1)

    def test_directory_run_allows_explicit_higher_extraction_limit(self):
        provider = FakeProvider(judge_response())
        with patch("src.evaluation.quality.ingest_pdfs", return_value=(self.document,)):
            too_small = run_quality_audit(
                manifest=self.manifest, schema_path=SCHEMA_PATH,
                artifact_dir=self.artifact.parent, source_root=self.source_root,
                output_dir=self.root / "too_small", selection=self.selection,
                provider=provider, max_extraction_chars=10,
            )
            result = run_quality_audit(
                manifest=self.manifest, schema_path=SCHEMA_PATH,
                artifact_dir=self.artifact.parent, source_root=self.source_root,
                output_dir=self.root / "fits", selection=self.selection,
                provider=provider, max_extraction_chars=160000,
            )
        self.assertEqual(load_quality_results(too_small.results_path)["data"]["failed_documents"], 1)
        self.assertIsNone(too_small.queue_path)
        self.assertTrue(result.queue_path.is_file())

    def test_failed_judge_writes_safe_json_and_resume_skips_successes(self):
        later = self.artifact.parent / "later.json"
        later.write_bytes(self.artifact.read_bytes())
        bad = judge_response()
        bad["findings"][0]["field_name"] = "imaginary_field"
        provider = FakeProvider([judge_response(), bad, bad, bad])
        out = self.root / "quality"
        with patch("src.evaluation.quality.ingest_pdfs", return_value=(self.document,)):
            partial = run_quality_audit(
                manifest=self.manifest, schema_path=SCHEMA_PATH,
                artifact_dir=self.artifact.parent, source_root=self.source_root,
                output_dir=out, selection=self.selection, provider=provider,
            )
        initial_results = load_quality_results(partial.results_path)
        data = initial_results["data"]
        self.assertEqual(data["status"], "partial")
        self.assertEqual((data["completed_documents"], data["failed_documents"]), (1, 1))
        self.assertIsNone(partial.queue_path)
        self.assertEqual(data["documents"][1]["failure"]["kind"], "business_validation")
        self.assertEqual(data["documents"][1]["failure"]["code"], "invalid_field_name")
        self.assertNotIn("imaginary_field", partial.results_path.read_text())
        self.assertEqual(len(provider.requests), 4)
        first_report = (out / "reports" / "fixture.json").read_bytes()
        resumed_provider = FakeProvider(judge_response(verdict="pass"))
        with patch("src.evaluation.quality.ingest_pdfs", return_value=(self.document,)):
            resumed = run_quality_audit(
                manifest=self.manifest, schema_path=SCHEMA_PATH,
                artifact_dir=self.artifact.parent, source_root=self.source_root,
                output_dir=out, selection=self.selection, provider=resumed_provider,
                resume=True,
            )
        self.assertEqual(len(resumed_provider.requests), 1)
        self.assertEqual((out / "reports" / "fixture.json").read_bytes(), first_report)
        resumed_results = load_quality_results(resumed.results_path)
        self.assertEqual(resumed_results["data"]["status"], "complete")
        self.assertEqual(resumed_results["provenance"]["run_id"],
                         initial_results["provenance"]["run_id"])
        self.assertTrue(resumed.queue_path.is_file())

    def test_resume_rejects_changed_shared_judge_template(self):
        out = self.root / "quality"
        with patch("src.evaluation.quality.ingest_pdfs", return_value=(self.document,)):
            run_quality_audit(
                manifest=self.manifest, schema_path=SCHEMA_PATH,
                artifact_dir=self.artifact.parent, source_root=self.source_root,
                output_dir=out, selection=self.selection,
                provider=FakeProvider(judge_response()),
            )
        from src.evaluation.quality import get_shared_prompt
        original_loader = get_shared_prompt

        def changed_template(name):
            text = original_loader(name)
            return text + " changed" if name == "quality_audit_request" else text

        provider = FakeProvider(judge_response())
        with patch("src.evaluation.quality.get_shared_prompt", side_effect=changed_template):
            with self.assertRaisesRegex(ValueError, "prompt"):
                run_quality_audit(
                    manifest=self.manifest, schema_path=SCHEMA_PATH,
                    artifact_dir=self.artifact.parent, source_root=self.source_root,
                    output_dir=out, selection=self.selection, provider=provider,
                    resume=True,
                )
        self.assertFalse(provider.requests)

    def test_resume_identity_mismatch_preserves_results_before_model_call(self):
        out = self.root / "quality"
        with patch("src.evaluation.quality.ingest_pdfs", return_value=(self.document,)):
            initial = run_quality_audit(
                manifest=self.manifest, schema_path=SCHEMA_PATH,
                artifact_dir=self.artifact.parent, source_root=self.source_root,
                output_dir=out, selection=self.selection,
                provider=FakeProvider(judge_response()),
            )
        before = initial.results_path.read_bytes()
        for mismatch in ("model", "artifact_set"):
            with self.subTest(mismatch=mismatch):
                selection = self.selection
                if mismatch == "model":
                    selection = ModelSelection("openai", "gpt-5-mini", "markdown")
                    message = "different schema, prompt or judge model"
                else:
                    (self.artifact.parent / "extra.json").write_bytes(self.artifact.read_bytes())
                    message = "different extraction artifact set"
                provider = FakeProvider(judge_response())
                with self.assertRaisesRegex(ValueError, message):
                    run_quality_audit(
                        manifest=self.manifest, schema_path=SCHEMA_PATH,
                        artifact_dir=self.artifact.parent, source_root=self.source_root,
                        output_dir=out, selection=selection, provider=provider,
                        resume=True,
                    )
                self.assertFalse(provider.requests)
                self.assertEqual(initial.results_path.read_bytes(), before)

    def test_resume_accepts_legacy_reports_with_unchanged_shared_prompts(self):
        out = self.root / "quality"
        with patch("src.evaluation.quality.ingest_pdfs", return_value=(self.document,)):
            result = run_quality_audit(
                manifest=self.manifest, schema_path=SCHEMA_PATH,
                artifact_dir=self.artifact.parent, source_root=self.source_root,
                output_dir=out, selection=self.selection,
                provider=FakeProvider(judge_response()),
            )
        report_path = out / "reports/fixture.json"
        report = read_artifact(report_path, expected_type="quality_audit",
                               data_contract="quality/audit_report")
        report["data"].pop("prompt_bundle_sha256")
        write_artifact(report_path, report, data_contract="quality/audit_report",
                       overwrite=True)
        results = load_quality_results(result.results_path)
        results["data"].pop("prompt_bundle_sha256")
        results["data"]["documents"][0]["report"].pop("prompt_bundle_sha256")
        write_artifact(result.results_path, results, data_contract="quality/batch_results",
                       overwrite=True)
        provider = FakeProvider(judge_response())
        with patch("src.evaluation.quality.ingest_pdfs", return_value=(self.document,)):
            resumed = run_quality_audit(
                manifest=self.manifest, schema_path=SCHEMA_PATH,
                artifact_dir=self.artifact.parent, source_root=self.source_root,
                output_dir=out, selection=self.selection, provider=provider,
                resume=True,
            )
        self.assertEqual(resumed.completed_documents, 1)
        self.assertFalse(provider.requests)

    def test_summary_only_materializes_legacy_partial_without_model_call(self):
        out = self.root / "quality"
        report_path = out / "reports" / "fixture.json"
        with patch("src.evaluation.quality.ingest_pdfs", return_value=(self.document,)):
            report = self.audit(FakeProvider(judge_response()))
        report["data"].pop("prompt_bundle_sha256")
        write_artifact(report_path, report, data_contract="quality/audit_report")
        later = self.artifact.parent / "later.json"
        later.write_bytes(self.artifact.read_bytes())
        result = run_quality_audit(
            manifest=self.manifest, schema_path=SCHEMA_PATH,
            artifact_dir=self.artifact.parent, source_root=self.source_root,
            output_dir=out, selection=self.selection, provider=None,
            summary_only=True,
        )
        data = load_quality_results(result.results_path)["data"]
        self.assertEqual(data["completed_documents"], 1)
        self.assertEqual(data["pending_documents"], 1)
        self.assertIsNone(result.queue_path)
        provider = FakeProvider(judge_response(verdict="pass"))
        with patch("src.evaluation.quality.ingest_pdfs", return_value=(self.document,)):
            resumed = run_quality_audit(
                manifest=self.manifest, schema_path=SCHEMA_PATH,
                artifact_dir=self.artifact.parent, source_root=self.source_root,
                output_dir=out, selection=self.selection, provider=provider,
                resume=True,
            )
        self.assertEqual(len(provider.requests), 1)
        self.assertTrue(resumed.queue_path.is_file())

    def test_summary_only_does_not_create_unverified_human_review_queue(self):
        out = self.root / "quality"
        with patch("src.evaluation.quality.ingest_pdfs", return_value=(self.document,)):
            report = self.audit(FakeProvider(judge_response()))
        write_artifact(out / "reports" / "fixture.json", report,
                       data_contract="quality/audit_report")
        result = run_quality_audit(
            manifest=self.manifest, schema_path=SCHEMA_PATH,
            artifact_dir=self.artifact.parent, source_root=self.source_root,
            output_dir=out, selection=self.selection, provider=None,
            summary_only=True,
        )
        self.assertIsNone(result.queue_path)
        self.assertFalse((out / "review_queue.json").exists())
        provider = FakeProvider(judge_response())
        resumed = run_quality_audit(
            manifest=self.manifest, schema_path=SCHEMA_PATH,
            artifact_dir=self.artifact.parent, source_root=self.source_root,
            output_dir=out, selection=self.selection, provider=provider,
            resume=True,
        )
        self.assertFalse(provider.requests)
        self.assertTrue(resumed.queue_path.is_file())

    def test_resume_rejects_changed_extraction_before_model_call(self):
        provider = FakeProvider(judge_response())
        out = self.root / "quality"
        with patch("src.evaluation.quality.ingest_pdfs", return_value=(self.document,)):
            run_quality_audit(
                manifest=self.manifest, schema_path=SCHEMA_PATH,
                artifact_dir=self.artifact.parent, source_root=self.source_root,
                output_dir=out, selection=self.selection, provider=provider,
            )
        self.write_extraction(data={**self.data, "_document_notes": "Changed"})
        resumed_provider = FakeProvider(judge_response())
        with self.assertRaisesRegex(ValueError, "changed"):
            run_quality_audit(
                manifest=self.manifest, schema_path=SCHEMA_PATH,
                artifact_dir=self.artifact.parent, source_root=self.source_root,
                output_dir=out, selection=self.selection, provider=resumed_provider,
                resume=True,
            )
        self.assertFalse(resumed_provider.requests)

    def test_failure_budget_preserves_pending_documents(self):
        for name in ("later.json", "last.json"):
            (self.artifact.parent / name).write_bytes(self.artifact.read_bytes())
        bad = judge_response()
        bad["findings"][0]["field_name"] = "not_a_canonical_field"
        provider = FakeProvider(bad)
        out = self.root / "quality"
        with patch("src.evaluation.quality.ingest_pdfs", return_value=(self.document,)):
            result = run_quality_audit(
                manifest=self.manifest, schema_path=SCHEMA_PATH,
                artifact_dir=self.artifact.parent, source_root=self.source_root,
                output_dir=out, selection=self.selection, provider=provider,
                max_failures=1,
            )
        self.assertEqual(len(provider.requests), 3)
        self.assertEqual((result.failed_documents, result.pending_documents), (1, 2))
        self.assertNotIn("not_a_canonical_field", result.results_path.read_text())

    def test_unexpected_provider_failure_stops_without_trying_later_pdfs(self):
        (self.artifact.parent / "later.json").write_bytes(self.artifact.read_bytes())

        class BrokenProvider:
            def __init__(self):
                self.requests = []

            def generate(self, request):
                self.requests.append(request)
                raise RuntimeError("provider transport failed")

        provider = BrokenProvider()
        with patch("src.evaluation.quality.ingest_pdfs", return_value=(self.document,)):
            result = run_quality_audit(
                manifest=self.manifest, schema_path=SCHEMA_PATH,
                artifact_dir=self.artifact.parent, source_root=self.source_root,
                output_dir=self.root / "quality", selection=self.selection,
                provider=provider,
            )
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual((result.failed_documents, result.pending_documents), (1, 1))
        self.assertNotIn("provider transport failed", result.results_path.read_text())


if __name__ == "__main__":
    unittest.main()
