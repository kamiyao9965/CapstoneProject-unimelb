from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from src.models import SchemaSection, VerticalSchema


class SchemaLoader:
    def load(self, schema_path: str | Path) -> VerticalSchema:
        path = Path(schema_path)
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if self._is_entity_schema_v2(payload):
            return self._load_entity_schema_v2(payload)
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
        if self._is_entity_schema_v2(schema.metadata):
            return self._build_entity_v2_field_definitions(schema.metadata)
        lines: list[str] = []
        for section_name, section in schema.active_sections().items():
            lines.append(f"[{section_name}]")
            for field in section.fields:
                description = f" - {field.description}" if field.description else ""
                lines.append(f"- {field.name}: {field.type}{description}")
        return "\n".join(lines)

    def build_enum_constraints(self, schema: VerticalSchema) -> str:
        if self._is_entity_schema_v2(schema.metadata):
            return self._build_entity_v2_enum_constraints(schema.metadata)
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
        if self._is_entity_schema_v2(schema.metadata):
            return self._build_entity_v2_json_schema(schema.metadata)
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
            return {"type": ["string", "null"], "enum": values + [None]}
        if field_type in {"currency", "number"}:
            return {"type": ["number", "null"]}
        if field_type == "bool":
            return {"type": ["boolean", "null"]}
        if field_type.startswith("list["):
            return {"type": "array", "items": {"type": "string"}}
        return {"type": ["string", "null"]}

    def _load_entity_schema_v2(self, payload: dict[str, Any]) -> VerticalSchema:
        canonical_values = payload.get("canonical_values", {})
        hospital_categories = canonical_values.get("hospital_categories", [])
        extras_services = canonical_values.get("extras_services", [])
        return VerticalSchema(
            vertical=payload.get("vertical", "private_health"),
            version=str(payload.get("schema_version", payload.get("version", "1.0"))),
            hospital=SchemaSection(
                canonical_categories=hospital_categories,
                aliases=self._build_default_aliases(hospital_categories),
            ),
            extras=SchemaSection(
                canonical_services=extras_services,
                aliases=self._build_default_aliases(extras_services),
            ),
            metadata={
                "schema_style": "entity_v2",
                "canonical_values": canonical_values,
                "entities": payload.get("entities", {}),
                "maturity_plan": payload.get("maturity_plan", {}),
                "root_object": payload.get("output", {}).get("root_object", "product_document"),
                "null_policy": payload.get("output", {}).get("null_policy"),
            },
        )

    def _build_entity_v2_field_definitions(self, metadata: dict[str, Any]) -> str:
        lines: list[str] = []
        entities = metadata.get("entities", {})
        for entity_name, entity_spec in entities.items():
            lines.append(f"[{entity_name}]")
            for field_name, field_spec in entity_spec.get("fields", {}).items():
                field_type = field_spec.get("type", "string")
                description = field_spec.get("description", "")
                description_suffix = f" - {description}" if description else ""
                lines.append(f"- {field_name}: {field_type}{description_suffix}")
        return "\n".join(lines)

    def _build_entity_v2_enum_constraints(self, metadata: dict[str, Any]) -> str:
        lines: list[str] = []
        entities = metadata.get("entities", {})
        canonical_values = metadata.get("canonical_values", {})
        for entity_name, entity_spec in entities.items():
            for field_name, field_spec in entity_spec.get("fields", {}).items():
                enum_ref = field_spec.get("enum_ref")
                if enum_ref:
                    values = self._resolve_enum_values(enum_ref, canonical_values)
                    if values:
                        lines.append(f"{entity_name}.{field_name}: {', '.join(values)}")
        return "\n".join(lines)

    def _build_entity_v2_json_schema(self, metadata: dict[str, Any]) -> dict[str, Any]:
        entities = metadata.get("entities", {})
        canonical_values = metadata.get("canonical_values", {})
        properties: dict[str, Any] = {}
        required_entities = self._required_entities(metadata)

        for entity_name, entity_spec in entities.items():
            item_properties: dict[str, Any] = {}
            required_fields: list[str] = []
            for field_name, field_spec in entity_spec.get("fields", {}).items():
                item_properties[field_name] = self._entity_field_to_json_schema(field_spec, canonical_values)
                if field_spec.get("required"):
                    required_fields.append(field_name)
            object_schema = {
                "type": "object",
                "properties": item_properties,
                "required": required_fields,
                "additionalProperties": False,
            }
            if self._entity_is_object(entity_name, entity_spec):
                properties[entity_name] = object_schema
            else:
                properties[entity_name] = {"type": "array", "items": object_schema}

        root_required = [name for name in required_entities if name in properties]
        if "product" in properties and "product" not in root_required:
            root_required.append("product")
        return {
            "type": "object",
            "properties": properties,
            "required": root_required,
            "additionalProperties": False,
        }

    def _entity_field_to_json_schema(
        self, field_spec: dict[str, Any], canonical_values: dict[str, list[str]]
    ) -> dict[str, Any]:
        field_type = str(field_spec.get("type", "string"))
        nullable = bool(field_spec.get("nullable", False))
        if field_type == "enum":
            values = self._resolve_enum_values(field_spec.get("enum_ref"), canonical_values)
            schema: dict[str, Any] = {"type": "string", "enum": values}
            if nullable:
                schema["type"] = ["string", "null"]
                schema["enum"] = values + [None]
            return schema

        type_map = {
            "string": "string",
            "integer": "integer",
            "number": "number",
            "boolean": "boolean",
            "bool": "boolean",
        }
        if field_type.startswith("array["):
            inner_type = field_type[6:-1].strip().lower()
            item_type = type_map.get(inner_type, "string")
            return {"type": "array", "items": {"type": item_type}}

        json_type = type_map.get(field_type.lower(), "string")
        if nullable:
            return {"type": [json_type, "null"]}
        return {"type": json_type}

    @staticmethod
    def _required_entities(metadata: dict[str, Any]) -> list[str]:
        maturity_plan = metadata.get("maturity_plan", {})
        return list(maturity_plan.get("mvp", {}).get("required_entities", []))

    @staticmethod
    def _entity_is_object(entity_name: str, entity_spec: dict[str, Any]) -> bool:
        cardinality = str(entity_spec.get("cardinality", "")).strip().lower()
        return entity_name == "product" or cardinality == "1"

    @staticmethod
    def _resolve_enum_values(enum_ref: str | None, canonical_values: dict[str, list[str]]) -> list[str]:
        if not enum_ref:
            return []
        key = enum_ref.split(".", 1)[1] if "." in enum_ref else enum_ref
        return list(canonical_values.get(key, []))

    def _build_default_aliases(self, canonical_names: list[str]) -> dict[str, list[str]]:
        aliases: dict[str, list[str]] = {}
        for name in canonical_names:
            aliases[name] = [name, self._decamelize(name)]
        return aliases

    @staticmethod
    def _decamelize(value: str) -> str:
        return re.sub(r"(?<!^)(?=[A-Z])", " ", value).replace("  ", " ").strip()

    @staticmethod
    def _is_entity_schema_v2(payload: dict[str, Any]) -> bool:
        return bool(payload.get("entities")) and bool(payload.get("canonical_values"))


def merge_aliases(section: SchemaSection) -> dict[str, list[str]]:
    aliases = {key: list(value) for key, value in section.aliases.items()}
    for name in section.canonical_names:
        aliases.setdefault(name, []).append(name)
    return aliases
