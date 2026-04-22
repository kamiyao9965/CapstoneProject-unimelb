from __future__ import annotations

import random
from collections import defaultdict
from pathlib import Path


DEFAULT_CATEGORIES = ("combined", "extras", "generalhealth", "hospital")


def select_samples(
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
    candidates = collect_candidates(input_root, categories)
    selected: list[Path] = []
    errors: list[str] = []

    for category in categories:
        by_company = candidates[category]
        companies = sorted(by_company)
        if len(companies) < per_category:
            errors.append(f"{category}: found {len(companies)} companies, need {per_category}.")
            continue

        for company in rng.sample(companies, per_category):
            selected.append(rng.choice(sorted(by_company[company])))

    if errors:
        raise ValueError("Not enough PDFs:\n" + "\n".join(f"- {error}" for error in errors))

    return [str(path) for path in selected]


def collect_candidates(
    input_root: Path,
    categories: tuple[str, ...],
) -> dict[str, dict[str, list[Path]]]:
    candidates: dict[str, dict[str, list[Path]]] = {
        category: defaultdict(list) for category in categories
    }

    for pdf_path in sorted(input_root.rglob("*.pdf")):
        parts = [part.lower() for part in pdf_path.parts]
        category = next((item for item in categories if item in parts), None)
        if category:
            candidates[category][company_from_path(pdf_path, input_root, category)].append(pdf_path)

    return candidates


def company_from_path(pdf_path: Path, input_root: Path, category: str) -> str:
    relative_parts = pdf_path.relative_to(input_root).parts
    lowered = [part.lower() for part in relative_parts]
    category_index = lowered.index(category)

    if category_index > 0:
        return relative_parts[category_index - 1]
    if category_index + 1 < len(relative_parts) - 1:
        return relative_parts[category_index + 1]
    return pdf_path.stem.split("-", 1)[0].split("_", 1)[0].split(" ", 1)[0]


def print_samples(sample_paths: list[str], input_root: Path, categories: tuple[str, ...]) -> None:
    print("Selected PDF samples:")
    for category in categories:
        print(f"[{category}]")
        for sample_path in sample_paths:
            path = Path(sample_path)
            if category not in [part.lower() for part in path.parts]:
                continue
            try:
                print(f"- {path.relative_to(input_root)}")
            except ValueError:
                print(f"- {path}")
