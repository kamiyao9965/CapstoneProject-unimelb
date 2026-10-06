from __future__ import annotations

import argparse
import json
import re
import sys
from hashlib import sha256
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
from src.models import ExtractionResult, ProductExtractionResult
from src.schema.loader import SchemaLoader
from src.schema.sampler import (
    DEFAULT_CATEGORIES,
    DEFAULT_MANIFEST_ROLES,
    DEFAULT_MANIFEST_SAMPLE_COUNT,
    print_samples,
    select_manifest_samples,
    select_samples,
)
from src.schema.validator import SchemaValidator
from src.schema_application.prompts import PET_PRODUCT_FAMILY_PROMPT_VERSION


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Konkrd private-health extraction pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser(
        "discover",
        help="Generate a feat discovered JSON schema from sample PDFs",
    )
    discover.add_argument("--samples", nargs="+")
    discover.add_argument("--input-root", default=str(default_private_health_pdf_root()))
    discover.add_argument("--manifest", help="Manifest JSON for stratified document sampling")
    discover.add_argument("--vertical", default="private_health")
    discover.add_argument(
        "--document-roles", nargs="+", default=list(DEFAULT_MANIFEST_ROLES),
        help="Manifest document_role values to sample (used with --manifest)",
    )
    discover.add_argument(
        "--sample-count",
        type=int,
        default=DEFAULT_MANIFEST_SAMPLE_COUNT,
        help=(
            "Total documents for manifest sampling; defaults to 12 across PDS, "
            "policy-booklet, combined-FSG/PDS, and renewal-PDS roles."
        ),
    )
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
    extract.add_argument("--manifest")
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
    batch.add_argument("--manifest")
    batch.add_argument(
        "--product-ids",
        nargs="+",
        help=(
            "Pet insurance only: rerun these manifest product IDs and include "
            "their linked base/update documents. Shared multi-plan booklets are "
            "rerun as one family."
        ),
    )
    batch.add_argument(
        "--archive-stale-products",
        action="store_true",
        help=(
            "For pet-insurance batches, move existing product JSON files whose "
            "product_id is no longer in the current manifest inventory into an "
            "archive directory before extraction."
        ),
    )
    batch.add_argument(
        "--limit",
        type=int,
        help="Process at most the first N PDFs in sorted path order",
    )
    batch.add_argument("--evaluate", action="store_true")
    batch.add_argument(
        "--evaluation-manifest",
        help=(
            "Pinned evaluation manifest. When supplied, only its hash-verified PDFs "
            "are processed and GT IDs are loaded without fuzzy matching."
        ),
    )
    batch.add_argument(
        "--resume",
        action="store_true",
        help="Reuse successful outputs whose PDF, schema, provider, and model match.",
    )
    batch.add_argument(
        "--trust-legacy-cache",
        action="store_true",
        help=(
            "With --resume, adopt pre-checkpoint outputs after path/provider/model/mtime "
            "checks. Their hashes are written once so later resumes are exact."
        ),
    )
    batch.add_argument("--provider", default=None)
    batch.add_argument("--model", default=None)
    batch.add_argument(
        "--no-fallback",
        action="store_true",
        help="Deprecated compatibility flag; schema_application extraction has no heuristic fallback.",
    )

    manifest = subparsers.add_parser(
        "build-eval-manifest",
        help="Build a reproducible exact-match private-health evaluation set",
    )
    manifest.add_argument("--input-root", default=str(default_private_health_pdf_root()))
    manifest.add_argument("--count", type=int, default=50)
    manifest.add_argument("--seed", type=int, required=True)
    manifest.add_argument(
        "--output",
        default="configs/private_health/evaluation_manifest.json",
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
            if args.manifest:
                sample_paths = select_manifest_samples(
                    input_root=input_root,
                    manifest_path=args.manifest,
                    count=args.sample_count,
                    seed=args.seed,
                    roles=tuple(args.document_roles),
                )
            else:
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
            vertical=args.vertical,
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
        data_contract=f"{args.vertical}/discovered_schema",
    )
    write_artifact(
        output_path,
        artifact,
        data_contract=f"{args.vertical}/discovered_schema",
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
        pdf_root=Path(args.pdf).parent,
        manifest_path=args.manifest,
    )
    schema_hash = extractor.schema_hash
    schema_path = Path(args.schema)

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
    from src.evaluation.document_classifier import PhisDocumentClassifier
    from src.evaluation.metrics import ExtractionEvaluator, PrivateHealthGroundTruthStore
    from src.evaluation.reporter import EvaluationReporter
    from src.evaluation.manifest import EvaluationManifest
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
    evaluation_manifest = (
        EvaluationManifest.load(args.evaluation_manifest)
        if args.evaluation_manifest
        else None
    )
    if evaluation_manifest and not args.evaluate:
        print("--evaluation-manifest requires --evaluate")
        return 1
    pdf_paths = (
        evaluation_manifest.resolve_paths(input_root)
        if evaluation_manifest
        else sorted(input_root.rglob("*.pdf"))
    )
    if not pdf_paths:
        print(f"No PDFs found under {input_root}")
        return 1
    if args.product_ids and args.limit is not None:
        print("--product-ids cannot be combined with --limit")
        return 1
    if args.limit is not None:
        if args.limit <= 0:
            print("--limit must be greater than zero")
            return 1
        pdf_paths = pdf_paths[:args.limit]
        print(f"Selected {len(pdf_paths)} PDF(s) with --limit {args.limit}")

    selection = resolve_selection(provider=args.provider, model=args.model)
    manifest_path = args.manifest
    default_pet_manifest = PROJECT_ROOT / "configs" / "pet_insurance" / "document_manifest.json"
    if schema.vertical == "pet_insurance" and not manifest_path and default_pet_manifest.is_file():
        manifest_path = str(default_pet_manifest)
        print(f"Using pet-insurance document manifest: {manifest_path}")
    if args.product_ids:
        if schema.vertical != "pet_insurance" or not manifest_path:
            print("--product-ids requires a pet_insurance schema and document manifest")
            return 1
        from src.schema_application.product_families import select_manifest_product_paths

        try:
            pdf_paths = select_manifest_product_paths(manifest_path, args.product_ids)
        except (ValueError, FileNotFoundError) as exc:
            print(f"Could not resolve targeted pet products: {exc}")
            return 1
        print(
            f"Selected {len(pdf_paths)} manifest PDF(s) for product IDs: "
            + ", ".join(args.product_ids)
        )

    source_hashes = {pdf_path: file_sha256(pdf_path) for pdf_path in pdf_paths}
    unique_source_documents = len(set(source_hashes.values()))
    duplicate_source_documents = len(pdf_paths) - unique_source_documents
    extractor = Extractor(
        schema_data=schema_data,
        selection=selection,
        pdf_root=input_root,
        manifest_path=manifest_path,
    )
    # Batch resume/failure artifacts need the same schema identity as the
    # single-document command.  Define it before entering the per-PDF try block
    # so both the normal and exception paths can safely reference it.
    schema_path = Path(args.schema)
    schema_hash = extractor.schema_hash

    if schema.vertical == "pet_insurance" and manifest_path:
        return run_pet_product_batch(
            extractor=extractor,
            schema=schema,
            pdf_paths=pdf_paths,
            source_hashes=source_hashes,
            manifest_path=manifest_path,
            selection=selection,
            resume=args.resume,
            archive_stale_products=args.archive_stale_products,
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
    resumed_documents = 0
    gt_match_diagnostics: list[dict[str, object]] = []
    gt_store = None
    evaluator = None
    reporter = None
    document_classifier = None
    evaluated_source_hashes: set[str] = set()
    if args.evaluate and args.vertical in {"private_health", "private_health_au"}:
        gt_store = PrivateHealthGroundTruthStore(
            config.data_dir / "private_health" / "labelled"
        )
        evaluator = ExtractionEvaluator()
        reporter = EvaluationReporter()
        document_classifier = PhisDocumentClassifier()

    for pdf_path in pdf_paths:
        output_path = default_output_path(schema.vertical, pdf_path)
        source_hash = source_hashes[pdf_path]
        try:
            result = None
            if args.resume:
                result = load_cached_batch_result(
                    output_path=output_path,
                    pdf_path=pdf_path,
                    schema_path=schema_path,
                    schema_hash=schema_hash,
                    source_hash=source_hash,
                    provider=selection.provider,
                    model=selection.model,
                    trust_legacy=args.trust_legacy_cache,
                )
            if result is not None:
                resumed_documents += 1
                print(f"Resumed {pdf_path.name} <- {output_path}")
            else:
                record = extractor.extract_one(pdf_path)
                result = ExtractionResult(
                    vertical=schema.vertical,
                    schema_version=schema.version,
                    source_path=str(pdf_path),
                    provider=selection.provider,
                    model=selection.model,
                    schema_sha256=schema_hash,
                    source_sha256=source_hash,
                    data=record,
                )
                result.write_json(output_path)
                print(f"Extracted {pdf_path.name} -> {output_path}")
        except Exception as exc:
            extraction_errors += 1
            print(f"Extraction failed for {pdf_path.name}: {exc}")
            safely_record_batch_failure(
                vertical=schema.vertical,
                pdf_path=pdf_path,
                schema_hash=schema_hash,
                source_hash=source_hash,
                provider=selection.provider,
                model=selection.model,
                document_input=selection.document_input,
                error=exc,
            )
            continue
        provider_counts[result.provider] = provider_counts.get(result.provider, 0) + 1
        if args.evaluate and result.provider == "heuristic":
            fallback_documents += 1
        for warning in result.warnings:
            warning_counts[warning] = warning_counts.get(warning, 0) + 1
            if len(warning_samples) < 5 and warning not in warning_samples:
                warning_samples.append(warning)
        if gt_store and evaluator:
            if source_hash in evaluated_source_hashes:
                continue
            evaluated_source_hashes.add(source_hash)
            try:
                if evaluation_manifest:
                    manifest_entry = evaluation_manifest.entry_for(pdf_path, input_root)
                    pinned_ids = (
                        manifest_entry.component_id_masters
                        or [manifest_entry.id_master]
                    )
                    product_match, ground_truth = gt_store.load_ground_truth_for_ids(
                        pdf_path, pinned_ids
                    )
                else:
                    product_match, ground_truth = gt_store.load_ground_truth(pdf_path)
                if ground_truth:
                    source_documents = (
                        document_classifier.load_documents(pdf_path)
                        if document_classifier
                        else None
                    )
                    document_classification = (
                        document_classifier.classify_documents(
                            source_documents or (), ground_truth
                        )
                        if document_classifier
                        else None
                    )
                    report = evaluator.evaluate(
                        extracted=result,
                        ground_truth=ground_truth,
                        product_key=product_match.id_master if product_match else None,
                        document_classification=document_classification,
                        source_documents=source_documents,
                    )
                else:
                    report = None
            except Exception as exc:
                evaluated_source_hashes.discard(source_hash)
                print(f"Evaluation failed for {pdf_path.name}: {exc}")
                safely_record_batch_failure(
                    vertical=schema.vertical,
                    pdf_path=pdf_path,
                    schema_hash=schema_hash,
                    source_hash=source_hash,
                    provider=selection.provider,
                    model=selection.model,
                    document_input=selection.document_input,
                    error=exc,
                    stage="evaluation",
                )
                continue
            if report is not None:
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
            total_documents=unique_source_documents,
            unmatched_documents=unmatched_documents,
            low_confidence_matches=low_confidence_matches,
            fallback_documents=fallback_documents,
            extraction_errors=extraction_errors,
            duplicate_source_documents=duplicate_source_documents,
        )
        model_reports = [
            report for report in reports
            if report.extraction_provider and report.extraction_provider != "heuristic"
        ]
        model_low_confidence_matches = sum(
            report.low_confidence_match for report in model_reports
        )
        model_summary = evaluator.aggregate(
            model_reports,
            total_documents=unique_source_documents,
            unmatched_documents=max(
                unique_source_documents - extraction_errors - len(model_reports), 0
            ),
            low_confidence_matches=model_low_confidence_matches,
            fallback_documents=fallback_documents,
            extraction_errors=extraction_errors,
            duplicate_source_documents=duplicate_source_documents,
        )
        high_confidence_reports = [report for report in reports if not report.low_confidence_match]
        high_confidence_model_reports = [
            report for report in model_reports if not report.low_confidence_match
        ]
        summary["high_confidence_only"] = evaluator.aggregate(
            high_confidence_reports,
            total_documents=len(high_confidence_reports),
        )
        summary["high_confidence_only"]["excluded_low_confidence_documents"] = (
            len(reports) - len(high_confidence_reports)
        )
        model_summary["high_confidence_only"] = evaluator.aggregate(
            high_confidence_model_reports,
            total_documents=len(high_confidence_model_reports),
        )
        model_summary["high_confidence_only"]["excluded_low_confidence_documents"] = (
            len(model_reports) - len(high_confidence_model_reports)
        )
        summary["ambiguous_matches"] = float(ambiguous_matches)
        ambiguous_sources = {
            str(item["source_path"])
            for item in gt_match_diagnostics
            if item.get("issue") == "ambiguous"
        }
        model_summary["ambiguous_matches"] = float(sum(
            report.source_path in ambiguous_sources for report in model_reports
        ))
        model_summary["identical_to_full_report"] = (
            len(model_reports) == len(reports)
        )
        report_root = config.outputs_dir / args.vertical / "evaluation"
        reporter.write_json(reports, summary, report_root / "report.json")
        reporter.write_markdown(reports, summary, report_root / "report.md")
        reporter.write_json(model_reports, model_summary, report_root / "report_model_only.json")
        reporter.write_markdown(model_reports, model_summary, report_root / "report_model_only.md")
        reporter.write_claim_evidence(reports, report_root / "claim_evidence.json")
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
    print(f"  Completed: {len(pdf_paths) - extraction_errors}/{len(pdf_paths)}")
    print(f"  Resumed: {resumed_documents}")
    print(f"  Failed: {extraction_errors}")
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


def run_pet_product_batch(
    *,
    extractor: object,
    schema: object,
    pdf_paths: list[Path],
    source_hashes: dict[Path, str],
    manifest_path: str | Path,
    selection: object,
    resume: bool,
    archive_stale_products: bool = False,
) -> int:
    """Extract pet insurance as product families and emit one file per product."""
    from src.schema_application.product_families import resolve_product_families

    families = resolve_product_families(manifest_path, pdf_paths)
    hashes_by_resolved_path = {
        path.resolve(): digest for path, digest in source_hashes.items()
    }
    output_root = load_config().outputs_dir / "pet_insurance" / "products"
    expected_product_ids = {
        product_id
        for family in families
        for product_id in family.expected_product_ids
    }
    if archive_stale_products:
        if not expected_product_ids:
            raise ValueError(
                "Cannot archive stale product outputs when the manifest has no "
                "declared product IDs."
            )
        archived = _archive_stale_product_outputs(output_root, expected_product_ids)
        if archived:
            print(f"Archived {len(archived)} stale product output(s): {archived[0].parent}")
    completed_products = 0
    resumed_products = 0
    failed_families = 0
    claimed_outputs: dict[Path, str] = {}
    for family in families:
        for document in family.unattached_documents:
            print(
                "Unattached amendment skipped: "
                f"{document.get('document_id')} in family {family.family_id}; "
                "no amends_document_ids link reaches a base document."
            )
        family_hashes = [hashes_by_resolved_path[path.resolve()] for path in family.pdf_paths]
        expected_paths = [
            output_root / f"{_safe_product_id(product_id)}.json"
            for product_id in family.expected_product_ids
        ]
        for path in expected_paths:
            owner = claimed_outputs.get(path)
            if owner is not None and owner != family.family_id:
                raise ValueError(
                    f"Product output {path.name} is claimed by both {owner!r} and "
                    f"{family.family_id!r}."
                )
            claimed_outputs[path] = family.family_id
        if resume and expected_paths and all(
            _cached_product_matches(
                path,
                schema_hash=extractor.schema_hash,
                source_hashes=family_hashes,
                provider=selection.provider,
                model=selection.model,
                extraction_prompt_version=PET_PRODUCT_FAMILY_PROMPT_VERSION,
            )
            for path in expected_paths
        ):
            resumed_products += len(expected_paths)
            completed_products += len(expected_paths)
            print(
                f"Resumed product family {family.family_id}: "
                f"{len(expected_paths)} product(s)"
            )
            continue
        try:
            record = extractor.extract_product_family(family)
            products = record.get("products")
            if not isinstance(products, list) or not products:
                raise ValueError(f"Product family {family.family_id} returned no products.")
            seen_ids: set[str] = set()
            prepared: list[tuple[str, ProductExtractionResult, Path]] = []
            for index, product_value in enumerate(products, start=1):
                if not isinstance(product_value, dict):
                    raise ValueError(
                        f"Product family {family.family_id} returned a non-object product."
                    )
                product = dict(product_value)
                product_id = _resolved_product_id(product, family.family_id, index)
                if product_id in seen_ids:
                    raise ValueError(
                        f"Product family {family.family_id} returned duplicate product_id "
                        f"{product_id!r}."
                    )
                seen_ids.add(product_id)
                if "product_id" in product:
                    product["product_id"] = product_id
                result = ProductExtractionResult(
                    schema_version=schema.version,
                    product_id=product_id,
                    document_family_id=family.family_id,
                    source_documents=list(record["source_documents"]),
                    provider=selection.provider,
                    model=selection.model,
                    schema_sha256=extractor.schema_hash,
                    extraction_prompt_version=PET_PRODUCT_FAMILY_PROMPT_VERSION,
                    source_sha256=family_hashes,
                    data=product,
                    family_notes=(
                        record.get("_notes")
                        if isinstance(record.get("_notes"), str)
                        else None
                    ),
                )
                output_path = output_root / f"{_safe_product_id(product_id)}.json"
                owner = claimed_outputs.get(output_path)
                if owner is not None and owner != family.family_id:
                    raise ValueError(
                        f"Product output {output_path.name} is claimed by both "
                        f"{owner!r} and {family.family_id!r}."
                    )
                claimed_outputs[output_path] = family.family_id
                prepared.append((product_id, result, output_path))
            for product_id, result, output_path in prepared:
                result.write_json(output_path)
                completed_products += 1
                print(
                    f"Extracted product {product_id} from {len(family.pdf_paths)} PDF(s) "
                    f"-> {output_path}"
                )
        except Exception as exc:
            failed_families += 1
            print(f"Product-family extraction failed for {family.family_id}: {exc}")
            failure_path = safely_record_batch_failure(
                vertical="pet_insurance",
                pdf_path=family.pdf_paths[0],
                schema_hash=extractor.schema_hash,
                source_hash=family_hashes[0],
                pdf_paths=list(family.pdf_paths),
                source_hashes=family_hashes,
                provider=selection.provider,
                model=selection.model,
                document_input=selection.document_input,
                error=exc,
                stage="product_family_extraction",
            )
            if failure_path is not None:
                print(f"Failure artifact: {failure_path}")

    print("\nProduct-oriented batch summary:")
    print(f"  Document families: {len(families)}")
    print(f"  Products written/resumed: {completed_products}")
    print(f"  Resumed products: {resumed_products}")
    print(f"  Failed families: {failed_families}")
    print(f"  Output directory: {output_root}")
    return 1 if failed_families else 0


def _resolved_product_id(product: dict[str, object], family_id: str, index: int) -> str:
    value = product.get("product_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return family_id if index == 1 else f"{family_id}_{index}"


def _safe_product_id(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip()).strip("._")
    if not safe:
        raise ValueError("product_id cannot be converted to a safe output filename.")
    return safe


def _cached_product_matches(
    path: Path,
    *,
    schema_hash: str,
    source_hashes: list[str],
    provider: str,
    model: str,
    extraction_prompt_version: str,
) -> bool:
    if not path.is_file():
        return False
    try:
        result = ProductExtractionResult.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return (
        result.schema_sha256 == schema_hash
        and result.source_sha256 == source_hashes
        and result.provider == provider
        and result.model == model
        and result.extraction_prompt_version == extraction_prompt_version
    )


def _archive_stale_product_outputs(
    output_root: Path, expected_product_ids: set[str]
) -> list[Path]:
    """Move obsolete product records aside without deleting recoverable data."""
    if not output_root.is_dir():
        return []
    stale: list[Path] = []
    for path in sorted(output_root.glob("*.json")):
        try:
            result = ProductExtractionResult.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except Exception:
            continue
        if result.product_id not in expected_product_ids:
            stale.append(path)
    if not stale:
        return []
    archive_root = output_root / "archive" / f"stale-{uuid4().hex}"
    archive_root.mkdir(parents=True, exist_ok=False)
    archived: list[Path] = []
    for path in stale:
        target = archive_root / path.name
        path.replace(target)
        archived.append(target)
    return archived


def command_build_eval_manifest(args: argparse.Namespace) -> int:
    from src.evaluation.manifest import build_evaluation_manifest
    from src.evaluation.metrics import PrivateHealthGroundTruthStore

    if args.count <= 0:
        print("--count must be greater than zero")
        return 1
    config = load_config()
    store = PrivateHealthGroundTruthStore(
        config.data_dir / "private_health" / "labelled"
    )
    try:
        manifest, diagnostics = build_evaluation_manifest(
            input_root=args.input_root,
            ground_truth_store=store,
            count=args.count,
            seed=args.seed,
        )
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"Could not build evaluation manifest: {exc}")
        return 1
    output = manifest.write(args.output)
    print(f"Wrote {len(manifest.entries)} pinned evaluation documents to {output}")
    print(json.dumps(diagnostics, indent=2, sort_keys=True))
    return 0


def default_output_path(vertical: str, pdf_path: Path) -> Path:
    config = load_config()
    relative_parts = pdf_path.with_suffix(".json").parts[-4:]
    return config.outputs_dir / vertical / "extractions" / Path(*relative_parts)


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_cached_batch_result(
    *,
    output_path: Path,
    pdf_path: Path,
    schema_path: Path,
    schema_hash: str,
    source_hash: str,
    provider: str,
    model: str,
    trust_legacy: bool,
) -> ExtractionResult | None:
    if not output_path.is_file():
        return None
    try:
        result = ExtractionResult.model_validate_json(
            output_path.read_text(encoding="utf-8")
        )
    except Exception:
        return None
    try:
        same_source = Path(result.source_path).resolve() == pdf_path.resolve()
    except OSError:
        same_source = False
    if not same_source or result.provider != provider or result.model != model:
        return None
    if result.schema_sha256 == schema_hash and result.source_sha256 == source_hash:
        return result
    is_legacy = result.schema_sha256 is None and result.source_sha256 is None
    if not (trust_legacy and is_legacy):
        return None
    newest_input = max(pdf_path.stat().st_mtime, schema_path.stat().st_mtime)
    if output_path.stat().st_mtime < newest_input:
        return None
    result.schema_sha256 = schema_hash
    result.source_sha256 = source_hash
    result.write_json(output_path)
    return result


def record_batch_failure(
    *,
    vertical: str,
    pdf_path: Path,
    schema_hash: str,
    source_hash: str,
    provider: str,
    model: str,
    document_input: str,
    error: Exception,
    stage: str = "extraction",
    pdf_paths: list[Path] | None = None,
    source_hashes: list[str] | None = None,
) -> Path:
    run_id = uuid4().hex
    all_pdf_paths = pdf_paths or [pdf_path]
    all_source_hashes = source_hashes or [source_hash]
    structured_errors = getattr(getattr(error, "result", None), "errors", ())
    details = [dict(item) for item in structured_errors if isinstance(item, dict)]
    failure = build_failure_artifact(
        artifact_type=f"batch_{stage}_error",
        contract_version="1.0.0",
        provenance={
            "run_id": run_id,
            "provider": provider,
            "model": model,
            "document_input": document_input,
            "source_documents": [path.as_posix() for path in all_pdf_paths],
            "source_artifacts": [
                f"schema_sha256:{schema_hash}",
                *(f"pdf_sha256:{digest}" for digest in all_source_hashes),
            ],
        },
        error_code=f"{stage}_failed",
        message=str(error),
        details=details,
    )
    return write_failure_artifact(
        load_config().outputs_dir / vertical / "extractions",
        f"batch_{stage}",
        run_id,
        failure,
    )


def safely_record_batch_failure(**kwargs: object) -> Path | None:
    try:
        return record_batch_failure(**kwargs)  # type: ignore[arg-type]
    except Exception as artifact_error:
        pdf_path = kwargs.get("pdf_path")
        print(
            f"Could not write failure artifact for {pdf_path}: {artifact_error}; "
            "continuing with the remaining PDFs."
        )
        return None


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
    if args.command == "build-eval-manifest":
        return command_build_eval_manifest(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
