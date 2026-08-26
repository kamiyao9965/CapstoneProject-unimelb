from __future__ import annotations

import csv
import math
import re
from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from src.PDFingestor.models import ParsedPDF
from src.evaluation.adapter import adapt_final_schema_for_evaluation
from src.evaluation.document_classifier import default_full_surface_classification
from src.evaluation.evidence import ClaimEvidenceAuditor
from src.evaluation.taxonomy import (
    canonical_extras_services,
    canonical_hospital_category,
    canonical_product_name,
)
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

        path_product_type = self._extract_product_type(path)
        if path_product_type == "Combined":
            combined = [
                item for item in ranked_candidates
                if item["product_type"].casefold() == "combined"
            ]
            if combined and self._credible_combined_candidate(combined[0]):
                return self._product_match(path, combined, combined[0])
            composite = self._combined_component_match(path, ranked_candidates)
            if composite is not None:
                return composite

        best = ranked_candidates[0]
        return self._product_match(path, ranked_candidates, best)

    def _product_match(
        self,
        path: Path,
        ranked_candidates: list[dict[str, Any]],
        best: dict[str, Any],
    ) -> ProductMatch | None:
        best_score = float(best["score"])
        best_row = self.products_by_master[str(best["id_master"])]

        if best_score < self.min_match_score:
            return None

        id_master = best_row["ID Master"]
        same_type = [
            item for item in ranked_candidates
            if item["product_type"].casefold() == best["product_type"].casefold()
        ]
        runner_up = same_type[1] if len(same_type) > 1 else None
        runner_up_score = float(runner_up["score"]) if runner_up else 0.0
        # An authoritative filepath match must not become ambiguous merely
        # because a fuzzy-name candidate happens to score within 0.05.  Exact
        # candidates are compared only with other exact candidates.
        best_is_exact = bool(best.get("match_evidence", {}).get("exact_pdf_filepath"))
        runner_up_is_exact = bool(
            runner_up
            and runner_up.get("match_evidence", {}).get("exact_pdf_filepath")
        )
        same_evidence_tier = best_is_exact == runner_up_is_exact
        ambiguous_match = bool(
            runner_up
            and same_evidence_tier
            and runner_up_score >= self.min_match_score
            and (best_score - runner_up_score) < 0.05
        )
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

    @staticmethod
    def _credible_combined_candidate(candidate: dict[str, Any]) -> bool:
        evidence = candidate["match_evidence"]
        return bool(
            evidence.get("exact_pdf_filepath")
            or float(evidence.get("name_similarity", 0.0)) >= 0.94
        )

    def _combined_component_match(
        self, path: Path, ranked_candidates: list[dict[str, Any]]
    ) -> ProductMatch | None:
        components: list[dict[str, Any]] = []
        ambiguous = False
        for product_type in ("Hospital", "GeneralHealth"):
            candidates = [
                item for item in ranked_candidates
                if item["product_type"].casefold() == product_type.casefold()
            ]
            if not candidates or float(candidates[0]["score"]) < self.min_match_score:
                return None
            components.append(candidates[0])
            if len(candidates) > 1:
                runner_up = float(candidates[1]["score"])
                best_exact = bool(
                    candidates[0].get("match_evidence", {}).get("exact_pdf_filepath")
                )
                runner_exact = bool(
                    candidates[1].get("match_evidence", {}).get("exact_pdf_filepath")
                )
                ambiguous |= (
                    best_exact == runner_exact
                    and
                    runner_up >= self.min_match_score
                    and float(candidates[0]["score"]) - runner_up < 0.05
                )

        rows = [self.products_by_master[str(item["id_master"])] for item in components]
        ids = [row["ID Master"] for row in rows]
        scores = [float(item["score"]) for item in components]
        hospital_row = next(row for row in rows if row["ProductType"].casefold() == "hospital")
        product_item_ids = sorted({
            item_id for id_master in ids
            for item_id in self.variants_by_master.get(id_master, [])
        })
        return ProductMatch(
            pdf_path=str(path),
            id_master="+".join(ids),
            fund_code=hospital_row["FundCode"],
            brand_code=hospital_row["BrandCode"],
            name_master=" + ".join(row["Name Master"].strip() for row in rows),
            product_type="Combined",
            hospital_tier=hospital_row.get("HospitalTier") or None,
            product_item_ids=product_item_ids,
            component_id_masters=ids,
            composite_match=True,
            match_score=min(scores),
            low_confidence_match=(
                min(scores) < self.low_confidence_threshold or ambiguous
            ),
            candidate_matches=ranked_candidates,
            ambiguous_match=ambiguous,
        )

    def rank_pdf_candidates(self, pdf_path: str | Path, limit: int = 20) -> list[dict[str, Any]]:
        path = Path(pdf_path)
        normalized_stem = self._normalize_name(path.stem)
        normalized_path = self._normalize_path(path)
        compact_path = self._normalize_name(path.as_posix())
        fund_code = self._extract_fund_code(path)
        product_type = self._extract_product_type(path)
        path_tier = self._extract_hospital_tier(path)
        path_excesses = self._extract_excesses(path.as_posix())
        path_dates = self._extract_dates(path.as_posix())
        path_name_tokens = self._distinctive_name_tokens(path.stem)
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
            if (
                product_type
                and product_type != "Combined"
                and row["ProductType"].lower() != product_type.lower()
            ):
                continue
            if product_type == "Combined" and row["ProductType"].casefold() not in {
                "combined", "hospital", "generalhealth",
            }:
                continue

            gt_pdf_path = row.get("Pdf Filepath", "")
            exact_filepath = self._filepath_matches(path, gt_pdf_path)
            strict_filepath = self._fund_relative_filepath_matches(
                path, gt_pdf_path, fund_code
            )
            name_candidates = [
                self._normalize_name(row["Name Master"]),
                self._normalize_name(Path(gt_pdf_path).stem),
            ]
            name_score = max(
                SequenceMatcher(None, normalized_stem, candidate).ratio()
                for candidate in name_candidates
                if candidate
            )
            master_name = self._normalize_name(row["Name Master"])
            if len(master_name) >= 8 and master_name in normalized_stem:
                name_score = max(name_score, 0.95)
            score = 1.0 if exact_filepath else name_score
            evidence: dict[str, Any] = {
                "exact_pdf_filepath": exact_filepath,
                "strict_pdf_filepath": strict_filepath,
                "name_similarity": round(name_score, 6),
                "product_type_match": not product_type or row["ProductType"].lower() == product_type.lower(),
            }
            score = self._apply_disambiguators(
                score,
                row,
                path_tier=path_tier,
                path_excesses=path_excesses,
                path_dates=path_dates,
                path_variant_ids=path_variant_ids,
                path_fund_code=fund_code,
                path_name_tokens=path_name_tokens,
                path_product_type=product_type,
                exact_filepath=exact_filepath,
                evidence=evidence,
            )
            score = min(max(score, 0.0), 1.0)
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
                # Exact paths remain authoritative, but their scores are no
                # longer all forced to 1.0: tier/excess/date/variant evidence
                # can now resolve multiple CSV rows sharing that filepath.
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
        path_fund_code: str | None,
        path_name_tokens: set[str],
        path_product_type: str | None,
        exact_filepath: bool,
        evidence: dict[str, Any],
    ) -> float:
        if path_fund_code:
            fund_match = path_fund_code in {row.get("FundCode"), row.get("BrandCode")}
            evidence["fund_or_brand_match"] = fund_match
            brand_match = path_fund_code == row.get("BrandCode")
            evidence["brand_match"] = brand_match
            if not fund_match:
                score -= 0.15
            elif not brand_match:
                # A fund can own multiple brand codes.  The directory code is
                # the stronger discriminator when otherwise identical master
                # rows share a filename (for example MYO versus MYO01).
                score -= 0.08

        row_name_tokens = self._distinctive_name_tokens(row.get("Name Master", ""))
        if exact_filepath:
            pass
        elif path_name_tokens and not row_name_tokens and path_product_type != "Combined":
            score -= 0.18
        elif path_name_tokens and row_name_tokens:
            overlap = path_name_tokens & row_name_tokens
            evidence["distinctive_name_tokens"] = sorted(overlap)
            if overlap:
                coverage = len(overlap) / len(path_name_tokens)
                score += (0.20 * coverage) * (1.0 - score)
                unmatched_row_tokens = row_name_tokens - path_name_tokens
                score -= min(0.04 * len(unmatched_row_tokens), 0.12)
                if path_product_type != "Combined" and len(path_name_tokens) <= 2:
                    score -= min(0.15 * len(path_name_tokens - overlap), 0.30)
                    if coverage == 1.0:
                        score += 0.15 * (1.0 - score)
            else:
                score -= 0.12
        row_tier = self._normalize_name(row.get("HospitalTier", "")) or None
        if path_tier and row_tier:
            tier_match = path_tier == row_tier
            evidence["hospital_tier_match"] = tier_match
            score = score + 0.08 * (1.0 - score) if tier_match else score - 0.18

        row_text = " ".join((row.get("Name Master", ""), row.get("Pdf Filepath", "")))
        row_excesses = self._extract_excesses(row_text)
        if path_excesses and row_excesses:
            excess_match = bool(path_excesses & row_excesses)
            evidence["excess_match"] = excess_match
            score = score + 0.10 * (1.0 - score) if excess_match else score - 0.20

        master_variants = set(self.variants_by_master.get(row["ID Master"], []))
        if path_variant_ids:
            variant_match = bool(path_variant_ids & master_variants)
            evidence["variant_match"] = variant_match
            score = score + 0.15 * (1.0 - score) if variant_match else score - 0.25

        row_dates = self._extract_dates(row_text)
        if path_dates and row_dates:
            date_match = bool(path_dates & row_dates)
            evidence["effective_date_match"] = date_match
            score = score + 0.05 * (1.0 - score) if date_match else score - 0.10
        return score

    @classmethod
    def _filepath_matches(cls, pdf_path: Path, gt_path: str) -> bool:
        if not gt_path.strip():
            return False
        actual = cls._normalize_path(pdf_path)
        labelled = cls._normalize_path(Path(gt_path))
        return (
            actual == labelled
            or actual.endswith(labelled)
            or labelled.endswith(actual)
            or cls._relative_product_path(actual) == cls._relative_product_path(labelled)
        )

    @classmethod
    def _fund_relative_filepath_matches(
        cls, pdf_path: Path, gt_path: str, fund_code: str | None
    ) -> bool:
        """Match the full path below the fund directory, retaining year/version."""
        if not gt_path.strip() or not fund_code:
            return False
        fund = cls._normalize_name(fund_code)

        def below_fund(value: str) -> str | None:
            parts = value.split("/")
            indexes = [index for index, part in enumerate(parts) if part == fund]
            return "/".join(parts[indexes[-1]:]) if indexes else None

        actual = below_fund(cls._normalize_path(pdf_path))
        labelled = below_fund(cls._normalize_path(Path(gt_path)))
        return bool(actual and labelled and actual == labelled)

    @staticmethod
    def _relative_product_path(normalized_path: str) -> str:
        parts = normalized_path.split("/")
        category_indexes = [
            index for index, part in enumerate(parts)
            if part in {"closed", "combined", "extras", "generalhealth", "hospital"}
        ]
        if not category_indexes:
            return normalized_path
        index = category_indexes[-1]
        return "/".join(parts[index:])

    @classmethod
    def _normalize_path(cls, path: Path) -> str:
        # Normalize each filesystem component separately.  Normalizing the
        # entire string used to turn words such as "Hospital" inside a filename
        # into fake directories and produced false exact-path matches.
        return "/".join(
            normalized for part in path.parts
            if (normalized := cls._normalize_name(part))
        )

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

    @staticmethod
    def _distinctive_name_tokens(value: str) -> set[str]:
        stop_words = {
            "accident", "and", "basic", "bronze", "co", "combined", "cover",
            "excess", "extras", "gold", "health", "hospital", "only",
            "pay", "plus", "policy", "product", "silver", "with",
        }
        normalized = re.sub(
            r"(?<![a-z])co[-_\s]*pay(?![a-z])", "copay", value.casefold()
        )
        return {
            token for token in re.findall(r"[a-z]+", normalized)
            if len(token) >= 4 and token not in stop_words
        }

    def load_ground_truth(self, pdf_path: str | Path) -> tuple[ProductMatch | None, dict[str, Any]]:
        match = self.match_pdf(pdf_path)
        if match is None:
            return None, {}

        return match, self._ground_truth_for_match(match)

    def load_ground_truth_for_ids(
        self,
        pdf_path: str | Path,
        id_masters: list[str],
    ) -> tuple[ProductMatch, dict[str, Any]]:
        """Load GT from manifest-pinned master IDs without fuzzy rematching."""
        if not id_masters:
            raise ValueError("At least one ID Master is required")
        missing = [value for value in id_masters if value not in self.products_by_master]
        if missing:
            raise KeyError(f"Unknown manifest ID Master value(s): {', '.join(missing)}")
        rows = [self.products_by_master[value] for value in id_masters]
        hospital_rows = [
            row for row in rows if row["ProductType"].casefold() == "hospital"
        ]
        composite = len(rows) > 1
        primary = hospital_rows[0] if hospital_rows else rows[0]
        match = ProductMatch(
            pdf_path=str(pdf_path),
            id_master="+".join(id_masters) if composite else id_masters[0],
            fund_code=primary["FundCode"],
            brand_code=primary["BrandCode"],
            name_master=" + ".join(row["Name Master"].strip() for row in rows),
            product_type="Combined" if composite else primary["ProductType"],
            hospital_tier=(hospital_rows[0].get("HospitalTier") or None) if hospital_rows else None,
            product_item_ids=sorted({
                item_id
                for id_master in id_masters
                for item_id in self.variants_by_master.get(id_master, [])
            }),
            component_id_masters=id_masters if composite else [],
            composite_match=composite,
            match_score=1.0,
            low_confidence_match=False,
            ambiguous_match=False,
        )
        return match, self._ground_truth_for_match(match)

    def _ground_truth_for_match(self, match: ProductMatch) -> dict[str, Any]:

        data: dict[str, Any] = {}
        master_ids = match.component_id_masters or [match.id_master]
        hospital_rows = [
            row for id_master in master_ids
            for row in self.hospital_by_master.get(id_master, [])
        ]
        extras_rows = [
            row for id_master in master_ids
            for row in self.extras_by_master.get(id_master, [])
        ]
        if hospital_rows:
            data["hospital"] = {
                "hospital_tier": match.hospital_tier,
                "clinical_categories": [
                    {"category": row["Title"], "coverage": row["Cover"]} for row in hospital_rows
                ],
            }
            if not match.composite_match:
                data["hospital"]["product_name"] = match.name_master
        if extras_rows:
            shared_groups_comparable = bool(
                getattr(self, "variant_rows", None) and match.product_item_ids
            )
            shared_groups = (
                self._build_shared_group_lookup(match.product_item_ids)
                if shared_groups_comparable
                else {}
            )
            services = []
            for row in extras_rows:
                # extras_benefits is a positive benefit surface. Canonical CSV
                # rows marked Covered=false are useful for exclusion analysis,
                # but are not recall targets for this extractor contract.
                if not self._parse_bool(row["Covered"]):
                    continue
                service = {
                    "service": row["Title"],
                    "covered": self._parse_bool(row["Covered"]),
                    "waiting_period": self._format_waiting_period(
                        row.get("WaitingPeriod", ""), row.get("WaitingPeriodUnit", "")
                    ),
                    "limit_per_person": self._parse_float(row.get("LimitPerPerson", "")),
                    "limit_per_policy": self._parse_float(row.get("LimitPerPolicy", "")),
                }
                # An empty group is meaningful only when the product-to-variant
                # mapping was loaded.  Without that mapping, [] means "unknown",
                # not "this service has no shared limit".
                if shared_groups_comparable:
                    service["shared_with"] = sorted(
                        shared_groups.get(row["Title"], [])
                    )
                services.append(service)
            data["extras"] = {
                "services": services,
            }
            if not match.composite_match:
                data["extras"]["product_name"] = match.name_master
        return data

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
        lowered = [part.casefold() for part in parts]
        categories = {"closed", "combined", "extras", "generalhealth", "hospital"}
        for index, part in enumerate(lowered):
            if part in categories and index > 0:
                candidate = parts[index - 1]
                if candidate.casefold() not in {"pdfs", "raw"}:
                    return candidate
        return None

    @staticmethod
    def _extract_product_type(path: Path) -> str | None:
        lowered_parts = [part.lower() for part in path.parts]
        if "combined" in lowered_parts:
            return "Combined"
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
        document_classification: dict[str, Any] | None = None,
        source_documents: Iterable[ParsedPDF] | None = None,
    ) -> EvaluationReport:
        ground_truth = self._positive_extras_ground_truth(ground_truth)
        phis_classification = (
            deepcopy(document_classification)
            if document_classification is not None
            else default_full_surface_classification(ground_truth)
        )
        phis_document_class = str(phis_classification.get("classification", "partial"))
        adapted_extraction = adapt_final_schema_for_evaluation(extracted.data)
        evaluation_data, comparable_ground_truth = self._scope_to_source_surface(
            adapted_extraction,
            ground_truth,
            phis_classification,
        )
        self._canonicalize_product_section(evaluation_data)
        self._canonicalize_product_section(comparable_ground_truth)
        self._scope_product_fields(evaluation_data, comparable_ground_truth)
        self._normalize_extras_semantics(evaluation_data, comparable_ground_truth)
        extracted_flat = self._flatten(evaluation_data)
        gt_flat = self._flatten(comparable_ground_truth)

        all_evaluation_data = deepcopy(adapted_extraction)
        self._canonicalize_product_section(all_evaluation_data)
        normalization_gt = deepcopy(comparable_ground_truth)
        self._normalize_extras_semantics(all_evaluation_data, normalization_gt)
        all_extracted_flat = self._flatten(all_evaluation_data)
        unscored_extracted_fields = sorted(set(all_extracted_flat) - set(extracted_flat))

        matched_fields = 0
        incorrect_fields: list[str] = []
        present_gt_fields = [key for key in gt_flat if key in extracted_flat]
        for key in present_gt_fields:
            if self._field_values_equal(key, extracted_flat[key], gt_flat[key]):
                matched_fields += 1
            else:
                incorrect_fields.append(key)

        # Precision is limited to fields the source-surface classifier marks as
        # comparable. Extra claims remain available to the evidence auditor and
        # are reported separately instead of being treated as automatic errors.
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
            evaluation_data, comparable_ground_truth, phis_classification
        )

        claim_evidence: dict[str, dict[str, Any]] = {}
        if source_documents is not None:
            claim_evidence = ClaimEvidenceAuditor().audit(
                extracted=all_extracted_flat,
                ground_truth=gt_flat,
                documents=source_documents,
                values_equal=self._values_equal,
            )
        supported_claims = sum(
            item.get("status") == "supported" for item in claim_evidence.values()
        )
        contradicted_claims = sum(
            item.get("status") == "contradicted" for item in claim_evidence.values()
        )
        unverifiable_claims = sum(
            item.get("status") == "unverifiable" for item in claim_evidence.values()
        )
        evaluated_claims = supported_claims + contradicted_claims
        hallucination_rate = (
            contradicted_claims / evaluated_claims if evaluated_claims else None
        )
        hallucinations_by_section = dict(Counter(
            key.split(".", 1)[0]
            for key, item in claim_evidence.items()
            if item.get("status") == "contradicted"
        ))

        missing_fields = [key for key in gt_flat if key not in extracted_flat]
        return EvaluationReport(
            source_path=extracted.source_path,
            source_sha256=extracted.source_sha256,
            product_key=product_key,
            extraction_provider=extracted.provider,
            extraction_model=extracted.model,
            field_precision=precision,
            field_recall=recall,
            field_presence_recall=field_presence_recall,
            value_accuracy=value_accuracy,
            normalization_accuracy=normalization_accuracy,
            coverage=coverage,
            hallucination_rate=hallucination_rate,
            hallucination_evaluated=bool(evaluated_claims),
            canonical_name_recall=normalization_accuracy,
            phis_document_class=phis_document_class,
            phis_classification=phis_classification,
            matched_fields=matched_fields,
            comparable_fields=comparable_count,
            extracted_fields=extracted_count,
            ground_truth_fields=gt_count,
            missing_fields=missing_fields,
            incorrect_fields=incorrect_fields,
            unscored_extracted_fields=unscored_extracted_fields,
            section_metrics=section_metrics,
            hallucinations_by_section=hallucinations_by_section,
            claim_evidence=claim_evidence,
            supported_claims=supported_claims,
            contradicted_claims=contradicted_claims,
            unverifiable_claims=unverifiable_claims,
        )

    @staticmethod
    def _positive_extras_ground_truth(
        ground_truth: dict[str, Any]
    ) -> dict[str, Any]:
        filtered = deepcopy(ground_truth)
        extras = filtered.get("extras")
        if isinstance(extras, dict) and isinstance(extras.get("services"), list):
            services: list[dict[str, Any]] = []
            for service in extras["services"]:
                if not isinstance(service, dict) or service.get("covered") is False:
                    continue
                if service.get("shared_with") in (None, [], ()):
                    service.pop("shared_with", None)
                services.append(service)
            extras["services"] = services
        return filtered

    def aggregate(
        self,
        reports: list[EvaluationReport],
        *,
        total_documents: int | None = None,
        unmatched_documents: int = 0,
        low_confidence_matches: int = 0,
        fallback_documents: int = 0,
        extraction_errors: int = 0,
        duplicate_source_documents: int = 0,
    ) -> dict[str, Any]:
        original_report_count = len(reports)
        reports = self.deduplicate_reports(reports)
        inferred_report_duplicates = original_report_count - len(reports)
        duplicate_source_documents = max(
            duplicate_source_documents,
            inferred_report_duplicates,
        )
        total = (
            max(
                total_documents - inferred_report_duplicates,
                len(reports) + unmatched_documents + extraction_errors,
            )
            if total_documents is not None
            else len(reports)
        )
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
                "supported_claims": 0.0,
                "contradicted_claims": 0.0,
                "unverifiable_claims": 0.0,
                "canonical_name_recall": 0.0,
                "total_documents": float(total),
                "matched_documents": 0.0,
                "unmatched_documents": float(unmatched_documents),
                "match_rate": 0.0,
                "low_confidence_matches": float(low_confidence_matches),
                "fallback_documents": float(fallback_documents),
                "extraction_errors": float(extraction_errors),
                "duplicate_source_documents": float(duplicate_source_documents),
                "product_accuracy": None,
                "product_presence_recall": None,
                "product_end_to_end_accuracy": None,
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
        supported_claims = sum(report.supported_claims for report in reports)
        contradicted_claims = sum(report.contradicted_claims for report in reports)
        unverifiable_claims = sum(report.unverifiable_claims for report in reports)
        evaluated_claims = supported_claims + contradicted_claims
        summary: dict[str, Any] = {
            "field_precision": mean(report.field_precision for report in reports),
            "field_recall": mean(report.field_recall for report in reports),
            "field_presence_recall": mean(report.field_presence_recall for report in reports),
            "value_accuracy": mean(report.value_accuracy for report in reports),
            "normalization_accuracy": mean(report.normalization_accuracy for report in reports),
            "coverage": mean(report.coverage for report in reports),
            "hallucination_rate": (
                contradicted_claims / evaluated_claims if evaluated_claims else None
            ),
            "hallucination_evaluated_documents": float(sum(report.hallucination_evaluated for report in reports)),
            "supported_claims": float(supported_claims),
            "contradicted_claims": float(contradicted_claims),
            "unverifiable_claims": float(unverifiable_claims),
            "canonical_name_recall": mean(report.canonical_name_recall for report in reports),
            "total_documents": float(total),
            "matched_documents": float(len(reports)),
            "unmatched_documents": float(unmatched_documents),
            "match_rate": len(reports) / total if total else 0.0,
            "low_confidence_matches": float(low_confidence_matches),
            "fallback_documents": float(fallback_documents),
            "extraction_errors": float(extraction_errors),
            "duplicate_source_documents": float(duplicate_source_documents),
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

    @staticmethod
    def deduplicate_reports(
        reports: list[EvaluationReport],
    ) -> list[EvaluationReport]:
        """Keep one evaluation report per known PDF content hash.

        Reports without a hash are retained because their identity cannot be
        proven.  This also keeps hand-built/legacy reports backwards compatible.
        """
        unique: list[EvaluationReport] = []
        seen_hashes: set[str] = set()
        for report in reports:
            source_hash = report.source_sha256
            if source_hash and source_hash in seen_hashes:
                continue
            if source_hash:
                seen_hashes.add(source_hash)
            unique.append(report)
        return unique

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
            "canonical_name_recall", "product_accuracy", "product_presence_recall",
            "product_end_to_end_accuracy", "hospital_category_recall", "hospital_coverage_accuracy",
            "extras_service_precision", "extras_service_recall",
            "extras_waiting_period_accuracy", "extras_limit_accuracy",
        )
        return {key: summary.get(key) for key in keys}

    def _micro_summary(self, reports: list[EvaluationReport]) -> dict[str, float | None]:
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
        return {
            "field_precision": matched / extracted if extracted else 0.0,
            "field_recall": matched / ground_truth if ground_truth else 0.0,
            "field_presence_recall": comparable / ground_truth if ground_truth else 0.0,
            "value_accuracy": matched / comparable if comparable else 0.0,
            "canonical_name_recall": mean(
                report.canonical_name_recall for report in reports
            ) if reports else 0.0,
            "product_accuracy": self._ratio_sum(product, "matched", "comparable"),
            "product_presence_recall": self._ratio_sum(product, "comparable", "ground_truth"),
            "product_end_to_end_accuracy": self._ratio_sum(product, "matched", "ground_truth"),
            "hospital_category_precision": self._ratio_sum(hospital, "matched_categories", "extracted_categories"),
            "hospital_category_recall": self._ratio_sum(hospital, "matched_categories", "ground_truth_categories"),
            "hospital_coverage_accuracy": self._ratio_sum(hospital, "coverage_matches", "coverage_comparable"),
            "extras_service_precision": self._ratio_sum(extras, "matched_services", "extracted_services"),
            "extras_service_recall": self._ratio_sum(extras, "matched_services", "ground_truth_services"),
            "extras_waiting_period_accuracy": self._ratio_sum(extras, "waiting_period_matches", "waiting_period_comparable"),
            "extras_limit_accuracy": self._ratio_sum(extras, "limit_matches", "limit_comparable"),
        }

    @staticmethod
    def _ratio_sum(
        rows: list[dict[str, Any]], numerator: str, denominator: str
    ) -> float | None:
        comparable_rows = [
            row for row in rows if isinstance(row.get(denominator), int | float)
        ]
        denominator_total = sum(int(row[denominator]) for row in comparable_rows)
        if not comparable_rows or denominator_total == 0:
            return None
        return sum(int(row.get(numerator, 0)) for row in comparable_rows) / denominator_total

    def _aggregate_section_metrics(
        self, reports: list[EvaluationReport]
    ) -> dict[str, float | None]:
        metric_paths = {
            "product_accuracy": ("product", "accuracy"),
            "product_presence_recall": ("product", "presence_recall"),
            "product_end_to_end_accuracy": ("product", "end_to_end_accuracy"),
            "hospital_category_recall": ("hospital", "category_recall"),
            "hospital_coverage_accuracy": ("hospital", "coverage_accuracy"),
            "extras_service_precision": ("extras", "service_precision"),
            "extras_service_recall": ("extras", "service_recall"),
            "extras_waiting_period_accuracy": ("extras", "waiting_period_accuracy"),
            "extras_limit_accuracy": ("extras", "limit_accuracy"),
        }
        summary: dict[str, float | None] = {}
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
            summary[output_key] = mean(values) if values else None
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
                                    flattened[f"{full_key}.{normalized_key}.{sub_key}"] = self._normalize_value(
                                        sub_value,
                                        semantic_key=sub_key,
                                    )
                else:
                    flattened[full_key] = tuple(
                        self._normalize_value(v, semantic_key=key)
                        for v in value
                        if v is not None
                    )
            else:
                flattened[full_key] = self._normalize_value(
                    value,
                    semantic_key=key,
                )
        return flattened

    def _normalization_accuracy(self, extracted: dict[str, Any], ground_truth: dict[str, Any]) -> float:
        extracted_names = {self._normalize_string(name) for name in self._collect_names(extracted)}
        gt_names = {self._normalize_string(name) for name in self._collect_names(ground_truth)}
        if not gt_names:
            return 1.0
        return len(extracted_names & gt_names) / len(gt_names)

    def _section_metrics(
        self,
        extracted: dict[str, Any],
        ground_truth: dict[str, Any],
        classification: dict[str, Any],
    ) -> dict[str, Any]:
        sections = classification.get("sections", {})
        hospital_profile = sections.get("hospital", {})
        extras_profile = sections.get("extras", {})
        return {
            "product": self._product_metrics(extracted, ground_truth),
            "hospital": self._section_metric_or_na(
                section_name="hospital",
                profile=hospital_profile,
                has_items=self._has_gt_items(
                    ground_truth, "hospital", "clinical_categories"
                ),
                metric=lambda: self._hospital_metrics(extracted, ground_truth),
            ),
            "extras": self._section_metric_or_na(
                section_name="extras",
                profile=extras_profile,
                has_items=self._has_gt_items(ground_truth, "extras", "services"),
                metric=lambda: self._extras_metrics(extracted, ground_truth),
            ),
        }

    @staticmethod
    def _section_metric_or_na(
        *,
        section_name: str,
        profile: dict[str, Any],
        has_items: bool,
        metric: Any,
    ) -> dict[str, Any] | None:
        section_class = profile.get("classification")
        if section_class == "non_standard":
            return {
                "comparable": False,
                "non_comparable_reason": "non_standard_source_surface",
                (
                    "category_recall" if section_name == "hospital" else "service_recall"
                ): None,
            }
        if not has_items and profile:
            return {
                "comparable": False,
                "non_comparable_reason": "no_source_visible_canonical_items",
                (
                    "category_recall" if section_name == "hospital" else "service_recall"
                ): None,
            }
        return metric() if has_items else None

    @staticmethod
    def _has_gt_items(
        ground_truth: dict[str, Any],
        section_name: str,
        item_name: str,
    ) -> bool:
        section = ground_truth.get(section_name)
        return isinstance(section, dict) and bool(section.get(item_name))

    def _scope_to_source_surface(
        self,
        extracted: dict[str, Any],
        ground_truth: dict[str, Any],
        classification: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        scoped_extracted = deepcopy(extracted)
        scoped_gt = deepcopy(ground_truth)
        sections = classification.get("sections", {})
        for section_name, item_name, key_name in (
            ("hospital", "clinical_categories", "category"),
            ("extras", "services", "service"),
        ):
            profile = sections.get(section_name)
            if not isinstance(profile, dict):
                continue
            section_class = profile.get("classification")
            visible = {
                self._normalize_string(str(name))
                for name in profile.get("visible_canonical_items", [])
            }
            if section_class == "non_standard" or (
                section_class == "partial" and not visible
            ):
                self._drop_section_items(scoped_extracted, section_name, item_name)
                self._drop_section_items(scoped_gt, section_name, item_name)
            elif section_class == "partial":
                for payload in (scoped_extracted, scoped_gt):
                    section = payload.get(section_name)
                    if isinstance(section, dict):
                        section[item_name] = [
                            item
                            for item in section.get(item_name, [])
                            if self._item_is_visible(item, key_name, visible)
                        ]
            visible_fields = profile.get("visible_fields")
            if isinstance(visible_fields, dict):
                for payload in (scoped_extracted, scoped_gt):
                    self._scope_item_fields(
                        payload,
                        section_name=section_name,
                        item_name=item_name,
                        key_name=key_name,
                        visible_fields=visible_fields,
                    )
        return scoped_extracted, scoped_gt

    def _scope_item_fields(
        self,
        payload: dict[str, Any],
        *,
        section_name: str,
        item_name: str,
        key_name: str,
        visible_fields: dict[str, Any],
    ) -> None:
        section = payload.get(section_name)
        if not isinstance(section, dict) or not isinstance(section.get(item_name), list):
            return
        normalized_visibility = {
            self._normalize_string(name): set(values)
            for name, values in visible_fields.items()
            if isinstance(values, list)
        }
        scoped_items: list[dict[str, Any]] = []
        for item in section[item_name]:
            if not isinstance(item, dict) or not item.get(key_name):
                continue
            canonical_names = (
                canonical_extras_services(item[key_name])
                if key_name == "service"
                else [canonical_hospital_category(item[key_name])]
            )
            allowed: set[str] = {key_name}
            for canonical in canonical_names:
                allowed.update(
                    normalized_visibility.get(self._normalize_string(canonical), set())
                )
            if "limit_amount" in allowed:
                allowed.update({"limit_per_person", "limit_per_policy", "limit_amount"})
            scoped_items.append({key: value for key, value in item.items() if key in allowed})
        section[item_name] = scoped_items

    def _normalize_extras_semantics(
        self,
        extracted: dict[str, Any],
        ground_truth: dict[str, Any],
    ) -> None:
        """Normalize known GT representation quirks before field comparison.

        The labelled extras table does not reliably preserve whether an annual
        limit is per person or per policy, so the monetary value is comparable
        while that basis is not.  Shared-limit membership is comparable only
        when variant mapping was available while constructing GT.
        """
        gt_services = self._extras_services(ground_truth)
        for service in gt_services:
            # In the labelled extras export, a present-but-null waiting period
            # denotes no wait rather than an unknown value.
            if "waiting_period" in service and service["waiting_period"] is None:
                service["waiting_period"] = "0 Month"
        extracted_by_name = self._keyed_items(
            self._extras_services(extracted), "service"
        )
        gt_by_name = self._keyed_items(gt_services, "service")
        for name in set(extracted_by_name) & set(gt_by_name):
            extracted_service = extracted_by_name[name]
            gt_service = gt_by_name[name]
            extracted_shared = self._canonical_shared_members(extracted_service)
            gt_shared = self._canonical_shared_members(gt_service)
            # A combined/shared group total is not the same business fact as an
            # individual service limit. Compare the monetary value only when
            # both sides identify the same shared membership; the membership
            # field itself remains comparable and can still report a mismatch.
            if bool(extracted_shared) != bool(gt_shared) or (
                extracted_shared and extracted_shared != gt_shared
            ):
                self._drop_limit_values(extracted_service)
                self._drop_limit_values(gt_service)
        for payload in (extracted, ground_truth):
            for service in self._extras_services(payload):
                limit_values = [
                    service.get(key)
                    for key in (
                        "limit_amount",
                        "limit_per_person",
                        "limit_per_policy",
                    )
                    if service.get(key) not in (None, "")
                ]
                if limit_values:
                    distinct: list[Any] = []
                    for value in limit_values:
                        if not any(self._values_equal(value, prior) for prior in distinct):
                            distinct.append(value)
                    service["limit_amount"] = (
                        distinct[0] if len(distinct) == 1 else distinct
                    )
                service.pop("limit_per_person", None)
                service.pop("limit_per_policy", None)
                if not self._canonical_shared_members(service):
                    service.pop("shared_with", None)

    @staticmethod
    def _drop_limit_values(service: dict[str, Any]) -> None:
        for key in ("limit_amount", "limit_per_person", "limit_per_policy"):
            service.pop(key, None)

    @staticmethod
    def _canonical_shared_members(service: dict[str, Any]) -> frozenset[str]:
        values = service.get("shared_with")
        if not isinstance(values, list):
            return frozenset()
        return frozenset(
            canonical
            for value in values
            for canonical in canonical_extras_services(value)
        )

    @staticmethod
    def _extras_services(payload: dict[str, Any]) -> list[dict[str, Any]]:
        extras = payload.get("extras")
        if not isinstance(extras, dict):
            return []
        services = extras.get("services")
        if not isinstance(services, list):
            return []
        return [service for service in services if isinstance(service, dict)]

    @staticmethod
    def _drop_section_items(
        payload: dict[str, Any], section_name: str, item_name: str
    ) -> None:
        section = payload.get(section_name)
        if isinstance(section, dict):
            section.pop(item_name, None)

    def _item_is_visible(
        self, item: Any, key_name: str, visible: set[str]
    ) -> bool:
        if not isinstance(item, dict) or not item.get(key_name):
            return False
        canonical_names = (
            canonical_extras_services(item[key_name])
            if key_name == "service"
            else [canonical_hospital_category(item[key_name])]
        )
        return any(self._normalize_string(str(name)) in visible for name in canonical_names)

    def _product_metrics(self, extracted: dict[str, Any], ground_truth: dict[str, Any]) -> dict[str, Any]:
        extracted_product = self._product_fields(extracted)
        gt_product = self._product_fields(ground_truth)
        comparable = [key for key in gt_product if key in extracted_product]
        matches = sum(
            1 for key in comparable
            if self._field_values_equal(
                key, extracted_product[key], gt_product[key]
            )
        )
        result: dict[str, Any] = {
            "matched": matches,
            "comparable": len(comparable),
            "ground_truth": len(gt_product),
            "accuracy": matches / len(comparable) if comparable else None,
            "presence_recall": len(comparable) / len(gt_product) if gt_product else 0.0,
            "end_to_end_accuracy": matches / len(gt_product) if gt_product else None,
        }
        for key in ("product_name", "hospital_tier"):
            present = key in extracted_product and key in gt_product
            result[f"{key}_present"] = int(present)
            result[f"{key}_match"] = int(
                present
                and self._field_values_equal(
                    key, extracted_product[key], gt_product[key]
                )
            )
        return result

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
        coverage_keys = [
            key for key in common if gt_categories[key].get("coverage") is not None
        ]
        matched_coverage = sum(
            1 for key in coverage_keys
            if self._values_equal(extracted_categories[key].get("coverage"), gt_categories[key].get("coverage"))
        )
        return {
            "matched_categories": len(common),
            "extracted_categories": len(extracted_keys),
            "ground_truth_categories": len(gt_keys),
            "coverage_matches": matched_coverage,
            "coverage_comparable": len(coverage_keys),
            "category_precision": len(common) / len(extracted_keys) if extracted_keys else 0.0,
            "category_recall": len(common) / len(gt_keys) if gt_keys else 0.0,
            "coverage_accuracy": matched_coverage / len(coverage_keys) if coverage_keys else None,
        }

    def _extras_metrics(self, extracted: dict[str, Any], ground_truth: dict[str, Any]) -> dict[str, Any]:
        extracted_services = self._keyed_items(extracted.get("extras", {}).get("services", []), "service")
        gt_services = self._keyed_items(ground_truth.get("extras", {}).get("services", []), "service")
        extracted_keys = set(extracted_services)
        gt_keys = set(gt_services)
        common = extracted_keys & gt_keys
        covered_keys = [
            key for key in common if gt_services[key].get("covered") is not None
        ]
        covered_matches = sum(
            1 for key in covered_keys
            if self._values_equal(extracted_services[key].get("covered"), gt_services[key].get("covered"))
        )
        waiting_keys = [key for key in common if gt_services[key].get("waiting_period") is not None]
        waiting_matches = sum(
            1 for key in waiting_keys
            if self._waiting_values_equal(
                extracted_services[key].get("waiting_period"),
                gt_services[key].get("waiting_period"),
            )
        )
        limit_keys = [
            key for key in common
            if gt_services[key].get("limit_amount") is not None
        ]
        limit_matches = sum(
            1 for key in limit_keys
            if self._limit_values_equal(
                extracted_services[key].get("limit_amount"),
                gt_services[key].get("limit_amount"),
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
            "covered_comparable": len(covered_keys),
            "covered_accuracy": covered_matches / len(covered_keys) if covered_keys else None,
            "waiting_period_accuracy": waiting_matches / len(waiting_keys) if waiting_keys else None,
            "limit_accuracy": limit_matches / len(limit_keys) if limit_keys else None,
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

    @classmethod
    def _canonicalize_product_section(cls, payload: dict[str, Any]) -> None:
        """Move product identity fields into one section before flattening.

        The final extraction schema uses top-level fields while legacy GT stores
        them inside hospital/extras.  Canonicalizing both sides prevents adapter
        presence (for example a missing category list) from dropping product
        values or counting the same value twice.
        """
        product = cls._product_fields(payload)
        for section_name in ("hospital", "extras"):
            section = payload.get(section_name)
            if isinstance(section, dict):
                section.pop("product_name", None)
                section.pop("hospital_tier", None)
        for key in ("product_name", "product_type", "hospital_tier"):
            payload.pop(key, None)
        if product:
            payload["product"] = product
        else:
            payload.pop("product", None)

    @staticmethod
    def _scope_product_fields(
        extracted: dict[str, Any], ground_truth: dict[str, Any]
    ) -> None:
        extracted_product = extracted.get("product")
        gt_product = ground_truth.get("product")
        if not isinstance(extracted_product, dict):
            return
        comparable_keys = set(gt_product) if isinstance(gt_product, dict) else set()
        extracted["product"] = {
            key: value
            for key, value in extracted_product.items()
            if key in comparable_keys
        }
        if not extracted["product"]:
            extracted.pop("product", None)

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
        if isinstance(left, frozenset) and isinstance(right, frozenset):
            if len(left) == 1 or len(right) == 1:
                return bool(left & right)
            return left == right
        left_waiting = self._waiting_period_options(left)
        right_waiting = self._waiting_period_options(right)
        if left_waiting is not None and right_waiting is not None:
            if len(left_waiting) == 1 or len(right_waiting) == 1:
                return bool(left_waiting & right_waiting)
            return left_waiting == right_waiting

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

    def _field_values_equal(self, key: str, left: Any, right: Any) -> bool:
        semantic_key = key.rsplit(".", 1)[-1]
        if semantic_key == "product_name":
            return canonical_product_name(left) == canonical_product_name(right)
        if semantic_key == "limit_amount":
            return self._limit_values_equal(left, right)
        return self._values_equal(left, right)

    def _limit_values_equal(self, left: Any, right: Any) -> bool:
        """Compare individual limits without conflating independent scopes.

        A singleton amount may match either value when one GT row has lost its
        per-person/per-policy basis. When both sides retain multiple independent
        amounts, every amount must match. Tiered schedules are reduced to their
        entry tier by the adapter before reaching this comparison.
        """
        left_values = self._limit_number_options(left)
        right_values = self._limit_number_options(right)
        if left_values is None or right_values is None:
            return self._values_equal(left, right)
        if len(left_values) == 1 or len(right_values) == 1:
            return bool(left_values & right_values)
        return left_values == right_values

    def _limit_number_options(self, value: Any) -> frozenset[Decimal] | None:
        raw_values = value if isinstance(value, (list, tuple)) else (value,)
        numbers = {
            number
            for item in raw_values
            if (number := self._normalized_number(item)) is not None
        }
        return frozenset(numbers) if numbers else None

    def _normalize_value(
        self,
        value: Any,
        *,
        semantic_key: str | None = None,
    ) -> Any:
        if isinstance(value, dict):
            return tuple(
                (
                    self._normalize_string(str(key)),
                    self._normalize_value(item, semantic_key=semantic_key),
                )
                for key, item in value.items()
            )
        if isinstance(value, list):
            normalized_items = [
                self._normalize_value(item, semantic_key=semantic_key)
                for item in value
            ]
            if semantic_key == "limit_amount":
                return tuple(normalized_items)
            return tuple(sorted(normalized_items, key=repr))
        if isinstance(value, str):
            if semantic_key == "product_name":
                return canonical_product_name(value)
            waiting_options = self._waiting_period_options(
                value,
                force=semantic_key == "waiting_period",
            )
            if semantic_key == "waiting_period" and waiting_options is not None:
                return waiting_options
            number = self._normalized_number(value)
            return number if number is not None else self._normalize_string(value)
        return value

    def _waiting_values_equal(self, left: Any, right: Any) -> bool:
        left_options = self._waiting_period_options(left, force=True)
        right_options = self._waiting_period_options(right, force=True)
        if left_options is None or right_options is None:
            return self._values_equal(left, right)
        if len(left_options) == 1 or len(right_options) == 1:
            return bool(left_options & right_options)
        return left_options == right_options

    @staticmethod
    def _waiting_period_options(
        value: Any,
        *,
        force: bool = False,
    ) -> frozenset[Decimal] | None:
        if not isinstance(value, str):
            return None
        normalized = value.casefold()
        values: set[Decimal] = set()
        has_no_wait = bool(re.search(
            r"\b(?:none|no\s+(?:waiting\s+period|wait))\b",
            normalized,
        ))
        if has_no_wait:
            values.add(Decimal(0))
        unit_days = {
            "day": Decimal(1),
            "week": Decimal(7),
            "month": Decimal(30),
            "year": Decimal(365),
        }
        duration_pattern = r"\b(\d+(?:\.\d+)?)\s*(day|week|month|year)s?\b"
        durations = re.findall(duration_pattern, normalized)
        for number, unit in durations:
            values.add(Decimal(number) * unit_days[unit])
        if not force and not has_no_wait and len(durations) == 1:
            residue = re.sub(duration_pattern, " ", normalized)
            residue_tokens = set(re.findall(r"[a-z]+", residue))
            if residue_tokens - {"waiting", "period", "wait", "varies", "variable"}:
                return None
        return frozenset(values) if values else None

    def _tiered_limit_baseline(self, value: Any) -> Decimal | None:
        if isinstance(value, dict):
            for item in value.values():
                baseline = self._tiered_limit_baseline(item)
                if baseline is not None:
                    return baseline
            return None
        if isinstance(value, (list, tuple)):
            for item in value:
                baseline = self._tiered_limit_baseline(item)
                if baseline is not None:
                    return baseline
            return None
        return self._normalized_number(value)

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
