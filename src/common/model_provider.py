"""Provider-neutral request and response contracts for model calls."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol

from src.common.model_config import ModelSelection, resolve_api_key
from src.common.openai_run import (
    DEFAULT_OPENAI_API_KEY_ENV,
    PROJECT_API_KEY_ENV,
    create_openai_client,
    managed_uploaded_pdfs,
    run_response,
    usage_value,
)
from anthropic import Anthropic
from openai import OpenAI

DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class ModelResponse:
    text: str
    provider: str
    model: str
    response_id: str | None = None
    usage: ModelUsage | None = None
    api_key_env: str | None = None


@dataclass(frozen=True)
class ProviderRequest:
    selection: ModelSelection
    system_prompt: str
    user_text: str
    document_paths: tuple[Path, ...]
    timeout_seconds: float
    cleanup_documents: bool
    request_params: Mapping[str, object]
    background: bool
    poll_interval: float
    log: Callable[[str], None] | None = None


class ModelProvider(Protocol):
    def generate(self, request: ProviderRequest) -> ModelResponse: ...


def create_provider(
    selection: ModelSelection,
    *,
    client: object | None = None,
) -> ModelProvider:
    if selection.provider == "openai":
        return OpenAIProvider(client=client)
    if selection.provider == "anthropic":
        return AnthropicProvider(client=client)
    if selection.provider == "deepseek":
        return DeepSeekProvider(client=client)
    raise NotImplementedError(f"Provider {selection.provider!r} is not configured yet.")


class OpenAIProvider:
    """OpenAI Responses API adapter using the existing file lifecycle helpers."""

    def __init__(self, client: object | None = None) -> None:
        self.client = client

    def generate(self, request: ProviderRequest) -> ModelResponse:
        if request.selection.provider != "openai":
            raise ValueError("OpenAIProvider requires an openai model selection.")

        api_key, api_key_env = resolve_api_key(request.selection)
        if not api_key:
            raise RuntimeError(
                f"{PROJECT_API_KEY_ENV} or {DEFAULT_OPENAI_API_KEY_ENV} must be set "
                "to use the OpenAI provider."
            )
        client = create_openai_client(
            self.client,
            api_key=api_key,
            timeout_seconds=request.timeout_seconds,
        )
        if request.selection.document_input == "pdf":
            response = self._generate_from_pdfs(client, request)
        else:
            response = self._run(
                client,
                request,
                content=[{"type": "input_text", "text": _markdown_user_text(request)}],
            )

        usage = getattr(response, "usage", None)
        return ModelResponse(
            text=response.output_text,
            provider=request.selection.provider,
            model=request.selection.model,
            response_id=getattr(response, "id", None),
            usage=ModelUsage(
                input_tokens=usage_value(usage, "input_tokens", "prompt_tokens"),
                output_tokens=usage_value(usage, "output_tokens", "completion_tokens"),
                total_tokens=usage_value(usage, "total_tokens"),
            ) if usage is not None else None,
            api_key_env=api_key_env,
        )

    def _generate_from_pdfs(self, client: object, request: ProviderRequest):
        with managed_uploaded_pdfs(
            client,
            list(request.document_paths),
            cleanup=request.cleanup_documents,
            log=request.log,
        ) as file_ids:
            content = [{"type": "input_text", "text": request.user_text}]
            content.extend({"type": "input_file", "file_id": file_id} for file_id in file_ids)
            return self._run(client, request, content=content)

    @staticmethod
    def _run(client: object, request: ProviderRequest, *, content: list[dict[str, object]]):
        return run_response(
            client,
            background=request.background,
            poll_interval=request.poll_interval,
            poll_timeout=request.timeout_seconds,
            log=request.log,
            model=request.selection.model,
            input=[
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": content},
            ],
            **request.request_params,
        )


class AnthropicProvider:
    """Native Anthropic Messages API adapter for PDF and Markdown input."""

    def __init__(self, client: object | None = None) -> None:
        self.client = client

    def generate(self, request: ProviderRequest) -> ModelResponse:
        if request.selection.provider != "anthropic":
            raise ValueError("AnthropicProvider requires an anthropic model selection.")
        api_key, api_key_env = resolve_api_key(request.selection)
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY must be set to use the Anthropic provider.")
        client = self.client or Anthropic(
            api_key=api_key,
            base_url=os.getenv("ANTHROPIC_BASE_URL") or None,
            timeout=request.timeout_seconds,
            max_retries=5,
        )
        content = self._content(request)
        parameters = dict(request.request_params)
        max_tokens = parameters.pop("max_tokens", 4096)
        response = client.messages.create(
            model=request.selection.model,
            max_tokens=max_tokens,
            system=request.system_prompt,
            messages=[{"role": "user", "content": content}],
            **parameters,
        )
        usage = getattr(response, "usage", None)
        return ModelResponse(
            text="".join(
                block.text for block in response.content if getattr(block, "type", None) == "text"
            ),
            provider=request.selection.provider,
            model=request.selection.model,
            response_id=getattr(response, "id", None),
            usage=ModelUsage(
                input_tokens=usage_value(usage, "input_tokens"),
                output_tokens=usage_value(usage, "output_tokens"),
            ) if usage is not None else None,
            api_key_env=api_key_env,
        )

    @staticmethod
    def _content(request: ProviderRequest) -> list[dict[str, object]]:
        if request.selection.document_input == "pdf":
            content: list[dict[str, object]] = []
            for path in request.document_paths:
                content.append(
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                        },
                    }
                )
            content.append({"type": "text", "text": request.user_text})
            return content

        return [{"type": "text", "text": _markdown_user_text(request)}]


class DeepSeekProvider:
    """DeepSeek OpenAI-compatible chat adapter; Markdown document input only."""

    def __init__(self, client: object | None = None) -> None:
        self.client = client

    def generate(self, request: ProviderRequest) -> ModelResponse:
        if request.selection.provider != "deepseek":
            raise ValueError("DeepSeekProvider requires a deepseek model selection.")
        if request.selection.document_input != "markdown":
            raise ValueError(
                "DeepSeekProvider supports markdown document input only; "
                "convert PDFs first with --document-input markdown."
            )

        api_key, api_key_env = resolve_api_key(request.selection)
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY must be set to use the DeepSeek provider.")
        client = self.client or OpenAI(
            api_key=api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL") or DEFAULT_DEEPSEEK_BASE_URL,
            timeout=request.timeout_seconds,
            max_retries=5,
        )

        response = client.chat.completions.create(
            model=request.selection.model,
            messages=[
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": _markdown_user_text(request)},
            ],
            **request.request_params,
        )
        usage = getattr(response, "usage", None)
        return ModelResponse(
            text=response.choices[0].message.content or "",
            provider=request.selection.provider,
            model=request.selection.model,
            response_id=getattr(response, "id", None),
            usage=ModelUsage(
                input_tokens=usage_value(usage, "prompt_tokens", "input_tokens"),
                output_tokens=usage_value(usage, "completion_tokens", "output_tokens"),
                total_tokens=usage_value(usage, "total_tokens"),
            ) if usage is not None else None,
            api_key_env=api_key_env,
        )


def _markdown_user_text(request: ProviderRequest) -> str:
    """The one canonical way Markdown documents are inlined after the user text."""
    markdown = "\n\n".join(
        path.read_text(encoding="utf-8") for path in request.document_paths
    )
    return f"{request.user_text}\n\n{markdown}"
