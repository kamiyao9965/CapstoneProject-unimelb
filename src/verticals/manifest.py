"""Validated, versioned configuration for one business vertical."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from src.common.json_contracts import ContractValidationError, validate_contract
from src.common.json_codec import loads_json


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFESTS = MappingProxyType(
    {
        "private_health": PROJECT_ROOT / "configs/private_health/manifest.json",
        "travel_insurance": PROJECT_ROOT / "configs/travel_insurance/manifest.json",
    }
)


class ManifestValidationError(ValueError):
    """Raised when a vertical manifest is unsafe, incomplete, or unsupported."""


@dataclass(frozen=True)
class DocumentModel:
    categories: tuple[str, ...]
    document_types: tuple[str, ...]
    extraction_unit: str
    output_cardinality: str


@dataclass(frozen=True)
class VerticalManifest:
    manifest_version: str
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


def default_manifest_path(vertical: str) -> Path:
    try:
        return DEFAULT_MANIFESTS[vertical]
    except KeyError as exc:
        known = ", ".join(sorted(DEFAULT_MANIFESTS))
        raise ManifestValidationError(
            f"Unknown vertical {vertical!r}; configured verticals: {known}."
        ) from exc


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
    return VerticalManifest(
        manifest_version=str(payload["manifest_version"]),
        vertical=str(payload["vertical"]),
        capabilities=MappingProxyType(dict(payload["capabilities"])),
        documents=DocumentModel(
            categories=tuple(documents["categories"]),
            document_types=tuple(documents["document_types"]),
            extraction_unit=str(documents["extraction_unit"]),
            output_cardinality=str(documents["output_cardinality"]),
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
