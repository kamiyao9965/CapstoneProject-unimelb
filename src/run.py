from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.schema.discovery import SchemaDiscovery
from src.schema.sampler import DEFAULT_CATEGORIES, print_samples, select_samples


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a private health schema from PDFs")
    parser.add_argument("--samples", nargs="+")
    parser.add_argument("--input-root", default="data/private_health/raw/PDFs")
    parser.add_argument("--categories", nargs="+", default=list(DEFAULT_CATEGORIES))
    parser.add_argument("--per-category", type=int, default=5)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-5"))
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--keep-uploaded-files", action="store_true")
    parser.add_argument("--output", default="outputs/private_health/schema.yaml")
    parser.add_argument("--usage-log", default="outputs/private_health/token_usage.jsonl")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    categories = tuple(category.lower() for category in args.categories)
    input_root = Path(args.input_root)
    output_path = next_available_path(Path(args.output))

    try:
        sample_paths = args.samples or select_samples(
            input_root=input_root,
            categories=categories,
            per_category=args.per_category,
            seed=args.seed,
        )
        if not args.samples:
            print_samples(sample_paths, input_root, categories)

        schema_yaml = SchemaDiscovery(
            model=args.model,
            cleanup_uploaded_files=not args.keep_uploaded_files,
            timeout_seconds=args.timeout,
            usage_log_path=args.usage_log,
        ).discover(sample_paths, output_path=output_path)
    except Exception as exc:
        print(f"Schema discovery failed: {exc}")
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(schema_yaml, encoding="utf-8")
    print(f"Wrote schema draft to {output_path}")
    return 0


def next_available_path(path: Path) -> Path:
    if not path.exists():
        return path

    for index in range(1, 10_000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Could not find an available output path for {path}")


if __name__ == "__main__":
    raise SystemExit(main())
