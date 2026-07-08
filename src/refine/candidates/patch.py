"""Schema patch data model and YAML IO for consensus refinement."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SUPPORTED_PATCH_TYPES = {
    "add_field",
    "rename_field",
    "merge_fields",
    "move_field_group",
    "update_description",
    "add_alias",
    "reject_field",
}


@dataclass(frozen=True)
class EvidenceDocument:
    path: str
    quote_or_summary: str = ""

    @classmethod
    def from_value(cls, value: object) -> "EvidenceDocument":
        if isinstance(value, str):
            return cls(path=value)
        if isinstance(value, dict):
            return cls(
                path=str(value.get("path") or value.get("document") or ""),
                quote_or_summary=str(
                    value.get("quote_or_summary") or value.get("summary") or ""
                ),
            )
        return cls(path=str(value))

    def to_dict(self) -> dict[str, str]:
        payload = {"path": self.path}
        if self.quote_or_summary:
            payload["quote_or_summary"] = self.quote_or_summary
        return payload


@dataclass(frozen=True)
class SchemaPatch:
    patch_type: str
    target_group: str
    field_name: str
    canonical_name: str
    field_type: str = "string"
    description: str = ""
    evidence_documents: tuple[EvidenceDocument, ...] = field(default_factory=tuple)
    confidence: float = 0.0
    rationale: str = ""
    source_run: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any], source_run: str = "") -> "SchemaPatch":
        field_name = str(value.get("field_name") or value.get("name") or "").strip()
        canonical_name = str(value.get("canonical_name") or field_name).strip()
        evidence = tuple(
            EvidenceDocument.from_value(item)
            for item in value.get("evidence_documents", []) or []
        )

        return cls(
            patch_type=str(value.get("patch_type") or "").strip(),
            target_group=str(value.get("target_group") or value.get("group") or "").strip(),
            field_name=field_name,
            canonical_name=canonical_name,
            field_type=str(value.get("type") or value.get("field_type") or "string").strip(),
            description=str(value.get("description") or "").strip(),
            evidence_documents=evidence,
            confidence=_float_or_zero(value.get("confidence")),
            rationale=str(value.get("rationale") or "").strip(),
            source_run=source_run,
        )

    def validate(self) -> None:
        if self.patch_type not in SUPPORTED_PATCH_TYPES:
            raise ValueError(f"Unsupported patch_type: {self.patch_type}")
        if not self.field_name and not self.canonical_name:
            raise ValueError("Patch must include field_name or canonical_name.")

    def to_dict(self) -> dict[str, object]:
        return {
            "patch_type": self.patch_type,
            "target_group": self.target_group,
            "field_name": self.field_name,
            "canonical_name": self.canonical_name,
            "type": self.field_type,
            "description": self.description,
            "evidence_documents": [
                document.to_dict() for document in self.evidence_documents
            ],
            "confidence": self.confidence,
            "rationale": self.rationale,
        }


def load_patch_file(path: str | Path) -> list[SchemaPatch]:
    payload = load_yaml(path)
    return parse_patch_payload(payload, source_run=Path(path).stem)


def parse_patch_payload(payload: object, source_run: str = "") -> list[SchemaPatch]:
    if isinstance(payload, list):
        raw_patches = payload
    elif isinstance(payload, dict):
        raw_patches = payload.get("patches", [])
    else:
        raise ValueError("Patch YAML must be a list or an object with a patches list.")

    patches: list[SchemaPatch] = []
    for item in raw_patches or []:
        if not isinstance(item, dict):
            raise ValueError("Each patch must be a YAML object.")
        patch = SchemaPatch.from_dict(item, source_run=source_run)
        patch.validate()
        patches.append(patch)
    return patches


def load_yaml(path: str | Path) -> object:
    yaml = _require_yaml()
    with Path(path).open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def parse_yaml_text(text: str) -> object:
    return _require_yaml().safe_load(text)


def dump_yaml(payload: object, path: str | Path) -> None:
    yaml = _require_yaml()
    resolved_path = Path(path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    with resolved_path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(payload, file, sort_keys=False, allow_unicode=True)


def _require_yaml() -> Any:
    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "PyYAML is required for consensus refinement. "
            "Run: pip install -r requirements.txt"
        ) from exc
    return yaml


def _float_or_zero(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
