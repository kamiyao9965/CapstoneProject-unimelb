from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.common.model_config import ModelSelection
from src.common.model_provider import ModelResponse, ModelUsage, ProviderRequest
from src.common.structured_output import StructuredOutputFailure
from src.schema.discovery import SchemaDiscovery
from src.schema_application.extractor import SchemaExtractor
from tests.test_extractor import VALID_RECORD
from tests.test_model_provider import VALID_SCHEMA_TEXT


class SequenceProvider:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.calls = 0

    def generate(self, request: ProviderRequest) -> ModelResponse:
        self.calls += 1
        return ModelResponse(
            text=next(self.responses),
            provider=request.selection.provider,
            model=request.selection.model,
            usage=ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        )


class AttemptUsageTests(unittest.TestCase):
    def test_every_attempt_is_logged_once_on_success_repair_and_exhaustion(self) -> None:
        for operation in ("discovery", "extraction"):
            valid_text = VALID_SCHEMA_TEXT if operation == "discovery" else json.dumps(VALID_RECORD)
            for responses, expected_validity in (
                ([valid_text], [True]),
                (["{}", valid_text], [False, True]),
                (["{}"] * 3, [False] * 3),
            ):
                with self.subTest(operation=operation, validity=expected_validity), tempfile.TemporaryDirectory() as tmp:
                    pdf_path = Path(tmp) / "sample.pdf"
                    pdf_path.touch()
                    usage_path = Path(tmp) / "usage.jsonl"
                    provider = SequenceProvider(responses)
                    options = {
                        "selection": ModelSelection("openai", "gpt-5", "markdown"),
                        "provider": provider,
                        "usage_log_path": usage_path,
                        "log": None,
                    }
                    if operation == "discovery":
                        engine = SchemaDiscovery(**options)
                        renderer = "src.schema.discovery.render_pdf_paths_for_prompt"
                        invoke = lambda: engine.discover([str(pdf_path)], run_id="usage-test")
                    else:
                        engine = SchemaExtractor(json.loads(VALID_SCHEMA_TEXT), **options)
                        renderer = "src.schema_application.extractor.render_pdf_paths_for_prompt"
                        invoke = lambda: engine.extract_one(pdf_path, run_id="usage-test")
                    with mock.patch(renderer, return_value="offline PDF text"):
                        if expected_validity[-1]:
                            invoke()
                        else:
                            with self.assertRaises(StructuredOutputFailure):
                                invoke()
                    entries = [json.loads(line) for line in usage_path.read_text().splitlines()]
                    self.assertEqual(provider.calls, len(expected_validity))
                    self.assertEqual([entry["attempt_number"] for entry in entries], list(range(1, provider.calls + 1)))
                    self.assertEqual([entry["validation_succeeded"] for entry in entries], expected_validity)
                    self.assertEqual({entry["run_id"] for entry in entries}, {"usage-test"})
                    self.assertEqual([entry["total_tokens"] for entry in entries], [15] * provider.calls)
