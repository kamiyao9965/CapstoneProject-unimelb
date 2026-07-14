"""Strict JSON parsing and serialization for untrusted runtime data."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any


class StrictJSONError(ValueError):
    """Raised when data is not representable as interoperable RFC-style JSON."""


def loads_json(text: str) -> Any:
    """Parse JSON without accepting duplicate keys or non-standard values."""
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except StrictJSONError:
        raise
    except (json.JSONDecodeError, UnicodeError, OverflowError) as exc:
        raise StrictJSONError(str(exc)) from exc
    _validate_json_value(value, path="$")
    return value


def dumps_json(
    value: Any,
    *,
    ensure_ascii: bool = False,
    indent: int | None = None,
    sort_keys: bool = False,
    separators: tuple[str, str] | None = None,
) -> str:
    """Serialize only values that remain valid, unambiguous UTF-8 JSON."""
    _validate_json_value(value, path="$")
    options: dict[str, object] = {
        "ensure_ascii": ensure_ascii,
        "allow_nan": False,
        "indent": indent,
        "sort_keys": sort_keys,
    }
    if separators is not None:
        options["separators"] = separators
    try:
        return json.dumps(value, **options)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise StrictJSONError(str(exc)) from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJSONError(f"Duplicate JSON object key: {key!r}.")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise StrictJSONError(f"Non-standard JSON numeric constant: {value}.")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise StrictJSONError(f"JSON number is outside the finite float range: {value}.")
    return parsed


def _validate_json_value(value: Any, *, path: str) -> None:
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise StrictJSONError(f"Non-finite JSON number at {path}.")
        return
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise StrictJSONError(f"Invalid Unicode string at {path}.") from exc
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise StrictJSONError(f"JSON object key at {path} must be a string.")
            _validate_json_value(key, path=f"{path}.<key>")
            _validate_json_value(item, path=f"{path}.{key}")
        return
    raise StrictJSONError(
        f"Value at {path} has non-JSON type {type(value).__name__!r}."
    )
