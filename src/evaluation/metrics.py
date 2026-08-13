from __future__ import annotations

import csv
import math
import re
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path
from statistics import mean
from typing import Any

from src.evaluation.adapter import adapt_final_schema_for_evaluation
from src.evaluation.taxonomy import canonical_extras_services, canonical_hospital_category
from src.models import EvaluationReport, ExtractionResult, ProductMatch


class PrivateHealthGroundTruthStore:
    def __init__(
        self,
        labelled_dir: str | Path,
        *,
        min_match_score: float = 0.65,
        low_confidence_threshold: float = 0.85,
    ) -> None:
        self.labelled_dir = Path(labelled_dir)
        self.min_match_score = min_match_score
        self.low_confidence_threshold = low_confidence_threshold
        self.products = self._read_csv("konkrd-prod-phi-products-master-formatted.csv")
        self.hospital_rows = self._read_csv("konkrd-prod-phi-hospital-services-master-unformatted.csv")
        self.extras_rows = self._read_csv("konkrd-prod-phi-extras-master-unformatted.csv")
        self.variant_rows = self._read_csv("konkrd-prod-phi-products-master-variant-formatted.csv", required=False)
        self.limit_group_rows = self._read_csv("konkrd-prod-phi-extras-limit-groups-unformatted.csv")
        self._build_indexes()

    def _read_csv(self, name: str, required: bool = True) -> list[dict[str, str]]:
        path = self.labelled_dir / name
        if not path.exists():
            if required:
                raise FileNotFoundError(path)
            return []
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def _build_indexes(self) -> None:
        self.products_by_master = {row["ID Master"]: row for row in self.products}

        self.hospital_by_master: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in self.hospital_rows:
            self.hospital_by_master[row["ID Master"]].append(row)

        self.extras_by_master: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in self.extras_rows:
            self.extras_by_master[row["ID Master"]].append(row)

        self.variants_by_master: dict[str, list[str]] = defaultdict(list)
        for row in self.variant_rows:
            self.variants_by_master[row["ID Master"]].append(row["ProductItemID"])

        self.limit_groups_by_product_item: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in self.limit_group_rows:
            self.limit_groups_by_product_item[row["ProductItemID"]].append(row)

    def match_pdf(self, pdf_path: str | Path) -> ProductMatch | None:
        path = Path(pdf_path)
        ranked_candidates = self.rank_pdf_candidates(path)
        if not ranked_candidates:
            return None
        best_score = ranked_candidates[0]["score"]
        best_row = self.products_by_master[str(ranked_candidates[0]["id_master"])]

        if best_score < self.min_match_score:
            return None

        id_master = best_row["ID Master"]
        runner_up_score = float(ranked_candidates[1]["score"]) if len(ranked_candidates) > 1 else 0.0
        ambiguous_match = runner_up_score >= self.min_match_score and (best_score - runner_up_score) < 0.05
        return ProductMatch(
            pdf_path=str(path),
            id_master=id_master,
            fund_code=best_row["FundCode"],
            brand_code=best_row["BrandCode"],
            name_master=best_row["Name Master"].strip(),
            product_type=best_row["ProductType"],
            hospital_tier=best_row.get("HospitalTier") or None,
            product_item_ids=self.variants_by_master.get(id_master, []),
            match_score=best_score,
            low_confidence_match=best_score < self.low_confidence_threshold or ambiguous_match,
            candidate_matches=ranked_candidates,
            ambiguous_match=ambiguous_match,
        )

    def rank_pdf_candidates(self, pdf_path: str | Path, limit: int = 5) -> list[dict[str, Any]]:
        path = Path(pdf_path)
        normalized_stem = self._normalize_name(path.stem)
        normalized_path = self._normalize_path(path)
        compact_path = self._normalize_name(path.as_posix())
        fund_code = self._extract_fund_code(path)
        product_type = self._extract_product_type(path)
        path_tier = self._extract_hospital_tier(path)
        path_excesses = self._extract_excesses(path.as_posix())
        path_dates = self._extract_dates(path.as_posix())
        path_variant_ids = {
            product_item_id
            for product_item_ids in self.variants_by_master.values()
            for product_item_id in product_item_ids
            if self._normalize_name(product_item_id) in compact_path
        }

        ranked: list[tuple[bool, float, dict[str, str], dict[str, Any]]] = []
        for row in self.products:
            if fund_code and row["FundCode"] != fund_code and row["BrandCode"] != fund_code:
                continue
            if product_type and row["ProductType"].lower() != product_type.lower():
                continue

            gt_pdf_path = row.get("Pdf Filepath", "")
            exact_filepath = self._filepath_matches(path, gt_pdf_path)
            name_candidates = [
                self._normalize_name(row["Name Master"]),
                self._normalize_name(Path(gt_pdf_path).stem),
            ]
            name_score = max(
                SequenceMatcher(None, normalized_stem, candidate).ratio()
                for candidate in name_candidates
                if candidate
            )
            score = 1.0 if exact_filepath else name_score
            evidence: dict[str, Any] = {
                "exact_pdf_filepath": exact_filepath,
                "name_similarity": round(name_score, 6),
                "product_type_match": not product_type or row["ProductType"].lower() == product_type.lower(),
            }
            if not exact_filepath:
                score = self._apply_disambiguators(
                    score,
                    row,
                    path_tier=path_tier,
                    path_excesses=path_excesses,
                    path_dates=path_dates,
                    path_variant_ids=path_variant_ids,
                    evidence=evidence,
                )
                score = min(max(score, 0.0), 0.999)
            if score >= self.min_match_score:
                ranked.append((exact_filepath, score, row, evidence))

        return [
            {
                "id_master": row["ID Master"],
                "name_master": row["Name Master"].strip(),
                "fund_code": row["FundCode"],
                "brand_code": row["BrandCode"],
                "product_type": row["ProductType"],
                "score": score,
                "match_evidence": evidence,
            }
            for _, score, row, evidence in sorted(
                ranked,
                key=lambda item: (item[0], item[1]),
                reverse=True,
            )[:limit]
        ]

    def _apply_disambiguators(
        self,
        score: float,
        row: dict[str, str],
        *,
        path_tier: str | None,
        path_excesses: set[str],
        path_dates: set[str],
        path_variant_ids: set[str],
        evidence: dict[str, Any],
    ) -> float:
        row_tier = self._normalize_name(row.get("HospitalTier", "")) or None
        if path_tier and row_tier:
            tier_match = path_tier == row_tier
            evidence["hospital_tier_match"] = tier_match
            score += 0.08 if tier_match else -0.18

        row_text = " ".join((row.get("Name Master", ""), row.get("Pdf Filepath", "")))
        row_excesses = self._extract_excesses(row_text)
        if path_excesses and row_excesses:
            excess_match = bool(path_excesses & row_excesses)
            evidence["excess_match"] = excess_match
            score += 0.10 if excess_match else -0.20

        master_variants = set(self.variants_by_master.get(row["ID Master"], []))
        if path_variant_ids:
            variant_match = bool(path_variant_ids & master_variants)
            evidence["variant_match"] = variant_match
            score += 0.15 if variant_match else -0.25

        row_dates = self._extract_dates(row_text)
        if path_dates and row_dates:
            date_match = bool(path_dates & row_dates)
            evidence["effective_date_match"] = date_match
            score += 0.05 if date_match else -0.10
        return score

    @classmethod
    def _filepath_matches(cls, pdf_path: Path, gt_path: str) -> bool:
        if not gt_path.strip():
            return False
        actual = cls._normalize_path(pdf_path)
        labelled = cls._normalize_path(Path(gt_path))
        return actual == labelled or actual.endswith(labelled) or labelled.endswith(actual)

    @staticmethod
    def _normalize_path(path: Path) -> str:
        return re.sub(r"[^a-z0-9]+", "/", path.as_posix().casefold()).strip("/")

    @classmethod
    def _extract_hospital_tier(cls, path: Path) -> str | None:
        normalized = cls._normalize_name(path.stem)
        for tier in ("basicplus", "bronzeplus", "silverplus", "gold", "silver", "bronze", "basic"):
            if tier in normalized:
                return tier
        return None

    @staticmethod
    def _extract_excesses(value: str) -> set[str]:
        matches = re.findall(
            r"(?:excess\D{0,8}\$?([1-9]\d{2,3})(?!\d)|"
            r"\$([1-9]\d{2,3})(?!\d)|"
            r"(?<!\d)([1-9]\d{2,3})\D{0,8}excess)",
            value,
            re.I,
        )
        return {number for groups in matches for number in groups if number}

    @staticmethod
    def _extract_dates(value: str) -> set[str]:
        return {
            re.sub(r"\D", "", match)
            for match in re.findall(r"\b(?:20\d{2}[-_/]?\d{1,2}[-_/]?\d{1,2}|\d{1,2}[-_/]\d{1,2}[-_/]20\d{2})\b", value)
        }

    def load_ground_truth(self, pdf_path: str | Path) -> tuple[ProductMatch | None, dict[str, Any]]:
        match = self.match_pdf(pdf_path)
        if match is None:
            return None, {}

        data: dict[str, Any] = {}
        hospital_rows = self.hospital_by_master.get(match.id_master, [])
        extras_rows = self.extras_by_master.get(match.id_master, [])
        if hospital_rows:
            data["hospital"] = {
                "product_name": match.name_master,
                "hospital_tier": match.hospital_tier,
                "clinical_categories": [
                    {"category": row["Title"], "coverage": row["Cover"]} for row in hospital_rows
                ],
            }
        if extras_rows:
            shared_groups = self._build_shared_group_lookup(match.product_item_ids)
            data["extras"] = {
                "product_name": match.name_master,
                "services": [
                    {
                        "service": row["Title"],
                        "covered": self._parse_bool(row["Covered"]),
                        "waiting_period": self._format_waiting_period(
                            row.get("WaitingPeriod", ""), row.get("WaitingPeriodUnit", "")
                        ),
                        "limit_per_person": self._parse_float(row.get("LimitPerPerson", "")),
                        "limit_per_policy": self._parse_float(row.get("LimitPerPolicy", "")),
                        "shared_with": sorted(shared_groups.get(row["Title"], [])),
                    }
                    for row in extras_rows
                ],
            }
        return match, data

    def _build_shared_group_lookup(self, product_item_ids: list[str]) -> dict[str, set[str]]:
        pair_counter: dict[str, Counter[str]] = defaultdict(Counter)
        for product_item_id in product_item_ids:
            for row in self.limit_groups_by_product_item.get(product_item_id, []):
                service = row["Service"]
                other = row["Service Combined With"]
                if service == other:
                    continue
                pair_counter[service][other] += 1

        result: dict[str, set[str]] = {}
        for service, counts in pair_counter.items():
            result[service] = {name for name, count in counts.items() if count >= 1}
        return result

    @staticmethod
    def _normalize_name(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.lower())

    @staticmethod
    def _extract_fund_code(path: Path) -> str | None:
        parts = path.parts
        if "PDFs" in parts:
            index = parts.index("PDFs")
            if index + 1 < len(parts):
                return parts[index + 1]
        return None

    @staticmethod
    def _extract_product_type(path: Path) -> str | None:
        lowered_parts = [part.lower() for part in path.parts]
        for part in lowered_parts:
            if part == "hospital":
                return "Hospital"
            if part in {"extras", "generalhealth"}:
                return "GeneralHealth"
        return None

    @staticmethod
    def _parse_bool(value: str) -> bool:
        return value.strip().lower() == "true"

    @staticmethod
    def _parse_float(value: str) -> float | None:
        cleaned = value.strip()
        if not cleaned:
            return None
        return float(cleaned)

    @staticmethod
    def _format_waiting_period(number: str, unit: str) -> str | None:
        number = number.strip()
        unit = unit.strip()
        if not number or not unit:
            return None
        return f"{number} {unit}"


class ExtractionEvaluator:
    def evaluate(
        self,
        extracted: ExtractionResult,
        ground_truth: dict[str, Any],
        product_key: str | None = None,
    ) -> EvaluationReport:
        evaluation_data = adapt_final_schema_for_evaluation(extracted.data)
        hospital_categories_comparable = self._hospital_categories_comparable(
            extracted.data, evaluation_data
        )
        extracted_flat = self._flatten(evaluation_data)
        comparable_ground_truth = self._without_non_comparable_hospital_categories(
            ground_truth
        ) if not hospital_categories_comparable else ground_truth
        gt_flat = self._flatten(comparable_ground_truth)

        matched_fields = 0
        incorrect_fields: list[str] = []
        present_gt_fields = [key for key in gt_flat if key in extracted_flat]
        for key in present_gt_fields:
            if self._values_equal(extracted_flat[key], gt_flat[key]):
                matched_fields += 1
            else:
                incorrect_fields.append(key)

        # Precision includes all adapted extraction fields.  This measures schema
        # alignment, but is deliberately not called hallucination: proving a
        # source conflict requires evidence that this GT-only evaluator lacks.
        extracted_count = len(extracted_flat)
        gt_count = len(gt_flat)
        comparable_count = len(present_gt_fields)
        precision = matched_fields / extracted_count if extracted_count else 0.0
        recall = matched_fields / gt_count if gt_count else 0.0
        field_presence_recall = comparable_count / gt_count if gt_count else 0.0
        value_accuracy = matched_fields / comparable_count if comparable_count else 0.0
        coverage = field_presence_recall
        normalization_accuracy = self._normalization_accuracy(evaluation_data, comparable_ground_truth)
        section_metrics = self._section_metrics(
            evaluation_data, ground_truth,
            hospital_categories_comparable=hospital_categories_comparable,
        )
        phis_document_class, phis_classification = self._classify_phis_surface(
            evaluation_data, ground_truth
        )

        missing_fields = [key for key in gt_flat if key not in extracted_flat]
        return EvaluationReport(
            source_path=extracted.source_path,
            product_key=product_key,
            extraction_provider=extracted.provider,
            extraction_model=extracted.model,
            field_precision=precision,
            field_recall=recall,
            field_presence_recall=field_presence_recall,
            value_accuracy=value_accuracy,
            normalization_accuracy=normalization_accuracy,
            coverage=coverage,
            hallucination_rate=None,
            hallucination_evaluated=False,
            canonical_name_recall=normalization_accuracy,
            phis_document_class=phis_document_class,
            phis_classification=phis_classification,
            matched_fields=matched_fields,
            comparable_fields=comparable_count,
            extracted_fields=extracted_count,
            ground_truth_fields=gt_count,
            missing_fields=missing_fields,
            incorrect_fields=incorrect_fields,
            section_metrics=section_metrics,
            hallucinations_by_section={},
        )

    def aggregate(
        self,
        reports: list[EvaluationReport],
        *,
        total_documents: int | None = None,
        unmatched_documents: int = 0,
        low_confidence_matches: int = 0,
        fallback_documents: int = 0,
        extraction_errors: int = 0,
    ) -> dict[str, Any]:
        total = total_documents if total_documents is not None else len(reports)
        if not reports:
            empty = {
                "field_precision": 0.0,
                "field_recall": 0.0,
                "field_presence_recall": 0.0,
                "value_accuracy": 0.0,
                "normalization_accuracy": 0.0,
                "coverage": 0.0,
                "hallucination_rate": None,
                "hallucination_evaluated_documents": 0.0,
                "canonical_name_recall": 0.0,
                "total_documents": float(total),
                "matched_documents": 0.0,
                "unmatched_documents": float(unmatched_documents),
                "match_rate": 0.0,
                "low_confidence_matches": float(low_confidence_matches),
                "fallback_documents": float(fallback_documents),
                "extraction_errors": float(extraction_errors),
                "product_accuracy": 0.0,
                "hospital_category_recall": 0.0,
                "hospital_coverage_accuracy": 0.0,
                "extras_service_precision": 0.0,
                "extras_service_recall": 0.0,
                "extras_waiting_period_accuracy": 0.0,
                "extras_limit_accuracy": 0.0,
            }
            empty["macro"] = self._metric_view(empty)
            empty["micro"] = self._micro_summary([])
            empty["document_classes"] = {}
            return empty
        section_summary = self._aggregate_section_metrics(reports)
        summary: dict[str, Any] = {
            "field_precision": mean(report.field_precision for report in reports),
            "field_recall": mean(report.field_recall for report in reports),
            "field_presence_recall": mean(report.field_presence_recall for report in reports),
            "value_accuracy": mean(report.value_accuracy for report in reports),
            "normalization_accuracy": mean(report.normalization_accuracy for report in reports),
            "coverage": mean(report.coverage for report in reports),
            "hallucination_rate": self._mean_available(report.hallucination_rate for report in reports),
            "hallucination_evaluated_documents": float(sum(report.hallucination_evaluated for report in reports)),
            "canonical_name_recall": mean(report.canonical_name_recall for report in reports),
            "total_documents": float(total),
            "matched_documents": float(len(reports)),
            "unmatched_documents": float(unmatched_documents),
            "match_rate": len(reports) / total if total else 0.0,
            "low_confidence_matches": float(low_confidence_matches),
            "fallback_documents": float(fallback_documents),
            "extraction_errors": float(extraction_errors),
            **section_summary,
        }
        summary["macro"] = self._metric_view(summary)
        summary["micro"] = self._micro_summary(reports)
        summary["document_classes"] = dict(Counter(report.phis_document_class for report in reports))
        summary["by_document_class"] = {
            document_class: self._subset_summary(
                [report for report in reports if report.phis_document_class == document_class]
            )
            for document_class in sorted({report.phis_document_class for report in reports})
        }
        return summary

    def _subset_summary(self, reports: list[EvaluationReport]) -> dict[str, Any]:
        macro = {
            "field_precision": mean(report.field_precision for report in reports),
            "field_recall": mean(report.field_recall for report in reports),
            "field_presence_recall": mean(report.field_presence_recall for report in reports),
            "value_accuracy": mean(report.value_accuracy for report in reports),
            "canonical_name_recall": mean(report.canonical_name_recall for report in reports),
            **self._aggregate_section_metrics(reports),
        }
        return {"documents": len(reports), "macro": macro, "micro": self._micro_summary(reports)}

    @staticmethod
    def _metric_view(summary: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "field_precision", "field_recall", "field_presence_recall", "value_accuracy",
            "canonical_name_recall", "hospital_category_recall", "hospital_coverage_accuracy",
            "extras_service_precision", "extras_service_recall",
            "extras_waiting_period_accuracy", "extras_limit_accuracy",
        )
        return {key: summary.get(key) for key in keys}

    def _micro_summary(self, reports: list[EvaluationReport]) -> dict[str, float]:
        matched = sum(report.matched_fields for report in reports)
        extracted = sum(report.extracted_fields for report in reports)
        comparable = sum(report.comparable_fields for report in reports)
        ground_truth = sum(report.ground_truth_fields for report in reports)
        hospital = [
            report.section_metrics["hospital"] for report in reports
            if isinstance(report.section_metrics.get("hospital"), dict)
        ]
        extras = [
            report.section_metrics["extras"] for report in reports
            if isinstance(report.section_metrics.get("extras"), dict)
        ]
        product = [
            report.section_metrics["product"] for report in reports
            if isinstance(report.section_metrics.get("product"), dict)
        ]
        canonical_recognized = 0
        canonical_ground_truth = 0
        for report in reports:
            sections = report.phis_classification.get("sections", {})
            for section in sections.values():
                canonical_recognized += int(section.get("recognized_item_count", 0))
                canonical_ground_truth += int(section.get("ground_truth_item_count", 0))
        return {
            "field_precision": matched / extracted if extracted else 0.0,
            "field_recall": matched / ground_truth if ground_truth else 0.0,
            "field_presence_recall": comparable / ground_truth if ground_truth else 0.0,
            "value_accuracy": matched / comparable if comparable else 0.0,
            "canonical_name_recall": canonical_recognized / canonical_ground_truth if canonical_ground_truth else 0.0,
            "product_accuracy": self._ratio_sum(product, "matched", "comparable"),
            "hospital_category_precision": self._ratio_sum(hospital, "matched_categories", "extracted_categories"),
            "hospital_category_recall": self._ratio_sum(hospital, "matched_categories", "ground_truth_categories"),
            "hospital_coverage_accuracy": self._ratio_sum(hospital, "coverage_matches", "matched_categories"),
            "extras_service_precision": self._ratio_sum(extras, "matched_services", "extracted_services"),
            "extras_service_recall": self._ratio_sum(extras, "matched_services", "ground_truth_services"),
            "extras_waiting_period_accuracy": self._ratio_sum(extras, "waiting_period_matches", "waiting_period_comparable"),
            "extras_limit_accuracy": self._ratio_sum(extras, "limit_matches", "limit_comparable"),
        }

    @staticmethod
    def _ratio_sum(rows: list[dict[str, Any]], numerator: str, denominator: str) -> float:
        denominator_total = sum(int(row.get(denominator, 0)) for row in rows)
        return sum(int(row.get(numerator, 0)) for row in rows) / denominator_total if denominator_total else 0.0

    def _aggregate_section_metrics(self, reports: list[EvaluationReport]) -> dict[str, float]:
        metric_paths = {
            "product_accuracy": ("product", "accuracy"),
            "hospital_category_recall": ("hospital", "category_recall"),
            "hospital_coverage_accuracy": ("hospital", "coverage_accuracy"),
            "extras_service_precision": ("extras", "service_precision"),
            "extras_service_recall": ("extras", "service_recall"),
            "extras_waiting_period_accuracy": ("extras", "waiting_period_accuracy"),
            "extras_limit_accuracy": ("extras", "limit_accuracy"),
        }
        summary: dict[str, float] = {}
        for output_key, path in metric_paths.items():
            values: list[float] = []
            for report in reports:
                value = report.section_metrics
                for part in path:
                    if not isinstance(value, dict) or part not in value:
                        value = None
                        break
                    value = value[part]
                if isinstance(value, int | float):
                    values.append(float(value))
            summary[output_key] = mean(values) if values else 0.0
        return summary

    def _flatten(self, payload: dict[str, Any], prefix: str = "") -> dict[str, Any]:
        flattened: dict[str, Any] = {}
        for key, value in payload.items():
            full_key = f"{prefix}.{key}" if prefix else key
            if value is None:
                continue
            if isinstance(value, dict):
                flattened.update(self._flatten(value, full_key))
            elif isinstance(value, list):
                if value and isinstance(value[0], dict):
                    for item in value:
                        item_key = item.get("category") or item.get("service") or item.get("name")
                        if item_key:
                            canonical_keys = (
                                canonical_extras_services(item_key)
                                if "service" in item else [canonical_hospital_category(item_key)]
                            )
                            for canonical_key in canonical_keys:
                                normalized_key = self._normalize_string(str(canonical_key))
                                for sub_key, sub_value in item.items():
                                    if sub_key in {"category", "service", "name"} or sub_value is None:
                                        continue
                                    flattened[f"{full_key}.{normalized_key}.{sub_key}"] = self._normalize_value(sub_value)
                else:
                    flattened[full_key] = tuple(self._normalize_value(v) for v in value if v is not None)
            else:
                flattened[full_key] = self._normalize_value(value)
        return flattened

    def _normalization_accuracy(self, extracted: dict[str, Any], ground_truth: dict[str, Any]) -> float:
        extracted_names = {self._normalize_string(name) for name in self._collect_names(extracted)}
        gt_names = {self._normalize_string(name) for name in self._collect_names(ground_truth)}
        if not gt_names:
            return 1.0
        return len(extracted_names & gt_names) / len(gt_names)

    def _section_metrics(
        self, extracted: dict[str, Any], ground_truth: dict[str, Any], *,
        hospital_categories_comparable: bool = True,
    ) -> dict[str, Any]:
        hospital_metrics: dict[str, Any] | None = None
        if self._has_gt_items(ground_truth, "hospital", "clinical_categories"):
            hospital_metrics = (
                self._hospital_metrics(extracted, ground_truth)
                if hospital_categories_comparable
                else {
                    "comparable": False,
                    "non_comparable_reason": "no_extracted_clinical_category_surface",
                }
            )
        return {
            "product": self._product_metrics(extracted, ground_truth),
            "hospital": hospital_metrics,
            "extras": (
                self._extras_metrics(extracted, ground_truth)
                if self._has_gt_items(ground_truth, "extras", "services")
                else None
            ),
        }

    def _classify_phis_surface(
        self, extracted: dict[str, Any], ground_truth: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """Infer whether the document exposes a complete canonical PHIS table.

        This is a surface classification, not a statement about model quality:
        it records how much of the labelled canonical taxonomy is visibly present
        in the extraction and how much raw terminology maps to that taxonomy.
        """
        evidence: dict[str, Any] = {
            "method": "canonical_surface_coverage_v1",
            "complete_threshold": 0.8,
            "non_standard_recognition_threshold": 0.5,
            "sections": {},
        }
        section_scores: list[tuple[float, float, int]] = []
        for section_name, item_name, key_name in (
            ("hospital", "clinical_categories", "category"),
            ("extras", "services", "service"),
        ):
            gt_section = ground_truth.get(section_name, {})
            gt_items = gt_section.get(item_name, []) if isinstance(gt_section, dict) else []
            if not gt_items:
                continue
            extracted_section = extracted.get(section_name, {})
            extracted_items = extracted_section.get(item_name, []) if isinstance(extracted_section, dict) else []
            raw_count = len(extracted_items) if isinstance(extracted_items, list) else 0
            extracted_keys = set(self._keyed_items(extracted_items, key_name))
            gt_keys = set(self._keyed_items(gt_items, key_name))
            recognized = len(extracted_keys & gt_keys)
            coverage = recognized / len(gt_keys) if gt_keys else 0.0
            recognition = recognized / len(extracted_keys) if extracted_keys else 0.0
            evidence["sections"][section_name] = {
                "raw_item_count": raw_count,
                "canonical_item_count": len(extracted_keys),
                "recognized_item_count": recognized,
                "ground_truth_item_count": len(gt_keys),
                "canonical_coverage": coverage,
                "taxonomy_recognition": recognition,
            }
            section_scores.append((coverage, recognition, raw_count))

        if section_scores and all(coverage >= 0.8 and recognition >= 0.8 for coverage, recognition, _ in section_scores):
            classification = "complete_phis"
        elif section_scores and any(raw_count > 0 for _, _, raw_count in section_scores) and all(
            recognition < 0.5 for _, recognition, raw_count in section_scores if raw_count > 0
        ):
            classification = "non_standard"
        else:
            classification = "partial"
        evidence["classification"] = classification
        return classification, evidence

    @staticmethod
    def _has_gt_items(
        ground_truth: dict[str, Any],
        section_name: str,
        item_name: str,
    ) -> bool:
        section = ground_truth.get(section_name)
        return isinstance(section, dict) and bool(section.get(item_name))

    @staticmethod
    def _has_extracted_items(
        extracted: dict[str, Any], section_name: str, item_name: str
    ) -> bool:
        section = extracted.get(section_name)
        return isinstance(section, dict) and bool(section.get(item_name))

    @classmethod
    def _hospital_categories_comparable(
        cls, raw_extraction: dict[str, Any], adapted: dict[str, Any]
    ) -> bool:
        if cls._has_extracted_items(adapted, "hospital", "clinical_categories"):
            return True
        notes = str(raw_extraction.get("_notes") or "").casefold()
        explicit_absence_markers = (
            "clinical categories not explicitly listed",
            "clinical categories are not explicitly listed",
            "clinical categories table not present",
            "clinical categories are not listed",
            "clinical categories not in standard",
            "clinical categories are general service categories rather than standard",
        )
        return not any(marker in notes for marker in explicit_absence_markers)

    @staticmethod
    def _without_non_comparable_hospital_categories(
        ground_truth: dict[str, Any]
    ) -> dict[str, Any]:
        from copy import deepcopy

        filtered = deepcopy(ground_truth)
        hospital = filtered.get("hospital")
        if isinstance(hospital, dict):
            hospital.pop("clinical_categories", None)
        return filtered

    def _product_metrics(self, extracted: dict[str, Any], ground_truth: dict[str, Any]) -> dict[str, float | int]:
        extracted_product = self._product_fields(extracted)
        gt_product = self._product_fields(ground_truth)
        comparable = [key for key in gt_product if key in extracted_product]
        matches = sum(
            1 for key in comparable
            if self._values_equal(extracted_product[key], gt_product[key])
        )
        return {
            "matched": matches,
            "comparable": len(comparable),
            "ground_truth": len(gt_product),
            "accuracy": matches / len(comparable) if comparable else 0.0,
            "presence_recall": len(comparable) / len(gt_product) if gt_product else 0.0,
        }

    def _hospital_metrics(self, extracted: dict[str, Any], ground_truth: dict[str, Any]) -> dict[str, float | int]:
        extracted_categories = self._keyed_items(
            extracted.get("hospital", {}).get("clinical_categories", []),
            "category",
        )
        gt_categories = self._keyed_items(
            ground_truth.get("hospital", {}).get("clinical_categories", []),
            "category",
        )
        extracted_keys = set(extracted_categories)
        gt_keys = set(gt_categories)
        common = extracted_keys & gt_keys
        matched_coverage = sum(
            1 for key in common
            if self._values_equal(extracted_categories[key].get("coverage"), gt_categories[key].get("coverage"))
        )
        return {
            "matched_categories": len(common),
            "extracted_categories": len(extracted_keys),
            "ground_truth_categories": len(gt_keys),
            "coverage_matches": matched_coverage,
            "category_precision": len(common) / len(extracted_keys) if extracted_keys else 0.0,
            "category_recall": len(common) / len(gt_keys) if gt_keys else 0.0,
            "coverage_accuracy": matched_coverage / len(common) if common else 0.0,
        }

    def _extras_metrics(self, extracted: dict[str, Any], ground_truth: dict[str, Any]) -> dict[str, float | int]:
        extracted_services = self._keyed_items(extracted.get("extras", {}).get("services", []), "service")
        gt_services = self._keyed_items(ground_truth.get("extras", {}).get("services", []), "service")
        extracted_keys = set(extracted_services)
        gt_keys = set(gt_services)
        common = extracted_keys & gt_keys
        covered_matches = sum(
            1 for key in common
            if self._values_equal(extracted_services[key].get("covered"), gt_services[key].get("covered"))
        )
        waiting_keys = [key for key in common if gt_services[key].get("waiting_period") is not None]
        waiting_matches = sum(
            1 for key in waiting_keys
            if self._values_equal(
                extracted_services[key].get("waiting_period"),
                gt_services[key].get("waiting_period"),
            )
        )
        limit_keys = [
            key for key in common
            if gt_services[key].get("limit_per_person") is not None
            or gt_services[key].get("limit_per_policy") is not None
        ]
        limit_matches = sum(
            1 for key in limit_keys
            if self._values_equal(
                extracted_services[key].get("limit_per_person"),
                gt_services[key].get("limit_per_person"),
            )
            and self._values_equal(
                extracted_services[key].get("limit_per_policy"),
                gt_services[key].get("limit_per_policy"),
            )
        )
        return {
            "matched_services": len(common),
            "extracted_services": len(extracted_keys),
            "ground_truth_services": len(gt_keys),
            "covered_matches": covered_matches,
            "waiting_period_comparable": len(waiting_keys),
            "waiting_period_matches": waiting_matches,
            "limit_comparable": len(limit_keys),
            "limit_matches": limit_matches,
            "service_precision": len(common) / len(extracted_keys) if extracted_keys else 0.0,
            "service_recall": len(common) / len(gt_keys) if gt_keys else 0.0,
            "covered_accuracy": covered_matches / len(common) if common else 0.0,
            "waiting_period_accuracy": waiting_matches / len(waiting_keys) if waiting_keys else 0.0,
            "limit_accuracy": limit_matches / len(limit_keys) if limit_keys else 0.0,
        }

    @staticmethod
    def _product_fields(payload: dict[str, Any]) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        for section_name in ("hospital", "extras"):
            section = payload.get(section_name, {})
            if isinstance(section, dict):
                for key in ("product_name", "hospital_tier"):
                    if section.get(key) is not None:
                        fields.setdefault(key, section[key])
        if isinstance(payload.get("product"), dict):
            for key in ("product_name", "product_type", "hospital_tier", "fund_code", "brand_code"):
                value = payload["product"].get(key)
                if value is not None:
                    fields[key] = value
        for key in ("product_name", "product_type", "hospital_tier"):
            if payload.get(key) is not None:
                fields[key] = payload[key]
        return fields

    def _keyed_items(self, items: Any, key_name: str) -> dict[str, dict[str, Any]]:
        if not isinstance(items, list):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            key = item.get(key_name)
            if key:
                canonical_keys = (
                    canonical_extras_services(key)
                    if key_name == "service"
                    else [canonical_hospital_category(key)]
                )
                for canonical_key in canonical_keys:
                    result[self._normalize_string(str(canonical_key))] = item
        return result

    @staticmethod
    def _mean_available(values: Any) -> float | None:
        available = [float(value) for value in values if isinstance(value, int | float)]
        return mean(available) if available else None

    def _collect_names(self, payload: dict[str, Any]) -> set[str]:
        names: set[str] = set()
        for value in payload.values():
            if isinstance(value, dict):
                names.update(self._collect_names(value))
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        if "category" in item:
                            names.add(canonical_hospital_category(item["category"]))
                        if "service" in item:
                            names.update(canonical_extras_services(item["service"]))
        return names

    def _values_equal(self, left: Any, right: Any) -> bool:
        left_number = self._normalized_number(left)
        right_number = self._normalized_number(right)
        if left_number is not None and right_number is not None:
            return math.isclose(
                float(left_number),
                float(right_number),
                rel_tol=1e-5,
                abs_tol=1e-5,
            )
        if isinstance(left, str) and isinstance(right, str):
            return self._normalize_string(left) == self._normalize_string(right)
        if isinstance(left, list) and isinstance(right, list):
            return self._normalize_value(left) == self._normalize_value(right)
        return left == right

    def _normalize_value(self, value: Any) -> Any:
        if isinstance(value, list):
            return tuple(sorted(self._normalize_value(item) for item in value))
        if isinstance(value, str):
            number = self._normalized_number(value)
            return number if number is not None else self._normalize_string(value)
        return value

    @staticmethod
    def _normalized_number(value: Any) -> Decimal | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int | float | Decimal):
            try:
                return Decimal(str(value))
            except InvalidOperation:
                return None
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        # Only treat the entire string as an amount/number.  Values such as
        # "2 months" remain semantic strings rather than becoming the number 2.
        if not re.fullmatch(r"(?:AUD\s*)?\$?\s*[+-]?(?:\d[\d,]*)(?:\.\d+)?", cleaned, re.I):
            return None
        try:
            return Decimal(re.sub(r"(?:AUD)|[$,\s]", "", cleaned, flags=re.I))
        except InvalidOperation:
            return None

    @staticmethod
    def _normalize_string(value: str) -> str:
        # Split CamelCase before folding punctuation so GT enum/category names
        # and human-readable labels share the same token stream.
        value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
        value = value.casefold().replace("&", " and ")
        tokens = re.findall(r"[a-z0-9]+", value)
        tokens = [token for token in tokens if token not in {"and"}]
        unit_aliases = {
            "months": "month",
            "years": "year",
            "weeks": "week",
            "days": "day",
            "dollars": "dollar",
        }
        return "".join(unit_aliases.get(token, token) for token in tokens)
