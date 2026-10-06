from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.common.json_artifacts import build_success_artifact, write_artifact
from src.evaluation.quality import build_review_queue, quality_queue_id
from src.evaluation.quality_review import load_decisions


ROOT = Path(__file__).resolve().parents[1]


class QualityReviewAppTests(unittest.TestCase):
    APP_PATH = ROOT / "src/ui/quality_review_app.py"

    def test_legacy_queue_displays_read_only_and_disables_saving(self):
        with tempfile.TemporaryDirectory() as temporary:
            quality_dir = Path(temporary)
            report = build_success_artifact(
                artifact_type="quality_audit", contract_version="1.0.0",
                data={
                    "source_artifact_sha256": "a" * 64, "schema_sha256": "b" * 64,
                    "pdf_sha256": "c" * 64, "prompt_sha256": "d" * 64,
                    "verdict": "uncertain", "correctness": "unknown",
                    "evidence_support": "unknown", "uncertainty": "high",
                    "summary": "Check the source PDF.", "findings": [],
                }, data_contract="quality/audit_report",
                provenance={
                    "run_id": "test", "vertical": "travel_insurance", "schema_version": "1.0.0",
                    "provider": "openai", "model": "gpt-5", "document_input": "markdown",
                    "source_documents": [str(quality_dir / "synthetic.pdf")],
                    "source_artifacts": [str(quality_dir / "synthetic.json")],
                },
            )
            queue = build_review_queue([report], sample_rate=0, seed=7)
            queue["contract_version"] = "1.0.0"
            queue["data"].pop("audited_inputs", None)
            queue["data"]["queue_id"] = quality_queue_id(queue["data"])
            queue_path = quality_dir / "review_queue.json"
            write_artifact(queue_path, queue, data_contract="quality/review_queue")
            before = queue_path.read_bytes()
            item_id = queue["data"]["items"][0]["item_id"]
            with patch("src.ui.quality_review.parse_cli_args",
                       return_value=argparse.Namespace(quality_dir=str(quality_dir))):
                at = AppTest.from_file(str(self.APP_PATH), default_timeout=20).run()
            self.assertFalse(at.exception)
            self.assertTrue(any("read-only" in warning.value for warning in at.warning))
            self.assertTrue(at.button(key=f"save:{item_id}").disabled)
            self.assertFalse((quality_dir / "review_decisions.json").exists())
            self.assertEqual(queue_path.read_bytes(), before)

    def test_review_ui_records_a_queue_bound_human_decision(self):
        with tempfile.TemporaryDirectory() as temporary:
            quality_dir = Path(temporary)
            source = quality_dir / "synthetic.pdf"
            report = build_success_artifact(
                artifact_type="quality_audit", contract_version="1.0.0",
                data={
                    "source_artifact_sha256": "a" * 64,
                    "schema_sha256": "b" * 64,
                    "pdf_sha256": "c" * 64,
                    "prompt_sha256": "d" * 64,
                    "verdict": "review", "correctness": "mixed",
                    "evidence_support": "mixed", "uncertainty": "medium",
                    "summary": "Check plan name.",
                    "findings": [{
                        "product_index": 0, "field_name": "plan_name", "issue_type": "wrong_value",
                        "reason": "PDF says Basic", "source_page": 1,
                        "source_quote": "Basic", "citation_verified": True,
                        "extracted_value": "Premium",
                    }],
                },
                data_contract="quality/audit_report",
                provenance={
                    "run_id": "test", "vertical": "travel_insurance",
                    "schema_version": "1.0.0", "provider": "openai", "model": "gpt-5",
                    "document_input": "markdown", "source_documents": [str(source)],
                    "source_artifacts": [str(quality_dir / "synthetic.json")],
                },
            )
            queue = build_review_queue([report], sample_rate=0.05, seed=7)
            queue_path = quality_dir / "review_queue.json"
            decisions_path = quality_dir / "review_decisions.json"
            write_artifact(queue_path, queue, data_contract="quality/review_queue")
            item_id = queue["data"]["items"][0]["item_id"]
            with patch("src.ui.quality_review.parse_cli_args",
                       return_value=argparse.Namespace(quality_dir=str(quality_dir))):
                at = AppTest.from_file(str(self.APP_PATH), default_timeout=20).run()
                self.assertFalse(at.exception)
                self.assertEqual(at.title[0].value, "Extraction quality review")
                self.assertTrue(any("PDF says Basic" in element.value for element in at.markdown))
                at.text_input(key="reviewer").set_value("Tester").run()
                at.text_area(key=f"notes:{item_id}").set_value("Confirmed against source").run()
                at.button(key=f"save:{item_id}").click().run()
            self.assertFalse(at.exception)
            decisions = load_decisions(queue_path, decisions_path)
            self.assertEqual(decisions["data"]["decisions"][0]["decision"], "issue_found")

    def test_partial_results_render_without_a_review_queue(self):
        with tempfile.TemporaryDirectory() as temporary:
            quality_dir = Path(temporary)
            result = build_success_artifact(
                artifact_type="quality_batch_results", contract_version="1.0.0",
                data={
                    "vertical": "travel_insurance", "schema_version": "1.0.0",
                    "schema_sha256": "a" * 64, "prompt_sha256": "b" * 64,
                    "provider": "openai", "model": "gpt-5.5", "status": "partial",
                    "total_documents": 2, "completed_documents": 0,
                    "failed_documents": 1, "pending_documents": 1,
                    "documents": [
                        {
                            "source_artifact": str(quality_dir / "failed.json"),
                            "source_artifact_sha256": "c" * 64,
                            "source_document": str(quality_dir / "failed.pdf"),
                            "status": "failed", "report_path": None, "report": None,
                            "failure": {"kind": "business_validation", "code": "invalid_field_name",
                                        "attempts": 3, "paths": ["$"]},
                        },
                        {
                            "source_artifact": str(quality_dir / "pending.json"),
                            "source_artifact_sha256": "d" * 64,
                            "source_document": str(quality_dir / "pending.pdf"),
                            "status": "pending", "report_path": None, "report": None,
                            "failure": None,
                        },
                    ],
                },
                data_contract="quality/batch_results",
                provenance={
                    "run_id": "test", "vertical": "travel_insurance", "schema_version": "1.0.0",
                    "provider": "openai", "model": "gpt-5.5", "document_input": "markdown",
                    "source_documents": [], "source_artifacts": [],
                },
            )
            write_artifact(quality_dir / "results.json", result,
                           data_contract="quality/batch_results")
            with patch("src.ui.quality_review.parse_cli_args",
                       return_value=argparse.Namespace(quality_dir=str(quality_dir))):
                at = AppTest.from_file(str(self.APP_PATH), default_timeout=20).run()
            self.assertFalse(at.exception)
            self.assertTrue(any("2" == metric.value for metric in at.metric))
            self.assertTrue(any("invalid_field_name" in element.value for element in at.markdown))
            self.assertTrue(any("not available" in element.value.lower() for element in at.info))


if __name__ == "__main__":
    unittest.main()
