from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


_GLOBAL_CATEGORY_LABELS = {
    "allclinicalcategories",
    "allhospitalclinicalcategories",
    "allcategories",
}


def normalize_extraction(
    payload: Mapping[str, object], schema_data: Mapping[str, object]
) -> dict[str, object]:
    """Apply evidence-preserving, deterministic private-health normalization."""
    normalized = deepcopy(dict(payload))
    _filter_extras_benefit_rows(normalized, schema_data)
    categories = normalized.get("hospital_clinical_categories")
    if not isinstance(categories, list):
        return normalized

    canonical_categories = [
        str(item.get("canonical_name"))
        for item in schema_data.get("hospital_categories", [])
        if isinstance(item, Mapping) and item.get("canonical_name")
    ]
    if not canonical_categories:
        return normalized

    expanded: list[object] = []
    for item in categories:
        if not isinstance(item, Mapping):
            expanded.append(item)
            continue
        label = item.get("category_name", item.get("category"))
        if _normalized_label(label) not in _GLOBAL_CATEGORY_LABELS:
            expanded.append(dict(item))
            continue
        # Expansion is allowed only from an explicit global row. Preserve its
        # coverage/status and notes; never infer coverage from the product tier.
        for canonical in canonical_categories:
            replacement = dict(item)
            if "category_name" in replacement:
                replacement["category_name"] = canonical
            else:
                replacement["category"] = canonical
            expanded.append(replacement)
    normalized["hospital_clinical_categories"] = expanded
    return normalized


def _filter_extras_benefit_rows(
    payload: dict[str, object], schema_data: Mapping[str, object]
) -> None:
    benefits = payload.get("extras_benefits")
    if not isinstance(benefits, list):
        return
    allowed = {
        str(item.get("canonical_name"))
        for item in schema_data.get("extras_services", [])
        if isinstance(item, Mapping) and item.get("canonical_name")
    }
    if not allowed:
        return
    filtered = []
    for item in benefits:
        if not isinstance(item, Mapping):
            continue
        service = item.get("service_name", item.get("service", item.get("name")))
        if service in allowed:
            filtered.append(dict(item))
    payload["extras_benefits"] = filtered


def _normalized_label(value: Any) -> str:
    return "".join(character for character in str(value or "").casefold() if character.isalnum())
