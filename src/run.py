from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import uuid4

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.common.json_artifacts import (
    build_failure_artifact,
    build_success_artifact,
    write_artifact,
    write_failure_artifact,
)
from src.common.data_paths import default_private_health_pdf_root
from src.common.model_config import resolve_selection
from src.config import load_config
from src.models import ExtractionResult
from src.schema.loader import SchemaLoader
from src.schema.sampler import DEFAULT_CATEGORIES, print_samples, select_samples
from src.schema.validator import SchemaValidator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Konkrd private-health extraction pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser(
        "discover",
        help="Generate a feat discovered JSON schema from sample PDFs",
    )
    discover.add_argument("--samples", nargs="+")
    discover.add_argument("--input-root", default=str(default_private_health_pdf_root()))
    discover.add_argument("--categories", nargs="+", default=list(DEFAULT_CATEGORIES))
    discover.add_argument("--per-category", type=int, default=5)
    discover.add_argument("--seed", type=int)
    discover.add_argument("--provider")
    discover.add_argument("--model")
    discover.add_argument("--document-input")
    discover.add_argument("--timeout", type=float, default=600.0)
    discover.add_argument("--keep-uploaded-files", action="store_true")
    discover.add_argument("--output", default="outputs/private_health/schema.json")
    discover.add_argument("--usage-log", default="outputs/private_health/token_usage.jsonl")

    extract = subparsers.add_parser("extract", help="Extract one PDF into structured JSON")
    extract.add_argument("--pdf", required=True)
    extract.add_argument("--schema", required=True)
    extract.add_argument("--output")
    extract.add_argument("--provider", default=None)
    extract.add_argument("--model", default=None)
    extract.add_argument(
        "--no-fallback",
        action="store_true",
        help="Deprecated compatibility flag; schema_application extraction has no heuristic fallback.",
    )

    batch = subparsers.add_parser("batch", help="Run extraction over collected PDFs")
    batch.add_argument("--vertical", default="private_health")
    batch.add_argument("--schema", required=True)
    batch.add_argument("--input-root", default=str(default_private_health_pdf_root()))
    batch.add_argument("--evaluate", action="store_true")
    batch.add_argument("--provider", default=None)
    batch.add_argument("--model", default=None)
    batch.add_argument(
        "--no-fallback",
        action="store_true",
        help="Deprecated compatibility flag; schema_application extraction has no heuristic fallback.",
    )

    crawl = subparsers.add_parser(
        "crawl",
        help="Discover and safely download public insurance document PDFs",
    )
    crawl.add_argument("--vertical", default="travel_insurance")
    crawl.add_argument(
        "--config",
        default="configs/travel_insurance/sources.json",
    )
    crawl.add_argument(
        "--data-root",
        default="data/travel_insurance/raw/PDFs",
    )
    crawl.add_argument(
        "--output-root",
        default="outputs/travel_insurance/acquisition",
    )
    crawl.add_argument(
        "--insurer",
        action="append",
        dest="insurers",
        default=[],
        help="Restrict collection to one configured insurer; repeat as needed",
    )
    crawl.add_argument("--include-archived", action="store_true")
    crawl.add_argument(
        "--discovery-only",
        action="store_true",
        help="Discover links and write metadata without downloading PDFs",
    )

    return parser


def command_discover(args: argparse.Namespace) -> int:
    from src.common.structured_output import StructuredOutputFailure
    from src.schema.discovery import SchemaDiscovery

    try:
        selection = resolve_selection(
            provider=args.provider,
            model=args.model,
            document_input=args.document_input,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    categories = tuple(category.lower() for category in args.categories)
    input_root = Path(args.input_root)
    output_path = next_available_path(Path(args.output))
    run_id = uuid4().hex
    sample_paths = list(args.samples or [])

    try:
        if not sample_paths:
            sample_paths = select_samples(
                input_root=input_root,
                categories=categories,
                per_category=args.per_category,
                seed=args.seed,
            )
            print_samples(sample_paths, input_root, categories)

        schema_data = SchemaDiscovery(
            selection=selection,
            cleanup_uploaded_files=not args.keep_uploaded_files,
            timeout_seconds=args.timeout,
            usage_log_path=args.usage_log,
            pdf_root=input_root,
        ).discover(sample_paths, output_path=output_path, run_id=run_id)
    except Exception as exc:
        details = (
            list(exc.result.errors)
            if isinstance(exc, StructuredOutputFailure)
            else []
        )
        failure = build_failure_artifact(
            artifact_type="schema_discovery_error",
            contract_version="1.0.0",
            provenance={
                "run_id": run_id,
                "provider": selection.provider,
                "model": selection.model,
                "document_input": selection.document_input,
                "source_documents": list(sample_paths),
                "source_artifacts": [],
            },
            error_code=(
                "structured_output_exhausted"
                if isinstance(exc, StructuredOutputFailure)
                else "schema_discovery_failed"
            ),
            message=str(exc),
            details=details,
        )
        try:
            error_path = write_failure_artifact(
                output_path.parent,
                "schema_discovery",
                run_id,
                failure,
            )
            print(f"Failure artifact: {error_path}")
        except Exception as artifact_exc:
            print(f"Could not write failure artifact: {artifact_exc}")
        print(f"Schema discovery failed: {exc}")
        return 1

    artifact = build_success_artifact(
        artifact_type="discovered_schema",
        contract_version="1.0.0",
        data=schema_data,
        provenance={
            "run_id": run_id,
            "provider": selection.provider,
            "model": selection.model,
            "document_input": selection.document_input,
            "source_documents": list(sample_paths),
            "source_artifacts": [],
        },
        data_contract="private_health/discovered_schema",
    )
    write_artifact(
        output_path,
        artifact,
        data_contract="private_health/discovered_schema",
    )
    print(f"Wrote schema draft to {output_path}")
    return 0


def command_extract(args: argparse.Namespace) -> int:
    from src.pipeline.extractor import Extractor

    schema_data = load_schema_data(args.schema)
    schema = SchemaLoader().load(args.schema)
    issues = SchemaValidator().validate(schema)
    if issues:
        print("Schema validation issues:")
        for issue in issues:
            print(f"- {issue}")
        return 1

    selection = resolve_selection(provider=args.provider, model=args.model)
    extractor = Extractor(
        schema_data=schema_data,
        selection=selection,
    )

    record = extractor.extract_one(args.pdf)
    result = ExtractionResult(
        vertical=schema.vertical,
        schema_version=schema.version,
        source_path=str(args.pdf),
        provider=selection.provider,
        model=selection.model,
        data=record,
    )

    output_path = args.output or default_output_path(schema.vertical, Path(args.pdf))
    result.write_json(output_path)
    print(f"Wrote extraction to {output_path}")
    return 0


def command_batch(args: argparse.Namespace) -> int:
    from src.evaluation.metrics import ExtractionEvaluator, PrivateHealthGroundTruthStore
    from src.evaluation.reporter import EvaluationReporter
    from src.pipeline.extractor import Extractor

    config = load_config()
    schema_data = load_schema_data(args.schema)
    schema = SchemaLoader().load(args.schema)
    issues = SchemaValidator().validate(schema)
    if issues:
        print("Schema validation issues:")
        for issue in issues:
            print(f"- {issue}")
        return 1

    input_root = (
        Path(args.input_root)
        if args.input_root
        else config.data_dir / args.vertical / "raw" / "PDFs"
    )
    pdf_paths = sorted(input_root.rglob("*.pdf"))
    if not pdf_paths:
        print(f"No PDFs found under {input_root}")
        return 1

    selection = resolve_selection(provider=args.provider, model=args.model)
    extractor = Extractor(
        schema_data=schema_data,
        selection=selection,
    )

    reports = []
    provider_counts: dict[str, int] = {}
    warning_counts: dict[str, int] = {}
    warning_samples: list[str] = []
    unmatched_documents = 0
    low_confidence_matches = 0
    ambiguous_matches = 0
    fallback_documents = 0
    extraction_errors = 0
    gt_match_diagnostics: list[dict[str, object]] = []
    gt_store = None
    evaluator = None
    reporter = None
    if args.evaluate and args.vertical in {"private_health", "private_health_au"}:
        gt_store = PrivateHealthGroundTruthStore(
            config.data_dir / "private_health" / "labelled"
        )
        evaluator = ExtractionEvaluator()
        reporter = EvaluationReporter()

    for pdf_path in pdf_paths:
        try:
            record = extractor.extract_one(pdf_path)
            result = ExtractionResult(
                vertical=schema.vertical,
                schema_version=schema.version,
                source_path=str(pdf_path),
                provider=selection.provider,
                model=selection.model,
                data=record,
            )
        except Exception as exc:
            extraction_errors += 1
            print(f"Extraction failed for {pdf_path.name}: {exc}")
            continue
        output_path = default_output_path(schema.vertical, pdf_path)
        result.write_json(output_path)
        provider_counts[result.provider] = provider_counts.get(result.provider, 0) + 1
        if args.evaluate and result.provider == "heuristic":
            fallback_documents += 1
        for warning in result.warnings:
            warning_counts[warning] = warning_counts.get(warning, 0) + 1
            if len(warning_samples) < 5 and warning not in warning_samples:
                warning_samples.append(warning)
        print(f"Extracted {pdf_path.name} -> {output_path}")

        if gt_store and evaluator:
            product_match, ground_truth = gt_store.load_ground_truth(pdf_path)
            if ground_truth:
                report = evaluator.evaluate(
                    extracted=result,
                    ground_truth=ground_truth,
                    product_key=product_match.id_master if product_match else None,
                )
                if product_match:
                    report.match_score = product_match.match_score
                    report.low_confidence_match = product_match.low_confidence_match
                    if product_match.low_confidence_match:
                        low_confidence_matches += 1
                reports.append(report)
            else:
                unmatched_documents += 1
                gt_match_diagnostics.append(
                    {
                        "source_path": str(pdf_path),
                        "issue": "unmatched",
                        "candidates": gt_store.rank_pdf_candidates(pdf_path) if gt_store else [],
                    }
                )
            if product_match and product_match.ambiguous_match:
                ambiguous_matches += 1
                gt_match_diagnostics.append(
                    {
                        "source_path": str(pdf_path),
                        "issue": "ambiguous",
                        "selected_id_master": product_match.id_master,
                        "selected_score": product_match.match_score,
                        "candidates": product_match.candidate_matches,
                    }
                )

    if evaluator and reporter:
        summary = evaluator.aggregate(
            reports,
            total_documents=len(pdf_paths),
            unmatched_documents=unmatched_documents,
            low_confidence_matches=low_confidence_matches,
            fallback_documents=fallback_documents,
            extraction_errors=extraction_errors,
        )
        model_reports = [
            report for report in reports
            if report.extraction_provider and report.extraction_provider != "heuristic"
        ]
        model_summary = evaluator.aggregate(
            model_reports,
            total_documents=len(pdf_paths),
            unmatched_documents=max(len(pdf_paths) - extraction_errors - len(model_reports), 0),
            low_confidence_matches=low_confidence_matches,
            fallback_documents=fallback_documents,
            extraction_errors=extraction_errors,
        )
        summary["ambiguous_matches"] = float(ambiguous_matches)
        model_summary["ambiguous_matches"] = float(ambiguous_matches)
        report_root = config.outputs_dir / args.vertical / "evaluation"
        reporter.write_json(reports, summary, report_root / "report.json")
        reporter.write_markdown(reports, summary, report_root / "report.md")
        reporter.write_json(model_reports, model_summary, report_root / "report_model_only.json")
        reporter.write_markdown(model_reports, model_summary, report_root / "report_model_only.md")
        if gt_match_diagnostics:
            import json

            diagnostics_path = report_root / "ground_truth_match_diagnostics.json"
            diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
            diagnostics_path.write_text(
                json.dumps(gt_match_diagnostics, indent=2),
                encoding="utf-8",
            )
        print(f"Wrote evaluation reports to {report_root}")
        if fallback_documents:
            print(
                "Evaluation warning: heuristic fallback results were written to report.json; "
                "use report_model_only.json for pure model-quality metrics or --no-fallback "
                "to fail instead of falling back."
            )
        if gt_match_diagnostics:
            print(
                "Evaluation warning: wrote ground-truth match diagnostics for unmatched "
                "or ambiguous PDFs."
            )
    elif args.evaluate:
        print("Evaluation skipped: no private-health ground truth matched the PDFs.")

    print("\nBatch extraction summary:")
    print(
        "  Providers: "
        + (
            ", ".join(
                f"{provider}={count}"
                for provider, count in sorted(provider_counts.items())
            )
            if provider_counts
            else "none"
        )
    )
    print(f"  Warnings: {sum(warning_counts.values())}")
    for warning in warning_samples:
        print(f"  - {warning} ({warning_counts[warning]})")

    return 0


def command_crawl(args: argparse.Namespace) -> int:
    if args.vertical != "travel_insurance":
        print("The crawl command currently supports only --vertical travel_insurance.")
        return 2
    from src.scraper.travel import run_travel_acquisition

    try:
        outcome = run_travel_acquisition(
            config_path=args.config,
            data_root=args.data_root,
            output_root=args.output_root,
            insurer_codes=args.insurers,
            include_archived=args.include_archived,
            discovery_only=args.discovery_only,
        )
    except Exception as exc:
        print(f"Travel-insurance acquisition failed: {exc}")
        return 1

    summary = outcome.data["summary"]
    print(f"Acquisition artifact: {outcome.artifact_path.resolve()}")
    print(
        "Summary: "
        f"providers={summary['providers_succeeded']}/{summary['providers_attempted']}, "
        f"discovered={summary['documents_discovered']}, "
        f"downloaded={summary['documents_downloaded']}, "
        f"valid_pdfs={summary['valid_pdfs']}, "
        f"errors={len(outcome.data['errors'])}"
    )
    for path in outcome.pdf_paths:
        print(f"PDF: {path}")
    return 0


def default_output_path(vertical: str, pdf_path: Path) -> Path:
    config = load_config()
    relative_parts = pdf_path.with_suffix(".json").parts[-4:]
    return config.outputs_dir / vertical / "extractions" / Path(*relative_parts)


def load_schema_data(schema_path: str | Path) -> dict[str, object]:
    path = Path(schema_path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if (
        isinstance(payload, dict)
        and payload.get("artifact_type") == "discovered_schema"
        and payload.get("status") == "success"
        and isinstance(payload.get("data"), dict)
    ):
        return payload["data"]
    if isinstance(payload, dict):
        return payload
    raise ValueError(f"Schema file must contain a JSON/YAML object: {path}")


def next_available_path(path: Path) -> Path:
    if not path.exists():
        return path

    for index in range(1, 10_000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Could not find an available output path for {path}")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "discover":
        return command_discover(args)
    if args.command == "extract":
        return command_extract(args)
    if args.command == "batch":
        return command_batch(args)
    if args.command == "crawl":
        return command_crawl(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
