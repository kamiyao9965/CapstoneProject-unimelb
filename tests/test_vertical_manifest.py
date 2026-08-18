from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.verticals.manifest import (
    PROJECT_ROOT,
    ManifestValidationError,
    load_vertical_manifest,
)
from src.verticals.registry import get_acquisition_adapter


class VerticalManifestTest(unittest.TestCase):
    def test_private_health_manifest_preserves_pipeline_defaults(self) -> None:
        manifest = load_vertical_manifest(
            PROJECT_ROOT / "configs/private_health/manifest.json"
        )

        self.assertEqual(manifest.vertical, "private_health")
        self.assertTrue(manifest.supports("discovery"))
        self.assertTrue(manifest.supports("extraction"))
        self.assertFalse(manifest.supports("storage"))
        self.assertEqual(
            manifest.documents.categories,
            ("combined", "extras", "generalhealth", "hospital"),
        )
        self.assertEqual(
            manifest.contract("discovered_schema"),
            "private_health/discovered_schema",
        )
        self.assertEqual(
            manifest.path("output_root"),
            PROJECT_ROOT / "outputs/private_health",
        )

    def test_travel_manifest_declares_multi_product_acquisition_boundary(self) -> None:
        manifest = load_vertical_manifest(
            PROJECT_ROOT / "configs/travel_insurance/manifest.json"
        )

        self.assertEqual(manifest.vertical, "travel_insurance")
        self.assertTrue(manifest.supports("acquisition"))
        self.assertTrue(manifest.supports("discovery"))
        self.assertTrue(manifest.supports("extraction"))
        self.assertTrue(manifest.supports("storage"))
        self.assertFalse(manifest.supports("refinement"))
        self.assertEqual(manifest.documents.extraction_unit, "product_release")
        self.assertEqual(manifest.documents.output_cardinality, "multiple")
        self.assertEqual(
            manifest.path("acquisition_config"),
            PROJECT_ROOT / "configs/travel_insurance/sources.json",
        )
        self.assertEqual(
            manifest.path("storage_mapping"),
            PROJECT_ROOT / "configs/travel_insurance/storage_mapping.json",
        )

    def test_unknown_manifest_property_is_rejected(self) -> None:
        source = json.loads(
            (PROJECT_ROOT / "configs/private_health/manifest.json").read_text(
                encoding="utf-8"
            )
        )
        source["unexpected"] = True
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps(source), encoding="utf-8")

            with self.assertRaises(ManifestValidationError):
                load_vertical_manifest(path)

    def test_paths_cannot_escape_project_root(self) -> None:
        source = json.loads(
            (PROJECT_ROOT / "configs/private_health/manifest.json").read_text(
                encoding="utf-8"
            )
        )
        source["paths"]["output_root"] = "../outside"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps(source), encoding="utf-8")

            manifest = load_vertical_manifest(path)
            with self.assertRaises(ManifestValidationError):
                manifest.path("output_root")

    def test_environment_path_override_preserves_external_dataset_support(self) -> None:
        manifest = load_vertical_manifest(
            PROJECT_ROOT / "configs/private_health/manifest.json"
        )
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            "os.environ", {"KONKRD_DATA_ROOT": tmp}
        ):
            self.assertEqual(
                manifest.path("input_root"),
                Path(tmp).resolve() / "data/private_health/raw/PDFs",
            )

    def test_adapter_registry_rejects_unregistered_code(self) -> None:
        with self.assertRaises(ManifestValidationError):
            get_acquisition_adapter("arbitrary.module:function")


if __name__ == "__main__":
    unittest.main()
