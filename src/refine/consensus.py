"""Field-level consensus refinement: multi-run patch voting over a schema.

Adapted from the `testing` branch (src/schema/refinement.py) per
docs/BRANCH_FUSION_PLAN.md. This stabilizes schema *fields* before extraction:

    base schema + N patch-generation runs
      -> normalize field/group names
      -> frequency voting (core/conditional/candidate/noise)
      -> consensus_schema.json + field_frequency.json + CLI summary

It complements (does not replace) the extraction-driven refinement in
src/refine/loop.py, which judges the schema on holdout extraction failures.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.common.data_paths import default_private_health_pdf_root
from src.refine.artifacts.renderer import (
    render_consensus_schema,
    render_frequency_json,
    render_report,
)
from src.refine.candidates.aggregator import FieldDecision, aggregate_patches
from src.refine.candidates.normalizer import load_alias_config, normalize_patches
from src.refine.candidates.patch import load_patch_file, write_patch_file
from src.refine.candidates.stability import (
    compute_patch_stability,
    write_patch_stability,
)
from src.refine.human_review import build_review_queue, write_review_queue
from src.refine.artifacts.schema_fields import fields_by_name, is_applicable_field_patch
from src.common.model_config import resolve_selection
from src.common.json_artifacts import ArtifactError, read_artifact
from src.schema.discovery import SchemaDiscovery
from src.schema.sampler import (
    DEFAULT_CATEGORIES,
    DEFAULT_MANIFEST_ROLES,
    print_samples,
    select_manifest_samples,
    select_samples,
)
from src.schema.migration import migrate_legacy_discovered_schema


@dataclass(frozen=True)
class ConsensusOutputs:
    patch_dir: Path
    consensus_schema_path: Path
    frequency_path: Path
    report: str
    stability_path: Path
    queue_path: Path
    decisions: list[FieldDecision]
    schema_build_samples: tuple[str, ...]


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
        input_root: str | Path | None = None,
        categories: tuple[str, ...] = DEFAULT_CATEGORIES,
        per_category: int = 5,
        runs: int = 3,
        seed: int | None = None,
        samples: list[str] | None = None,
        base_sample_paths: Iterable[str | Path] = (),
        output_dir: str | Path = "outputs/private_health/consensus",
        alias_config_path: str | Path | None = None,
        manifest_path: str | Path | None = None,
        document_roles: tuple[str, ...] = DEFAULT_MANIFEST_ROLES,
    ) -> ConsensusOutputs:
        if runs <= 0:
            raise ValueError("runs must be greater than 0.")
        input_root = input_root or default_private_health_pdf_root()

        base_schema = Path(base_schema_path)
        if not base_schema.exists():
            raise FileNotFoundError(base_schema)

        resolved_output_dir = Path(output_dir)
        patch_dir = resolved_output_dir / "candidate_patches"
        consensus_schema_path = resolved_output_dir / "consensus_schema.json"
        frequency_path = resolved_output_dir / "field_frequency.json"
        stability_path = resolved_output_dir / "patch_stability.json"
        queue_path = resolved_output_dir / "review_queue.json"

        vertical = getattr(self.discovery, "vertical", "private_health")
        field_aliases, group_aliases = load_alias_config(
            alias_config_path,
            vertical=vertical,
        )
        schema_contract = f"{vertical}/discovered_schema"
        current_schema = migrate_legacy_discovered_schema(read_artifact(
            base_schema,
            expected_type="discovered_schema",
            data_contract=schema_contract,
        )["data"])
        all_patches = []
        schema_build_samples = list(dict.fromkeys(str(path) for path in base_sample_paths))

        for run_number in range(1, runs + 1):
            patch_path = patch_dir / f"run_{run_number:03d}.json"
            run_id = f"consensus-{run_number:03d}"
            reusable = self._load_reusable_run(
                patch_path,
                base_schema=base_schema,
                run_id=run_id,
                vertical=vertical,
            )
            if reusable is not None:
                patches, source_documents = reusable
                self._log(
                    f"Reusing validated consensus run {run_number}/{runs}: "
                    f"{patch_path}"
                )
                for sample_path in source_documents:
                    if sample_path not in schema_build_samples:
                        schema_build_samples.append(sample_path)
                all_patches.extend(
                    normalize_patches(patches, field_aliases, group_aliases)
                )
                continue

            run_seed = _run_seed(seed, run_number)
            self._log(f"Starting consensus run {run_number}/{runs}")
            if samples:
                sample_paths = samples
            elif manifest_path:
                sample_paths = select_manifest_samples(
                    input_root=Path(input_root),
                    manifest_path=manifest_path,
                    count=per_category,
                    seed=run_seed,
                    roles=document_roles,
                )
            else:
                sample_paths = select_samples(
                    input_root=Path(input_root),
                    categories=categories,
                    per_category=per_category,
                    seed=run_seed,
                )
                print_samples(sample_paths, Path(input_root), categories)
            for sample_path in sample_paths:
                if sample_path not in schema_build_samples:
                    schema_build_samples.append(sample_path)

            patch_data = self.discovery.discover_patches(
                sample_pdfs=sample_paths,
                current_schema=current_schema,
                output_path=patch_path,
                run_id=run_id,
            )
            write_patch_file(
                patch_data,
                patch_path,
                provenance={
                    "run_id": run_id,
                    "provider": self.discovery.selection.provider,
                    "model": self.discovery.selection.model,
                    "document_input": self.discovery.selection.document_input,
                    "source_documents": list(sample_paths),
                    "source_artifacts": [base_schema.as_posix()],
                },
                vertical=vertical,
                overwrite=patch_path.exists(),
            )
            all_patches.extend(
                normalize_patches(
                    load_patch_file(patch_path, vertical=vertical),
                    field_aliases,
                    group_aliases,
                )
            )

        decisions = aggregate_patches(all_patches, total_runs=runs)
        existing_fields = fields_by_name(current_schema.get("fields", []))
        for decision in decisions:
            if not is_applicable_field_patch(decision, existing_fields):
                self._log(
                    "Skipped unsupported add_alias target: "
                    f"{decision.canonical_name} is not an existing schema field. "
                    "Taxonomy aliases are not supported."
                )
        render_frequency_json(
            decisions,
            frequency_path,
            vertical=vertical,
            overwrite=frequency_path.exists(),
        )
        render_consensus_schema(
            base_schema,
            decisions,
            consensus_schema_path,
            vertical=vertical,
            overwrite=consensus_schema_path.exists(),
        )
        report = render_report(decisions)
        write_patch_stability(
            compute_patch_stability(all_patches, total_runs=runs),
            stability_path,
            provenance={
                "run_id": None, "provider": None, "model": None,
                "document_input": None, "source_documents": [],
                "source_artifacts": [path.as_posix() for path in sorted(patch_dir.glob("*.json"))],
            },
            vertical=vertical,
            overwrite=stability_path.exists(),
        )
        write_review_queue(
            build_review_queue(
                decisions,
                current_schema,
                total_runs=runs,
                base_schema_path=base_schema,
                schema_build_samples=schema_build_samples,
                vertical=vertical,
            ),
            queue_path,
            provenance={
                "run_id": None, "provider": None, "model": None,
                "document_input": None, "source_documents": [],
                "source_artifacts": [frequency_path.as_posix(), base_schema.as_posix()],
            },
            vertical=vertical,
            overwrite=queue_path.exists(),
        )

        return ConsensusOutputs(
            patch_dir=patch_dir,
            consensus_schema_path=consensus_schema_path,
            frequency_path=frequency_path,
            report=report,
            stability_path=stability_path,
            queue_path=queue_path,
            decisions=decisions,
            schema_build_samples=tuple(schema_build_samples),
        )

    def _load_reusable_run(
        self,
        patch_path: Path,
        *,
        base_schema: Path,
        run_id: str,
        vertical: str,
    ) -> tuple[list, tuple[str, ...]] | None:
        """Load a completed run only when its artifact and provenance match."""
        if not patch_path.exists():
            return None
        try:
            artifact = read_artifact(
                patch_path,
                expected_type="candidate_patch_set",
                data_contract=f"{vertical}/candidate_patch_set",
            )
            provenance = artifact.get("provenance")
            if not isinstance(provenance, dict):
                raise ValueError("candidate patch provenance must be an object")
            if provenance.get("run_id") != run_id:
                raise ValueError(
                    f"expected run_id {run_id!r}, got {provenance.get('run_id')!r}"
                )
            source_artifacts = provenance.get("source_artifacts")
            if not isinstance(source_artifacts, list) or not _contains_path(
                source_artifacts, base_schema
            ):
                raise ValueError("candidate patch was built from a different base schema")
            source_documents = provenance.get("source_documents")
            if not isinstance(source_documents, list) or any(
                not isinstance(path, str) for path in source_documents
            ):
                raise ValueError("candidate patch source_documents must be a string list")
            patches = load_patch_file(patch_path, vertical=vertical)
        except (ArtifactError, OSError, TypeError, ValueError) as exc:
            self._log(
                f"Ignoring invalid consensus checkpoint {patch_path}: {exc}. "
                "The run will be regenerated."
            )
            return None
        return patches, tuple(source_documents)

    def _log(self, message: str) -> None:
        if self.log:
            self.log(message)


def _contains_path(values: list[object], expected: Path) -> bool:
    expected_path = expected.resolve()
    for value in values:
        if isinstance(value, str):
            try:
                if Path(value).resolve() == expected_path:
                    return True
            except OSError:
                continue
    return False


def _run_seed(seed: int | None, run_number: int) -> int | None:
    # Each run samples a different PDF set so voting measures cross-sample
    # stability. Offset from the base seed keeps the whole sweep reproducible.
    if seed is None:
        return run_number
    return seed + run_number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Standalone consensus refinement: N patch runs against a base "
        "schema, frequency voting, review queue (uses the selected provider API)"
    )
    parser.add_argument("--base-schema")
    parser.add_argument("--input-root")
    parser.add_argument("--manifest", help="Manifest JSON for stratified document sampling")
    parser.add_argument(
        "--document-roles", nargs="+", default=list(DEFAULT_MANIFEST_ROLES),
        help="Manifest document_role values used for consensus sampling",
    )
    parser.add_argument("--per-category", type=int, default=5)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--provider")
    parser.add_argument(
        "--vertical", default="private_health",
        choices=("private_health", "pet_insurance"),
    )
    parser.add_argument("--model")
    parser.add_argument("--document-input")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--out-dir")
    parser.add_argument(
        "--alias-config",
        help="Alias JSON (default: configs/<vertical>/aliases.json)",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.base_schema is None:
        args.base_schema = f"outputs/{args.vertical}/schema.json"
    if args.input_root is None:
        args.input_root = (
            "data/raw"
            if args.vertical == "pet_insurance"
            else str(default_private_health_pdf_root())
        )
    if args.out_dir is None:
        args.out_dir = f"outputs/{args.vertical}/consensus"
    try:
        selection = resolve_selection(
            provider=args.provider,
            model=args.model,
            document_input=args.document_input,
        )
    except ValueError as exc:
        parser.error(str(exc))
    outputs = SchemaConsensusRefinement(
        discovery=SchemaDiscovery(
            selection=selection,
            timeout_seconds=args.timeout,
            usage_log_path=str(Path(args.out_dir) / "token_usage.jsonl"),
            pdf_root=args.input_root,
            vertical=args.vertical,
        ),
    ).refine(
        base_schema_path=args.base_schema,
        input_root=args.input_root,
        per_category=args.per_category,
        runs=args.runs,
        seed=args.seed,
        output_dir=args.out_dir,
        alias_config_path=args.alias_config,
        manifest_path=args.manifest,
        document_roles=tuple(args.document_roles),
    )
    print(f"Wrote candidate patches to {outputs.patch_dir}")
    print(f"Wrote consensus schema (auto-merge reference) to {outputs.consensus_schema_path}")
    print(f"Wrote patch stability to {outputs.stability_path}")
    print(f"Wrote review queue to {outputs.queue_path}")
    print(outputs.report)
    print(
        "\nNext: review the queue, then apply decisions:\n"
        f"  streamlit run src/review_app.py -- --consensus-dir {args.out_dir}\n"
        f"  python src/refine/review.py apply --consensus-dir {args.out_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
