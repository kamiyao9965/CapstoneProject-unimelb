from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.common.document_preprocessor import (
    MinerUPreprocessor,
    ensure_markdown,
    markdown_path,
    prepare_documents,
)
from src.common.model_config import ModelSelection


class FakePreprocessor:
    def __init__(self, *, writes_output: bool = True) -> None:
        self.writes_output = writes_output
        self.calls: list[tuple[Path, Path]] = []

    def convert(self, source_pdf: Path, output_markdown: Path) -> None:
        self.calls.append((source_pdf, output_markdown))
        if self.writes_output:
            output_markdown.write_text("# converted\n", encoding="utf-8")


class MarkdownMirrorTest(unittest.TestCase):
    def test_maps_pdf_path_to_matching_markdown_tree(self) -> None:
        pdf_root = Path("data/private_health/raw/PDFs")
        source = pdf_root / "HCF" / "hospital" / "product.pdf"

        self.assertEqual(
            markdown_path(source, pdf_root),
            Path("data/private_health/raw/Markdown/HCF/hospital/product.md"),
        )

    def test_rejects_lexical_parent_escape_from_pdf_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_root = Path(tmp) / "PDFs"
            pdf_root.mkdir()
            outside = pdf_root / ".." / "outside.pdf"
            outside.touch()

            with self.assertRaisesRegex(ValueError, "below the configured PDF root"):
                markdown_path(outside, pdf_root)

    def test_rejects_source_symlink_that_escapes_pdf_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf_root = root / "PDFs"
            pdf_root.mkdir()
            outside = root / "outside.pdf"
            outside.touch()
            linked = pdf_root / "linked.pdf"
            linked.symlink_to(outside)

            with self.assertRaisesRegex(ValueError, "below the configured PDF root"):
                ensure_markdown(linked, pdf_root, FakePreprocessor())

    def test_rejects_cached_markdown_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf_root = root / "PDFs"
            source = pdf_root / "product.pdf"
            source.parent.mkdir()
            source.touch()
            outside = root / "outside.md"
            outside.write_text("secret", encoding="utf-8")
            cached = root / "Markdown" / "product.md"
            cached.parent.mkdir()
            cached.symlink_to(outside)

            with self.assertRaisesRegex(ValueError, "symbolic link"):
                ensure_markdown(source, pdf_root, FakePreprocessor())

    def test_reuses_existing_markdown_without_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_root = Path(tmp) / "PDFs"
            source = pdf_root / "HCF" / "extras" / "product.pdf"
            source.parent.mkdir(parents=True)
            source.touch()
            output = markdown_path(source, pdf_root)
            output.parent.mkdir(parents=True)
            output.write_text("# cached\n", encoding="utf-8")
            preprocessor = FakePreprocessor()

            resolved = ensure_markdown(source, pdf_root, preprocessor)

        self.assertEqual(resolved, output)
        self.assertEqual(preprocessor.calls, [])

    def test_fails_if_preprocessor_does_not_produce_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_root = Path(tmp) / "PDFs"
            source = pdf_root / "HCF" / "extras" / "product.pdf"
            source.parent.mkdir(parents=True)
            source.touch()

            with self.assertRaisesRegex(RuntimeError, "did not create"):
                ensure_markdown(source, pdf_root, FakePreprocessor(writes_output=False))

    def test_mineru_preprocessor_copies_generated_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.pdf"
            target = Path(tmp) / "Markdown" / "source.md"
            source.touch()

            def fake_run(command, check, capture_output, text):
                output_root = Path(command[command.index("-o") + 1])
                generated = output_root / "source" / "auto" / "source.md"
                generated.parent.mkdir(parents=True)
                generated.write_text("# converted\n", encoding="utf-8")
                return mock.Mock()

            MinerUPreprocessor(run=fake_run).convert(source, target)

            self.assertEqual(target.read_text(encoding="utf-8"), "# converted\n")


class ExplodingPreprocessor:
    def convert(self, source_pdf: Path, output_markdown: Path) -> None:
        raise AssertionError("PDF mode must never invoke the preprocessor.")


class PrepareDocumentsTest(unittest.TestCase):
    def test_pdf_selection_returns_sources_and_never_touches_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_root = Path(tmp) / "PDFs"
            source = pdf_root / "HCF" / "hospital" / "product.pdf"
            source.parent.mkdir(parents=True)
            source.touch()

            prepared = prepare_documents(
                ModelSelection("openai", "gpt-5", "pdf"),
                [source],
                pdf_root,
                preprocessor=ExplodingPreprocessor(),
            )

            self.assertEqual(prepared, (source,))
            self.assertFalse((Path(tmp) / "Markdown").exists())

    def test_markdown_selection_maps_every_nested_pdf_to_its_mirror(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_root = Path(tmp) / "PDFs"
            sources = [
                pdf_root / "HCF" / "hospital" / "gold.pdf",
                pdf_root / "Bupa" / "extras" / "nested" / "silver.pdf",
            ]
            for source in sources:
                source.parent.mkdir(parents=True)
                source.touch()
            preprocessor = FakePreprocessor()

            prepared = prepare_documents(
                ModelSelection("deepseek", "deepseek-chat", "markdown"),
                sources,
                pdf_root,
                preprocessor=preprocessor,
            )

            self.assertEqual(
                prepared,
                (
                    Path(tmp) / "Markdown" / "HCF" / "hospital" / "gold.md",
                    Path(tmp) / "Markdown" / "Bupa" / "extras" / "nested" / "silver.md",
                ),
            )
            self.assertEqual(len(preprocessor.calls), 2)
            for path in prepared:
                self.assertTrue(path.exists())

    def test_markdown_selection_reuses_cached_mirror_without_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_root = Path(tmp) / "PDFs"
            source = pdf_root / "HCF" / "extras" / "product.pdf"
            source.parent.mkdir(parents=True)
            source.touch()
            cached = markdown_path(source, pdf_root)
            cached.parent.mkdir(parents=True)
            cached.write_text("# cached\n", encoding="utf-8")
            preprocessor = FakePreprocessor()

            prepared = prepare_documents(
                ModelSelection("anthropic", "claude-test", "markdown"),
                [source],
                pdf_root,
                preprocessor=preprocessor,
            )

            self.assertEqual(prepared, (cached,))
            self.assertEqual(preprocessor.calls, [])

    def test_markdown_selection_requires_a_pdf_root(self) -> None:
        with self.assertRaisesRegex(ValueError, "PDF input root"):
            prepare_documents(
                ModelSelection("anthropic", "claude-test", "markdown"),
                [Path("sample.pdf")],
                None,
                preprocessor=FakePreprocessor(),
            )

    def test_markdown_conversion_failure_propagates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_root = Path(tmp) / "PDFs"
            source = pdf_root / "HCF" / "extras" / "product.pdf"
            source.parent.mkdir(parents=True)
            source.touch()

            with self.assertRaisesRegex(RuntimeError, "did not create"):
                prepare_documents(
                    ModelSelection("anthropic", "claude-test", "markdown"),
                    [source],
                    pdf_root,
                    preprocessor=FakePreprocessor(writes_output=False),
                )


if __name__ == "__main__":
    unittest.main()
