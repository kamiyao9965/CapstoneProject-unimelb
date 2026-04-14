from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from src.config import AppConfig, load_config
from src.models import Evidence, ExtractionInput, ExtractionResult, VerticalSchema
from src.pipeline.normalizer import ServiceNormalizer
from src.schema.loader import SchemaLoader

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional dependency
    OpenAI = None


class LLMExtractor:
    """
    Schema-driven extractor with a private-health heuristic fallback.
    """

    def __init__(
        self,
        schema: VerticalSchema,
        client: Any | None = None,
        config: AppConfig | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        self.schema = schema
        self.config = config or load_config()
        self.provider = provider or self.config.default_llm_provider
        self.model = model or self.config.default_llm_model
        self.client = client or self._build_client()
        self.loader = SchemaLoader()
        self.normalizer = ServiceNormalizer(schema)

    def extract(self, extraction_input: ExtractionInput) -> ExtractionResult:
        if self.provider == "openai" and self.client is not None:
            try:
                return self._extract_with_openai(extraction_input)
            except Exception as exc:  # pragma: no cover - network/runtime dependent
                fallback = self._heuristic_extract(extraction_input)
                fallback.warnings.append(f"OpenAI extraction failed: {exc}")
                return fallback
        return self._heuristic_extract(extraction_input)

    def _build_client(self) -> Any | None:
        if self.provider == "openai" and self.config.openai_api_key and OpenAI is not None:
            return OpenAI(api_key=self.config.openai_api_key)
        return None

    def _extract_with_openai(self, extraction_input: ExtractionInput) -> ExtractionResult:
        system_prompt = self._build_system_prompt(self.schema)
        tool_schema = self.loader.build_json_schema(self.schema)
        user_text = self._build_user_prompt(extraction_input)
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "submit_extraction",
                        "description": "Return extracted structured data for the insurance product.",
                        "parameters": tool_schema,
                        "strict": True,
                    },
                }
            ],
            tool_choice={"type": "function", "function": {"name": "submit_extraction"}},
            parallel_tool_calls=False,
        )
        tool_calls = response.choices[0].message.tool_calls or []
        for tool_call in tool_calls:
            if tool_call.function.name == "submit_extraction":
                return ExtractionResult(
                    vertical=self.schema.vertical,
                    schema_version=self.schema.version,
                    source_path=extraction_input.source_document.path,
                    provider="openai",
                    model=self.model,
                    data=json.loads(tool_call.function.arguments),
                )
        raise RuntimeError("OpenAI response did not contain the submit_extraction tool output.")

    def _build_system_prompt(self, schema: VerticalSchema) -> str:
        return (
            f"You are extracting structured data from an Australian {schema.vertical} insurance product PDF.\n"
            "CANONICAL FIELD NAMES you must use:\n"
            f"{self.loader.build_field_definitions(schema)}\n\n"
            "VALID ENUM VALUES:\n"
            f"{self.loader.build_enum_constraints(schema)}\n\n"
            "RULES:\n"
            "- Only extract information explicitly stated in the document.\n"
            "- Use null for any field not mentioned.\n"
            "- Normalize service names to the canonical list.\n"
            "- For shared limits, list all services that share the limit in shared_with.\n"
        )

    def _build_user_prompt(self, extraction_input: ExtractionInput) -> str:
        text = extraction_input.text[:14000]
        if extraction_input.mode == "table_assisted":
            tables = json.dumps(extraction_input.tables[:8], ensure_ascii=False)
            return f"FULL TEXT:\n{text}\n\nTABLES:\n{tables}"
        return f"FULL TEXT:\n{text}"

    def _heuristic_extract(self, extraction_input: ExtractionInput) -> ExtractionResult:
        if self.schema.vertical == "private_health":
            data, evidences, normalizations, warnings = self._extract_private_health(extraction_input)
            return ExtractionResult(
                vertical=self.schema.vertical,
                schema_version=self.schema.version,
                source_path=extraction_input.source_document.path,
                provider="heuristic",
                model="rule-based-private-health",
                data=data,
                evidences=evidences,
                normalized_names=normalizations,
                warnings=warnings,
            )

        data = {"document_summary": self._summarize_generic(extraction_input.text)}
        return ExtractionResult(
            vertical=self.schema.vertical,
            schema_version=self.schema.version,
            source_path=extraction_input.source_document.path,
            provider="heuristic",
            model="rule-based-generic",
            data=data,
            warnings=["Generic heuristic fallback used; configure OpenAI for schema-driven extraction."],
        )

    def _extract_private_health(
        self, extraction_input: ExtractionInput
    ) -> tuple[dict[str, Any], dict[str, list[Evidence]], list[Any], list[str]]:
        text = extraction_input.text
        lines = [self._clean_line(line) for line in text.splitlines()]
        hospital_data, hospital_evidence = self._extract_hospital(lines)
        extras_data, extras_evidence, normalizations = self._extract_extras(lines)
        data: dict[str, Any] = {}
        evidences: dict[str, list[Evidence]] = {}
        warnings: list[str] = []
        if hospital_data:
            data["hospital"] = hospital_data
            evidences.update(hospital_evidence)
        if extras_data:
            data["extras"] = extras_data
            evidences.update(extras_evidence)
        if not data:
            warnings.append("No private health sections were confidently extracted from the document.")
        return data, evidences, normalizations, warnings

    def _extract_hospital(self, lines: list[str]) -> tuple[dict[str, Any], dict[str, list[Evidence]]]:
        if self.schema.hospital is None:
            return {}, {}

        alias_map = self._alias_lookup(self.schema.hospital.aliases)
        tier = self._extract_hospital_tier(lines)
        product_name = self._extract_product_name(lines)
        section_hits: dict[str, set[str]] = {"Covered": set(), "Restricted": set(), "Excluded": set()}
        evidence_map: dict[str, list[Evidence]] = defaultdict(list)

        for index, line in enumerate(lines):
            if not line:
                continue
            lowered = line.lower()
            inline_status = self._infer_status_from_line(lowered)
            matched_categories = self._find_canonical_matches(line, alias_map)
            for category in matched_categories:
                if inline_status is None:
                    continue
                section_hits[inline_status].add(category)
                evidence_map[f"hospital.clinical_categories.{category}"].append(
                    Evidence(text=line, page=self._guess_page(index, lines))
                )

        explicit_sections = {
            "Restricted": self._collect_section_items(
                lines, {"restricted cover for", "restricted cover"}, alias_map
            ),
            "Excluded": self._collect_section_items(
                lines,
                {"does not include cover for", "services not included", "not included"},
                alias_map,
            ),
            "Covered": self._collect_section_items(
                lines,
                {"includes cover for", "policy includes cover for", "hospital includes"},
                alias_map,
            ),
        }
        for status, categories in explicit_sections.items():
            for category, evidence_text in categories.items():
                section_hits[status].add(category)
                evidence_map[f"hospital.clinical_categories.{category}"].append(
                    Evidence(text=evidence_text, page=1)
                )

        statuses: list[dict[str, str | None]] = []
        for category in self.schema.hospital.canonical_categories:
            coverage = None
            for status in ("Excluded", "Restricted", "Covered"):
                if category in section_hits[status]:
                    coverage = status
                    break
            statuses.append({"category": category, "coverage": coverage})

        if not any(item["coverage"] for item in statuses) and tier is None:
            return {}, {}

        data = {
            "product_name": product_name,
            "hospital_tier": tier,
            "clinical_categories": statuses,
        }
        if product_name:
            evidence_map["hospital.product_name"].append(Evidence(text=product_name, page=1))
        if tier:
            evidence_map["hospital.hospital_tier"].append(Evidence(text=tier, page=1))
        return data, dict(evidence_map)

    def _extract_extras(
        self, lines: list[str]
    ) -> tuple[dict[str, Any], dict[str, list[Evidence]], list[Any]]:
        if self.schema.extras is None:
            return {}, {}, []

        alias_map = self._alias_lookup(self.schema.extras.aliases)
        service_blocks: dict[str, list[str]] = defaultdict(list)
        service_order: list[str] = []
        current_service: str | None = None

        for line in lines:
            if not line:
                continue
            matched = self._find_canonical_matches(line, alias_map)
            if matched:
                current_service = matched[0]
                if current_service not in service_order:
                    service_order.append(current_service)
                service_blocks[current_service].append(line)
                continue
            if current_service:
                service_blocks[current_service].append(line)

        matched_service_count = sum(1 for block in service_blocks.values() if block)
        extras_signal = any("extra" in line.lower() for line in lines[:60])
        if matched_service_count < 2 and not extras_signal:
            return {}, {}, []

        services: list[dict[str, Any]] = []
        evidence_map: dict[str, list[Evidence]] = defaultdict(list)
        normalizations = []
        pending_combined_service: str | None = None

        ordered_services = service_order + [
            service for service in self.schema.extras.canonical_services if service not in service_order
        ]
        for service in ordered_services:
            block = service_blocks.get(service, [])
            joined = " ".join(block)
            covered = bool(block)
            waiting_period = self._extract_waiting_period(joined)
            limit_values = self._extract_limit_values(joined)
            shared_with: list[str] = []
            has_combined_limit = "combined limit" in joined.lower()
            if pending_combined_service and covered:
                shared_with = [pending_combined_service]
                pending_combined_service = None
            elif has_combined_limit and covered:
                pending_combined_service = service

            if block:
                evidence_map[f"extras.services.{service}"].append(Evidence(text=block[0], page=1))
            normalizations.append(self.normalizer.normalize(service))
            services.append(
                {
                    "service": service,
                    "covered": covered,
                    "waiting_period": waiting_period,
                    "limit_per_person": limit_values.get("per_person"),
                    "limit_per_policy": limit_values.get("per_policy"),
                    "shared_with": shared_with,
                }
            )

        lookup = {item["service"]: item for item in services}
        for item in services:
            for other in item["shared_with"]:
                if item["service"] not in lookup[other]["shared_with"]:
                    lookup[other]["shared_with"].append(item["service"])

        product_name = self._extract_product_name(lines)
        data = {"product_name": product_name, "services": services}
        if product_name:
            evidence_map["extras.product_name"].append(Evidence(text=product_name, page=1))
        return data, dict(evidence_map), normalizations

    def _extract_product_name(self, lines: list[str]) -> str | None:
        ignore_prefixes = (
            "product summary",
            "important information",
            "features",
            "inclusions",
            "hospital includes",
        )
        candidates = []
        for line in lines[:18]:
            if not line:
                continue
            lowered = line.lower()
            if any(lowered.startswith(prefix) for prefix in ignore_prefixes):
                continue
            if len(line.split()) >= 2 and re.search(r"[A-Za-z]", line):
                score = 0
                if line.upper() == line and 2 <= len(line.split()) <= 8:
                    score += 4
                if re.search(r"\b(gold|silver|bronze|basic|extras|hospital)\b", lowered):
                    score += 3
                if len(line) <= 40:
                    score += 2
                if not line.endswith("."):
                    score += 1
                candidates.append((score, line))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (-item[0], item[1]))
        return candidates[0][1]

    def _extract_hospital_tier(self, lines: list[str]) -> str | None:
        joined = " ".join(lines[:40]).lower()
        patterns = {
            "SilverPlus": r"silver\s*\+|silver plus",
            "BronzePlus": r"bronze\s*\+|bronze plus",
            "BasicPlus": r"basic\s*\+|basic plus",
            "Gold": r"\bgold\b",
            "Silver": r"\bsilver\b",
            "Bronze": r"\bbronze\b",
            "Basic": r"\bbasic\b",
        }
        for tier, pattern in patterns.items():
            if re.search(pattern, joined):
                return tier
        return None

    def _infer_status_from_line(self, lowered: str) -> str | None:
        if "restricted cover for" in lowered:
            return "Restricted"
        if "does not include cover for" in lowered or "services not included" in lowered or " excluded " in f" {lowered} ":
            return "Excluded"
        if "covers services such as" in lowered or "includes cover for" in lowered:
            return "Covered"
        return None

    def _find_canonical_matches(self, line: str, alias_map: dict[str, list[str]]) -> list[str]:
        lowered = f" {self._normalize_text(line)} "
        matches: list[str] = []
        for canonical, aliases in alias_map.items():
            for alias in aliases:
                alias_norm = self._normalize_text(alias)
                if alias_norm and alias_norm in lowered:
                    matches.append(canonical)
                    break
        return matches

    def _alias_lookup(self, aliases: dict[str, list[str]]) -> dict[str, list[str]]:
        expanded: dict[str, list[str]] = {}
        for canonical, values in aliases.items():
            expanded[canonical] = sorted(set(values + [canonical, self._decamelize(canonical)]), key=str.lower)
        return expanded

    def _collect_section_items(
        self,
        lines: list[str],
        heading_markers: set[str],
        alias_map: dict[str, list[str]],
    ) -> dict[str, str]:
        collected: dict[str, str] = {}
        index = 0
        while index < len(lines):
            lowered = lines[index].lower()
            if any(marker in lowered for marker in heading_markers):
                index += 1
                while index < len(lines):
                    line = lines[index]
                    if not line:
                        break
                    normalized_line = line.lower()
                    if ":" in line or self._looks_like_heading(line):
                        break
                    if len(line.split()) > 8 and not self._find_canonical_matches(line, alias_map):
                        break
                    for category in self._find_canonical_matches(line, alias_map):
                        collected[category] = line
                    index += 1
                continue
            index += 1
        return collected

    def _extract_waiting_period(self, text: str) -> str | None:
        match = re.search(r"(\d+)\s*(day|days|month|months|year|years)", text, flags=re.IGNORECASE)
        if not match:
            return None
        number, unit = match.groups()
        unit = unit.lower()
        if unit.endswith("s"):
            unit = unit[:-1]
        return f"{number} {unit.capitalize()}"

    def _extract_limit_values(self, text: str) -> dict[str, float | None]:
        amounts = [self._parse_amount(match) for match in re.findall(r"\$\s*[\d,]+(?:\.\d{2})?", text)]
        result = {"per_person": None, "per_policy": None}
        if not amounts:
            return result
        if len(amounts) == 1:
            result["per_person"] = amounts[0]
            return result

        lowered = text.lower()
        if "per policy" in lowered or "policy" in lowered:
            result["per_person"] = amounts[0]
            result["per_policy"] = amounts[1] if len(amounts) > 1 else None
        else:
            result["per_person"] = amounts[0]
            result["per_policy"] = amounts[1] if len(amounts) > 1 else None
        return result

    def _summarize_generic(self, text: str) -> dict[str, Any]:
        lines = [self._clean_line(line) for line in text.splitlines() if self._clean_line(line)]
        return {
            "title": lines[0] if lines else None,
            "headline_lines": lines[:12],
            "currency_values": sorted(set(re.findall(r"\$\s*[\d,]+(?:\.\d{2})?", text)))[:20],
        }

    @staticmethod
    def _clean_line(line: str) -> str:
        return " ".join(line.strip().split())

    @staticmethod
    def _parse_amount(value: str) -> float:
        return float(value.replace("$", "").replace(",", "").strip())

    @staticmethod
    def _normalize_text(value: str) -> str:
        cleaned = re.sub(r"[^a-z0-9]+", " ", value.lower())
        return f" {cleaned} "

    @staticmethod
    def _decamelize(value: str) -> str:
        return re.sub(r"(?<!^)(?=[A-Z])", " ", value).replace("  ", " ").strip()

    @staticmethod
    def _looks_like_heading(value: str) -> bool:
        if value.upper() == value and len(value.split()) <= 10:
            return True
        if value.istitle() and len(value.split()) <= 5:
            return True
        return False

    @staticmethod
    def _guess_page(index: int, lines: list[str]) -> int | None:
        if not lines:
            return None
        return 1
