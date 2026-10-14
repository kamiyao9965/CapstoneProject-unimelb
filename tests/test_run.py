from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
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
            [
                "batch", "--schema", "schema.json", "--resume",
                "--trust-legacy-cache", "--archive-stale-products",
            ]
        )

        self.assertTrue(args.resume)
        self.assertTrue(args.trust_legacy_cache)
        self.assertTrue(args.archive_stale_products)

    def test_batch_accepts_targeted_pet_product_ids(self) -> None:
        args = build_parser().parse_args([
            "batch", "--schema", "schema.json", "--product-ids",
            "pd_insurance_accident", "pd_insurance_classic",
        ])

        self.assertEqual(
            args.product_ids,
            ["pd_insurance_accident", "pd_insurance_classic"],
        )

    def test_batch_exposes_long_configurable_timeout(self) -> None:
        default_args = build_parser().parse_args(["batch", "--schema", "schema.json"])
        custom_args = build_parser().parse_args([
            "batch", "--schema", "schema.json", "--timeout", "1350",
        ])

        self.assertEqual(default_args.timeout, 1800.0)
        self.assertEqual(custom_args.timeout, 1350.0)

    def test_batch_passes_timeout_to_pet_extractor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "product.pdf"
            pdf.write_bytes(b"pdf")
            manifest = root / "manifest.json"
            manifest.write_text('{"documents": []}', encoding="utf-8")
            args = build_parser().parse_args([
                "batch",
                "--schema", str(root / "schema.json"),
                "--input-root", str(root),
                "--manifest", str(manifest),
                "--timeout", "1350",
            ])
            schema = SimpleNamespace(vertical="pet_insurance", version="test")
            loader = mock.Mock()
            loader.load.return_value = schema
            validator = mock.Mock()
            validator.validate.return_value = []
            extractor = SimpleNamespace(schema_hash="schema-hash")
            selection = ModelSelection("openai", "gpt-5", "markdown")

            with mock.patch(
                "src.run.load_schema_data",
                return_value={"vertical": "pet_insurance"},
            ), mock.patch(
                "src.run.SchemaLoader",
                return_value=loader,
            ), mock.patch(
                "src.run.SchemaValidator",
                return_value=validator,
            ), mock.patch(
                "src.run.resolve_selection",
                return_value=selection,
            ), mock.patch(
                "src.pipeline.extractor.Extractor",
                return_value=extractor,
            ) as extractor_factory, mock.patch(
                "src.run.run_pet_product_batch",
                return_value=0,
            ):
                exit_code = run_module.command_batch(args)

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                extractor_factory.call_args.kwargs["timeout_seconds"],
                1350.0,
            )

    def test_manifest_discovery_defaults_to_broader_12_document_sample(self) -> None:
        args = build_parser().parse_args(["discover", "--manifest", "manifest.json"])

        self.assertEqual(args.sample_count, 12)
        self.assertEqual(
            args.document_roles,
            ["pds", "policy_booklet", "combined_fsg_pds", "renewal_pds"],
        )


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

    def test_product_resume_requires_current_product_prompt_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "product.json"
            run_module.ProductExtractionResult(
                schema_version="1.0.0",
                product_id="example",
                document_family_id="example",
                source_documents=[],
                provider="openai",
                model="gpt-5",
                schema_sha256="schema-hash",
                extraction_prompt_version="pet-product-family.v1",
                source_sha256=["source-hash"],
                data={"product_id": "example"},
            ).write_json(output)

            self.assertFalse(run_module._cached_product_matches(
                output,
                schema_hash="schema-hash",
                source_hashes=["source-hash"],
                provider="openai",
                model="gpt-5",
                extraction_prompt_version="pet-product-family.v2",
            ))

    def test_archives_only_product_ids_absent_from_current_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for product_id in ("current", "stale"):
                run_module.ProductExtractionResult(
                    schema_version="1.0.0",
                    product_id=product_id,
                    document_family_id=product_id,
                    provider="openai",
                    model="gpt-5",
                    data={"product_id": product_id},
                ).write_json(root / f"{product_id}.json")

            archived = run_module._archive_stale_product_outputs(root, {"current"})

            self.assertTrue((root / "current.json").exists())
            self.assertEqual([path.name for path in archived], ["stale.json"])
            self.assertTrue(archived[0].is_file())

    def test_failed_family_archives_superseded_expected_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "product.pdf"
            pdf.write_bytes(b"pdf")
            output_root = root / "pet_insurance" / "products"
            output_root.mkdir(parents=True)
            old_output = output_root / "example.json"
            run_module.ProductExtractionResult(
                schema_version="old",
                product_id="example",
                document_family_id="example-family",
                provider="openai",
                model="gpt-5",
                extraction_prompt_version="pet-product-family.old",
                data={"product_id": "example"},
            ).write_json(old_output)
            family = SimpleNamespace(
                family_id="example-family",
                expected_product_ids=["example"],
                unattached_documents=[],
                pdf_paths=[pdf],
            )
            extractor = SimpleNamespace(
                schema_hash="schema-hash",
                extract_product_family=mock.Mock(
                    side_effect=RuntimeError("provider timed out")
                ),
            )

            with mock.patch(
                "src.schema_application.product_families.resolve_product_families",
                return_value=[family],
            ), mock.patch(
                "src.run.load_config",
                return_value=SimpleNamespace(outputs_dir=root),
            ), mock.patch(
                "src.run.safely_record_batch_failure",
                return_value=None,
            ):
                exit_code = run_module.run_pet_product_batch(
                    extractor=extractor,
                    schema=SimpleNamespace(version="current"),
                    pdf_paths=[pdf],
                    source_hashes={pdf: "source-hash"},
                    manifest_path=root / "manifest.json",
                    selection=ModelSelection("openai", "gpt-5", "markdown"),
                    resume=False,
                )

            self.assertEqual(exit_code, 1)
            self.assertFalse(old_output.exists())
            archived = list(
                output_root.glob("archive/superseded-*/example.json")
            )
            self.assertEqual(len(archived), 1)
            self.assertTrue(archived[0].is_file())

    def test_matching_resumed_family_is_not_archived(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "product.pdf"
            pdf.write_bytes(b"pdf")
            output_root = root / "pet_insurance" / "products"
            output_root.mkdir(parents=True)
            output = output_root / "example.json"
            run_module.ProductExtractionResult(
                schema_version="current",
                product_id="example",
                document_family_id="example-family",
                provider="openai",
                model="gpt-5",
                schema_sha256="schema-hash",
                extraction_prompt_version=run_module.PET_PRODUCT_FAMILY_PROMPT_VERSION,
                source_sha256=["source-hash"],
                data={"product_id": "example"},
            ).write_json(output)
            family = SimpleNamespace(
                family_id="example-family",
                expected_product_ids=["example"],
                unattached_documents=[],
                pdf_paths=[pdf],
            )
            extract_product_family = mock.Mock()
            extractor = SimpleNamespace(
                schema_hash="schema-hash",
                extract_product_family=extract_product_family,
            )

            with mock.patch(
                "src.schema_application.product_families.resolve_product_families",
                return_value=[family],
            ), mock.patch(
                "src.run.load_config",
                return_value=SimpleNamespace(outputs_dir=root),
            ):
                exit_code = run_module.run_pet_product_batch(
                    extractor=extractor,
                    schema=SimpleNamespace(version="current"),
                    pdf_paths=[pdf],
                    source_hashes={pdf: "source-hash"},
                    manifest_path=root / "manifest.json",
                    selection=ModelSelection("openai", "gpt-5", "markdown"),
                    resume=True,
                )

            self.assertEqual(exit_code, 0)
            self.assertTrue(output.is_file())
            self.assertFalse((output_root / "archive").exists())
            extract_product_family.assert_not_called()

    def test_batch_failure_artifact_preserves_structured_validation_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            error = RuntimeError("structured extraction failed")
            structured_error = {
                "path": "$.products[0].benefit_coverages[2]",
                "message": "source_block_id was not found",
                "kind": "evidence_related",
                "repair_hint": "Use block p4_text_1.",
            }
            response = SimpleNamespace(
                response_id="response-1",
                provider="openai",
                model="gpt-5",
                text='{"products": [{"product_id": "example"}]}',
                usage=SimpleNamespace(
                    input_tokens=100,
                    output_tokens=20,
                    total_tokens=120,
                ),
            )
            error.result = SimpleNamespace(
                errors=(structured_error,),
                attempts=(SimpleNamespace(
                    number=1,
                    response=response,
                    errors=(structured_error,),
                ),),
            )
            with mock.patch(
                "src.run.load_config",
                return_value=SimpleNamespace(outputs_dir=root),
            ):
                path = run_module.record_batch_failure(
                    vertical="pet_insurance",
                    pdf_path=Path("product.pdf"),
                    schema_hash="schema-hash",
                    source_hash="source-hash",
                    provider="openai",
                    model="gpt-5",
                    document_input="markdown",
                    error=error,
                    stage="product_family_extraction",
                )

            artifact = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(artifact["status"], "failed")
            self.assertEqual(artifact["provenance"]["document_input"], "markdown")
            self.assertEqual(
                artifact["error"]["details"][0],
                {
                    "path": "$.products[0].benefit_coverages[2]",
                    "message": "source_block_id was not found",
                },
            )
            diagnostics_path = path.with_name(f"{path.stem}.attempts.json")
            diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
            self.assertEqual(
                diagnostics["artifact_type"],
                "structured_output_attempt_diagnostics",
            )
            self.assertEqual(diagnostics["run_id"], path.stem)
            self.assertEqual(
                diagnostics["attempts"][0]["response_text"],
                response.text,
            )
            self.assertEqual(
                diagnostics["attempts"][0]["errors"][0]["kind"],
                "evidence_related",
            )
            self.assertEqual(
                diagnostics["attempts"][0]["usage"]["total_tokens"],
                120,
            )


if __name__ == "__main__":
    unittest.main()
