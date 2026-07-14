"""Shared names and policy constants for human review artifacts."""

from __future__ import annotations

SUPPORTED_ACTIONS = {"accept", "reject", "edit"}

QUEUE_FILENAME = "review_queue.json"
DECISIONS_FILENAME = "review_decisions.json"
REVIEWED_SCHEMA_FILENAME = "reviewed_schema.json"
