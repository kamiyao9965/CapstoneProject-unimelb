"""Shared names and policy constants for human review artifacts."""

from __future__ import annotations

SUPPORTED_ACTIONS = {"accept", "reject", "edit"}

QUEUE_FILENAME = "review_queue.yaml"
DECISIONS_FILENAME = "review_decisions.yaml"
REVIEWED_SCHEMA_FILENAME = "reviewed_schema.yaml"
