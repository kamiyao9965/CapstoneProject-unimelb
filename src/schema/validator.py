from __future__ import annotations

from src.models import VerticalSchema


class SchemaValidator:
    def validate(self, schema: VerticalSchema) -> list[str]:
        issues: list[str] = []
        if not schema.active_sections():
            issues.append("Schema must define at least one section.")
        for section_name, section in schema.active_sections().items():
            field_names = [field.name for field in section.fields]
            if len(field_names) != len(set(field_names)):
                issues.append(f"{section_name}: duplicate field names found.")
            canonical_names = section.canonical_names
            if canonical_names and len(canonical_names) != len(set(canonical_names)):
                issues.append(f"{section_name}: duplicate canonical names found.")
            missing_aliases = [name for name in canonical_names if name not in section.aliases]
            if missing_aliases:
                issues.append(
                    f"{section_name}: canonical names missing aliases: {', '.join(missing_aliases[:10])}"
                )
        return issues
