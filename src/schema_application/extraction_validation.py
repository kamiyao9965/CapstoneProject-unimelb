"""Business validation for relationships inside one extracted record."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping


class PetEvidenceValidationError(ValueError):
    """Evidence failure with machine-readable repair context for the model."""

    def __init__(
        self,
        *,
        path: str | None = None,
        message: str | None = None,
        repair_hint: str | None = None,
        errors: list[Mapping[str, object]] | None = None,
    ) -> None:
        if errors is None:
            if path is None or message is None or repair_hint is None:
                raise TypeError(
                    "PetEvidenceValidationError requires either errors or "
                    "path/message/repair_hint."
                )
            errors = [{
                "path": path,
                "message": message,
                "kind": "evidence_related",
                "repair_hint": repair_hint,
            }]
        self.errors = tuple(dict(error) for error in errors)
        combined = "; ".join(
            str(error.get("message", "Pet evidence validation failed"))
            for error in self.errors
        )
        super().__init__(combined)


def normalize_pet_benefit_categories(payload: Mapping[str, object]) -> None:
    """Derive the legacy included-category list from richer coverage rows.

    ``benefit_coverages`` is the authoritative representation because it keeps
    included, optional, and excluded states.  The older
    ``covered_benefit_categories`` field is retained for comparison consumers,
    but it must never drift from those rows.
    """
    products = payload.get("products")
    if not isinstance(products, list):
        return
    for product in products:
        if not isinstance(product, dict) or "benefit_coverages" not in product:
            continue
        coverages = product.get("benefit_coverages")
        if coverages is None:
            product["covered_benefit_categories"] = None
            continue
        if not isinstance(coverages, list):
            continue
        included: list[str] = []
        for coverage in coverages:
            if not isinstance(coverage, Mapping):
                continue
            category = coverage.get("benefit_category")
            if (
                coverage.get("coverage_status") == "included"
                and isinstance(category, str)
                and category not in included
            ):
                included.append(category)
        product["covered_benefit_categories"] = included


def validate_pet_benefit_category_consistency(payload: Mapping[str, object]) -> None:
    """Require the legacy included list to equal authoritative coverage rows."""
    products = payload.get("products")
    if not isinstance(products, list):
        return
    for product_index, product in enumerate(products):
        if not isinstance(product, Mapping):
            continue
        coverages = product.get("benefit_coverages")
        categories = product.get("covered_benefit_categories")
        if coverages is None:
            if categories is not None:
                raise ValueError(
                    f"Pet extraction products[{product_index}] must set "
                    "covered_benefit_categories to null when benefit_coverages is null."
                )
            continue
        if not isinstance(coverages, list) or not isinstance(categories, list):
            raise ValueError(
                f"Pet extraction products[{product_index}] benefit coverage fields "
                "must both be lists or both be null."
            )
        coverage_categories = [
            coverage.get("benefit_category")
            for coverage in coverages
            if isinstance(coverage, Mapping)
        ]
        duplicates = sorted({
            str(category)
            for category in coverage_categories
            if coverage_categories.count(category) > 1
        })
        if duplicates:
            raise ValueError(
                f"Pet extraction products[{product_index}].benefit_coverages "
                f"contains duplicate categories: {duplicates}."
            )
        expected = {
            str(coverage.get("benefit_category"))
            for coverage in coverages
            if isinstance(coverage, Mapping)
            and coverage.get("coverage_status") == "included"
        }
        actual = {str(category) for category in categories}
        if actual != expected or len(categories) != len(actual):
            raise ValueError(
                f"Pet extraction products[{product_index}].covered_benefit_categories "
                "must equal the categories whose benefit_coverages status is included."
            )


def validate_pet_benefit_coverage_completeness(
    payload: Mapping[str, object],
) -> None:
    """Reject product records that omit coverage rows implied elsewhere.

    Evidence validation can prove that emitted rows are grounded, but it cannot
    notice a category the model left out entirely.  Cross-field facts, waiting
    periods, and sublimits provide deterministic completeness signals without
    requiring us to infer coverage from silence in the source document.
    """
    products = payload.get("products")
    if not isinstance(products, list):
        return

    errors: list[str] = []
    for product_index, product in enumerate(products):
        if not isinstance(product, Mapping):
            continue
        location = f"products[{product_index}]"
        coverages = product.get("benefit_coverages")
        if not isinstance(coverages, list) or not coverages:
            errors.append(
                f"{location}.benefit_coverages must contain coverage rows and at "
                "least one included core benefit"
            )
            continue

        statuses = {
            str(coverage.get("benefit_category")): coverage.get("coverage_status")
            for coverage in coverages
            if isinstance(coverage, Mapping)
            and isinstance(coverage.get("benefit_category"), str)
        }
        if "included" not in statuses.values():
            errors.append(
                f"{location}.benefit_coverages must contain at least one included "
                "core benefit"
            )

        def require_category(
            category: str,
            reason: str,
            allowed_statuses: set[str] | None = None,
        ) -> None:
            status = statuses.get(category)
            if status is None:
                errors.append(
                    f"{location}.benefit_coverages omits {category!r} even though "
                    f"{reason}"
                )
            elif allowed_statuses is not None and status not in allowed_statuses:
                allowed = ", ".join(sorted(allowed_statuses))
                errors.append(
                    f"{location}.benefit_coverages category {category!r} has status "
                    f"{status!r}; {reason} requires one of: {allowed}"
                )

        routine_available = product.get("routine_care_available")
        if isinstance(routine_available, bool):
            require_category(
                "routine_care",
                f"routine_care_available is {routine_available}",
                {"included", "optional"} if routine_available else {"excluded"},
            )

        euthanasia_covered = product.get("euthanasia_covered")
        if isinstance(euthanasia_covered, bool):
            require_category(
                "essential_euthanasia",
                f"euthanasia_covered is {euthanasia_covered}",
                {"included"} if euthanasia_covered else {"excluded"},
            )

        direct_category_fields = {
            "routine_care_items_covered": "routine_care",
            "overseas_vet_treatment_coverage_notes": "overseas_vet_treatment",
            "consultations_cover_opt_out_available": "consultations",
            "emergency_boarding_max_days_per_period": "emergency_pet_boarding",
        }
        for field_name, category in direct_category_fields.items():
            if product.get(field_name) is not None:
                require_category(category, f"{field_name} is populated")

        for field_name in ("waiting_periods", "sublimits"):
            rows = product.get(field_name)
            if not isinstance(rows, list):
                continue
            for row_index, row in enumerate(rows):
                if not isinstance(row, Mapping):
                    continue
                category = row.get("benefit_category")
                if not isinstance(category, str) or not category.strip():
                    continue
                require_category(
                    category,
                    f"{field_name}[{row_index}].benefit_category references it",
                    {"included", "optional"},
                )

    if errors:
        message = "Pet extraction coverage completeness failed: " + "; ".join(errors)
        raise PetEvidenceValidationError(
            path="$.products",
            message=message,
            repair_hint=(
                "Add or correct every listed benefit_coverages row. Re-read the "
                "source and cite a meaningful block plus a contiguous verbatim "
                "quote for each added category; do not infer excluded from silence."
            ),
        )


def validate_pet_benefit_coverage_evidence(
    payload: Mapping[str, object],
    source_blocks: Mapping[str, Mapping[str, tuple[int, str]]],
) -> None:
    """Report every actionable coverage-evidence error in one repair pass."""
    products = payload.get("products")
    if not isinstance(products, list):
        return
    errors: list[Mapping[str, object]] = []
    for product_index, product in enumerate(products):
        if not isinstance(product, Mapping):
            continue
        coverages = product.get("benefit_coverages")
        if coverages is None:
            continue
        if not isinstance(coverages, list):
            raise ValueError(
                f"Pet extraction products[{product_index}].benefit_coverages must be a list."
            )
        for coverage_index, coverage in enumerate(coverages):
            location = f"products[{product_index}].benefit_coverages[{coverage_index}]"
            errors.extend(_pet_benefit_coverage_evidence_errors(
                coverage,
                source_blocks,
                location=location,
            ))
    if errors:
        raise PetEvidenceValidationError(errors=errors)


def _pet_benefit_coverage_evidence_errors(
    coverage: object,
    source_blocks: Mapping[str, Mapping[str, tuple[int, str]]],
    *,
    location: str,
) -> list[Mapping[str, object]]:
    """Validate one row without hiding later errors behind an earlier one."""
    if not isinstance(coverage, Mapping):
        return [{
            "path": f"$.{location}",
            "message": f"Pet extraction {location} must be an object.",
            "kind": "evidence_related",
            "repair_hint": (
                "Return a complete benefit coverage object using one supplied source "
                "document, block ID, page, and contiguous verbatim quote."
            ),
        }]

    document_id = coverage.get("source_document_id")
    if not isinstance(document_id, str) or document_id not in source_blocks:
        return [{
            "path": f"$.{location}.source_document_id",
            "message": (
                f"Pet extraction {location}.source_document_id must identify "
                "one of the supplied source documents."
            ),
            "kind": "evidence_related",
            "repair_hint": (
                "Choose source_document_id exactly from: "
                + ", ".join(sorted(source_blocks))
                + ". Re-read the supplied source context before choosing it."
            ),
        }]

    block_id = coverage.get("source_block_id")
    document_blocks = source_blocks[document_id]
    if not isinstance(block_id, str) or block_id not in document_blocks:
        return [{
            "path": f"$.{location}.source_block_id",
            "message": (
                f"Pet extraction {location}.source_block_id must identify "
                "one of the supplied text or table blocks."
            ),
            "kind": "evidence_related",
            "repair_hint": (
                f"Choose an exact block_id or table_id marker from source "
                f"document {document_id!r}. Do not invent or reconstruct an ID."
            ),
        }]

    errors: list[Mapping[str, object]] = []
    block_page, block_text = document_blocks[block_id]
    page_value = coverage.get("source_page")
    valid_page = (
        not isinstance(page_value, bool)
        and isinstance(page_value, (int, float))
        and int(page_value) == page_value
        and int(page_value) >= 1
    )
    if not valid_page:
        errors.append({
            "path": f"$.{location}.source_page",
            "message": (
                f"Pet extraction {location}.source_page must be a positive integer."
            ),
            "kind": "evidence_related",
            "repair_hint": (
                "Copy the page number attached to the selected source block from the "
                "supplied source context."
            ),
        })
    elif int(page_value) != block_page:
        errors.append({
            "path": f"$.{location}.source_page",
            "message": (
                f"Pet extraction {location}.source_page {int(page_value)} does not "
                f"match source block {block_id!r} on page {block_page}."
            ),
            "kind": "evidence_related",
            "repair_hint": (
                f"Set source_page to {block_page} if {block_id!r} is the intended "
                "evidence block; otherwise select the correct block."
            ),
        })

    category = coverage.get("benefit_category")
    meaningful_block = _block_is_meaningful_evidence(block_text)
    category_supported = _block_supports_category(category, block_text)
    replacement_hint = None
    if not meaningful_block or not category_supported:
        replacement_hint = _replacement_block_repair_hint(
            document_id=document_id,
            block_id=block_id,
            page=block_page,
            block_text=block_text,
            category=category,
            coverage_status=coverage.get("coverage_status"),
            document_blocks=document_blocks,
            reason=(
                "the cited block is too short or semantically empty"
                if not meaningful_block
                else f"the cited block does not discuss category {category!r}"
            ),
        )
    if not meaningful_block:
        assert replacement_hint is not None
        errors.append({
            "path": f"$.{location}.source_block_id",
            "message": (
                f"Pet extraction {location}.source_block_id {block_id!r} points "
                "to a short or semantically empty source fragment."
            ),
            "kind": "evidence_related",
            "repair_hint": replacement_hint,
        })
    elif not category_supported:
        assert replacement_hint is not None
        errors.append({
            "path": f"$.{location}.source_block_id",
            "message": (
                f"Pet extraction {location}.source_block_id {block_id!r} does "
                f"not contain evidence terms for category {category!r}."
            ),
            "kind": "evidence_related",
            "repair_hint": replacement_hint,
        })

    quote = coverage.get("source_quote")
    normalized_quote = _normalize_evidence_text(quote)
    if len(normalized_quote) < 12:
        if not meaningful_block or not category_supported:
            assert replacement_hint is not None
        errors.append({
            "path": f"$.{location}.source_quote",
            "message": (
                f"Pet extraction {location}.source_quote must contain a specific, "
                "non-trivial source excerpt."
            ),
            "kind": "evidence_related",
            "repair_hint": (
                replacement_hint
                if not meaningful_block or not category_supported
                else _block_repair_hint(
                    document_id=document_id,
                    block_id=block_id,
                    page=block_page,
                    block_text=block_text,
                    instruction=(
                        "Copy a specific verbatim excerpt from the cited block. Do "
                        "not paraphrase or compose a new heading."
                    ),
                )
            ),
        })
    elif not _quote_is_supported(normalized_quote, block_text):
        if not meaningful_block or not category_supported:
            assert replacement_hint is not None
        errors.append({
            "path": f"$.{location}.source_quote",
            "message": (
                f"Pet extraction {location}.source_quote is not supported because "
                f"it is not a verbatim excerpt of source block {block_id!r}."
            ),
            "kind": "evidence_related",
            "repair_hint": (
                replacement_hint
                if not meaningful_block or not category_supported
                else _block_repair_hint(
                    document_id=document_id,
                    block_id=block_id,
                    page=block_page,
                    block_text=block_text,
                    instruction=(
                        "Replace source_quote with a contiguous verbatim excerpt from "
                        "this exact block. Do not paraphrase, summarize, or combine "
                        "words from different parts of the source."
                    ),
                )
            ),
        })
    return errors


def _normalize_evidence_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value)
    # Recover at least the visible CamelCase boundaries produced by collapsed
    # PDF table cells before case-folding removes that signal.
    normalized = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", normalized).casefold()
    return " ".join(
        "".join(character if character.isalnum() else " " for character in normalized).split()
    )


def _quote_is_supported(normalized_quote: str, block_text: str) -> bool:
    normalized_block = _normalize_evidence_text(block_text)
    if not normalized_quote:
        return False
    if normalized_quote in normalized_block:
        return True
    # PDF table extraction sometimes collapses all spaces inside a cell
    # ("WhatIsCovered"), while the model restores only those word boundaries.
    # Ignore whitespace alone; word order and every alphanumeric character must
    # still form one contiguous source excerpt.
    compact_quote = normalized_quote.replace(" ", "")
    compact_block = normalized_block.replace(" ", "")
    if compact_quote in compact_block:
        return True
    # Multi-column PDF text can interleave a neighbouring column inside an
    # otherwise verbatim sentence, and table rows can place another plan's cell
    # between the row label and the selected value.  Accept that parser noise
    # only when every quoted token still occurs exactly and in order within a
    # bounded window.  This remains stricter than fuzzy/paraphrase matching.
    return _ordered_tokens_with_bounded_noise(
        normalized_quote.split(), normalized_block.split()
    )


def _ordered_tokens_with_bounded_noise(
    quote_tokens: list[str], block_tokens: list[str]
) -> bool:
    if len(quote_tokens) < 3 or len(block_tokens) < len(quote_tokens):
        return False
    max_total_noise = max(3, len(quote_tokens))
    max_single_gap = 16
    for start, token in enumerate(block_tokens):
        if token != quote_tokens[0]:
            continue
        cursor = start
        positions = [start]
        for quote_token in quote_tokens[1:]:
            try:
                cursor = block_tokens.index(quote_token, cursor + 1)
            except ValueError:
                break
            positions.append(cursor)
        if len(positions) != len(quote_tokens):
            continue
        gaps = [right - left - 1 for left, right in zip(positions, positions[1:])]
        if sum(gaps) <= max_total_noise and max(gaps, default=0) <= max_single_gap:
            return True
    return False


def _block_is_meaningful_evidence(block_text: str) -> bool:
    normalized = _normalize_evidence_text(block_text)
    return len(normalized) >= 12 and len(normalized.split()) >= 2


def _block_repair_hint(
    *,
    document_id: str,
    block_id: str,
    page: int,
    block_text: str,
    instruction: str,
) -> str:
    return (
        f"{instruction}\n"
        f"Cited source document={document_id!r}, page={page}, block={block_id!r}.\n"
        "Exact cited block text:\n"
        f"---\n{_bounded_evidence_text(block_text)}\n---"
    )


def _bounded_evidence_text(value: str, limit: int = 6000) -> str:
    if len(value) <= limit:
        return value
    half = limit // 2
    return value[:half] + "\n...[middle truncated]...\n" + value[-half:]


def _replacement_block_repair_hint(
    *,
    document_id: str,
    block_id: str,
    page: int,
    block_text: str,
    category: object,
    coverage_status: object,
    document_blocks: Mapping[str, tuple[int, str]],
    reason: str,
) -> str:
    return (
        _block_repair_hint(
            document_id=document_id,
            block_id=block_id,
            page=page,
            block_text=block_text,
            instruction=(
                f"Do not cite this fragment. Select another block because {reason}. "
                "The replacement must "
                "explicitly support the category and coverage status; then copy a "
                "contiguous verbatim quote from that same replacement block."
            ),
        )
        + _category_candidate_repair_hint(
            category=category,
            coverage_status=coverage_status,
            document_blocks=document_blocks,
            excluded_block_id=block_id,
        )
    )


def _category_candidate_repair_hint(
    *,
    category: object,
    coverage_status: object,
    document_blocks: Mapping[str, tuple[int, str]],
    excluded_block_id: str,
) -> str:
    """Offer likely category/status blocks instead of making the model guess IDs."""
    status_terms = {
        "included": ("cover", "covered", "will pay", "reimburse", "provide"),
        "optional": ("optional", "selected", "select", "include"),
        "excluded": ("not cover", "not covered", "will not pay", "only covers"),
    }.get(str(coverage_status), ())
    opposite_terms = {
        "included": ("not cover", "not covered", "will not pay", "excluded"),
        "optional": ("not cover", "not covered", "will not pay", "excluded"),
        "excluded": ("will pay", "reimburse", "included"),
    }.get(str(coverage_status), ())
    block_items = list(document_blocks.items())
    block_positions = {
        candidate_id: index
        for index, (candidate_id, _) in enumerate(block_items)
    }
    excluded_page = document_blocks.get(excluded_block_id, (0, ""))[0]
    excluded_position = block_positions.get(excluded_block_id, 0)
    candidates: list[tuple[int, int, int, int, int, str, int, str]] = []
    for candidate_id, (candidate_page, candidate_text) in block_items:
        if (
            candidate_id == excluded_block_id
            or not _block_is_meaningful_evidence(candidate_text)
            or not _block_supports_category(category, candidate_text)
        ):
            continue
        normalized = _normalize_evidence_text(candidate_text)
        status_score = sum(
            _normalize_evidence_text(term) in normalized for term in status_terms
        )
        status_score -= sum(
            _normalize_evidence_text(term) in normalized for term in opposite_terms
        )
        page_distance = abs(candidate_page - excluded_page)
        block_distance = abs(block_positions[candidate_id] - excluded_position)
        candidates.append((
            0 if candidate_page == excluded_page else 1,
            page_distance,
            block_distance,
            0 if status_score > 0 else 1,
            -status_score,
            candidate_id,
            candidate_page,
            candidate_text,
        ))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: item[:6])
    sections = ["\nLikely category-matching candidate blocks:"]
    for *_, candidate_id, candidate_page, candidate_text in candidates[:3]:
        sections.append(
            f"\n- page={candidate_page}, block={candidate_id!r}\n"
            f"---\n{_bounded_evidence_text(candidate_text, limit=1500)}\n---"
        )
    return "".join(sections)


_BENEFIT_CATEGORY_EVIDENCE_TERMS: dict[str, tuple[str, ...]] = {
    "accidental_injury_treatment": ("accident*", "injur*"),
    "illness_treatment": (
        "illness*", "disease*", "sickness*", "specified condition*",
    ),
    "dental_illness_treatment": (
        "dental illness*", "dental condition*", "dental disease*", "gingivitis",
        "dental injury or disease",
    ),
    "dental_injury_treatment": (
        "dental injur*", "dental damage", "fractured teeth", "tooth fracture",
        "dental treatment",
    ),
    "consultations": ("consult*", "vet visit", "examination fee"),
    "emergency_pet_boarding": ("emergency boarding", "boarding", "kennel", "cattery"),
    # The source corpus uses euthanasia, euthanise, and euthanase.  The shared
    # stem is deliberate and still specific to this benefit category.
    "essential_euthanasia": ("euthan*", "put to sleep"),
    "overseas_vet_treatment": (
        "overseas", "new zealand", "norfolk island", "outside australia",
        "outside of australia", "within australia", "another country",
        "agreed country",
    ),
    "behavioural_conditions_treatment": (
        "behaviour*", "behavior*", "anxiety", "phobia",
    ),
    "specialised_therapies": (
        "specialised therap*", "specialized therap*", "physiotherapy", "acupuncture",
        "hydrotherapy", "chiropractic",
    ),
    "routine_care": (
        "routine", "preventative", "preventive", "vaccinat*", "microchip*",
        "health and wellness", "wellness check",
    ),
    "third_party_property_damage_liability": (
        "third party liability", "third party property damage",
        "property damage liability", "damage to property",
    ),
    "lost_pet_advertising_reward": (
        "lost pet", "advertising reward", "advertising and reward", "recovery reward",
    ),
    "holiday_cancellation_costs": (
        "holiday cancellation", "holiday vacation cancellation",
        "vacation cancellation", "trip cancellation", "cancelled holiday",
    ),
    "cremation_or_burial": ("cremation", "burial", "disposal of a deceased"),
    "hip_joint_surgery": ("hip joint", "hip replacement", "hip dysplasia"),
    "cruciate_ligament_condition": ("cruciate",),
    "tick_paralysis": ("tick paralysis",),
}


def _block_supports_category(category: object, block_text: str) -> bool:
    if not isinstance(category, str):
        return False
    terms = _BENEFIT_CATEGORY_EVIDENCE_TERMS.get(category)
    if not terms:
        # Extensible/custom vocabularies still receive block and quote checks.
        return True
    normalized_block = _normalize_evidence_text(block_text)
    return any(_evidence_term_matches(term, normalized_block) for term in terms)


def _evidence_term_matches(term: str, normalized_block: str) -> bool:
    """Match complete token phrases, with an explicit ``*`` suffix for stems."""
    normalized_term = _normalize_evidence_text(term.rstrip("*"))
    term_tokens = normalized_term.split()
    block_tokens = normalized_block.split()
    if not term_tokens:
        return False
    stemmed_last_token = term.endswith("*")
    width = len(term_tokens)
    for start in range(len(block_tokens) - width + 1):
        candidate = block_tokens[start:start + width]
        if candidate[:-1] != term_tokens[:-1]:
            continue
        if (
            candidate[-1].startswith(term_tokens[-1])
            if stemmed_last_token
            else candidate[-1] == term_tokens[-1]
        ):
            return True
    return False


def normalize_pet_unfilled(
    payload: Mapping[str, object], schema_data: Mapping[str, object]
) -> None:
    """Derive product ``_unfilled`` arrays from the extracted field values.

    ``_unfilled`` is bookkeeping rather than a policy fact.  A model omission in
    this array should not discard an otherwise valid, expensive extraction, so
    product-family extraction canonicalises it before applying the strict
    consistency validator.
    """
    products = payload.get("products")
    if not isinstance(products, list):
        return
    product_fields = [
        field
        for field in schema_data.get("fields", [])
        if isinstance(field, Mapping)
        and isinstance(field.get("name"), str)
        and _pet_field_is_product_scoped(field, schema_data)
    ]
    for product in products:
        if not isinstance(product, dict):
            continue
        cover_scope = product.get("cover_scope")
        product["_unfilled"] = [
            str(field["name"])
            for field in product_fields
            if _pet_field_applies_to_product(field, cover_scope, schema_data)
            and product.get(str(field["name"])) is None
        ]


def validate_pet_product_inventory(
    payload: Mapping[str, object], expected_product_ids: list[str]
) -> None:
    """Require every manifest product ID exactly once in a model response."""
    expected = list(dict.fromkeys(expected_product_ids))
    if not expected:
        return
    products = payload.get("products")
    if not isinstance(products, list):
        raise ValueError("Pet extraction products must be a list.")
    actual = [
        str(product.get("product_id") or "").strip()
        for product in products
        if isinstance(product, Mapping)
    ]
    missing = [product_id for product_id in expected if product_id not in actual]
    unexpected = [product_id for product_id in actual if product_id not in expected]
    duplicates = sorted({
        product_id for product_id in actual if actual.count(product_id) > 1
    })
    if missing or unexpected or duplicates or len(actual) != len(products):
        details = []
        if missing:
            details.append(f"missing={missing}")
        if unexpected:
            details.append(f"unexpected={unexpected}")
        if duplicates:
            details.append(f"duplicates={duplicates}")
        if len(actual) != len(products):
            details.append("one or more product entries are not objects")
        raise ValueError(
            "Pet extraction must return every expected product_id exactly once: "
            + "; ".join(details)
        )


def validate_unfilled_consistency(
    payload: Mapping[str, object], schema_data: Mapping[str, object]
) -> None:
    """Ensure null applicable fields and `_unfilled` describe the same set."""
    raw_unfilled = payload.get("_unfilled")
    if not isinstance(raw_unfilled, list) or any(
        not isinstance(name, str) for name in raw_unfilled
    ):
        raise ValueError("Extraction _unfilled must be a list of field names.")
    unfilled = set(raw_unfilled)
    product_type = payload.get("product_type")
    for field in schema_data.get("fields", []):
        if not isinstance(field, Mapping) or not isinstance(field.get("name"), str):
            continue
        name = str(field["name"])
        applies_to = field.get("applies_to") or []
        applicable = not isinstance(product_type, str) or product_type in applies_to
        if not applicable:
            if name in unfilled:
                raise ValueError(
                    f"Extraction _unfilled must not include inapplicable field {name!r}."
                )
            continue
        is_null = payload.get(name) is None
        if is_null and name not in unfilled:
            raise ValueError(
                f"Extraction null field {name!r} must be listed in _unfilled."
            )
        if not is_null and name in unfilled:
            raise ValueError(
                f"Extraction populated field {name!r} must not be listed in _unfilled."
            )


def validate_pet_unfilled_consistency(
    payload: Mapping[str, object], schema_data: Mapping[str, object]
) -> None:
    """Validate `_unfilled` for every product in a pet document/family payload.

    Pet extraction has a document envelope around one or more products, unlike
    private-health extraction.  JSON Schema ensures the field shape, while this
    validator keeps the model's missing-data declaration trustworthy for each
    individual product and enables bounded repair when it is not.
    """
    _validate_pet_required_strings(payload.get("document"), schema_data, "document")

    products = payload.get("products")
    if products is None:
        # Update/SPDS-only document extraction returns amendments rather than
        # product records, so there is no product-level _unfilled to validate.
        return
    if not isinstance(products, list):
        raise ValueError("Pet extraction products must be a list.")

    product_fields = [
        field
        for field in schema_data.get("fields", [])
        if isinstance(field, Mapping)
        and isinstance(field.get("name"), str)
        and _pet_field_is_product_scoped(field, schema_data)
    ]
    for index, product in enumerate(products):
        if not isinstance(product, Mapping):
            raise ValueError(f"Pet extraction products[{index}] must be an object.")
        _validate_pet_required_strings(product, schema_data, "product", index=index)
        raw_unfilled = product.get("_unfilled")
        if not isinstance(raw_unfilled, list) or any(
            not isinstance(name, str) for name in raw_unfilled
        ):
            raise ValueError(
                f"Pet extraction products[{index}]._unfilled must be a list of field names."
            )
        unfilled = set(raw_unfilled)
        cover_scope = product.get("cover_scope")
        for field in product_fields:
            name = str(field["name"])
            if not _pet_field_applies_to_product(field, cover_scope, schema_data):
                if name in unfilled:
                    raise ValueError(
                        f"Pet extraction products[{index}]._unfilled must not include "
                        f"inapplicable field {name!r}."
                    )
                continue
            is_null = product.get(name) is None
            if is_null and name not in unfilled:
                raise ValueError(
                    f"Pet extraction products[{index}] null field {name!r} "
                    "must be listed in _unfilled."
                )
            if not is_null and name in unfilled:
                raise ValueError(
                    f"Pet extraction products[{index}] populated field {name!r} "
                    "must not be listed in _unfilled."
                )


def _validate_pet_required_strings(
    value: object,
    schema_data: Mapping[str, object],
    target: str,
    *,
    index: int | None = None,
) -> None:
    if not isinstance(value, Mapping):
        return
    location = "document" if index is None else f"products[{index}]"
    for field in schema_data.get("fields", []):
        if not isinstance(field, Mapping):
            continue
        if (
            field.get("required") is not True
            or field.get("type") != "string"
            or target not in (field.get("applies_to") or [])
        ):
            continue
        name = str(field.get("name") or "")
        field_value = value.get(name)
        if not isinstance(field_value, str) or not field_value.strip():
            raise ValueError(
                f"Pet extraction {location} required field {name!r} "
                "must be a non-empty string."
            )


def _pet_field_is_product_scoped(
    field: Mapping[str, object], schema_data: Mapping[str, object]
) -> bool:
    applies_to = {str(value) for value in field.get("applies_to") or []}
    cover_scopes = {str(value) for value in schema_data.get("cover_scopes") or []}
    return "product" in applies_to or bool(applies_to.intersection(cover_scopes))


def _pet_field_applies_to_product(
    field: Mapping[str, object],
    cover_scope: object,
    schema_data: Mapping[str, object],
) -> bool:
    applies_to = {str(value) for value in field.get("applies_to") or []}
    if "product" in applies_to:
        return True
    cover_scopes = {str(value) for value in schema_data.get("cover_scopes") or []}
    return isinstance(cover_scope, str) and cover_scope in cover_scopes and cover_scope in applies_to
