from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from src.common.json_artifacts import (
    build_success_artifact,
    read_artifact,
    write_artifact,
)
from src.common.data_paths import default_private_health_pdf_root
from src.common.model_config import ModelSelection, resolve_selection
from src.refine.human_review import write_review_queue
from src.refine.pipeline import cli, rounds
from src.refine.pipeline import steps
from src.verticals.manifest import PROJECT_ROOT
from tests.test_json_contracts import VALID_DISCOVERED_SCHEMA
from src.schema.validation import normalize_schema


PROVENANCE = {
    "run_id": "test", "provider": "openai", "model": "gpt-5",
    "document_input": "pdf", "source_documents": [], "source_artifacts": [],
}


def write_schema(path: Path, data: dict | None = None) -> None:
    artifact = build_success_artifact(
        artifact_type="discovered_schema", contract_version="1.0.0",
        data=normalize_schema(data or VALID_DISCOVERED_SCHEMA), provenance=PROVENANCE,
        data_contract="private_health/discovered_schema",
    )
    write_artifact(path, artifact, data_contract="private_health/discovered_schema")


def review_queue_metadata(samples: list[str] | None = None) -> dict[str, object]:
    return {
        "generated_at": "2026-07-14T00:00:00+00:00",
        "consensus_source": "candidate_schema_patches",
        "total_runs": 1,
        "base_schema_path": "schema.json",
        "schema_build_samples": samples or [],
    }


def write_reviewed_fixture(consensus_dir: Path, schema=None, samples=()):
    from src.refine.human_review import build_review_queue, write_review_queue, empty_decisions, write_review_decisions, apply_review_files
    base = schema or VALID_DISCOVERED_SCHEMA
    base_path = consensus_dir / "base.json"
    write_schema(base_path, base)
    queue = build_review_queue([], base, 1, base_path, schema_build_samples=samples)
    write_review_queue(queue, consensus_dir / "review_queue.json")
    write_review_decisions(empty_decisions(queue=queue), consensus_dir / "review_decisions.json")
    apply_review_files(consensus_dir)


def make_args(tmp: str, **overrides) -> SimpleNamespace:
    values = {
        "input_root": str(default_private_health_pdf_root()),
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
        self.assertIsNone(args.consensus_runs)
        self.assertFalse(args.review_ui)
        self.assertIsNone(args.resume_review)
        self.assertFalse(args.autonomous)
        self.assertEqual(args.rounds, 1)

    def test_manifest_applies_refinement_paths_and_categories(self) -> None:
        args = cli.build_parser().parse_args([])
        cli.configure_args(args)

        self.assertEqual(args.consensus_runs, 1)

        self.assertEqual(
            args.input_root,
            PROJECT_ROOT / "konkrd-data/data/private_health/raw/PDFs",
        )
        self.assertEqual(args.out_dir, PROJECT_ROOT / "outputs/private_health/refine")
        self.assertEqual(
            args.vertical_manifest.documents.categories,
            ("combined", "extras", "generalhealth", "hospital"),
        )

    def test_travel_manifest_defaults_to_five_consensus_runs(self) -> None:
        args = cli.build_parser().parse_args(
            ["--manifest", "configs/travel_insurance/manifest.json"]
        )

        cli.configure_args(args)

        self.assertEqual(args.consensus_runs, 5)

    def test_provider_flags_resolve_to_the_default_selection(self) -> None:
        args = cli.build_parser().parse_args([])
        selection = resolve_selection(
            provider=args.provider,
            model=args.model,
            document_input=args.document_input,
            environment={},
        )

        self.assertEqual(selection, ModelSelection("openai", "gpt-5", "markdown"))


class PipelineSelectionTest(unittest.TestCase):
    def test_generate_schema_passes_args_selection_to_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = make_args(tmp)
            args.selection = ModelSelection("anthropic", "claude-test", "pdf")
            out_path = Path(tmp) / "schema.json"
            discovery = mock.Mock()
            discovery.discover.return_value = normalize_schema(VALID_DISCOVERED_SCHEMA)

            with mock.patch.object(steps, "SchemaDiscovery", return_value=discovery) as factory:
                steps.generate_schema(args, None, out_path, sample_paths=["sample.pdf"])

        self.assertEqual(factory.call_args.kwargs["selection"], args.selection)
        self.assertEqual(factory.call_args.kwargs["pdf_root"], Path(args.input_root))
        self.assertTrue(discovery.discover.call_args.kwargs["run_id"])

    def test_run_consensus_stage_passes_pdf_root_to_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = make_args(tmp)
            args.selection = ModelSelection("openai", "gpt-5", "pdf")
            draft_path = Path(tmp) / "draft.json"
            write_schema(draft_path)
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
                 mock.patch.object(steps, "load_records", return_value=([], 0)), \
                 mock.patch.object(steps, "analyze", return_value=None), \
                 mock.patch.object(steps, "print_report"), \
                 mock.patch.object(steps, "build_feedback", return_value="feedback"), \
                 mock.patch.object(steps, "build_feedback_data", return_value={
                     "instructions": ["feedback"],
                     "analysis": {"documents": 0, "error_docs": 0,
                         "source_category_counts": {},
                         "product_type_correct": 0,
                         "product_type_unclassified": 0,
                         "product_type_accuracy": 0.0,
                         "product_type_mismatches": {},
                         "fill_rate": {},
                         "evaluated_documents": {}, "weak_fields": [],
                         "missing_required": {}, "enum_violations": {},
                         "model_unfilled": {}},
                 }):
                steps.evaluate_schema(args, VALID_DISCOVERED_SCHEMA, round_dir)

        self.assertEqual(factory.call_args.kwargs["pdf_root"], Path(args.input_root))


class RunRoundModeTest(unittest.TestCase):
    """Drive run_round with the API-facing steps mocked out."""

    def run_round(self, tmp: str, consensus_outputs=None, **arg_overrides):
        args = make_args(tmp, **arg_overrides)
        round_dir = Path(tmp) / "round_1"

        def fake_generate(args_, feedback, out_path, sample_paths=None):
            self.assertEqual(tuple(sample_paths or ()), ("pdfs/discovery.pdf",))
            write_schema(out_path)
            return VALID_DISCOVERED_SCHEMA

        def fake_consensus(args_, draft_path, rd, base_sample_paths=()):
            consensus_dir = rd / "consensus"
            consensus_dir.mkdir(parents=True, exist_ok=True)
            schema_path = consensus_dir / "consensus_schema.json"
            schema = dict(VALID_DISCOVERED_SCHEMA)
            schema["fields"] = [*VALID_DISCOVERED_SCHEMA["fields"], {
                "name": "excess", "type": "number", "description": "Excess",
                "applies_to": ["hospital"], "required": False, "values": [],
                "aliases": [],
            }]
            write_schema(schema_path, schema)
            queue_path = consensus_dir / "review_queue.json"
            write_review_queue(
                {"metadata": review_queue_metadata(), "updates": []},
                queue_path,
            )
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
            self.assertTrue((round_dir / "schema.json").exists())
            self.assertFalse((round_dir / "schema_draft.json").exists())
            self.assertTrue((Path(tmp) / "final_schema.json").exists())

    def test_unattended_consensus_evaluates_auto_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result, round_dir, evaluate, _ = self.run_round(tmp, consensus_runs=3)
            self.assertEqual(result, "feedback text")
            evaluate.assert_called_once()
            self.assertEqual(
                evaluate.call_args.kwargs["exclude_paths"],
                ("pdfs/discovery.pdf", "pdfs/consensus.pdf"),
            )
            artifact = read_artifact(
                round_dir / "schema.json", expected_type="discovered_schema",
                data_contract="private_health/discovered_schema",
            )
            self.assertIn("excess", [f["name"] for f in artifact["data"]["fields"]])
            final_artifact = read_artifact(
                Path(tmp) / "final_schema.json",
                expected_type="discovered_schema",
                data_contract="private_health/discovered_schema",
            )
            self.assertIn("excess", [f["name"] for f in final_artifact["data"]["fields"]])
            self.assertTrue((round_dir / "schema_draft.json").exists())

    def test_attended_consensus_pauses_before_holdout_schema_application(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result, round_dir, evaluate, _ = self.run_round(
                tmp, consensus_runs=3, review_ui=True
            )
            self.assertIsNone(result)
            evaluate.assert_not_called()
            self.assertTrue((round_dir / "schema.json").exists())
            self.assertFalse((Path(tmp) / "final_schema.json").exists())


class ResumeReviewTest(unittest.TestCase):
    def test_resume_preserves_existing_round_and_final_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            round_dir = root / "round_1"
            consensus = round_dir / "consensus"
            consensus.mkdir(parents=True)
            write_reviewed_fixture(consensus)
            write_schema(round_dir / "schema.json")
            write_schema(root / "final_schema.json")
            before = (round_dir / "schema.json").read_bytes()
            args = make_args(tmp, resume_review=str(round_dir))
            with mock.patch.object(rounds, "evaluate_schema", return_value=(None, "feedback")):
                self.assertEqual(rounds.resume_review(args), 0)
            self.assertEqual((round_dir / "schema.json").read_bytes(), before)
            self.assertTrue((round_dir / "schema_1.json").exists())
            self.assertTrue((root / "final_schema_1.json").exists())

    def test_resume_rejects_other_vertical_before_evaluation(self) -> None:
        from src.verticals.manifest import default_manifest_path, load_vertical_manifest
        with tempfile.TemporaryDirectory() as tmp:
            round_dir = Path(tmp) / "round_1"
            consensus = round_dir / "consensus"
            consensus.mkdir(parents=True)
            write_reviewed_fixture(consensus)
            args = make_args(tmp, resume_review=str(round_dir))
            args.vertical_manifest = load_vertical_manifest(default_manifest_path("travel_insurance"))
            with mock.patch.object(rounds, "evaluate_schema") as evaluate:
                with self.assertRaisesRegex(ValueError, "vertical"):
                    rounds.resume_review(args)
                evaluate.assert_not_called()

    def test_missing_reviewed_schema_fails_without_evaluating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            round_dir = Path(tmp) / "round_1"
            (round_dir / "consensus").mkdir(parents=True)
            args = make_args(tmp, resume_review=str(round_dir))
            with mock.patch.object(rounds, "evaluate_schema") as evaluate:
                exit_code = rounds.resume_review(args)
            self.assertEqual(exit_code, 1)
            evaluate.assert_not_called()

    def test_reviewed_schema_runs_holdout_schema_application_before_publish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            round_dir = Path(tmp) / "round_1"
            consensus_dir = round_dir / "consensus"
            consensus_dir.mkdir(parents=True)
            write_reviewed_fixture(consensus_dir)
            args = make_args(tmp, resume_review=str(round_dir))
            evaluate = mock.Mock(return_value=(None, "feedback"))

            with mock.patch.object(rounds, "evaluate_schema", evaluate):
                exit_code = rounds.resume_review(args)

            self.assertEqual(exit_code, 0)
            evaluate.assert_called_once()
            self.assertEqual(evaluate.call_args.args[1], normalize_schema(VALID_DISCOVERED_SCHEMA))
            self.assertEqual(evaluate.call_args.kwargs["exclude_paths"], tuple())
            self.assertTrue((round_dir / "schema.json").exists())
            self.assertTrue((Path(tmp) / "final_schema.json").exists())

    def test_reviewed_schema_is_published_into_round_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            round_dir = Path(tmp) / "round_1"
            consensus_dir = round_dir / "consensus"
            consensus_dir.mkdir(parents=True)
            schema = dict(VALID_DISCOVERED_SCHEMA)
            schema["fields"] = [*VALID_DISCOVERED_SCHEMA["fields"], {
                "name": "excess", "type": "number", "description": "Excess",
                "applies_to": ["hospital"], "required": False, "values": [],
                "aliases": [],
            }]
            write_reviewed_fixture(consensus_dir, schema, ["pdfs/discovery.pdf", "pdfs/consensus.pdf"])
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
            artifact = read_artifact(
                round_dir / "schema.json", expected_type="discovered_schema",
                data_contract="private_health/discovered_schema",
            )
            self.assertIn("excess", [f["name"] for f in artifact["data"]["fields"]])
            final_artifact = read_artifact(
                Path(tmp) / "final_schema.json",
                expected_type="discovered_schema",
                data_contract="private_health/discovered_schema",
            )
            self.assertIn("excess", [f["name"] for f in final_artifact["data"]["fields"]])


if __name__ == "__main__":
    unittest.main()
