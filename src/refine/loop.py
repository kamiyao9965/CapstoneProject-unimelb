from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.extract.analyze import analyze, build_feedback, load_field_specs, load_records, print_report
from src.extract.extractor import SchemaExtractor
from src.refine.consensus import SchemaConsensusRefinement
from src.schema.discovery import SchemaDiscovery
from src.schema.sampler import DEFAULT_CATEGORIES, select_samples


def generate_schema(args, feedback: str | None, out_path: Path) -> str:
    """generate step: discovery over the sampling set, optionally with feedback."""
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
    """consensus step: N patch runs against the draft, frequency voting, merge.

    Stabilizes schema *fields* before extraction evaluation. Patch runs do not
    receive the extraction feedback again - the draft already incorporates it.
    Writes candidate patches, auto-merge reference schema, patch stability and
    the human review queue under round_dir/consensus/.
    """
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
    """extract + find-failures steps on a HOLDOUT set (different seed)."""
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


def next_round_index(out_dir: Path) -> int:
    """First round_N directory that does not exist yet, so resuming a
    human-in-the-loop session creates round_2, round_3, ... instead of
    overwriting round_1."""
    index = 1
    while (out_dir / f"round_{index}").exists():
        index += 1
    return index


def run_round(args, round_index: int, feedback_in: str | None) -> str | None:
    """One loop round. Returns feedback text, or None when the round stopped
    for human review (attended mode) and evaluation has not run yet."""
    round_dir = Path(args.out_dir) / f"round_{round_index}"
    round_dir.mkdir(parents=True, exist_ok=True)
    # schema.yaml is always the schema this round was evaluated on; with
    # consensus enabled the single-generation draft is kept separately.
    schema_path = round_dir / "schema.yaml"
    with_consensus = args.consensus_runs > 1
    draft_path = round_dir / "schema_draft.yaml" if with_consensus else schema_path

    print(f"\n========== ROUND {round_index} ==========")
    print("[generate] discovering schema" + (" with feedback" if feedback_in else ""))
    schema_text = generate_schema(args, feedback_in, draft_path)
    print(f"[generate] wrote {draft_path}")

    if with_consensus:
        print(f"[consensus] voting over {args.consensus_runs} patch runs")
        outputs = run_consensus_stage(args, draft_path, round_dir)

        if args.review_ui:
            # ATTENDED: stop before evaluation; a human reviews the queue,
            # applies decisions, then resumes. schema.yaml is deliberately not
            # written yet - it must be the schema that gets evaluated.
            consensus_dir = round_dir / "consensus"
            print(
                "\n--- Human review stop ---\n"
                f"Review queue: {outputs.queue_path}\n"
                "1. Review proposals:\n"
                f"     streamlit run src/review_app.py -- --consensus-dir {consensus_dir}\n"
                "2. Apply your decisions (also available from the UI):\n"
                f"     python src/refine/review.py apply --consensus-dir {consensus_dir}\n"
                "3. Evaluate the reviewed schema on the holdout set:\n"
                f"     python src/refine/loop.py --resume-review {round_dir}"
            )
            return None

        # UNATTENDED: threshold auto-merge, evaluate the consensus schema.
        schema_text = outputs.consensus_schema_path.read_text(encoding="utf-8")
        schema_path.write_text(schema_text, encoding="utf-8")
        print(f"[consensus] wrote {schema_path}")

    print("[extract + analyze] evaluating schema on holdout PDFs")
    _analysis, feedback_out = evaluate_schema(args, schema_text, round_dir)
    print("\n[find-failures] refinement feedback:\n" + feedback_out)
    return feedback_out


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Schema refinement loop: generate -> extract -> analyze -> review -> update"
    )
    parser.add_argument("--input-root", default="data/private_health/raw/PDFs")
    parser.add_argument("--per-category", type=int, default=5, help="PDFs/category for discovery")
    parser.add_argument("--seed", type=int, default=42, help="Discovery sampling seed")
    parser.add_argument("--eval-per-category", type=int, default=2, help="PDFs/category for evaluation")
    parser.add_argument("--eval-seed", type=int, default=7,
                        help="Evaluation sampling seed - keep different from --seed (holdout)")
    parser.add_argument("--model", default="gpt-5")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--out-dir", default="outputs/private_health/refine")
    parser.add_argument("--rounds", type=int, default=1, help="Max rounds")
    parser.add_argument("--consensus-runs", type=int, default=1,
                        help="Patch-voting runs per round before evaluation. 1 (default) keeps "
                             "single-generation behavior; N>1 multiplies API cost by ~N.")
    parser.add_argument("--review-ui", action="store_true",
                        help="With --consensus-runs N>1: stop the round after writing the "
                             "review queue so a human can accept/reject/edit proposals. "
                             "Evaluation then runs via --resume-review.")
    parser.add_argument("--resume-review",
                        help="Path to a round_N directory whose consensus/reviewed_schema.yaml "
                             "should be evaluated on the holdout set")
    parser.add_argument("--autonomous", action="store_true",
                        help="Feed failures back and re-generate automatically. "
                             "Default is human-in-the-loop: run one round then stop for review.")
    parser.add_argument("--resume-feedback",
                        help="Path to a (human-edited) feedback file to seed round 1")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.review_ui and args.autonomous:
        parser.error("--review-ui needs a human in the loop; drop --autonomous.")
    if args.review_ui and args.consensus_runs <= 1:
        parser.error("--review-ui requires --consensus-runs N greater than 1.")

    if args.resume_review:
        return resume_review(args)

    Path(args.out_dir).mkdir(parents=True, exist_ok=True)

    out_dir = Path(args.out_dir)
    start_index = next_round_index(out_dir)

    feedback: str | None = None
    if args.resume_feedback:
        feedback = Path(args.resume_feedback).read_text(encoding="utf-8").strip()
        print(f"Seeding round {start_index} with feedback from {args.resume_feedback}")

    if not args.autonomous:
        # HUMAN-IN-THE-LOOP: one round, then hand control back. The round index
        # auto-increments so each resume keeps its own round_N directory.
        feedback_out = run_round(args, start_index, feedback)
        if feedback_out is None:
            return 0  # attended stop; review instructions already printed
        print(
            "\n--- Human-in-the-loop stop ---\n"
            "Review the schema and feedback under "
            f"{args.out_dir}/round_{start_index}/. Edit feedback.txt if needed, then re-run with:\n"
            f"  python src/refine/loop.py --resume-feedback {args.out_dir}/round_{start_index}/feedback.txt\n"
            "or pass --autonomous to let the loop iterate on its own."
        )
        return 0

    # AUTONOMOUS: iterate, feeding each round's failures into the next generate.
    for offset in range(args.rounds):
        feedback = run_round(args, start_index + offset, feedback)
    print(f"\nCompleted {args.rounds} autonomous round(s). Outputs under {args.out_dir}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
