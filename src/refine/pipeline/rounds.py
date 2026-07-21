"""Round-level orchestration for the schema refinement loop."""

from __future__ import annotations

from pathlib import Path

from src.common.json_artifacts import read_artifact, write_artifact
from src.refine.human_review import QUEUE_FILENAME
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
        schema_data, schema_build_samples = _run_consensus_stage(
            args,
            draft_path,
            round_dir,
            schema_path,
            schema_build_samples,
        )

    print("[extract + analyze] evaluating schema on holdout PDFs")
    _analysis, feedback_out = evaluate_schema(
        args,
        schema_data,
        round_dir,
        exclude_paths=schema_build_samples,
    )
    print("\n[find-failures] refinement feedback:\n" + feedback_out)
    if with_consensus and args.review_ui:
        _print_review_stop(round_dir, round_dir / "consensus" / QUEUE_FILENAME)
        return None

    final_schema_path = publish_final_schema(schema_path, Path(args.out_dir))
    print(f"[final-schema] wrote {final_schema_path}")
    return feedback_out


def publish_final_schema(schema_path: Path, out_dir: Path) -> Path:
    """Publish the latest completed round schema to a stable path for extraction."""
    artifact = read_artifact(
        schema_path,
        expected_type="discovered_schema",
        data_contract="private_health/discovered_schema",
    )
    final_schema_path = out_dir / "final_schema.json"
    write_artifact(
        final_schema_path,
        artifact,
        data_contract="private_health/discovered_schema",
    )
    return final_schema_path


def _run_consensus_stage(
    args,
    draft_path: Path,
    round_dir: Path,
    schema_path: Path,
    base_sample_paths: tuple[str, ...],
) -> tuple[dict[str, object], tuple[str, ...]]:
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
        data_contract="private_health/discovered_schema",
    )
    write_artifact(
        schema_path,
        artifact,
        data_contract="private_health/discovered_schema",
    )
    suffix = " for pre-review extraction analysis" if args.review_ui else ""
    print(f"[consensus] wrote {schema_path}{suffix}")
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
        "3. Publish the reviewed schema as the final schema:\n"
        f"     python src/refine/loop.py --resume-review {round_dir}"
    )


def resume_review(args) -> int:
    """Publish a human-reviewed schema in its original round directory."""
    round_dir = Path(args.resume_review)
    reviewed_path = round_dir / "consensus" / "reviewed_schema.json"
    if not reviewed_path.exists():
        print(
            f"{reviewed_path} not found. Apply your review decisions first:\n"
            f"  python src/refine/review.py apply --consensus-dir {round_dir / 'consensus'}"
        )
        return 1

    artifact = read_artifact(
        reviewed_path,
        expected_type="discovered_schema",
        data_contract="private_health/discovered_schema",
    )
    schema_path = round_dir / "schema.json"
    write_artifact(
        schema_path, artifact, data_contract="private_health/discovered_schema"
    )
    print(f"[resume-review] wrote reviewed schema to {schema_path}")
    final_schema_path = publish_final_schema(schema_path, Path(args.out_dir))
    print(f"[final-schema] wrote {final_schema_path}")
    print(
        "\nPre-review extraction feedback is available at:\n"
        f"  {round_dir / 'refinement_feedback.json'}\n"
        "To feed it into the next round:\n"
        f"  python src/refine/loop.py --resume-feedback {round_dir / 'refinement_feedback.json'}"
    )
    return 0
