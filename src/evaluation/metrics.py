from __future__ import annotations

import csv
import math
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from statistics import mean
from typing import Any

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
        fund_code = self._extract_fund_code(path)
        product_type = self._extract_product_type(path)

        ranked: list[tuple[float, dict[str, str]]] = []
        for row in self.products:
            if fund_code and row["FundCode"] != fund_code and row["BrandCode"] != fund_code:
                continue
            if product_type and row["ProductType"].lower() != product_type.lower():
                continue

            name_candidates = [
                self._normalize_name(row["Name Master"]),
                self._normalize_name(Path(row.get("Pdf Filepath", "")).stem),
            ]
            score = max(
                SequenceMatcher(None, normalized_stem, candidate).ratio()
                for candidate in name_candidates
                if candidate
            )
            if normalized_stem in name_candidates[0] or name_candidates[0] in normalized_stem:
                score = max(score, 0.97)
            if name_candidates[1] and (
                normalized_stem == name_candidates[1]
                or normalized_stem in name_candidates[1]
                or name_candidates[1] in normalized_stem
            ):
                score = max(score, 0.99)
            if score >= self.min_match_score:
                ranked.append((score, row))

        return [
            {
                "id_master": row["ID Master"],
                "name_master": row["Name Master"].strip(),
                "fund_code": row["FundCode"],
                "brand_code": row["BrandCode"],
                "product_type": row["ProductType"],
                "score": score,
            }
            for score, row in sorted(ranked, key=lambda item: item[0], reverse=True)[:limit]
        ]

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
        data = self._labelled_sections(extracted.data, ground_truth)
        extracted_flat = self._flatten(data)
        gt_flat = self._flatten(ground_truth)

        matched_fields = 0
        incorrect_fields: list[str] = []
        present_gt_fields = [key for key in gt_flat if key in extracted_flat]
        for key, value in extracted_flat.items():
            if key in gt_flat and self._values_equal(value, gt_flat[key]):
                matched_fields += 1
            else:
                incorrect_fields.append(key)

        extracted_count = len(extracted_flat)
        gt_count = len(gt_flat)
        comparable_count = len(present_gt_fields)
        precision = matched_fields / extracted_count if extracted_count else 0.0
        recall = matched_fields / gt_count if gt_count else 0.0
        field_presence_recall = comparable_count / gt_count if gt_count else 0.0
        value_accuracy = matched_fields / comparable_count if comparable_count else 0.0
        coverage = field_presence_recall
        hallucinations = [key for key in extracted_flat if key not in gt_flat]
        normalization_accuracy = self._normalization_accuracy(data, ground_truth)
        section_metrics = self._section_metrics(data, ground_truth)
        hallucinations_by_section = dict(Counter(key.split(".", 1)[0] for key in hallucinations))

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
            hallucination_rate=(len(hallucinations) / extracted_count if extracted_count else 0.0),
            matched_fields=matched_fields,
            comparable_fields=comparable_count,
            extracted_fields=extracted_count,
            ground_truth_fields=gt_count,
            missing_fields=missing_fields,
            incorrect_fields=incorrect_fields,
            section_metrics=section_metrics,
            hallucinations_by_section=hallucinations_by_section,
        )

    @staticmethod
    def _labelled_sections(data: dict[str, Any], ground_truth: dict[str, Any]) -> dict[str, Any]:
        """Align exact field names with existing Health label sections.

        Discovered extraction is flat; historical labelled metrics are nested.
        Only label structure determines destinations, never predicted product_type.
        Unknown names remain visible as unmatched fields, without synonym mapping.
        """
        result = {key: value for key, value in data.items()
                  if key not in {"product_type", "_unfilled", "_notes"}}
        for section in ("hospital", "extras"):
            labels = ground_truth.get(section)
            if not isinstance(labels, dict) or section in result:
                continue
            matches = {key: data[key] for key in labels if key in data}
            if matches:
                result[section] = matches
                for key in matches:
                    result.pop(key, None)
        return result

    def aggregate(
        self,
        reports: list[EvaluationReport],
        *,
        total_documents: int | None = None,
        unmatched_documents: int = 0,
        low_confidence_matches: int = 0,
        fallback_documents: int = 0,
        extraction_errors: int = 0,
    ) -> dict[str, float]:
        total = total_documents if total_documents is not None else len(reports)
        if not reports:
            return {
                "field_precision": 0.0,
                "field_recall": 0.0,
                "field_presence_recall": 0.0,
                "value_accuracy": 0.0,
                "normalization_accuracy": 0.0,
                "coverage": 0.0,
                "hallucination_rate": 0.0,
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
        section_summary = self._aggregate_section_metrics(reports)
        return {
            "field_precision": mean(report.field_precision for report in reports),
            "field_recall": mean(report.field_recall for report in reports),
            "field_presence_recall": mean(report.field_presence_recall for report in reports),
            "value_accuracy": mean(report.value_accuracy for report in reports),
            "normalization_accuracy": mean(report.normalization_accuracy for report in reports),
            "coverage": mean(report.coverage for report in reports),
            "hallucination_rate": mean(report.hallucination_rate for report in reports),
            "total_documents": float(total),
            "matched_documents": float(len(reports)),
            "unmatched_documents": float(unmatched_documents),
            "match_rate": len(reports) / total if total else 0.0,
            "low_confidence_matches": float(low_confidence_matches),
            "fallback_documents": float(fallback_documents),
            "extraction_errors": float(extraction_errors),
            **section_summary,
        }

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
                            for sub_key, sub_value in item.items():
                                if sub_key in {"category", "service", "name"} or sub_value is None:
                                    continue
                                flattened[f"{full_key}.{item_key}.{sub_key}"] = self._normalize_value(sub_value)
                else:
                    flattened[full_key] = tuple(self._normalize_value(v) for v in value if v is not None)
            else:
                flattened[full_key] = self._normalize_value(value)
        return flattened

    def _normalization_accuracy(self, extracted: dict[str, Any], ground_truth: dict[str, Any]) -> float:
        extracted_names = self._collect_names(extracted)
        gt_names = self._collect_names(ground_truth)
        if not gt_names:
            return 1.0
        matches = sum(1 for name in extracted_names if name in gt_names)
        return matches / len(gt_names)

    def _section_metrics(self, extracted: dict[str, Any], ground_truth: dict[str, Any]) -> dict[str, Any]:
        return {
            "product": self._product_metrics(extracted, ground_truth),
            "hospital": self._hospital_metrics(extracted, ground_truth),
            "extras": self._extras_metrics(extracted, ground_truth),
        }

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

    @staticmethod
    def _keyed_items(items: Any, key_name: str) -> dict[str, dict[str, Any]]:
        if not isinstance(items, list):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            key = item.get(key_name)
            if key:
                result[str(key)] = item
        return result

    def _collect_names(self, payload: dict[str, Any]) -> set[str]:
        names: set[str] = set()
        for value in payload.values():
            if isinstance(value, dict):
                names.update(self._collect_names(value))
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        if "category" in item:
                            names.add(str(item["category"]))
                        if "service" in item:
                            names.add(str(item["service"]))
        return names

    def _values_equal(self, left: Any, right: Any) -> bool:
        if isinstance(left, float) or isinstance(right, float):
            try:
                return math.isclose(float(left), float(right), rel_tol=1e-5, abs_tol=1e-5)
            except (TypeError, ValueError):
                return False
        return left == right

    def _normalize_value(self, value: Any) -> Any:
        if isinstance(value, list):
            return tuple(sorted(str(item) for item in value))
        if isinstance(value, str):
            return " ".join(value.split())
        return value
