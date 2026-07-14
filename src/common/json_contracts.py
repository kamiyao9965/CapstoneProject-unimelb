"""Authoritative JSON Schema catalog and validation boundary."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from src.common.json_codec import loads_json


CONTRACT_ROOT = Path(__file__).resolve().parents[2] / "contracts"
_CONTRACT_PATHS = {
    "artifact_envelope": "artifact_envelope.schema.json",
    "private_health/discovered_schema": (
        "private_health/discovered_schema.schema.json"
    ),
    "private_health/candidate_patch_set": "private_health/candidate_patch_set.schema.json",
    "private_health/aliases": "private_health/aliases.schema.json",
    "private_health/field_frequency": "private_health/field_frequency.schema.json",
    "private_health/patch_stability": "private_health/patch_stability.schema.json",
    "private_health/review_queue": "private_health/review_queue.schema.json",
    "private_health/review_decisions": "private_health/review_decisions.schema.json",
    "private_health/refinement_feedback": "private_health/refinement_feedback.schema.json",
}


class ContractValidationError(ValueError):
    """Raised when a payload does not satisfy an authoritative JSON contract."""

    def __init__(self, contract_name: str, errors: list[dict[str, str]]) -> None:
        self.contract_name = contract_name
        self.errors = errors
        detail = "; ".join(
            f"{item['path']}: {item['message']}" for item in errors
        )
        super().__init__(f"JSON contract {contract_name!r} validation failed: {detail}")


def load_contract(name: str) -> dict[str, Any]:
    """Load an allowlisted contract by logical name.

    An explicit catalog prevents caller-controlled path traversal and makes the
    contract set reviewable in one place.
    """
    relative_path = _CONTRACT_PATHS.get(name)
    if relative_path is None:
        raise ValueError(f"Unknown JSON contract {name!r}.")
    path = CONTRACT_ROOT / relative_path
    contract = loads_json(path.read_text(encoding="utf-8"))
    if not isinstance(contract, dict):
        raise RuntimeError(f"JSON contract {name!r} must contain an object.")
    return copy.deepcopy(contract)


def validate_contract(payload: Any, name: str) -> Any:
    """Validate a payload and return the same object when it is valid."""
    validator = Draft202012Validator(
        load_contract(name),
        format_checker=FormatChecker(),
    )
    errors = sorted(validator.iter_errors(payload), key=_error_sort_key)
    if errors:
        raise ContractValidationError(name, [_error_detail(error) for error in errors])
    return payload


def validate_inline_contract(
    payload: Any,
    schema: Mapping[str, Any],
    name: str = "inline",
) -> Any:
    """Validate against a runtime-compiled JSON Schema."""
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(payload), key=_error_sort_key)
    if errors:
        raise ContractValidationError(name, [_error_detail(error) for error in errors])
    return payload


def _error_detail(error: ValidationError) -> dict[str, str]:
    path = "$"
    for component in error.absolute_path:
        path += f"[{component}]" if isinstance(component, int) else f".{component}"
    return {"path": path, "message": _safe_error_message(error)}


def _error_sort_key(error: ValidationError) -> tuple[str, str]:
    return (
        "/".join(str(part) for part in error.absolute_path),
        str(error.validator),
    )


_SAFE_PROPERTY_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")


def _safe_error_message(error: ValidationError) -> str:
    """Describe a contract violation without echoing the invalid instance."""
    validator = str(error.validator)
    expected = error.validator_value
    if validator == "type":
        return f"Expected JSON type {expected!r}."
    if validator == "enum":
        return f"Value must be one of the contract enum values: {expected!r}."
    if validator == "const":
        return f"Value must equal the contract constant {expected!r}."
    if validator == "required":
        instance_keys = set(error.instance) if isinstance(error.instance, dict) else set()
        missing = [name for name in expected if name not in instance_keys]
        return f"Properties {missing!r} are required by the contract."
    if validator == "additionalProperties":
        if isinstance(error.instance, dict) and isinstance(error.schema, dict):
            allowed = set(error.schema.get("properties", {}))
            unexpected = sorted(set(error.instance) - allowed)
            if unexpected and all(_SAFE_PROPERTY_NAME.fullmatch(name) for name in unexpected):
                return f"Unexpected properties are not allowed: {unexpected!r}."
        return "One or more unexpected properties are not allowed."
    if validator == "uniqueItems":
        return "Array items must be unique."
    if validator in {
        "minItems", "maxItems", "minLength", "maxLength", "minimum",
        "maximum", "pattern", "format",
    }:
        return f"Value must satisfy {validator}={expected!r}."
    if validator in {"oneOf", "anyOf", "allOf", "if", "then", "else"}:
        return f"Value does not satisfy the contract {validator} rule."
    return f"Value failed contract validator {validator!r}."
