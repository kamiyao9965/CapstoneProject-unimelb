"""Shared names and policy constants for human review artifacts."""

from __future__ import annotations

# Patch types whose real semantics (delete old field, fold sources, move
# groups) are not implemented in v1; accept applies a plain upsert, so the
# reviewer is expected to Edit then Accept.
MANUAL_EDIT_PATCH_TYPES = {"rename_field", "merge_fields", "move_field_group"}
SUPPORTED_ACTIONS = {"accept", "reject", "edit"}

QUEUE_FILENAME = "review_queue.yaml"
DECISIONS_FILENAME = "review_decisions.yaml"
REVIEWED_SCHEMA_FILENAME = "reviewed_schema.yaml"
