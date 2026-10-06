from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.common.json_artifacts import (
    build_failure_artifact,
    next_available_path,
    build_success_artifact,
    write_artifact,
    write_failure_artifact,
    write_text_output,
)
from src.common.json_codec import dumps_json
from src.common.json_contracts import load_contract
from src.common.model_config import resolve_selection
from src.common.models import ExtractionResult
from src.pdf_ingestion.adapter import DEFAULT_DOCUMENT_PARSER, DOCUMENT_PARSERS
from src.schema.sampler import category_from_path, print_samples, select_samples
from src.schema.loader import load_schema_data
from src.verticals.manifest import ManifestValidationError, VerticalManifest, resolve_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Konkrd insurance extraction pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser(
        "discover",
        help="Generate a draft JSON schema from sample PDFs",
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
    discover.add_argument(
        "--document-parser",
        choices=DOCUMENT_PARSERS,
        default=DEFAULT_DOCUMENT_PARSER,
        help="PDF parsing route: pdfingestor (default) or mineru (local MinerU pipeline)",
    )
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
        "--document-parser",
        choices=DOCUMENT_PARSERS,
        default=DEFAULT_DOCUMENT_PARSER,
        help="PDF parsing route: pdfingestor (default) or mineru (local MinerU pipeline)",
    )
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
    batch.add_argument(
        "--categories",
        nargs="+",
        help="Only extract PDFs inside these manifest category folders, for example pds",
    )
    batch.add_argument(
        "--output-dir",
        help=(
            "Write <dir>/<path below input root>.json and skip PDFs whose result "
            "already exists there, so an interrupted run can be resumed"
        ),
    )
    batch.add_argument("--evaluate", action="store_true")
    batch.add_argument("--provider", default=None)
    batch.add_argument("--model", default=None)
    batch.add_argument(
        "--document-parser",
        choices=DOCUMENT_PARSERS,
        default=DEFAULT_DOCUMENT_PARSER,
        help="PDF parsing route: pdfingestor (default) or mineru (local MinerU pipeline)",
    )
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

    canonical_compile = subparsers.add_parser(
        "canonical-compile",
        help="Compile one human-approved Canonical Schema without applying DDL",
    )
    canonical_compile.add_argument("--manifest")
    canonical_compile.add_argument("--schema", required=True)
    canonical_compile.add_argument("--output-dir", required=True)

    storage_init = subparsers.add_parser(
        "storage-init",
        help="Create missing PostgreSQL core and vertical storage tables",
    )
    storage_init.add_argument("--manifest")
    storage_init.add_argument("--schema")
    storage_init.add_argument(
        "--database-url-env",
        default="KONKRD_DATABASE_URL",
        help="Environment variable containing the PostgreSQL URL",
    )

    storage_load = subparsers.add_parser(
        "storage-load",
        help="Load one validated extraction artifact into PostgreSQL",
    )
    storage_load.add_argument("--manifest")
    storage_load.add_argument("--schema")
    storage_load.add_argument("--artifact", required=True)
    storage_load.add_argument("--insurer-code", required=True)
    storage_load.add_argument(
        "--database-url-env",
        default="KONKRD_DATABASE_URL",
        help="Environment variable containing the PostgreSQL URL",
    )

    storage_load_batch = subparsers.add_parser(
        "storage-load-batch",
        help="Load every validated extraction artifact in a folder into PostgreSQL",
    )
    storage_load_batch.add_argument("--manifest")
    storage_load_batch.add_argument("--schema")
    storage_load_batch.add_argument("--artifact-dir", required=True)
    storage_load_batch.add_argument(
        "--database-url-env",
        default="KONKRD_DATABASE_URL",
        help="Environment variable containing the PostgreSQL URL",
    )

    quality_audit = subparsers.add_parser(
        "quality-audit", help="Screen extraction artifacts against source PDFs with an LLM judge",
    )
    quality_audit.add_argument("--manifest")
    quality_audit.add_argument("--schema", help="Approved Canonical Schema override")
    quality_audit.add_argument("--artifact-dir", required=True)
    quality_audit.add_argument("--source-root", help="Allowed source PDF root")
    quality_audit.add_argument("--output-dir", required=True, help="Separate audit directory; new unless --resume or --summary-only")
    quality_audit.add_argument("--provider")
    quality_audit.add_argument("--model")
    quality_audit.add_argument("--document-parser", choices=DOCUMENT_PARSERS,
                               help="Default: use each extraction artifact's parser route")
    quality_audit.add_argument("--sample-rate", type=float, default=0.05)
    quality_audit.add_argument("--seed", type=int, default=42)
    quality_audit.add_argument("--max-document-chars", type=int, default=120_000)
    quality_audit.add_argument("--max-extraction-chars", type=int, default=60_000)
    quality_audit.add_argument("--resume", action="store_true",
                               help="Reuse verified reports in an existing audit directory")
    quality_audit.add_argument("--summary-only", action="store_true",
                               help="Build results.json from an existing audit without model calls")
    quality_audit.add_argument("--max-failures", type=int, default=3,
                               help="Stop after this many new document failures (default: 3)")

    return parser


def configure_command(args: argparse.Namespace) -> VerticalManifest:
    """Load one manifest and apply its defaults before a command runs."""
    manifest = resolve_manifest(args.manifest, vertical=getattr(args, "vertical", None), operation=args.command)
    args.vertical = manifest.vertical
    args.vertical_manifest = manifest

    if args.command == "batch" and args.evaluate:
        manifest.require_capability("evaluation")

    if args.command == "discover":
        args.input_root = Path(args.input_root) if args.input_root else manifest.path("input_root")
        args.categories = args.categories or list(manifest.documents.categories)
        output_root = manifest.path("output_root")
        args.output = Path(args.output) if args.output else output_root / "schemas/schema.json"
        args.usage_log = (
            Path(args.usage_log) if args.usage_log else output_root / "logs/discovery_usage.jsonl"
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
    elif args.command == "canonical-compile":
        args.schema = Path(args.schema)
        args.output_dir = Path(args.output_dir)
    elif args.command in {"storage-init", "storage-load", "storage-load-batch"}:
        args.schema = Path(args.schema) if args.schema else manifest.path("canonical_schema")
        if args.command == "storage-load":
            args.artifact = Path(args.artifact)
        if args.command == "storage-load-batch":
            args.artifact_dir = Path(args.artifact_dir)
    elif args.command == "quality-audit":
        args.schema = Path(args.schema) if args.schema else manifest.path("canonical_schema")
        args.artifact_dir = Path(args.artifact_dir)
        args.source_root = Path(args.source_root) if args.source_root else manifest.path("input_root")
        args.output_dir = Path(args.output_dir)
    return manifest


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
            document_parser=args.document_parser,
            manifest=manifest,
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
                "document_parser": args.document_parser,
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
            error_path = (
                output_path.parent
                / "errors"
                / "schema_discovery"
                / f"{run_id}.json"
            )
            if not error_path.is_file():
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
            "document_parser": args.document_parser,
            "source_documents": list(sample_paths),
            "source_artifacts": [],
        },
        data_contract_schema=load_contract(manifest.contract("discovered_schema"), manifest=manifest),
    )
    write_artifact(
        output_path,
        artifact,
        data_contract_schema=load_contract(manifest.contract("discovered_schema"), manifest=manifest),
    )
    print(f"Wrote schema draft to {output_path}")
    return 0


def command_extract(args: argparse.Namespace) -> int:
    manifest = args.vertical_manifest
    output_path = Path(args.output) if args.output else default_output_path(manifest, Path(args.pdf))
    if output_path.exists():
        print(f"Output already exists; choose a new path: {output_path}")
        return 1
    schema_data = load_schema_data(args.schema, args.vertical_manifest)
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
        document_parser=args.document_parser,
    )

    record = extractor.extract_one(args.pdf)
    result = ExtractionResult(
        vertical=manifest.vertical,
        schema_version=str(schema_data["version"]),
        source_path=str(args.pdf),
        provider=selection.provider,
        model=selection.model,
        document_parser=args.document_parser,
        data=record,
    )

    result.write_json(output_path)
    print(f"Wrote extraction to {output_path}")
    return 0


def command_batch(args: argparse.Namespace) -> int:
    from src.verticals.registry import get_evaluation_tools

    manifest = args.vertical_manifest
    categories = tuple(category.lower() for category in args.categories or ())
    unknown_categories = sorted(set(categories) - set(manifest.documents.categories))
    if unknown_categories:
        print(
            f"Unknown categories {unknown_categories}; choose from: "
            f"{', '.join(manifest.documents.categories)}."
        )
        return 2
    schema_data = load_schema_data(args.schema, args.vertical_manifest)
    if schema_data.get("vertical") != manifest.vertical:
        print(
            f"Schema vertical {schema_data.get('vertical')!r} does not match "
            f"manifest vertical {manifest.vertical!r}."
        )
        return 1

    input_root = (
        Path(args.input_root)
        if args.input_root
        else manifest.path("input_root")
    )
    pdf_paths = sorted(input_root.rglob("*.pdf"))
    if categories:
        pdf_paths = [
            path
            for path in pdf_paths
            if category_from_path(path.relative_to(input_root), categories)
        ]
    if not pdf_paths:
        in_categories = f" in categories {', '.join(categories)}" if categories else ""
        print(f"No PDFs found under {input_root}{in_categories}")
        return 1

    output_dir = Path(args.output_dir) if args.output_dir else None
    selection = resolve_selection(provider=args.provider, model=args.model)
    skipped_existing = 0
    if output_dir is not None:
        from src.schema_application.records import load_cached_extraction

        pending_paths = []
        for pdf_path in pdf_paths:
            target = output_dir / _extraction_relative_path(pdf_path, input_root)
            if target.exists():
                try:
                    load_cached_extraction(
                        target, pdf_path=pdf_path, schema=schema_data, manifest=manifest,
                        selection=selection, document_parser=args.document_parser,
                    )
                except (OSError, ValueError) as exc:
                    print(f"Cannot reuse existing extraction {target}: {exc}")
                    return 1
                print(f"Skipping {pdf_path.name}: result already exists at {target}")
            else:
                pending_paths.append(pdf_path)
        skipped_existing = len(pdf_paths) - len(pending_paths)
        pdf_paths = pending_paths
        if not pdf_paths:
            print(f"Nothing to extract: all {skipped_existing} results already exist in {output_dir}.")
            return 0

    extractor = _build_schema_extractor(
        manifest,
        schema_data,
        selection,
        pdf_root=input_root,
        document_parser=args.document_parser,
    )

    provider_counts: dict[str, int] = {}
    extraction_errors = 0
    evaluation = None
    if args.evaluate:
        from src.evaluation.batch import BatchEvaluation

        evaluation = BatchEvaluation(
            *get_evaluation_tools(manifest, manifest.path("labelled_root"))
        )

    for pdf_path in pdf_paths:
        try:
            record = extractor.extract_one(pdf_path)
            result = ExtractionResult(
                vertical=manifest.vertical,
                schema_version=str(schema_data["version"]),
                source_path=str(pdf_path),
                provider=selection.provider,
                model=selection.model,
                document_parser=args.document_parser,
                data=record,
            )
        except Exception as exc:
            extraction_errors += 1
            print(f"Extraction failed for {pdf_path.name}: {exc}")
            continue
        output_path = (
            output_dir / _extraction_relative_path(pdf_path, input_root)
            if output_dir is not None
            else default_output_path(manifest, pdf_path, input_root=input_root)
        )
        result.write_json(output_path)
        provider_counts[result.provider] = provider_counts.get(result.provider, 0) + 1
        print(f"Extracted {pdf_path.name} -> {output_path}")

        if evaluation is not None:
            evaluation.evaluate_one(pdf_path, result)

    if evaluation is not None:
        report_root = manifest.path("output_root") / "evaluation"
        evaluation.write_reports(
            report_root, total_documents=len(pdf_paths), extraction_errors=extraction_errors,
        )
        print(f"Wrote evaluation reports to {report_root}")
        if evaluation.diagnostics:
            print(
                "Evaluation warning: wrote ground-truth match diagnostics for unmatched "
                "or ambiguous PDFs."
            )

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
    if output_dir is not None:
        print(f"  Skipped existing results: {skipped_existing}")
    print(f"  Extraction errors: {extraction_errors}")
    return 1 if extraction_errors else 0


def _build_schema_extractor(
    manifest: VerticalManifest,
    schema_data: dict[str, object],
    selection,
    *,
    pdf_root: Path,
    document_parser: str,
):
    from src.schema_application.extractor import SchemaExtractor

    return SchemaExtractor(
        schema_data=schema_data,
        selection=selection,
        manifest=manifest,
        usage_log_path=manifest.path("output_root") / "logs/extraction_usage.jsonl",
        pdf_root=pdf_root,
        document_parser=document_parser,
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


def command_canonical_compile(args: argparse.Namespace) -> int:
    """Render reviewed extraction and PostgreSQL contracts without applying DDL."""
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable

    from src.schema.canonical import compile_canonical_extraction_contract
    from src.storage.canonical import compile_vertical_storage_metadata

    output_dir = Path(args.output_dir)
    try:
        schema_data = load_schema_data(args.schema, args.vertical_manifest)
        if schema_data.get("vertical") != args.vertical_manifest.vertical:
            raise ValueError(
                f"Canonical Schema vertical {schema_data.get('vertical')!r} does not "
                f"match manifest vertical {args.vertical_manifest.vertical!r}."
            )
        extraction_contract = compile_canonical_extraction_contract(schema_data)
        compiled_storage = compile_vertical_storage_metadata(schema_data)
        ddl = str(
            CreateTable(compiled_storage.table).compile(
                dialect=postgresql.dialect()
            )
        )
        if output_dir.exists():
            raise FileExistsError(
                f"Refusing to overwrite existing output directory: {output_dir}"
            )
        output_dir.mkdir(parents=True, exist_ok=False)
        extraction_path = write_text_output(
            output_dir / "extraction_contract.json",
            dumps_json(extraction_contract, ensure_ascii=False, indent=2) + "\n",
        )
        ddl_path = write_text_output(
            output_dir / "vertical_table.sql",
            ddl.rstrip() + ";\n",
        )
    except Exception as exc:
        print(f"Canonical Schema compilation failed: {exc}")
        return 1

    print(f"Extraction contract: {extraction_path.resolve()}")
    print(f"PostgreSQL DDL preview: {ddl_path.resolve()}")
    return 0


def command_storage_init(args: argparse.Namespace) -> int:
    """Create the approved PostgreSQL schema without destructive migrations."""
    from src.storage.service import initialize_storage, resolve_database_url

    try:
        database_url = resolve_database_url(args.database_url_env)
        initialize_storage(
            database_url=database_url,
            manifest=args.vertical_manifest,
            schema_path=args.schema,
        )
    except Exception as exc:
        print(f"PostgreSQL storage initialization failed: {exc}")
        return 1
    print(
        f"PostgreSQL storage is ready for vertical={args.vertical_manifest.vertical}, "
        f"schema={Path(args.schema).resolve()}"
    )
    return 0


def command_storage_load(args: argparse.Namespace) -> int:
    """Validate and atomically load one extraction artifact into PostgreSQL."""
    from src.storage.service import load_extraction_artifact, resolve_database_url

    try:
        database_url = resolve_database_url(args.database_url_env)
        summary = load_extraction_artifact(
            database_url=database_url,
            manifest=args.vertical_manifest,
            schema_path=args.schema,
            artifact_path=args.artifact,
            insurer_code=args.insurer_code,
        )
    except Exception as exc:
        print(f"PostgreSQL storage load failed: {exc}")
        return 1
    print(
        "PostgreSQL load complete: "
        f"run_id={summary.run_id}, document_id={summary.document_id}, "
        f"schema_version_id={summary.schema_version_id}, "
        f"products={summary.products_loaded}, releases={len(summary.release_ids)}"
    )
    return 0


def command_storage_load_batch(args: argparse.Namespace) -> int:
    """Load every extraction artifact in a folder, one transaction per artifact."""
    from src.storage.service import load_extraction_directory, resolve_database_url

    try:
        database_url = resolve_database_url(args.database_url_env)
        results = load_extraction_directory(
            database_url=database_url,
            manifest=args.vertical_manifest,
            schema_path=args.schema,
            artifact_dir=args.artifact_dir,
        )
    except Exception as exc:
        print(f"PostgreSQL batch load failed: {exc}")
        return 1
    for result in results:
        if result.summary is not None:
            print(
                f"Loaded {result.artifact_path} (insurer={result.insurer_code}, "
                f"products={result.summary.products_loaded})"
            )
        else:
            print(f"Failed {result.artifact_path}: {result.error}")
    failed = sum(result.summary is None for result in results)
    print(
        "PostgreSQL batch load summary: "
        f"loaded={len(results) - failed}, failed={failed}, total={len(results)}"
    )
    return 1 if failed else 0


def command_quality_audit(args: argparse.Namespace) -> int:
    """Optional offline quality screen; no extraction, schema or DB mutation."""
    from src.common.model_provider import create_provider
    from src.evaluation.quality import run_quality_audit

    try:
        selection = resolve_selection(
            provider=args.provider, model=args.model, document_input="markdown",
        )
        result = run_quality_audit(
            manifest=args.vertical_manifest, schema_path=args.schema,
            artifact_dir=args.artifact_dir, source_root=args.source_root,
            output_dir=args.output_dir, selection=selection,
            provider=None if args.summary_only else create_provider(selection),
            sample_rate=args.sample_rate,
            seed=args.seed, document_parser=args.document_parser,
            max_document_chars=args.max_document_chars,
            max_extraction_chars=args.max_extraction_chars,
            resume=args.resume, summary_only=args.summary_only,
            max_failures=args.max_failures,
        )
    except (ValueError, FileNotFoundError, FileExistsError) as exc:
        print(f"Quality audit failed: {exc}")
        return 1
    except Exception as exc:
        # Provider failures may contain sensitive request details; keep CLI/UI output safe.
        print(f"Quality audit failed ({type(exc).__name__}); inspect configuration and any saved results.json before resuming.")
        return 1
    print(f"Quality results JSON: {result.results_path}")
    print(
        "Quality audit: "
        f"completed={result.completed_documents}, "
        f"failed={result.failed_documents}, "
        f"pending={result.pending_documents}"
    )
    if result.queue_path is not None:
        print(f"Quality review queue: {result.queue_path}")
    else:
        print("Quality review queue is unavailable until every document has a valid judge report.")
    print(f"Open review UI: .venv/bin/python -m streamlit run src/ui/quality_review_app.py -- --quality-dir {shlex.quote(str(args.output_dir))}")
    if args.summary_only:
        return 0
    return 0 if result.queue_path is not None else 1


def default_output_path(manifest: VerticalManifest, pdf_path: Path, *, input_root=None) -> Path:
    from src.common.json_artifacts import next_available_path

    relative = _extraction_relative_path(pdf_path, input_root or manifest.path("input_root"))
    return next_available_path(manifest.path("output_root") / "extractions" / relative)


def _extraction_relative_path(pdf_path: Path, input_root: str | Path) -> Path:
    import hashlib

    source = pdf_path.resolve()
    root = Path(input_root).resolve()
    if source.is_relative_to(root):
        return source.relative_to(root).with_suffix(".json")
    # Explicit PDFs outside the configured tree must not collide by basename.
    identity = hashlib.sha256(str(source).encode()).hexdigest()[:12]
    return Path(f"{source.stem}_{identity}.json")


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
    if args.command == "canonical-compile":
        return command_canonical_compile(args)
    if args.command == "storage-init":
        return command_storage_init(args)
    if args.command == "storage-load":
        return command_storage_load(args)
    if args.command == "storage-load-batch":
        return command_storage_load_batch(args)
    if args.command == "quality-audit":
        return command_quality_audit(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
