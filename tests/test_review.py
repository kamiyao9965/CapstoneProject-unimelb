from __future__ import annotations

import unittest

from src.refine.aggregator import FieldDecision
from src.refine.review import (
    apply_review,
    build_review_queue,
    clear_decision,
    derive_status,
    empty_decisions,
    upsert_decision,
)

BASE_SCHEMA = {
    "vertical": "private_health",
    "fields": [
        {
            "name": "product_name",
            "type": "string",
            "description": "Existing description",
            "applies_to": ["hospital", "extras"],
            "required": True,
            "values": [],
        }
    ],
}


def make_decision(name: str, **overrides) -> FieldDecision:
    values = {
        "canonical_name": name,
        "target_group": "extras_cover",
        "field_type": "number",
        "description": f"{name} description",
        "frequency": 8,
        "total_runs": 10,
        "decision": "core",
        "aliases": [],
        "source_runs": ["run_001"],
        "average_confidence": 0.87,
        "patch_types": ["add_field"],
        "rationale_samples": ["seen everywhere"],
        "reject_votes": 1,
        "reject_rationale_samples": ["promotional"],
    }
    values.update(overrides)
    return FieldDecision(**values)


def make_queue(decisions=None) -> dict:
    return build_review_queue(
        decisions if decisions is not None else [make_decision("annual_limit")],
        BASE_SCHEMA,
        total_runs=10,
        base_schema_path="outputs/private_health/schema.yaml",
        generated_at="2026-07-08T00:00:00+00:00",
    )


class BuildReviewQueueTest(unittest.TestCase):
    def test_queue_is_deterministic(self) -> None:
        decisions = [make_decision("annual_limit"), make_decision("excess")]
        self.assertEqual(make_queue(decisions), make_queue(decisions))

    def test_item_shape_and_id(self) -> None:
        item = make_queue()["updates"][0]
        self.assertEqual(item["id"], "field:annual_limit")
        self.assertEqual(item["frequency"], "8/10")
        self.assertEqual(item["reject_votes"], "1/10")
        self.assertEqual(item["suggested_decision"], "core")
        self.assertEqual(item["reject_rationale_samples"], ["promotional"])
        self.assertFalse(item["needs_manual_edit"])

    def test_metadata_declares_patch_source(self) -> None:
        metadata = make_queue()["metadata"]
        self.assertEqual(metadata["consensus_source"], "candidate_schema_patches")
        self.assertEqual(metadata["total_runs"], 10)

    def test_needs_manual_edit_for_rename_merge_move(self) -> None:
        for patch_type in ("rename_field", "merge_fields", "move_field_group"):
            queue = make_queue([make_decision("excess", patch_types=[patch_type])])
            self.assertTrue(queue["updates"][0]["needs_manual_edit"], patch_type)

    def test_group_maps_to_product_type_never_group_name(self) -> None:
        queue = make_queue([make_decision("annual_limit", target_group="extras_cover")])
        self.assertEqual(queue["updates"][0]["proposed_update"]["applies_to"], ["extras"])

    def test_unknown_group_maps_to_empty_applies_to(self) -> None:
        queue = make_queue([make_decision("excess", target_group="member_services")])
        self.assertEqual(queue["updates"][0]["proposed_update"]["applies_to"], [])

    def test_existing_field_metadata_wins_in_proposed_update(self) -> None:
        queue = make_queue([make_decision("product_name", description="new desc")])
        proposed = queue["updates"][0]["proposed_update"]
        self.assertEqual(proposed["type"], "string")
        self.assertEqual(proposed["description"], "Existing description")
        self.assertEqual(proposed["applies_to"], ["hospital", "extras"])
        self.assertTrue(proposed["required"])


class DeriveStatusTest(unittest.TestCase):
    def test_status_is_derived_not_stored(self) -> None:
        queue = make_queue([make_decision("annual_limit"), make_decision("excess")])
        self.assertNotIn("review_status", queue["updates"][0])

        decisions = empty_decisions()
        upsert_decision(decisions, "field:annual_limit", "accept")
        status = derive_status(queue, decisions)
        self.assertEqual(status["field:annual_limit"], "accepted")
        self.assertEqual(status["field:excess"], "pending")

    def test_upsert_replaces_and_clear_returns_to_pending(self) -> None:
        queue = make_queue()
        decisions = empty_decisions()
        upsert_decision(decisions, "field:annual_limit", "accept")
        upsert_decision(decisions, "field:annual_limit", "reject", "changed my mind")
        self.assertEqual(len(decisions["decisions"]), 1)
        self.assertEqual(
            derive_status(queue, decisions)["field:annual_limit"], "rejected"
        )
        clear_decision(decisions, "field:annual_limit")
        self.assertEqual(
            derive_status(queue, decisions)["field:annual_limit"], "pending"
        )

    def test_upsert_rejects_unknown_action(self) -> None:
        with self.assertRaises(ValueError):
            upsert_decision(empty_decisions(), "field:x", "approve")


class ApplyReviewTest(unittest.TestCase):
    def apply(self, queue, decisions):
        return apply_review(queue, decisions, BASE_SCHEMA)

    def test_accept_reject_edit_and_pending(self) -> None:
        queue = make_queue(
            [
                make_decision("annual_limit"),
                make_decision("promo_text", decision="candidate"),
                make_decision("excess"),
                make_decision("waiting_period"),
            ]
        )
        decisions = empty_decisions()
        upsert_decision(decisions, "field:annual_limit", "accept")
        upsert_decision(decisions, "field:promo_text", "reject", "marketing only")
        upsert_decision(
            decisions,
            "field:excess",
            "edit",
            "clarified",
            {"name": "excess", "type": "number", "description": "Excess per admission",
             "applies_to": ["hospital"], "required": False, "values": []},
        )

        reviewed, summary = self.apply(queue, decisions)
        names = [field["name"] for field in reviewed["fields"]]

        self.assertIn("product_name", names)      # base preserved
        self.assertIn("annual_limit", names)       # accepted
        self.assertIn("excess", names)             # edited
        self.assertNotIn("promo_text", names)      # rejected
        self.assertNotIn("waiting_period", names)  # pending -> not applied

        excess = next(f for f in reviewed["fields"] if f["name"] == "excess")
        self.assertEqual(excess["description"], "Excess per admission")

        self.assertEqual(summary["applied"], ["field:annual_limit"])
        self.assertEqual(summary["edited"], ["field:excess"])
        self.assertEqual(summary["rejected"], ["field:promo_text"])
        self.assertEqual(summary["pending"], ["field:waiting_period"])
        self.assertEqual(reviewed["review"]["pending_count"], 1)

    def test_base_schema_is_not_mutated(self) -> None:
        queue = make_queue()
        decisions = empty_decisions()
        upsert_decision(decisions, "field:annual_limit", "accept")
        self.apply(queue, decisions)
        self.assertEqual(len(BASE_SCHEMA["fields"]), 1)

    def test_accept_filters_group_names_out_of_applies_to(self) -> None:
        queue = make_queue()
        # Simulate a hand-edited queue that smuggled a group name in.
        queue["updates"][0]["proposed_update"]["applies_to"] = ["extras_cover", "extras"]
        decisions = empty_decisions()
        upsert_decision(decisions, "field:annual_limit", "accept")
        reviewed, _ = self.apply(queue, decisions)
        field = next(f for f in reviewed["fields"] if f["name"] == "annual_limit")
        self.assertEqual(field["applies_to"], ["extras"])

    def test_edit_filters_group_names_out_of_applies_to(self) -> None:
        queue = make_queue()
        decisions = empty_decisions()
        upsert_decision(
            decisions,
            "field:annual_limit",
            "edit",
            edited_update={
                "name": "annual_limit",
                "type": "number",
                "description": "Annual limit",
                "applies_to": ["extras_cover", "extras"],
                "required": False,
                "values": [],
            },
        )
        reviewed, _ = self.apply(queue, decisions)
        field = next(f for f in reviewed["fields"] if f["name"] == "annual_limit")
        self.assertEqual(field["applies_to"], ["extras"])

    def test_unknown_decision_id_fails_loudly(self) -> None:
        decisions = empty_decisions()
        upsert_decision(decisions, "field:not_in_queue", "accept")
        with self.assertRaises(ValueError):
            self.apply(make_queue(), decisions)

    def test_edit_without_payload_fails(self) -> None:
        queue = make_queue()
        decisions = empty_decisions()
        decisions["decisions"].append(
            {"id": "field:annual_limit", "action": "edit", "edited_update": None}
        )
        with self.assertRaises(ValueError):
            self.apply(queue, decisions)

    def test_unsupported_action_fails(self) -> None:
        queue = make_queue()
        decisions = empty_decisions()
        decisions["decisions"].append(
            {"id": "field:annual_limit", "action": "approve"}
        )
        with self.assertRaises(ValueError):
            self.apply(queue, decisions)


if __name__ == "__main__":
    unittest.main()
