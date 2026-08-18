from __future__ import annotations

import unittest

from src.common.json_contracts import ContractValidationError, validate_contract
from src.scraper.classification import (
    classify_document,
    classify_product_axes,
    parse_effective_date,
    relationship_confidence,
)


def valid_acquisition_data() -> dict[str, object]:
    return {
        "vertical": "travel_insurance",
        "run_id": "20260811T010203Z-a1b2c3d4",
        "source_config": "configs/travel_insurance/sources.json",
        "documents": [
            {
                "document_id": "sha256:" + "a" * 64,
                "insurer_code": "cover_more",
                "brand_code": "cover_more",
                "issuer": None,
                "underwriter": "Zurich Australian Insurance Limited",
                "document_type": "pds",
                "title": "Combined FSG and Product Disclosure Statement",
                "source_page": "https://example.com/pds",
                "discovered_url": "https://example.com/current-pds.pdf",
                "final_url": "https://example.com/current-pds.pdf",
                "section_heading": "Current plans",
                "anchor_text": "Comprehensive PDS",
                "geographic_scopes": ["international", "domestic"],
                "trip_frequencies": ["single_trip", "annual_multi_trip"],
                "plan_tiers": ["comprehensive", "basic"],
                "effective_from": "2025-10-15",
                "version_status": "current",
                "retrieval_status": "downloaded",
                "validation_status": "valid_pdf",
                "parse_status": "parsed",
                "local_path": "data/travel_insurance/raw/PDFs/cover_more/pds/a.pdf",
                "sha256": "a" * 64,
                "content_type": "application/pdf",
                "size_bytes": 128,
                "error_code": None,
                "evidence": ["anchor: Comprehensive PDS"],
            }
        ],
        "relationships": [],
        "product_releases": [
            {
                "release_id": "cover_more:2025-10-15:aaaaaaaaaaaa",
                "insurer_code": "cover_more",
                "pds_document_id": "sha256:" + "a" * 64,
                "related_document_ids": [],
                "geographic_scopes": ["international", "domestic"],
                "trip_frequencies": ["single_trip", "annual_multi_trip"],
                "plan_tiers": ["comprehensive", "basic"],
                "effective_from": "2025-10-15",
                "completeness_status": "pds_only",
            }
        ],
        "review_items": [],
        "errors": [],
        "summary": {
            "providers_attempted": 1,
            "providers_succeeded": 1,
            "documents_discovered": 1,
            "documents_downloaded": 1,
            "valid_pdfs": 1,
            "failed_documents": 0,
            "relationships": 0,
            "review_items": 0,
        },
    }


class TravelAcquisitionContractTest(unittest.TestCase):
    def test_valid_run_data_satisfies_contract(self) -> None:
        payload = valid_acquisition_data()

        self.assertIs(
            validate_contract(payload, "travel_insurance/acquisition_run"),
            payload,
        )

    def test_status_dimensions_cannot_be_collapsed_into_one_field(self) -> None:
        payload = valid_acquisition_data()
        document = dict(payload["documents"][0])  # type: ignore[index]
        document.pop("parse_status")
        payload["documents"] = [document]

        with self.assertRaisesRegex(ContractValidationError, "parse_status"):
            validate_contract(payload, "travel_insurance/acquisition_run")

    def test_product_axes_reject_unsupported_values(self) -> None:
        payload = valid_acquisition_data()
        document = dict(payload["documents"][0])  # type: ignore[index]
        document["trip_frequencies"] = ["weekend_only"]
        payload["documents"] = [document]

        with self.assertRaises(ContractValidationError):
            validate_contract(payload, "travel_insurance/acquisition_run")


class TravelClassificationTest(unittest.TestCase):
    def test_combined_fsg_pds_is_classified_as_pds(self) -> None:
        self.assertEqual(
            classify_document(
                "Combined Financial Services Guide and Product Disclosure Statement",
                "https://example.com/combined-fsg-pds.pdf",
            ),
            "pds",
        )

    def test_spds_takes_precedence_over_pds_substring(self) -> None:
        self.assertEqual(
            classify_document(
                "Supplementary Product Disclosure Statement",
                "https://example.com/spds.pdf",
            ),
            "spds",
        )

    def test_standalone_fsg_wins_over_policy_page_context(self) -> None:
        self.assertEqual(
            classify_document(
                "Financial Services Guide (FSG)",
                "https://example.com/travel-fsg.pdf",
                "Policy wording and Product Disclosure Statement documents",
            ),
            "fsg",
        )

    def test_explicit_tmd_title_wins_over_generic_spds_reference_in_text(self) -> None:
        self.assertEqual(
            classify_document(
                "Target Market Determination (TMD)",
                "https://example.com/travel-tmd.pdf",
                "Read the Product Disclosure Statement and any Supplementary PDS.",
            ),
            "tmd",
        )

    def test_three_product_axes_are_independent_and_multi_valued(self) -> None:
        axes = classify_product_axes(
            "International and Domestic Comprehensive and Basic cover, "
            "available for Single Trip and Annual Multi-Trip policies."
        )

        self.assertEqual(axes["geographic_scopes"], ["international", "domestic"])
        self.assertEqual(axes["trip_frequencies"], ["single_trip", "annual_multi_trip"])
        self.assertEqual(axes["plan_tiers"], ["comprehensive", "basic"])

    def test_unknown_is_used_instead_of_guessing(self) -> None:
        axes = classify_product_axes("Travel insurance policy documents")

        self.assertEqual(axes["geographic_scopes"], ["unknown"])
        self.assertEqual(axes["trip_frequencies"], ["unknown"])
        self.assertEqual(axes["plan_tiers"], ["unknown"])

    def test_generic_essential_cover_is_not_an_essentials_plan(self) -> None:
        axes = classify_product_axes("The policy includes essential cover for travellers.")

        self.assertEqual(axes["plan_tiers"], ["unknown"])

    def test_effective_date_is_normalised(self) -> None:
        self.assertEqual(
            parse_effective_date("Effective from 15 October 2025"),
            "2025-10-15",
        )

    def test_issued_on_or_after_date_is_normalised(self) -> None:
        self.assertEqual(
            parse_effective_date("Policies issued on or after 12 July 2025"),
            "2025-07-12",
        )

    def test_conflict_forces_review_and_caps_confidence(self) -> None:
        confidence, review_required = relationship_confidence(
            explicit_reference=True,
            same_section=True,
            same_provider=True,
            compatible_axes=True,
            conflict=True,
        )

        self.assertEqual(confidence, "medium")
        self.assertTrue(review_required)

    def test_context_match_without_explicit_reference_is_high(self) -> None:
        confidence, review_required = relationship_confidence(
            explicit_reference=False,
            same_section=True,
            same_provider=True,
            compatible_axes=True,
        )

        self.assertEqual(confidence, "high")
        self.assertFalse(review_required)


if __name__ == "__main__":
    unittest.main()
