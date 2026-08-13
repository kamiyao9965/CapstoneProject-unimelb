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

    def test_failure_is_recorded_and_remaining_documents_continue(self) -> None:
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
                written = extractor.extract_many(pdfs, out_dir)

            self.assertEqual(calls, ["one.pdf", "two.pdf", "three.pdf"])
            self.assertEqual(len(written), 2)
            self.assertTrue((out_dir / "one.json").exists())
            self.assertTrue((out_dir / "three.json").exists())
            self.assertEqual(len(list((out_dir / "errors" / "extraction").glob("*.json"))), 1)

    def test_identical_pdf_hash_is_extracted_once_and_reused_across_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "FundA" / "hospital" / "product.pdf"
            second = root / "FundB" / "hospital" / "copy.pdf"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_bytes(b"same-pdf-content")
            second.write_bytes(b"same-pdf-content")
            extractor = SchemaExtractor(
                schema_data=VALID_DISCOVERED_SCHEMA,
                extraction_cache_dir=root / "cache",
                log=None,
            )
            record = {
                "product_type": "hospital", "product_name": "Same",
                "_unfilled": [], "_notes": None,
            }

            with mock.patch.object(extractor, "extract_one", return_value=record) as extract:
                first_output = extractor.extract_many([first], root / "out-a")[0]
                second_output = extractor.extract_many([second], root / "out-b")[0]

            self.assertEqual(extract.call_count, 1)
            self.assertEqual(
                json.loads(first_output.read_text())["data"],
                json.loads(second_output.read_text())["data"],
            )
            self.assertEqual(
                json.loads(second_output.read_text())["provenance"]["source_documents"],
                [second.as_posix()],
            )

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
                "src.schema_application.extractor.render_documents_for_prompt",
                return_value="# PDF: example\nstructured content",
            ), mock.patch(
                "src.schema_application.extractor.ingest_pdfs", return_value=(),
            ), mock.patch(
                "src.schema_application.extractor.document_quality",
                return_value={"hard_failures": [], "has_key_heading": True},
            ):
                output_path = SchemaExtractor(
                    schema_data=VALID_DISCOVERED_SCHEMA,
                    provider=RecordingProvider(),
                    usage_log_path=usage_path,
                    extraction_cache_dir=root / "extraction-cache",
                    log=None,
                ).extract_many([pdf_path], root / "extractions")[0]

            usage = json.loads(usage_path.read_text(encoding="utf-8"))
            artifact = json.loads(output_path.read_text(encoding="utf-8"))

            self.assertEqual(usage["run_id"], artifact["provenance"]["run_id"])

    def test_success_artifact_records_product_type_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf_root = root / "PDFs"
            pdf_path = pdf_root / "NTF" / "hospital" / "top-extras.pdf"
            pdf_path.parent.mkdir(parents=True)
            pdf_path.touch()
            override_path = root / "overrides.json"
            override_path.write_text(
                '{"NTF/hospital/top-extras.pdf": "extras"}',
                encoding="utf-8",
            )
            extractor = SchemaExtractor(
                schema_data=VALID_DISCOVERED_SCHEMA,
                pdf_root=pdf_root,
                product_type_overrides_path=override_path,
                log=None,
            )

            with mock.patch.object(
                extractor,
                "extract_one",
                return_value={
                    "product_type": "extras",
                    "product_name": "Top Extras",
                    "_unfilled": [],
                    "_notes": None,
                },
            ):
                output_path = extractor.extract_many([pdf_path], root / "extractions")[0]

            artifact = json.loads(output_path.read_text(encoding="utf-8"))
            source_artifacts = artifact["provenance"]["source_artifacts"]
            self.assertIn("directory_product_type:hospital", source_artifacts)
            self.assertIn("override_product_type:extras", source_artifacts)
            self.assertIn("effective_product_type:extras", source_artifacts)
            self.assertIn("model_product_type:extras", source_artifacts)
            self.assertIn("product_type_conflict:directory_override", source_artifacts)

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
                "src.schema_application.extractor.render_documents_for_prompt",
                return_value="# PDF: example\nstructured content",
            ), mock.patch(
                "src.schema_application.extractor.ingest_pdfs", return_value=(),
            ), mock.patch(
                "src.schema_application.extractor.document_quality",
                return_value={"hard_failures": [], "has_key_heading": True},
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
        self.assertIn("Compact field guide for extraction semantics", provider.request.user_text)
        self.assertIn("product_type", provider.request.user_text)
        self.assertIn("Canonical private-health product classification", provider.request.user_text)
        self.assertNotIn('"fields"', provider.request.user_text)
        self.assertNotIn('"hospital_categories"', provider.request.user_text)
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
                "src.schema_application.extractor.render_documents_for_prompt",
                return_value="# PDF: example\nstructured content",
            ), mock.patch(
                "src.schema_application.extractor.ingest_pdfs", return_value=(),
            ), mock.patch(
                "src.schema_application.extractor.document_quality",
                return_value={"hard_failures": [], "has_key_heading": True},
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
                "src.schema_application.extractor.render_documents_for_prompt",
                return_value="# PDF: example\nstructured content",
            ), mock.patch(
                "src.schema_application.extractor.ingest_pdfs", return_value=(),
            ), mock.patch(
                "src.schema_application.extractor.document_quality",
                return_value={"hard_failures": [], "has_key_heading": True},
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
