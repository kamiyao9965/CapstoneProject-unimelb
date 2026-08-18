"""Deterministic travel-insurance document and product classification."""

from __future__ import annotations

import re
from datetime import datetime


_MONTH_PATTERN = (
    r"January|February|March|April|May|June|July|August|September|October|"
    r"November|December"
)
_EFFECTIVE_DATE = re.compile(
    rf"(?:effective|issued|applies)\s+(?:on\s+or\s+after|on|from)?\s*"
    rf"(\d{{1,2}}\s+(?:{_MONTH_PATTERN})\s+\d{{4}})",
    re.IGNORECASE,
)


def classify_document(title: str, url: str, text: str = "") -> str:
    """Classify a PDF using explicit phrases before weaker URL hints."""
    primary = " ".join((title, url)).lower()
    evidence = " ".join((primary, text[:4_000])).lower()
    if re.search(r"\bspds\b|supplementary\s+product\s+disclosure", primary):
        return "spds"
    if re.search(r"target\s+market\s+determination|\btmd\b", primary):
        return "tmd"
    if re.search(r"benefit(?:s)?\s+summary|schedule\s+of\s+benefits|brochure", primary):
        return "brochure"
    primary_is_fsg = bool(re.search(r"financial\s+services\s+guide|\bfsg\b", primary))
    primary_is_pds = bool(
        re.search(r"product\s+disclosure\s+statement|policy\s+wording|\bpds\b", primary)
    )
    if primary_is_fsg and not primary_is_pds:
        return "fsg"
    if re.search(r"\bspds\b|supplementary\s+product\s+disclosure", evidence):
        return "spds"
    if re.search(r"target\s+market\s+determination|\btmd\b", evidence):
        return "tmd"
    if re.search(
        r"product\s+disclosure\s+statement|policy\s+wording|\bpds\b",
        evidence,
    ):
        return "pds"
    if re.search(r"benefit(?:s)?\s+summary|schedule\s+of\s+benefits|brochure", evidence):
        return "brochure"
    if re.search(r"financial\s+services\s+guide|\bfsg\b", evidence):
        return "fsg"
    return "unknown"


def classify_product_axes(text: str) -> dict[str, list[str]]:
    """Return independent, multi-valued canonical travel product axes."""
    normalised = re.sub(r"[-_/]+", " ", text.lower())
    geographic_scopes = _ordered_matches(
        normalised,
        (
            ("international", r"\binternational\b|\boverseas\b"),
            ("domestic", r"\bdomestic\b|within australia"),
            ("inbound", r"\binbound\b|visitors? to australia"),
        ),
    )
    trip_frequencies = _ordered_matches(
        normalised,
        (
            ("single_trip", r"\bsingle\s+trip\b|\bone\s+trip\b"),
            ("annual_multi_trip", r"\bannual\s+multi\s+trip\b|\bmulti\s+trip\b"),
        ),
    )
    plan_tiers = _ordered_matches(
        normalised,
        (
            ("comprehensive", r"\bcomprehensive(?:\+|\s+plus)?\b|\bpremier\b"),
            ("essentials", r"\bessentials(?:\s+(?:plan|cover))?\b|\bstandard\s+plan\b"),
            ("basic", r"\bbasic\b|\bbudget\b"),
            ("medical_only", r"\bmedical\s+only\b"),
        ),
    )
    return {
        "geographic_scopes": geographic_scopes or ["unknown"],
        "trip_frequencies": trip_frequencies or ["unknown"],
        "plan_tiers": plan_tiers or ["unknown"],
    }


def parse_effective_date(text: str) -> str | None:
    """Parse an explicit English effective/issue date into ISO format."""
    match = _EFFECTIVE_DATE.search(text[:30_000])
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%d %B %Y").date().isoformat()
    except ValueError:
        return None


def relationship_confidence(
    *,
    explicit_reference: bool,
    same_section: bool,
    same_provider: bool,
    compatible_axes: bool,
    conflict: bool = False,
) -> tuple[str, bool]:
    """Map relationship evidence to confidence and review requirement."""
    if conflict:
        return "medium", True
    if explicit_reference or (same_section and same_provider and compatible_axes):
        return "high", False
    strong_context_matches = sum((same_section, same_provider, compatible_axes))
    if strong_context_matches >= 2:
        return "medium", True
    return "low", True


def _ordered_matches(
    text: str,
    patterns: tuple[tuple[str, str], ...],
) -> list[str]:
    return [value for value, pattern in patterns if re.search(pattern, text)]
