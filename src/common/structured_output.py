"""Bounded parse/validate/repair orchestration for model JSON output."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from src.common.json_contracts import (
    ContractValidationError,
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
) -> StructuredOutputResult:
    """Run one request plus bounded validation repair attempts."""
    if request.structured_output is None:
        raise ValueError("Structured output runner requires a structured_output spec.")
    if (data_contract is None) == (data_contract_schema is None):
        raise ValueError(
            "Provide exactly one of data_contract or data_contract_schema."
        )
    if max_repair_attempts < 0:
        raise ValueError("max_repair_attempts must not be negative.")

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
        data, errors = _validate_response(
            response.text,
            data_contract=data_contract,
            data_contract_schema=data_contract_schema,
            business_validator=business_validator,
        )
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
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    try:
        payload = loads_json(text)
    except StrictJSONError as exc:
        return None, [{
            "path": "$",
            "message": f"Invalid strict JSON: {exc}",
        }]

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
