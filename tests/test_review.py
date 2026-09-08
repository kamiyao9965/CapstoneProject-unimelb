from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest import mock

from src.refine.candidates.aggregator import FieldDecision
from src.refine.artifacts.schema_fields import fields_by_name
from src.refine.human_review import (
    apply_review,
    build_review_queue,
    clear_decision,
    derive_status,
    empty_decisions,
    load_review_decisions,
    save_review_decision,
    remove_review_decision,
    upsert_decision,
    write_review_decisions,
    write_review_queue,
)
from src.refine.human_review import decisions as decisions_module

BASE_SCHEMA = {
    "vertical": "private_health",
    "version": "0.1-draft",
    "description": "Schema",
    "product_types": ["hospital", "extras", "generalhealth", "combined"],
    "fields": [
        {
            "name": "product_type",
            "type": "enum",
            "description": "Product classification",
            "applies_to": ["hospital", "extras", "generalhealth", "combined"],
            "required": True,
            "values": ["hospital", "extras", "generalhealth", "combined"],
            "aliases": [],
        },
        {
            "name": "product_name",
            "type": "string",
            "description": "Existing description",
            "applies_to": ["hospital", "extras"],
            "required": True,
            "values": [],
            "aliases": [],
        }
    ],
    "hospital_categories": [],
    "extras_services": [],
    "notes": [],
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
        base_schema_path="outputs/private_health/schema.json",
        generated_at="2026-07-08T00:00:00+00:00",
    )


class BuildReviewQueueTest(unittest.TestCase):
    def test_manual_only_queue_omits_safe_core_but_keeps_uncertain_and_conflicted(self) -> None:
        safe = make_decision("safe_core", decision="core", reject_votes=0)
        uncertain = make_decision("uncertain", decision="conditional", reject_votes=0)
        conflicted = make_decision(
            "conflicted", decision="core", reject_votes=0, has_conflict=True
        )

        queue = build_review_queue(
            [safe, uncertain, conflicted],
            BASE_SCHEMA,
            total_runs=5,
            base_schema_path="base.json",
            manual_only=True,
            promoted_decisions=frozenset({"core"}),
        )

        self.assertEqual(
            [item["canonical_name"] for item in queue["updates"]],
            ["uncertain", "conflicted"],
        )

    def test_manual_only_queue_keeps_protected_identity_fields(self) -> None:
        identity = make_decision("product_name", decision="core", reject_votes=0)
        queue = build_review_queue(
            [identity],
            BASE_SCHEMA,
            total_runs=5,
            base_schema_path="base.json",
            manual_only=True,
            promoted_decisions=frozenset({"core"}),
            protected_fields=frozenset({"product_name", "product_type"}),
        )

        self.assertEqual(queue["updates"][0]["canonical_name"], "product_name")

    def test_fields_by_name_rejects_malformed_entries_instead_of_dropping_them(self) -> None:
        with self.assertRaisesRegex(ValueError, "field 1"):
            fields_by_name([{"name": "valid"}, {"description": "missing name"}])

    def test_fields_by_name_rejects_duplicate_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate"):
            fields_by_name([{"name": "same"}, {"name": "same"}])

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
    def test_windows_lock_uses_blocking_byte_range_lock_and_unlock(self) -> None:
        class FakeMSVCRT:
            LK_LOCK = 1
            LK_UNLCK = 2

            def __init__(self) -> None:
                self.calls: list[int] = []

            def locking(self, _descriptor: int, mode: int, _bytes: int) -> None:
                self.calls.append(mode)

        fake_msvcrt = FakeMSVCRT()
        with tempfile.TemporaryFile(mode="w+") as lock:
            with (
                mock.patch.object(decisions_module, "fcntl", None),
                mock.patch.object(decisions_module, "msvcrt", fake_msvcrt, create=True),
            ):
                with decisions_module._exclusive_lock(lock):
                    pass

        self.assertEqual(
            fake_msvcrt.calls,
            [fake_msvcrt.LK_LOCK, fake_msvcrt.LK_UNLCK],
        )

    def test_atomic_decision_updates_merge_with_latest_file_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "review_decisions.json"
            queue = make_queue([make_decision("first"), make_decision("second")])
            write_review_queue(queue, path.parent / "review_queue.json")
            write_review_decisions(empty_decisions(queue=queue), path)
            save_review_decision(path, "field:first", "accept")

            save_review_decision(path, "field:second", "reject", "not supported")
            payload = load_review_decisions(path)

            self.assertEqual(
                {item["id"] for item in payload["decisions"]},
                {"field:first", "field:second"},
            )

            remove_review_decision(path, "field:first")
            payload = load_review_decisions(path)
            self.assertEqual(
                [item["id"] for item in payload["decisions"]],
                ["field:second"],
            )

    def test_status_is_derived_not_stored(self) -> None:
        queue = make_queue([make_decision("annual_limit"), make_decision("excess")])
        self.assertNotIn("review_status", queue["updates"][0])

        decisions = empty_decisions(queue=queue)
        upsert_decision(decisions, "field:annual_limit", "accept")
        status = derive_status(queue, decisions)
        self.assertEqual(status["field:annual_limit"], "accepted")
        self.assertEqual(status["field:excess"], "pending")

    def test_upsert_replaces_and_clear_returns_to_pending(self) -> None:
        queue = make_queue()
        decisions = empty_decisions(queue=queue)
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
    def test_same_field_decision_from_another_queue_is_rejected(self) -> None:
        queue = make_queue()
        other = build_review_queue([make_decision("annual_limit")], BASE_SCHEMA,
                                   total_runs=10, base_schema_path="another_run/schema.json")
        decisions = empty_decisions(queue=other)
        upsert_decision(decisions, "field:annual_limit", "accept")
        with self.assertRaisesRegex(ValueError, "identity"):
            apply_review(queue, decisions, BASE_SCHEMA)

    def test_base_content_change_with_same_version_is_rejected(self) -> None:
        queue = make_queue()
        changed = dict(BASE_SCHEMA, description="changed after review started")
        with self.assertRaisesRegex(ValueError, "identity"):
            apply_review(queue, empty_decisions(queue=queue), changed)

    def test_legacy_decisions_cannot_be_implicitly_bound_to_new_queue(self) -> None:
        with self.assertRaisesRegex(ValueError, "identity"):
            apply_review(make_queue(), empty_decisions(), BASE_SCHEMA)

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
        decisions = empty_decisions(queue=queue)
        upsert_decision(decisions, "field:annual_limit", "accept")
        upsert_decision(decisions, "field:promo_text", "reject", "marketing only")
        upsert_decision(
            decisions,
            "field:excess",
            "edit",
            "clarified",
            {"name": "excess", "type": "number", "description": "Excess per admission",
             "applies_to": ["hospital"], "required": False, "values": [],
             "aliases": []},
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
        self.assertNotIn("review", reviewed)

    def test_base_schema_is_not_mutated(self) -> None:
        queue = make_queue()
        decisions = empty_decisions(queue=queue)
        upsert_decision(decisions, "field:annual_limit", "accept")
        self.apply(queue, decisions)
        self.assertEqual(len(BASE_SCHEMA["fields"]), 2)

    def test_accept_rejects_group_names_in_applies_to(self) -> None:
        queue = make_queue()
        decisions = empty_decisions(queue=queue)
        # Simulate a hand-edited queue that smuggled a group name in.
        queue["updates"][0]["proposed_update"]["applies_to"] = ["extras_cover", "extras"]
        upsert_decision(decisions, "field:annual_limit", "accept")
        with self.assertRaisesRegex(ValueError, "identity|unknown product types"):
            self.apply(queue, decisions)

    def test_edit_rejects_group_names_in_applies_to(self) -> None:
        queue = make_queue()
        decisions = empty_decisions(queue=queue)
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
                "aliases": [],
            },
        )
        with self.assertRaisesRegex(ValueError, "identity|unknown product types"):
            self.apply(queue, decisions)

    def test_manual_patch_cannot_be_plainly_accepted(self) -> None:
        queue = make_queue(
            [make_decision("renamed_field", patch_types=["rename_field"])]
        )
        decisions = empty_decisions(queue=queue)
        upsert_decision(decisions, "field:renamed_field", "accept")

        with self.assertRaisesRegex(ValueError, "must be edited"):
            self.apply(queue, decisions)

    def test_rename_patch_cannot_be_applied_as_an_edited_upsert(self) -> None:
        queue = make_queue(
            [make_decision("renamed_field", patch_types=["rename_field"])]
        )
        decisions = empty_decisions(queue=queue)
        upsert_decision(
            decisions,
            "field:renamed_field",
            "edit",
            edited_update={
                "name": "renamed_field",
                "type": "number",
                "description": "Renamed field",
                "applies_to": ["extras"],
                "required": False,
                "values": [],
                "aliases": [],
            },
        )

        with self.assertRaisesRegex(ValueError, "cannot be applied as a field upsert"):
            self.apply(queue, decisions)

    def test_unknown_decision_id_fails_loudly(self) -> None:
        queue = make_queue()
        decisions = empty_decisions(queue=queue)
        upsert_decision(decisions, "field:not_in_queue", "accept")
        with self.assertRaises(ValueError):
            self.apply(queue, decisions)

    def test_edit_without_payload_fails(self) -> None:
        queue = make_queue()
        decisions = empty_decisions(queue=queue)
        decisions["decisions"].append(
            {"id": "field:annual_limit", "action": "edit", "edited_update": None}
        )
        with self.assertRaises(ValueError):
            self.apply(queue, decisions)

    def test_unsupported_action_fails(self) -> None:
        queue = make_queue()
        decisions = empty_decisions(queue=queue)
        decisions["decisions"].append(
            {"id": "field:annual_limit", "action": "approve"}
        )
        with self.assertRaises(ValueError):
            self.apply(queue, decisions)


if __name__ == "__main__":
    unittest.main()
