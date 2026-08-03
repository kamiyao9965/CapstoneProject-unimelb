from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.common.model_config import ModelSelection, resolve_selection
from src.refine import consensus as consensus_module
from src.refine.consensus import build_parser as build_consensus_parser
from src.stability import measure as measure_module
from src.stability.measure import build_parser as build_stability_parser


class StandaloneSelectionParserTest(unittest.TestCase):
    def test_no_flags_resolve_to_current_defaults_for_both_commands(self) -> None:
        for parser_builder in (build_consensus_parser, build_stability_parser):
            args = parser_builder().parse_args([])
            selection = resolve_selection(
                provider=args.provider,
                model=args.model,
                document_input=args.document_input,
                environment={},
            )

            self.assertEqual(selection, ModelSelection("openai", "gpt-5", "markdown"))

    def test_both_commands_accept_explicit_selection_flags(self) -> None:
        for parser_builder in (build_consensus_parser, build_stability_parser):
            args = parser_builder().parse_args(
                ["--provider", "deepseek", "--model", "deepseek-test", "--document-input", "markdown"]
            )

            self.assertEqual(args.provider, "deepseek")
            self.assertEqual(args.model, "deepseek-test")
            self.assertEqual(args.document_input, "markdown")

    def test_consensus_main_passes_input_root_to_discovery_as_pdf_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            argv = [
                "consensus.py",
                "--input-root", str(Path(tmp) / "PDFs"),
                "--out-dir", str(Path(tmp) / "out"),
            ]
            refinement = mock.Mock()
            refinement.refine.return_value = mock.Mock(
                patch_dir=Path(tmp), consensus_schema_path=Path(tmp),
                stability_path=Path(tmp), queue_path=Path(tmp),
            )

            with mock.patch.object(consensus_module, "SchemaDiscovery") as factory, \
                 mock.patch.object(
                     consensus_module, "SchemaConsensusRefinement", return_value=refinement
                 ), \
                 mock.patch("sys.argv", argv):
                exit_code = consensus_module.main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(factory.call_args.kwargs["pdf_root"], str(Path(tmp) / "PDFs"))

    def test_stability_main_passes_input_root_to_discovery_as_pdf_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            argv = [
                "measure.py",
                "--runs", "2",
                "--input-root", str(Path(tmp) / "PDFs"),
                "--out-dir", str(Path(tmp) / "out"),
            ]
            discovery = mock.Mock()
            discovery.discover.return_value = {
                "vertical": "private_health", "version": "0.1-draft",
                "description": "Schema", "product_types": ["hospital"],
                "fields": [{"name": "product_name", "type": "string",
                            "description": "Name", "applies_to": ["hospital"],
                            "required": True, "values": [], "aliases": []}],
                "hospital_categories": [], "extras_services": [], "notes": [],
            }

            with mock.patch.object(
                     measure_module, "SchemaDiscovery", return_value=discovery
                 ) as factory, \
                 mock.patch.object(measure_module, "select_samples", return_value=["a.pdf"]), \
                 mock.patch.object(measure_module, "signature_from_file", return_value={}), \
                 mock.patch.object(measure_module, "compare"), \
                 mock.patch("sys.argv", argv):
                exit_code = measure_module.main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(factory.call_args.kwargs["pdf_root"], str(Path(tmp) / "PDFs"))
        run_ids = [call.kwargs["run_id"] for call in discovery.discover.call_args_list]
        self.assertEqual(len(run_ids), 2)
        self.assertEqual(len(set(run_ids)), 2)


if __name__ == "__main__":
    unittest.main()
