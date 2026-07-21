from __future__ import annotations

import re
from difflib import SequenceMatcher

from src.models import NormalizationResult, VerticalSchema

try:
    from rapidfuzz import fuzz, process
except ImportError:  # pragma: no cover - optional dependency
    fuzz = None
    process = None


class ServiceNormalizer:
    """
    Fuzzy map raw names to the canonical names in the schema.
    """

    def __init__(self, schema: VerticalSchema, threshold: float = 80.0) -> None:
        self.schema = schema
        self.threshold = threshold
        self.canonical_names: list[str] = []
        for section in schema.active_sections().values():
            self.canonical_names.extend(section.canonical_names)

    def normalize(self, raw_name: str) -> NormalizationResult:
        if not self.canonical_names:
            return NormalizationResult(raw=raw_name, canonical=None, confidence=0.0, requires_review=True)

        if process and fuzz:
            match, score, _ = process.extractOne(
                raw_name, self.canonical_names, scorer=fuzz.WRatio
            )
            return NormalizationResult(
                raw=raw_name,
                canonical=match if score >= self.threshold else None,
                confidence=float(score),
                requires_review=score < self.threshold,
            )

        raw_norm = self._normalize_string(raw_name)
        best_name = None
        best_score = 0.0
        for candidate in self.canonical_names:
            score = SequenceMatcher(None, raw_norm, self._normalize_string(candidate)).ratio() * 100
            if score > best_score:
                best_name = candidate
                best_score = score
        return NormalizationResult(
            raw=raw_name,
            canonical=best_name if best_score >= self.threshold else None,
            confidence=best_score,
            requires_review=best_score < self.threshold,
        )

    @staticmethod
    def _normalize_string(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.lower())
