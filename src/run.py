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
from src.common.model_config import resolve_selection
from src.config import load_config
from src.models import ExtractionResult
from src.schema.sampler import print_samples, select_samples
from src.verticals.manifest import (
    ManifestValidationError,
    VerticalManifest,
    default_manifest_path,
    load_vertical_manifest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Konkrd private-health extraction pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser(
        "discover",
        help="Generate a feat discovered JSON schema from sample PDFs",
    )
    discover.add_argument("--manifest")
    discover.add_argument("--samples", nargs="+")
    discover.add_argument("--input-root")
    discover.add_argument("--categories", nargs="+")
    discover.add_argument("--per-category", type=int, default=5)
    discover.add_argument("--seed", type=int)
    discover.add_argument("--provider")
    discover.add_argument("--model")
    discover.add_argument("--document-input")
    discover.add_argument("--timeout", type=float, default=600.0)
    discover.add_argument("--keep-uploaded-files", action="store_true")
    discover.add_argument("--output")
    discover.add_argument("--usage-log")

    extract = subparsers.add_parser("extract", help="Extract one PDF into structured JSON")
    extract.add_argument("--manifest")
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
    batch.add_argument("--manifest")
    batch.add_argument("--vertical")
    batch.add_argument("--schema", required=True)
    batch.add_argument("--input-root")
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
    crawl.add_argument("--manifest")
    crawl.add_argument("--vertical")
    crawl.add_argument(
        "--config",
        default=None,
    )
    crawl.add_argument(
        "--data-root",
        default=None,
    )
    crawl.add_argument(
        "--output-root",
        default=None,
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


def configure_command(args: argparse.Namespace) -> VerticalManifest:
    """Load one manifest and apply its defaults before a command runs."""
    default_vertical = "travel_insurance" if args.command == "crawl" else "private_health"
    requested_vertical = getattr(args, "vertical", None) or default_vertical
    manifest_path = args.manifest or default_manifest_path(requested_vertical)
    manifest = load_vertical_manifest(manifest_path)
    if getattr(args, "vertical", None) and args.vertical != manifest.vertical:
        raise ManifestValidationError(
            f"--vertical {args.vertical!r} conflicts with manifest vertical "
            f"{manifest.vertical!r}."
        )
    args.vertical = manifest.vertical
    args.vertical_manifest = manifest

    capability = {
        "discover": "discovery",
        "extract": "extraction",
        "batch": "extraction",
        "crawl": "acquisition",
    }[args.command]
    manifest.require_capability(capability)
    if args.command == "batch" and args.evaluate:
        manifest.require_capability("evaluation")

    if args.command == "discover":
        args.input_root = Path(args.input_root) if args.input_root else manifest.path("input_root")
        args.categories = args.categories or list(manifest.documents.categories)
        output_root = manifest.path("output_root")
        args.output = Path(args.output) if args.output else output_root / "schema.json"
        args.usage_log = (
            Path(args.usage_log) if args.usage_log else output_root / "token_usage.jsonl"
        )
    elif args.command == "batch":
        args.input_root = Path(args.input_root) if args.input_root else manifest.path("input_root")
    elif args.command == "crawl":
        args.config = Path(args.config) if args.config else manifest.path("acquisition_config")
        args.data_root = Path(args.data_root) if args.data_root else manifest.path("input_root")
        args.output_root = (
            Path(args.output_root)
            if args.output_root
            else manifest.path("acquisition_output")
        )
    return manifest


def command_discover(args: argparse.Namespace) -> int:
    from src.common.structured_output import StructuredOutputFailure
    from src.schema.discovery import SchemaDiscovery
    from src.verticals.registry import get_prompt, get_schema_validator

    try:
        selection = resolve_selection(
            provider=args.provider,
            model=args.model,
            document_input=args.document_input,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    manifest = args.vertical_manifest
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
            vertical=manifest.vertical,
            discovery_contract=manifest.contract("discovered_schema"),
            discovery_prompt=get_prompt(manifest.prompt("discovery")),
            schema_validator=get_schema_validator(
                manifest.adapter("schema_validator")
            ),
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
        data_contract=manifest.contract("discovered_schema"),
    )
    write_artifact(
        output_path,
        artifact,
        data_contract=manifest.contract("discovered_schema"),
    )
    print(f"Wrote schema draft to {output_path}")
    return 0


def command_extract(args: argparse.Namespace) -> int:
    manifest = args.vertical_manifest
    schema_data = load_schema_data(args.schema)
    if schema_data.get("vertical") != manifest.vertical:
        print(
            f"Schema vertical {schema_data.get('vertical')!r} does not match "
            f"manifest vertical {manifest.vertical!r}."
        )
        return 1

    selection = resolve_selection(provider=args.provider, model=args.model)
    extractor = _build_schema_extractor(
        manifest,
        schema_data,
        selection,
        pdf_root=manifest.path("input_root"),
    )

    record = extractor.extract_one(args.pdf)
    result = ExtractionResult(
        vertical=manifest.vertical,
        schema_version=str(schema_data["version"]),
        source_path=str(args.pdf),
        provider=selection.provider,
        model=selection.model,
        data=record,
    )

    output_path = args.output or default_output_path(manifest.vertical, Path(args.pdf))
    result.write_json(output_path)
    print(f"Wrote extraction to {output_path}")
    return 0


def command_batch(args: argparse.Namespace) -> int:
    from src.evaluation.metrics import ExtractionEvaluator, PrivateHealthGroundTruthStore
    from src.evaluation.reporter import EvaluationReporter

    config = load_config()
    manifest = args.vertical_manifest
    schema_data = load_schema_data(args.schema)
    if schema_data.get("vertical") != manifest.vertical:
        print(
            f"Schema vertical {schema_data.get('vertical')!r} does not match "
            f"manifest vertical {manifest.vertical!r}."
        )
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
    extractor = _build_schema_extractor(
        manifest,
        schema_data,
        selection,
        pdf_root=input_root,
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
                vertical=manifest.vertical,
                schema_version=str(schema_data["version"]),
                source_path=str(pdf_path),
                provider=selection.provider,
                model=selection.model,
                data=record,
            )
        except Exception as exc:
            extraction_errors += 1
            print(f"Extraction failed for {pdf_path.name}: {exc}")
            continue
        output_path = default_output_path(manifest.vertical, pdf_path)
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


def _build_schema_extractor(
    manifest: VerticalManifest,
    schema_data: dict[str, object],
    selection,
    *,
    pdf_root: Path,
):
    from src.schema_application.extractor import SchemaExtractor
    from src.verticals.registry import get_prompt, get_schema_validator

    return SchemaExtractor(
        schema_data=schema_data,
        selection=selection,
        schema_contract=manifest.contract("discovered_schema"),
        schema_validator=get_schema_validator(
            manifest.adapter("schema_validator")
        ),
        output_cardinality=manifest.documents.output_cardinality,
        extraction_prompt=get_prompt(manifest.prompt("extraction")),
        usage_log_path=manifest.path("output_root") / "extraction_usage.jsonl",
        pdf_root=pdf_root,
    )


def command_crawl(args: argparse.Namespace) -> int:
    from src.verticals.registry import get_acquisition_adapter

    manifest = args.vertical_manifest
    run_acquisition = get_acquisition_adapter(manifest.adapter("acquisition"))

    try:
        outcome = run_acquisition(
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
    try:
        configure_command(args)
    except ManifestValidationError as exc:
        parser.error(str(exc))
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
