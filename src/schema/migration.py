"""Explicit compatibility migrations for persisted discovered schemas."""

from __future__ import annotations

import re
from copy import deepcopy
from collections.abc import Mapping


SAMPLE_SOURCE_LOCATION = re.compile(
    r"(?ix)"
    r"(?:\btable_id\s*[:=]\s*[a-z0-9_.-]+)"
    r"|(?:\bpages?\s*(?:number\s*)?[:#]?\s*\d+(?:\s*[-–]\s*\d+)?)"
    r"|(?:\bp\d+(?:_t\d+)?(?:\s*[-–]\s*p?\d+(?:_t\d+)?)?\b)"
)


_REQUIRED_EXTRAS_TAXONOMY: tuple[dict[str, object], ...] = (
    {
        "canonical_name": "endodontic",
        "aliases": ["endodontics", "root canal", "root canal treatment"],
        "description": (
            "Endodontic treatment, including root-canal treatment. Keep this "
            "separate from major dental when it is listed separately."
        ),
    },
    {
        "canonical_name": "vaccinations",
        "aliases": ["vaccines", "immunisations", "serum and vaccine"],
        "description": (
            "Vaccinations, immunisations, and eligible vaccine or serum benefits."
        ),
    },
    {
        "canonical_name": "glucose_monitor",
        "aliases": [
            "glucose monitor",
            "blood glucose monitor",
            "diabetic supplies",
        ],
        "description": (
            "Blood-glucose monitoring devices and eligible diabetic monitoring supplies."
        ),
    },
)


def migrate_legacy_discovered_schema(
    payload: Mapping[str, object],
) -> dict[str, object]:
    """Return a current-schema copy without mutating a persisted legacy value."""
    migrated = deepcopy(dict(payload))
    description = migrated.get("description")
    if isinstance(description, str):
        migrated["description"] = _remove_source_locations(description)

    fields = migrated.get("fields")
    if not isinstance(fields, list):
        return migrated
    for field in fields:
        if not isinstance(field, dict):
            continue
        field_description = field.get("description")
        if isinstance(field_description, str):
            field["description"] = _remove_source_locations(field_description)
        item_fields = field.get("item_fields")
        if isinstance(item_fields, list):
            for item in item_fields:
                if isinstance(item, dict) and isinstance(item.get("description"), str):
                    item["description"] = _remove_source_locations(item["description"])
        _migrate_canonical_item_policy(field, item_fields)
    _migrate_extras_taxonomy(migrated, fields)
    return migrated


def _migrate_extras_taxonomy(
    schema: dict[str, object], fields: list[object]
) -> None:
    """Keep the extraction enum aligned with the evaluator's PHIS surface."""
    uses_extras_taxonomy = any(
        isinstance(field, dict)
        and field.get("name") == "extras_benefits"
        and isinstance(field.get("item_fields"), list)
        and any(
            isinstance(item, dict) and item.get("enum_ref") == "extras_services"
            for item in field["item_fields"]
        )
        for field in fields
    )
    services = schema.get("extras_services")
    if not uses_extras_taxonomy or not isinstance(services, list):
        return

    owned_aliases = {
        _taxonomy_key(alias)
        for policy in _REQUIRED_EXTRAS_TAXONOMY
        for alias in policy["aliases"]
    } | {
        _taxonomy_key(policy["canonical_name"])
        for policy in _REQUIRED_EXTRAS_TAXONOMY
    }
    required_names = {
        str(policy["canonical_name"]) for policy in _REQUIRED_EXTRAS_TAXONOMY
    }
    for service in services:
        if not isinstance(service, dict):
            continue
        if service.get("canonical_name") in required_names:
            continue
        aliases = service.get("aliases")
        if isinstance(aliases, list):
            service["aliases"] = [
                alias for alias in aliases if _taxonomy_key(alias) not in owned_aliases
            ]

    by_name = {
        service.get("canonical_name"): service
        for service in services
        if isinstance(service, dict)
    }
    for policy in _REQUIRED_EXTRAS_TAXONOMY:
        name = str(policy["canonical_name"])
        current = by_name.get(name)
        if current is None:
            services.append(deepcopy(policy))
            continue
        current.update(deepcopy(policy))


def _taxonomy_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _migrate_canonical_item_policy(
    field: dict[str, object], item_fields: object
) -> None:
    if not isinstance(item_fields, list):
        return
    # Import lazily so validation remains the single policy source without a
    # module-import cycle: validation imports only the source-location regex.
    from src.schema.validation import CANONICAL_ITEM_FIELD_POLICIES

    policy = CANONICAL_ITEM_FIELD_POLICIES.get(str(field.get("name")))
    if policy is None:
        return
    legacy_names = policy.get("legacy_item_names")
    if isinstance(legacy_names, dict):
        existing_names = {
            item.get("name") for item in item_fields if isinstance(item, dict)
        }
        for item in item_fields:
            if not isinstance(item, dict):
                continue
            replacement = legacy_names.get(item.get("name"))
            if isinstance(replacement, str) and replacement not in existing_names:
                item["name"] = replacement
                existing_names.add(replacement)

    required_items = policy.get("required_item_fields")
    if not isinstance(required_items, dict):
        return
    by_name = {
        item.get("name"): item
        for item in item_fields
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    for item_name, shape in required_items.items():
        if not isinstance(shape, dict):
            continue
        item = by_name.get(item_name)
        if item is None:
            item = {"name": item_name, "description": None}
            item_fields.append(item)
        item.update(deepcopy(shape))


def _remove_source_locations(value: str) -> str:
    cleaned = SAMPLE_SOURCE_LOCATION.sub("", value)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    cleaned = re.sub(r"\s+([,.;:)])", r"\1", cleaned)
    cleaned = re.sub(r"([(:])\s+", r"\1", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()
