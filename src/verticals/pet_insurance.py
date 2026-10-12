"""Pet comparison semantics; the shared engine owns generation and extraction."""

from __future__ import annotations

from src.common.structured_output import BusinessDiagnostics

COMPARISON_FIELDS = {
    "co_payment_percentage": "number", "covered_benefit_categories": "list[enum]",
    "annual_benefit_limit_options_aud": "list[number]", "benefit_percentage_options": "list[number]",
    "excess_options_aud": "list[number]", "temporary_condition_reinstatement_after_months": "number",
    "benefit_coverages": "list[object]",
}


def validate_schema(schema: dict, manifest) -> None:
    fields = {field["name"]: field for field in schema["fields"]}
    for name, kind in COMPARISON_FIELDS.items():
        if name not in fields or fields[name]["type"] != kind:
            raise ValueError(f"Pet comparison field {name} must have type {kind}.")
    props = fields["benefit_coverages"].get("item_schema", {}).get("properties", {})
    for name in ("benefit_category", "coverage_status", "source_document_id", "source_block_id", "source_page", "source_quote"):
        if name not in props:
            raise ValueError(f"Pet benefit_coverages must declare {name} evidence/meaning.")
        expected = "integer" if name == "source_page" else "string"
        if props[name].get("type") != expected:
            raise ValueError(f"Pet benefit_coverages.{name} must have type {expected}.")
    categories = {entry["canonical_name"] for entry in schema["taxonomies"]["benefit_categories"]}
    if set(fields["covered_benefit_categories"]["values"]) != categories or set(props["benefit_category"].get("enum", [])) != categories:
        raise ValueError("Pet category enums must agree with benefit_categories taxonomy.")
    if set(props["coverage_status"].get("enum", [])) != {"included", "optional", "excluded"}:
        raise ValueError("Pet coverage status must distinguish included, optional and excluded.")


def validate_records(schema: dict, payload: dict, *, document_count: int = 1) -> None:
    for index, product in enumerate(payload["products"]):
        coverages, included = product["benefit_coverages"], product["covered_benefit_categories"]
        if (coverages is None) != (included is None):
            raise ValueError(f"$.products[{index}]: category list and benefit coverages must be unknown together.")
        if coverages is not None:
            expected = [item["benefit_category"] for item in coverages if item["coverage_status"] == "included"]
            if len(set(included)) != len(included) or set(included) != set(expected):
                raise ValueError(f"$.products[{index}].covered_benefit_categories must match included coverages exactly.")
        for name in ("co_payment_percentage", "benefit_percentage_options"):
            values = product[name] if isinstance(product[name], list) else [product[name]]
            if any(value is not None and not 0 <= value <= 100 for value in values):
                raise ValueError(f"$.products[{index}].{name}: percentages must be between 0 and 100.")
        for name in ("annual_benefit_limit_options_aud", "excess_options_aud", "temporary_condition_reinstatement_after_months"):
            values = product[name] if isinstance(product[name], list) else [product[name]]
            if any(value is not None and value < 0 for value in values):
                raise ValueError(f"$.products[{index}].{name}: amounts and durations cannot be negative.")


def source_context(schema: dict, source_index: dict) -> tuple[str, object]:
    return "", None


def validate_sources(schema: dict, payload: dict, source_index: dict, context) -> None:
    errors = []
    for index, product in enumerate(payload["products"]):
        for item_index, item in enumerate(product["benefit_coverages"] or []):
            path = f"$.products[{index}].benefit_coverages[{item_index}]"
            document = source_index.get(item["source_document_id"])
            block = document and document["blocks"].get(item["source_block_id"])
            quote = " ".join(item["source_quote"].casefold().split())
            if (not block or block["page"] != item["source_page"] or not quote
                    or quote not in " ".join(block["text"].casefold().split())):
                errors.append({"path": path, "message": "Pet citation must match its document ID, block ID, PDF page and verbatim source quote."})
    if errors:
        raise BusinessDiagnostics(errors)
