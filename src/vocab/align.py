"""Align a schema's canonical vocabulary with the labelled ground truth.

Accuracy against Konkrd's labelled CSVs is only computable when the schema's
canonical names match the names used in those CSVs. A hand check of one product
showed 27/38 categories matching by name while the coverage statuses were 100%
correct - i.e. the entire apparent error was naming, not extraction.

This tool pairs every canonical name in a schema with its ground-truth
counterpart, grades how confident that pairing is, and lists what a human still
has to decide. It reads local files only; no API calls, no cost.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_LABELLED_DIR = "data/private_health/labelled"
HOSPITAL_CSV = "konkrd-prod-phi-hospital-services-master-unformatted.csv"
EXTRAS_CSV = "konkrd-prod-phi-extras-master-unformatted.csv"
TITLE_COLUMN = "Title"

# A fuzzy pairing below this ratio is not worth showing to a reviewer.
FUZZY_FLOOR = 0.55
# Token overlap at or above this is treated as a strong candidate.
JACCARD_STRONG = 0.5


def split_tokens(name: str) -> list[str]:
    """Break CamelCase / snake_case / hyphenated names into lowercase tokens."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", spaced)
    parts = re.split(r"[\s_\-/]+", spaced)
    return [p.lower() for p in parts if p]


def singular(token: str) -> str:
    if len(token) > 3 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def token_set(name: str) -> frozenset[str]:
    return frozenset(singular(t) for t in split_tokens(name))


# Words that flip the meaning of whatever follows them: "EyeNoCataracts" is the
# eye category EXCLUDING cataracts, so pairing it with "Cataracts" is backwards.
NEGATIONS = {"no", "not", "non", "excl", "excluding", "exclude", "without", "other"}


def negated_tokens(name: str) -> frozenset[str]:
    """Tokens that appear immediately after a negation word."""
    tokens = [singular(t) for t in split_tokens(name)]
    return frozenset(
        tokens[index + 1]
        for index, token in enumerate(tokens[:-1])
        if token in NEGATIONS
    )


def normal_key(name: str) -> str:
    """Order-insensitive, plural-insensitive key: DentalGeneral == GeneralDental."""
    return "".join(sorted(token_set(name)))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class Pairing:
    ours: str
    golden: str | None
    tier: str            # exact | normalized | subset | fuzzy | none
    score: float = 1.0
    alternatives: list[str] = field(default_factory=list)

    @property
    def needs_human(self) -> bool:
        return self.tier in {"subset", "fuzzy", "none"}


def pair_name(ours: str, golden_names: list[str]) -> Pairing:
    golden_by_exact = {g: g for g in golden_names}
    if ours in golden_by_exact:
        return Pairing(ours, ours, "exact")

    ours_key = normal_key(ours)
    for g in golden_names:
        if normal_key(g) == ours_key:
            return Pairing(ours, g, "normalized")

    ours_tokens = token_set(ours)
    negated = negated_tokens(ours)
    # Score a candidate only on tokens the name does not explicitly exclude.
    def shared(g: str) -> int:
        return len((token_set(g) & ours_tokens) - negated)

    subset_hits = [
        g for g in golden_names
        if token_set(g) and (token_set(g) <= ours_tokens or ours_tokens <= token_set(g))
    ]
    subset_hits = [g for g in subset_hits if shared(g) > 0]
    if subset_hits:
        best = max(subset_hits, key=shared)
        others = [g for g in subset_hits if g != best]
        return Pairing(ours, best, "subset", 1.0, others)

    scored: list[tuple[float, str]] = []
    for g in golden_names:
        # A fuzzy pairing with no word in common is a spelling coincidence
        # (Hypnotherapy/Physiotherapy), not a real match. Require shared meaning.
        if shared(g) == 0:
            continue
        overlap = jaccard(ours_tokens, token_set(g))
        ratio = SequenceMatcher(None, ours.lower(), g.lower()).ratio()
        scored.append((max(overlap if overlap >= JACCARD_STRONG else 0.0, ratio), g))
    scored.sort(reverse=True)
    if scored and scored[0][0] >= FUZZY_FLOOR:
        best_score, best = scored[0]
        alts = [g for s, g in scored[1:3] if s >= FUZZY_FLOOR]
        return Pairing(ours, best, "fuzzy", round(best_score, 3), alts)

    return Pairing(ours, None, "none", 0.0)


def load_golden(labelled_dir: Path, filename: str) -> list[str]:
    path = labelled_dir / filename
    if not path.exists():
        raise FileNotFoundError(path)
    names: set[str] = set()
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            title = (row.get(TITLE_COLUMN) or "").strip()
            if title:
                names.add(title)
    return sorted(names)


def load_schema(path: Path) -> dict:
    """Accept either a JSON artifact (new format) or a raw YAML schema (legacy)."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        from src.common.json_codec import loads_json

        payload = loads_json(text)
        if isinstance(payload, dict) and "data" in payload and "artifact_type" in payload:
            return payload["data"]
        return payload
    import yaml

    return yaml.safe_load(text) or {}


def canonical_names(schema: dict, section: str) -> list[str]:
    return [
        str(item["canonical_name"]).strip()
        for item in (schema.get(section) or [])
        if isinstance(item, dict) and item.get("canonical_name")
    ]


def report_section(title: str, ours: list[str], golden: list[str]) -> list[Pairing]:
    pairings = [pair_name(name, golden) for name in ours]
    matched_golden = {p.golden for p in pairings if p.golden}
    missing_golden = [g for g in golden if g not in matched_golden]

    by_tier: dict[str, list[Pairing]] = {}
    for p in pairings:
        by_tier.setdefault(p.tier, []).append(p)

    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")
    print(f"schema names: {len(ours)}   ground-truth names: {len(golden)}")
    counts = " | ".join(
        f"{tier}: {len(by_tier.get(tier, []))}"
        for tier in ("exact", "normalized", "subset", "fuzzy", "none")
    )
    print(f"pairings -> {counts}")

    if by_tier.get("normalized"):
        print("\n[normalized] same words, different form - safe to rename:")
        for p in by_tier["normalized"]:
            print(f"   {p.ours:<45} -> {p.golden}")

    if by_tier.get("subset"):
        print("\n[subset] one name qualifies the other - CHECK, usually the same thing:")
        for p in by_tier["subset"]:
            extra = f"   (also: {', '.join(p.alternatives)})" if p.alternatives else ""
            print(f"   {p.ours:<45} -> {p.golden}{extra}")

    if by_tier.get("fuzzy"):
        print("\n[fuzzy] similar wording - HUMAN DECISION REQUIRED:")
        for p in sorted(by_tier["fuzzy"], key=lambda x: -x.score):
            extra = f"   (or: {', '.join(p.alternatives)})" if p.alternatives else ""
            print(f"   {p.ours:<45} -> {p.golden}  [{p.score}]{extra}")

    if by_tier.get("none"):
        print("\n[no match] not in the ground truth - not evaluable for accuracy:")
        for p in by_tier["none"]:
            print(f"   {p.ours}")

    if missing_golden:
        print(f"\n[gap] in ground truth but absent from the schema ({len(missing_golden)}):")
        for name in missing_golden:
            print(f"   {name}")

    return pairings


def build_rename_map(pairings: list[Pairing], include_uncertain: bool) -> dict[str, str]:
    """schema name -> ground-truth name, for the pairings worth applying."""
    tiers = {"normalized", "subset"} if include_uncertain else {"normalized"}
    return {
        p.ours: p.golden
        for p in pairings
        if p.golden and p.tier in tiers and p.ours != p.golden
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Align schema canonical names with the labelled ground-truth vocabulary"
    )
    parser.add_argument("--schema", required=True, help="Schema JSON artifact or legacy YAML")
    parser.add_argument("--labelled-dir", default=DEFAULT_LABELLED_DIR)
    parser.add_argument(
        "--write-map",
        help="Write the proposed schema-name -> ground-truth-name map to this JSON file",
    )
    parser.add_argument(
        "--include-subset",
        action="store_true",
        help="Also put [subset] pairings in the written map (review them first)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    schema = load_schema(Path(args.schema))
    labelled_dir = Path(args.labelled_dir)

    hospital_pairs = report_section(
        "HOSPITAL CATEGORIES",
        canonical_names(schema, "hospital_categories"),
        load_golden(labelled_dir, HOSPITAL_CSV),
    )
    extras_pairs = report_section(
        "EXTRAS SERVICES",
        canonical_names(schema, "extras_services"),
        load_golden(labelled_dir, EXTRAS_CSV),
    )

    total = hospital_pairs + extras_pairs
    aligned = sum(1 for p in total if p.tier in {"exact", "normalized"})
    print(f"\n{'=' * 70}")
    print(f"Directly usable for scoring now: {aligned}/{len(total)}")
    print(f"Needing a human decision:        {sum(1 for p in total if p.needs_human)}")

    if args.write_map:
        from src.common.json_codec import dumps_json

        payload = {
            "hospital_categories": build_rename_map(hospital_pairs, args.include_subset),
            "extras_services": build_rename_map(extras_pairs, args.include_subset),
        }
        out = Path(args.write_map)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(dumps_json(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        counted = sum(len(v) for v in payload.values())
        print(f"\nWrote {counted} rename proposals to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
