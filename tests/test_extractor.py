from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.common.model_config import ModelSelection
from src.common.model_provider import ModelResponse, ProviderRequest
from src.extract.extractor import SchemaExtractor


class ExtractManyOutputTest(unittest.TestCase):
    def test_same_stem_pdfs_write_distinct_json_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first_pdf = Path(tmp) / "FundA" / "hospital" / "product.pdf"
            second_pdf = Path(tmp) / "FundB" / "hospital" / "product.pdf"
            out_dir = Path(tmp) / "extractions"
            extractor = SchemaExtractor(schema_text="fields: []", log=None)

            with mock.patch.object(
                extractor,
                "extract_one",
                side_effect=[{"fund": "A"}, {"fund": "B"}],
            ):
                written = extractor.extract_many([first_pdf, second_pdf], out_dir)

            self.assertEqual(len(set(written)), 2)
            records = [
                json.loads(path.read_text(encoding="utf-8")) for path in written
            ]
            self.assertCountEqual([record["fund"] for record in records], ["A", "B"])

    def test_extract_one_passes_logical_pdf_to_injected_provider(self) -> None:
        class RecordingProvider:
            def __init__(self) -> None:
                self.request: ProviderRequest | None = None

            def generate(self, request: ProviderRequest) -> ModelResponse:
                self.request = request
                return ModelResponse(
                    text='{"fund": "Example"}',
                    provider=request.selection.provider,
                    model=request.selection.model,
                )

        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "example.pdf"
            pdf_path.touch()
            provider = RecordingProvider()
            selection = ModelSelection("openai", "gpt-5", "pdf")
            record = SchemaExtractor(
                schema_text="fields: []",
                selection=selection,
                provider=provider,
                usage_log_path=None,
                log=None,
            ).extract_one(pdf_path)

        self.assertEqual(record, {"fund": "Example"})
        self.assertIsNotNone(provider.request)
        self.assertEqual(provider.request.selection, selection)
        self.assertEqual(provider.request.document_paths, (pdf_path,))
        self.assertIn("Schema (YAML)", provider.request.user_text)

    def test_extract_one_markdown_mode_sends_mirror_and_logs_source_pdf(self) -> None:
        class RecordingProvider:
            def __init__(self) -> None:
                self.request: ProviderRequest | None = None

            def generate(self, request: ProviderRequest) -> ModelResponse:
                self.request = request
                return ModelResponse(
                    text='{"fund": "Example"}',
                    provider=request.selection.provider,
                    model=request.selection.model,
                )

        class WritingPreprocessor:
            def convert(self, source_pdf: Path, output_markdown: Path) -> None:
                output_markdown.write_text("# converted\n", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            pdf_root = Path(tmp) / "PDFs"
            pdf_path = pdf_root / "HCF" / "hospital" / "example.pdf"
            pdf_path.parent.mkdir(parents=True)
            pdf_path.touch()
            usage_log = Path(tmp) / "usage.jsonl"
            provider = RecordingProvider()
            record = SchemaExtractor(
                schema_text="fields: []",
                selection=ModelSelection("anthropic", "claude-test", "markdown"),
                provider=provider,
                pdf_root=pdf_root,
                preprocessor=WritingPreprocessor(),
                usage_log_path=usage_log,
                log=None,
            ).extract_one(pdf_path)

            self.assertEqual(record, {"fund": "Example"})
            self.assertEqual(
                provider.request.document_paths,
                (Path(tmp) / "Markdown" / "HCF" / "hospital" / "example.md",),
            )
            logged = json.loads(usage_log.read_text(encoding="utf-8"))
            self.assertEqual(logged["source_pdf"], pdf_path.as_posix())
            self.assertEqual(logged["document_input"], "markdown")


if __name__ == "__main__":
    unittest.main()
