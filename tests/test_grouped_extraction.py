from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from src.common.model_config import ModelSelection
from src.common.model_provider import ModelResponse, ProviderRequest
from src.common.models import ExtractionResult
from src.schema_application.extractor import SchemaExtractor
from src.verticals.documents import resolve_document_groups
from src.verticals.manifest import VerticalManifest, resolve_manifest
from tests.test_nested_contracts import closed_benefit_item_schema, discovered_travel_schema


GROUPED_RECORD = {
    "products": [{
        "product_name": "Example plan", "product_type": "domestic", "benefits": [],
        "_unfilled": [], "_notes": None,
    }],
    "_document_notes": None,
}
GROUPED_SELECTION = ModelSelection("openai", "gpt-5", "markdown")


def grouped_fixture(
    input_root: Path, *, linked: bool = True, inventory: bool = True,
) -> tuple[VerticalManifest, dict[str, Path], dict[str, object]]:
    input_root.mkdir(parents=True, exist_ok=True)
    paths = {}
    for document_id in ("A", "B", "C"):
        paths[document_id] = input_root / f"{document_id.lower()}.pdf"
        paths[document_id].write_bytes(f"%PDF-1.4\nSynthetic {document_id} fixture\n%%EOF\n".encode())
    entries = []
    for document_id in ("A", "B"):
        entries.append({
            "document_id": document_id,
            "source_path": paths[document_id].name,
            "document_role": "spds" if document_id == "B" and linked else "pds",
            "amends_document_ids": ["A"] if document_id == "B" and linked else [],
            "metadata": {"product_id": "same_product", "brand": "same_brand"},
        })
    manifest = resolve_manifest(vertical="travel_insurance")
    manifest = replace(manifest, documents=replace(manifest.documents, inventory=tuple(entries) if inventory else ()))
    schema = discovered_travel_schema()
    schema["fields"][-1]["item_schema"] = closed_benefit_item_schema()
    return manifest, paths, schema


def render_document_headers(pdf_paths, **kwargs) -> str:
    return "\n\n".join(f"# PDF: {Path(path).name}\nSource text for {Path(path).name}." for path in pdf_paths)


class GroupRecordingProvider:
    def __init__(self, *, fail: bool = False) -> None:
        self.requests: list[ProviderRequest] = []
        self.fail = fail

    def generate(self, request: ProviderRequest) -> ModelResponse:
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("synthetic provider failure")
        return ModelResponse(
            text=json.dumps(GROUPED_RECORD), provider=request.selection.provider, model=request.selection.model,
        )


class GroupedExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.input_root = self.root / "inputs"
        self.manifest, self.paths, self.schema = grouped_fixture(self.input_root)

    def extractor(self, provider: GroupRecordingProvider, *, manifest=None) -> SchemaExtractor:
        return SchemaExtractor(
            self.schema, selection=GROUPED_SELECTION, provider=provider,
            manifest=manifest or self.manifest, pdf_root=self.input_root,
            pdfingestor_cache_dir=self.root / "cache", parsed_markdown_dir=self.root / "markdown",
            usage_log_path=None, log=None,
        )

    def test_extract_group_sends_base_and_amendment_in_the_same_request(self) -> None:
        group = resolve_document_groups(self.manifest, [self.paths["A"]], input_root=self.input_root)[0]
        provider = GroupRecordingProvider()
        with mock.patch(
            "src.schema_application.extractor.render_pdf_paths_for_prompt", side_effect=render_document_headers,
        ):
            data = self.extractor(provider).extract_group(group, run_id="synthetic_group_run")
        self.assertEqual(data, GROUPED_RECORD)
        self.assertEqual(len(provider.requests), 1)
        self.assertIn("# PDF: a.pdf", provider.requests[0].user_text)
        self.assertIn("# PDF: b.pdf", provider.requests[0].user_text)
        self.assertTrue(provider.requests[0].structured_output.strict)

    def test_extract_one_collects_the_selected_pds_amendment(self) -> None:
        provider = GroupRecordingProvider()
        with mock.patch(
            "src.schema_application.extractor.render_pdf_paths_for_prompt", side_effect=render_document_headers,
        ):
            self.extractor(provider).extract_one(self.paths["A"])
        self.assertEqual(len(provider.requests), 1)
        self.assertIn("# PDF: a.pdf", provider.requests[0].user_text)
        self.assertIn("# PDF: b.pdf", provider.requests[0].user_text)

    def test_extract_many_emits_one_artifact_with_complete_group_provenance(self) -> None:
        group = resolve_document_groups(self.manifest, [self.paths["A"]], input_root=self.input_root)[0]
        provider = GroupRecordingProvider()
        with mock.patch(
            "src.schema_application.extractor.render_pdf_paths_for_prompt", side_effect=render_document_headers,
        ):
            written = self.extractor(provider).extract_many([self.paths["A"], self.paths["B"]], self.root / "results")
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(len(written), 1)
        artifact = json.loads(written[0].read_text(encoding="utf-8"))
        provenance = artifact["provenance"]
        self.assertEqual(provenance["source_documents"], [str(path) for path in group.pdf_paths])
        self.assertEqual(provenance["source_document_refs"], [document.to_dict() for document in group.documents])
        self.assertEqual(provenance["document_group_id"], group.group_id)
        self.assertEqual({ref["document_id"] for ref in provenance["source_document_refs"]}, {"A", "B"})
        self.assertTrue(all(len(ref["sha256"]) == 64 for ref in provenance["source_document_refs"]))

    def test_extract_many_keeps_unlinked_equal_product_ids_in_separate_requests(self) -> None:
        documents = list(copy.deepcopy(self.manifest.documents.inventory))
        documents[1].update(document_role="pds", amends_document_ids=[])
        manifest = replace(self.manifest, documents=replace(self.manifest.documents, inventory=tuple(documents)))
        provider = GroupRecordingProvider()
        with mock.patch(
            "src.schema_application.extractor.render_pdf_paths_for_prompt", side_effect=render_document_headers,
        ):
            written = self.extractor(provider, manifest=manifest).extract_many(
                [self.paths["A"], self.paths["B"]], self.root / "results",
            )
        self.assertEqual(len(written), 2)
        self.assertEqual(len(provider.requests), 2)
        self.assertTrue(all(request.user_text.count("# PDF:") == 1 for request in provider.requests))

    def test_empty_inventory_preserves_single_pdf_extraction(self) -> None:
        manifest = replace(self.manifest, documents=replace(self.manifest.documents, inventory=()))
        provider = GroupRecordingProvider()
        with mock.patch(
            "src.schema_application.extractor.render_pdf_paths_for_prompt", side_effect=render_document_headers,
        ):
            data = self.extractor(provider, manifest=manifest).extract_one(self.paths["A"])
        self.assertEqual(data, GROUPED_RECORD)
        self.assertEqual(len(provider.requests), 1)
        self.assertIn("# PDF: a.pdf", provider.requests[0].user_text)
        self.assertNotIn("# PDF: b.pdf", provider.requests[0].user_text)

    def test_failure_artifact_keeps_all_group_documents_and_refs(self) -> None:
        group = resolve_document_groups(self.manifest, [self.paths["A"]], input_root=self.input_root)[0]
        provider = GroupRecordingProvider(fail=True)
        output = self.root / "results"
        with mock.patch(
            "src.schema_application.extractor.render_pdf_paths_for_prompt", side_effect=render_document_headers,
        ):
            with self.assertRaises(RuntimeError):
                self.extractor(provider).extract_many([self.paths["A"]], output)
        errors = list((output / "errors" / "extraction").glob("*.json"))
        self.assertEqual(len(errors), 1)
        provenance = json.loads(errors[0].read_text(encoding="utf-8"))["provenance"]
        self.assertEqual(provenance["source_documents"], [str(path) for path in group.pdf_paths])
        self.assertEqual(provenance["source_document_refs"], [document.to_dict() for document in group.documents])
        self.assertEqual(provenance["document_group_id"], group.group_id)

    def test_native_result_round_trip_keeps_group_identity_and_primary_source(self) -> None:
        group = resolve_document_groups(self.manifest, [self.paths["A"]], input_root=self.input_root)[0]
        result = ExtractionResult(
            vertical=self.manifest.vertical, schema_version=str(self.schema["version"]),
            source_path=str(group.primary_path), source_documents=[str(path) for path in group.pdf_paths],
            source_document_refs=[document.to_dict() for document in group.documents], document_group_id=group.group_id,
            provider=GROUPED_SELECTION.provider, model=GROUPED_SELECTION.model, document_parser="pdfingestor",
            data=copy.deepcopy(GROUPED_RECORD),
        )
        path = result.write_json(self.root / "native_result.json")
        reloaded = ExtractionResult.model_validate_json(path.read_bytes())
        self.assertEqual(reloaded.source_path, str(group.primary_path))
        self.assertEqual(reloaded.source_documents, [str(path) for path in group.pdf_paths])
        self.assertEqual(reloaded.source_document_refs, [document.to_dict() for document in group.documents])
        self.assertEqual(reloaded.document_group_id, group.group_id)


if __name__ == "__main__":
    unittest.main()
