from __future__ import annotations

import random
from collections import defaultdict
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Iterable


DEFAULT_CATEGORIES = ("combined", "extras", "generalhealth", "hospital")


def select_samples(
    input_root: Path,
    categories: tuple[str, ...] = DEFAULT_CATEGORIES,
    per_category: int = 5,
    seed: int | None = None,
    exclude_paths: Iterable[str | Path] = (),
) -> list[str]:
    if per_category <= 0:
        raise ValueError("--per-category must be greater than 0.")
    if not input_root.exists():
        raise ValueError(f"Input root does not exist: {input_root}")

    rng = random.Random(seed)
    candidates = collect_candidates(input_root, categories)
    excluded = {Path(path).resolve() for path in exclude_paths}
    excluded_identities = {
        document_identity(path)
        for path in excluded
        if path.is_file()
    }
    selected: list[Path] = []
    selected_identities: set[str] = set()
    errors: list[str] = []

    for category in categories:
        by_company = {
            company: [
                path
                for path in paths
                if path.resolve() not in excluded
                and document_identity(path) not in excluded_identities
            ]
            for company, paths in candidates[category].items()
        }
        by_company = {company: paths for company, paths in by_company.items() if paths}
        category_selection = _select_unique_documents(
            by_company,
            per_category,
            rng,
            selected_identities,
        )
        if category_selection is None:
            unique_documents = {
                document_identity(path)
                for paths in by_company.values()
                for path in paths
                if document_identity(path) not in selected_identities
            }
            errors.append(
                f"{category}: found {len(unique_documents)} unique documents across "
                f"{len(by_company)} companies, need {per_category}."
            )
            continue

        for path in category_selection:
            selected.append(path)
            selected_identities.add(document_identity(path))

    if errors:
        raise ValueError(
            "Not enough unique PDFs:\n" + "\n".join(f"- {error}" for error in errors)
        )

    return [str(path) for path in selected]


def document_identity(path: str | Path) -> str:
    """Return a content identity that treats copied PDFs as the same document."""
    resolved = Path(path).resolve()
    stat = resolved.stat()
    return _cached_digest(resolved, stat.st_size, stat.st_mtime_ns)


@lru_cache(maxsize=None)
def _cached_digest(path: Path, size: int, modified_ns: int) -> str:
    del size, modified_ns  # cache-key metadata invalidates the digest after file changes
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _select_unique_documents(
    by_company: dict[str, list[Path]],
    count: int,
    rng: random.Random,
    unavailable_identities: set[str],
) -> list[Path] | None:
    """Choose distinct companies and content identities, or report impossibility."""
    companies = sorted(by_company)
    rng.shuffle(companies)
    choices: dict[str, list[Path]] = {}
    for company in companies:
        paths = sorted(by_company[company])
        rng.shuffle(paths)
        choices[company] = paths

    def search(
        company_index: int,
        chosen: list[Path],
        used_identities: set[str],
    ) -> list[Path] | None:
        if len(chosen) == count:
            return chosen
        if len(companies) - company_index < count - len(chosen):
            return None

        for index in range(company_index, len(companies)):
            company = companies[index]
            for path in choices[company]:
                identity = document_identity(path)
                if identity in used_identities or identity in unavailable_identities:
                    continue
                result = search(
                    index + 1,
                    [*chosen, path],
                    used_identities | {identity},
                )
                if result is not None:
                    return result
        return None

    return search(0, [], set())


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
