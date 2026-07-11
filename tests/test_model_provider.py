from __future__ import annotations

import json
import tempfile
import unittest
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from src.common.model_config import ModelSelection
from src.common.model_provider import ModelResponse, ProviderRequest, create_provider
from src.schema.discovery import SchemaDiscovery

VALID_SCHEMA_TEXT = """vertical: private_health
version: 0.1-draft
product_types: [hospital]
fields:
  - name: product_name
    type: string
    description: Product name
    applies_to: [hospital]
    required: true
    values: []
"""


class RecordingProvider:
    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    def generate(self, request: ProviderRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            text=VALID_SCHEMA_TEXT,
            provider=request.selection.provider,
            model=request.selection.model,
            response_id="response_001",
        )


class ProviderContractTest(unittest.TestCase):
    def test_discovery_passes_logical_pdf_request_to_injected_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "sample.pdf"
            pdf_path.touch()
            selection = ModelSelection("openai", "gpt-5", "pdf")
            provider = RecordingProvider()
            discovery = SchemaDiscovery(
                selection=selection,
                provider=provider,
                usage_log_path=None,
                log=None,
            )

            schema = discovery.discover([str(pdf_path)])

        self.assertEqual(schema, VALID_SCHEMA_TEXT)
        self.assertEqual(len(provider.requests), 1)
        request = provider.requests[0]
        self.assertEqual(request.selection, selection)
        self.assertEqual(request.document_paths, (pdf_path,))
        self.assertIn("Generate a private_health YAML schema", request.user_text)

    def test_discovery_markdown_mode_sends_mirrors_and_logs_source_pdfs(self) -> None:
        class WritingPreprocessor:
            def convert(self, source_pdf: Path, output_markdown: Path) -> None:
                output_markdown.write_text("# converted\n", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            pdf_root = Path(tmp) / "PDFs"
            pdf_path = pdf_root / "HCF" / "hospital" / "sample.pdf"
            pdf_path.parent.mkdir(parents=True)
            pdf_path.touch()
            usage_log = Path(tmp) / "usage.jsonl"
            selection = ModelSelection("anthropic", "claude-test", "markdown")
            provider = RecordingProvider()

            SchemaDiscovery(
                selection=selection,
                provider=provider,
                pdf_root=pdf_root,
                preprocessor=WritingPreprocessor(),
                usage_log_path=usage_log,
                log=None,
            ).discover([str(pdf_path)])

            request = provider.requests[0]
            self.assertEqual(
                request.document_paths,
                (Path(tmp) / "Markdown" / "HCF" / "hospital" / "sample.md",),
            )
            logged = json.loads(usage_log.read_text(encoding="utf-8"))
            self.assertEqual(logged["sample_pdfs"], [pdf_path.as_posix()])
            self.assertEqual(logged["document_input"], "markdown")

    def test_discovery_markdown_conversion_failure_stops_before_provider(self) -> None:
        class FailingPreprocessor:
            def convert(self, source_pdf: Path, output_markdown: Path) -> None:
                raise RuntimeError(f"conversion failed for {source_pdf}")

        with tempfile.TemporaryDirectory() as tmp:
            pdf_root = Path(tmp) / "PDFs"
            pdf_path = pdf_root / "sample.pdf"
            pdf_root.mkdir(parents=True)
            pdf_path.touch()
            provider = RecordingProvider()

            with self.assertRaisesRegex(RuntimeError, "conversion failed"):
                SchemaDiscovery(
                    selection=ModelSelection("anthropic", "claude-test", "markdown"),
                    provider=provider,
                    pdf_root=pdf_root,
                    preprocessor=FailingPreprocessor(),
                    usage_log_path=None,
                    log=None,
                ).discover([str(pdf_path)])

            self.assertEqual(provider.requests, [])

    def test_openai_provider_sends_markdown_text_without_file_upload(self) -> None:
        class ExplodingFiles:
            def __getattr__(self, name):
                raise AssertionError("Markdown input must not touch the files API.")

        class FakeResponses:
            def __init__(self) -> None:
                self.kwargs = None

            def create(self, **kwargs):
                self.kwargs = kwargs
                return SimpleNamespace(
                    id="resp_001",
                    output_text="fields: []",
                    usage=SimpleNamespace(input_tokens=9, output_tokens=3, total_tokens=12),
                )

        responses = FakeResponses()
        fake_client = SimpleNamespace(responses=responses, files=ExplodingFiles())
        with tempfile.TemporaryDirectory() as tmp:
            markdown_path = Path(tmp) / "sample.md"
            markdown_path.write_text("# policy\n", encoding="utf-8")
            selection = ModelSelection("openai", "gpt-5", "markdown")
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
                response = create_provider(selection, client=fake_client).generate(
                    ProviderRequest(
                        selection=selection,
                        system_prompt="system", user_text="read this",
                        document_paths=(markdown_path,),
                        timeout_seconds=1, cleanup_documents=True, request_params={},
                        background=False, poll_interval=0, log=None,
                    )
                )

        self.assertEqual(response.text, "fields: []")
        self.assertEqual(response.usage.total_tokens, 12)
        user_content = responses.kwargs["input"][1]["content"]
        self.assertEqual(len(user_content), 1)
        self.assertEqual(user_content[0]["type"], "input_text")
        self.assertIn("read this", user_content[0]["text"])
        self.assertIn("# policy", user_content[0]["text"])

    def test_anthropic_provider_sends_native_pdf_document_and_normalizes_response(self) -> None:
        class FakeMessages:
            def __init__(self) -> None:
                self.kwargs = None

            def create(self, **kwargs):
                self.kwargs = kwargs
                return SimpleNamespace(
                    id="msg_001",
                    content=[SimpleNamespace(type="text", text="fields: []")],
                    usage=SimpleNamespace(input_tokens=12, output_tokens=5),
                )

        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "sample.pdf"
            pdf_path.write_bytes(b"%PDF-test")
            messages = FakeMessages()
            with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True):
                provider = create_provider(
                    ModelSelection("anthropic", "claude-test", "pdf"),
                    client=SimpleNamespace(messages=messages),
                )
                response = provider.generate(
                    ProviderRequest(
                        selection=ModelSelection("anthropic", "claude-test", "pdf"),
                        system_prompt="system",
                        user_text="read this",
                        document_paths=(pdf_path,),
                        timeout_seconds=1,
                        cleanup_documents=True,
                        request_params={},
                        background=False,
                        poll_interval=0,
                        log=None,
                    )
                )

        self.assertEqual(response.text, "fields: []")
        self.assertEqual(response.usage.input_tokens, 12)
        self.assertEqual(messages.kwargs["messages"][0]["content"][0]["type"], "document")

    def test_deepseek_provider_rejects_pdf_before_key_or_client_use(self) -> None:
        class ExplodingClient:
            def __getattr__(self, name):
                raise AssertionError("DeepSeek PDF rejection must not touch the client.")

        with mock.patch.dict(os.environ, {}, clear=True):
            provider = create_provider(
                ModelSelection("deepseek", "deepseek-chat", "pdf"),
                client=ExplodingClient(),
            )
            with self.assertRaisesRegex(ValueError, "markdown"):
                provider.generate(
                    ProviderRequest(
                        selection=ModelSelection("deepseek", "deepseek-chat", "pdf"),
                        system_prompt="system", user_text="user", document_paths=(),
                        timeout_seconds=1, cleanup_documents=True, request_params={},
                        background=False, poll_interval=0, log=None,
                    )
                )

    def test_deepseek_provider_sends_markdown_chat_request_and_normalizes_response(self) -> None:
        class FakeCompletions:
            def __init__(self) -> None:
                self.kwargs = None

            def create(self, **kwargs):
                self.kwargs = kwargs
                return SimpleNamespace(
                    id="chatcmpl_001",
                    choices=[
                        SimpleNamespace(message=SimpleNamespace(content="fields: []"))
                    ],
                    usage=SimpleNamespace(
                        prompt_tokens=20, completion_tokens=7, total_tokens=27
                    ),
                )

        completions = FakeCompletions()
        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        with tempfile.TemporaryDirectory() as tmp:
            markdown_path = Path(tmp) / "sample.md"
            markdown_path.write_text("# policy\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=True):
                selection = ModelSelection("deepseek", "deepseek-chat", "markdown")
                response = create_provider(selection, client=fake_client).generate(
                    ProviderRequest(
                        selection=selection,
                        system_prompt="system prompt", user_text="read this",
                        document_paths=(markdown_path,),
                        timeout_seconds=1, cleanup_documents=True,
                        request_params={"temperature": 0.2},
                        background=False, poll_interval=0, log=None,
                    )
                )

        self.assertEqual(response.text, "fields: []")
        self.assertEqual(response.provider, "deepseek")
        self.assertEqual(response.response_id, "chatcmpl_001")
        self.assertEqual(response.usage.input_tokens, 20)
        self.assertEqual(response.usage.output_tokens, 7)
        self.assertEqual(response.usage.total_tokens, 27)
        self.assertEqual(response.api_key_env, "DEEPSEEK_API_KEY")
        self.assertEqual(completions.kwargs["model"], "deepseek-chat")
        self.assertEqual(completions.kwargs["temperature"], 0.2)
        self.assertEqual(
            completions.kwargs["messages"][0],
            {"role": "system", "content": "system prompt"},
        )
        user_message = completions.kwargs["messages"][1]
        self.assertEqual(user_message["role"], "user")
        self.assertIn("read this", user_message["content"])
        self.assertIn("# policy", user_message["content"])

    def test_deepseek_provider_configures_official_endpoint_and_env_override(self) -> None:
        from src.common import model_provider

        selection = ModelSelection("deepseek", "deepseek-chat", "markdown")
        request = ProviderRequest(
            selection=selection,
            system_prompt="system", user_text="user", document_paths=(),
            timeout_seconds=9, cleanup_documents=True, request_params={},
            background=False, poll_interval=0, log=None,
        )

        def fake_client_factory(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(
                        create=lambda **_: SimpleNamespace(
                            id=None,
                            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
                            usage=None,
                        )
                    )
                )
            )

        captured: dict[str, object] = {}
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=True):
            with mock.patch.object(model_provider, "OpenAI", fake_client_factory):
                create_provider(selection).generate(request)
        self.assertEqual(captured["base_url"], "https://api.deepseek.com")
        self.assertEqual(captured["api_key"], "test-key")
        self.assertEqual(captured["timeout"], 9)

        captured = {}
        environment = {"DEEPSEEK_API_KEY": "test-key", "DEEPSEEK_BASE_URL": "https://proxy.local/v1"}
        with mock.patch.dict(os.environ, environment, clear=True):
            with mock.patch.object(model_provider, "OpenAI", fake_client_factory):
                create_provider(selection).generate(request)
        self.assertEqual(captured["base_url"], "https://proxy.local/v1")

    def test_deepseek_provider_requires_api_key_before_client_construction(self) -> None:
        from src.common import model_provider

        selection = ModelSelection("deepseek", "deepseek-chat", "markdown")
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(
                model_provider, "OpenAI",
                side_effect=AssertionError("No client may be built without a key."),
            ):
                with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_API_KEY"):
                    create_provider(selection).generate(
                        ProviderRequest(
                            selection=selection,
                            system_prompt="system", user_text="user", document_paths=(),
                            timeout_seconds=1, cleanup_documents=True, request_params={},
                            background=False, poll_interval=0, log=None,
                        )
                    )

    def test_anthropic_provider_honours_base_url_override(self) -> None:
        from src.common import model_provider

        captured: dict[str, object] = {}

        def fake_anthropic(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                messages=SimpleNamespace(
                    create=lambda **_: SimpleNamespace(
                        id=None,
                        content=[SimpleNamespace(type="text", text="ok")],
                        usage=None,
                    )
                )
            )

        selection = ModelSelection("anthropic", "claude-test", "markdown")
        environment = {
            "ANTHROPIC_API_KEY": "test-key",
            "ANTHROPIC_BASE_URL": "https://anthropic-proxy.local",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            with mock.patch.object(model_provider, "Anthropic", fake_anthropic):
                create_provider(selection).generate(
                    ProviderRequest(
                        selection=selection,
                        system_prompt="system", user_text="user", document_paths=(),
                        timeout_seconds=1, cleanup_documents=True, request_params={},
                        background=False, poll_interval=0, log=None,
                    )
                )

        self.assertEqual(captured["base_url"], "https://anthropic-proxy.local")

    def test_openai_client_honours_base_url_environment_override(self) -> None:
        from src.common.openai_run import create_openai_client

        environment = {
            "OPENAI_API_KEY": "test-key",
            "OPENAI_BASE_URL": "https://openai-proxy.local/v1",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            client = create_openai_client(None, api_key="test-key", timeout_seconds=1)

        self.assertEqual(str(client.base_url).rstrip("/"), "https://openai-proxy.local/v1")

    def test_anthropic_provider_reads_markdown_documents_as_text(self) -> None:
        calls = {}

        def create(**kwargs):
            calls.update(kwargs)
            return SimpleNamespace(
                id="msg_002",
                content=[SimpleNamespace(type="text", text="ok")],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

        messages = SimpleNamespace(create=create)
        with tempfile.TemporaryDirectory() as tmp:
            markdown_path = Path(tmp) / "sample.md"
            markdown_path.write_text("# source\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True):
                response = create_provider(
                    ModelSelection("anthropic", "claude-test", "markdown"),
                    client=SimpleNamespace(messages=messages),
                ).generate(
                    ProviderRequest(
                        selection=ModelSelection("anthropic", "claude-test", "markdown"),
                        system_prompt="system", user_text="read this", document_paths=(markdown_path,),
                        timeout_seconds=1, cleanup_documents=True, request_params={},
                        background=False, poll_interval=0, log=None,
                    )
                )

        self.assertEqual(response.text, "ok")
        self.assertIn("# source", calls["messages"][0]["content"][0]["text"])

    def test_anthropic_provider_rejects_max_token_truncation(self) -> None:
        response = SimpleNamespace(
            id="msg_truncated",
            stop_reason="max_tokens",
            content=[SimpleNamespace(type="text", text="partial")],
            usage=None,
        )
        client = SimpleNamespace(
            messages=SimpleNamespace(create=lambda **_: response)
        )
        selection = ModelSelection("anthropic", "claude-test", "markdown")

        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "truncated"):
                create_provider(selection, client=client).generate(
                    ProviderRequest(
                        selection=selection,
                        system_prompt="system",
                        user_text="user",
                        document_paths=(),
                        timeout_seconds=1,
                        cleanup_documents=True,
                        request_params={},
                        background=False,
                        poll_interval=0,
                        log=None,
                    )
                )

    def test_deepseek_provider_rejects_length_truncation(self) -> None:
        response = SimpleNamespace(
            id="chat_truncated",
            choices=[
                SimpleNamespace(
                    finish_reason="length",
                    message=SimpleNamespace(content="partial"),
                )
            ],
            usage=None,
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_: response)
            )
        )
        selection = ModelSelection("deepseek", "deepseek-chat", "markdown")

        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "truncated"):
                create_provider(selection, client=client).generate(
                    ProviderRequest(
                        selection=selection,
                        system_prompt="system",
                        user_text="user",
                        document_paths=(),
                        timeout_seconds=1,
                        cleanup_documents=True,
                        request_params={},
                        background=False,
                        poll_interval=0,
                        log=None,
                    )
                )


if __name__ == "__main__":
    unittest.main()
