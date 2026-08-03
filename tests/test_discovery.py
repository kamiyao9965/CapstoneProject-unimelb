from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.common.model_config import ModelSelection
from src.common.model_provider import ModelResponse, ProviderRequest
from src.schema.discovery import SchemaDiscovery


class SchemaDiscoveryInputTest(unittest.TestCase):
    def test_pdfingestor_failure_stops_before_provider(self) -> None:
        class RecordingProvider:
            def __init__(self) -> None:
                self.requests: list[ProviderRequest] = []

            def generate(self, request: ProviderRequest) -> ModelResponse:
                self.requests.append(request)
                raise AssertionError("provider must not be called")

        with tempfile.TemporaryDirectory() as tmp:
            first_pdf = Path(tmp) / "first.pdf"
            first_pdf.touch()
            provider = RecordingProvider()
            discovery = SchemaDiscovery(
                selection=ModelSelection("openai", "gpt-5", "markdown"),
                provider=provider,
                usage_log_path=None,
                log=None,
            )

            with mock.patch(
                "src.schema.discovery.render_pdf_paths_for_prompt",
                side_effect=RuntimeError("PDFingestor failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "PDFingestor failed"):
                    discovery.discover([str(first_pdf)])

            self.assertEqual(provider.requests, [])


if __name__ == "__main__":
    unittest.main()
