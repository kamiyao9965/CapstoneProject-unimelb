from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import run as cli
from src.common.model_config import ModelSelection
from src.common.model_provider import ModelResponse, ProviderRequest
from src.common.structured_output import StructuredOutputFailure
from src.refine.consensus import SchemaConsensusRefinement
from src.schema.discovery import SchemaDiscovery
from src.verticals.manifest import resolve_manifest
from tests.test_nested_contracts import closed_benefit_item_schema
from tests.test_schema_generation import generation_patch_payload, generation_payload


def native_generation_schema(vertical: str = "travel_insurance") -> dict[str, object]:
    wire = generation_payload(vertical)
    native = {key: copy.deepcopy(wire[key]) for key in (
        "vertical", "version", "description", "product_types", "fields", "taxonomies", "notes",
    )}
    for field in native["fields"]:
        field.pop("item_node_id")
        if field["type"] == "list[object]":
            field["item_schema"] = closed_benefit_item_schema()
    return native


class GenerationRecordingProvider:
    def __init__(self, responses: list[dict[str, object]], *, adaptive_native=None) -> None:
        self.responses = copy.deepcopy(responses)
        self.adaptive_native = copy.deepcopy(adaptive_native)
        self.requests: list[ProviderRequest] = []

    def generate(self, request: ProviderRequest) -> ModelResponse:
        self.requests.append(request)
        payload = self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]
        if self.adaptive_native is not None and "schema_nodes" not in request.structured_output.schema["properties"]:
            payload = self.adaptive_native
        return ModelResponse(
            text=json.dumps(payload), provider=request.selection.provider, model=request.selection.model,
        )


class StrictDiscoveryGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.pdf = self.root / "synthetic.pdf"
        self.pdf.write_bytes(b"%PDF-1.4\nSynthetic discovery fixture\n%%EOF\n")
        self.renderer = mock.patch(
            "src.schema.discovery.render_pdf_paths_for_prompt", return_value="Synthetic source text.",
        )
        self.renderer.start()
        self.addCleanup(self.renderer.stop)

    def discovery(self, provider: GenerationRecordingProvider, *, vertical="travel_insurance") -> SchemaDiscovery:
        return SchemaDiscovery(
            selection=ModelSelection("openai", "gpt-5", "markdown"), provider=provider,
            manifest=resolve_manifest(vertical=vertical), log=None,
            usage_log_path=self.root / "usage.jsonl", pdf_root=self.root,
            pdfingestor_cache_dir=self.root / "cache", parsed_markdown_dir=self.root / "markdown",
        )

    def test_discovery_requests_use_strict_generation_contract(self) -> None:
        for vertical in ("private_health", "travel_insurance"):
            with self.subTest(vertical=vertical):
                provider = GenerationRecordingProvider(
                    [generation_payload(vertical)], adaptive_native=native_generation_schema(vertical),
                )
                self.discovery(provider, vertical=vertical).discover([str(self.pdf)])
                self.assertEqual(len(provider.requests), 1)
                self.assertTrue(provider.requests[0].structured_output.strict)
                self.assertIn("schema_nodes", provider.requests[0].structured_output.schema["properties"])

    def test_patch_requests_use_strict_generation_contract(self) -> None:
        for vertical in ("private_health", "travel_insurance"):
            with self.subTest(vertical=vertical):
                wire = generation_patch_payload(vertical)
                native = {"patches": [copy.deepcopy(wire["patches"][0])]}
                native["patches"][0].pop("item_node_id")
                native["patches"][0]["item_schema"] = closed_benefit_item_schema()
                provider = GenerationRecordingProvider([wire], adaptive_native=native)
                self.discovery(provider, vertical=vertical).discover_patches(
                    [str(self.pdf)], native_generation_schema(vertical),
                )
                self.assertEqual(len(provider.requests), 1)
                self.assertTrue(provider.requests[0].structured_output.strict)
                self.assertIn("schema_nodes", provider.requests[0].structured_output.schema["properties"])

    def test_discovery_returns_decoded_native_schema_for_both_verticals(self) -> None:
        for vertical in ("private_health", "travel_insurance"):
            with self.subTest(vertical=vertical):
                provider = GenerationRecordingProvider([generation_payload(vertical)])
                decoded = self.discovery(provider, vertical=vertical).discover([str(self.pdf)])
                self.assertNotIn("schema_nodes", decoded)
                self.assertNotIn("item_node_id", decoded["fields"][-1])
                self.assertIn("item_schema", decoded["fields"][-1])
                self.assertIs(decoded["fields"][-1]["item_schema"]["additionalProperties"], False)

    def test_bad_node_reference_or_type_uses_one_repair_then_decodes(self) -> None:
        for defect in ("missing_reference", "type_conflict"):
            with self.subTest(defect=defect):
                invalid = generation_payload()
                if defect == "missing_reference":
                    invalid["schema_nodes"][0]["properties"][0]["node_id"] = "missing_node"
                else:
                    invalid["schema_nodes"][1]["minimum"] = 0
                provider = GenerationRecordingProvider([invalid, generation_payload()])
                decoded = self.discovery(provider).discover([str(self.pdf)])
                self.assertEqual(len(provider.requests), 2)
                self.assertTrue(all(request.structured_output.strict for request in provider.requests))
                self.assertIn("item_schema", decoded["fields"][-1])
                attempts = [json.loads(line) for line in (self.root / "usage.jsonl").read_text().splitlines()][-2:]
                self.assertEqual([item["attempt_number"] for item in attempts], [1, 2])
                self.assertEqual([item["validation_succeeded"] for item in attempts], [False, True])

    def test_discovery_and_patch_decode_failures_stop_after_three_requests(self) -> None:
        for patches in (False, True):
            with self.subTest(patches=patches):
                invalid = generation_patch_payload() if patches else generation_payload()
                invalid["schema_nodes"][0]["properties"][0]["node_id"] = "missing_node"
                provider = GenerationRecordingProvider([invalid])
                engine = self.discovery(provider)
                with self.assertRaises(StructuredOutputFailure) as caught:
                    if patches:
                        engine.discover_patches([str(self.pdf)], native_generation_schema())
                    else:
                        engine.discover([str(self.pdf)])
                self.assertEqual(len(provider.requests), 3)
                self.assertEqual(len(caught.exception.result.attempts), 3)
                self.assertTrue(all(request.structured_output.strict for request in provider.requests))

    def test_cli_schema_artifact_stores_decoded_schema_not_generation_wire(self) -> None:
        provider = GenerationRecordingProvider([generation_payload()])
        engine = self.discovery(provider)
        destination = self.root / "discovered.json"
        args = cli.build_parser().parse_args([
            "discover", "--manifest", str(engine.manifest.source_path), "--samples", str(self.pdf),
            "--input-root", str(self.root), "--output", str(destination),
            "--provider", "openai", "--model", "gpt-5", "--usage-log", str(self.root / "usage.jsonl"),
        ])
        cli.configure_command(args)
        with mock.patch("src.schema.discovery.SchemaDiscovery", return_value=engine), mock.patch("builtins.print"):
            self.assertEqual(cli.command_discover(args), 0)
        artifact = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(artifact["artifact_type"], "discovered_schema")
        self.assertNotIn("schema_nodes", artifact["data"])
        self.assertNotIn("item_node_id", artifact["data"]["fields"][-1])
        self.assertIn("item_schema", artifact["data"]["fields"][-1])

    def test_consensus_patch_artifact_stores_decoded_patch_not_generation_wire(self) -> None:
        provider = GenerationRecordingProvider([generation_patch_payload()])
        engine = self.discovery(provider)
        base = self.root / "base_schema.json"
        base.write_text(json.dumps(native_generation_schema()), encoding="utf-8")
        outputs = SchemaConsensusRefinement(engine, log=None, manifest=engine.manifest).refine(
            base, input_root=self.root, runs=1, samples=[str(self.pdf)], output_dir=self.root / "consensus",
        )
        artifact = json.loads((outputs.patch_dir / "run_001.json").read_text(encoding="utf-8"))
        self.assertEqual(artifact["artifact_type"], "candidate_patch_set")
        self.assertNotIn("schema_nodes", artifact["data"])
        self.assertNotIn("item_node_id", artifact["data"]["patches"][0])
        self.assertIn("item_schema", artifact["data"]["patches"][0])


if __name__ == "__main__":
    unittest.main()
