"""Compatibility entry point for the refinement loop CLI."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.refine.pipeline import (
    build_parser,
    evaluate_schema,
    generate_schema,
    main,
    next_round_index,
    publish_final_schema,
    resume_extraction,
    resume_review,
    run_consensus_stage,
    run_round,
)

__all__ = [
    "build_parser",
    "evaluate_schema",
    "generate_schema",
    "main",
    "next_round_index",
    "publish_final_schema",
    "resume_extraction",
    "resume_review",
    "run_consensus_stage",
    "run_round",
]


if __name__ == "__main__":
    raise SystemExit(main())
