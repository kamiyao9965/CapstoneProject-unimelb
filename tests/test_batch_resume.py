from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from src import run as cli
from src.common.json_artifacts import build_success_artifact, write_artifact
from src.common.models import ExtractionResult
from src.schema.contract import compile_extraction_contract
from src.schema.validation import normalize_schema
from src.verticals.manifest import PROJECT_ROOT
from tests.test_extractor import VALID_RECORD
from tests.test_json_contracts import VALID_DISCOVERED_SCHEMA


class BatchResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir=PROJECT_ROOT)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.input_root = self.root / "input"
        self.input_root.mkdir()
        self.pdf = self.input_root / "cached.pdf"
        self.pdf.write_bytes(b"offline fixture")
        self.output_dir = self.root / "results"
        self.output_dir.mkdir()
        self.cached = self.output_dir / "cached.json"
        self.args = cli.build_parser().parse_args([
            "batch", "--schema", "schema.json", "--input-root", str(self.input_root),
            "--output-dir", str(self.output_dir), "--provider", "openai", "--model", "gpt-5",
        ])
        manifest = cli.configure_command(self.args)
        self.args.vertical_manifest = replace(manifest, paths={
            **manifest.paths, "output_root": str(self.root / "outputs"),
        })
        self.schema = normalize_schema(VALID_DISCOVERED_SCHEMA, manifest)

    def result(self, **changes) -> ExtractionResult:
        return ExtractionResult(**{
            "vertical": "private_health", "schema_version": str(self.schema["version"]),
            "source_path": str(self.pdf), "provider": "openai", "model": "gpt-5",
            "document_parser": "pdfingestor", "data": copy.deepcopy(VALID_RECORD),
            **changes,
        })

    def test_invalid_cached_result_stops_before_provider_and_preserves_input(self) -> None:
        cases = {
            "vertical": {"vertical": "travel_insurance"},
            "version": {"schema_version": "other-version"},
            "source": {"source_path": str(self.input_root / "other.pdf")},
            "provider": {"provider": "anthropic"},
            "model": {"model": "other-model"},
            "parser": {"document_parser": "mineru"},
            "missing_parser": {"document_parser": None},
            "invalid_data": {"data": {"product_type": "hospital"}},
            "broken_json": None,
            "directory": None,
        }
        (self.input_root / "pending.pdf").write_bytes(b"offline fixture")
        for name, changes in cases.items():
            with self.subTest(name=name):
                if self.cached.is_dir():
                    self.cached.rmdir()
                elif self.cached.exists():
                    self.cached.unlink()
                if name == "directory":
                    self.cached.mkdir()
                    before = None
                else:
                    if name == "broken_json":
                        self.cached.write_text("{broken JSON", encoding="utf-8")
                    else:
                        self.result(**changes).write_json(self.cached)
                    before = self.cached.read_bytes()
                with mock.patch.object(cli, "load_schema_data", return_value=self.schema), \
                     mock.patch.object(cli, "_build_schema_extractor") as builder, \
                     mock.patch("builtins.print"):
                    self.assertEqual(cli.command_batch(self.args), 1)
                builder.assert_not_called()
                if before is None:
                    self.assertTrue(self.cached.is_dir())
                    self.cached.rmdir()
                else:
                    self.assertEqual(self.cached.read_bytes(), before)
                    self.cached.unlink()

    def test_valid_native_and_enveloped_results_resume_without_provider(self) -> None:
        for format_name in ("native", "envelope"):
            with self.subTest(format_name=format_name):
                if format_name == "native":
                    self.result().write_json(self.cached)
                else:
                    artifact = build_success_artifact(
                        artifact_type="extraction_result", contract_version="1.0.0",
                        data=VALID_RECORD,
                        data_contract_schema=compile_extraction_contract(self.schema),
                        provenance={
                            "run_id": "fixture", "vertical": "private_health",
                            "schema_version": str(self.schema["version"]),
                            "provider": "openai", "model": "gpt-5", "document_input": "markdown",
                            "document_parser": "pdfingestor", "source_documents": [str(self.pdf)],
                            "source_artifacts": [],
                        },
                    )
                    write_artifact(self.cached, artifact,
                                   data_contract_schema=compile_extraction_contract(self.schema))
                before = self.cached.read_bytes()
                with mock.patch.object(cli, "load_schema_data", return_value=self.schema), \
                     mock.patch.object(cli, "_build_schema_extractor") as builder, \
                     mock.patch("builtins.print"):
                    self.assertEqual(cli.command_batch(self.args), 0)
                builder.assert_not_called()
                self.assertEqual(self.cached.read_bytes(), before)
                self.cached.unlink()

    def test_cached_canonical_results_keep_contract_and_identity_validation(self) -> None:
        from tests.test_canonical_schema import approved_travel_schema
        from tests.test_canonical_storage import valid_extraction_payload

        self.args = cli.build_parser().parse_args([
            "batch", "--vertical", "travel_insurance", "--schema", "schema.json",
            "--input-root", str(self.input_root), "--output-dir", str(self.output_dir),
            "--provider", "openai", "--model", "gpt-5",
        ])
        cli.configure_command(self.args)
        self.schema = approved_travel_schema()
        for case in ("valid", "invalid_type", "duplicate_identity"):
            with self.subTest(case=case):
                data = valid_extraction_payload()
                if case == "invalid_type":
                    data["products"][0]["product_name"] = 123
                elif case == "duplicate_identity":
                    data["products"].append(copy.deepcopy(data["products"][0]))
                self.result(vertical="travel_insurance", data=data).write_json(self.cached)
                before = self.cached.read_bytes()
                with mock.patch.object(cli, "load_schema_data", return_value=self.schema), \
                     mock.patch.object(cli, "_build_schema_extractor") as builder, \
                     mock.patch("builtins.print"):
                    self.assertEqual(cli.command_batch(self.args), 0 if case == "valid" else 1)
                builder.assert_not_called()
                self.assertEqual(self.cached.read_bytes(), before)
                self.cached.unlink()

    def test_resume_evaluation_includes_cached_results_even_when_all_are_cached(self) -> None:
        from src.evaluation.metrics import ExtractionEvaluator
        from src.evaluation.reporter import EvaluationReporter

        pending = self.input_root / "pending.pdf"
        pending.write_bytes(b"offline fixture")
        self.result().write_json(self.cached)
        before = self.cached.read_bytes()
        self.args.evaluate = True
        labels = mock.Mock()
        labels.load_ground_truth.return_value = (None, {"hospital": {"product_name": "Example"}})
        extractor = mock.Mock()
        extractor.extract_one.return_value = VALID_RECORD
        for attempt in ("partial", "all_cached"):
            with self.subTest(attempt=attempt), \
                 mock.patch.object(cli, "load_schema_data", return_value=self.schema), \
                 mock.patch.object(cli, "_build_schema_extractor", return_value=extractor) as builder, \
                 mock.patch("src.verticals.registry.get_evaluation_tools", return_value=(
                     labels, ExtractionEvaluator(), EvaluationReporter(),
                 )), mock.patch("builtins.print"):
                self.assertEqual(cli.command_batch(self.args), 0)
                if attempt == "partial":
                    builder.assert_called_once()
                    extractor.extract_one.assert_called_once_with(pending)
                else:
                    builder.assert_not_called()
                report = json.loads((self.root / "outputs/evaluation/report.json").read_text())
                self.assertEqual(report["summary"]["total_documents"], 2)
                self.assertEqual(report["summary"]["matched_documents"], 2)
                self.assertEqual({item["source_path"] for item in report["reports"]},
                                 {str(self.pdf), str(pending)})
                self.assertEqual(self.cached.read_bytes(), before)

    def test_cached_documents_remain_in_evaluation_when_new_extraction_fails(self) -> None:
        from src.evaluation.metrics import ExtractionEvaluator
        from src.evaluation.reporter import EvaluationReporter

        (self.input_root / "pending.pdf").write_bytes(b"offline fixture")
        self.result().write_json(self.cached)
        self.args.evaluate = True
        labels = mock.Mock()
        labels.load_ground_truth.return_value = (None, {"hospital": {"product_name": "Example"}})
        extractor = mock.Mock()
        extractor.extract_one.side_effect = ValueError("invalid output")
        with mock.patch.object(cli, "load_schema_data", return_value=self.schema), \
             mock.patch.object(cli, "_build_schema_extractor", return_value=extractor), \
             mock.patch("src.verticals.registry.get_evaluation_tools", return_value=(
                 labels, ExtractionEvaluator(), EvaluationReporter(),
             )), mock.patch("builtins.print"):
            self.assertEqual(cli.command_batch(self.args), 1)
        report = json.loads((self.root / "outputs/evaluation/report.json").read_text())
        self.assertEqual(report["summary"]["total_documents"], 2)
        self.assertEqual(report["summary"]["matched_documents"], 1)
        self.assertEqual(report["summary"]["extraction_errors"], 1)
