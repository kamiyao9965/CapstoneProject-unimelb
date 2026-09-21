from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from src.PDFingestor.adapter import ingest_pdfs, render_documents_for_prompt
from src.PDFingestor.mineru import MinerUIngestor, content_list_to_pages, html_table_rows


CONTENT_LIST = [
    {"type": "header", "text": "Example Travel Insurance", "bbox": [80, 30, 400, 45], "page_idx": 0},
    {"type": "text", "text": "Combined FSG and PDS", "text_level": 1, "bbox": [80, 60, 700, 90], "page_idx": 0},
    {"type": "text", "text": "Cover starts when you leave home.", "bbox": [80, 100, 900, 140], "page_idx": 0},
    {
        "type": "table",
        "img_path": "images/table.png",
        "table_caption": ["Table of benefits"],
        "table_footnote": ["* Limits apply per person."],
        "table_body": (
            "<table><tr><td rowspan=2>Section</td><td colspan=2>Benefit limit</td></tr>"
            "<tr><td>Comprehensive</td><td>Essentials</td></tr>"
            "<tr><td>Medical</td><td>Unlimited</td><td>$1,000,000</td></tr></table>"
        ),
        "bbox": [80, 160, 920, 420],
        "page_idx": 1,
    },
    {
        "type": "list",
        "sub_type": "ref_text",
        "list_items": ["Pre-existing conditions", "Pregnancy"],
        "bbox": [80, 440, 900, 480],
        "page_idx": 1,
    },
    {"type": "image", "img_path": "images/logo.png", "image_caption": [], "image_footnote": [], "page_idx": 2},
    {"type": "page_number", "text": "3", "bbox": [480, 960, 520, 975], "page_idx": 2},
]


class FakeMinerURunner:
    """Writes a MinerU-shaped output tree instead of running the real worker."""

    def __init__(self, content_list: list[dict] | None = None, *, writes_output: bool = True) -> None:
        self.content_list = CONTENT_LIST if content_list is None else content_list
        self.writes_output = writes_output
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        parse_dir = Path(command[4]) / "document" / "auto"
        if self.writes_output:
            parse_dir.mkdir(parents=True)
            (parse_dir / "document_content_list.json").write_text(
                json.dumps(self.content_list), encoding="utf-8"
            )
            (parse_dir / "document_content_list_v2.json").write_text("[]", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")


class MinerUContentListTest(unittest.TestCase):
    def test_content_list_becomes_pages_with_markdown_tables(self) -> None:
        pages = content_list_to_pages(CONTENT_LIST)

        self.assertEqual([page.page_num for page in pages], [1, 2, 3])
        self.assertEqual(
            [block.content for block in pages[0].blocks],
            ["Example Travel Insurance", "# Combined FSG and PDS", "Cover starts when you leave home."],
        )
        table = pages[1].blocks[0]
        self.assertEqual(table.type, "table")
        self.assertEqual(table.table_id, "p2-t1")
        self.assertEqual(table.source_engine, "mineru")
        self.assertEqual(table.caption_context, {"summary": "Table of benefits"})
        self.assertEqual(
            table.markdown,
            "| Section | Benefit limit | Benefit limit |\n"
            "| --- | --- | --- |\n"
            "| Section | Comprehensive | Essentials |\n"
            "| Medical | Unlimited | $1,000,000 |",
        )
        self.assertEqual(
            [block.content for block in pages[1].blocks[1:]],
            ["* Limits apply per person.", "- Pre-existing conditions\n- Pregnancy"],
        )
        self.assertEqual([block.content for block in pages[2].blocks], ["3"])

    def test_rendered_prompt_uses_the_shared_page_and_table_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "travel.pdf"
            pdf.write_bytes(b"%PDF-1.7 fixture")
            parsed = MinerUIngestor(runner=FakeMinerURunner(), python_executable="python").ingest(pdf)

        rendered = render_documents_for_prompt([parsed])

        self.assertIn("<!-- page 2 -->", rendered)
        self.assertIn("table table_id=p2-t1 source=mineru", rendered)
        self.assertIn("<!-- table_context: Table of benefits -->", rendered)
        self.assertIn("| Medical | Unlimited | $1,000,000 |", rendered)

    def test_html_rows_fill_spans_and_gaps(self) -> None:
        self.assertEqual(
            html_table_rows(
                "<table><tr><td>A</td><td rowspan='2'>B</td></tr><tr><td>C</td></tr>"
                "<tr><td>X</td><td>Y</td><td rowspan=2>Z</td></tr><tr></tr></table>"
            ),
            [["A", "B"], ["C", "B"], ["X", "Y", "Z"], ["", "", "Z"]],
        )

    def test_item_without_page_index_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "page_idx"):
            content_list_to_pages([{"type": "text", "text": "No page"}])


class MinerUIngestorTest(unittest.TestCase):
    def test_runs_local_pipeline_backend_and_reuses_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "travel.pdf"
            pdf.write_bytes(b"%PDF-1.7 fixture")
            runner = FakeMinerURunner()
            ingestor = MinerUIngestor(Path(tmp) / "cache", runner=runner, python_executable="/venv/bin/python")

            first = ingestor.ingest(pdf)
            second = ingestor.ingest(pdf)

        self.assertEqual(len(runner.calls), 1)
        command, kwargs = runner.calls[0]
        self.assertEqual(command[:2], ["/venv/bin/python", "-c"])
        self.assertIn("do_parse", command[2])
        self.assertEqual(command[3], str(pdf))
        self.assertEqual(command[5:], ["pipeline", "auto", "ch"])
        self.assertTrue(kwargs["check"])
        self.assertIn("timeout", kwargs)
        self.assertEqual(first.parser["engine"], "mineru")
        self.assertEqual(second.model_dump(), first.model_dump())

    def test_document_without_content_fails_and_is_not_cached(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "scan.pdf"
            pdf.write_bytes(b"%PDF-1.7 fixture")
            cache_dir = Path(tmp) / "cache"
            runner = FakeMinerURunner([{"type": "image", "img_path": "images/page.png", "page_idx": 0}])
            ingestor = MinerUIngestor(cache_dir, runner=runner, python_executable="python")

            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, "no text or table content"):
                    ingestor.ingest(pdf)

            self.assertEqual(len(runner.calls), 2)
            self.assertEqual(list(cache_dir.rglob("*.json")), [])

    def test_missing_interpreter_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "travel.pdf"
            pdf.write_bytes(b"%PDF-1.7 fixture")
            ingestor = MinerUIngestor(
                runner=mock.Mock(side_effect=FileNotFoundError("python")),
                python_executable="missing-python",
            )

            with self.assertRaisesRegex(RuntimeError, "Cannot start the MinerU worker"):
                ingestor.ingest(pdf)

    def test_worker_failure_reports_exit_code_and_last_error_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "travel.pdf"
            pdf.write_bytes(b"%PDF-1.7 fixture")
            stderr = "loading models\nValueError: bad pdf\rProcessing pages: 100%|##########| 40/40\n"
            error = subprocess.CalledProcessError(2, ["python"], output="", stderr=stderr)
            ingestor = MinerUIngestor(runner=mock.Mock(side_effect=error), python_executable="python")

            with self.assertRaisesRegex(RuntimeError, "exit code 2: ValueError: bad pdf"):
                ingestor.ingest(pdf)

    def test_timeout_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "travel.pdf"
            pdf.write_bytes(b"%PDF-1.7 fixture")
            error = subprocess.TimeoutExpired(["python"], 5)
            ingestor = MinerUIngestor(
                runner=mock.Mock(side_effect=error), python_executable="python", timeout_seconds=5
            )

            with self.assertRaisesRegex(RuntimeError, "timed out after 5 seconds"):
                ingestor.ingest(pdf)

    def test_missing_content_list_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "travel.pdf"
            pdf.write_bytes(b"%PDF-1.7 fixture")
            ingestor = MinerUIngestor(
                runner=FakeMinerURunner(writes_output=False), python_executable="python"
            )

            with self.assertRaisesRegex(RuntimeError, "produced 0 content lists"):
                ingestor.ingest(pdf)


class MinerUWorkerScriptTest(unittest.TestCase):
    """Runs the real worker script against a stand-in mineru package."""

    def test_worker_calls_do_parse_for_the_content_list_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "fake" / "mineru" / "cli"
            package.mkdir(parents=True)
            (root / "fake" / "mineru" / "__init__.py").write_text("", encoding="utf-8")
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "common.py").write_text(
                textwrap.dedent(
                    """
                    import json
                    from pathlib import Path

                    def do_parse(output_dir, names, pdf_bytes_list, langs, **kwargs):
                        target = Path(output_dir) / names[0] / kwargs["parse_method"]
                        target.mkdir(parents=True)
                        summary = (
                            f"{len(pdf_bytes_list[0])} bytes {langs[0]} {kwargs['backend']} "
                            f"md={kwargs['f_dump_md']} content_list={kwargs['f_dump_content_list']}"
                        )
                        (target / f"{names[0]}_content_list.json").write_text(
                            json.dumps([{"type": "text", "text": summary, "page_idx": 0}])
                        )
                    """
                ),
                encoding="utf-8",
            )
            pdf = root / "travel.pdf"
            pdf.write_bytes(b"%PDF-1.7 fixture")
            environment = {**os.environ, "PYTHONPATH": str(root / "fake")}

            def runner(command, **kwargs):
                return subprocess.run(command, env=environment, **kwargs)

            parsed = MinerUIngestor(runner=runner).ingest(pdf)

        self.assertEqual(
            parsed.pages[0].blocks[0].content,
            "16 bytes ch pipeline md=False content_list=True",
        )

    def test_worker_reports_missing_mineru_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "travel.pdf"
            pdf.write_bytes(b"%PDF-1.7 fixture")

            def runner_without_site_packages(command, **kwargs):
                return subprocess.run([command[0], "-S", *command[1:]], **kwargs)

            ingestor = MinerUIngestor(runner=runner_without_site_packages, python_executable=sys.executable)

            with self.assertRaisesRegex(RuntimeError, "MinerU is not installed"):
                ingestor.ingest(pdf)


class DocumentParserRoutingTest(unittest.TestCase):
    def test_mineru_choice_uses_mineru_ingestor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "travel.pdf"
            pdf.write_bytes(b"%PDF-1.7 fixture")
            with mock.patch("src.PDFingestor.adapter.MinerUIngestor") as factory, \
                 mock.patch("src.PDFingestor.adapter.build_ingestor") as default_factory:
                ingest_pdfs([pdf], cache_dir=Path(tmp) / "cache", document_parser="mineru")

        factory.assert_called_once_with(Path(tmp) / "cache")
        factory.return_value.ingest.assert_called_once_with(pdf)
        default_factory.assert_not_called()

    def test_default_choice_keeps_pdfingestor(self) -> None:
        with mock.patch("src.PDFingestor.adapter.MinerUIngestor") as factory, \
             mock.patch("src.PDFingestor.adapter.build_ingestor") as default_factory:
            ingest_pdfs([], cache_dir="cache")

        default_factory.assert_called_once_with("cache", camelot_enabled=True)
        factory.assert_not_called()

    def test_unknown_parser_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported document parser 'ocr'"):
            ingest_pdfs([], document_parser="ocr")


if __name__ == "__main__":
    unittest.main()
