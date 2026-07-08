"""Round-level orchestration for the schema refinement loop."""

from __future__ import annotations

from pathlib import Path

from src.refine.pipeline.steps import (
    evaluate_schema,
    generate_schema,
    run_consensus_stage,
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
    schema_path = round_dir / "schema.yaml"
    with_consensus = args.consensus_runs > 1
    draft_path = round_dir / "schema_draft.yaml" if with_consensus else schema_path

    print(f"\n========== ROUND {round_index} ==========")
    print("[generate] discovering schema" + (" with feedback" if feedback_in else ""))
    schema_text = generate_schema(args, feedback_in, draft_path)
    print(f"[generate] wrote {draft_path}")

    if with_consensus:
        schema_text = _run_consensus_or_pause(args, draft_path, round_dir, schema_path)
        if schema_text is None:
            return None

    print("[extract + analyze] evaluating schema on holdout PDFs")
    _analysis, feedback_out = evaluate_schema(args, schema_text, round_dir)
    print("\n[find-failures] refinement feedback:\n" + feedback_out)
    return feedback_out


def _run_consensus_or_pause(
    args,
    draft_path: Path,
    round_dir: Path,
    schema_path: Path,
) -> str | None:
    print(f"[consensus] voting over {args.consensus_runs} patch runs")
    outputs = run_consensus_stage(args, draft_path, round_dir)

    if args.review_ui:
        _print_review_stop(round_dir, outputs.queue_path)
        return None

    schema_text = outputs.consensus_schema_path.read_text(encoding="utf-8")
    schema_path.write_text(schema_text, encoding="utf-8")
    print(f"[consensus] wrote {schema_path}")
    return schema_text


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
    reviewed_path = round_dir / "consensus" / "reviewed_schema.yaml"
    if not reviewed_path.exists():
        print(
            f"{reviewed_path} not found. Apply your review decisions first:\n"
            f"  python src/refine/review.py apply --consensus-dir {round_dir / 'consensus'}"
        )
        return 1

    schema_text = reviewed_path.read_text(encoding="utf-8")
    schema_path = round_dir / "schema.yaml"
    schema_path.write_text(schema_text, encoding="utf-8")
    print(f"[resume-review] evaluating {reviewed_path} on holdout PDFs")

    _analysis, feedback = evaluate_schema(args, schema_text, round_dir)
    print("\n[find-failures] refinement feedback:\n" + feedback)
    print(
        "\nTo feed this into the next round:\n"
        f"  python src/refine/loop.py --resume-feedback {round_dir / 'feedback.txt'}"
    )
    return 0
