"""Business validation for relationships inside one extracted record."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping


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


def validate_pet_benefit_coverage_evidence(
    payload: Mapping[str, object],
    source_blocks: Mapping[str, Mapping[str, tuple[int, str]]],
) -> None:
    """Verify every coverage decision points to a semantically relevant block."""
    products = payload.get("products")
    if not isinstance(products, list):
        return
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
            location = (
                f"products[{product_index}].benefit_coverages[{coverage_index}]"
            )
            if not isinstance(coverage, Mapping):
                raise ValueError(f"Pet extraction {location} must be an object.")
            document_id = coverage.get("source_document_id")
            if not isinstance(document_id, str) or document_id not in source_blocks:
                raise ValueError(
                    f"Pet extraction {location}.source_document_id must identify "
                    "one of the supplied source documents."
                )
            block_id = coverage.get("source_block_id")
            document_blocks = source_blocks[document_id]
            if not isinstance(block_id, str) or block_id not in document_blocks:
                raise ValueError(
                    f"Pet extraction {location}.source_block_id must identify "
                    "one of the supplied text or table blocks."
                )
            page_value = coverage.get("source_page")
            if (
                isinstance(page_value, bool)
                or not isinstance(page_value, (int, float))
                or int(page_value) != page_value
                or int(page_value) < 1
            ):
                raise ValueError(
                    f"Pet extraction {location}.source_page must be a positive integer."
                )
            page = int(page_value)
            block_page, block_text = document_blocks[block_id]
            if page != block_page:
                raise ValueError(
                    f"Pet extraction {location}.source_page {page} does not match "
                    f"source block {block_id!r} on page {block_page}."
                )
            quote = coverage.get("source_quote")
            normalized_quote = _normalize_evidence_text(quote)
            if len(normalized_quote) < 12:
                raise ValueError(
                    f"Pet extraction {location}.source_quote must contain a "
                    "specific, non-trivial source excerpt."
                )
            if not _quote_is_supported(normalized_quote, block_text):
                raise ValueError(
                    f"Pet extraction {location}.source_quote is not supported by "
                    f"source block {block_id!r}."
                )
            category = coverage.get("benefit_category")
            if not _block_supports_category(category, block_text):
                raise ValueError(
                    f"Pet extraction {location}.source_block_id {block_id!r} does "
                    f"not contain evidence terms for category {category!r}."
                )


def _normalize_evidence_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(
        "".join(character if character.isalnum() else " " for character in normalized).split()
    )


def _quote_is_supported(normalized_quote: str, block_text: str) -> bool:
    normalized_block = _normalize_evidence_text(block_text)
    if normalized_quote in normalized_block:
        return True
    quote_tokens = {
        token for token in normalized_quote.split() if len(token) >= 3
    }
    block_tokens = set(normalized_block.split())
    if len(quote_tokens) < 3:
        return False
    overlap = len(quote_tokens.intersection(block_tokens))
    return overlap >= 2 and overlap / len(quote_tokens) >= 0.5


_BENEFIT_CATEGORY_EVIDENCE_TERMS: dict[str, tuple[str, ...]] = {
    "accidental_injury_treatment": ("accident", "accidental", "injury"),
    "illness_treatment": ("illness", "disease", "sickness"),
    "dental_illness_treatment": (
        "dental illness", "dental condition", "dental disease", "gingivitis",
    ),
    "dental_injury_treatment": (
        "dental injury", "dental damage", "fractured teeth", "tooth fracture",
    ),
    "consultations": ("consultation", "consult", "vet visit", "examination fee"),
    "emergency_pet_boarding": ("emergency boarding", "boarding", "kennel", "cattery"),
    "essential_euthanasia": ("euthanasia", "put to sleep"),
    "overseas_vet_treatment": (
        "overseas", "new zealand", "norfolk island", "outside australia",
    ),
    "behavioural_conditions_treatment": (
        "behaviour", "behavior", "anxiety", "phobia",
    ),
    "specialised_therapies": (
        "specialised therap", "specialized therap", "physiotherapy", "acupuncture",
        "hydrotherapy", "chiropractic",
    ),
    "routine_care": (
        "routine", "preventative", "preventive", "vaccination", "microchip",
    ),
    "third_party_property_damage_liability": (
        "third party liability", "third party property damage",
        "property damage liability", "damage to property",
    ),
    "lost_pet_advertising_reward": (
        "lost pet", "advertising reward", "advertising and reward", "recovery reward",
    ),
    "holiday_cancellation_costs": (
        "holiday cancellation", "trip cancellation", "cancelled holiday",
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
    return any(_normalize_evidence_text(term) in normalized_block for term in terms)


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
