from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.schema.discovery import SchemaDiscovery
from src.schema.sampler import DEFAULT_CATEGORIES, select_samples
from src.stability.compare import compare
from src.stability.signature import signature_from_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run schema discovery N times on the SAME sampled PDFs and measure drift"
    )
    parser.add_argument("--runs", type=int, default=3, help="Number of discovery runs")
    parser.add_argument("--input-root", default="data/private_health/raw/PDFs")
    parser.add_argument("--per-category", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42,
                        help="Sampling seed - fixed so every run sees the same PDFs")
    parser.add_argument("--model", default="gpt-5")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--temperature", type=float,
                        help="Forward temperature to the model (opt-in; may be rejected by gpt-5)")
    parser.add_argument("--out-dir", default="outputs/private_health/stability")
    parser.add_argument("--show-items", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.runs < 2:
        print("--runs must be at least 2 to measure drift.")
        return 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Sample ONCE so the only thing varying across runs is the model itself.
    sample_paths = select_samples(
        input_root=Path(args.input_root),
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
        schema_yaml = SchemaDiscovery(
            model=args.model,
            timeout_seconds=args.timeout,
            usage_log_path=str(out_dir / "token_usage.jsonl"),
            request_params=request_params,
        ).discover(sample_paths, output_path=out_dir / f"run_{index}.yaml")
        path = out_dir / f"run_{index}.yaml"
        path.write_text(schema_yaml, encoding="utf-8")
        schema_paths.append(path)
        print(f"Wrote {path}\n")

    signatures = [signature_from_file(p) for p in schema_paths]
    compare(signatures, args.show_items)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
