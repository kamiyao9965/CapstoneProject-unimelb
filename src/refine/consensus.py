"""Field-level consensus refinement: multi-run patch voting over a schema.

Adapted from the `testing` branch (src/schema/refinement.py) per
docs/BRANCH_FUSION_PLAN.md. This stabilizes schema *fields* before extraction:

    base schema + N patch-generation runs
      -> normalize field/group names
      -> frequency voting (core/conditional/candidate/noise)
      -> consensus_schema.yaml + field_frequency.yaml + consensus_report.md

It complements (does not replace) the extraction-driven refinement in
src/refine/loop.py, which judges the schema on holdout extraction failures.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.refine.artifacts.renderer import (
    render_consensus_schema,
    render_frequency_yaml,
    render_report,
)
from src.refine.candidates.aggregator import FieldDecision, aggregate_patches
from src.refine.candidates.normalizer import load_alias_config, normalize_patches
from src.refine.candidates.patch import dump_yaml, load_patch_file, parse_yaml_text
from src.refine.candidates.stability import (
    compute_patch_stability,
    write_patch_stability,
)
from src.refine.human_review import build_review_queue, write_review_queue
from src.schema.discovery import SchemaDiscovery
from src.schema.sampler import DEFAULT_CATEGORIES, print_samples, select_samples


@dataclass(frozen=True)
class ConsensusOutputs:
    patch_dir: Path
    consensus_schema_path: Path
    frequency_path: Path
    report_path: Path
    stability_path: Path
    queue_path: Path
    decisions: list[FieldDecision]


class SchemaConsensusRefinement:
    def __init__(
        self,
        discovery: SchemaDiscovery,
        log: Callable[[str], None] | None = print,
    ) -> None:
        self.discovery = discovery
        self.log = log

    def refine(
        self,
        base_schema_path: str | Path,
        input_root: str | Path = "data/private_health/raw/PDFs",
        categories: tuple[str, ...] = DEFAULT_CATEGORIES,
        per_category: int = 5,
        runs: int = 10,
        seed: int | None = None,
        samples: list[str] | None = None,
        output_dir: str | Path = "outputs/private_health/consensus",
        alias_config_path: str | Path | None = None,
    ) -> ConsensusOutputs:
        if runs <= 0:
            raise ValueError("runs must be greater than 0.")

        base_schema = Path(base_schema_path)
        if not base_schema.exists():
            raise FileNotFoundError(base_schema)

        resolved_output_dir = Path(output_dir)
        patch_dir = resolved_output_dir / "candidate_patches"
        consensus_schema_path = resolved_output_dir / "consensus_schema.yaml"
        frequency_path = resolved_output_dir / "field_frequency.yaml"
        report_path = resolved_output_dir / "consensus_report.md"
        stability_path = resolved_output_dir / "patch_stability.yaml"
        queue_path = resolved_output_dir / "review_queue.yaml"

        field_aliases, group_aliases = load_alias_config(alias_config_path)
        current_schema = base_schema.read_text(encoding="utf-8")
        all_patches = []

        for run_number in range(1, runs + 1):
            run_seed = _run_seed(seed, run_number)
            self._log(f"Starting consensus run {run_number}/{runs}")
            sample_paths = samples or select_samples(
                input_root=Path(input_root),
                categories=categories,
                per_category=per_category,
                seed=run_seed,
            )
            if not samples:
                print_samples(sample_paths, Path(input_root), categories)

            patch_path = patch_dir / f"run_{run_number:03d}.yaml"
            patch_yaml = self.discovery.discover_patches(
                sample_pdfs=sample_paths,
                current_schema=current_schema,
                output_path=patch_path,
            )
            dump_yaml(parse_yaml_text(patch_yaml), patch_path)
            all_patches.extend(
                normalize_patches(load_patch_file(patch_path), field_aliases, group_aliases)
            )

        decisions = aggregate_patches(all_patches, total_runs=runs)
        render_frequency_yaml(decisions, frequency_path)
        render_consensus_schema(base_schema, decisions, consensus_schema_path)
        render_report(decisions, report_path)
        write_patch_stability(
            compute_patch_stability(all_patches, total_runs=runs), stability_path
        )
        write_review_queue(
            build_review_queue(
                decisions,
                parse_yaml_text(current_schema) or {},
                total_runs=runs,
                base_schema_path=base_schema,
            ),
            queue_path,
        )

        return ConsensusOutputs(
            patch_dir=patch_dir,
            consensus_schema_path=consensus_schema_path,
            frequency_path=frequency_path,
            report_path=report_path,
            stability_path=stability_path,
            queue_path=queue_path,
            decisions=decisions,
        )

    def _log(self, message: str) -> None:
        if self.log:
            self.log(message)


def _run_seed(seed: int | None, run_number: int) -> int | None:
    # Each run samples a different PDF set so voting measures cross-sample
    # stability. Offset from the base seed keeps the whole sweep reproducible.
    if seed is None:
        return run_number
    return seed + run_number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Standalone consensus refinement: N patch runs against a base "
        "schema, frequency voting, review queue (uses the OpenAI API)"
    )
    parser.add_argument("--base-schema", default="outputs/private_health/schema.yaml")
    parser.add_argument("--input-root", default="data/private_health/raw/PDFs")
    parser.add_argument("--per-category", type=int, default=5)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--model", default="gpt-5")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--out-dir", default="outputs/private_health/consensus")
    parser.add_argument("--alias-config", help="Alias YAML (default: configs/private_health/aliases.yaml)")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    outputs = SchemaConsensusRefinement(
        discovery=SchemaDiscovery(
            model=args.model,
            timeout_seconds=args.timeout,
            usage_log_path=str(Path(args.out_dir) / "token_usage.jsonl"),
        ),
    ).refine(
        base_schema_path=args.base_schema,
        input_root=args.input_root,
        per_category=args.per_category,
        runs=args.runs,
        seed=args.seed,
        output_dir=args.out_dir,
        alias_config_path=args.alias_config,
    )
    print(f"Wrote candidate patches to {outputs.patch_dir}")
    print(f"Wrote consensus schema (auto-merge reference) to {outputs.consensus_schema_path}")
    print(f"Wrote patch stability to {outputs.stability_path}")
    print(f"Wrote review queue to {outputs.queue_path}")
    print(
        "\nNext: review the queue, then apply decisions:\n"
        f"  streamlit run src/review_app.py -- --consensus-dir {args.out_dir}\n"
        f"  python src/refine/review.py apply --consensus-dir {args.out_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
