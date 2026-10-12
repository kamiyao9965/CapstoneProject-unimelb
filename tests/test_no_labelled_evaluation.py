from __future__ import annotations

import contextlib
import io
import unittest

from src.run import build_parser
from src.ui.tool.commands import CommandRequest, build_command
from src.verticals.manifest import discover_manifests


class NoLabelledEvaluationTests(unittest.TestCase):
    def test_batch_rejects_removed_evaluate_option(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(["batch", "--schema", "schema.json", "--evaluate"])

    def test_console_does_not_silently_accept_removed_evaluation(self) -> None:
        for vertical in discover_manifests():
            with self.subTest(vertical=vertical), self.assertRaisesRegex(ValueError, "evaluation"):
                build_command(CommandRequest("batch", {
                    "vertical": vertical, "schema": "schema.json", "evaluate": True,
                }))

    def test_manifests_do_not_enable_ground_truth_evaluator(self) -> None:
        for manifest in discover_manifests().values():
            with self.subTest(vertical=manifest.vertical):
                self.assertFalse(manifest.supports("evaluation"))
                self.assertNotEqual(manifest.adapters.get("evaluator"), "private_health_labelled_v1")


if __name__ == "__main__":
    unittest.main()
