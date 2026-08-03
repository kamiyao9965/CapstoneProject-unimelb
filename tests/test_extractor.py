from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.common.model_config import ModelSelection
from src.common.model_provider import ModelResponse, ProviderRequest
from src.schema_application.extractor import SchemaExtractor
from tests.test_json_contracts import VALID_DISCOVERED_SCHEMA

VALID_RECORD = {
    "product_type": "hospital",
    "product_name": "Example",
    "_unfilled": [],
    "_notes": None,
}


class ExtractManyOutputTest(unittest.TestCase):
    def test_rejects_invalid_schema_before_extraction(self) -> None:
        with self.assertRaises(ValueError):
            SchemaExtractor(schema_data={"fields": []}, log=None)

    def test_same_stem_pdfs_write_distinct_json_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first_pdf = Path(tmp) / "FundA" / "hospital" / "product.pdf"
            second_pdf = Path(tmp) / "FundB" / "hospital" / "product.pdf"
            out_dir = Path(tmp) / "extractions"
            extractor = SchemaExtractor(schema_data=VALID_DISCOVERED_SCHEMA, log=None)

            with mock.patch.object(
                extractor,
                "extract_one",
                side_effect=[
                    {
                        "product_type": "hospital", "product_name": "A",
                        "_unfilled": [], "_notes": None,
                    },
                    {
                        "product_type": "hospital", "product_name": "B",
                        "_unfilled": [], "_notes": None,
                    },
                ],
            ):
                written = extractor.extract_many([first_pdf, second_pdf], out_dir)

            self.assertEqual(len(set(written)), 2)
            artifacts = [json.loads(path.read_text(encoding="utf-8")) for path in written]
            self.assertCountEqual(
                [artifact["data"]["product_name"] for artifact in artifacts],
                ["A", "B"],
            )

    def test_failure_stops_remaining_documents_and_preserves_prior_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdfs = [Path(tmp) / f"{name}.pdf" for name in ("one", "two", "three")]
            out_dir = Path(tmp) / "extractions"
            extractor = SchemaExtractor(schema_data=VALID_DISCOVERED_SCHEMA, log=None)
            calls: list[str] = []

            def extract(path: Path, *, run_id: str | None = None) -> dict:
                calls.append(Path(path).name)
                if Path(path).name == "two.pdf":
                    raise RuntimeError("provider failed")
                return {
                    "product_type": "hospital",
                    "product_name": Path(path).stem,
                    "_unfilled": [],
                    "_notes": None,
                }

            with mock.patch.object(extractor, "extract_one", side_effect=extract):
                with self.assertRaisesRegex(RuntimeError, "provider failed"):
                    extractor.extract_many(pdfs, out_dir)

            self.assertEqual(calls, ["one.pdf", "two.pdf"])
            self.assertTrue((out_dir / "one.json").exists())
            self.assertFalse((out_dir / "three.json").exists())
            self.assertEqual(len(list((out_dir / "errors" / "extraction").glob("*.json"))), 1)

    def test_usage_and_success_artifact_share_one_logical_run_id(self) -> None:
        class RecordingProvider:
            def generate(self, request: ProviderRequest) -> ModelResponse:
                return ModelResponse(
                    text=json.dumps(VALID_RECORD),
                    provider=request.selection.provider,
                    model=request.selection.model,
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf_path = root / "example.pdf"
            pdf_path.touch()
            usage_path = root / "usage.jsonl"
            with mock.patch(
                "src.schema_application.extractor.render_pdf_paths_for_prompt",
                return_value="# PDF: example\nstructured content",
            ):
                output_path = SchemaExtractor(
                    schema_data=VALID_DISCOVERED_SCHEMA,
                    provider=RecordingProvider(),
                    usage_log_path=usage_path,
                    log=None,
                ).extract_many([pdf_path], root / "extractions")[0]

            usage = json.loads(usage_path.read_text(encoding="utf-8"))
            artifact = json.loads(output_path.read_text(encoding="utf-8"))

            self.assertEqual(usage["run_id"], artifact["provenance"]["run_id"])

    def test_extract_one_passes_logical_pdf_to_injected_provider(self) -> None:
        class RecordingProvider:
            def __init__(self) -> None:
                self.request: ProviderRequest | None = None

            def generate(self, request: ProviderRequest) -> ModelResponse:
                self.request = request
                return ModelResponse(
                    text=json.dumps(VALID_RECORD),
                    provider=request.selection.provider,
                    model=request.selection.model,
                )

        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "example.pdf"
            pdf_path.touch()
            provider = RecordingProvider()
            selection = ModelSelection("openai", "gpt-5", "markdown")
            with mock.patch(
                "src.schema_application.extractor.render_pdf_paths_for_prompt",
                return_value="# PDF: example\nstructured content",
            ):
                record = SchemaExtractor(
                    schema_data=VALID_DISCOVERED_SCHEMA,
                    selection=selection,
                    provider=provider,
                    usage_log_path=None,
                    log=None,
                ).extract_one(pdf_path)

        self.assertEqual(record, VALID_RECORD)
        self.assertIsNotNone(provider.request)
        self.assertEqual(provider.request.selection, selection)
        self.assertEqual(provider.request.document_paths, ())
        self.assertIn("Discovered schema data", provider.request.user_text)
        self.assertEqual(provider.request.structured_output.name, "extraction_result")
        self.assertTrue(provider.request.structured_output.strict)

    def test_open_list_object_field_uses_non_strict_provider_schema(self) -> None:
        class RecordingProvider:
            def __init__(self) -> None:
                self.request: ProviderRequest | None = None

            def generate(self, request: ProviderRequest) -> ModelResponse:
                self.request = request
                return ModelResponse(
                    text=json.dumps(
                        {
                            "product_type": "extras",
                            "product_name": "Example",
                            "benefits": [{"label": "Dental", "limit": 500}],
                            "_unfilled": [],
                            "_notes": None,
                        }
                    ),
                    provider=request.selection.provider,
                    model=request.selection.model,
                )

        schema = json.loads(json.dumps(VALID_DISCOVERED_SCHEMA))
        schema["fields"].append(
            {
                "name": "benefits",
                "type": "list[object]",
                "description": "Benefit entries whose nested shape is source-defined.",
                "applies_to": ["extras"],
                "required": False,
                "values": [],
                "aliases": [],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "example.pdf"
            pdf_path.touch()
            provider = RecordingProvider()
            with mock.patch(
                "src.schema_application.extractor.render_pdf_paths_for_prompt",
                return_value="# PDF: example\nstructured content",
            ):
                SchemaExtractor(
                    schema_data=schema,
                    selection=ModelSelection("openai", "gpt-5", "markdown"),
                    provider=provider,
                    usage_log_path=None,
                    log=None,
                ).extract_one(pdf_path)

        self.assertIsNotNone(provider.request)
        self.assertFalse(provider.request.structured_output.strict)

    def test_extract_one_uses_pdfingestor_text_and_logs_source_pdf(self) -> None:
        class RecordingProvider:
            def __init__(self) -> None:
                self.request: ProviderRequest | None = None

            def generate(self, request: ProviderRequest) -> ModelResponse:
                self.request = request
                return ModelResponse(
                    text=json.dumps(VALID_RECORD),
                    provider=request.selection.provider,
                    model=request.selection.model,
                )

        with tempfile.TemporaryDirectory() as tmp:
            pdf_root = Path(tmp) / "PDFs"
            pdf_path = pdf_root / "HCF" / "hospital" / "example.pdf"
            pdf_path.parent.mkdir(parents=True)
            pdf_path.touch()
            usage_log = Path(tmp) / "usage.jsonl"
            provider = RecordingProvider()
            with mock.patch(
                "src.schema_application.extractor.render_pdf_paths_for_prompt",
                return_value="# PDF: example\nstructured content",
            ):
                record = SchemaExtractor(
                    schema_data=VALID_DISCOVERED_SCHEMA,
                    selection=ModelSelection("anthropic", "claude-test", "markdown"),
                    provider=provider,
                    pdf_root=pdf_root,
                    usage_log_path=usage_log,
                    log=None,
                ).extract_one(pdf_path)

            self.assertEqual(record, VALID_RECORD)
            self.assertEqual(provider.request.document_paths, ())
            self.assertIn("PDFingestor structured representation", provider.request.user_text)
            logged = json.loads(usage_log.read_text(encoding="utf-8"))
            self.assertEqual(logged["source_pdf"], pdf_path.as_posix())
            self.assertEqual(logged["document_input"], "markdown")


if __name__ == "__main__":
    unittest.main()
