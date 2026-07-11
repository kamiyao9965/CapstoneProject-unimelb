from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.common.model_config import ModelSelection, resolve_selection
from src import run as run_module
from src.run import build_parser


class RunParserTest(unittest.TestCase):
    def test_no_flags_resolve_to_current_openai_pdf_defaults(self) -> None:
        args = build_parser().parse_args([])
        selection = resolve_selection(
            provider=args.provider,
            model=args.model,
            document_input=args.document_input,
            environment={},
        )

        self.assertEqual(selection, ModelSelection("openai", "gpt-5", "pdf"))

    def test_provider_model_and_document_input_flags_are_exposed(self) -> None:
        args = build_parser().parse_args(
            ["--provider", "anthropic", "--model", "claude-test", "--document-input", "markdown"]
        )

        self.assertEqual(args.provider, "anthropic")
        self.assertEqual(args.model, "claude-test")
        self.assertEqual(args.document_input, "markdown")

    def test_main_passes_input_root_to_discovery_as_pdf_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            discovery = mock.Mock()
            discovery.discover.return_value = "fields: []\n"
            argv = [
                "run.py",
                "--samples", "sample.pdf",
                "--input-root", str(Path(tmp) / "PDFs"),
                "--output", str(Path(tmp) / "schema.yaml"),
                "--usage-log", str(Path(tmp) / "usage.jsonl"),
            ]

            with mock.patch.object(run_module, "SchemaDiscovery", return_value=discovery) as factory, \
                 mock.patch("sys.argv", argv):
                exit_code = run_module.main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(factory.call_args.kwargs["pdf_root"], Path(tmp) / "PDFs")


if __name__ == "__main__":
    unittest.main()
