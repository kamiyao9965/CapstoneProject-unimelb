from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.common.json_artifacts import build_success_artifact, write_artifact
from src.common.data_paths import default_private_health_pdf_root
from src.common.model_config import resolve_selection
from src.schema.discovery import SchemaDiscovery
from src.schema.sampler import (
    DEFAULT_CATEGORIES,
    DEFAULT_MANIFEST_ROLES,
    select_manifest_samples,
    select_samples,
)
from src.stability.compare import compare
from src.stability.signature import signature_from_file
from src.refine.verticals import contract_name


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run schema discovery N times on the SAME sampled PDFs and measure drift"
    )
    parser.add_argument("--runs", type=int, default=3, help="Number of discovery runs")
    parser.add_argument("--input-root")
    parser.add_argument(
        "--vertical", default="private_health",
        choices=("private_health", "pet_insurance"),
    )
    parser.add_argument("--manifest", help="Manifest JSON for stratified document sampling")
    parser.add_argument(
        "--document-roles", nargs="+", default=list(DEFAULT_MANIFEST_ROLES),
        help="Manifest document_role values used for sampling",
    )
    parser.add_argument("--per-category", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42,
                        help="Sampling seed - fixed so every run sees the same PDFs")
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--document-input")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--temperature", type=float,
                        help="Forward temperature to the model (opt-in; may be rejected by gpt-5)")
    parser.add_argument("--out-dir")
    parser.add_argument("--show-items", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        selection = resolve_selection(
            provider=args.provider,
            model=args.model,
            document_input=args.document_input,
        )
    except ValueError as exc:
        parser.error(str(exc))
    if args.runs < 2:
        print("--runs must be at least 2 to measure drift.")
        return 1

    input_root = Path(
        args.input_root
        or ("data/raw" if args.vertical == "pet_insurance" else default_private_health_pdf_root())
    )
    out_dir = Path(args.out_dir or f"outputs/{args.vertical}/stability")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Sample ONCE so the only thing varying across runs is the model itself.
    if args.manifest:
        sample_paths = select_manifest_samples(
            input_root=input_root,
            manifest_path=args.manifest,
            count=args.per_category,
            seed=args.seed,
            roles=tuple(args.document_roles),
        )
    else:
        sample_paths = select_samples(
            input_root=input_root,
            categories=DEFAULT_CATEGORIES,
            per_category=args.per_category,
            seed=args.seed,
        )
    print(f"Fixed sample of {len(sample_paths)} PDFs (seed={args.seed}). "
          f"Running discovery {args.runs}x...\n")

    request_params = {"temperature": args.temperature} if args.temperature is not None else None
    schema_paths: list[Path] = []
    for index in range(1, args.runs + 1):
        print(f"--- Run {index}/{args.runs} ---")
        path = out_dir / f"run_{index}.json"
        run_id = uuid4().hex
        schema_data = SchemaDiscovery(
            selection=selection,
            timeout_seconds=args.timeout,
            usage_log_path=str(out_dir / "token_usage.jsonl"),
            request_params=request_params,
            pdf_root=str(input_root),
            vertical=args.vertical,
        ).discover(sample_paths, output_path=path, run_id=run_id)
        artifact = build_success_artifact(
            artifact_type="discovered_schema",
            contract_version="1.0.0",
            data=schema_data,
            provenance={
                "run_id": run_id,
                "provider": selection.provider,
                "model": selection.model,
                "document_input": selection.document_input,
                "source_documents": list(sample_paths),
                "source_artifacts": [],
            },
            data_contract=contract_name(args.vertical, "discovered_schema"),
        )
        write_artifact(
            path,
            artifact,
            data_contract=contract_name(args.vertical, "discovered_schema"),
        )
        schema_paths.append(path)
        print(f"Wrote {path}\n")

    signatures = [signature_from_file(p) for p in schema_paths]
    compare(signatures, args.show_items)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
