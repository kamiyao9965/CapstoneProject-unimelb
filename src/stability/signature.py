from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# The dimensions we track for drift. Each maps to a set of identifier strings
# pulled out of a schema so two schemas can be compared set-against-set.
DIMENSIONS = ("product_types", "fields", "hospital_categories", "extras_services")


@dataclass(frozen=True)
class SchemaSignature:
    """Comparable fingerprint of one schema document."""

    label: str
    product_types: frozenset[str] = field(default_factory=frozenset)
    fields: frozenset[str] = field(default_factory=frozenset)
    hospital_categories: frozenset[str] = field(default_factory=frozenset)
    extras_services: frozenset[str] = field(default_factory=frozenset)

    def get(self, dimension: str) -> frozenset[str]:
        return getattr(self, dimension)


def _canonical_names(items: object, key: str) -> frozenset[str]:
    """Collect a key (e.g. name / canonical_name) from a list of mappings."""
    if not isinstance(items, list):
        return frozenset()
    names = set()
    for item in items:
        if isinstance(item, dict) and item.get(key):
            names.add(str(item[key]).strip())
    return frozenset(names)


def signature_from_text(text: str, label: str) -> SchemaSignature:
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{label}: schema is not a YAML mapping")

    product_types = data.get("product_types") or []
    pt = frozenset(str(p).strip() for p in product_types) if isinstance(product_types, list) else frozenset()

    return SchemaSignature(
        label=label,
        product_types=pt,
        fields=_canonical_names(data.get("fields"), "name"),
        hospital_categories=_canonical_names(data.get("hospital_categories"), "canonical_name"),
        extras_services=_canonical_names(data.get("extras_services"), "canonical_name"),
    )


def signature_from_file(path: str | Path) -> SchemaSignature:
    path = Path(path)
    return signature_from_text(path.read_text(encoding="utf-8"), label=path.name)
