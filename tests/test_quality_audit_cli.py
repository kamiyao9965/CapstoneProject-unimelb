from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from src.run import build_parser, command_quality_audit, configure_command


class QualityAuditCliTests(unittest.TestCase):
    def test_cli_resolves_travel_defaults_and_dispatches_without_live_call(self):
        args = build_parser().parse_args([
            "quality-audit", "--artifact-dir", "extractions/run1",
            "--output-dir", "quality/run1", "--seed", "7",
        ])
        configure_command(args)
        self.assertEqual(args.vertical, "travel_insurance")
        self.assertEqual(args.schema.name, "canonical_schema_v1.json")
        self.assertEqual(args.source_root, args.vertical_manifest.path("input_root"))
        with patch("src.common.model_provider.create_provider", return_value=object()), patch(
            "src.evaluation.quality.run_quality_audit", return_value=Path("quality/run1/review_queue.json")
        ) as runner:
            result = command_quality_audit(args)
        self.assertEqual(result, 0)
        self.assertEqual(runner.call_args.kwargs["seed"], 7)
        self.assertEqual(runner.call_args.kwargs["selection"].document_input, "markdown")


if __name__ == "__main__":
    unittest.main()
