from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest import mock

from src.common.model_config import ModelSelection, resolve_selection
from src import run as run_module
from src.run import build_parser, configure_command
from src.verticals.manifest import PROJECT_ROOT
from src.schema.validation import normalize_schema
from tests.test_json_contracts import VALID_DISCOVERED_SCHEMA
from tests.test_canonical_schema import approved_travel_schema
from tests.test_travel_schema_migration import (
    VALID_TRAVEL_EXTRACTION,
    VALID_TRAVEL_SCHEMA,
)


class RunParserTest(unittest.TestCase):
    def test_batch_returns_failure_when_extraction_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.pdf").write_bytes(b"offline fixture")
            args = build_parser().parse_args([
                "batch", "--schema", "schema.json", "--input-root", tmp])
            configure_command(args)
            extractor = mock.Mock()
            extractor.extract_one.side_effect = ValueError("invalid output")
            with mock.patch.object(run_module, "load_schema_data", return_value={
                "vertical": "private_health", "version": "test"}), mock.patch.object(
                    run_module, "_build_schema_extractor", return_value=extractor):
                self.assertEqual(run_module.command_batch(args), 1)

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
            discovery.discover.return_value = normalize_schema(VALID_DISCOVERED_SCHEMA)
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
        configure_command(args)

        self.assertEqual(args.output, PROJECT_ROOT / "outputs/private_health/schema.json")

    def test_explicit_vertical_cannot_conflict_with_manifest(self) -> None:
        args = build_parser().parse_args(
            [
                "batch",
                "--manifest",
                "configs/travel_insurance/manifest.json",
                "--vertical",
                "private_health",
                "--schema",
                "schema.json",
            ]
        )

        with self.assertRaisesRegex(ValueError, "conflicts with manifest"):
            configure_command(args)

    def test_crawl_defaults_come_from_travel_manifest(self) -> None:
        args = build_parser().parse_args(["crawl"])
        configure_command(args)

        self.assertEqual(args.vertical, "travel_insurance")
        self.assertEqual(
            args.config,
            PROJECT_ROOT / "configs/travel_insurance/sources.json",
        )
        self.assertEqual(
            args.data_root,
            PROJECT_ROOT / "data/travel_insurance/raw/PDFs",
        )
        self.assertEqual(
            args.output_root,
            PROJECT_ROOT / "outputs/travel_insurance/acquisition",
        )

    def test_crawl_parser_uses_insurer_not_llm_provider(self) -> None:
        args = build_parser().parse_args(
            [
                "crawl",
                "--vertical",
                "travel_insurance",
                "--config",
                "configs/travel_insurance/sources.json",
                "--insurer",
                "allianz",
                "--insurer",
                "scti",
            ]
        )

        self.assertEqual(args.insurers, ["allianz", "scti"])
        self.assertFalse(hasattr(args, "provider"))

    def test_canonical_compile_defaults_to_storage_enabled_travel_manifest(self) -> None:
        args = build_parser().parse_args(
            [
                "canonical-compile",
                "--schema",
                "approved.json",
                "--output-dir",
                "compiled",
            ]
        )

        manifest = configure_command(args)

        self.assertEqual(manifest.vertical, "travel_insurance")
        self.assertTrue(manifest.supports("storage"))

    def test_storage_init_defaults_to_manifest_canonical_schema_and_env_url(self) -> None:
        args = build_parser().parse_args(["storage-init"])

        manifest = configure_command(args)

        self.assertEqual(manifest.vertical, "travel_insurance")
        self.assertEqual(
            args.schema,
            PROJECT_ROOT / "configs/travel_insurance/canonical_schema_v1.json",
        )
        self.assertEqual(args.database_url_env, "KONKRD_DATABASE_URL")
        self.assertFalse(hasattr(args, "database_url"))

    def test_storage_init_calls_postgresql_service_without_printing_url(self) -> None:
        argv = [
            "run.py",
            "storage-init",
            "--manifest",
            "configs/travel_insurance/manifest.json",
        ]

        with mock.patch(
            "src.storage.service.resolve_database_url",
            return_value="postgresql+psycopg://user:secret@localhost/db",
        ), mock.patch("src.storage.service.initialize_storage") as initialize, \
             mock.patch("builtins.print") as print_message, \
             mock.patch("sys.argv", argv):
            exit_code = run_module.main()

        self.assertEqual(exit_code, 0)
        initialize.assert_called_once()
        printed = " ".join(str(call) for call in print_message.call_args_list)
        self.assertNotIn("secret", printed)

    def test_storage_load_reports_stable_summary(self) -> None:
        summary = mock.Mock(
            run_id="run-1",
            document_id="sha256:document",
            schema_version_id="sha256:schema",
            products_loaded=2,
            release_ids=("release-1", "release-2"),
        )
        argv = [
            "run.py",
            "storage-load",
            "--manifest",
            "configs/travel_insurance/manifest.json",
            "--artifact",
            "outputs/travel_insurance/extractions/example.json",
            "--insurer-code",
            "cover_more",
        ]

        with mock.patch(
            "src.storage.service.resolve_database_url",
            return_value="postgresql+psycopg://user:secret@localhost/db",
        ), mock.patch(
            "src.storage.service.load_extraction_artifact",
            return_value=summary,
        ) as load, mock.patch("builtins.print") as print_message, \
             mock.patch("sys.argv", argv):
            exit_code = run_module.main()

        self.assertEqual(exit_code, 0)
        load.assert_called_once()
        printed = " ".join(str(call) for call in print_message.call_args_list)
        self.assertIn("products=2", printed)
        self.assertNotIn("secret", printed)

    def test_canonical_compile_writes_contract_and_postgresql_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            schema_path = root / "approved.json"
            schema_path.write_text(
                json.dumps(approved_travel_schema()),
                encoding="utf-8",
            )
            output_dir = root / "compiled"
            argv = [
                "run.py",
                "canonical-compile",
                "--schema",
                str(schema_path),
                "--output-dir",
                str(output_dir),
            ]

            with mock.patch("sys.argv", argv):
                exit_code = run_module.main()

            extraction_contract = json.loads(
                (output_dir / "extraction_contract.json").read_text(encoding="utf-8")
            )
            ddl = (output_dir / "vertical_table.sql").read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertIn("products", extraction_contract["properties"])
        self.assertIn("CREATE TABLE travel_product_details", ddl)
        self.assertIn("JSONB", ddl)

    def test_canonical_compile_rejects_candidate_without_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            schema = approved_travel_schema()
            schema["status"] = "candidate"
            schema["review"] = None
            schema_path = root / "candidate.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            output_dir = root / "compiled"
            argv = [
                "run.py",
                "canonical-compile",
                "--schema",
                str(schema_path),
                "--output-dir",
                str(output_dir),
            ]

            with mock.patch("sys.argv", argv):
                exit_code = run_module.main()

            self.assertEqual(exit_code, 1)
            self.assertFalse(output_dir.exists())

    def test_canonical_compile_refuses_existing_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            schema_path = root / "approved.json"
            schema_path.write_text(
                json.dumps(approved_travel_schema()),
                encoding="utf-8",
            )
            output_dir = root / "compiled"
            output_dir.mkdir()
            argv = [
                "run.py",
                "canonical-compile",
                "--schema",
                str(schema_path),
                "--output-dir",
                str(output_dir),
            ]

            with mock.patch("sys.argv", argv):
                exit_code = run_module.main()

        self.assertEqual(exit_code, 1)

    def test_travel_discovery_injects_manifest_contract_prompt_and_validator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            discovery = mock.Mock()
            discovery.discover.return_value = normalize_schema(VALID_TRAVEL_SCHEMA)
            output = Path(tmp) / "schema.json"
            argv = [
                "run.py",
                "discover",
                "--manifest",
                "configs/travel_insurance/manifest.json",
                "--samples",
                "travel.pdf",
                "--output",
                str(output),
                "--usage-log",
                str(Path(tmp) / "usage.jsonl"),
            ]

            with mock.patch(
                "src.schema.discovery.SchemaDiscovery", return_value=discovery
            ) as factory, mock.patch("sys.argv", argv):
                exit_code = run_module.main()

        self.assertEqual(exit_code, 0)
        manifest = factory.call_args.kwargs["manifest"]
        self.assertEqual(manifest.vertical, "travel_insurance")
        self.assertEqual(manifest.contract("discovered_schema"), "travel_insurance/discovered_schema")

    def test_travel_extract_injects_multiple_product_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf_path = root / "travel.pdf"
            pdf_path.touch()
            schema_path = root / "schema.json"
            schema_path.write_text(json.dumps(VALID_TRAVEL_SCHEMA), encoding="utf-8")
            output_path = root / "extraction.json"
            extractor = mock.Mock()
            extractor.extract_one.return_value = VALID_TRAVEL_EXTRACTION
            argv = [
                "run.py",
                "extract",
                "--manifest",
                "configs/travel_insurance/manifest.json",
                "--pdf",
                str(pdf_path),
                "--schema",
                str(schema_path),
                "--output",
                str(output_path),
            ]

            with mock.patch(
                "src.schema_application.extractor.SchemaExtractor",
                return_value=extractor,
            ) as factory, mock.patch("sys.argv", argv):
                exit_code = run_module.main()

            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(payload["data"]["products"]), 2)
        manifest = factory.call_args.kwargs["manifest"]
        self.assertEqual(manifest.vertical, "travel_insurance")
        self.assertEqual(manifest.documents.output_cardinality, "multiple")

    def test_discovery_reuses_failure_artifact_written_by_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "schema.json"
            run_id = "fixed-run-id"
            error_path = root / "errors" / "schema_discovery" / f"{run_id}.json"

            class FailingDiscovery:
                def discover(self, *_args, **_kwargs):
                    error_path.parent.mkdir(parents=True)
                    error_path.write_text("already written", encoding="utf-8")
                    raise RuntimeError("provider failed")

            argv = [
                "run.py",
                "discover",
                "--manifest",
                "configs/travel_insurance/manifest.json",
                "--samples",
                "travel.pdf",
                "--output",
                str(output),
                "--usage-log",
                str(root / "usage.jsonl"),
            ]

            with mock.patch(
                "src.schema.discovery.SchemaDiscovery",
                return_value=FailingDiscovery(),
            ), mock.patch("src.run.uuid4") as uuid_factory, mock.patch(
                "src.run.write_failure_artifact"
            ) as write_failure, mock.patch("sys.argv", argv):
                uuid_factory.return_value.hex = run_id
                exit_code = run_module.main()

        self.assertEqual(exit_code, 1)
        write_failure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
