"""Bounded parse/validate/repair orchestration for model JSON output."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from src.common.json_contracts import (
    ContractValidationError,
    load_contract,
    validate_contract,
    validate_inline_contract,
)
from src.common.json_codec import StrictJSONError, loads_json
from src.common.model_provider import (
    ModelProvider,
    ModelResponse,
    ProviderRequest,
    ProviderResponseError,
)


@dataclass(frozen=True)
class StructuredAttempt:
    number: int
    response: ModelResponse
    errors: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class StructuredOutputResult:
    data: dict[str, Any] | None
    attempts: tuple[StructuredAttempt, ...]
    errors: tuple[dict[str, str], ...] = ()


class StructuredOutputFailure(RuntimeError):
    """Exhausted structured-output validation without exposing invalid data."""

    def __init__(self, result: StructuredOutputResult) -> None:
        self.result = result
        detail = "; ".join(
            f"{item['path']}: {item['message']}" for item in result.errors
        )
        super().__init__(
            "Structured output remained invalid or failed after "
            f"{len(result.attempts)} attempt(s)"
            + (f": {detail}" if detail else ".")
        )


def run_structured_output(
    provider: ModelProvider,
    request: ProviderRequest,
    *,
    data_contract: str | None = None,
    data_contract_schema: Mapping[str, Any] | None = None,
    business_validator: Callable[[object], object] | None = None,
    max_repair_attempts: int = 2,
    drop_structural_noise: bool = False,
) -> StructuredOutputResult:
    """Run one request plus bounded validation repair attempts.

    ``drop_structural_noise`` cleans formatting noise that carries no contract
    data before validation: undeclared double-underscore keys such as
    ``__typename`` are removed (or renamed to a missing declared key such as
    ``_document_notes``) and duplicate items leave unique string lists. Every
    other contract violation still fails, and each cleanup is logged.
    """
    if request.structured_output is None:
        raise ValueError("Structured output runner requires a structured_output spec.")
    if (data_contract is None) == (data_contract_schema is None):
        raise ValueError(
            "Provide exactly one of data_contract or data_contract_schema."
        )
    if max_repair_attempts < 0:
        raise ValueError("max_repair_attempts must not be negative.")
    noise_schema: Mapping[str, Any] | None = None
    if drop_structural_noise:
        noise_schema = data_contract_schema if data_contract_schema is not None else load_contract(str(data_contract))

    attempts: list[StructuredAttempt] = []
    current_request = request
    for attempt_number in range(1, max_repair_attempts + 2):
        try:
            response = provider.generate(current_request)
        except ProviderResponseError as exc:
            errors = ({"path": "$", "message": str(exc)},)
            attempt = StructuredAttempt(attempt_number, exc.response, errors)
            result = StructuredOutputResult(
                data=None,
                attempts=(*attempts, attempt),
                errors=errors,
            )
            raise StructuredOutputFailure(result) from exc
        noise_notes: list[str] = []
        data, errors = _validate_response(
            response.text,
            data_contract=data_contract,
            data_contract_schema=data_contract_schema,
            business_validator=business_validator,
            noise_schema=noise_schema,
            noise_notes=noise_notes,
        )
        if noise_notes and current_request.log:
            current_request.log("Removed structural noise before validation: " + "; ".join(noise_notes))
        attempts.append(
            StructuredAttempt(attempt_number, response, tuple(errors))
        )
        if not errors:
            return StructuredOutputResult(data=data, attempts=tuple(attempts))
        if attempt_number <= max_repair_attempts:
            current_request = replace(
                request,
                user_text=_repair_text(request.user_text, errors, attempt_number),
            )

    result = StructuredOutputResult(
        data=None,
        attempts=tuple(attempts),
        errors=attempts[-1].errors,
    )
    raise StructuredOutputFailure(result)


def _validate_response(
    text: str,
    *,
    data_contract: str | None,
    data_contract_schema: Mapping[str, Any] | None,
    business_validator: Callable[[object], object] | None,
    noise_schema: Mapping[str, Any] | None = None,
    noise_notes: list[str] | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    try:
        payload = loads_json(text)
    except StrictJSONError as exc:
        return None, [{
            "path": "$",
            "message": f"Invalid strict JSON: {exc}",
        }]
    if noise_schema is not None:
        payload = _drop_structural_noise(payload, noise_schema, "$", noise_notes if noise_notes is not None else [])

    try:
        if data_contract is not None:
            validate_contract(payload, data_contract)
        else:
            assert data_contract_schema is not None
            validate_inline_contract(
                payload, data_contract_schema, "runtime_extraction_result"
            )
    except ContractValidationError as exc:
        return None, list(exc.errors)
    if business_validator is not None:
        try:
            business_validator(payload)
        except ValueError as exc:
            return None, [{"path": "$", "message": str(exc)}]
    if not isinstance(payload, dict):
        return None, [{"path": "$", "message": "Output must be a JSON object."}]
    return payload, []


def _drop_structural_noise(
    value: object,
    schema: Mapping[str, Any],
    path: str,
    notes: list[str],
) -> object:
    """Remove formatting noise that carries no contract data; values are never changed."""
    properties = schema.get("properties")
    if isinstance(value, dict) and schema.get("additionalProperties") is False and isinstance(properties, Mapping):
        for key in [key for key in value if key not in properties and key.startswith("__")]:
            noise = value.pop(key)
            # A misspelled declared key such as __document_notes__ keeps its value
            # only when the declared key itself is missing.
            target = next(
                (name for name in properties if name.startswith("_") and name.strip("_") == key.strip("_")),
                None,
            )
            if target is not None and target not in value:
                value[target] = noise
                notes.append(f"{path}: renamed {key!r} to {target!r}")
            else:
                notes.append(f"{path}: removed {key!r}")
        for key, child_schema in properties.items():
            if key in value and isinstance(child_schema, Mapping):
                value[key] = _drop_structural_noise(value[key], child_schema, f"{path}.{key}", notes)
    elif isinstance(value, list):
        if schema.get("uniqueItems") is True and all(isinstance(item, str) for item in value):
            unique = list(dict.fromkeys(value))
            if len(unique) < len(value):
                notes.append(f"{path}: removed {len(value) - len(unique)} duplicate item(s)")
                value = unique
        items = schema.get("items")
        if isinstance(items, Mapping):
            value = [
                _drop_structural_noise(item, items, f"{path}[{index}]", notes)
                for index, item in enumerate(value)
            ]
    return value


def _repair_text(
    original_user_text: str,
    errors: list[dict[str, str]],
    repair_number: int,
) -> str:
    details = "\n".join(
        f"- {item['path']}: {item['message']}" for item in errors
    )
    return (
        f"{original_user_text}\n\n"
        f"Repair attempt {repair_number}. The previous response failed validation:\n"
        f"{details}\n"
        "Return the complete corrected JSON object. Do not omit unchanged fields, "
        "add commentary, or wrap it in Markdown."
    )
