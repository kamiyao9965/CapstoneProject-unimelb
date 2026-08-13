from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from src.evaluation.taxonomy import canonical_extras_services, canonical_hospital_category, normalized_name


def adapt_final_schema_for_evaluation(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the private-health extraction in the ground-truth section shape.

    Ground-truth-shaped payloads are accepted too.  This makes the adapter safe
    at the evaluation boundary while extraction remains coupled only to the
    discovered final schema.
    """
    adapted: dict[str, Any] = {}
    product_name = payload.get("product_name")

    hospital = payload.get("hospital")
    if isinstance(hospital, dict):
        adapted["hospital"] = deepcopy(hospital)
    final_categories = payload.get("hospital_clinical_categories")
    if isinstance(final_categories, list):
        section = adapted.setdefault("hospital", {})
        section["clinical_categories"] = [
            mapped
            for item in final_categories
            if (mapped := _adapt_category(item)) is not None
        ]
        _set_if_present(section, "product_name", product_name)
        _set_if_present(
            section,
            "hospital_tier",
            payload.get("hospital_tier") or payload.get("product_tier"),
        )

    extras = payload.get("extras")
    if isinstance(extras, dict):
        adapted["extras"] = deepcopy(extras)
    final_benefits = payload.get("extras_benefits")
    if isinstance(final_benefits, list):
        section = adapted.setdefault("extras", {})
        services = []
        for item in final_benefits:
            mapped = _adapt_service(item)
            if mapped is None:
                continue
            canonical_names = canonical_extras_services(mapped["service"])
            for canonical in canonical_names:
                expanded = deepcopy(mapped)
                expanded["service"] = canonical
                shared_with = [
                    name for name in expanded.get("shared_with", []) if name != canonical
                ]
                if shared_with:
                    expanded["shared_with"] = shared_with
                else:
                    expanded.pop("shared_with", None)
                services.append(expanded)
        services = _merge_duplicate_services(services)
        _merge_waiting_periods(services, payload.get("waiting_periods"))
        _merge_annual_limits(services, payload.get("annual_limits"))
        section["services"] = services
        _set_if_present(section, "product_name", product_name)

    # Preserve the already-supported canonical product section.  Final-schema
    # product fields are represented inside hospital/extras, as they are in GT.
    if isinstance(payload.get("product"), dict):
        adapted["product"] = deepcopy(payload["product"])
    return adapted


def _adapt_category(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    category = item.get("category", item.get("category_name"))
    if not category:
        return None
    result = {"category": canonical_hospital_category(category)}
    coverage = item.get("coverage", item.get("status"))
    if isinstance(coverage, str):
        coverage = {
            "covered": "Covered",
            "notcovered": "NotCovered",
            "restricted": "Restricted",
        }.get(_name(coverage), coverage)
    _set_if_present(result, "coverage", coverage)
    return result


def _adapt_service(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    service = item.get("service", item.get("service_name", item.get("name")))
    if not service:
        return None
    if _is_non_service_row(service):
        return None
    result = {"service": service}
    # A row in extras_benefits denotes an included benefit unless extraction
    # explicitly says otherwise.
    result["covered"] = item.get("covered", True)
    for key in ("waiting_period", "limit_per_person", "limit_per_policy", "shared_with"):
        _set_if_present(result, key, item.get(key))
    _merge_limit_value(result, item)
    combined = item.get("combined_limits", item.get("combined_with"))
    if combined:
        result["shared_with"] = _canonical_names(combined, exclude=service)
    return result


_NON_SERVICE_ROW_LABELS = {
    "annual limit", "annual limits", "benefit", "benefit amount",
    "benefit limit", "combined limit", "description", "example", "examples",
    "maximum benefit", "notes", "per visit", "per visit benefit",
    "provider", "providers", "service limit", "waiting period",
    "waiting periods",
}


def _is_non_service_row(value: Any) -> bool:
    label = " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))
    if label in _NON_SERVICE_ROW_LABELS:
        return True
    return bool(re.fullmatch(
        r"(?:initial|subsequent|standard) (?:consultation|visit|treatment)s?", label
    ))


def _merge_waiting_periods(services: list[dict[str, Any]], periods: Any) -> None:
    if not isinstance(periods, list):
        return
    by_name = {_name(item["service"]): item for item in services}
    for period in periods:
        if not isinstance(period, dict):
            continue
        for canonical in canonical_extras_services(period.get("service")):
            target = by_name.get(_name(canonical))
            if target is not None and target.get("waiting_period") is None:
                _set_if_present(target, "waiting_period", period.get("waiting_period", period.get("period")))


def _merge_annual_limits(services: list[dict[str, Any]], limits: Any) -> None:
    if not isinstance(limits, list):
        return
    by_name = {_name(item["service"]): item for item in services}
    for limit in limits:
        if not isinstance(limit, dict):
            continue
        amount = limit.get("limit_amount", limit.get("amount"))
        for canonical in canonical_extras_services(limit.get("service")):
            target = by_name.get(_name(canonical))
            if target is None:
                continue
            if amount is not None:
                per = str(limit.get("per") or "").lower()
                key = "limit_per_policy" if any(word in per for word in ("policy", "family")) else "limit_per_person"
                target.setdefault(key, _limit_amount(amount))
            shared = _canonical_names(limit.get("combined_with"), exclude=canonical)
            if shared:
                target["shared_with"] = sorted(set(target.get("shared_with", [])) | set(shared))


def _merge_limit_value(target: dict[str, Any], source: dict[str, Any]) -> None:
    amount = source.get("limit")
    if amount is None or amount == "":
        return
    per = str(source.get("per") or "").casefold()
    key = "limit_per_policy" if any(word in per for word in ("policy", "family")) else "limit_per_person"
    target.setdefault(key, _limit_amount(amount))


def _canonical_names(value: Any, *, exclude: Any = None) -> list[str]:
    if not value:
        return []
    raw_names = value if isinstance(value, list) else re.split(r"[,/&+]|\band\b|\bwith\b", str(value), flags=re.I)
    names: list[str] = []
    excluded = set(canonical_extras_services(exclude))
    canonical_surface = {
        "Acupuncture", "Audiology", "ChineseHerbalMedicine", "Chiropractic",
        "DentalGeneral", "DentalMajor", "Dietetics", "Endodontic",
        "ExercisePhysiology", "HearingAids", "HomeNursing", "NonPBS",
        "OccupationalTherapy", "Optical", "Orthodontic", "Orthoptics", "Orthotics",
        "Osteopathy", "Physiotherapy", "Podiatry", "Psychology", "RemedialMassage",
        "SpeechTherapy", "Vaccinations",
    }
    for raw_name in raw_names:
        names.extend(name for name in canonical_extras_services(raw_name) if name in canonical_surface)
    return sorted({name for name in names if name not in excluded})


def _limit_amount(value: Any) -> Any:
    if isinstance(value, int | float):
        return value
    match = re.search(r"(?:AUD\s*)?\$\s*([0-9][0-9,]*(?:\.\d+)?)", str(value), re.I)
    if not match:
        return value
    number = float(match.group(1).replace(",", ""))
    return int(number) if number.is_integer() else number


def _merge_duplicate_services(services: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for service in services:
        key = normalized_name(service["service"])
        target = merged.setdefault(key, {"service": service["service"], "covered": service.get("covered", True)})
        for field, value in service.items():
            if field in {"service", "covered"} or value in (None, [], ""):
                continue
            if field == "shared_with":
                target[field] = sorted(set(target.get(field, [])) | set(value))
            else:
                target.setdefault(field, value)
    return list(merged.values())


def _name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _set_if_present(target: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        target[key] = value
