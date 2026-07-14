from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.stability.signature import DIMENSIONS, SchemaSignature, signature_from_file


def jaccard(sets: list[frozenset[str]]) -> tuple[float, set[str], set[str]]:
    """Return (stability, stable_core, union) for a list of sets.

    stability = |intersection over all| / |union| (1.0 == identical everywhere).
    """
    union: set[str] = set().union(*sets) if sets else set()
    core: set[str] = set(sets[0]).intersection(*sets[1:]) if sets else set()
    stability = len(core) / len(union) if union else 1.0
    return stability, core, union


def occurrence_counts(sets: list[frozenset[str]]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for one in sets:
        counter.update(one)
    return counter


def compare(signatures: list[SchemaSignature], show_items: bool) -> float:
    n = len(signatures)
    print(f"Comparing {n} schemas: {', '.join(s.label for s in signatures)}\n")

    header = f"{'dimension':<20} {'stable':>7} {'drift':>6} {'union':>6} {'stability':>10}"
    print(header)
    print("-" * len(header))

    per_dim_stability: list[tuple[float, int]] = []
    drift_report: dict[str, list[tuple[str, int]]] = {}

    for dim in DIMENSIONS:
        sets = [s.get(dim) for s in signatures]
        stability, core, union = jaccard(sets)
        drift = union - core
        per_dim_stability.append((stability, len(union)))
        print(f"{dim:<20} {len(core):>7} {len(drift):>6} {len(union):>6} {stability:>9.1%}")

        if drift:
            counts = occurrence_counts(sets)
            drift_report[dim] = sorted(
                ((item, counts[item]) for item in drift),
                key=lambda pair: (pair[1], pair[0]),
            )

    print("-" * len(header))

    # Overall = union-weighted mean of per-dimension stability, so large
    # dimensions (fields, categories) count more than tiny ones (product_types).
    total_union = sum(size for _, size in per_dim_stability)
    overall = (
        sum(stab * size for stab, size in per_dim_stability) / total_union
        if total_union
        else 1.0
    )
    print(f"{'OVERALL (weighted)':<20} {'':>7} {'':>6} {total_union:>6} {overall:>9.1%}\n")

    if show_items and drift_report:
        print("Drifting items (appearances / total runs):")
        for dim, items in drift_report.items():
            print(f"  [{dim}]")
            for item, count in items:
                print(f"    {count}/{n}  {item}")
        print()

    if overall >= 0.9:
        verdict = "HIGH contract stability - safe to build on"
    elif overall >= 0.75:
        verdict = "MODERATE stability - core is stable, edges drift; pin or merge before building"
    else:
        verdict = "LOW stability - schema is not reproducible; add consensus/merge before use"
    print(f"Verdict: {verdict}")

    return overall


def collect_paths(schemas: list[str] | None, directory: str | None) -> list[Path]:
    paths: list[Path] = []
    if directory:
        paths.extend(sorted(Path(directory).glob("*.json")))
    if schemas:
        paths.extend(Path(p) for p in schemas)
    return paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Measure schema drift across discovery runs")
    parser.add_argument("--schemas", nargs="+", help="Two or more schema JSON artifacts")
    parser.add_argument("--dir", help="Directory of *.json schemas to compare")
    parser.add_argument("--show-items", action="store_true", help="List drifting items")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    paths = collect_paths(args.schemas, args.dir)
    if len(paths) < 2:
        print("Need at least 2 schema files (use --schemas a.json b.json or --dir DIR).")
        return 1

    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        print("Missing files:\n" + "\n".join(f"- {m}" for m in missing))
        return 1

    signatures = [signature_from_file(p) for p in paths]
    compare(signatures, args.show_items)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
