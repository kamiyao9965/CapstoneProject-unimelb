from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.common.json_artifacts import build_success_artifact, read_artifact, write_artifact
from src.common.model_config import ModelSelection
from src.refine.consensus import SchemaConsensusRefinement
from src.refine.human_review import apply_review, empty_decisions, upsert_decision
from src.verticals.travel_insurance import (
    SUPPORTED_TRAVEL_PRODUCT_TYPES,
    validate_travel_schema_mapping,
)


PRODUCT_TYPES = ["international_single_trip", "domestic"]


def travel_schema() -> dict[str, object]:
    return {
        "vertical": "travel_insurance",
        "version": "0.1-draft",
        "description": "Travel schema",
        "product_types": PRODUCT_TYPES,
        "product_type_field": {
            "name": "product_type", "type": "enum",
            "description": "Product classification", "applies_to": PRODUCT_TYPES,
            "required": True, "values": PRODUCT_TYPES, "aliases": [],
        },
        "fields": [
            {"name": "product_name", "type": "string",
             "description": "Product name", "applies_to": PRODUCT_TYPES,
             "required": True, "values": [], "aliases": []}
        ],
        "coverage_categories": [],
        "notes": [],
    }


def patch(name: str) -> dict[str, object]:
    return {
        "patch_type": "add_field", "target_group": "travel_cover",
        "field_name": name, "canonical_name": name, "type": "string",
        "description": f"{name} description", "applies_to": PRODUCT_TYPES,
        "required": False, "values": [], "evidence_documents": [],
        "confidence": 0.9, "rationale": "Repeated across PDS documents",
    }


class FakeTravelPatchDiscovery:
    selection = ModelSelection("openai", "gpt-5", "markdown")

    def __init__(self) -> None:
        self.calls = 0

    def discover_patches(self, **_kwargs):
        self.calls += 1
        patches = []
        if self.calls <= 4:
            patches.append(patch("geographic_coverage_details"))
        if self.calls <= 3:
            patches.append(patch("uncertain_marketing_cover"))
        return {"patches": patches}


class TravelConsensusTests(unittest.TestCase):
    def test_five_runs_auto_apply_four_votes_and_queue_three_votes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_path = root / "base.json"
            artifact = build_success_artifact(
                artifact_type="discovered_schema",
                contract_version="1.0.0",
                data=travel_schema(),
                provenance={
                    "run_id": "test", "provider": None, "model": None,
                    "document_input": None, "source_documents": [],
                    "source_artifacts": [],
                },
                data_contract="travel_insurance/discovered_schema",
            )
            write_artifact(
                base_path, artifact,
                data_contract="travel_insurance/discovered_schema",
            )
            outputs = SchemaConsensusRefinement(
                FakeTravelPatchDiscovery(),
                log=None,
                schema_contract="travel_insurance/discovered_schema",
                patch_contract="schema_refinement/candidate_patch_set",
                schema_validator=validate_travel_schema_mapping,
                valid_product_types=tuple(sorted(SUPPORTED_TRAVEL_PRODUCT_TYPES)),
                promoted_decisions=frozenset({"core"}),
                manual_only_queue=True,
            ).refine(
                base_schema_path=base_path,
                runs=5,
                samples=["pds/a.pdf"],
                output_dir=root / "consensus",
                alias_config_path="configs/travel_insurance/aliases.json",
            )

            schema = read_artifact(
                outputs.consensus_schema_path,
                expected_type="discovered_schema",
                data_contract="travel_insurance/discovered_schema",
            )["data"]
            queue = read_artifact(
                outputs.queue_path,
                expected_type="review_queue",
                data_contract="schema_refinement/review_queue",
            )["data"]
            self.assertIn(
                "geographic_coverage_details",
                [field["name"] for field in schema["fields"]],
            )
            self.assertEqual(
                [item["canonical_name"] for item in queue["updates"]],
                ["uncertain_marketing_cover"],
            )
            self.assertEqual(queue["metadata"]["vertical"], "travel_insurance")

            decisions = empty_decisions("reviewer")
            upsert_decision(
                decisions, "field:uncertain_marketing_cover", "reject"
            )
            reviewed, _ = apply_review(
                queue,
                decisions,
                schema,
                schema_validator=validate_travel_schema_mapping,
                allowed_product_types=set(SUPPORTED_TRAVEL_PRODUCT_TYPES),
            )
            self.assertIn(
                "geographic_coverage_details",
                [field["name"] for field in reviewed["fields"]],
            )


if __name__ == "__main__":
    unittest.main()
