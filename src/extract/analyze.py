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
from src.extract.contract import compile_extraction_contract
from src.schema.validation import (
    JSONScalar,
    SUPPORTED_PRODUCT_TYPES,
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
    applies_to: tuple[str, ...] = tuple(sorted(SUPPORTED_PRODUCT_TYPES))


def load_field_specs(schema_data: dict[str, object]) -> list[FieldSpec]:
    validate_contract(schema_data, "private_health/discovered_schema")
    data = validate_schema_mapping(schema_data)
    specs: list[FieldSpec] = []
    for item in data.get("fields") or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        specs.append(
            FieldSpec(
                name=str(item["name"]),
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
) -> tuple[list[dict], int]:
    records: list[dict] = []
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
        except ValueError:
            failures += 1
            continue
        records.append(artifact["data"])
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
    unclassified_docs: int
    fill_rate: dict[str, float]
    evaluated_documents: dict[str, int]
    weak_fields: list[str]
    missing_required: dict[str, int]      # field -> docs missing it
    enum_violations: dict[str, list[JSONScalar]]  # field -> bad values seen
    model_unfilled: dict[str, int]         # field -> times model self-reported unfilled


def analyze(
    records: list[dict],
    specs: list[FieldSpec],
    *,
    failed_artifacts: int = 0,
) -> Analysis:
    spec_by_name = {s.name: s for s in specs}
    error_docs = failed_artifacts
    docs = [record for record in records if _product_type(record) is not None]
    unclassified_docs = len(records) - len(docs)

    filled_counts = {s.name: 0 for s in specs}
    evaluated_documents = {s.name: 0 for s in specs}
    missing_required: dict[str, int] = {}
    enum_violations: dict[str, list[JSONScalar]] = {}
    model_unfilled: dict[str, int] = {}

    for record in docs:
        product_type = _product_type(record)
        for name, spec in spec_by_name.items():
            if product_type not in spec.applies_to:
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
    weak_fields = (
        sorted(
            name
            for name, rate in fill_rate.items()
            if evaluated_documents[name]
            and rate < WEAK_FILL_THRESHOLD
            and not spec_by_name[name].required
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


def _product_type(record: dict) -> str | None:
    value = record.get("product_type")
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in SUPPORTED_PRODUCT_TYPES else None


def print_report(analysis: Analysis) -> None:
    print(
        f"Documents analyzed: {analysis.documents} "
        f"({analysis.error_docs} errored, {analysis.unclassified_docs} unclassified)\n"
    )

    print("Field fill rate (lowest first):")
    for name, rate in sorted(analysis.fill_rate.items(), key=lambda kv: kv[1]):
        marker = " <- weak" if name in analysis.weak_fields else ""
        print(f"  {rate:>6.0%}  {name}{marker}")
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
            f"{analysis.error_docs} extraction attempt(s) failed and "
            f"{analysis.unclassified_docs} record(s) had no valid product_type. "
            "Fix extraction, parsing, or classification errors before refining the schema."
        ]

    lines: list[str] = []
    if analysis.weak_fields:
        lines.append(
            "The following fields were extractable in fewer than "
            f"{int(WEAK_FILL_THRESHOLD * 100)}% of documents. Either drop them, split "
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
            "weak_fields": analysis.weak_fields,
            "missing_required": analysis.missing_required,
            "enum_violations": analysis.enum_violations,
            "model_unfilled": analysis.model_unfilled,
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
    schema_artifact = read_artifact(
        args.schema,
        expected_type="discovered_schema",
        data_contract="private_health/discovered_schema",
    )
    specs = load_field_specs(schema_artifact["data"])
    extraction_contract = compile_extraction_contract(schema_artifact["data"])
    records, failed_artifacts = load_records(
        Path(args.extractions),
        extraction_contract,
    )
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
            data_contract="private_health/refinement_feedback",
        )
        write_artifact(
            args.feedback_out,
            artifact,
            data_contract="private_health/refinement_feedback",
        )
        print(f"\nWrote feedback to {args.feedback_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
