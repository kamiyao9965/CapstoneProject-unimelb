from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from src.schema.validation import SUPPORTED_PRODUCT_TYPES

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OVERRIDE_PATH = PROJECT_ROOT / "configs/private_health/product_type_overrides.json"
IGNORE_MARKERS = {"", "ignore", "ignored", "exclude", "excluded", "skip"}


@dataclass(frozen=True)
class ProductTypeResolution:
    directory_product_type: str | None
    override_product_type: str | None
    effective_product_type: str | None
    conflict: bool = False


def load_product_type_overrides(
    path: str | Path | None = DEFAULT_OVERRIDE_PATH,
) -> dict[str, str | None]:
    if path is None:
        return {}
    override_path = Path(path)
    if not override_path.exists():
        return {}
    payload = json.loads(override_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Product type override file must be a JSON object: {override_path}")

    overrides: dict[str, str | None] = {}
    for raw_key, raw_value in payload.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise ValueError("Product type override keys must be non-empty path strings.")
        if raw_value is None:
            overrides[raw_key] = None
            continue
        if not isinstance(raw_value, str):
            raise ValueError(f"Product type override for {raw_key!r} must be a string or null.")
        normalized = raw_value.strip().lower()
        if normalized in IGNORE_MARKERS:
            overrides[raw_key] = None
        elif normalized in SUPPORTED_PRODUCT_TYPES:
            overrides[raw_key] = normalized
        else:
            raise ValueError(
                f"Product type override for {raw_key!r} has unsupported value {raw_value!r}."
            )
    return overrides


def resolve_product_type(
    pdf_path: str | Path,
    *,
    input_root: str | Path | None = None,
    categories: tuple[str, ...] = tuple(sorted(SUPPORTED_PRODUCT_TYPES)),
    overrides: Mapping[str, str | None] | None = None,
) -> ProductTypeResolution:
    path = Path(pdf_path)
    directory_product_type = directory_product_type_for_path(path, categories)
    override_product_type = _override_for_path(path, input_root, overrides or {})
    effective_product_type = (
        override_product_type
        if override_product_type is not None or _has_override(path, input_root, overrides or {})
        else directory_product_type
    )
    return ProductTypeResolution(
        directory_product_type=directory_product_type,
        override_product_type=override_product_type,
        effective_product_type=effective_product_type,
        conflict=(
            directory_product_type is not None
            and override_product_type is not None
            and directory_product_type != override_product_type
        ),
    )


def directory_product_type_for_path(
    pdf_path: str | Path,
    categories: tuple[str, ...] = tuple(sorted(SUPPORTED_PRODUCT_TYPES)),
) -> str | None:
    parts = [part.lower() for part in Path(pdf_path).parts]
    return next((item for item in categories if item in parts), None)


def _override_for_path(
    path: Path,
    input_root: str | Path | None,
    overrides: Mapping[str, str | None],
) -> str | None:
    matched = _matching_override_key(path, input_root, overrides)
    return overrides[matched] if matched is not None else None


def _has_override(
    path: Path,
    input_root: str | Path | None,
    overrides: Mapping[str, str | None],
) -> bool:
    return _matching_override_key(path, input_root, overrides) is not None


def _matching_override_key(
    path: Path,
    input_root: str | Path | None,
    overrides: Mapping[str, str | None],
) -> str | None:
    candidates = _path_match_candidates(path, input_root)
    for key in overrides:
        normalized_key = _normalize_path_text(key)
        if normalized_key in candidates:
            return key
        if any(candidate.endswith(normalized_key) for candidate in candidates):
            return key
        if any(normalized_key.endswith(candidate) for candidate in candidates):
            return key
    return None


def _path_match_candidates(path: Path, input_root: str | Path | None) -> set[str]:
    candidates = {_normalize_path_text(path.as_posix()), _normalize_path_text(str(path))}
    try:
        candidates.add(_normalize_path_text(path.resolve().as_posix()))
    except OSError:
        pass
    if input_root is not None:
        root = Path(input_root)
        try:
            candidates.add(_normalize_path_text(path.relative_to(root).as_posix()))
        except ValueError:
            pass
        try:
            resolved_path = path.resolve()
            resolved_root = root.resolve()
            candidates.add(_normalize_path_text(resolved_path.relative_to(resolved_root).as_posix()))
        except (OSError, ValueError):
            pass
    return candidates


def _normalize_path_text(value: str) -> str:
    return value.replace("\\", "/").strip().lower().lstrip("./")
