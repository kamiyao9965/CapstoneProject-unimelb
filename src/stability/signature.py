from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.common.json_artifacts import read_artifact
from src.common.json_codec import dumps_json, loads_json
from src.common.json_contracts import validate_contract
from src.schema.migration import migrate_legacy_discovered_schema

# The dimensions we track for drift. Each maps to a set of identifier strings
# pulled out of a schema so two schemas can be compared set-against-set.
DIMENSIONS = (
    "product_types",
    "field_contracts",
    "hospital_categories",
    "extras_services",
)


@dataclass(frozen=True)
class SchemaSignature:
    """Comparable fingerprint of one schema document."""

    label: str
    product_types: frozenset[str] = field(default_factory=frozenset)
    fields: frozenset[str] = field(default_factory=frozenset)
    field_contracts: frozenset[str] = field(default_factory=frozenset)
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


def _field_contracts(items: object) -> frozenset[str]:
    """Return deterministic full-field contracts, excluding run annotations."""
    if not isinstance(items, list):
        return frozenset()
    contracts: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        contract = {
            key: value
            for key, value in item.items()
            if key not in {"consensus", "review"}
        }
        for unordered_key in ("applies_to", "values", "aliases"):
            value = contract.get(unordered_key)
            if isinstance(value, list) and all(
                isinstance(element, (str, int, float, bool, type(None)))
                for element in value
            ):
                contract[unordered_key] = sorted(value, key=lambda element: str(element))
        contracts.add(
            dumps_json(contract, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        )
    return frozenset(contracts)


def signature_from_artifact(artifact: object, label: str) -> SchemaSignature:
    validate_contract(artifact, "artifact_envelope")
    if not isinstance(artifact, dict) or artifact.get("status") != "success":
        raise ValueError(f"{label}: schema artifact is not successful")
    if artifact.get("artifact_type") != "discovered_schema":
        raise ValueError(f"{label}: expected a discovered_schema artifact")
    data = migrate_legacy_discovered_schema(artifact["data"])
    validate_contract(data, "private_health/discovered_schema")

    product_types = data.get("product_types") or []
    pt = (
        frozenset(str(product_type).strip() for product_type in product_types)
        if isinstance(product_types, list)
        else frozenset()
    )

    return SchemaSignature(
        label=label,
        product_types=pt,
        fields=_canonical_names(data.get("fields"), "name"),
        field_contracts=_field_contracts(data.get("fields")),
        hospital_categories=_canonical_names(data.get("hospital_categories"), "canonical_name"),
        extras_services=_canonical_names(data.get("extras_services"), "canonical_name"),
    )


def signature_from_text(text: str, label: str) -> SchemaSignature:
    """Parse a JSON artifact string and build its semantic signature."""
    try:
        artifact = loads_json(text)
    except ValueError as exc:
        raise ValueError(f"{label}: schema artifact is not valid strict JSON: {exc}") from exc
    return signature_from_artifact(artifact, label)


def signature_from_file(path: str | Path) -> SchemaSignature:
    path = Path(path)
    artifact = read_artifact(
        path,
        expected_type="discovered_schema",
        data_contract="private_health/discovered_schema",
    )
    return signature_from_artifact(artifact, label=path.name)
