from __future__ import annotations

import argparse
import random
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.schema.discovery import SchemaDiscovery

DEFAULT_CATEGORIES = ("combined", "extras", "generalhealth", "hospital")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a private health schema from sample PDFs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser("discover", help="Generate a draft schema from private health PDFs")
    discover.add_argument("--vertical", default="private_health")
    discover.add_argument("--samples", nargs="+")
    discover.add_argument("--input-root", default="data/private_health/raw/PDFs")
    discover.add_argument("--categories", nargs="+", default=list(DEFAULT_CATEGORIES))
    discover.add_argument("--per-category", type=int, default=5)
    discover.add_argument("--seed", type=int)
    discover.add_argument("--output", default="outputs/private_health/schema.yaml")

    return parser


def command_discover(args: argparse.Namespace) -> int:
    if args.vertical != "private_health":
        print("This testing branch only supports --vertical private_health.")
        return 1

    sample_paths = args.samples
    if not sample_paths:
        try:
            sample_paths = select_random_samples(
                input_root=Path(args.input_root),
                categories=tuple(category.lower() for category in args.categories),
                per_category=args.per_category,
                seed=args.seed,
            )
        except ValueError as exc:
            print(exc)
            return 1
        print_selected_samples(sample_paths, Path(args.input_root), args.categories)

    discovery = SchemaDiscovery()
    schema_yaml = discovery.discover(sample_paths, args.vertical)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(schema_yaml, encoding="utf-8")
    print(f"Wrote schema draft to {output_path}")
    return 0


def select_random_samples(
    input_root: Path,
    categories: tuple[str, ...] = DEFAULT_CATEGORIES,
    per_category: int = 5,
    seed: int | None = None,
) -> list[str]:
    if per_category <= 0:
        raise ValueError("--per-category must be greater than 0.")
    if not input_root.exists():
        raise ValueError(f"Input root does not exist: {input_root}")

    rng = random.Random(seed)
    candidates = collect_pdf_candidates(input_root, categories)
    selected: list[Path] = []
    errors: list[str] = []

    for category in categories:
        by_company = candidates.get(category, {})
        companies = sorted(by_company)
        if len(companies) < per_category:
            errors.append(
                f"{category}: found {len(companies)} companies with PDFs, need {per_category}."
            )
            continue

        chosen_companies = rng.sample(companies, per_category)
        for company in chosen_companies:
            selected.append(rng.choice(sorted(by_company[company])))

    if errors:
        detail = "\n".join(f"- {error}" for error in errors)
        raise ValueError(f"Not enough PDFs to build the requested sample:\n{detail}")

    return [str(path) for path in selected]


def collect_pdf_candidates(
    input_root: Path,
    categories: tuple[str, ...],
) -> dict[str, dict[str, list[Path]]]:
    candidates: dict[str, dict[str, list[Path]]] = {
        category: defaultdict(list) for category in categories
    }

    for pdf_path in sorted(input_root.rglob("*.pdf")):
        path_parts_lower = [part.lower() for part in pdf_path.parts]
        matched_categories = [category for category in categories if category in path_parts_lower]
        if not matched_categories:
            continue

        category = matched_categories[0]
        company = infer_company(pdf_path, input_root, category)
        candidates[category][company].append(pdf_path)

    return candidates


def infer_company(pdf_path: Path, input_root: Path, category: str) -> str:
    relative_parts = pdf_path.relative_to(input_root).parts
    lowered = [part.lower() for part in relative_parts]

    if category in lowered:
        category_index = lowered.index(category)
        if category_index > 0:
            return relative_parts[category_index - 1]
        if category_index + 1 < len(relative_parts) - 1:
            return relative_parts[category_index + 1]

    return infer_company_from_filename(pdf_path.stem)


def infer_company_from_filename(stem: str) -> str:
    separators = ("-", "_", " ")
    first_break = len(stem)
    for separator in separators:
        index = stem.find(separator)
        if index > 0:
            first_break = min(first_break, index)
    return stem[:first_break] if first_break < len(stem) else stem


def print_selected_samples(sample_paths: list[str], input_root: Path, categories: list[str]) -> None:
    categories_lower = [category.lower() for category in categories]
    print("Selected PDF samples:")
    for category in categories_lower:
        print(f"[{category}]")
        for sample_path in sample_paths:
            path = Path(sample_path)
            parts_lower = [part.lower() for part in path.parts]
            if category not in parts_lower:
                continue
            try:
                display_path = path.relative_to(input_root)
            except ValueError:
                display_path = path
            print(f"- {display_path}")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "discover":
        return command_discover(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
