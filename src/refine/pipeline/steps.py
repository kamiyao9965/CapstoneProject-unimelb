"""API-backed steps used by schema generation and refinement."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable
from uuid import uuid4

from src.common.json_artifacts import build_success_artifact, write_artifact
from src.common.model_config import ModelSelection, resolve_selection
from src.schema_application.analyze import (
    analyze,
    build_feedback,
    build_feedback_data,
    load_field_specs,
    load_records,
    print_report,
)
from src.schema_application.extractor import SchemaExtractor
from src.schema.contract import compile_extraction_contract
from src.refine.consensus import SchemaConsensusRefinement
from src.schema.discovery import SchemaDiscovery
from src.schema.sampler import (
    DEFAULT_CATEGORIES,
    DEFAULT_MANIFEST_ROLES,
    select_manifest_samples,
    select_samples,
)
from src.refine.verticals import contract_name


def select_discovery_samples(args) -> tuple[str, ...]:
    manifest_path = getattr(args, "manifest", None)
    if manifest_path:
        return tuple(
            select_manifest_samples(
                input_root=Path(args.input_root),
                manifest_path=manifest_path,
                count=args.per_category,
                seed=args.seed,
                roles=tuple(getattr(args, "document_roles", DEFAULT_MANIFEST_ROLES)),
            )
        )
    return tuple(
        select_samples(
            input_root=Path(args.input_root),
            categories=DEFAULT_CATEGORIES,
            per_category=args.per_category,
            seed=args.seed,
        )
    )


def _selection(args) -> ModelSelection:
    existing = getattr(args, "selection", None)
    if existing is not None:
        return existing
    return resolve_selection(
        provider=getattr(args, "provider", None),
        model=getattr(args, "model", None),
        document_input=getattr(args, "document_input", None),
    )


def generate_schema(
    args,
    feedback: str | None,
    out_path: Path,
    sample_paths: Iterable[str | Path] | None = None,
) -> dict[str, object]:
    """Generate a schema over the discovery sample, optionally with feedback."""
    if sample_paths is None:
        sample_paths = select_discovery_samples(args)
    resolved_sample_paths = [str(path) for path in sample_paths]
    selection = _selection(args)
    run_id = uuid4().hex
    schema_data = SchemaDiscovery(
        selection=selection,
        timeout_seconds=args.timeout,
        usage_log_path=str(Path(args.out_dir) / "token_usage.jsonl"),
        extra_instructions=feedback,
        pdf_root=Path(args.input_root),
        vertical=getattr(args, "vertical", "private_health"),
    ).discover(resolved_sample_paths, output_path=out_path, run_id=run_id)
    artifact = build_success_artifact(
        artifact_type="discovered_schema",
        contract_version="1.0.0",
        data=schema_data,
        provenance={
            "run_id": run_id, "provider": selection.provider,
            "model": selection.model, "document_input": selection.document_input,
            "source_documents": resolved_sample_paths, "source_artifacts": [],
        },
        data_contract=contract_name(getattr(args, "vertical", "private_health"), "discovered_schema"),
    )
    write_artifact(
        out_path,
        artifact,
        data_contract=contract_name(getattr(args, "vertical", "private_health"), "discovered_schema"),
    )
    return schema_data


def run_consensus_stage(
    args,
    draft_schema_path: Path,
    round_dir: Path,
    base_sample_paths: Iterable[str | Path] = (),
):
    """Run N patch generations against a draft and render consensus artifacts."""
    return SchemaConsensusRefinement(
        discovery=SchemaDiscovery(
            selection=_selection(args),
            timeout_seconds=args.timeout,
            usage_log_path=str(Path(args.out_dir) / "token_usage.jsonl"),
            pdf_root=Path(args.input_root),
            vertical=getattr(args, "vertical", "private_health"),
        ),
        ).refine(
            base_schema_path=draft_schema_path,
            input_root=args.input_root,
            per_category=args.per_category,
            runs=args.consensus_runs,
            seed=args.seed,
            base_sample_paths=base_sample_paths,
            output_dir=round_dir / "consensus",
            manifest_path=getattr(args, "manifest", None),
            document_roles=tuple(getattr(args, "document_roles", DEFAULT_MANIFEST_ROLES)),
        )


def evaluate_schema(
    args,
    schema_data: dict[str, object],
    round_dir: Path,
    exclude_paths: Iterable[str | Path] = (),
    *,
    overwrite_feedback: bool = False,
):
    """Apply a schema to holdout PDFs, find failures, and write feedback."""
    manifest_path = getattr(args, "manifest", None)
    if manifest_path:
        eval_paths = select_manifest_samples(
            input_root=Path(args.input_root),
            manifest_path=manifest_path,
            count=args.eval_per_category,
            seed=args.eval_seed,
            exclude_paths=exclude_paths,
            roles=tuple(getattr(args, "document_roles", DEFAULT_MANIFEST_ROLES)),
        )
    else:
        eval_paths = select_samples(
            input_root=Path(args.input_root),
            categories=DEFAULT_CATEGORIES,
            per_category=args.eval_per_category,
            seed=args.eval_seed,
            exclude_paths=exclude_paths,
        )
    extractions_dir = round_dir / "extractions"
    extractor = SchemaExtractor(
        schema_data=schema_data,
        selection=_selection(args),
        timeout_seconds=args.timeout,
        usage_log_path=str(round_dir / "extraction_usage.jsonl"),
        pdf_root=Path(args.input_root),
        manifest_path=manifest_path,
    )
    extractor.extract_many(eval_paths, extractions_dir)

    specs = load_field_specs(schema_data)
    vertical = getattr(args, "vertical", "private_health")
    records, failed_artifacts = load_records(
        extractions_dir,
        compile_extraction_contract(schema_data),
        vertical=vertical,
        current_schema_hash=extractor.schema_hash,
    )
    analysis = analyze(
        records, specs, failed_artifacts=failed_artifacts, vertical=vertical
    )
    print_report(analysis)
    feedback = build_feedback(analysis)
    feedback_data = build_feedback_data(analysis)
    feedback_artifact = build_success_artifact(
        artifact_type="refinement_feedback",
        contract_version="1.0.0",
        data=feedback_data,
        provenance={
            "run_id": None, "provider": None, "model": None,
            "document_input": None, "source_documents": list(eval_paths),
            "source_artifacts": [],
        },
        data_contract=contract_name(vertical, "refinement_feedback"),
    )
    write_artifact(
        round_dir / "refinement_feedback.json",
        feedback_artifact,
        data_contract=contract_name(vertical, "refinement_feedback"),
        overwrite=overwrite_feedback,
    )
    return analysis, feedback
