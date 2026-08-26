from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from src.evaluation.taxonomy import canonical_extras_services, canonical_hospital_category, normalized_name


class CompatibilityConflictError(ValueError):
    """Legacy aliases supplied conflicting values at the compatibility boundary."""


def adapt_final_schema_for_evaluation(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the private-health extraction in the ground-truth section shape.

    Ground-truth-shaped payloads are accepted too.  This makes the adapter safe
    at the evaluation boundary while extraction remains coupled only to the
    discovered final schema.
    """
    adapted: dict[str, Any] = {}
    product_name = payload.get("product_name")
    product: dict[str, Any] = (
        deepcopy(payload["product"])
        if isinstance(payload.get("product"), dict)
        else {}
    )
    _set_if_present(product, "product_name", product_name)
    _set_if_present(product, "product_type", payload.get("product_type"))
    _set_if_present(
        product,
        "hospital_tier",
        payload.get("hospital_tier")
        or payload.get("product_tier")
        or payload.get("tier"),
    )
    if product:
        adapted["product"] = product

    hospital = payload.get("hospital")
    if isinstance(hospital, dict):
        adapted["hospital"] = deepcopy(hospital)
    final_categories = payload.get("clinical_categories")
    if not isinstance(final_categories, list):
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
            payload.get("hospital_tier") or payload.get("product_tier") or payload.get("tier"),
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
        _merge_waiting_periods(services, payload.get("extras_waiting_periods"))
        _merge_annual_limits(services, payload.get("annual_limits"))
        _merge_shared_limits(services, payload.get("extras_shared_limits"))
        for service in services:
            service.pop("shared_limit_group", None)
        section["services"] = services
        _set_if_present(section, "product_name", product_name)

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
            "included": "Covered",
            "notcovered": "NotCovered",
            "excluded": "NotCovered",
            "restricted": "Restricted",
        }.get(_name(coverage), coverage)
    _set_if_present(result, "coverage", coverage)
    return result


def _adapt_service(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    service = _legacy_alias_value(item, ("service_name", "service", "name"))
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
    annual_amount = item.get("annual_limit_per_person", item.get("annual_limit_amount_aud"))
    annual_scope = str(item.get("annual_limit_scope") or "per_person").casefold()
    annual_key = (
        "limit_per_policy"
        if annual_scope in {"per_policy", "per_membership"}
        else "limit_per_person"
    )
    annual_type = _name(item.get("annual_limit_type"))
    if annual_amount is None and annual_type in {"unlimited", "noannuallimit", "nolimit"}:
        # The labelled CSV represents an explicitly unlimited service with 0.
        # Keep that legacy convention confined to this evaluation adapter.
        annual_amount = 0
    if annual_amount is None:
        tiered_amounts = _tiered_limit_amounts(item.get("notes"))
        # The evaluation policy compares the entry/base tier. Later loyalty
        # tiers are retained in extraction notes but are not a separate GT row.
        annual_amount = tiered_amounts[0] if tiered_amounts else None
    _set_if_present(result, annual_key, annual_amount)
    _set_if_present(result, "limit_per_policy", item.get("annual_limit_per_policy"))
    _set_if_present(result, "shared_limit_group", item.get("shared_limit_group"))
    _merge_limit_value(result, item)
    combined = item.get("combined_limits", item.get("combined_with"))
    if combined:
        result["shared_with"] = _canonical_names(combined, exclude=service)
    return result


def _legacy_alias_value(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    populated = [(key, item.get(key)) for key in keys if item.get(key) is not None]
    distinct = {str(value) for _, value in populated}
    if len(distinct) > 1:
        details = ", ".join(f"{key}={value!r}" for key, value in populated)
        raise CompatibilityConflictError(
            f"Conflicting legacy aliases for canonical key {keys[0]!r}: {details}"
        )
    return populated[0][1] if populated else None


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
        service_name = period.get("service", period.get("service_name"))
        for canonical in canonical_extras_services(service_name):
            target = by_name.get(_name(canonical))
            if target is not None and target.get("waiting_period") is None:
                value = period.get("waiting_period", period.get("period"))
                if value is None:
                    value = _current_waiting_period(period)
                _set_if_present(target, "waiting_period", value)


def _current_waiting_period(period: dict[str, Any]) -> str | None:
    months = period.get("wait_months", period.get("waiting_period_months"))
    if months is not None:
        return f"{_plain_number(months)} Month"
    days = period.get("wait_days", period.get("waiting_period_days"))
    if days is not None:
        return f"{_plain_number(days)} Day"
    notes = str(period.get("notes") or "").strip()
    if re.fullmatch(
        r"(?:none|nil|no\s+(?:waiting\s+period|wait)|immediate(?:\s+cover)?)\.?",
        notes,
        re.I,
    ):
        return "0 Month"
    return None


def _tiered_limit_amounts(notes: Any) -> list[int | float] | None:
    """Recover a loyalty/tenure limit schedule retained in schema notes."""
    text = str(notes or "")
    if not re.search(r"\b(?:year|years|yrs|tenure|loyalty)\b", text, re.I):
        return None
    amounts = [
        _limit_amount(match.group(0))
        for match in re.finditer(r"(?:AUD\s*)?\$\s*[0-9][0-9,]*(?:\.\d+)?", text, re.I)
    ]
    numeric = [amount for amount in amounts if isinstance(amount, int | float)]
    return numeric if len(numeric) >= 2 else None


def _plain_number(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _merge_annual_limits(services: list[dict[str, Any]], limits: Any) -> None:
    if not isinstance(limits, list):
        return
    by_name = {_name(item["service"]): item for item in services}
    for limit in limits:
        if not isinstance(limit, dict):
            continue
        amount = limit.get("limit_amount", limit.get("amount"))
        limit_type = _name(limit.get("annual_limit_type", limit.get("limit_type")))
        if amount is None and limit_type in {"unlimited", "noannuallimit", "nolimit"}:
            amount = 0
        if amount is None:
            tiered_amounts = _tiered_limit_amounts(limit.get("notes"))
            amount = tiered_amounts[0] if tiered_amounts else None
        service_name = limit.get("service", limit.get("service_name"))
        for canonical in canonical_extras_services(service_name):
            target = by_name.get(_name(canonical))
            if target is None:
                continue
            if amount is not None:
                per = str(limit.get("per") or limit.get("limit_scope") or "").lower()
                key = "limit_per_policy" if any(word in per for word in ("policy", "family")) else "limit_per_person"
                target.setdefault(key, _limit_amount(amount))
            shared = _canonical_names(limit.get("combined_with"), exclude=canonical)
            if shared:
                target["shared_with"] = sorted(set(target.get("shared_with", [])) | set(shared))


def _merge_shared_limits(services: list[dict[str, Any]], groups: Any) -> None:
    if not isinstance(groups, list):
        return
    groups_by_id: dict[str, list[dict[str, Any]]] = {}
    for group in groups:
        if isinstance(group, dict) and group.get("shared_limit_group"):
            groups_by_id.setdefault(str(group["shared_limit_group"]), []).append(group)
    members: dict[str, list[dict[str, Any]]] = {}
    for service in services:
        group_id = service.get("shared_limit_group")
        if group_id:
            members.setdefault(str(group_id), []).append(service)
    for group_id, grouped_services in members.items():
        names = {str(service["service"]) for service in grouped_services}
        for service in grouped_services:
            for group in groups_by_id.get(group_id, []):
                amount = group.get("amount_aud")
                scope = str(group.get("limit_scope") or "per_person").casefold()
                limit_key = (
                    "limit_per_policy"
                    if scope in {"per_policy", "per_membership"}
                    else "limit_per_person"
                )
                if amount is not None:
                    service.setdefault(limit_key, amount)
            others = sorted(names - {str(service["service"])})
            if others:
                service["shared_with"] = sorted(
                    set(service.get("shared_with", [])) | set(others)
                )


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
