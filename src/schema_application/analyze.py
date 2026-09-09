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
from src.schema.contract import compile_extraction_contract
from src.schema.loader import load_schema_data
from src.verticals.manifest import default_manifest_path, load_vertical_manifest
from src.schema.sampler import category_from_path
from src.schema.validation import (
    JSONScalar,
    validate_schema_mapping,
)

# Fields filled in fewer than this fraction of documents are flagged as weak:
# either the schema is asking for something the PDFs rarely contain, or the
# field name/description is too ambiguous to extract reliably.
WEAK_FILL_THRESHOLD = 0.25


@dataclass
class FieldSpec:
    name: str
    type: str = "string"
    required: bool = False
    values: list[JSONScalar] = field(default_factory=list)
    applies_to: tuple[str, ...] = ()
    universal: bool = False


@dataclass(frozen=True)
class ExtractionRecord:
    """Validated model data paired with its authoritative dataset category."""

    data: dict[str, object]
    source_document: str
    source_category: str | None


def load_field_specs(schema_data: dict[str, object], manifest=None) -> list[FieldSpec]:
    data = validate_schema_mapping(schema_data, manifest=manifest)
    specs: list[FieldSpec] = []
    for item in data.get("fields") or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        specs.append(
            FieldSpec(
                name=str(item["name"]),
                universal=set(item["applies_to"]) == set(data["product_types"]),
                type=str(item.get("type", "string")),
                required=bool(item.get("required", False)),
                values=list(item.get("values") or []),
                applies_to=tuple(str(value) for value in (item.get("applies_to") or [])),
            )
        )
    return specs


def load_records(
    extraction_dir: Path,
    extraction_contract: dict[str, object],
    manifest=None,
) -> tuple[list[ExtractionRecord], int]:
    manifest = manifest or load_vertical_manifest(default_manifest_path("private_health"))
    records: list[ExtractionRecord] = []
    failures = 0
    for path in sorted(extraction_dir.glob("*.json")):
        try:
            artifact = loads_json(path.read_text(encoding="utf-8"))
            validate_contract(artifact, "artifact_envelope")
            if (
                artifact["status"] != "success"
                or artifact["artifact_type"] != "extraction_result"
                or not isinstance(artifact["data"], dict)
            ):
                raise ValueError("Not a successful extraction result artifact.")
            validate_inline_contract(
                artifact["data"],
                extraction_contract,
                "runtime_extraction_result",
            )
            source_documents = artifact["provenance"]["source_documents"]
            if len(source_documents) != 1:
                raise ValueError(
                    "Extraction result must identify exactly one source document."
                )
            source_document = source_documents[0]
            source_category = category_from_path(source_document, manifest.documents.categories)
            if source_category is None:
                raise ValueError(
                    "Extraction source document must identify exactly one supported "
                    "dataset category in its path."
                )
        except ValueError:
            failures += 1
            continue
        payload = artifact["data"]
        product_records = payload["products"] if manifest.documents.output_cardinality == "multiple" else [payload]
        for record in product_records:
            records.append(ExtractionRecord(data=record, source_document=source_document,
                source_category=manifest.documents.category_product_types.get(source_category)))
    failures += sum(
        1
        for path in (extraction_dir / "errors" / "extraction").glob("*.json")
        if path.is_file()
    )
    return records, failures


def is_filled(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, list, dict)) and len(value) == 0:
        return False
    return True


@dataclass
class Analysis:
    documents: int
    error_docs: int
    source_category_counts: dict[str, int]
    product_type_correct: int
    product_type_unclassified: int
    product_type_accuracy: float | None
    product_type_mismatches: dict[str, int]
    fill_rate: dict[str, float | None]
    evaluated_documents: dict[str, int]
    weak_fields: list[str]
    missing_required: dict[str, int]      # field -> docs missing it
    enum_violations: dict[str, list[JSONScalar]]  # field -> bad values seen
    model_unfilled: dict[str, int]         # field -> times model self-reported unfilled


def analyze(
    records: list[ExtractionRecord],
    specs: list[FieldSpec],
    *,
    failed_artifacts: int = 0,
) -> Analysis:
    spec_by_name = {s.name: s for s in specs}
    error_docs = failed_artifacts
    source_category_counts: dict[str, int] = {}
    product_type_correct = 0
    product_type_unclassified = 0
    product_type_mismatches: dict[str, int] = {}

    filled_counts = {s.name: 0 for s in specs}
    evaluated_documents = {s.name: 0 for s in specs}
    missing_required: dict[str, int] = {}
    enum_violations: dict[str, list[JSONScalar]] = {}
    model_unfilled: dict[str, int] = {}

    trusted_records = sum(record.source_category is not None for record in records)
    allowed_types = {value for spec in specs for value in spec.applies_to}
    for extraction in records:
        record = extraction.data
        source_category = extraction.source_category
        source_category_counts[source_category or "unknown"] = (
            source_category_counts.get(source_category or "unknown", 0) + 1
        )
        predicted_product_type = _product_type(record, allowed_types)
        if predicted_product_type is None:
            product_type_unclassified += 1
        if source_category is None:
            pass  # Unlabelled records never contribute to classification accuracy.
        elif predicted_product_type == source_category:
            product_type_correct += 1
        else:
            mismatch = (
                f"{source_category} -> "
                f"{predicted_product_type or 'unclassified'}"
            )
            product_type_mismatches[mismatch] = (
                product_type_mismatches.get(mismatch, 0) + 1
            )

        for name, spec in spec_by_name.items():
            if spec.applies_to and source_category not in spec.applies_to and not (source_category is None and spec.universal):
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
            else None
        )
        for name in filled_counts
    }
    weak_fields = (
        sorted(
            name
            for name, rate in fill_rate.items()
            if evaluated_documents[name]
            and rate < WEAK_FILL_THRESHOLD
            and not spec_by_name[name].required
        )
        if records
        else []
    )

    return Analysis(
        documents=len(records),
        error_docs=error_docs,
        source_category_counts=dict(sorted(source_category_counts.items())),
        product_type_correct=product_type_correct,
        product_type_unclassified=product_type_unclassified,
        product_type_accuracy=(
            product_type_correct / trusted_records if trusted_records else None
        ),
        product_type_mismatches=dict(sorted(product_type_mismatches.items())),
        fill_rate=fill_rate,
        evaluated_documents=evaluated_documents,
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
    )


def _product_type(record: dict, allowed_types: set[str]) -> str | None:
    value = record.get("product_type")
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if not allowed_types or normalized in allowed_types else None


def print_report(analysis: Analysis) -> None:
    print(
        f"Extraction records analyzed: {analysis.documents} "
        f"({analysis.error_docs} errored)"
    )
    coverage = ", ".join(
        f"{category}={count}"
        for category, count in analysis.source_category_counts.items()
    ) or "none"
    print(f"Source category coverage: {coverage}")
    accuracy = f"{analysis.product_type_accuracy:.0%}" if analysis.product_type_accuracy is not None else "N/A (no trusted labels)"
    print(
        f"Product type classification: {accuracy} "
        f"({analysis.product_type_correct}/{analysis.documents} correct, "
        f"{analysis.product_type_unclassified} unclassified)"
    )
    if analysis.product_type_mismatches:
        print("Product type mismatches:")
        for transition, count in analysis.product_type_mismatches.items():
            print(f"  {transition}: {count}")
    print()

    print("Field fill rate (lowest first):")
    for name, rate in sorted(analysis.fill_rate.items(), key=lambda kv: -1 if kv[1] is None else kv[1]):
        marker = " <- weak" if name in analysis.weak_fields else ""
        rendered_rate = (
            f"{rate:>6.0%}"
            if analysis.evaluated_documents[name]
            else f"{'N/A':>6}"
        )
        print(f"  {rendered_rate}  {name}{marker}")
    print()

    if analysis.missing_required:
        print("Required fields missing in some documents:")
        for name, count in sorted(analysis.missing_required.items(), key=lambda kv: -kv[1]):
            print(f"  {name}: missing in {count}")
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
        return [
            "No documents were successfully extracted; "
            f"{analysis.error_docs} extraction or artifact validation attempt(s) failed. "
            "Fix extraction, parsing, contract, or source-category errors before "
            "refining the schema."
        ]

    lines: list[str] = []
    if analysis.weak_fields:
        lines.append(
            "The following fields were extractable in fewer than "
            f"{int(WEAK_FILL_THRESHOLD * 100)}% of applicable holdout documents. "
            "Either drop them, split "
            "them into more specific fields, or clarify their description so they map "
            "to what the PDFs actually contain: " + ", ".join(analysis.weak_fields) + "."
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
    if analysis.product_type_mismatches:
        details = "; ".join(
            f"{transition}: {count}"
            for transition, count in analysis.product_type_mismatches.items()
        )
        lines.append(
            "Model product_type matched the authoritative directory category in "
            f"{analysis.product_type_accuracy:.0%} of evaluated documents. Clarify the "
            "product_type field description or allowed-value guidance without "
            f"changing field applicability. Mismatches: {details}."
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
            "source_category_counts": analysis.source_category_counts,
            "product_type_correct": analysis.product_type_correct,
            "product_type_unclassified": analysis.product_type_unclassified,
            "product_type_accuracy": analysis.product_type_accuracy,
            "product_type_mismatches": analysis.product_type_mismatches,
            "fill_rate": analysis.fill_rate,
            "evaluated_documents": analysis.evaluated_documents,
            "weak_fields": analysis.weak_fields,
            "missing_required": analysis.missing_required,
            "enum_violations": analysis.enum_violations,
            "model_unfilled": analysis.model_unfilled,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze extraction failures against a schema")
    parser.add_argument("--manifest")
    parser.add_argument("--schema", required=True, help="Schema JSON artifact the extraction used")
    parser.add_argument("--extractions", required=True, help="Directory of extraction *.json files")
    parser.add_argument("--feedback-out", help="Write refinement feedback JSON artifact")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    manifest = load_vertical_manifest(args.manifest) if args.manifest else None
    schema = load_schema_data(args.schema, manifest)
    manifest = manifest or load_vertical_manifest(default_manifest_path(schema["vertical"]))
    specs = load_field_specs(schema, manifest)
    extraction_contract = compile_extraction_contract(schema,
        data_contract=manifest.contract("discovered_schema"),
        output_cardinality=manifest.documents.output_cardinality, manifest=manifest)
    records, failed_artifacts = load_records(Path(args.extractions), extraction_contract, manifest)
    if not records and not failed_artifacts:
        print(f"No extraction JSON files found in {args.extractions}")
        return 1

    analysis = analyze(records, specs, failed_artifacts=failed_artifacts)
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
            data_contract="schema_refinement/refinement_feedback",
        )
        write_artifact(
            args.feedback_out,
            artifact,
            data_contract="schema_refinement/refinement_feedback",
        )
        print(f"\nWrote feedback to {args.feedback_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
