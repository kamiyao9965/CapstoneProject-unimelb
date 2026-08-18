"""CLI parser and mode dispatch for the refinement loop."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.common.json_artifacts import read_artifact
from src.common.json_codec import dumps_json
from src.common.model_config import resolve_selection
from src.refine.pipeline.rounds import next_round_index, resume_review, run_round
from src.verticals.manifest import (
    ManifestValidationError,
    VerticalManifest,
    default_manifest_path,
    load_vertical_manifest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Schema generation loop: PDF samples -> discovery -> optional consensus "
            "-> optional human review -> holdout schema application -> feedback"
        )
    )
    parser.add_argument("--manifest")
    parser.add_argument("--input-root")
    parser.add_argument(
        "--per-category", type=int, default=5, help="PDFs/category for discovery"
    )
    parser.add_argument("--seed", type=int, default=42, help="Discovery sampling seed")
    parser.add_argument(
        "--eval-per-category",
        type=int,
        default=2,
        help="PDFs/category for evaluation",
    )
    parser.add_argument(
        "--eval-seed",
        type=int,
        default=7,
        help="Evaluation sampling seed - keep different from --seed (holdout)",
    )
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--document-input")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--out-dir")
    parser.add_argument("--rounds", type=int, default=1, help="Max rounds")
    parser.add_argument(
        "--consensus-runs",
        type=int,
        default=1,
        help=(
            "Patch-voting runs per round before evaluation. 1 (default) keeps "
            "single-generation behavior; N>1 multiplies API cost by ~N."
        ),
    )
    parser.add_argument(
        "--review-ui",
        action="store_true",
        help=(
            "With --consensus-runs N>1: stop the round after writing the "
            "review queue so a human can accept/reject/edit proposals. "
            "Holdout extraction and failure discovery then run via --resume-review."
        ),
    )
    parser.add_argument(
        "--resume-review",
        help=(
            "Path to a round_N directory whose consensus/reviewed_schema.json "
            "should be applied to holdout PDFs before publishing final_schema.json"
        ),
    )
    parser.add_argument(
        "--autonomous",
        action="store_true",
        help=(
            "Feed failures back and re-generate automatically. "
            "Default is human-in-the-loop: run one round then stop for review."
        ),
    )
    parser.add_argument(
        "--resume-feedback",
        help="Path to a validated feedback JSON artifact to seed round 1",
    )
    return parser


def configure_args(args: argparse.Namespace) -> VerticalManifest:
    manifest = load_vertical_manifest(
        args.manifest or default_manifest_path("private_health")
    )
    manifest.require_capability("refinement")
    args.vertical_manifest = manifest
    args.input_root = (
        Path(args.input_root) if args.input_root else manifest.path("input_root")
    )
    args.out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else manifest.path("output_root") / "refine"
    )
    return manifest


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        configure_args(args)
    except ManifestValidationError as exc:
        parser.error(str(exc))
    try:
        args.selection = resolve_selection(
            provider=args.provider,
            model=args.model,
            document_input=args.document_input,
        )
    except ValueError as exc:
        parser.error(str(exc))

    if args.review_ui and args.autonomous:
        parser.error("--review-ui needs a human in the loop; drop --autonomous.")
    if args.review_ui and args.consensus_runs <= 1:
        parser.error("--review-ui requires --consensus-runs N greater than 1.")

    if args.resume_review:
        return resume_review(args)

    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    return _run_from_start(args)


def _run_from_start(args) -> int:
    out_dir = Path(args.out_dir)
    start_index = next_round_index(out_dir)
    feedback = _load_feedback(args, start_index)

    if not args.autonomous:
        feedback_out = run_round(args, start_index, feedback)
        if feedback_out is None:
            return 0
        _print_human_loop_stop(args, start_index)
        return 0

    for offset in range(args.rounds):
        feedback = run_round(args, start_index + offset, feedback)
    print(f"\nCompleted {args.rounds} autonomous round(s). Outputs under {args.out_dir}.")
    return 0


def _load_feedback(args, start_index: int) -> str | None:
    if not args.resume_feedback:
        return None
    artifact = read_artifact(
        args.resume_feedback,
        expected_type="refinement_feedback",
        data_contract=args.vertical_manifest.contract("refinement_feedback"),
    )
    feedback = dumps_json(artifact["data"], ensure_ascii=False)
    print(f"Seeding round {start_index} with feedback from {args.resume_feedback}")
    return feedback


def _print_human_loop_stop(args, round_index: int) -> None:
    print(
        "\n--- Human-in-the-loop stop ---\n"
        "Review the schema and feedback under "
        f"{args.out_dir}/round_{round_index}/. Review refinement_feedback.json, then re-run with:\n"
        f"  python src/refine/loop.py --resume-feedback {args.out_dir}/round_{round_index}/refinement_feedback.json\n"
        "or pass --autonomous to let the loop iterate on its own."
    )
