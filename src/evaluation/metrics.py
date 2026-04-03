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
    def __init__(self, labelled_dir: str | Path) -> None:
        self.labelled_dir = Path(labelled_dir)
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
        normalized_stem = self._normalize_name(path.stem)
        fund_code = self._extract_fund_code(path)
        product_type = self._extract_product_type(path)

        best_row: dict[str, str] | None = None
        best_score = 0.0
        for row in self.products:
            if fund_code and row["FundCode"] != fund_code and row["BrandCode"] != fund_code:
                continue
            if product_type and row["ProductType"].lower() != product_type.lower():
                continue

            candidates = [
                self._normalize_name(row["Name Master"]),
                self._normalize_name(Path(row.get("Pdf Filepath", "")).stem),
            ]
            score = max(SequenceMatcher(None, normalized_stem, candidate).ratio() for candidate in candidates if candidate)
            if normalized_stem in candidates[0] or candidates[0] in normalized_stem:
                score = max(score, 0.97)
            if candidates[1] and (normalized_stem == candidates[1] or normalized_stem in candidates[1] or candidates[1] in normalized_stem):
                score = max(score, 0.99)
            if score > best_score:
                best_score = score
                best_row = row

        if best_row is None or best_score < 0.55:
            return None

        id_master = best_row["ID Master"]
        return ProductMatch(
            pdf_path=str(path),
            id_master=id_master,
            fund_code=best_row["FundCode"],
            brand_code=best_row["BrandCode"],
            name_master=best_row["Name Master"].strip(),
            product_type=best_row["ProductType"],
            hospital_tier=best_row.get("HospitalTier") or None,
            product_item_ids=self.variants_by_master.get(id_master, []),
        )

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
        extracted_flat = self._flatten(extracted.data)
        gt_flat = self._flatten(ground_truth)

        matched_fields = 0
        incorrect_fields: list[str] = []
        for key, value in extracted_flat.items():
            if key in gt_flat and self._values_equal(value, gt_flat[key]):
                matched_fields += 1
            else:
                incorrect_fields.append(key)

        extracted_count = len(extracted_flat)
        gt_count = len(gt_flat)
        precision = matched_fields / extracted_count if extracted_count else 0.0
        recall = matched_fields / gt_count if gt_count else 0.0
        coverage = extracted_count / gt_count if gt_count else 0.0
        hallucinations = [key for key in extracted_flat if key not in gt_flat]
        normalization_accuracy = self._normalization_accuracy(extracted.data, ground_truth)

        missing_fields = [key for key in gt_flat if key not in extracted_flat]
        return EvaluationReport(
            source_path=extracted.source_path,
            product_key=product_key,
            field_precision=precision,
            field_recall=recall,
            normalization_accuracy=normalization_accuracy,
            coverage=coverage,
            hallucination_rate=(len(hallucinations) / extracted_count if extracted_count else 0.0),
            matched_fields=matched_fields,
            extracted_fields=extracted_count,
            ground_truth_fields=gt_count,
            missing_fields=missing_fields,
            incorrect_fields=incorrect_fields,
        )

    def aggregate(self, reports: list[EvaluationReport]) -> dict[str, float]:
        if not reports:
            return {
                "field_precision": 0.0,
                "field_recall": 0.0,
                "normalization_accuracy": 0.0,
                "coverage": 0.0,
                "hallucination_rate": 0.0,
            }
        return {
            "field_precision": mean(report.field_precision for report in reports),
            "field_recall": mean(report.field_recall for report in reports),
            "normalization_accuracy": mean(report.normalization_accuracy for report in reports),
            "coverage": mean(report.coverage for report in reports),
            "hallucination_rate": mean(report.hallucination_rate for report in reports),
        }

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
