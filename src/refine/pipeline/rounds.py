"""Round-level orchestration for the schema refinement loop."""

from __future__ import annotations

from pathlib import Path

from src.common.json_artifacts import read_artifact, write_artifact
from src.refine.human_review import QUEUE_FILENAME, load_review_queue
from src.refine.pipeline.steps import (
    evaluate_schema,
    generate_schema,
    run_consensus_stage,
    select_discovery_samples,
)


def next_round_index(out_dir: Path) -> int:
    """Return the first round_N directory that does not exist yet."""
    index = 1
    while (out_dir / f"round_{index}").exists():
        index += 1
    return index


def run_round(args, round_index: int, feedback_in: str | None) -> str | None:
    """Run one generate/consensus/evaluate round.

    Returns feedback text when evaluation runs, or None when attended review
    pauses the round before evaluation.
    """
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
        schema_data, schema_build_samples = _run_consensus_or_pause(
            args,
            draft_path,
            round_dir,
            schema_path,
            schema_build_samples,
        )
        if schema_data is None:
            return None

    print("[extract + analyze] evaluating schema on holdout PDFs")
    _analysis, feedback_out = evaluate_schema(
        args,
        schema_data,
        round_dir,
        exclude_paths=schema_build_samples,
    )
    print("\n[find-failures] refinement feedback:\n" + feedback_out)
    return feedback_out


def _run_consensus_or_pause(
    args,
    draft_path: Path,
    round_dir: Path,
    schema_path: Path,
    base_sample_paths: tuple[str, ...],
) -> tuple[dict[str, object] | None, tuple[str, ...]]:
    print(f"[consensus] voting over {args.consensus_runs} patch runs")
    outputs = run_consensus_stage(
        args,
        draft_path,
        round_dir,
        base_sample_paths=base_sample_paths,
    )

    if args.review_ui:
        _print_review_stop(round_dir, outputs.queue_path)
        return None, outputs.schema_build_samples

    artifact = read_artifact(
        outputs.consensus_schema_path,
        expected_type="discovered_schema",
        data_contract="private_health/discovered_schema",
    )
    write_artifact(
        schema_path,
        artifact,
        data_contract="private_health/discovered_schema",
    )
    print(f"[consensus] wrote {schema_path}")
    return artifact["data"], outputs.schema_build_samples


def _print_review_stop(round_dir: Path, queue_path: Path) -> None:
    consensus_dir = round_dir / "consensus"
    print(
        "\n--- Human review stop ---\n"
        f"Review queue: {queue_path}\n"
        "1. Review proposals:\n"
        f"     streamlit run src/review_app.py -- --consensus-dir {consensus_dir}\n"
        "2. Apply your decisions (also available from the UI):\n"
        f"     python src/refine/review.py apply --consensus-dir {consensus_dir}\n"
        "3. Evaluate the reviewed schema on the holdout set:\n"
        f"     python src/refine/loop.py --resume-review {round_dir}"
    )


def resume_review(args) -> int:
    """Evaluate a human-reviewed schema in its original round directory."""
    round_dir = Path(args.resume_review)
    reviewed_path = round_dir / "consensus" / "reviewed_schema.json"
    if not reviewed_path.exists():
        print(
            f"{reviewed_path} not found. Apply your review decisions first:\n"
            f"  python src/refine/review.py apply --consensus-dir {round_dir / 'consensus'}"
        )
        return 1

    queue_path = round_dir / "consensus" / QUEUE_FILENAME
    if not queue_path.exists():
        print(f"{queue_path} not found; cannot verify the holdout sample split.")
        return 1
    queue = load_review_queue(queue_path)
    samples = queue.get("metadata", {}).get("schema_build_samples", [])
    if not isinstance(samples, list) or not samples:
        print(
            f"{queue_path} has no schema_build_samples metadata; rerun the "
            "consensus round before evaluating this review."
        )
        return 1
    schema_build_samples = tuple(str(path) for path in samples)

    artifact = read_artifact(
        reviewed_path,
        expected_type="discovered_schema",
        data_contract="private_health/discovered_schema",
    )
    schema_path = round_dir / "schema.json"
    write_artifact(
        schema_path, artifact, data_contract="private_health/discovered_schema"
    )
    print(f"[resume-review] evaluating {reviewed_path} on holdout PDFs")

    _analysis, feedback = evaluate_schema(
        args,
        artifact["data"],
        round_dir,
        exclude_paths=schema_build_samples,
    )
    print("\n[find-failures] refinement feedback:\n" + feedback)
    print(
        "\nTo feed this into the next round:\n"
        f"  python src/refine/loop.py --resume-feedback {round_dir / 'refinement_feedback.json'}"
    )
    return 0
