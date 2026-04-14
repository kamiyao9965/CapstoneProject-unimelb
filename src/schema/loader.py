from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.models import SchemaSection, VerticalSchema


class SchemaLoader:
    def load(self, schema_path: str | Path) -> VerticalSchema:
        path = Path(schema_path)
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        return VerticalSchema.model_validate(payload)

    def dump(self, schema: VerticalSchema, output_path: str | Path) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(schema.model_dump(mode="python"), sort_keys=False, allow_unicode=False),
            encoding="utf-8",
        )
        return path

    def build_field_definitions(self, schema: VerticalSchema) -> str:
        lines: list[str] = []
        for section_name, section in schema.active_sections().items():
            lines.append(f"[{section_name}]")
            for field in section.fields:
                description = f" - {field.description}" if field.description else ""
                lines.append(f"- {field.name}: {field.type}{description}")
        return "\n".join(lines)

    def build_enum_constraints(self, schema: VerticalSchema) -> str:
        lines: list[str] = []
        for section_name, section in schema.active_sections().items():
            for field in section.fields:
                if field.values:
                    joined = ", ".join(field.values)
                    lines.append(f"{section_name}.{field.name}: {joined}")
            canonical_names = section.canonical_names
            if canonical_names:
                lines.append(f"{section_name}.canonical_names: {', '.join(canonical_names)}")
        return "\n".join(lines)

    def build_json_schema(self, schema: VerticalSchema) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        for section_name, section in schema.active_sections().items():
            section_properties: dict[str, Any] = {}
            required: list[str] = []
            for field in section.fields:
                section_properties[field.name] = self._field_to_json_schema(field.type, field.values)
                required.append(field.name)
            if section.canonical_categories:
                section_properties["clinical_categories"] = {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "enum": section.canonical_categories},
                            "coverage": {
                                "type": ["string", "null"],
                                "enum": ["Covered", "Restricted", "Excluded"],
                            },
                        },
                        "required": ["category", "coverage"],
                        "additionalProperties": False,
                    },
                }
            if section.canonical_services:
                section_properties["services"] = {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "service": {"type": "string", "enum": section.canonical_services},
                            "covered": {"type": ["boolean", "null"]},
                            "waiting_period": {"type": ["string", "null"]},
                            "limit_per_person": {"type": ["number", "null"]},
                            "limit_per_policy": {"type": ["number", "null"]},
                            "shared_with": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": [
                            "service",
                            "covered",
                            "waiting_period",
                            "limit_per_person",
                            "limit_per_policy",
                            "shared_with",
                        ],
                        "additionalProperties": False,
                    },
                }
            properties[section_name] = {
                "type": "object",
                "properties": section_properties,
                "required": required,
                "additionalProperties": False,
            }
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }

    def _field_to_json_schema(self, field_type: str, values: list[str]) -> dict[str, Any]:
        field_type = field_type.strip()
        if field_type == "enum":
            return {"type": ["string", "null"], "enum": values}
        if field_type in {"currency", "number"}:
            return {"type": ["number", "null"]}
        if field_type == "bool":
            return {"type": ["boolean", "null"]}
        if field_type.startswith("list["):
            return {"type": "array", "items": {"type": "string"}}
        return {"type": ["string", "null"]}


def merge_aliases(section: SchemaSection) -> dict[str, list[str]]:
    aliases = {key: list(value) for key, value in section.aliases.items()}
    for name in section.canonical_names:
        aliases.setdefault(name, []).append(name)
    return aliases
