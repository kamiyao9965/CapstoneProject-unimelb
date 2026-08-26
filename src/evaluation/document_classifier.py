from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from src.PDFingestor.adapter import DEFAULT_CACHE_DIR, ingest_pdfs
from src.PDFingestor.models import ParsedPDF, TableBlock
from src.evaluation.taxonomy import (
    canonical_extras_services,
    canonical_hospital_categories,
    canonical_hospital_category,
    normalized_name,
)


DOCUMENT_CLASSES = {"complete_phis", "partial", "non_standard"}
DEFAULT_MANIFEST_PATH = Path("configs/private_health/document_classes.json")


class PhisDocumentClassifier:
    """Classify the PHIS surface from source PDF structure, never extraction output."""

    def __init__(
        self,
        *,
        manifest_path: str | Path | None = DEFAULT_MANIFEST_PATH,
        cache_dir: str | Path | None = None,
        complete_threshold: float = 0.8,
    ) -> None:
        self.manifest_path = Path(manifest_path) if manifest_path else None
        self.cache_dir = Path(cache_dir or DEFAULT_CACHE_DIR)
        self.complete_threshold = complete_threshold
        self.manifest = self._load_manifest()

    def classify(
        self, pdf_path: str | Path, ground_truth: dict[str, Any]
    ) -> dict[str, Any]:
        documents = self.load_documents(pdf_path)
        return self.classify_documents(documents, ground_truth)

    def load_documents(self, pdf_path: str | Path) -> tuple[ParsedPDF, ...]:
        return tuple(
            ingest_pdfs(
                (pdf_path,), cache_dir=self.cache_dir, pdf_root=Path(pdf_path).parent
            )
        )

    def classify_documents(
        self, documents: Iterable[ParsedPDF], ground_truth: dict[str, Any]
    ) -> dict[str, Any]:
        documents = tuple(documents)
        override = self._manifest_override(documents)
        if override is not None:
            return self._with_default_override_sections(override, ground_truth)

        sections: dict[str, Any] = {}
        for section_name, item_name, key_name in (
            ("hospital", "clinical_categories", "category"),
            ("extras", "services", "service"),
        ):
            gt_section = ground_truth.get(section_name)
            gt_items = gt_section.get(item_name, []) if isinstance(gt_section, dict) else []
            if not gt_items:
                continue
            sections[section_name] = self._classify_section(
                documents, section_name, gt_items, key_name
            )

        section_classes = [section["classification"] for section in sections.values()]
        if section_classes and all(value == "complete_phis" for value in section_classes):
            classification = "complete_phis"
        elif section_classes and all(value == "non_standard" for value in section_classes):
            classification = "non_standard"
        else:
            classification = "partial"
        return {
            "classification": classification,
            "method": "source_table_surface_v2",
            "source_only": True,
            "complete_threshold": self.complete_threshold,
            "sections": sections,
        }

    def _classify_section(
        self,
        documents: tuple[ParsedPDF, ...],
        section_name: str,
        gt_items: list[dict[str, Any]],
        key_name: str,
    ) -> dict[str, Any]:
        gt_by_key: dict[str, str] = {}
        for item in gt_items:
            if not isinstance(item, dict) or not item.get(key_name):
                continue
            canonical_names = self._canonical_names(section_name, item[key_name])
            for canonical in canonical_names:
                gt_by_key[normalized_name(canonical)] = canonical

        tables = [
            block
            for document in documents
            for page in document.pages
            for block in page.blocks
            if isinstance(block, TableBlock)
        ]
        document_text = "\n".join(document.to_llm_markdown() for document in documents)
        heading_found = self._standard_heading_found(section_name, document_text)
        canonical_list_heading = self._canonical_list_heading_found(
            section_name, document_text
        )
        labels = [label for table in tables for label in self._row_labels(table)]
        if canonical_list_heading:
            labels.extend(self._compact_text_labels(documents))
        visible: dict[str, str] = {}
        recognized_labels = 0
        for label in labels:
            recognized_in_label = False
            for canonical in self._canonical_names(section_name, label):
                key = normalized_name(canonical)
                if key in gt_by_key:
                    visible[key] = gt_by_key[key]
                    recognized_in_label = True
            recognized_labels += int(recognized_in_label)

        visible_fields = self._visible_item_fields(
            section_name=section_name,
            tables=tables,
            gt_by_key=gt_by_key,
        )

        structured_surface = bool(tables) or heading_found
        coverage = len(visible) / len(gt_by_key) if gt_by_key else 0.0
        if coverage >= self.complete_threshold:
            classification = "complete_phis"
        elif visible:
            # Any source-visible canonical item makes this a partial PHIS
            # surface.  ``non_standard`` is reserved for structured documents
            # where none of the labelled taxonomy is observable.
            classification = "partial"
        elif structured_surface:
            # A structured benefits document with no canonical category/service
            # surface is non-standard for database-taxonomy recall.  Its source
            # content may still be extracted and evaluated under other schemas.
            classification = "non_standard"
        else:
            classification = "partial"

        return {
            "classification": classification,
            "source_table_count": len(tables),
            "source_candidate_label_count": len(labels),
            "recognized_label_count": recognized_labels,
            "recognized_item_count": len(visible),
            "ground_truth_item_count": len(gt_by_key),
            "canonical_coverage": coverage,
            "standard_heading_found": heading_found,
            "canonical_list_heading_found": canonical_list_heading,
            "visible_canonical_items": sorted(visible.values()),
            "visible_fields": visible_fields,
        }

    def _visible_item_fields(
        self,
        *,
        section_name: str,
        tables: list[TableBlock],
        gt_by_key: dict[str, str],
    ) -> dict[str, list[str]]:
        """Describe which values, not merely which items, occur in source tables."""
        fields: dict[str, set[str]] = {}
        for table in tables:
            rows = table.raw_rows or table.rows
            if not rows:
                continue
            headers = list(table.headers)
            if not headers and rows:
                headers = [str(value or "") for value in rows[0]]
            normalized_headers = [normalized_name(value) for value in headers]
            for row_index, row in enumerate(rows):
                if row_index == 0 and not table.headers:
                    continue
                canonical_items: set[str] = set()
                for cell in row:
                    for canonical in self._canonical_names(section_name, cell):
                        key = normalized_name(canonical)
                        if key in gt_by_key:
                            canonical_items.add(gt_by_key[key])
                if not canonical_items:
                    continue
                row_text = " ".join(str(value or "") for value in row)
                for canonical in canonical_items:
                    visible = fields.setdefault(canonical, set())
                    visible.add("category" if section_name == "hospital" else "service")
                    if section_name == "hospital":
                        if self._coverage_value_visible(row_text):
                            visible.add("coverage")
                        continue
                    # A service listed in an included-benefits table provides
                    # positive coverage evidence even without a boolean cell.
                    visible.add("covered")
                    for column_index, cell in enumerate(row):
                        header = (
                            normalized_headers[column_index]
                            if column_index < len(normalized_headers)
                            else ""
                        )
                        value = str(cell or "").strip()
                        if not value:
                            continue
                        if "waiting" in header and self._waiting_value_visible(value):
                            visible.add("waiting_period")
                        if any(token in header for token in ("limit", "yearly", "annual")):
                            if self._limit_value_visible(value):
                                visible.add("limit_amount")
        return {name: sorted(values) for name, values in sorted(fields.items())}

    @staticmethod
    def _coverage_value_visible(value: str) -> bool:
        normalized = " ".join(value.casefold().split())
        return any(
            token in normalized
            for token in ("covered", "included", "restricted", "not covered", "excluded")
        )

    @staticmethod
    def _waiting_value_visible(value: str) -> bool:
        normalized = " ".join(value.casefold().split())
        return bool(
            re.search(r"\b\d+(?:\.\d+)?\s*(?:day|week|month|year)s?\b", normalized)
            or re.search(r"\b(?:none|nil|no waiting period|immediate cover)\b", normalized)
        )

    @staticmethod
    def _limit_value_visible(value: str) -> bool:
        normalized = " ".join(value.casefold().split())
        return bool(
            re.search(r"(?:\$|aud\s*)\s*\d", normalized)
            or re.search(r"\b(?:unlimited|no annual limit|no limit)\b", normalized)
        )

    @staticmethod
    def _canonical_names(section_name: str, value: Any) -> list[str]:
        if section_name == "extras":
            return canonical_extras_services(value)
        return canonical_hospital_categories(value)

    @staticmethod
    def _row_labels(table: TableBlock) -> list[str]:
        rows = table.raw_rows or table.rows
        labels: list[str] = []
        for index, row in enumerate(rows):
            if not row:
                continue
            for column_index, cell in enumerate(row):
                label = str(cell or "").strip()
                if not label:
                    continue
                normalized = normalized_name(label)
                if index == 0 and normalized in {
                    "category", "clinicalcategory", "service", "services", "treatment",
                    "cover", "coverage", "status", "benefit", "benefits",
                }:
                    continue
                # Very long benefit prose is not a category label and makes
                # substring matching unnecessarily permissive.
                if len(label) <= 180:
                    labels.append(label)
        return labels

    @staticmethod
    def _compact_text_labels(documents: tuple[ParsedPDF, ...]) -> list[str]:
        return [
            block.content.strip()
            for document in documents
            for page in document.pages
            for block in page.blocks
            if block.type in {"text", "vision"}
            and block.content.strip()
            and len(block.content.strip()) <= 180
        ]

    @staticmethod
    def _standard_heading_found(section_name: str, text: str) -> bool:
        normalized = " ".join(text.casefold().replace("&", " and ").split())
        phrases = (
            ("clinical categories", "hospital cover", "hospital treatment")
            if section_name == "hospital"
            else ("extras cover", "general treatment", "ancillary", "included extras")
        )
        return any(phrase in normalized for phrase in phrases)

    @staticmethod
    def _canonical_list_heading_found(section_name: str, text: str) -> bool:
        normalized = " ".join(text.casefold().replace("&", " and ").split())
        phrases = (
            (
                "clinical categories included",
                "clinical categories excluded",
                "clinical categories are",
                "all clinical categories",
                "clinical category table",
            )
            if section_name == "hospital"
            else (
                "included extras",
                "extras services",
                "general treatment services",
                "what you are covered for",
            )
        )
        return any(phrase in normalized for phrase in phrases)

    def _load_manifest(self) -> dict[str, Any]:
        if self.manifest_path is None or not self.manifest_path.exists():
            return {}
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        documents = payload.get("documents", payload)
        if not isinstance(documents, dict):
            raise ValueError(f"Invalid PHIS document-class manifest: {self.manifest_path}")
        return documents

    def _manifest_override(
        self, documents: tuple[ParsedPDF, ...]
    ) -> dict[str, Any] | None:
        for document in documents:
            source = Path(document.source_path).as_posix()
            for key in (document.pdf_hash, source, Path(source).name):
                raw = self.manifest.get(key)
                if raw is not None:
                    return self._validated_override(raw, key)
            for key, raw in self.manifest.items():
                if "/" in key and source.endswith(key):
                    return self._validated_override(raw, key)
        return None

    @staticmethod
    def _validated_override(raw: Any, key: str) -> dict[str, Any]:
        if isinstance(raw, str):
            raw = {"classification": raw}
        if not isinstance(raw, dict) or raw.get("classification") not in DOCUMENT_CLASSES:
            raise ValueError(f"Invalid PHIS document-class override for {key!r}")
        result = deepcopy(raw)
        result.setdefault("sections", {})
        result.update({"method": "manual_manifest", "source_only": True, "manifest_key": key})
        for section in result["sections"].values():
            if isinstance(section, dict):
                section.setdefault("visible_canonical_items", [])
        return result

    @staticmethod
    def _with_default_override_sections(
        override: dict[str, Any], ground_truth: dict[str, Any]
    ) -> dict[str, Any]:
        sections = override.setdefault("sections", {})
        document_class = override["classification"]
        for section_name, item_name in (
            ("hospital", "clinical_categories"),
            ("extras", "services"),
        ):
            gt_section = ground_truth.get(section_name)
            if not isinstance(gt_section, dict) or not gt_section.get(item_name):
                continue
            section = sections.setdefault(section_name, {})
            section.setdefault("classification", document_class)
            section.setdefault("visible_canonical_items", [])
        return override


def default_full_surface_classification(ground_truth: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible explicit default for evaluator-only unit callers."""
    sections: dict[str, Any] = {}
    for section_name, item_name, key_name in (
        ("hospital", "clinical_categories", "category"),
        ("extras", "services", "service"),
    ):
        section = ground_truth.get(section_name)
        items = section.get(item_name, []) if isinstance(section, dict) else []
        if not items:
            continue
        visible: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            value = item.get(key_name)
            if section_name == "extras":
                visible.update(canonical_extras_services(value))
            elif value:
                visible.add(canonical_hospital_category(value))
        sections[section_name] = {
            "classification": "complete_phis",
            "visible_canonical_items": sorted(visible),
            "recognized_item_count": len(visible),
            "ground_truth_item_count": len(visible),
            "canonical_coverage": 1.0,
        }
    return {
        "classification": "complete_phis",
        "method": "explicit_full_ground_truth_default",
        "source_only": True,
        "sections": sections,
    }
