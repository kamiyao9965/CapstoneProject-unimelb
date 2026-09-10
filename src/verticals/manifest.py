"""Validated, versioned configuration for one business vertical."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from src.common.json_contracts import ContractValidationError, load_contract, validate_contract
from src.common.json_codec import loads_json


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VERTICAL = "private_health"
OPERATION_CAPABILITIES = {
    "discover": "discovery", "extract": "extraction", "batch": "extraction",
    "crawl": "acquisition", "refine": "refinement", "canonical_compile": "storage",
    "storage_init": "storage", "storage_load": "storage",
}


class ManifestValidationError(ValueError):
    """Raised when a vertical manifest is unsafe, incomplete, or unsupported."""


@dataclass(frozen=True)
class DocumentModel:
    categories: tuple[str, ...]
    document_types: tuple[str, ...]
    extraction_unit: str
    output_cardinality: str
    category_product_types: Mapping[str, str]


@dataclass(frozen=True)
class VerticalManifest:
    manifest_version: str
    display_name: str
    product_types: tuple[str, ...]
    taxonomies: tuple[str, ...]
    identity_fields: tuple[str, ...]
    consensus_runs: int
    promoted_decisions: frozenset[str]
    manual_only_queue: bool
    protected_fields: frozenset[str]
    vertical: str
    capabilities: Mapping[str, bool]
    documents: DocumentModel
    paths: Mapping[str, str]
    path_environment: Mapping[str, Mapping[str, str]]
    contracts: Mapping[str, str | None]
    prompts: Mapping[str, str | None]
    adapters: Mapping[str, str | None]
    source_path: Path

    def supports(self, capability: str) -> bool:
        return bool(self.capabilities.get(capability, False))

    def require_capability(self, capability: str) -> None:
        if not self.supports(capability):
            raise ManifestValidationError(
                f"Vertical {self.vertical!r} does not support {capability!r}. "
                "Add its contracts, prompts, adapter, and tests before enabling it."
            )

    def path(self, name: str) -> Path:
        value = self.paths.get(name)
        if value is None:
            raise ManifestValidationError(
                f"Vertical {self.vertical!r} does not define path {name!r}."
            )
        environment_override = self.path_environment.get(name)
        configured_root = (
            os.getenv(environment_override["variable"])
            if environment_override
            else None
        )
        if configured_root:
            return (Path(configured_root).expanduser() / environment_override["suffix"]).resolve()

        candidate = Path(value)
        resolved = (
            candidate.resolve()
            if candidate.is_absolute()
            else (PROJECT_ROOT / candidate).resolve()
        )
        try:
            resolved.relative_to(PROJECT_ROOT.resolve())
        except ValueError as exc:
            raise ManifestValidationError(
                f"Manifest path {name!r} must stay inside the project root: {value}"
            ) from exc
        return resolved

    def contract(self, name: str) -> str:
        value = self.contracts.get(name)
        if not value:
            raise ManifestValidationError(
                f"Vertical {self.vertical!r} does not define contract {name!r}."
            )
        return value

    def adapter(self, name: str) -> str:
        value = self.adapters.get(name)
        if not value:
            raise ManifestValidationError(
                f"Vertical {self.vertical!r} does not define adapter {name!r}."
            )
        return value

    def prompt(self, name: str) -> str:
        value = self.prompts.get(name)
        if not value:
            raise ManifestValidationError(
                f"Vertical {self.vertical!r} does not define prompt {name!r}."
            )
        candidate = (self.source_path.parent / value).resolve()
        if not candidate.is_relative_to(self.source_path.parent) or not candidate.is_file():
            raise ManifestValidationError(f"Missing or unsafe prompt {name!r}: {value}")
        return str(candidate)


def discover_manifests(config_root: str | Path | None = None) -> dict[str, VerticalManifest]:
    manifests: dict[str, VerticalManifest] = {}
    for path in sorted(Path(config_root or PROJECT_ROOT / "configs").glob("*/manifest.json")):
        manifest = load_vertical_manifest(path)
        if manifest.vertical in manifests:
            raise ManifestValidationError(f"Duplicate vertical {manifest.vertical!r}.")
        manifests[manifest.vertical] = manifest
    return manifests


def default_manifest_path(vertical: str) -> Path:
    # Directory discovery has no hard-coded vertical list and rejects duplicate codes.
    manifests = discover_manifests()
    if vertical not in manifests:
        raise ManifestValidationError(f"Unknown vertical {vertical!r}; configured verticals: {', '.join(manifests)}.")
    return manifests[vertical].source_path


def resolve_manifest(path=None, *, vertical=None, operation=None) -> VerticalManifest:
    """Resolve CLI defaults and enforce the same operation capability as the UI."""
    capability = OPERATION_CAPABILITIES[operation.replace("-", "_")] if operation else None
    if path:
        manifest = load_vertical_manifest(path)
        if vertical and vertical != manifest.vertical:
            raise ManifestValidationError(f"Vertical {vertical!r} conflicts with manifest vertical {manifest.vertical!r}.")
    else:
        manifests = discover_manifests()
        if vertical is None:
            eligible = [m for m in manifests.values() if not capability or m.supports(capability)]
            manifest = next((m for m in eligible if m.vertical == DEFAULT_VERTICAL), None)
            if manifest is None:
                if len(eligible) != 1:
                    raise ManifestValidationError("Select a manifest supporting this operation.")
                manifest = eligible[0]
        else:
            if vertical not in manifests:
                raise ManifestValidationError(f"Unknown vertical: {vertical!r}.")
            manifest = manifests[vertical]
    if capability:
        manifest.require_capability(capability)
    return manifest


def load_vertical_manifest(path: str | Path) -> VerticalManifest:
    source_path = Path(path).resolve()
    try:
        payload = loads_json(source_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestValidationError(
            f"Could not read vertical manifest {source_path}: {exc}"
        ) from exc
    try:
        validate_contract(payload, "vertical_manifest")
    except ContractValidationError as exc:
        raise ManifestValidationError(str(exc)) from exc
    assert isinstance(payload, dict)
    documents = payload["documents"]
    assert isinstance(documents, dict)
    manifest = VerticalManifest(
        manifest_version=str(payload["manifest_version"]),
        vertical=str(payload["vertical"]),
        display_name=payload["display_name"],
        product_types=tuple(payload["schema"]["product_types"]),
        taxonomies=tuple(payload["schema"]["taxonomies"]),
        identity_fields=tuple(payload["schema"]["identity_fields"]),
        consensus_runs=payload["refinement"]["consensus_runs"],
        promoted_decisions=frozenset(payload["refinement"]["promoted_decisions"]),
        manual_only_queue=payload["refinement"]["manual_only_queue"],
        protected_fields=frozenset(payload["refinement"]["protected_fields"]),
        capabilities=MappingProxyType(dict(payload["capabilities"])),
        documents=DocumentModel(
            categories=tuple(documents["categories"]),
            document_types=tuple(documents["document_types"]),
            extraction_unit=str(documents["extraction_unit"]),
            output_cardinality=str(documents["output_cardinality"]),
            category_product_types=MappingProxyType(dict(documents["category_product_types"])),
        ),
        paths=MappingProxyType(dict(payload["paths"])),
        path_environment=MappingProxyType(
            {
                name: MappingProxyType(dict(config))
                for name, config in payload.get("path_environment", {}).items()
            }
        ),
        contracts=MappingProxyType(dict(payload["contracts"])),
        prompts=MappingProxyType(dict(payload["prompts"])),
        adapters=MappingProxyType(dict(payload["adapters"])),
        source_path=source_path,
    )
    if set(manifest.documents.category_product_types) - set(manifest.documents.categories) or set(manifest.documents.category_product_types.values()) - set(manifest.product_types):
        raise ManifestValidationError("Trusted category/product mapping must use declared categories and product types.")
    for name in manifest.paths:
        manifest.path(name)
    if manifest.contract("discovered_schema") != f"{manifest.vertical}/discovered_schema":
        raise ManifestValidationError("Discovered schema contract vertical conflicts with manifest.")
    try:
        for contract in manifest.contracts.values():
            if contract:
                load_contract(contract, manifest=manifest)
    except ValueError as exc:
        raise ManifestValidationError(str(exc)) from exc
    for name in ("discovery", "patch", "extraction"):
        path = Path(manifest.prompt(name))
        if not path.read_text(encoding="utf-8").strip():
            raise ManifestValidationError(f"Empty prompt {name!r}: {path}")
    return manifest
