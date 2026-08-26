from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest import mock

from src.common.model_config import ModelSelection, resolve_selection
from src import run as run_module
from src.run import build_parser


class RunParserTest(unittest.TestCase):
    def test_no_flags_resolve_to_current_openai_pdf_defaults(self) -> None:
        args = build_parser().parse_args(["discover"])
        selection = resolve_selection(
            provider=args.provider,
            model=args.model,
            document_input=args.document_input,
            environment={},
        )

        self.assertEqual(selection, ModelSelection("openai", "gpt-5", "markdown"))

    def test_provider_model_and_document_input_flags_are_exposed(self) -> None:
        args = build_parser().parse_args(
            ["discover", "--provider", "anthropic", "--model", "claude-test", "--document-input", "markdown"]
        )

        self.assertEqual(args.provider, "anthropic")
        self.assertEqual(args.model, "claude-test")
        self.assertEqual(args.document_input, "markdown")

    def test_main_passes_input_root_to_discovery_as_pdf_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            discovery = mock.Mock()
            discovery.discover.return_value = {
                "vertical": "private_health", "version": "0.1-draft",
                "description": "Schema", "product_types": ["hospital"],
                "fields": [{"name": "product_name", "type": "string",
                            "description": "Name", "applies_to": ["hospital"],
                            "required": True, "values": [], "aliases": [],
                            "enum_ref": None, "item_fields": [], "unique_items": False}],
                "hospital_categories": [], "extras_services": [], "notes": [],
            }
            output = Path(tmp) / "schema.json"
            argv = [
                "run.py",
                "discover",
                "--samples", "sample.pdf",
                "--input-root", str(Path(tmp) / "PDFs"),
                "--output", str(output),
                "--usage-log", str(Path(tmp) / "usage.jsonl"),
            ]

            with mock.patch("src.schema.discovery.SchemaDiscovery", return_value=discovery) as factory, \
                 mock.patch("sys.argv", argv):
                exit_code = run_module.main()

            self.assertEqual(exit_code, 0)
            self.assertEqual(factory.call_args.kwargs["pdf_root"], Path(tmp) / "PDFs")
            artifact = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(artifact["status"], "success")
            self.assertEqual(artifact["artifact_type"], "discovered_schema")

    def test_default_output_is_json(self) -> None:
        args = build_parser().parse_args(["discover"])

        self.assertEqual(args.output, "outputs/private_health/schema.json")

    def test_batch_resume_flags_are_explicit(self) -> None:
        args = build_parser().parse_args(
            ["batch", "--schema", "schema.json", "--resume", "--trust-legacy-cache"]
        )

        self.assertTrue(args.resume)
        self.assertTrue(args.trust_legacy_cache)


class BatchResumeTest(unittest.TestCase):
    def _result(self, pdf_path: Path, **overrides: object) -> run_module.ExtractionResult:
        values = {
            "vertical": "private_health",
            "schema_version": "1.0.0",
            "source_path": str(pdf_path),
            "provider": "deepseek",
            "model": "deepseek-v4-pro",
            "data": {"product_name": "Example"},
        }
        values.update(overrides)
        return run_module.ExtractionResult(**values)

    def test_exact_hashes_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "product.pdf"
            schema = root / "schema.json"
            output = root / "product.json"
            pdf.write_bytes(b"pdf")
            schema.write_text("{}", encoding="utf-8")
            self._result(
                pdf,
                schema_sha256="schema-hash",
                source_sha256=run_module.file_sha256(pdf),
            ).write_json(output)

            cached = run_module.load_cached_batch_result(
                output_path=output,
                pdf_path=pdf,
                schema_path=schema,
                schema_hash="schema-hash",
                source_hash=run_module.file_sha256(pdf),
                provider="deepseek",
                model="deepseek-v4-pro",
                trust_legacy=False,
            )

            self.assertIsNotNone(cached)

    def test_changed_pdf_is_not_resumed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "product.pdf"
            schema = root / "schema.json"
            output = root / "product.json"
            pdf.write_bytes(b"old")
            schema.write_text("{}", encoding="utf-8")
            self._result(
                pdf,
                schema_sha256="schema-hash",
                source_sha256=run_module.file_sha256(pdf),
            ).write_json(output)
            pdf.write_bytes(b"new")

            cached = run_module.load_cached_batch_result(
                output_path=output,
                pdf_path=pdf,
                schema_path=schema,
                schema_hash="schema-hash",
                source_hash=run_module.file_sha256(pdf),
                provider="deepseek",
                model="deepseek-v4-pro",
                trust_legacy=False,
            )

            self.assertIsNone(cached)

    def test_legacy_cache_is_adopted_only_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "product.pdf"
            schema = root / "schema.json"
            output = root / "product.json"
            pdf.write_bytes(b"pdf")
            schema.write_text("{}", encoding="utf-8")
            self._result(pdf).write_json(output)
            source_hash = run_module.file_sha256(pdf)

            cached = run_module.load_cached_batch_result(
                output_path=output,
                pdf_path=pdf,
                schema_path=schema,
                schema_hash="schema-hash",
                source_hash=source_hash,
                provider="deepseek",
                model="deepseek-v4-pro",
                trust_legacy=True,
            )

            self.assertEqual(cached.schema_sha256, "schema-hash")
            persisted = run_module.ExtractionResult.model_validate_json(
                output.read_text(encoding="utf-8")
            )
            self.assertEqual(persisted.source_sha256, source_hash)


if __name__ == "__main__":
    unittest.main()
