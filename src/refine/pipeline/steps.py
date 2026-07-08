"""API-backed steps used by the refinement loop."""

from __future__ import annotations

from pathlib import Path

from src.extract.analyze import (
    analyze,
    build_feedback,
    load_field_specs,
    load_records,
    print_report,
)
from src.extract.extractor import SchemaExtractor
from src.refine.consensus import SchemaConsensusRefinement
from src.schema.discovery import SchemaDiscovery
from src.schema.sampler import DEFAULT_CATEGORIES, select_samples


def generate_schema(args, feedback: str | None, out_path: Path) -> str:
    """Generate a schema over the discovery sample, optionally with feedback."""
    sample_paths = select_samples(
        input_root=Path(args.input_root),
        categories=DEFAULT_CATEGORIES,
        per_category=args.per_category,
        seed=args.seed,
    )
    schema_yaml = SchemaDiscovery(
        model=args.model,
        timeout_seconds=args.timeout,
        usage_log_path=str(Path(args.out_dir) / "token_usage.jsonl"),
        extra_instructions=feedback,
    ).discover(sample_paths, output_path=out_path)
    out_path.write_text(schema_yaml, encoding="utf-8")
    return schema_yaml


def run_consensus_stage(args, draft_schema_path: Path, round_dir: Path):
    """Run N patch generations against a draft and render consensus artifacts."""
    return SchemaConsensusRefinement(
        discovery=SchemaDiscovery(
            model=args.model,
            timeout_seconds=args.timeout,
            usage_log_path=str(Path(args.out_dir) / "token_usage.jsonl"),
        ),
    ).refine(
        base_schema_path=draft_schema_path,
        input_root=args.input_root,
        per_category=args.per_category,
        runs=args.consensus_runs,
        seed=args.seed,
        output_dir=round_dir / "consensus",
    )


def evaluate_schema(args, schema_text: str, round_dir: Path):
    """Extract holdout PDFs, analyze failures, and write feedback.txt."""
    eval_paths = select_samples(
        input_root=Path(args.input_root),
        categories=DEFAULT_CATEGORIES,
        per_category=args.eval_per_category,
        seed=args.eval_seed,
    )
    extractions_dir = round_dir / "extractions"
    SchemaExtractor(
        schema_text=schema_text,
        model=args.model,
        timeout_seconds=args.timeout,
        usage_log_path=str(round_dir / "extraction_usage.jsonl"),
    ).extract_many(eval_paths, extractions_dir)

    specs = load_field_specs(schema_text)
    records = load_records(extractions_dir)
    analysis = analyze(records, specs)
    print_report(analysis)
    feedback = build_feedback(analysis)
    (round_dir / "feedback.txt").write_text(feedback + "\n", encoding="utf-8")
    return analysis, feedback
