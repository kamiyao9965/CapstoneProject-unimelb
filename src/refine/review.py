"""CLI wrapper for applying human review decisions."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.refine.human_review import (
    DECISIONS_FILENAME,
    REVIEWED_SCHEMA_FILENAME,
    apply_review_files,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply human review decisions to produce reviewed_schema.json"
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    apply_command = subcommands.add_parser(
        "apply", help="Apply review_decisions.json to the queue"
    )
    apply_command.add_argument(
        "--consensus-dir",
        required=True,
        help=f"Directory containing review_queue.json and {DECISIONS_FILENAME}",
    )
    apply_command.add_argument(
        "--base-schema",
        help="Base schema JSON artifact (default: base_schema_path from queue metadata)",
    )
    apply_command.add_argument(
        "--out",
        help=f"Output path (default: <consensus-dir>/{REVIEWED_SCHEMA_FILENAME})",
    )
    return parser


def run_apply(args: argparse.Namespace) -> int:
    try:
        output_path, summary = apply_review_files(
            consensus_dir=args.consensus_dir,
            base_schema_path=args.base_schema,
            output_path=args.out,
        )
    except FileNotFoundError as exc:
        print(exc)
        return 1

    print(f"Wrote {output_path}")
    print(
        f"applied={len(summary['applied'])} edited={len(summary['edited'])} "
        f"rejected={len(summary['rejected'])} pending={len(summary['pending'])}"
    )
    if summary["pending"]:
        print("PENDING (not applied): " + ", ".join(summary["pending"]))
    return 0


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "apply":
        return run_apply(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
