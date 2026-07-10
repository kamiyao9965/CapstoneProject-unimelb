from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.schema.sampler import select_samples


class SelectSamplesTest(unittest.TestCase):
    def test_excluded_pdfs_cannot_be_selected_for_holdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            used_pdf = root / "FundA" / "hospital" / "product.pdf"
            holdout_pdf = root / "FundB" / "hospital" / "product.pdf"
            used_pdf.parent.mkdir(parents=True)
            holdout_pdf.parent.mkdir(parents=True)
            used_pdf.touch()
            holdout_pdf.touch()

            selected = select_samples(
                input_root=root,
                categories=("hospital",),
                per_category=1,
                seed=7,
                exclude_paths=[used_pdf],
            )

            self.assertEqual(selected, [str(holdout_pdf)])


if __name__ == "__main__":
    unittest.main()
