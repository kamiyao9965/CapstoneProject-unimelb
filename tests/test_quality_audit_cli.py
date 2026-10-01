from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from src.evaluation.quality import QualityAuditRun
from src.run import build_parser, command_quality_audit, configure_command


class QualityAuditCliTests(unittest.TestCase):
    def test_cli_resolves_travel_defaults_and_dispatches_without_live_call(self):
        args = build_parser().parse_args([
            "quality-audit", "--artifact-dir", "extractions/run1",
            "--output-dir", "quality/run1", "--seed", "7",
            "--max-extraction-chars", "160000",
        ])
        configure_command(args)
        self.assertEqual(args.vertical, "travel_insurance")
        self.assertEqual(args.schema.name, "canonical_schema_v1.json")
        self.assertEqual(args.source_root, args.vertical_manifest.path("input_root"))
        with patch("src.common.model_provider.create_provider", return_value=object()), patch(
            "src.evaluation.quality.run_quality_audit",
            return_value=QualityAuditRun(Path("quality/run1/results.json"),
                                          Path("quality/run1/review_queue.json"), 1, 0, 0),
        ) as runner:
            result = command_quality_audit(args)
        self.assertEqual(result, 0)
        self.assertEqual(runner.call_args.kwargs["seed"], 7)
        self.assertEqual(runner.call_args.kwargs["max_extraction_chars"], 160000)
        self.assertEqual(runner.call_args.kwargs["selection"].document_input, "markdown")

    def test_summary_only_does_not_construct_a_model_provider(self):
        args = build_parser().parse_args([
            "quality-audit", "--artifact-dir", "extractions/run1",
            "--output-dir", "quality/run1", "--summary-only",
        ])
        configure_command(args)
        with patch("src.common.model_provider.create_provider") as provider, patch(
            "src.evaluation.quality.run_quality_audit",
            return_value=QualityAuditRun(Path("quality/run1/results.json"), None, 4, 0, 15),
        ) as runner:
            self.assertEqual(command_quality_audit(args), 0)
        provider.assert_not_called()
        self.assertTrue(runner.call_args.kwargs["summary_only"])
        self.assertIsNone(runner.call_args.kwargs["provider"])

    def test_partial_live_batch_has_nonzero_exit_code(self):
        args = build_parser().parse_args([
            "quality-audit", "--artifact-dir", "extractions/run1",
            "--output-dir", "quality/run1", "--resume", "--max-failures", "2",
        ])
        configure_command(args)
        with patch("src.common.model_provider.create_provider", return_value=object()), patch(
            "src.evaluation.quality.run_quality_audit",
            return_value=QualityAuditRun(Path("quality/run1/results.json"), None, 4, 1, 14),
        ) as runner:
            self.assertEqual(command_quality_audit(args), 1)
        self.assertTrue(runner.call_args.kwargs["resume"])
        self.assertEqual(runner.call_args.kwargs["max_failures"], 2)


if __name__ == "__main__":
    unittest.main()
