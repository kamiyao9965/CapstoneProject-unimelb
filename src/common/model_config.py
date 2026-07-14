"""Provider-neutral model selection and credential environment lookup."""

from __future__ import annotations

import os
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Mapping

from src.common.json_codec import loads_json

DEFAULT_PROVIDER = "openai"
DEFAULT_MODEL = "gpt-5"
DEFAULT_DOCUMENT_INPUT = "pdf"

SUPPORTED_PROVIDERS = frozenset({"openai", "anthropic", "deepseek"})
SUPPORTED_DOCUMENT_INPUTS = frozenset({"pdf", "markdown"})

API_KEY_ENVIRONMENTS = {
    "openai": ("MY_OPENAI_API_KEY", "OPENAI_API_KEY"),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
}


@dataclass(frozen=True)
class ModelSelection:
    provider: str
    model: str
    document_input: str


@dataclass(frozen=True)
class StructuredOutputCapability:
    mode: str


def require_structured_output_capability(
    selection: ModelSelection,
) -> StructuredOutputCapability:
    """Resolve an approved provider/model structured-output capability."""
    path = Path(__file__).resolve().parents[2] / "configs" / "model_capabilities.json"
    config = loads_json(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Model capability config must be a JSON object.")
    provider_config = config.get("providers", {}).get(selection.provider)
    if not isinstance(provider_config, dict):
        raise ValueError(
            f"Provider {selection.provider!r} is not approved for structured output."
        )
    patterns = provider_config.get("model_patterns", [])
    if not any(fnmatchcase(selection.model, pattern) for pattern in patterns):
        raise ValueError(
            f"Model {selection.provider}/{selection.model} is not approved for "
            "structured output. Update configs/model_capabilities.json only after "
            "verifying provider support."
        )
    document_inputs = provider_config.get("document_inputs", [])
    if selection.document_input not in document_inputs:
        raise ValueError(
            f"{selection.provider}/{selection.model} does not support structured "
            f"output with {selection.document_input} document input."
        )
    mode = provider_config.get("structured_output_mode")
    if mode not in {"json_schema", "json_object"}:
        raise ValueError(
            f"Invalid structured output capability for {selection.provider}."
        )
    return StructuredOutputCapability(mode=mode)


def resolve_selection(
    *,
    provider: str | None = None,
    model: str | None = None,
    document_input: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> ModelSelection:
    """Resolve CLI values, then environment defaults, then safe built-ins."""
    environment = os.environ if environment is None else environment
    resolved_provider = _normalized(
        provider
        if provider is not None
        else environment.get("LLM_PROVIDER", DEFAULT_PROVIDER)
    )
    if resolved_provider not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"Unsupported provider {resolved_provider!r}. "
            f"Choose one of: {', '.join(sorted(SUPPORTED_PROVIDERS))}."
        )
    environment_model = environment.get("LLM_MODEL")
    if environment_model is None and resolved_provider == "openai":
        environment_model = environment.get("OPENAI_MODEL", DEFAULT_MODEL)
    if model is None and environment_model is None and resolved_provider != "openai":
        raise ValueError(
            f"Model must be provided for provider {resolved_provider!r} via "
            "--model or LLM_MODEL."
        )
    resolved_model = _trimmed(model if model is not None else environment_model)
    resolved_document_input = _normalized(
        document_input
        if document_input is not None
        else environment.get("LLM_DOCUMENT_INPUT", DEFAULT_DOCUMENT_INPUT)
    )

    if resolved_document_input not in SUPPORTED_DOCUMENT_INPUTS:
        raise ValueError(
            f"Unsupported document input {resolved_document_input!r}. "
            f"Choose one of: {', '.join(sorted(SUPPORTED_DOCUMENT_INPUTS))}."
        )
    if not resolved_model:
        raise ValueError("Model must not be empty.")

    return ModelSelection(resolved_provider, resolved_model, resolved_document_input)


def resolve_api_key(
    selection: ModelSelection,
    environment: Mapping[str, str] | None = None,
) -> tuple[str | None, str]:
    """Return the selected provider's key and the environment variable name."""
    environment = os.environ if environment is None else environment
    key_environment_names = API_KEY_ENVIRONMENTS[selection.provider]
    for environment_name in key_environment_names:
        key = environment.get(environment_name)
        if key:
            return key, environment_name
    return None, key_environment_names[0]


def _normalized(value: str | None) -> str:
    return _trimmed(value).lower()


def _trimmed(value: str | None) -> str:
    return (value or "").strip()
