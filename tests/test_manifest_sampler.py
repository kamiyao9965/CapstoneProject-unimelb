from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.schema.sampler import (
    load_document_manifest,
    manifest_family_ids,
    select_manifest_samples,
)


class ManifestSamplerTest(unittest.TestCase):
    def test_pet_manifest_keeps_verified_multi_plan_products_separate(self) -> None:
        manifest = load_document_manifest(
            Path(__file__).resolve().parents[1]
            / "configs"
            / "pet_insurance"
            / "document_manifest.json"
        )
        by_id = {
            str(document["document_id"]): document
            for document in manifest["documents"]
        }

        accident_plus = by_id["bow_wow_meow_accident_plus_pds_2023_06_14"]
        self.assertEqual(
            accident_plus["products"][0]["cover_scope"],
            "specified_conditions_only",
        )

        seniors = by_id[
            "australian_seniors_top_essential_accident_only_policy_booklet"
        ]
        self.assertEqual(
            [product["product_id"] for product in seniors["products"]],
            [
                "australian_seniors_top",
                "australian_seniors_essential",
                "australian_seniors_accident_only",
            ],
        )
        self.assertEqual(
            [product["cover_scope"] for product in seniors["products"]],
            ["accident_and_illness", "accident_and_illness", "accident_only"],
        )

        pd_insurance = by_id["pd_insurance_pet_pds_v6_6_2025_11_04"]
        self.assertEqual(
            [product["product_id"] for product in pd_insurance["products"]],
            [
                "pd_insurance_accident",
                "pd_insurance_classic",
                "pd_insurance_deluxe",
            ],
        )

    def test_balances_brand_and_role_and_excludes_families(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "raw"
            root.mkdir()
            documents = []
            rows = [
                ("a_pds.pdf", "A", "pds", "family_a"),
                ("a_update.pdf", "A", "update", "family_a"),
                ("b_pds.pdf", "B", "pds", "family_b"),
                ("c_booklet.pdf", "C", "policy_booklet", "family_c"),
            ]
            for name, brand, role, family in rows:
                path = root / name
                path.write_bytes(name.encode())
                documents.append({
                    "document_id": name,
                    "source_path": str(path),
                    "brand_hint": brand,
                    "document_role": role,
                    "document_family_id": family,
                    "amends_document_ids": [],
                    "discovery_eligible": role != "update",
                    "products": [],
                })
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(json.dumps({"documents": documents}), encoding="utf-8")

            discovery = select_manifest_samples(
                root, manifest, count=2, seed=3, roles=("pds", "policy_booklet")
            )
            holdout = select_manifest_samples(
                root,
                manifest,
                count=1,
                seed=4,
                roles=("pds", "policy_booklet"),
                exclude_paths=discovery,
            )

            discovery_families = manifest_family_ids(discovery, manifest, root)
            holdout_families = manifest_family_ids(holdout, manifest, root)
            self.assertEqual(len(discovery), 2)
            self.assertEqual(len(holdout), 1)
            self.assertTrue(discovery_families.isdisjoint(holdout_families))
            self.assertNotIn("a_update.pdf", {Path(path).name for path in discovery + holdout})

    def test_manifest_requires_unique_family_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "raw"
            root.mkdir()
            path = root / "only.pdf"
            path.write_bytes(b"only")
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(json.dumps({"documents": [{
                "document_id": "only",
                "source_path": str(path),
                "brand_hint": "A",
                "document_role": "pds",
                "document_family_id": "family",
                "discovery_eligible": True,
                "products": [],
            }]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "document families"):
                select_manifest_samples(root, manifest, count=2)


if __name__ == "__main__":
    unittest.main()
