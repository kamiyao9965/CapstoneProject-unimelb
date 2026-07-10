from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


if __name__ == "__main__":
    unittest.main()
