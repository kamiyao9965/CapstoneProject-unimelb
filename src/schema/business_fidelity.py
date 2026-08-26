from __future__ import annotations

BUSINESS_CRITICAL_FIELDS = (
    "ambulance_benefit",
    "accident_cover",
    "gap_cover",
    "continuity_of_cover",
    "cover_options",
    "clinical_categories",
    "annual_limits",
    "age_based_discount",
    "excess_options",
    "benefit_percentage",
    "tier",
)

BUSINESS_FIELD_ALIASES = {
    "ambulance_benefit": (
        "ambulance_cover",
        "ambulance_coverage",
        "ambulance_included",
    ),
    "annual_limits": ("annual_limit", "overall_annual_limit"),
}

GENERIC_REPLACEMENT_RISKS = {
    "coverage_status": ("clinical_categories",),
    "conditions": (
        "accident_cover",
        "gap_cover",
        "continuity_of_cover",
        "age_based_discount",
        "cover_options",
    ),
    "exclusions": ("accident_cover", "gap_cover", "ambulance_benefit"),
    "annual_limits": ("excess_options",),
    "source_references": (),
}

BUSINESS_FIDELITY_RULES = """
Business fidelity guardrail:
- Do not improve apparent fill rate by deleting or over-generalising important
  comparison fields.
- Critical fields should be preserved unless they are split, renamed, or
  narrowed into equally specific fields with the same business meaning.
- If a critical field is sparse, prefer fixing applies_to, description, type,
  aliases, or nested object shape before dropping it, unless refinement feedback
  explicitly marks it for deletion under the project's ground-truth-aligned
  sparse-field policy. That explicit deletion instruction takes precedence.
- Generic catch-all fields such as coverage_status, conditions, exclusions,
  annual_limits, and source_references may complement the schema, but they must
  not replace specific fields needed for product comparison.
- Preserve clinical category analysis as a specific hospital/combined concept;
  do not collapse it into a generic coverage_status field alone.
- Preserve direct product-feature comparability for ambulance, accident cover,
  gap cover, excess options, continuity of cover, cover options, and age-based
  discounts where source evidence supports them.
- For percentage fields, state the unit explicitly. If numeric, use percentage
  points, so 60 means 60%, not 0.6.
""".strip()


def protected_fields_in_schema(schema_fields: object) -> list[str]:
    if not isinstance(schema_fields, list):
        return []
    existing = _existing_field_names(schema_fields)
    return [
        name
        for name in BUSINESS_CRITICAL_FIELDS
        if _canonical_or_alias_present(name, existing)
    ]


def replacement_risk_summary(schema_fields: object) -> list[str]:
    if not isinstance(schema_fields, list):
        return []
    existing = _existing_field_names(schema_fields)
    summaries: list[str] = []
    for generic, specific_fields in GENERIC_REPLACEMENT_RISKS.items():
        if generic not in existing:
            continue
        present_specific = [
            field for field in specific_fields
            if _canonical_or_alias_present(field, existing)
        ]
        missing_specific = [
            field for field in specific_fields
            if not _canonical_or_alias_present(field, existing)
        ]
        if missing_specific:
            summaries.append(
                f"{generic} is generic and must not replace "
                + ", ".join(missing_specific)
                + "."
            )
        elif present_specific:
            summaries.append(
                f"{generic} may complement, but not replace, "
                + ", ".join(present_specific)
                + "."
            )
    return summaries


def _existing_field_names(schema_fields: list[object]) -> set[str]:
    return {
        str(field.get("name"))
        for field in schema_fields
        if isinstance(field, dict) and isinstance(field.get("name"), str)
    }


def _canonical_or_alias_present(canonical_name: str, existing: set[str]) -> bool:
    candidates = {canonical_name, *BUSINESS_FIELD_ALIASES.get(canonical_name, ())}
    return bool(candidates & existing)
