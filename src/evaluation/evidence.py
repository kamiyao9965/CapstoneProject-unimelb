from __future__ import annotations

import re
from typing import Any, Iterable

from src.PDFingestor.models import ParsedPDF
from src.evaluation.taxonomy import (
    canonical_extras_services,
    canonical_hospital_categories,
    normalized_name,
)


class ClaimEvidenceAuditor:
    """Conservatively classify extracted atomic claims against source evidence.

    A GT disagreement is called contradicted only after the evaluator has scoped
    that field to a source-visible surface. Claims outside that surface are
    supported when a source block can be found, otherwise they remain
    unverifiable rather than being labelled hallucinations.
    """

    def audit(
        self,
        *,
        extracted: dict[str, Any],
        ground_truth: dict[str, Any],
        documents: Iterable[ParsedPDF],
        values_equal: Any,
    ) -> dict[str, dict[str, Any]]:
        blocks = self._blocks(documents)
        evidence: dict[str, dict[str, Any]] = {}
        for field_path, value in extracted.items():
            if field_path in ground_truth:
                matched = values_equal(value, ground_truth[field_path])
                if matched:
                    evidence[field_path] = {
                        "status": "supported",
                        "basis": "source_scoped_ground_truth",
                        "extracted": self._json_safe(value),
                        "ground_truth": self._json_safe(ground_truth[field_path]),
                    }
                    continue
                extracted_source = self._find_source_support(field_path, value, blocks)
                if extracted_source is not None:
                    evidence[field_path] = {
                        "status": "supported",
                        "basis": "source_block_gt_disagreement",
                        "extracted": self._json_safe(value),
                        "ground_truth": self._json_safe(ground_truth[field_path]),
                        **extracted_source,
                    }
                    continue
                gt_source = self._find_source_support(
                    field_path, ground_truth[field_path], blocks
                )
                evidence[field_path] = {
                    "status": "contradicted" if gt_source is not None else "unverifiable",
                    "basis": (
                        "source_supports_ground_truth"
                        if gt_source is not None
                        else "gt_disagreement_without_claim_level_source_match"
                    ),
                    "extracted": self._json_safe(value),
                    "ground_truth": self._json_safe(ground_truth[field_path]),
                    **(gt_source or {}),
                }
                continue
            source = self._find_source_support(field_path, value, blocks)
            if source is None:
                evidence[field_path] = {
                    "status": "unverifiable",
                    "basis": "no_claim_level_source_match",
                    "extracted": self._json_safe(value),
                }
            else:
                evidence[field_path] = {
                    "status": "supported",
                    "basis": "source_block",
                    "extracted": self._json_safe(value),
                    **source,
                }
        return evidence

    @staticmethod
    def _blocks(documents: Iterable[ParsedPDF]) -> list[dict[str, Any]]:
        return [
            {
                "page": page.page_num,
                "block_id": block.block_id,
                "text": (
                    block.markdown if block.type == "table" else block.content
                ),
            }
            for document in documents
            for page in document.pages
            for block in page.blocks
        ]

    def _find_source_support(
        self,
        field_path: str,
        value: Any,
        blocks: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        entity = self._path_entity(field_path)
        for block in blocks:
            text = str(block["text"])
            if entity and not self._entity_in_text(entity, field_path, text):
                continue
            if not self._value_in_text(value, field_path, text):
                continue
            quote = " ".join(text.split())[:300]
            return {
                "page": block["page"],
                "block_id": block["block_id"],
                "quote": quote,
            }
        return None

    @staticmethod
    def _path_entity(field_path: str) -> str | None:
        parts = field_path.split(".")
        if len(parts) >= 4 and parts[0] in {"hospital", "extras"}:
            return parts[-2]
        return None

    @staticmethod
    def _entity_in_text(entity: str, field_path: str, text: str) -> bool:
        if field_path.startswith("extras."):
            return normalized_name(entity) in {
                normalized_name(value) for value in canonical_extras_services(text)
            }
        if field_path.startswith("hospital."):
            return normalized_name(entity) in {
                normalized_name(value) for value in canonical_hospital_categories(text)
            }
        return normalized_name(entity) in normalized_name(text)

    @staticmethod
    def _value_in_text(value: Any, field_path: str, text: str) -> bool:
        normalized_text = normalized_name(text)
        if isinstance(value, bool):
            if value:
                return True
            return any(token in text.casefold() for token in ("not covered", "excluded"))
        numbers = re.findall(r"\d+(?:\.\d+)?", str(value))
        if numbers and any(number in re.sub(r",", "", text) for number in numbers):
            return True
        normalized_value = normalized_name(str(value))
        if not normalized_value:
            return False
        if normalized_value in normalized_text:
            return True
        if field_path.endswith("coverage"):
            aliases = {
                "covered": {"covered", "included"},
                "notcovered": {"notcovered", "excluded"},
                "restricted": {"restricted"},
            }
            return any(token in normalized_text for token in aliases.get(normalized_value, set()))
        return False

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if isinstance(value, (set, frozenset, tuple)):
            return sorted(value, key=str)
        return value
