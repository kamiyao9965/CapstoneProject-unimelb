from __future__ import annotations

import copy
import tempfile
import unittest
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from src.verticals.documents import resolve_document_groups
from src.verticals.manifest import VerticalManifest, resolve_manifest


class DocumentGroupingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name).resolve() / "inputs"
        self.root.mkdir()
        self.paths = {}
        for name in ("A", "B", "C"):
            path = self.root / f"{name.lower()}.pdf"
            path.write_bytes(f"%PDF-1.4\nSynthetic {name} fixture\n%%EOF\n".encode())
            self.paths[name] = path

    def document(
        self,
        document_id: str,
        *,
        role: str = "pds",
        amends: tuple[str, ...] = (),
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        path = self.paths[document_id]
        return {
            "document_id": document_id,
            "source_path": path.name,
            "document_role": role,
            "amends_document_ids": list(amends),
            "sha256": sha256(path.read_bytes()).hexdigest(),
            "metadata": copy.deepcopy(metadata or {}),
        }

    def manifest(self, *inventory: dict[str, object]) -> VerticalManifest:
        manifest = resolve_manifest(vertical="travel_insurance")
        documents = replace(manifest.documents, inventory=tuple(copy.deepcopy(inventory)))
        return replace(manifest, documents=documents)

    def resolve(self, manifest: VerticalManifest, *ids: str):
        return resolve_document_groups(
            manifest, [self.paths[document_id] for document_id in ids], input_root=self.root,
        )

    def test_only_explicit_amendment_links_join_documents(self) -> None:
        manifest = self.manifest(
            self.document("A"), self.document("B", role="spds", amends=("A",)), self.document("C"),
        )
        original = copy.deepcopy(manifest.documents.inventory)
        groups = self.resolve(manifest, "A", "B", "C")
        by_ids = {tuple(document.document_id for document in group.documents): group for group in groups}
        self.assertEqual(set(by_ids), {("A", "B"), ("C",)})
        self.assertEqual(by_ids[("A", "B")].pdf_paths, (self.paths["A"], self.paths["B"]))
        self.assertEqual(by_ids[("A", "B")].primary_path, self.paths["A"])
        self.assertEqual(len({group.group_id for group in groups}), 2)
        self.assertTrue(all(isinstance(group.group_id, str) and group.group_id for group in groups))
        self.assertEqual(manifest.documents.inventory, original)

    def test_equal_product_brand_or_family_metadata_never_joins_unlinked_pds(self) -> None:
        metadata = {
            "product_id": "same_product",
            "products": [{"product_id": "same_product"}],
            "brand": "Same insurer",
            "document_family_id": "same_family",
        }
        manifest = self.manifest(
            self.document("A", metadata=metadata), self.document("B", metadata=metadata),
        )
        groups = self.resolve(manifest, "A", "B")
        self.assertEqual({group.pdf_paths for group in groups}, {(self.paths["A"],), (self.paths["B"],)})

    def test_selecting_base_collects_associated_updates_but_not_unrelated_pds(self) -> None:
        manifest = self.manifest(
            self.document("A"), self.document("B", role="spds", amends=("A",)), self.document("C"),
        )
        groups = self.resolve(manifest, "A")
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].pdf_paths, (self.paths["A"], self.paths["B"]))

    def test_selecting_update_also_collects_its_base(self) -> None:
        manifest = self.manifest(self.document("A"), self.document("B", role="update", amends=("A",)))
        groups = self.resolve(manifest, "B")
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].pdf_paths, (self.paths["A"], self.paths["B"]))
        self.assertEqual(groups[0].primary_path, self.paths["A"])

    def test_update_chains_follow_topology_even_when_inventory_and_dates_disagree(self) -> None:
        manifest = self.manifest(
            self.document("C", role="update", amends=("B",), metadata={"document_date": "2024-01-01"}),
            self.document("B", role="spds", amends=("A",), metadata={"document_date": "2026-01-01"}),
            self.document("A", metadata={"document_date": "2025-01-01"}),
        )
        groups = self.resolve(manifest, "C", "B", "A")
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].pdf_paths, (self.paths["A"], self.paths["B"], self.paths["C"]))
        self.assertEqual(tuple(document.document_id for document in groups[0].documents), ("A", "B", "C"))

    def test_explicit_update_can_join_two_base_documents(self) -> None:
        manifest = self.manifest(
            self.document("A"), self.document("B"), self.document("C", role="spds", amends=("A", "B")),
        )
        groups = self.resolve(manifest, "A")
        self.assertEqual(len(groups), 1)
        self.assertEqual(set(groups[0].pdf_paths), set(self.paths.values()))
        self.assertEqual(groups[0].documents[-1].document_id, "C")

    def test_group_identity_and_order_do_not_depend_on_selection_or_inventory_order(self) -> None:
        inventory = (self.document("A"), self.document("B", role="spds", amends=("A",)))
        first = self.resolve(self.manifest(*inventory), "A", "B", "A")
        second = self.resolve(self.manifest(*reversed(inventory)), "B", "A")
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(first[0].group_id, second[0].group_id)
        self.assertEqual(first[0].pdf_paths, second[0].pdf_paths)
        self.assertEqual(first[0].primary_path, second[0].primary_path)

    def test_group_references_preserve_declared_metadata_and_verified_hashes(self) -> None:
        metadata = {"document_date": "2026-10-01", "brand": "Example insurer"}
        base = self.document("A", metadata=metadata)
        update = self.document("B", role="spds", amends=("A",))
        group = self.resolve(self.manifest(base, update), "A")[0]
        by_id = {document.document_id: document for document in group.documents}
        self.assertEqual(by_id["A"].metadata, metadata)
        self.assertEqual(by_id["A"].sha256, base["sha256"])
        self.assertEqual(by_id["B"].document_role, "spds")
        self.assertEqual(tuple(by_id["B"].amends_document_ids), ("A",))

    def test_missing_amended_document_id_is_rejected(self) -> None:
        manifest = self.manifest(self.document("A"), self.document("B", role="spds", amends=("missing",)))
        with self.assertRaises(ValueError):
            self.resolve(manifest, "A", "B")

    def test_self_references_and_amendment_cycles_are_rejected(self) -> None:
        for inventory in (
            (self.document("A", amends=("A",)),),
            (self.document("A", amends=("B",)), self.document("B", role="spds", amends=("A",))),
        ):
            with self.subTest(inventory=inventory):
                with self.assertRaises(ValueError):
                    self.resolve(self.manifest(*inventory), "A")

    def test_unlinked_spds_and_updates_are_rejected_instead_of_extracted_alone(self) -> None:
        for role in ("spds", "update", "supplementary_pds"):
            with self.subTest(role=role):
                manifest = self.manifest(self.document("B", role=role))
                with self.assertRaises(ValueError):
                    self.resolve(manifest, "B")

    def test_inventory_path_cannot_escape_input_root(self) -> None:
        outside = self.root.parent / "outside.pdf"
        outside.write_bytes(b"%PDF-1.4\nOutside synthetic fixture\n%%EOF\n")
        document = self.document("A")
        document["source_path"] = "../outside.pdf"
        document["sha256"] = sha256(outside.read_bytes()).hexdigest()
        with self.assertRaises(ValueError):
            resolve_document_groups(self.manifest(document), [outside], input_root=self.root)

    def test_inventory_symlink_cannot_escape_input_root(self) -> None:
        outside = self.root.parent / "outside.pdf"
        outside.write_bytes(b"%PDF-1.4\nOutside synthetic fixture\n%%EOF\n")
        link = self.root / "linked.pdf"
        link.symlink_to(outside)
        document = self.document("A")
        document["source_path"] = link.name
        document["sha256"] = sha256(outside.read_bytes()).hexdigest()
        with self.assertRaises(ValueError):
            resolve_document_groups(self.manifest(document), [link], input_root=self.root)

    def test_mismatched_declared_hash_is_rejected(self) -> None:
        document = self.document("A")
        document["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.resolve(self.manifest(document), "A")

    def test_missing_linked_pdf_is_rejected(self) -> None:
        manifest = self.manifest(self.document("A"), self.document("B", role="spds", amends=("A",)))
        self.paths["B"].unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.resolve(manifest, "A")

    def test_empty_inventory_retains_independent_single_pdf_tasks(self) -> None:
        groups = self.resolve(self.manifest(), "A", "B")
        self.assertEqual({group.pdf_paths for group in groups}, {(self.paths["A"],), (self.paths["B"],)})
        self.assertTrue(all(group.primary_path == group.pdf_paths[0] for group in groups))

    def test_unregistered_amendment_folder_requires_explicit_inventory_link(self) -> None:
        folder = self.root / "insurer" / "SPDS"
        folder.mkdir(parents=True)
        path = folder / "synthetic.pdf"
        path.write_bytes(self.paths["B"].read_bytes())
        with self.assertRaisesRegex(ValueError, "Unlinked SPDS"):
            resolve_document_groups(self.manifest(), [path], input_root=self.root)

    def test_sample_groups_cannot_share_secondary_pdf_content_with_holdout_or_each_other(self) -> None:
        from src.schema.sampler import select_samples
        self.paths["D"] = self.root / "d.pdf"
        self.paths["D"].write_bytes(self.paths["B"].read_bytes())
        manifest = self.manifest(
            self.document("A", metadata={"insurer_code": "one"}),
            self.document("B", role="spds", amends=("A",)),
            self.document("C", metadata={"insurer_code": "two"}),
            self.document("D", role="spds", amends=("C",)),
        )
        for count, excluded in ((1, [self.paths["A"]]), (2, [])):
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, "Not enough unique PDFs"):
                select_samples(self.root, ("pds",), per_category=count, seed=42,
                               manifest=manifest, exclude_paths=excluded)
if __name__ == "__main__":
    unittest.main()
