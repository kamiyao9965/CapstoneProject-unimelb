from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.common.json_artifacts import (
    build_success_artifact,
    read_artifact,
    write_artifact,
)
from src.common.json_contracts import validate_contract
from src.common.json_codec import loads_json
from src.common.json_contracts import validate_inline_contract
from src.schema.business_fidelity import (
    BUSINESS_FIDELITY_RULES,
    protected_fields_in_schema,
    replacement_risk_summary,
)
from src.schema.contract import compile_extraction_contract, schema_hash
from src.schema.migration import migrate_legacy_discovered_schema
from src.schema.validation import (
    JSONScalar,
    SUPPORTED_PRODUCT_TYPES,
    validate_pet_schema_mapping,
    validate_schema_mapping,
)
from src.refine.verticals import contract_name

# Fields filled in fewer than this fraction of documents are flagged as weak:
# either the schema is asking for something the PDFs rarely contain, or the
# field name/description is too ambiguous to extract reliably.
WEAK_FILL_THRESHOLD = 0.25

# Fields directly scored from the labelled private-health CSVs. Sparse holdout
# samples must not cause refinement to delete the target labels themselves.
GROUND_TRUTH_ALIGNED_FIELDS = frozenset({
    "product_type",
    "product_name",
    "fund_name",
    "insurer_name",
    "tier",
    "product_tier",
    "clinical_categories",
    "hospital_clinical_categories",
    "extras_benefits",
    "extras_waiting_periods",
    "extras_shared_limits",
})

PET_COMPARISON_FIELDS = frozenset({
    "annual_benefit_limit_aud",
    "annual_benefit_limit_options_aud",
    "benefit_percentage",
    "benefit_percentage_options",
    "co_payment_percentage",
    "excess_aud",
    "excess_options_aud",
    "waiting_periods",
    "covered_benefit_categories",
    "benefit_coverages",
    "key_general_exclusions",
})
PET_COMPARISON_GROUPS: dict[str, tuple[str, ...]] = {
    "annual_benefit_limit": (
        "annual_benefit_limit_aud", "annual_benefit_limit_options_aud",
    ),
    "reimbursement": (
        "benefit_percentage", "benefit_percentage_options", "co_payment_percentage",
    ),
    "excess": ("excess_aud", "excess_options_aud"),
}
PET_BUSINESS_FIDELITY_RULES = """
Pet-insurance fidelity guardrail:
- Preserve product-comparison fields for annual limits, reimbursement or
  co-payment percentages, excesses, waiting periods, covered benefits and
  exclusions when evidence supports them.
- Do not replace those structured fields with a generic coverage_summary,
  conditions, exclusions, annual_limits or source_references field alone.
- A sparse optional field may be removed only when the feedback explicitly
  identifies it as weak for the sampled product records.
""".strip()


@dataclass
class FieldSpec:
    name: str
    type: str = "string"
    required: bool = False
    values: list[JSONScalar] = field(default_factory=list)
    applies_to: tuple[str, ...] = tuple(sorted(SUPPORTED_PRODUCT_TYPES))


def load_field_specs(schema_data: dict[str, object]) -> list[FieldSpec]:
    vertical = _schema_vertical(schema_data)
    validate_contract(schema_data, contract_name(vertical, "discovered_schema"))
    if vertical == "pet_insurance":
        data = validate_pet_schema_mapping(schema_data)
    else:
        data = validate_schema_mapping(migrate_legacy_discovered_schema(schema_data))
    cover_scopes = {str(value) for value in data.get("cover_scopes") or []}
    specs: list[FieldSpec] = []
    for item in data.get("fields") or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        applies_to = tuple(str(value) for value in (item.get("applies_to") or []))
        # Pet analysis is product-level. Document-envelope metadata is not a
        # product comparison field and should not affect product fill rates.
        if vertical == "pet_insurance" and not (
            "product" in applies_to or set(applies_to).intersection(cover_scopes)
        ):
            continue
        specs.append(
            FieldSpec(
                name=str(item["name"]),
                type=str(item.get("type", "string")),
                required=bool(item.get("required", False)),
                values=list(item.get("values") or []),
                applies_to=applies_to,
            )
        )
    return specs


def load_records(
    extraction_dir: Path,
    extraction_contract: dict[str, object],
    *,
    vertical: str = "private_health",
    current_schema_hash: str | None = None,
) -> tuple[list[dict], int]:
    records: list[dict] = []
    failures = 0
    successful_identities: set[tuple[str, str]] = set()
    for path in sorted(extraction_dir.glob("*.json")):
        try:
            artifact = loads_json(path.read_text(encoding="utf-8"))
            if vertical == "pet_insurance" and _is_product_result(artifact):
                result_hash = artifact.get("schema_sha256")
                if (
                    current_schema_hash is not None
                    and isinstance(result_hash, str)
                    and result_hash != current_schema_hash
                ):
                    continue
                data = artifact.get("data")
                product_contract = (
                    extraction_contract.get("properties", {})
                    .get("products", {})
                    .get("items")
                )
                if not isinstance(data, dict) or not isinstance(product_contract, dict):
                    raise ValueError("Invalid product-oriented extraction result.")
                validate_inline_contract(data, product_contract, "pet_product_result")
                records.append(dict(data))
                continue
            validate_contract(artifact, "artifact_envelope")
            if (
                artifact["status"] != "success"
                or artifact["artifact_type"] != "extraction_result"
                or not isinstance(artifact["data"], dict)
            ):
                raise ValueError("Not a successful extraction result artifact.")
            identity = _artifact_identity(artifact)
            if (
                current_schema_hash is not None
                and identity is not None
                and identity[0] != current_schema_hash
            ):
                continue
            validate_inline_contract(
                artifact["data"],
                extraction_contract,
                "runtime_extraction_result",
            )
            data = dict(artifact["data"])
            if identity is not None:
                successful_identities.add(identity)
        except ValueError:
            failures += 1
            continue
        if vertical == "pet_insurance":
            products = data.get("products")
            if not isinstance(products, list):
                failures += 1
                continue
            for product in products:
                if not isinstance(product, dict):
                    failures += 1
                    continue
                record = dict(product)
                document = data.get("document")
                if isinstance(document, dict):
                    record["_document_role"] = document.get("document_role")
                records.append(record)
        else:
            record = data
            record["_product_type_evidence"] = _product_type_evidence(artifact)
            records.append(record)
    for path in (extraction_dir / "errors" / "extraction").glob("*.json"):
        if not path.is_file():
            continue
        try:
            failure_artifact = loads_json(path.read_text(encoding="utf-8"))
            validate_contract(failure_artifact, "artifact_envelope")
            identity = _artifact_identity(failure_artifact)
        except ValueError:
            identity = None
        if (
            current_schema_hash is not None
            and identity is not None
            and identity[0] != current_schema_hash
        ):
            continue
        if identity is None or identity not in successful_identities:
            failures += 1
    return records, failures


def _is_product_result(payload: object) -> bool:
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("product_id"), str)
        and isinstance(payload.get("document_family_id"), str)
        and isinstance(payload.get("data"), dict)
    )


def _artifact_identity(artifact: object) -> tuple[str, str] | None:
    """Return the schema/PDF identity shared by success and failure artifacts."""
    if not isinstance(artifact, dict):
        return None
    provenance = artifact.get("provenance") or {}
    if not isinstance(provenance, dict):
        return None
    source_artifacts = provenance.get("source_artifacts") or []
    schema_hash = _source_artifact_value(source_artifacts, "schema_sha256")
    pdf_hash = _source_artifact_value(source_artifacts, "pdf_sha256")
    if schema_hash and pdf_hash:
        return schema_hash, pdf_hash
    return None


def is_filled(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, list, dict)) and len(value) == 0:
        return False
    return True


@dataclass
class Analysis:
    vertical: str
    documents: int
    error_docs: int
    unclassified_docs: int
    fill_rate: dict[str, float]
    evaluated_documents: dict[str, int]
    applies_to_mismatches: dict[str, dict[str, int]]
    weak_fields: list[str]
    missing_required: dict[str, int]      # field -> docs missing it
    enum_violations: dict[str, list[JSONScalar]]  # field -> bad values seen
    model_unfilled: dict[str, int]         # field -> times model self-reported unfilled
    comparison_fill_rate: dict[str, float]
    comparison_missing: dict[str, int]
    business_fidelity: dict[str, object]


def analyze(
    records: list[dict],
    specs: list[FieldSpec],
    *,
    failed_artifacts: int = 0,
    vertical: str = "private_health",
) -> Analysis:
    spec_by_name = {s.name: s for s in specs}
    error_docs = failed_artifacts
    if vertical == "pet_insurance":
        docs = list(records)
        unclassified_docs = 0
    else:
        docs = [record for record in records if _product_type(record) is not None]
        unclassified_docs = len(records) - len(docs)

    filled_counts = {s.name: 0 for s in specs}
    evaluated_documents = {s.name: 0 for s in specs}
    applies_to_mismatches: dict[str, dict[str, int]] = {}
    missing_required: dict[str, int] = {}
    enum_violations: dict[str, list[JSONScalar]] = {}
    model_unfilled: dict[str, int] = {}
    schema_field_payloads = [
        {
            "name": spec.name,
            "type": spec.type,
            "applies_to": list(spec.applies_to),
        }
        for spec in specs
    ]

    for record in docs:
        product_type = _product_type(record) if vertical != "pet_insurance" else _pet_scope(record)
        for name, spec in spec_by_name.items():
            applicable = _field_applies_to_record(spec, record, vertical)
            if not applicable:
                if is_filled(record.get(name)):
                    counts = applies_to_mismatches.setdefault(name, {})
                    label = product_type or "unknown_scope"
                    counts[label] = counts.get(label, 0) + 1
                continue
            evaluated_documents[name] += 1
            value = record.get(name)
            if is_filled(value):
                filled_counts[name] += 1
                # enum check on scalar values
                if (
                    spec.type == "enum"
                    and spec.values
                    and isinstance(value, (str, int, float, bool))
                ):
                    if value not in spec.values:
                        enum_violations.setdefault(name, []).append(value)
            elif spec.required:
                missing_required[name] = missing_required.get(name, 0) + 1

        for name in record.get("_unfilled") or []:
            if name in spec_by_name:
                model_unfilled[name] = model_unfilled.get(name, 0) + 1

    fill_rate = {
        name: (
            filled_counts[name] / evaluated_documents[name]
            if evaluated_documents[name]
            else 0.0
        )
        for name in filled_counts
    }
    comparison_fill_rate: dict[str, float] = {}
    comparison_missing: dict[str, int] = {}
    if vertical == "pet_insurance" and docs:
        available_names = set(spec_by_name)
        for group_name, candidates in PET_COMPARISON_GROUPS.items():
            fields = [name for name in candidates if name in available_names]
            if not fields:
                continue
            filled = sum(
                any(is_filled(record.get(name)) for name in fields)
                for record in docs
            )
            comparison_fill_rate[group_name] = filled / len(docs)
            comparison_missing[group_name] = len(docs) - filled
    weak_fields = (
        sorted(
            name
            for name, rate in fill_rate.items()
            if evaluated_documents[name]
            and rate < WEAK_FILL_THRESHOLD
            and not spec_by_name[name].required
            and name not in _protected_sparse_fields(vertical)
        )
        if docs
        else []
    )

    return Analysis(
        documents=len(docs),
        error_docs=error_docs,
        unclassified_docs=unclassified_docs,
        fill_rate=fill_rate,
        evaluated_documents=evaluated_documents,
        applies_to_mismatches={
            name: dict(sorted(counts.items()))
            for name, counts in sorted(applies_to_mismatches.items())
        },
        weak_fields=weak_fields,
        missing_required=missing_required,
        enum_violations={
            key: sorted(
                set(values),
                key=lambda item: (type(item).__name__, repr(item)),
            )
            for key, values in enum_violations.items()
        },
        model_unfilled=model_unfilled,
        comparison_fill_rate=comparison_fill_rate,
        comparison_missing=comparison_missing,
        business_fidelity=_business_fidelity(schema_field_payloads, vertical),
        vertical=vertical,
    )


def _schema_vertical(schema_data: dict[str, object]) -> str:
    vertical = schema_data.get("vertical")
    if vertical not in {"private_health", "pet_insurance"}:
        raise ValueError("Schema vertical must be private_health or pet_insurance.")
    return str(vertical)


def _pet_scope(record: dict) -> str | None:
    value = record.get("cover_scope")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _field_applies_to_record(
    spec: FieldSpec, record: dict, vertical: str
) -> bool:
    if vertical != "pet_insurance":
        return _product_type(record) in spec.applies_to
    if "product" in spec.applies_to:
        return True
    scope = _pet_scope(record)
    return scope is not None and scope in spec.applies_to


def _protected_sparse_fields(vertical: str) -> frozenset[str]:
    return PET_COMPARISON_FIELDS if vertical == "pet_insurance" else GROUND_TRUTH_ALIGNED_FIELDS


def _business_fidelity(
    schema_fields: list[dict[str, object]], vertical: str
) -> dict[str, object]:
    if vertical == "pet_insurance":
        existing = {str(field.get("name")) for field in schema_fields}
        protected = sorted(PET_COMPARISON_FIELDS.intersection(existing))
        generic_names = {"coverage_summary", "conditions", "annual_limits", "source_references"}
        risks = [
            f"{name} is generic and must not replace pet comparison fields."
            for name in sorted(generic_names.intersection(existing))
        ]
        return {
            "protected_fields": protected,
            "replacement_risks": risks,
            "rule_summary": PET_BUSINESS_FIDELITY_RULES,
        }
    return {
        "protected_fields": protected_fields_in_schema(schema_fields),
        "replacement_risks": replacement_risk_summary(schema_fields),
        "rule_summary": BUSINESS_FIDELITY_RULES,
    }


def _product_type(record: dict) -> str | None:
    evidence = record.get("_product_type_evidence")
    if isinstance(evidence, dict):
        override_value = evidence.get("override")
        if isinstance(override_value, str):
            override = override_value.strip().lower()
            if override in SUPPORTED_PRODUCT_TYPES:
                return override
    value = record.get("product_type")
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in SUPPORTED_PRODUCT_TYPES else None


def _product_type_evidence(artifact: dict) -> dict[str, str | bool | None]:
    provenance = artifact.get("provenance") or {}
    source_artifacts = provenance.get("source_artifacts") or []
    evidence = {
        "directory": _source_artifact_value(source_artifacts, "directory_product_type"),
        "override": _source_artifact_value(source_artifacts, "override_product_type"),
        "effective": _source_artifact_value(source_artifacts, "effective_product_type"),
        "model": _source_artifact_value(source_artifacts, "model_product_type"),
        "conflict": "product_type_conflict:directory_override" in source_artifacts,
    }
    if evidence["model"] is None:
        data = artifact.get("data") or {}
        model_value = data.get("product_type") if isinstance(data, dict) else None
        if isinstance(model_value, str) and model_value.strip():
            evidence["model"] = model_value.strip().lower()
    return evidence


def _source_artifact_value(source_artifacts: object, prefix: str) -> str | None:
    if not isinstance(source_artifacts, list):
        return None
    marker = f"{prefix}:"
    for item in source_artifacts:
        if isinstance(item, str) and item.startswith(marker):
            return item[len(marker):]
    return None


def print_report(analysis: Analysis) -> None:
    unit = "Products" if analysis.vertical == "pet_insurance" else "Documents"
    print(
        f"{unit} analyzed: {analysis.documents} "
        f"({analysis.error_docs} errored, {analysis.unclassified_docs} unclassified)\n"
    )

    print("Field fill rate (lowest first):")
    for name, rate in sorted(analysis.fill_rate.items(), key=lambda kv: kv[1]):
        marker = " <- weak" if name in analysis.weak_fields else ""
        print(f"  {rate:>6.0%}  {name}{marker}")
    print()

    if analysis.comparison_fill_rate:
        print("Comparison-value availability (scalar or options):")
        for name, rate in analysis.comparison_fill_rate.items():
            print(
                f"  {rate:>6.0%}  {name} "
                f"({analysis.comparison_missing[name]}/{analysis.documents} missing)"
            )
        print()

    if analysis.missing_required:
        print("Required fields missing in some documents:")
        for name, count in sorted(analysis.missing_required.items(), key=lambda kv: -kv[1]):
            print(f"  {name}: missing in {count}")
        print()

    if analysis.applies_to_mismatches:
        print("Fields filled outside schema applies_to:")
        for name, counts in analysis.applies_to_mismatches.items():
            details = ", ".join(
                f"{product_type} ({count})"
                for product_type, count in counts.items()
            )
            print(f"  {name}: {details}")
        print()

    protected = analysis.business_fidelity.get("protected_fields") or []
    risks = analysis.business_fidelity.get("replacement_risks") or []
    if protected or risks:
        print("Business fidelity guardrail:")
        if protected:
            print(f"  Protected fields: {', '.join(map(str, protected))}")
        for risk in risks:
            print(f"  {risk}")
        print()

    if analysis.enum_violations:
        print("Enum violations (value not in schema's allowed list):")
        for name, values in analysis.enum_violations.items():
            print(f"  {name}: {', '.join(map(str, values))}")
        print()


def build_feedback(analysis: Analysis) -> str:
    """Turn the failure signals into instructions the discovery step can act on."""
    return "\n".join(f"- {line}" for line in build_feedback_instructions(analysis))


def build_feedback_instructions(analysis: Analysis) -> list[str]:
    """Build structured refinement instructions without parsing rendered text."""
    if analysis.documents == 0:
        unit = "product record(s)" if analysis.vertical == "pet_insurance" else "documents"
        return [
            f"No {unit} were successfully extracted; "
            f"{analysis.error_docs} extraction attempt(s) failed and "
            f"{analysis.unclassified_docs} record(s) had no valid product_type. "
            "Fix extraction, parsing, or classification errors before refining the schema."
        ]

    lines: list[str] = []
    protected = analysis.business_fidelity.get("protected_fields") or []
    risks = analysis.business_fidelity.get("replacement_risks") or []
    if protected or risks:
        details = []
        if protected:
            details.append("preserve " + ", ".join(map(str, protected)))
        details.extend(str(risk) for risk in risks)
        lines.append(
            "Business fidelity guardrail: do not improve fill rate by deleting "
            "or replacing specific comparison fields with generic catch-all "
            "fields. Critical fields may be split, renamed, narrowed, or have "
            "their applies_to/type/description fixed, but must not be replaced "
            "by coverage_status, conditions, exclusions, annual_limits, or "
            "source_references alone. " + " ".join(details)
        )
    if analysis.applies_to_mismatches:
        details = "; ".join(
            f"{name}: add {', '.join(counts)}"
            for name, counts in analysis.applies_to_mismatches.items()
        )
        lines.append(
            "These fields were extracted with non-empty values outside their declared "
            "applicability targets. "
            "Update the schema applicability instead of "
            f"treating these values as extraction noise: {details}."
        )
    if analysis.weak_fields:
        lines.append(
            "Remove the following optional fields from the next schema because they "
            "were extractable in fewer than "
            f"{int(WEAK_FILL_THRESHOLD * 100)}% of applicable documents: "
            + ", ".join(analysis.weak_fields)
            + ". This is a ground-truth-aligned sparse-field deletion instruction, "
            "not a request to rename, split, narrow, or rewrite these fields. It "
            "overrides the business-fidelity preference to preserve sparse comparison "
            "fields. Do not replace the removed fields with generic catch-all fields."
        )
    if analysis.missing_required:
        names = ", ".join(sorted(analysis.missing_required))
        lines.append(
            f"These fields are marked required but were missing in some documents: {names}. "
            "Reconsider whether they are truly required across all product types."
        )
    if analysis.enum_violations:
        details = "; ".join(
            f"{name}: saw {', '.join(map(str, vals))}"
            for name, vals in analysis.enum_violations.items()
        )
        lines.append(
            "These enum fields saw values outside their allowed list; expand or correct "
            f"the allowed values: {details}."
        )
    if not lines:
        lines.append("No systematic extraction failures detected; schema looks well-fitted.")
    return lines


def build_feedback_data(analysis: Analysis) -> dict[str, object]:
    return {
        "instructions": build_feedback_instructions(analysis),
        "analysis": {
            "documents": analysis.documents,
            "error_docs": analysis.error_docs,
            "unclassified_docs": analysis.unclassified_docs,
            "fill_rate": analysis.fill_rate,
            "evaluated_documents": analysis.evaluated_documents,
            "applies_to_mismatches": analysis.applies_to_mismatches,
            "weak_fields": analysis.weak_fields,
            "missing_required": analysis.missing_required,
            "enum_violations": analysis.enum_violations,
            "model_unfilled": analysis.model_unfilled,
            "comparison_fill_rate": analysis.comparison_fill_rate,
            "comparison_missing": analysis.comparison_missing,
            "business_fidelity": analysis.business_fidelity,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze extraction failures against a schema")
    parser.add_argument("--schema", required=True, help="Schema JSON artifact the extraction used")
    parser.add_argument("--extractions", required=True, help="Directory of extraction *.json files")
    parser.add_argument("--feedback-out", help="Write refinement feedback JSON artifact")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    schema_artifact = read_artifact(args.schema, expected_type="discovered_schema")
    schema_data = schema_artifact["data"]
    if not isinstance(schema_data, dict):
        raise ValueError("Schema artifact data must be an object.")
    vertical = _schema_vertical(schema_data)
    validate_contract(schema_data, contract_name(vertical, "discovered_schema"))
    specs = load_field_specs(schema_data)
    extraction_contract = compile_extraction_contract(schema_data)
    records, failed_artifacts = load_records(
        Path(args.extractions),
        extraction_contract,
        vertical=vertical,
        current_schema_hash=schema_hash(schema_data),
    )
    if not records and not failed_artifacts:
        print(f"No extraction JSON files found in {args.extractions}")
        return 1

    analysis = analyze(
        records, specs, failed_artifacts=failed_artifacts, vertical=vertical
    )
    print_report(analysis)
    feedback = build_feedback(analysis)
    print("Refinement feedback:\n" + feedback)

    if args.feedback_out:
        data = build_feedback_data(analysis)
        artifact = build_success_artifact(
            artifact_type="refinement_feedback",
            contract_version="1.0.0",
            data=data,
            provenance={
                "run_id": None, "provider": None, "model": None,
                "document_input": None, "source_documents": [],
                "source_artifacts": [Path(args.schema).as_posix()],
            },
            data_contract=contract_name(vertical, "refinement_feedback"),
        )
        write_artifact(
            args.feedback_out,
            artifact,
            data_contract=contract_name(vertical, "refinement_feedback"),
        )
        print(f"\nWrote feedback to {args.feedback_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
