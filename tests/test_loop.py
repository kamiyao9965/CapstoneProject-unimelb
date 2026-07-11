from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from src.common.model_config import ModelSelection, resolve_selection
from src.refine.pipeline import cli, rounds
from src.refine.pipeline import steps


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

    def test_provider_flags_resolve_to_the_default_selection(self) -> None:
        args = cli.build_parser().parse_args([])
        selection = resolve_selection(
            provider=args.provider,
            model=args.model,
            document_input=args.document_input,
            environment={},
        )

        self.assertEqual(selection, ModelSelection("openai", "gpt-5", "pdf"))


class PipelineSelectionTest(unittest.TestCase):
    def test_generate_schema_passes_args_selection_to_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = make_args(tmp)
            args.selection = ModelSelection("anthropic", "claude-test", "pdf")
            out_path = Path(tmp) / "schema.yaml"
            discovery = mock.Mock()
            discovery.discover.return_value = "fields: []\n"

            with mock.patch.object(steps, "SchemaDiscovery", return_value=discovery) as factory:
                steps.generate_schema(args, None, out_path, sample_paths=["sample.pdf"])

        self.assertEqual(factory.call_args.kwargs["selection"], args.selection)
        self.assertEqual(factory.call_args.kwargs["pdf_root"], Path(args.input_root))

    def test_run_consensus_stage_passes_pdf_root_to_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = make_args(tmp)
            args.selection = ModelSelection("openai", "gpt-5", "pdf")
            draft_path = Path(tmp) / "draft.yaml"
            draft_path.write_text("fields: []\n", encoding="utf-8")
            discovery = mock.Mock()

            with mock.patch.object(steps, "SchemaDiscovery", return_value=discovery) as factory, \
                 mock.patch.object(steps, "SchemaConsensusRefinement") as refinement:
                steps.run_consensus_stage(args, draft_path, Path(tmp) / "round_1")

        self.assertEqual(factory.call_args.kwargs["pdf_root"], Path(args.input_root))
        refinement.assert_called_once()

    def test_evaluate_schema_passes_pdf_root_to_extractor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = make_args(tmp)
            args.selection = ModelSelection("openai", "gpt-5", "pdf")
            round_dir = Path(tmp) / "round_1"
            round_dir.mkdir()
            extractor = mock.Mock()
            extractor.extract_many.return_value = []

            with mock.patch.object(steps, "select_samples", return_value=["pdfs/eval.pdf"]), \
                 mock.patch.object(steps, "SchemaExtractor", return_value=extractor) as factory, \
                 mock.patch.object(steps, "load_field_specs", return_value={}), \
                 mock.patch.object(steps, "load_records", return_value=[]), \
                 mock.patch.object(steps, "analyze", return_value=None), \
                 mock.patch.object(steps, "print_report"), \
                 mock.patch.object(steps, "build_feedback", return_value="feedback"):
                steps.evaluate_schema(args, "fields: []", round_dir)

        self.assertEqual(factory.call_args.kwargs["pdf_root"], Path(args.input_root))


class RunRoundModeTest(unittest.TestCase):
    """Drive run_round with the API-facing steps mocked out."""

    def run_round(self, tmp: str, consensus_outputs=None, **arg_overrides):
        args = make_args(tmp, **arg_overrides)
        round_dir = Path(tmp) / "round_1"

        def fake_generate(args_, feedback, out_path, sample_paths=None):
            self.assertEqual(tuple(sample_paths or ()), ("pdfs/discovery.pdf",))
            out_path.write_text("fields: []\n", encoding="utf-8")
            return "fields: []\n"

        def fake_consensus(args_, draft_path, rd, base_sample_paths=()):
            consensus_dir = rd / "consensus"
            consensus_dir.mkdir(parents=True, exist_ok=True)
            schema_path = consensus_dir / "consensus_schema.yaml"
            schema_path.write_text("fields: [{name: excess}]\n", encoding="utf-8")
            queue_path = consensus_dir / "review_queue.yaml"
            queue_path.write_text("updates: []\n", encoding="utf-8")
            return SimpleNamespace(
                consensus_schema_path=schema_path,
                queue_path=queue_path,
                schema_build_samples=(
                    *base_sample_paths,
                    "pdfs/consensus.pdf",
                ),
            )

        evaluate = mock.Mock(return_value=(None, "feedback text"))
        with mock.patch.object(
                rounds,
                "select_discovery_samples",
                return_value=("pdfs/discovery.pdf",),
             ), \
             mock.patch.object(rounds, "generate_schema", side_effect=fake_generate), \
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
            self.assertEqual(
                evaluate.call_args.kwargs["exclude_paths"],
                ("pdfs/discovery.pdf",),
            )
            self.assertTrue((round_dir / "schema.yaml").exists())
            self.assertFalse((round_dir / "schema_draft.yaml").exists())

    def test_unattended_consensus_evaluates_auto_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result, round_dir, evaluate, _ = self.run_round(tmp, consensus_runs=3)
            self.assertEqual(result, "feedback text")
            evaluate.assert_called_once()
            self.assertEqual(
                evaluate.call_args.kwargs["exclude_paths"],
                ("pdfs/discovery.pdf", "pdfs/consensus.pdf"),
            )
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

    def test_missing_sample_metadata_fails_without_evaluating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            round_dir = Path(tmp) / "round_1"
            consensus_dir = round_dir / "consensus"
            consensus_dir.mkdir(parents=True)
            (consensus_dir / "reviewed_schema.yaml").write_text(
                "fields: [{name: excess}]\n", encoding="utf-8"
            )
            (consensus_dir / "review_queue.yaml").write_text(
                "metadata: {}\nupdates: []\n", encoding="utf-8"
            )
            args = make_args(tmp, resume_review=str(round_dir))
            evaluate = mock.Mock(return_value=(None, "feedback"))

            with mock.patch.object(rounds, "evaluate_schema", evaluate):
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
            (consensus_dir / "review_queue.yaml").write_text(
                "metadata:\n"
                "  schema_build_samples:\n"
                "    - pdfs/discovery.pdf\n"
                "    - pdfs/consensus.pdf\n"
                "updates: []\n",
                encoding="utf-8",
            )
            args = make_args(tmp, resume_review=str(round_dir))
            evaluate = mock.Mock(return_value=(None, "feedback"))
            with mock.patch.object(rounds, "evaluate_schema", evaluate):
                exit_code = rounds.resume_review(args)
            self.assertEqual(exit_code, 0)
            evaluate.assert_called_once()
            self.assertEqual(
                evaluate.call_args.kwargs["exclude_paths"],
                ("pdfs/discovery.pdf", "pdfs/consensus.pdf"),
            )
            self.assertIn("excess", (round_dir / "schema.yaml").read_text())


if __name__ == "__main__":
    unittest.main()
