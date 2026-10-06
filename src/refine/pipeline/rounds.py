"""Round-level orchestration for schema generation and refinement."""

from __future__ import annotations

from pathlib import Path

from src.common.json_artifacts import read_artifact, write_artifact
from src.schema.migration import migrate_legacy_discovered_schema
from src.refine.human_review import QUEUE_FILENAME, load_review_queue
from src.refine.pipeline.steps import (
    evaluate_schema,
    generate_schema,
    run_consensus_stage,
    select_discovery_samples,
)
from src.refine.verticals import contract_name


def next_round_index(out_dir: Path) -> int:
    """Return the first round_N directory that does not exist yet."""
    index = 1
    while (out_dir / f"round_{index}").exists():
        index += 1
    return index


def run_round(args, round_index: int, feedback_in: str | None) -> str | None:
    """Run one schema-generation round.

    Order:
      PDF samples -> schema discovery -> optional consensus
      -> optional human review stop -> holdout schema application
      -> failure discovery -> refinement feedback -> final_schema.json.

    Returns feedback text when holdout evaluation runs, or None when attended
    review pauses the round before holdout extraction.
    """
    vertical = getattr(args, "vertical", "private_health")
    round_dir = Path(args.out_dir) / f"round_{round_index}"
    round_dir.mkdir(parents=True, exist_ok=True)
    schema_path = round_dir / "schema.json"
    with_consensus = args.consensus_runs > 1
    draft_path = round_dir / "schema_draft.json" if with_consensus else schema_path

    print(f"\n========== ROUND {round_index} ==========")
    print("[generate] discovering schema" + (" with feedback" if feedback_in else ""))
    schema_build_samples = select_discovery_samples(args)
    schema_data = generate_schema(
        args,
        feedback_in,
        draft_path,
        sample_paths=schema_build_samples,
    )
    print(f"[generate] wrote {draft_path}")

    if with_consensus:
        schema_data, schema_build_samples = _run_consensus_stage(
            args,
            draft_path,
            round_dir,
            schema_path,
            schema_build_samples,
        )

    if with_consensus and args.review_ui:
        _print_review_stop(
            round_dir,
            round_dir / "consensus" / QUEUE_FILENAME,
            vertical=vertical,
        )
        return None

    print("[schema-application] extracting holdout PDFs and discovering failures")
    _analysis, feedback_out = evaluate_schema(
        args,
        schema_data,
        round_dir,
        exclude_paths=schema_build_samples,
    )
    print("\n[refinement-feedback]\n" + feedback_out)

    final_schema_path = publish_final_schema(
        schema_path, Path(args.out_dir), vertical=vertical
    )
    print(f"[final-schema] wrote {final_schema_path}")
    return feedback_out


def publish_final_schema(
    schema_path: Path, out_dir: Path, *, vertical: str = "private_health"
) -> Path:
    """Atomically replace the stable schema with the latest completed round."""
    artifact = read_artifact(
        schema_path,
        expected_type="discovered_schema",
        data_contract=contract_name(vertical, "discovered_schema"),
    )
    migrated_data = (
        migrate_legacy_discovered_schema(artifact["data"])
        if vertical == "private_health" else artifact["data"]
    )
    if migrated_data != artifact["data"]:
        artifact = dict(artifact)
        artifact["data"] = migrated_data
        provenance = dict(artifact.get("provenance") or {})
        provenance["source_artifacts"] = [
            *(provenance.get("source_artifacts") or []),
            "schema_migration:canonical-item-policy-v1",
        ]
        artifact["provenance"] = provenance
    final_schema_path = out_dir / "final_schema.json"
    write_artifact(
        final_schema_path,
        artifact,
        data_contract=contract_name(vertical, "discovered_schema"),
        overwrite=True,
    )
    return final_schema_path


def _run_consensus_stage(
    args,
    draft_path: Path,
    round_dir: Path,
    schema_path: Path,
    base_sample_paths: tuple[str, ...],
) -> tuple[dict[str, object], tuple[str, ...]]:
    vertical = getattr(args, "vertical", "private_health")
    print(f"[consensus] voting over {args.consensus_runs} patch runs")
    outputs = run_consensus_stage(
        args,
        draft_path,
        round_dir,
        base_sample_paths=base_sample_paths,
    )

    artifact = read_artifact(
        outputs.consensus_schema_path,
        expected_type="discovered_schema",
        data_contract=contract_name(vertical, "discovered_schema"),
    )
    write_artifact(
        schema_path,
        artifact,
        data_contract=contract_name(vertical, "discovered_schema"),
    )
    suffix = " for human review" if args.review_ui else ""
    print(f"[consensus] wrote {schema_path}{suffix}")
    return artifact["data"], outputs.schema_build_samples


def _print_review_stop(
    round_dir: Path, queue_path: Path, *, vertical: str = "private_health"
) -> None:
    consensus_dir = round_dir / "consensus"
    print(
        "\n--- Human review stop ---\n"
        f"Review queue: {queue_path}\n"
        "1. Review proposals:\n"
        f"     streamlit run src/review_app.py -- --consensus-dir {consensus_dir} --vertical {vertical}\n"
        "2. Apply your decisions (also available from the UI):\n"
        f"     python src/refine/review.py apply --consensus-dir {consensus_dir} --vertical {vertical}\n"
        "3. Resume with the reviewed schema for holdout extraction, failure discovery, "
        "feedback, and final_schema.json:\n"
        f"     PYTHONPATH=. .venv/bin/python -m src.refine.loop "
        f"--vertical {vertical} --resume-review {round_dir}"
    )


def resume_review(args) -> int:
    """Evaluate a human-reviewed schema, write feedback, then publish final_schema.json."""
    vertical = getattr(args, "vertical", "private_health")
    round_dir = Path(args.resume_review)
    reviewed_path = round_dir / "consensus" / "reviewed_schema.json"
    if not reviewed_path.exists():
        print(
            f"{reviewed_path} not found. Apply your review decisions first:\n"
            f"  python src/refine/review.py apply --consensus-dir {round_dir / 'consensus'} --vertical {vertical}"
        )
        return 1

    artifact = read_artifact(
        reviewed_path,
        expected_type="discovered_schema",
        data_contract=contract_name(vertical, "discovered_schema"),
    )
    schema_path = round_dir / "schema.json"
    write_artifact(
        schema_path,
        artifact,
        data_contract=contract_name(vertical, "discovered_schema"),
        overwrite=True,
    )
    print(f"[resume-review] wrote reviewed schema to {schema_path}")
    schema_build_samples = _schema_build_samples_from_review_queue(
        round_dir / "consensus" / QUEUE_FILENAME, vertical=vertical
    )
    print("[schema-application] extracting holdout PDFs and discovering failures")
    _analysis, feedback_out = evaluate_schema(
        args,
        artifact["data"],
        round_dir,
        exclude_paths=schema_build_samples,
        overwrite_feedback=True,
    )
    print("\n[refinement-feedback]\n" + feedback_out)
    final_schema_path = publish_final_schema(
        schema_path, round_dir.parent, vertical=vertical
    )
    print(f"[final-schema] wrote {final_schema_path}")
    print(
        "\nReviewed-schema extraction feedback is available at:\n"
        f"  {round_dir / 'refinement_feedback.json'}\n"
        "To feed it into the next round:\n"
        f"  python src/refine/loop.py --resume-feedback {round_dir / 'refinement_feedback.json'}"
    )
    return 0


def resume_consensus(args) -> int:
    """Resume an interrupted consensus stage, then evaluate and publish the round."""
    vertical = getattr(args, "vertical", "private_health")
    round_dir = Path(args.resume_consensus)
    draft_path = round_dir / "schema_draft.json"
    schema_path = round_dir / "schema.json"
    if args.consensus_runs <= 1:
        print("--resume-consensus requires --consensus-runs N greater than 1.")
        return 1
    if not draft_path.exists():
        print(f"{draft_path} not found; the round has no schema draft to resume.")
        return 1
    if schema_path.exists():
        print(
            f"{schema_path} already exists; consensus is complete. "
            f"Use --resume-extraction {round_dir} instead."
        )
        return 1

    draft_artifact = read_artifact(
        draft_path,
        expected_type="discovered_schema",
        data_contract=contract_name(vertical, "discovered_schema"),
    )
    provenance = draft_artifact.get("provenance") or {}
    source_documents = (
        provenance.get("source_documents", [])
        if isinstance(provenance, dict)
        else []
    )
    base_sample_paths = tuple(
        str(path) for path in source_documents if isinstance(path, str)
    )

    print(
        f"[resume-consensus] resuming {round_dir} with "
        f"{args.consensus_runs} total patch runs"
    )
    schema_data, schema_build_samples = _run_consensus_stage(
        args,
        draft_path,
        round_dir,
        schema_path,
        base_sample_paths,
    )
    if args.review_ui:
        _print_review_stop(
            round_dir,
            round_dir / "consensus" / QUEUE_FILENAME,
            vertical=vertical,
        )
        return 0

    print("[schema-application] extracting holdout PDFs and discovering failures")
    _analysis, feedback_out = evaluate_schema(
        args,
        schema_data,
        round_dir,
        exclude_paths=schema_build_samples,
        overwrite_feedback=True,
    )
    print("\n[refinement-feedback]\n" + feedback_out)
    final_schema_path = publish_final_schema(
        schema_path, round_dir.parent, vertical=vertical
    )
    print(f"[final-schema] wrote {final_schema_path}")
    return 0


def resume_extraction(args) -> int:
    """Resume holdout extraction for a round, reusing validated PDF artifacts."""
    vertical = getattr(args, "vertical", "private_health")
    round_dir = Path(args.resume_extraction)
    schema_path = round_dir / "schema.json"
    if not schema_path.exists():
        print(f"{schema_path} not found; the round has no schema to evaluate.")
        return 1
    artifact = read_artifact(
        schema_path,
        expected_type="discovered_schema",
        data_contract=contract_name(vertical, "discovered_schema"),
    )
    schema_build_samples = _schema_build_samples_from_review_queue(
        round_dir / "consensus" / QUEUE_FILENAME, vertical=vertical
    )
    print("[resume-extraction] reusing successes and retrying unfinished holdout PDFs")
    _analysis, feedback_out = evaluate_schema(
        args,
        artifact["data"],
        round_dir,
        exclude_paths=schema_build_samples,
        overwrite_feedback=True,
    )
    print("\n[refinement-feedback]\n" + feedback_out)
    final_schema_path = publish_final_schema(
        schema_path, round_dir.parent, vertical=vertical
    )
    print(f"[final-schema] wrote {final_schema_path}")
    return 0


def _schema_build_samples_from_review_queue(
    queue_path: Path, *, vertical: str = "private_health"
) -> tuple[str, ...]:
    if not queue_path.exists():
        return tuple()
    queue = load_review_queue(queue_path, vertical=vertical)
    metadata = queue.get("metadata", {})
    if not isinstance(metadata, dict):
        return tuple()
    samples = metadata.get("schema_build_samples") or []
    if not isinstance(samples, list):
        return tuple()
    return tuple(str(path) for path in samples)
