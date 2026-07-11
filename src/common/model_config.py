"""Provider-neutral model selection and credential environment lookup."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

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
        provider if provider is not None else environment.get("LLM_PROVIDER", DEFAULT_PROVIDER)
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
