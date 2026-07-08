from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from src.refine.pipeline import cli, rounds


def make_args(tmp: str, **overrides) -> SimpleNamespace:
    values = {
        "input_root": "data/private_health/raw/PDFs",
        "per_category": 5,
        "seed": 42,
        "eval_per_category": 2,
        "eval_seed": 7,
        "model": "gpt-5",
        "timeout": 600.0,
        "out_dir": tmp,
        "rounds": 1,
        "consensus_runs": 1,
        "review_ui": False,
        "autonomous": False,
        "resume_feedback": None,
        "resume_review": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ParserBackwardCompatTest(unittest.TestCase):
    def test_defaults_keep_current_behavior(self) -> None:
        args = cli.build_parser().parse_args([])
        self.assertEqual(args.consensus_runs, 1)
        self.assertFalse(args.review_ui)
        self.assertIsNone(args.resume_review)
        self.assertFalse(args.autonomous)
        self.assertEqual(args.rounds, 1)


class RunRoundModeTest(unittest.TestCase):
    """Drive run_round with the API-facing steps mocked out."""

    def run_round(self, tmp: str, consensus_outputs=None, **arg_overrides):
        args = make_args(tmp, **arg_overrides)
        round_dir = Path(tmp) / "round_1"

        def fake_generate(args_, feedback, out_path):
            out_path.write_text("fields: []\n", encoding="utf-8")
            return "fields: []\n"

        def fake_consensus(args_, draft_path, rd):
            consensus_dir = rd / "consensus"
            consensus_dir.mkdir(parents=True, exist_ok=True)
            schema_path = consensus_dir / "consensus_schema.yaml"
            schema_path.write_text("fields: [{name: excess}]\n", encoding="utf-8")
            queue_path = consensus_dir / "review_queue.yaml"
            queue_path.write_text("updates: []\n", encoding="utf-8")
            return SimpleNamespace(
                consensus_schema_path=schema_path, queue_path=queue_path
            )

        evaluate = mock.Mock(return_value=(None, "feedback text"))
        with mock.patch.object(rounds, "generate_schema", side_effect=fake_generate), \
             mock.patch.object(rounds, "run_consensus_stage", side_effect=fake_consensus) as consensus_mock, \
             mock.patch.object(rounds, "evaluate_schema", evaluate):
            result = rounds.run_round(args, 1, None)
        return result, round_dir, evaluate, consensus_mock

    def test_default_round_skips_consensus_and_evaluates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result, round_dir, evaluate, consensus_mock = self.run_round(tmp)
            self.assertEqual(result, "feedback text")
            consensus_mock.assert_not_called()
            evaluate.assert_called_once()
            self.assertTrue((round_dir / "schema.yaml").exists())
            self.assertFalse((round_dir / "schema_draft.yaml").exists())

    def test_unattended_consensus_evaluates_auto_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result, round_dir, evaluate, _ = self.run_round(tmp, consensus_runs=3)
            self.assertEqual(result, "feedback text")
            evaluate.assert_called_once()
            # schema.yaml is the consensus auto-merge output, draft kept aside.
            self.assertIn("excess", (round_dir / "schema.yaml").read_text())
            self.assertTrue((round_dir / "schema_draft.yaml").exists())

    def test_attended_consensus_stops_before_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result, round_dir, evaluate, _ = self.run_round(
                tmp, consensus_runs=3, review_ui=True
            )
            self.assertIsNone(result)
            evaluate.assert_not_called()
            # schema.yaml must not exist yet - it is written on resume-review.
            self.assertFalse((round_dir / "schema.yaml").exists())


class ResumeReviewTest(unittest.TestCase):
    def test_missing_reviewed_schema_fails_without_evaluating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            round_dir = Path(tmp) / "round_1"
            (round_dir / "consensus").mkdir(parents=True)
            args = make_args(tmp, resume_review=str(round_dir))
            with mock.patch.object(rounds, "evaluate_schema") as evaluate:
                exit_code = rounds.resume_review(args)
            self.assertEqual(exit_code, 1)
            evaluate.assert_not_called()

    def test_reviewed_schema_is_evaluated_into_round_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            round_dir = Path(tmp) / "round_1"
            consensus_dir = round_dir / "consensus"
            consensus_dir.mkdir(parents=True)
            (consensus_dir / "reviewed_schema.yaml").write_text(
                "fields: [{name: excess}]\n", encoding="utf-8"
            )
            args = make_args(tmp, resume_review=str(round_dir))
            evaluate = mock.Mock(return_value=(None, "feedback"))
            with mock.patch.object(rounds, "evaluate_schema", evaluate):
                exit_code = rounds.resume_review(args)
            self.assertEqual(exit_code, 0)
            evaluate.assert_called_once()
            self.assertIn("excess", (round_dir / "schema.yaml").read_text())


if __name__ == "__main__":
    unittest.main()
