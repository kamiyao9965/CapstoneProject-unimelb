from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.schema.sampler import collect_candidates, select_samples


class SelectSamplesTest(unittest.TestCase):
    def test_excluded_pdfs_cannot_be_selected_for_holdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            used_pdf = root / "FundA" / "hospital" / "product.pdf"
            holdout_pdf = root / "FundB" / "hospital" / "product.pdf"
            used_pdf.parent.mkdir(parents=True)
            holdout_pdf.parent.mkdir(parents=True)
            used_pdf.write_bytes(b"used-policy")
            holdout_pdf.write_bytes(b"holdout-policy")

            selected = select_samples(
                input_root=root,
                categories=("hospital",),
                per_category=1,
                seed=7,
                exclude_paths=[used_pdf],
            )

            self.assertEqual(selected, [str(holdout_pdf)])

    def test_content_duplicate_of_excluded_pdf_cannot_enter_holdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            used_pdf = root / "FundA" / "hospital" / "used.pdf"
            duplicate_pdf = root / "FundB" / "hospital" / "duplicate.pdf"
            for path, content in (
                (used_pdf, b"same-policy"),
                (duplicate_pdf, b"same-policy"),
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)

            with self.assertRaisesRegex(ValueError, "Not enough unique PDFs"):
                select_samples(
                    input_root=root,
                    categories=("hospital",),
                    per_category=1,
                    seed=7,
                    exclude_paths=[used_pdf],
                )

    def test_one_sample_sweep_never_selects_duplicate_content_twice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = (
                (root / "FundA" / "hospital" / "a.pdf", b"shared"),
                (root / "FundB" / "hospital" / "b.pdf", b"shared"),
            )
            for path, content in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)

            with self.assertRaisesRegex(ValueError, "Not enough unique PDFs"):
                select_samples(
                    input_root=root,
                    categories=("hospital",),
                    per_category=2,
                    seed=1,
                )

    def test_product_type_override_moves_candidate_to_effective_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            override_path = root / "overrides.json"
            mislabeled = root / "NTF" / "hospital" / "top-extras.pdf"
            mislabeled.parent.mkdir(parents=True)
            mislabeled.write_bytes(b"extras-policy")
            override_path.write_text(
                '{"NTF/hospital/top-extras.pdf": "extras"}',
                encoding="utf-8",
            )

            candidates = collect_candidates(
                root,
                ("extras", "hospital"),
                product_type_overrides_path=override_path,
            )

            self.assertEqual(candidates["extras"]["NTF"], [mislabeled])
            self.assertEqual(candidates["hospital"], {})


if __name__ == "__main__":
    unittest.main()
