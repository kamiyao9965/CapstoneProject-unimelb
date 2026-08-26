"""Schema patch data model and JSON artifact IO for consensus refinement."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from src.common.json_artifacts import (
    build_success_artifact,
    read_artifact,
    write_artifact,
)
from src.common.json_contracts import validate_contract
from src.schema.validation import (
    JSONScalar, SUPPORTED_FIELD_TYPES, SUPPORTED_PRODUCT_TYPES,
    validate_canonical_item_policy, validate_item_field_payload,
    validate_reusable_description,
)


SUPPORTED_PATCH_TYPES = {
    "add_field",
    "rename_field",
    "merge_fields",
    "move_field_group",
    "update_description",
    "update_field_shape",
    "add_alias",
    "reject_field",
}
MANUAL_EDIT_PATCH_TYPES = frozenset(
    {"rename_field", "merge_fields", "move_field_group"}
)


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
class SchemaItemField:
    name: str
    field_type: str
    required: bool
    description: str = ""
    values: tuple[JSONScalar, ...] = field(default_factory=tuple)
    enum_ref: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SchemaItemField":
        return cls(
            name=str(value.get("name") or "").strip(),
            field_type=str(value.get("type") or "").strip(),
            required=value.get("required") is True,
            description=str(value.get("description") or "").strip(),
            values=_scalar_tuple(value.get("values"), "item_fields.values"),
            enum_ref=(str(value["enum_ref"]) if value.get("enum_ref") else None),
        )

    def validate(self, *, field_name: str = "patch", index: int = 0) -> None:
        validate_item_field_payload(
            self.to_dict(), field_name=field_name, index=index
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name, "type": self.field_type, "required": self.required,
            "description": self.description or None, "values": list(self.values),
            "enum_ref": self.enum_ref,
        }


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
    applies_to: tuple[str, ...] = field(default_factory=tuple)
    required: bool | None = None
    values: tuple[JSONScalar, ...] = field(default_factory=tuple)
    enum_ref: str | None = None
    item_fields: tuple[SchemaItemField, ...] = field(default_factory=tuple)
    unique_items: bool = False

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
            confidence=_confidence(value.get("confidence")),
            rationale=str(value.get("rationale") or "").strip(),
            source_run=source_run,
            applies_to=_string_tuple(value.get("applies_to"), "applies_to"),
            required=(
                value.get("required")
                if isinstance(value.get("required"), bool)
                else None
            ),
            values=_scalar_tuple(value.get("values"), "values"),
            enum_ref=(str(value["enum_ref"]) if value.get("enum_ref") else None),
            item_fields=tuple(
                SchemaItemField.from_dict(item)
                for item in (value.get("item_fields") or [])
                if isinstance(item, dict)
            ),
            unique_items=value.get("unique_items") is True,
        )

    def validate(self) -> None:
        if self.patch_type not in SUPPORTED_PATCH_TYPES:
            raise ValueError(f"Unsupported patch_type: {self.patch_type}")
        if not self.field_name and not self.canonical_name:
            raise ValueError("Patch must include field_name or canonical_name.")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Patch confidence must be between 0 and 1.")
        if self.description:
            validate_reusable_description(
                self.description,
                f"Schema patch {self.canonical_name!r} description",
            )
        if self.patch_type in {"add_field", "update_field_shape"}:
            if self.field_type not in SUPPORTED_FIELD_TYPES:
                raise ValueError(f"Unsupported field type: {self.field_type}")
            if not self.description:
                raise ValueError("add_field patch must include a description.")
            if not self.applies_to or set(self.applies_to) - SUPPORTED_PRODUCT_TYPES:
                raise ValueError(
                    "add_field patch applies_to must contain supported product types."
                )
            if self.required is None:
                raise ValueError("add_field patch must declare required as true or false.")
            if self.field_type in {"enum", "list[enum]"}:
                if bool(self.values) == bool(self.enum_ref):
                    raise ValueError("add_field enum patch requires exactly one of values or enum_ref.")
            elif self.values or self.enum_ref:
                raise ValueError("Non-enum add_field patch cannot declare values or enum_ref.")
            if self.field_type == "list[object]":
                if not self.item_fields:
                    raise ValueError("add_field list[object] patch must declare item_fields.")
                for index, item_field in enumerate(self.item_fields):
                    item_field.validate(
                        field_name=self.canonical_name, index=index
                    )
                validate_canonical_item_policy(
                    self.canonical_name,
                    [item_field.to_dict() for item_field in self.item_fields],
                )
            elif self.item_fields:
                raise ValueError("Non-object add_field patch cannot declare item_fields.")
            if self.field_type not in {
                "list[string]", "list[number]", "list[boolean]", "list[enum]"
            } and self.unique_items:
                raise ValueError(
                    f"Patch type {self.field_type!r} cannot enable unique_items."
                )
            if self.canonical_name == "product_type" and (
                self.field_type != "enum"
                or self.values
                or self.enum_ref != "product_types"
                or self.required is not True
            ):
                raise ValueError(
                    "product_type shape patches must use type=enum, values=[], "
                    "enum_ref=product_types, and required=true."
                )
        if self.patch_type == "update_description" and not self.description:
            raise ValueError("update_description patch must include a description.")
        if self.patch_type == "add_alias" and self.field_name == self.canonical_name:
            raise ValueError("add_alias patch must provide an alias distinct from canonical_name.")

    def to_dict(self) -> dict[str, object]:
        return {
            "patch_type": self.patch_type,
            "target_group": self.target_group,
            "field_name": self.field_name,
            "canonical_name": self.canonical_name,
            "type": self.field_type,
            "description": self.description,
            "applies_to": list(self.applies_to),
            "required": self.required,
            "values": list(self.values),
            "enum_ref": self.enum_ref,
            "item_fields": [item.to_dict() for item in self.item_fields],
            "unique_items": self.unique_items,
            "evidence_documents": [
                document.to_dict() for document in self.evidence_documents
            ],
            "confidence": self.confidence,
            "rationale": self.rationale,
        }


def load_patch_file(path: str | Path) -> list[SchemaPatch]:
    artifact = read_artifact(
        path,
        expected_type="candidate_patch_set",
        data_contract="private_health/candidate_patch_set",
    )
    return parse_patch_payload(artifact["data"], source_run=Path(path).stem)


def parse_patch_payload(payload: object, source_run: str = "") -> list[SchemaPatch]:
    if isinstance(payload, list):
        raw_patches = payload
    elif isinstance(payload, dict):
        raw_patches = payload.get("patches", [])
    else:
        raise ValueError("Patch JSON must be an object with a patches list.")

    patches: list[SchemaPatch] = []
    for item in raw_patches or []:
        if not isinstance(item, dict):
            raise ValueError("Each patch must be a JSON object.")
        patch = SchemaPatch.from_dict(item, source_run=source_run)
        patch.validate()
        patches.append(patch)
    return patches


def write_patch_file(
    payload: Mapping[str, object],
    path: str | Path,
    *,
    provenance: Mapping[str, object],
) -> Path:
    artifact = build_success_artifact(
        artifact_type="candidate_patch_set",
        contract_version="1.0.0",
        data=payload,
        provenance=provenance,
        data_contract="private_health/candidate_patch_set",
    )
    return write_artifact(
        path, artifact, data_contract="private_health/candidate_patch_set"
    )


def _confidence(value: object) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Patch confidence must be numeric.") from exc


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"Patch {label} must be a list.")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"Patch {label} must contain strings only.")
    return tuple(value)


def _scalar_tuple(value: object, label: str) -> tuple[JSONScalar, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"Patch {label} must be a list.")
    if any(not isinstance(item, (str, int, float, bool)) for item in value):
        raise ValueError(f"Patch {label} must contain scalar values only.")
    return tuple(value)
