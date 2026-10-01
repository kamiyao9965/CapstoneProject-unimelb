from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.pdf_ingestion.adapter import render_documents_for_prompt, render_pdf_paths_for_prompt
from src.pdf_ingestion.models import PageRepresentation, ParsedPDF, TextBlock
from src.pdf_ingestion.cache import sha256_file, stable_config_hash
from src.pdf_ingestion.parser import PDFIngestor
from src.pdf_ingestion.mineru import MinerUIngestor


def parsed_document(source_path: Path, text: str) -> ParsedPDF:
    return ParsedPDF(
        pdf_id=source_path.name,
        pdf_hash="fixture-hash",
        source_path=str(source_path),
        pages=[
            PageRepresentation(
                page_num=1,
                width=1000.0,
                height=1000.0,
                blocks=[TextBlock(block_id="p1-b1", content=text, top=0.0)],
            )
        ],
    )


class ParsedMarkdownTest(unittest.TestCase):
    def test_relocated_pdf_reuses_cache_with_current_source_and_markdown_path(self) -> None:
        for parser, ingestor_type, parse_method in (
            ("pdfingestor", PDFIngestor, "_parse_pdf"),
            ("mineru", MinerUIngestor, "_run_mineru"),
        ):
            with self.subTest(parser=parser), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                pdf = root / "pdf/travel_insurance/fund/pds/current.pdf"
                pdf.parent.mkdir(parents=True)
                pdf.write_bytes(b"cached PDF fixture")
                ingestor = ingestor_type(root / "cache")
                digest = sha256_file(pdf)
                config_hash = stable_config_hash(ingestor._parser_config())
                old = parsed_document(root / "old/location/old-name.pdf", "Cover")
                old.pdf_hash = digest
                cache_path = ingestor.cache.write(digest, config_hash, old.model_dump())
                cache_bytes = cache_path.read_bytes()

                with mock.patch.object(ingestor, parse_method, side_effect=AssertionError("Cache must be reused")):
                    current = ingestor.ingest(pdf)
                self.assertEqual(current.source_path, str(pdf))
                self.assertEqual(current.pdf_id, "current.pdf")
                self.assertEqual(current.pages, old.pages)
                self.assertEqual(cache_path.read_bytes(), cache_bytes)

                with mock.patch("src.pdf_ingestion.adapter.ingest_pdfs", return_value=(current,)):
                    render_pdf_paths_for_prompt(
                        [pdf], pdf_root=root / "pdf/travel_insurance",
                        markdown_dir=root / "markdown/travel_insurance",
                        document_parser=parser,
                    )
                markdown = root / "markdown/travel_insurance" / parser / "fund/pds/current.md"
                self.assertIn(str(pdf), markdown.read_text())
                self.assertNotIn("old/location", markdown.read_text())

    def test_saves_one_file_per_pdf_under_its_parser_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf_root = root / "PDFs"
            first = parsed_document(pdf_root / "tick" / "pds" / "single.pdf", "Single trip cover")
            second = parsed_document(pdf_root / "tick" / "pds" / "cruise.pdf", "Cruise cover")
            with mock.patch("src.pdf_ingestion.adapter.ingest_pdfs", return_value=(first, second)):
                prompt = render_pdf_paths_for_prompt(
                    ["tick/pds/single.pdf", "tick/pds/cruise.pdf"],
                    pdf_root=pdf_root,
                    document_parser="mineru",
                    markdown_dir=root / "parsed_markdown",
                )

            single_path = root / "parsed_markdown" / "mineru" / "tick" / "pds" / "single.md"
            self.assertEqual(
                single_path.read_text(encoding="utf-8"),
                render_documents_for_prompt([first]) + "\n",
            )
            self.assertTrue((root / "parsed_markdown" / "mineru" / "tick" / "pds" / "cruise.md").is_file())
            self.assertEqual(prompt, render_documents_for_prompt([first, second]))

    def test_pdf_outside_input_root_uses_a_distinct_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "elsewhere" / "policy.pdf"
            document = parsed_document(outside, "Outside the corpus")
            with mock.patch("src.pdf_ingestion.adapter.ingest_pdfs", return_value=(document,)):
                render_pdf_paths_for_prompt(
                    [outside],
                    pdf_root=root / "PDFs",
                    markdown_dir=root / "parsed_markdown",
                )

            identity = hashlib.sha256(str(outside.resolve()).encode("utf-8")).hexdigest()[:12]
            self.assertTrue(
                (root / "parsed_markdown" / "pdfingestor" / f"policy_{identity}.md").is_file()
            )

    def test_unchanged_text_is_not_rewritten_and_changed_text_is_updated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "PDFs" / "allianz" / "pds" / "travel.pdf"
            markdown_path = root / "parsed_markdown" / "pdfingestor" / "allianz" / "pds" / "travel.md"
            options = {"pdf_root": root / "PDFs", "markdown_dir": root / "parsed_markdown"}

            with mock.patch(
                "src.pdf_ingestion.adapter.ingest_pdfs",
                return_value=(parsed_document(source, "Version one"),),
            ):
                render_pdf_paths_for_prompt([source], **options)
                os.utime(markdown_path, ns=(1, 1))
                render_pdf_paths_for_prompt([source], **options)
            self.assertEqual(markdown_path.stat().st_mtime_ns, 1)

            with mock.patch(
                "src.pdf_ingestion.adapter.ingest_pdfs",
                return_value=(parsed_document(source, "Version two"),),
            ):
                render_pdf_paths_for_prompt([source], **options)
            self.assertIn("Version two", markdown_path.read_text(encoding="utf-8"))

    def test_without_markdown_dir_nothing_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            document = parsed_document(root / "PDFs" / "a.pdf", "Text")
            with mock.patch("src.pdf_ingestion.adapter.ingest_pdfs", return_value=(document,)):
                render_pdf_paths_for_prompt([root / "PDFs" / "a.pdf"], pdf_root=root / "PDFs")

            self.assertEqual(list(root.rglob("*.md")), [])


if __name__ == "__main__":
    unittest.main()
