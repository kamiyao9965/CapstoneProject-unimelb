from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.common.json_artifacts import read_artifact
from src.common.json_contracts import validate_contract
from src.refine.artifacts.renderer import render_consensus_schema
from src.refine.artifacts.schema_fields import (
    decision_is_auto_promotable,
    field_payload_from_decision,
)
from src.refine.candidates.aggregator import FieldDecision, aggregate_patches
from src.refine.candidates.patch import SchemaPatch, parse_patch_payload
from src.refine.human_review import (
    apply_review,
    build_review_queue,
    empty_decisions,
    upsert_decision,
    write_review_queue,
)
from src.refine.human_review.queue import load_review_queue
from src.schema.canonical import (
    build_canonical_candidate,
    compile_canonical_extraction_contract,
)
from src.verticals.manifest import resolve_manifest
from tests.test_canonical_schema import approved_travel_schema
from tests.test_nested_contracts import (
    closed_benefit_item_schema,
    discovered_travel_schema,
)


def nested_patch_payload(*, item_schema: dict[str, object] | None = None) -> dict[str, object]:
    payload = {
        "patch_type": "add_field",
        "target_group": "domestic_cover",
        "field_name": "new_benefits",
        "canonical_name": "new_benefits",
        "type": "list[object]",
        "description": "Additional benefits with exact limits and source evidence.",
        "applies_to": ["domestic"],
        "required": False,
        "values": [],
        "confidence": 0.9,
        "rationale": "Explicitly documented benefit structure.",
        "evidence_documents": [],
    }
    if item_schema is not None:
        payload["item_schema"] = copy.deepcopy(item_schema)
    return payload


def nested_patches(*schemas: dict[str, object]) -> list[SchemaPatch]:
    return [
        SchemaPatch.from_dict(nested_patch_payload(item_schema=item_schema), source_run=f"run_{index}")
        for index, item_schema in enumerate(schemas, 1)
    ]


def conflicting_item_schema() -> dict[str, object]:
    schema = closed_benefit_item_schema()
    schema["properties"]["limit"]["properties"]["amount_aud"]["type"] = ["string", "null"]
    return schema


def build_test_candidate(schema: dict[str, object]) -> dict[str, object]:
    # A synthetic approved template avoids reading or changing approved configs.
    manifest = resolve_manifest(vertical="travel_insurance")
    with tempfile.TemporaryDirectory() as tmp:
        template_path = Path(tmp) / "approved_test_fixture.json"
        template_path.write_text(json.dumps(approved_travel_schema()), encoding="utf-8")
        original = template_path.read_bytes()
        with mock.patch.object(type(manifest), "path", return_value=template_path):
            candidate = build_canonical_candidate(schema, manifest)
        if template_path.read_bytes() != original:
            raise AssertionError("Candidate construction changed its approved template.")
        return candidate


class NestedPatchPropagationTests(unittest.TestCase):
    def test_patch_accepts_optional_nested_contract_in_constructor(self) -> None:
        item_schema = closed_benefit_item_schema()
        patch = SchemaPatch(
            patch_type="add_field",
            target_group="domestic_cover",
            field_name="new_benefits",
            canonical_name="new_benefits",
            field_type="list[object]",
            description="New benefits.",
            applies_to=("domestic",),
            required=False,
            item_schema=item_schema,
        )
        patch.validate({"domestic"})
        self.assertEqual(patch.item_schema, item_schema)
        self.assertEqual(patch.to_dict()["item_schema"], item_schema)

    def test_patch_parsing_and_serialization_preserve_nested_contract(self) -> None:
        item_schema = closed_benefit_item_schema()
        payload = nested_patch_payload(item_schema=item_schema)
        original = copy.deepcopy(payload)
        patches = parse_patch_payload(
            {"patches": [payload]}, source_run="run_1", allowed_product_types={"domestic"},
        )
        self.assertEqual(patches[0].item_schema, item_schema)
        serialized = {"patches": [patches[0].to_dict()]}
        self.assertEqual(serialized["patches"][0]["item_schema"], item_schema)
        validate_contract(serialized, "schema_refinement/candidate_patch_set")
        self.assertEqual(payload, original)

    def test_decision_constructor_and_frequency_serialization_preserve_nested_contract(self) -> None:
        item_schema = closed_benefit_item_schema()
        decision = FieldDecision(
            canonical_name="new_benefits",
            target_group="domestic_cover",
            field_type="list[object]",
            description="New benefits.",
            frequency=2,
            total_runs=2,
            decision="core",
            applies_to=["domestic"],
            required=False,
            patch_types=["add_field"],
            item_schema=item_schema,
        )
        self.assertEqual(decision.item_schema, item_schema)
        serialized = {"fields": [decision.to_dict()]}
        self.assertEqual(serialized["fields"][0]["item_schema"], item_schema)
        validate_contract(serialized, "schema_refinement/field_frequency")

    def test_absent_nested_contract_is_omitted_from_historical_serialization(self) -> None:
        patch = SchemaPatch.from_dict(nested_patch_payload(), source_run="run_1")
        decision = aggregate_patches([patch], total_runs=1)[0]
        self.assertNotIn("item_schema", patch.to_dict())
        self.assertNotIn("item_schema", decision.to_dict())
        queue = build_review_queue(
            [decision], discovered_travel_schema(), total_runs=1,
            base_schema_path="base_schema.json", manual_only=False,
        )
        self.assertNotIn("item_schema", queue["updates"][0]["proposed_update"])

    def test_agreeing_nested_contracts_survive_aggregation(self) -> None:
        item_schema = closed_benefit_item_schema()
        decision = aggregate_patches(nested_patches(item_schema, item_schema), total_runs=2)[0]
        self.assertFalse(decision.has_conflict)
        self.assertEqual(decision.item_schema, item_schema)
        self.assertEqual(decision.to_dict()["item_schema"], item_schema)

    def test_different_nested_contracts_are_conflicts_despite_equal_outer_types(self) -> None:
        decision = aggregate_patches(
            nested_patches(closed_benefit_item_schema(), conflicting_item_schema()), total_runs=2,
        )[0]
        self.assertEqual(decision.field_type, "list[object]")
        self.assertEqual(decision.decision, "core")
        self.assertTrue(decision.has_conflict)
        self.assertFalse(decision_is_auto_promotable(decision, frozenset({"core"}), frozenset()))

    def test_dictionary_key_order_does_not_create_a_nested_contract_conflict(self) -> None:
        item_schema = closed_benefit_item_schema()
        reordered = dict(reversed(list(item_schema.items())))
        reordered["properties"] = dict(reversed(list(item_schema["properties"].items())))
        decision = aggregate_patches(nested_patches(item_schema, reordered), total_runs=2)[0]
        self.assertFalse(decision.has_conflict)
        self.assertEqual(decision.item_schema, item_schema)

    def test_field_payload_keeps_the_nested_contract_from_an_add_field_decision(self) -> None:
        item_schema = closed_benefit_item_schema()
        decision = aggregate_patches(nested_patches(item_schema), total_runs=1)[0]
        proposed = field_payload_from_decision(decision, valid_product_types=("domestic",))
        self.assertEqual(proposed["item_schema"], item_schema)

    def test_description_only_update_preserves_existing_nested_shape(self) -> None:
        schema = discovered_travel_schema()
        existing_field = schema["fields"][-1]
        existing_field["item_schema"] = closed_benefit_item_schema()
        original = copy.deepcopy(existing_field)
        payload = nested_patch_payload(item_schema=conflicting_item_schema())
        payload.update(
            patch_type="update_description", field_name="benefits", canonical_name="benefits",
            description="Clarified benefit description.",
        )
        decision = aggregate_patches([SchemaPatch.from_dict(payload, "run_1")], total_runs=1)[0]
        proposed = field_payload_from_decision(decision, existing_field, ("domestic",))
        self.assertEqual(proposed["description"], "Clarified benefit description.")
        self.assertEqual(proposed["item_schema"], original["item_schema"])
        self.assertEqual(existing_field, original)


class NestedRefinementBoundaryTests(unittest.TestCase):
    def test_conflicting_nested_contracts_cannot_be_auto_promoted(self) -> None:
        decisions = aggregate_patches(
            nested_patches(closed_benefit_item_schema(), conflicting_item_schema()), total_runs=2,
        )
        manifest = resolve_manifest(vertical="travel_insurance")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "base.json"
            destination = Path(tmp) / "consensus.json"
            source.write_text(json.dumps(discovered_travel_schema()), encoding="utf-8")
            render_consensus_schema(source, decisions, destination, manifest=manifest)
            data = read_artifact(
                destination, expected_type="discovered_schema",
                data_contract=manifest.contract("discovered_schema"),
            )["data"]
        self.assertNotIn("new_benefits", {field["name"] for field in data["fields"]})

    def test_agreeing_nested_contract_survives_auto_promotion(self) -> None:
        item_schema = closed_benefit_item_schema()
        decisions = aggregate_patches(nested_patches(item_schema, item_schema), total_runs=2)
        manifest = resolve_manifest(vertical="travel_insurance")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "base.json"
            destination = Path(tmp) / "consensus.json"
            source.write_text(json.dumps(discovered_travel_schema()), encoding="utf-8")
            render_consensus_schema(source, decisions, destination, manifest=manifest)
            data = read_artifact(
                destination, expected_type="discovered_schema",
                data_contract=manifest.contract("discovered_schema"),
            )["data"]
        fields = {field["name"]: field for field in data["fields"]}
        self.assertEqual(fields["new_benefits"]["item_schema"], item_schema)

    def test_review_queue_round_trip_and_accept_keep_nested_contract(self) -> None:
        item_schema = closed_benefit_item_schema()
        decision = aggregate_patches(nested_patches(item_schema), total_runs=1)[0]
        base_schema = discovered_travel_schema()
        queue = build_review_queue(
            [decision], base_schema, total_runs=1, base_schema_path="base_schema.json",
            manual_only=False, generated_at="2026-10-12T00:00:00Z",
        )
        self.assertEqual(queue["updates"][0]["proposed_update"]["item_schema"], item_schema)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "queue.json"
            write_review_queue(queue, path)
            queue = load_review_queue(path)
        review = empty_decisions("test-reviewer", queue=queue)
        upsert_decision(review, "field:new_benefits", "accept")
        reviewed, summary = apply_review(queue, review, base_schema)
        self.assertEqual(summary["applied"], ["field:new_benefits"])
        fields = {field["name"]: field for field in reviewed["fields"]}
        self.assertEqual(fields["new_benefits"]["item_schema"], item_schema)

    def test_conflicted_nested_decision_remains_in_manual_review_queue(self) -> None:
        decisions = aggregate_patches(
            nested_patches(closed_benefit_item_schema(), conflicting_item_schema()), total_runs=2,
        )
        queue = build_review_queue(
            decisions, discovered_travel_schema(), total_runs=2,
            base_schema_path="base_schema.json", manual_only=True,
        )
        self.assertEqual([item["id"] for item in queue["updates"]], ["field:new_benefits"])
        self.assertTrue(queue["updates"][0]["has_conflict"])

    def test_canonical_candidate_preserves_discovered_nested_contract_without_approval(self) -> None:
        schema = discovered_travel_schema()
        item_schema = closed_benefit_item_schema()
        schema["fields"][-1]["item_schema"] = item_schema
        original = copy.deepcopy(schema)
        candidate = build_test_candidate(schema)
        fields = {field["name"]: field for field in candidate["fields"]}
        self.assertEqual(fields["benefits"]["item_schema"], item_schema)
        self.assertEqual(fields["benefits"]["storage"], {"strategy": "jsonb"})
        self.assertEqual(candidate["status"], "candidate")
        self.assertIsNone(candidate["review"])
        self.assertEqual(schema, original)
        with self.assertRaisesRegex(ValueError, "human-approved"):
            compile_canonical_extraction_contract(candidate)

    def test_canonical_candidate_preserves_resolved_local_nested_definitions(self) -> None:
        schema = discovered_travel_schema()
        item_schema = closed_benefit_item_schema()
        schema["$defs"] = {"Benefit": item_schema}
        schema["fields"][-1]["item_schema"] = {"$ref": "#/$defs/Benefit"}
        candidate = build_test_candidate(schema)
        fields = {field["name"]: field for field in candidate["fields"]}
        self.assertEqual(fields["benefits"]["item_schema"], item_schema)
        self.assertEqual(candidate["status"], "candidate")
        self.assertIsNone(candidate["review"])


if __name__ == "__main__":
    unittest.main()
