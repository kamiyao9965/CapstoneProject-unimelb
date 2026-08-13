from __future__ import annotations

import json
import tempfile
import unittest
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from src.common.model_config import ModelSelection
from src.common.json_contracts import load_contract
from src.common import model_provider
from src.common.model_provider import (
    ModelResponse,
    ProviderResponseError,
    ProviderRequest,
    StructuredOutputSpec,
    create_provider,
)
from src.schema.discovery import SchemaDiscovery

VALID_SCHEMA_TEXT = json.dumps({
    "vertical": "private_health",
    "version": "0.1-draft",
    "description": "Schema",
    "product_types": ["hospital"],
    "fields": [{
        "name": "product_type", "type": "enum",
        "description": "Product classification", "applies_to": ["hospital"],
        "required": True, "values": ["hospital"], "aliases": [],
    }, {
        "name": "product_name", "type": "string", "description": "Product name",
        "applies_to": ["hospital"], "required": True, "values": [],
        "aliases": [],
    }],
    "hospital_categories": [],
    "extras_services": [],
    "notes": [],
})

OUTPUT_SPEC = StructuredOutputSpec(
    name="discovered_schema",
    schema={
        "type": "object",
        "properties": {"fields": {"type": "array"}},
        "required": ["fields"],
        "additionalProperties": False,
    },
)

PROVIDER_EDGE_SPEC = StructuredOutputSpec(
    name="edge_schema",
    schema={
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "fixed", "tags", "note"],
        "properties": {
            "kind": {"enum": ["hospital", "extras"]},
            "fixed": {"const": "private_health"},
            "tags": {
                "type": "array",
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
            },
            "note": {"type": ["string", "null"]},
        },
    },
)


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
    def test_authoritative_model_contracts_project_for_native_schema_providers(self) -> None:
        for contract_name in (
            "private_health/discovered_schema",
            "private_health/candidate_patch_set",
        ):
            with self.subTest(contract=contract_name, provider="openai"):
                projected = model_provider._project_openai_schema(
                    load_contract(contract_name), strict=True
                )
                self.assertEqual(projected["type"], "object")
            with self.subTest(contract=contract_name, provider="anthropic"):
                projected = model_provider._project_anthropic_schema(
                    load_contract(contract_name)
                )
                self.assertEqual(projected["type"], "object")

    def test_discovery_passes_logical_pdf_request_to_injected_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "sample.pdf"
            pdf_path.touch()
            selection = ModelSelection("openai", "gpt-5", "markdown")
            provider = RecordingProvider()
            discovery = SchemaDiscovery(
                selection=selection,
                provider=provider,
                usage_log_path=None,
                log=None,
            )

            with mock.patch(
                "src.schema.discovery.render_pdf_paths_for_prompt",
                return_value="# PDF: sample\nstructured content",
            ):
                schema = discovery.discover([str(pdf_path)])

        self.assertIn("product_name", [field["name"] for field in schema["fields"]])
        self.assertEqual(len(provider.requests), 1)
        request = provider.requests[0]
        self.assertEqual(request.selection, selection)
        self.assertEqual(request.document_paths, ())
        self.assertIn("structured content", request.user_text)
        self.assertIn("Generate a private_health schema", request.user_text)
        self.assertEqual(request.structured_output.name, "discovered_schema")

    def test_discovery_logs_source_pdfs_for_pdfingestor_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_root = Path(tmp) / "PDFs"
            pdf_path = pdf_root / "HCF" / "hospital" / "sample.pdf"
            pdf_path.parent.mkdir(parents=True)
            pdf_path.touch()
            usage_log = Path(tmp) / "usage.jsonl"
            selection = ModelSelection("anthropic", "claude-test", "markdown")
            provider = RecordingProvider()

            discovery = SchemaDiscovery(
                selection=selection,
                provider=provider,
                pdf_root=pdf_root,
                usage_log_path=usage_log,
                log=None,
            )
            with mock.patch(
                "src.schema.discovery.render_pdf_paths_for_prompt",
                return_value="# PDF: sample\nstructured content",
            ):
                discovery.discover([str(pdf_path)])

            request = provider.requests[0]
            self.assertEqual(
                request.document_paths, (),
            )
            logged = json.loads(usage_log.read_text(encoding="utf-8"))
            self.assertEqual(logged["sample_pdfs"], [pdf_path.as_posix()])
            self.assertEqual(logged["document_input"], "markdown")

    def test_discovery_pdfingestor_failure_stops_before_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_root = Path(tmp) / "PDFs"
            pdf_path = pdf_root / "sample.pdf"
            pdf_root.mkdir(parents=True)
            pdf_path.touch()
            provider = RecordingProvider()

            discovery = SchemaDiscovery(
                    selection=ModelSelection("anthropic", "claude-test", "markdown"),
                    provider=provider,
                    pdf_root=pdf_root,
                    usage_log_path=None,
                    log=None,
                )
            with mock.patch(
                "src.schema.discovery.render_pdf_paths_for_prompt",
                side_effect=RuntimeError("PDFingestor failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "PDFingestor failed"):
                    discovery.discover([str(pdf_path)])

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

    def test_openai_provider_uses_strict_responses_json_schema(self) -> None:
        calls = {}

        def create(**kwargs):
            calls.update(kwargs)
            return SimpleNamespace(
                id="resp_json",
                output_text='{"fields": []}',
                usage=None,
            )

        client = SimpleNamespace(
            responses=SimpleNamespace(create=create),
            files=SimpleNamespace(),
        )
        selection = ModelSelection("openai", "gpt-5", "markdown")
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            create_provider(selection, client=client).generate(
                ProviderRequest(
                    selection=selection, system_prompt="system", user_text="user",
                    document_paths=(), timeout_seconds=1, cleanup_documents=True,
                    request_params={}, background=False, poll_interval=0, log=None,
                    structured_output=OUTPUT_SPEC,
                )
            )

        self.assertEqual(calls["text"]["format"]["type"], "json_schema")
        self.assertEqual(calls["text"]["format"]["name"], "discovered_schema")
        self.assertTrue(calls["text"]["format"]["strict"])
        self.assertEqual(calls["text"]["format"]["schema"], OUTPUT_SPEC.schema)

    def test_openai_provider_projects_to_its_documented_schema_subset(self) -> None:
        calls = {}

        def create(**kwargs):
            calls.update(kwargs)
            return SimpleNamespace(id="resp_json", output_text="{}", usage=None)

        selection = ModelSelection("openai", "gpt-5", "markdown")
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            create_provider(
                selection,
                client=SimpleNamespace(
                    responses=SimpleNamespace(create=create), files=SimpleNamespace()
                ),
            ).generate(
                ProviderRequest(
                    selection=selection, system_prompt="system", user_text="user",
                    document_paths=(), timeout_seconds=1, cleanup_documents=True,
                    request_params={}, background=False, poll_interval=0, log=None,
                    structured_output=PROVIDER_EDGE_SPEC,
                )
            )

        schema = calls["text"]["format"]["schema"]
        self.assertNotIn("$schema", schema)
        self.assertNotIn("uniqueItems", schema["properties"]["tags"])
        self.assertNotIn("minLength", schema["properties"]["tags"]["items"])
        self.assertEqual(schema["properties"]["kind"]["type"], "string")
        self.assertEqual(schema["properties"]["fixed"]["type"], "string")

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

    def test_anthropic_provider_uses_native_output_config(self) -> None:
        calls = {}

        def create(**kwargs):
            calls.update(kwargs)
            return SimpleNamespace(
                id="msg_json",
                content=[SimpleNamespace(type="text", text='{"fields": []}')],
                usage=None,
            )

        selection = ModelSelection("anthropic", "claude-sonnet-4-5", "markdown")
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True):
            create_provider(
                selection,
                client=SimpleNamespace(messages=SimpleNamespace(create=create)),
            ).generate(
                ProviderRequest(
                    selection=selection, system_prompt="system", user_text="user",
                    document_paths=(), timeout_seconds=1, cleanup_documents=True,
                    request_params={}, background=False, poll_interval=0, log=None,
                    structured_output=OUTPUT_SPEC,
                )
            )

        self.assertEqual(
            calls["output_config"],
            {"format": {"type": "json_schema", "schema": OUTPUT_SPEC.schema}},
        )

    def test_anthropic_provider_transforms_unsupported_schema_constraints(self) -> None:
        calls = {}

        def create(**kwargs):
            calls.update(kwargs)
            return SimpleNamespace(
                id="msg_json",
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text="{}")],
                usage=None,
            )

        selection = ModelSelection("anthropic", "claude-sonnet-4-5", "markdown")
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True):
            create_provider(
                selection,
                client=SimpleNamespace(messages=SimpleNamespace(create=create)),
            ).generate(
                ProviderRequest(
                    selection=selection, system_prompt="system", user_text="user",
                    document_paths=(), timeout_seconds=1, cleanup_documents=True,
                    request_params={}, background=False, poll_interval=0, log=None,
                    structured_output=PROVIDER_EDGE_SPEC,
                )
            )

        schema = calls["output_config"]["format"]["schema"]
        self.assertNotIn("$schema", schema)
        self.assertNotIn("uniqueItems", schema["properties"]["tags"])
        self.assertNotIn("minLength", schema["properties"]["tags"]["items"])
        self.assertEqual(schema["properties"]["kind"]["type"], "string")
        self.assertEqual(schema["properties"]["note"]["anyOf"][1]["type"], "null")

    def test_anthropic_provider_rejects_schema_over_union_limit_before_api_call(self) -> None:
        class ExplodingMessages:
            def create(self, **kwargs):
                raise AssertionError("Schema limit must fail before the API call.")

        properties = {
            f"field_{index}": {"type": ["string", "null"]}
            for index in range(17)
        }
        spec = StructuredOutputSpec(
            name="too_many_unions",
            schema={
                "type": "object",
                "additionalProperties": False,
                "required": list(properties),
                "properties": properties,
            },
        )
        selection = ModelSelection("anthropic", "claude-sonnet-4-5", "markdown")

        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True):
            with self.assertRaisesRegex(ValueError, "16 union"):
                create_provider(
                    selection,
                    client=SimpleNamespace(messages=ExplodingMessages()),
                ).generate(
                    ProviderRequest(
                        selection=selection, system_prompt="system", user_text="user",
                        document_paths=(), timeout_seconds=1,
                        cleanup_documents=True, request_params={}, background=False,
                        poll_interval=0, log=None, structured_output=spec,
                    )
                )

    def test_anthropic_refusal_is_normalized_without_exposing_refusal_text(self) -> None:
        response = SimpleNamespace(
            id="msg_refusal",
            stop_reason="refusal",
            content=[SimpleNamespace(type="text", text="SENSITIVE REFUSAL TEXT")],
            usage=SimpleNamespace(input_tokens=8, output_tokens=3),
        )
        selection = ModelSelection("anthropic", "claude-sonnet-4-5", "markdown")

        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True):
            with self.assertRaises(ProviderResponseError) as caught:
                create_provider(
                    selection,
                    client=SimpleNamespace(
                        messages=SimpleNamespace(create=lambda **_: response)
                    ),
                ).generate(
                    ProviderRequest(
                        selection=selection, system_prompt="system", user_text="user",
                        document_paths=(), timeout_seconds=1,
                        cleanup_documents=True, request_params={}, background=False,
                        poll_interval=0, log=None, structured_output=OUTPUT_SPEC,
                    )
                )

        self.assertEqual(caught.exception.response.response_id, "msg_refusal")
        self.assertEqual(caught.exception.response.usage.total_tokens, 11)
        self.assertNotIn("SENSITIVE REFUSAL TEXT", str(caught.exception))

    def test_deepseek_provider_rejects_pdf_before_key_or_client_use(self) -> None:
        class ExplodingClient:
            def __getattr__(self, name):
                raise AssertionError("DeepSeek PDF rejection must not touch the client.")

        with mock.patch.dict(os.environ, {}, clear=True):
            provider = create_provider(
                ModelSelection("deepseek", "deepseek-v4-flash", "pdf"),
                client=ExplodingClient(),
            )
            with self.assertRaisesRegex(ValueError, "markdown"):
                provider.generate(
                    ProviderRequest(
                        selection=ModelSelection("deepseek", "deepseek-v4-flash", "pdf"),
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
                selection = ModelSelection("deepseek", "deepseek-v4-flash", "markdown")
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
        self.assertEqual(completions.kwargs["model"], "deepseek-v4-flash")
        self.assertEqual(completions.kwargs["temperature"], 0.2)
        self.assertEqual(
            completions.kwargs["messages"][0],
            {"role": "system", "content": "system prompt"},
        )
        user_message = completions.kwargs["messages"][1]
        self.assertEqual(user_message["role"], "user")
        self.assertIn("read this", user_message["content"])
        self.assertIn("# policy", user_message["content"])

    def test_deepseek_provider_uses_json_object_mode_and_schema_instruction(self) -> None:
        calls = {}

        def create(**kwargs):
            calls.update(kwargs)
            return SimpleNamespace(
                id="chat_json",
                choices=[SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content='{"fields": []}'),
                )],
                usage=None,
            )

        selection = ModelSelection("deepseek", "deepseek-v4-pro", "markdown")
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=True):
            create_provider(
                selection,
                client=SimpleNamespace(
                    chat=SimpleNamespace(completions=SimpleNamespace(create=create))
                ),
            ).generate(
                ProviderRequest(
                    selection=selection, system_prompt="system", user_text="user",
                    document_paths=(), timeout_seconds=1, cleanup_documents=True,
                    request_params={}, background=False, poll_interval=0, log=None,
                    structured_output=OUTPUT_SPEC,
                )
            )

        self.assertEqual(calls["response_format"], {"type": "json_object"})
        self.assertIn("valid JSON", calls["messages"][0]["content"])
        self.assertIn('"required"', calls["messages"][0]["content"])

    def test_deepseek_provider_rejects_empty_structured_output(self) -> None:
        response = SimpleNamespace(
            id="chat_empty",
            choices=[SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content=""),
            )],
            usage=None,
        )
        selection = ModelSelection("deepseek", "deepseek-v4-flash", "markdown")
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "empty"):
                create_provider(
                    selection,
                    client=SimpleNamespace(
                        chat=SimpleNamespace(
                            completions=SimpleNamespace(create=lambda **_: response)
                        )
                    ),
                ).generate(
                    ProviderRequest(
                        selection=selection, system_prompt="system", user_text="user",
                        document_paths=(), timeout_seconds=1, cleanup_documents=True,
                        request_params={}, background=False, poll_interval=0, log=None,
                        structured_output=OUTPUT_SPEC,
                    )
                )

    def test_deepseek_provider_configures_official_endpoint_and_env_override(self) -> None:
        from src.common import model_provider

        selection = ModelSelection("deepseek", "deepseek-v4-flash", "markdown")
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

        selection = ModelSelection("deepseek", "deepseek-v4-flash", "markdown")
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
        selection = ModelSelection("deepseek", "deepseek-v4-flash", "markdown")

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
