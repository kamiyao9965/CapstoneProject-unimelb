from __future__ import annotations

import random
import json
from collections import defaultdict
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Iterable

from src.schema.product_types import (
    DEFAULT_OVERRIDE_PATH,
    load_product_type_overrides,
    resolve_product_type,
)

DEFAULT_CATEGORIES = ("combined", "extras", "generalhealth", "hospital")
# Discovery should see both product terms and document-level context. Updates
# and SPDS files are held back for product-family extraction validation.
DEFAULT_MANIFEST_ROLES = (
    "pds",
    "policy_booklet",
    "combined_fsg_pds",
    "renewal_pds",
)
DEFAULT_MANIFEST_SAMPLE_COUNT = 12


def load_document_manifest(path: str | Path) -> dict[str, object]:
    """Load and lightly validate the domain document manifest.

    The manifest is deliberately metadata-only: raw PDFs remain the source of
    truth.  Detailed product attributes may be null until document extraction
    and review have resolved them.
    """
    manifest_path = Path(path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("documents"), list):
        raise ValueError(f"Document manifest must contain a documents list: {manifest_path}")
    seen_ids: set[str] = set()
    for index, document in enumerate(payload["documents"]):
        if not isinstance(document, dict):
            raise ValueError(f"Manifest document {index} must be an object.")
        document_id = document.get("document_id")
        source_path = document.get("source_path")
        family_id = document.get("document_family_id")
        role = document.get("document_role")
        if not all(isinstance(value, str) and value.strip() for value in (document_id, source_path, family_id, role)):
            raise ValueError(
                f"Manifest document {index} needs document_id, source_path, "
                "document_family_id, and document_role strings."
            )
        if document_id in seen_ids:
            raise ValueError(f"Manifest contains duplicate document_id: {document_id}")
        seen_ids.add(document_id)
        if not isinstance(document.get("amends_document_ids", []), list):
            raise ValueError(f"Manifest document {document_id} amends_document_ids must be a list.")
    return payload


def select_manifest_samples(
    input_root: Path,
    manifest_path: str | Path,
    *,
    count: int,
    seed: int | None = None,
    exclude_paths: Iterable[str | Path] = (),
    roles: tuple[str, ...] = DEFAULT_MANIFEST_ROLES,
    exclude_families: Iterable[str] = (),
) -> list[str]:
    """Select balanced manifest documents for discovery or holdout.

    Selection balances ``brand_hint`` and ``document_role`` while allowing at
    most one document from each ``document_family_id``.  Excluding a discovery
    path therefore excludes its whole family, preventing PDS/Update leakage
    between discovery and holdout.  ``count`` is the total number of documents
    in manifest mode (unlike legacy category mode, where it is per category).
    """
    if count <= 0:
        raise ValueError("count must be greater than 0.")
    root = Path(input_root).resolve()
    payload = load_document_manifest(manifest_path)
    documents = payload["documents"]
    assert isinstance(documents, list)
    role_set = {role.strip().lower() for role in roles if role.strip()}
    excluded_paths = {Path(path).resolve() for path in exclude_paths}
    excluded_families = {str(value) for value in exclude_families}
    path_to_family: dict[Path, str] = {}
    candidates: list[dict[str, object]] = []
    for document in documents:
        if not isinstance(document, dict):
            continue
        source = _manifest_source_path(document.get("source_path"), root)
        if source is None or not source.is_file():
            continue
        try:
            source.relative_to(root)
        except ValueError:
            continue
        family = str(document["document_family_id"])
        path_to_family[source] = family
        role = str(document["document_role"]).strip().lower()
        if document.get("discovery_eligible") is not True or role not in role_set:
            continue
        if source in excluded_paths or family in excluded_families:
            continue
        candidates.append({
            "path": source,
            "family": family,
            "role": role,
            "brand": str(document.get("brand_hint") or "unknown").strip().casefold() or "unknown",
        })

    for path in excluded_paths:
        family = path_to_family.get(path)
        if family:
            excluded_families.add(family)
    candidates = [item for item in candidates if item["family"] not in excluded_families]
    if len({str(item["family"]) for item in candidates}) < count:
        available = len({str(item["family"]) for item in candidates})
        raise ValueError(
            f"Not enough unique manifest document families: found {available}, need {count}."
        )

    rng = random.Random(seed)
    for item in candidates:
        item["tie"] = rng.random()
    selected: list[dict[str, object]] = []
    used_families: set[str] = set()
    brand_counts: defaultdict[str, int] = defaultdict(int)
    role_counts: defaultdict[str, int] = defaultdict(int)
    while len(selected) < count:
        available = [item for item in candidates if str(item["family"]) not in used_families]
        if not available:
            break
        # First cover underrepresented brands, then underrepresented roles;
        # seeded randomness only breaks otherwise equivalent ties.
        item = min(
            available,
            key=lambda value: (
                brand_counts[str(value["brand"])],
                role_counts[str(value["role"])],
                float(value["tie"]),
            ),
        )
        selected.append(item)
        used_families.add(str(item["family"]))
        brand_counts[str(item["brand"])] += 1
        role_counts[str(item["role"])] += 1
    return [str(item["path"]) for item in selected]


def manifest_family_ids(
    paths: Iterable[str | Path], manifest_path: str | Path, input_root: Path
) -> set[str]:
    """Resolve document family IDs for paths selected from a manifest."""
    payload = load_document_manifest(manifest_path)
    root = Path(input_root).resolve()
    wanted = {Path(path).resolve() for path in paths}
    families: set[str] = set()
    for document in payload["documents"]:
        if not isinstance(document, dict):
            continue
        source = _manifest_source_path(document.get("source_path"), root)
        if source in wanted:
            families.add(str(document["document_family_id"]))
    return families


def _manifest_source_path(value: object, input_root: Path) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    if not path.is_absolute():
        # Manifest paths are repository-relative; accepting input-root-relative
        # paths keeps the loader useful for temporary test manifests too.
        repository_relative = path.resolve()
        input_relative = (input_root / path).resolve()
        path = repository_relative if repository_relative.exists() else input_relative
    return path.resolve()


def select_samples(
    input_root: Path,
    categories: tuple[str, ...] = DEFAULT_CATEGORIES,
    per_category: int = 5,
    seed: int | None = None,
    exclude_paths: Iterable[str | Path] = (),
    product_type_overrides_path: str | Path | None = DEFAULT_OVERRIDE_PATH,
) -> list[str]:
    if per_category <= 0:
        raise ValueError("--per-category must be greater than 0.")
    if not input_root.exists():
        raise ValueError(f"Input root does not exist: {input_root}")

    rng = random.Random(seed)
    candidates = collect_candidates(
        input_root,
        categories,
        product_type_overrides_path=product_type_overrides_path,
    )
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
    product_type_overrides_path: str | Path | None = DEFAULT_OVERRIDE_PATH,
) -> dict[str, dict[str, list[Path]]]:
    candidates: dict[str, dict[str, list[Path]]] = {
        category: defaultdict(list) for category in categories
    }
    overrides = load_product_type_overrides(product_type_overrides_path)

    for pdf_path in sorted(input_root.rglob("*.pdf")):
        resolution = resolve_product_type(
            pdf_path,
            input_root=input_root,
            categories=categories,
            overrides=overrides,
        )
        category = resolution.effective_product_type
        if category:
            candidates[category][company_from_path(pdf_path, input_root, category)].append(pdf_path)

    return candidates


def company_from_path(pdf_path: Path, input_root: Path, category: str) -> str:
    relative_parts = pdf_path.relative_to(input_root).parts
    lowered = [part.lower() for part in relative_parts]
    if category not in lowered:
        return relative_parts[0] if len(relative_parts) > 1 else pdf_path.stem
    category_index = lowered.index(category)

    if category_index > 0:
        return relative_parts[category_index - 1]
    if category_index + 1 < len(relative_parts) - 1:
        return relative_parts[category_index + 1]
    return pdf_path.stem.split("-", 1)[0].split("_", 1)[0].split(" ", 1)[0]


def print_samples(sample_paths: list[str], input_root: Path, categories: tuple[str, ...]) -> None:
    overrides = load_product_type_overrides()
    print("Selected PDF samples:")
    for category in categories:
        print(f"[{category}]")
        for sample_path in sample_paths:
            path = Path(sample_path)
            resolution = resolve_product_type(
                path,
                input_root=input_root,
                categories=categories,
                overrides=overrides,
            )
            if resolution.effective_product_type != category:
                continue
            try:
                print(f"- {path.relative_to(input_root)}")
            except ValueError:
                print(f"- {path}")
