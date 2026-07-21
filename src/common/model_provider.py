"""Provider-neutral request and response contracts for model calls."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from src.common.model_config import (
    ModelSelection,
    require_structured_output_capability,
    resolve_api_key,
)
from src.common.openai_run import (
    DEFAULT_OPENAI_API_KEY_ENV,
    PROJECT_API_KEY_ENV,
    create_openai_client,
    managed_uploaded_pdfs,
    run_response,
    usage_value,
)
from openai import OpenAI

try:
    from anthropic import Anthropic, transform_schema
except ImportError:  # pragma: no cover - optional provider dependency
    Anthropic = None
    transform_schema = None

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


class ProviderResponseError(RuntimeError):
    """A completed provider response that cannot be accepted as model output."""

    def __init__(self, message: str, response: ModelResponse) -> None:
        self.response = response
        super().__init__(message)


@dataclass(frozen=True)
class StructuredOutputSpec:
    """Provider-neutral schema request translated only inside adapters."""

    name: str
    schema: Mapping[str, object]
    strict: bool = True


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
    structured_output: StructuredOutputSpec | None = None


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
        _validate_structured_output_request(request)

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
        text = response.output_text or ""
        model_response = ModelResponse(
            text=text,
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
        if request.structured_output is not None and not text.strip():
            raise ProviderResponseError(
                "OpenAI structured output response was empty or refused.",
                model_response,
            )
        return model_response

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
        parameters = dict(request.request_params)
        if request.structured_output is not None:
            _reserve_parameter(parameters, "text", "OpenAI structured output")
            spec = request.structured_output
            parameters["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": spec.name,
                    "strict": spec.strict,
                    "schema": _project_openai_schema(spec.schema, strict=spec.strict),
                }
            }
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
            **parameters,
        )


class AnthropicProvider:
    """Native Anthropic Messages API adapter for PDF and Markdown input."""

    def __init__(self, client: object | None = None) -> None:
        self.client = client

    def generate(self, request: ProviderRequest) -> ModelResponse:
        if request.selection.provider != "anthropic":
            raise ValueError("AnthropicProvider requires an anthropic model selection.")
        _validate_structured_output_request(request)
        api_key, api_key_env = resolve_api_key(request.selection)
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY must be set to use the Anthropic provider.")
        if Anthropic is None:
            raise RuntimeError(
                "The anthropic package must be installed to use the Anthropic provider."
            )
        client = self.client or Anthropic(
            api_key=api_key,
            base_url=os.getenv("ANTHROPIC_BASE_URL") or None,
            timeout=request.timeout_seconds,
            max_retries=5,
        )
        content = self._content(request)
        parameters = dict(request.request_params)
        max_tokens = parameters.pop("max_tokens", 4096)
        if request.structured_output is not None:
            _reserve_parameter(parameters, "output_config", "Anthropic structured output")
            parameters["output_config"] = {
                "format": {
                    "type": "json_schema",
                    "schema": _project_anthropic_schema(
                        request.structured_output.schema
                    ),
                }
            }
        response = client.messages.create(
            model=request.selection.model,
            max_tokens=max_tokens,
            system=request.system_prompt,
            messages=[{"role": "user", "content": content}],
            **parameters,
        )
        usage = getattr(response, "usage", None)
        text = "".join(
                block.text for block in response.content if getattr(block, "type", None) == "text"
            )
        input_tokens = usage_value(usage, "input_tokens")
        output_tokens = usage_value(usage, "output_tokens")
        model_response = ModelResponse(
            text=text,
            provider=request.selection.provider,
            model=request.selection.model,
            response_id=getattr(response, "id", None),
            usage=ModelUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=(
                    input_tokens + output_tokens
                    if input_tokens is not None and output_tokens is not None
                    else None
                ),
            ) if usage is not None else None,
            api_key_env=api_key_env,
        )
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "max_tokens":
            raise ProviderResponseError(
                "Anthropic response was truncated because it reached max_tokens; "
                "increase max_tokens before accepting the generated artifact.",
                model_response,
            )
        if stop_reason == "refusal":
            raise ProviderResponseError(
                "Anthropic refused the structured output request.",
                model_response,
            )
        if request.structured_output is not None and not text.strip():
            raise ProviderResponseError(
                "Anthropic structured output response was empty.",
                model_response,
            )
        return model_response

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
        _validate_structured_output_request(request)
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

        parameters = dict(request.request_params)
        system_prompt = request.system_prompt
        if request.structured_output is not None:
            _reserve_parameter(parameters, "response_format", "DeepSeek structured output")
            parameters["response_format"] = {"type": "json_object"}
            system_prompt += (
                "\n\nReturn one complete valid JSON object only. It must satisfy this "
                "JSON Schema exactly:\n"
                + json.dumps(request.structured_output.schema, ensure_ascii=False)
            )
        response = client.chat.completions.create(
            model=request.selection.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": _markdown_user_text(request)},
            ],
            **parameters,
        )
        usage = getattr(response, "usage", None)
        if not getattr(response, "choices", None):
            empty_response = _deepseek_model_response(
                request, response, api_key_env, usage, text=""
            )
            raise ProviderResponseError(
                "DeepSeek response contained no completion choices.", empty_response
            )
        choice = response.choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        text = choice.message.content or ""
        model_response = _deepseek_model_response(
            request, response, api_key_env, usage, text=text
        )
        if finish_reason == "length":
            raise ProviderResponseError(
                "DeepSeek response was truncated because it reached the output length limit.",
                model_response,
            )
        if finish_reason == "content_filter":
            raise ProviderResponseError(
                "DeepSeek response was blocked by the content filter.", model_response
            )
        if request.structured_output is not None and not text.strip():
            raise ProviderResponseError(
                "DeepSeek structured output response was empty.", model_response
            )
        return model_response


def _markdown_user_text(request: ProviderRequest) -> str:
    """The one canonical way Markdown documents are inlined after the user text."""
    markdown = "\n\n".join(
        path.read_text(encoding="utf-8") for path in request.document_paths
    )
    return f"{request.user_text}\n\n{markdown}"


def _deepseek_model_response(
    request: ProviderRequest,
    response: object,
    api_key_env: str | None,
    usage: object | None,
    *,
    text: str,
) -> ModelResponse:
    return ModelResponse(
        text=text,
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


def _validate_structured_output_request(request: ProviderRequest) -> None:
    if request.structured_output is not None:
        require_structured_output_capability(request.selection)


def _reserve_parameter(
    parameters: Mapping[str, object],
    name: str,
    purpose: str,
) -> None:
    if name in parameters:
        raise ValueError(
            f"request_params must not override reserved {name!r} for {purpose}."
        )


_OPENAI_SUPPORTED_KEYS = frozenset({
    "type", "description", "title", "enum", "const", "properties", "required",
    "additionalProperties", "items", "minItems", "maxItems", "pattern", "format",
    "multipleOf", "maximum", "exclusiveMaximum", "minimum", "exclusiveMinimum",
    "anyOf", "$ref", "$defs",
})
_OPENAI_REJECTED_KEYS = frozenset({
    "allOf", "not", "dependentRequired", "dependentSchemas", "if", "then", "else",
    "patternProperties",
})


def _project_openai_schema(
    schema: Mapping[str, object],
    *,
    strict: bool,
) -> dict[str, object]:
    """Project the local contract into OpenAI's documented JSON Schema subset."""
    return _project_openai_node(dict(schema), strict=strict, path="$")


def _project_openai_node(
    schema: Mapping[str, Any],
    *,
    strict: bool,
    path: str,
) -> dict[str, Any]:
    rejected = _OPENAI_REJECTED_KEYS.intersection(schema)
    if rejected:
        raise ValueError(
            f"OpenAI structured output schema at {path} uses unsupported keywords: "
            + ", ".join(sorted(rejected))
        )
    projected: dict[str, Any] = {}
    for key, value in schema.items():
        if key in {"$schema", "$id", "uniqueItems", "minLength", "maxLength"}:
            continue
        if key == "oneOf":
            projected["anyOf"] = [
                _project_openai_node(item, strict=strict, path=f"{path}.oneOf")
                for item in value
            ]
            continue
        if key not in _OPENAI_SUPPORTED_KEYS:
            continue
        if key in {"properties", "$defs"}:
            projected[key] = {
                name: _project_openai_node(
                    item,
                    strict=strict,
                    path=f"{path}.{key}.{name}",
                )
                for name, item in value.items()
            }
        elif key in {"items"} and isinstance(value, Mapping):
            projected[key] = _project_openai_node(
                value, strict=strict, path=f"{path}.{key}"
            )
        elif key == "anyOf":
            projected[key] = [
                _project_openai_node(item, strict=strict, path=f"{path}.anyOf")
                for item in value
            ]
        else:
            projected[key] = value

    inferred_type = _inferred_json_type(projected)
    if "type" not in projected and inferred_type is not None:
        projected["type"] = inferred_type
    if projected.get("type") == "object":
        properties = projected.get("properties", {})
        if strict:
            if schema.get("additionalProperties") is True:
                raise ValueError(
                    f"OpenAI strict schema at {path} cannot contain an open object."
                )
            projected["additionalProperties"] = False
            missing = set(properties) - set(projected.get("required", []))
            if missing:
                raise ValueError(
                    f"OpenAI strict schema at {path} has non-required properties: "
                    + ", ".join(sorted(missing))
                )
    return projected


def _project_anthropic_schema(schema: Mapping[str, object]) -> dict[str, object]:
    """Use the installed Anthropic SDK's documented schema transformation."""
    if transform_schema is None:
        raise RuntimeError(
            "The anthropic package must be installed to use Anthropic structured output."
        )
    normalized = _normalize_anthropic_node(dict(schema), path="$")
    try:
        projected = transform_schema(normalized)
    except ValueError as exc:
        raise ValueError(f"Anthropic structured output schema is unsupported: {exc}") from exc
    union_count = _count_schema_key(projected, "anyOf")
    if union_count > 16:
        raise ValueError(
            "Anthropic structured output supports at most 16 union parameters; "
            f"this schema requires {union_count}."
        )
    optional_count = _count_optional_properties(projected)
    if optional_count > 24:
        raise ValueError(
            "Anthropic structured output supports at most 24 optional parameters; "
            f"this schema requires {optional_count}."
        )
    return projected


def _normalize_anthropic_node(
    schema: Mapping[str, Any],
    *,
    path: str,
) -> dict[str, Any]:
    normalized = {
        key: value for key, value in schema.items() if key not in {"$schema", "$id"}
    }
    if normalized.get("type") == "object" and normalized.get("additionalProperties") is True:
        raise ValueError(
            f"Anthropic structured output cannot represent open object schema at {path}."
        )
    for key in ("properties", "$defs"):
        if isinstance(normalized.get(key), Mapping):
            normalized[key] = {
                name: _normalize_anthropic_node(item, path=f"{path}.{key}.{name}")
                for name, item in normalized[key].items()
            }
    if isinstance(normalized.get("items"), Mapping):
        normalized["items"] = _normalize_anthropic_node(
            normalized["items"], path=f"{path}.items"
        )
    for key in ("anyOf", "oneOf", "allOf"):
        if isinstance(normalized.get(key), list):
            normalized[key] = [
                _normalize_anthropic_node(item, path=f"{path}.{key}")
                for item in normalized[key]
            ]

    inferred_type = _inferred_json_type(normalized)
    if "type" not in normalized and inferred_type is not None:
        normalized["type"] = inferred_type
    type_value = normalized.get("type")
    if isinstance(type_value, list):
        shared = {key: value for key, value in normalized.items() if key != "type"}
        variants: list[dict[str, Any]] = []
        for item_type in type_value:
            variant = {**shared, "type": item_type}
            if "enum" in shared:
                variant["enum"] = [
                    item for item in shared["enum"] if _json_type_of(item) == item_type
                ]
            variants.append(variant)
        return {"anyOf": variants}
    return normalized


def _inferred_json_type(schema: Mapping[str, Any]) -> str | list[str] | None:
    values: list[Any] = []
    if "const" in schema:
        values = [schema["const"]]
    elif isinstance(schema.get("enum"), list) and schema["enum"]:
        values = schema["enum"]
    if not values:
        return None
    types = list(dict.fromkeys(_json_type_of(value) for value in values))
    return types[0] if len(types) == 1 else types


def _json_type_of(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    raise ValueError(f"Unsupported JSON Schema literal type: {type(value).__name__}.")


def _count_schema_key(schema: Any, key: str) -> int:
    if isinstance(schema, Mapping):
        return (1 if key in schema else 0) + sum(
            _count_schema_key(value, key) for value in schema.values()
        )
    if isinstance(schema, list):
        return sum(_count_schema_key(value, key) for value in schema)
    return 0


def _count_optional_properties(schema: Any) -> int:
    if isinstance(schema, Mapping):
        count = 0
        properties = schema.get("properties")
        if isinstance(properties, Mapping):
            count += len(set(properties) - set(schema.get("required", [])))
        return count + sum(_count_optional_properties(value) for value in schema.values())
    if isinstance(schema, list):
        return sum(_count_optional_properties(value) for value in schema)
    return 0
