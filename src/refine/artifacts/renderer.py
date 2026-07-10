"""Render consensus artifacts: merged schema, field frequency, and report.

Field metadata is written under a `consensus` key (rather than `refinement`) to
keep consensus refinement distinct from the extraction-driven refinement loop.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from src.refine.artifacts.schema_fields import (
    field_payload_from_decision,
    fields_by_name,
)
from src.refine.candidates.aggregator import FieldDecision
from src.refine.candidates.patch import (
    MANUAL_EDIT_PATCH_TYPES,
    dump_yaml,
    load_yaml,
)


PROMOTED_DECISIONS = {"core", "conditional"}


def render_consensus_schema(
    base_schema_path: str | Path,
    decisions: list[FieldDecision],
    output_path: str | Path,
) -> None:
    base_schema = load_yaml(base_schema_path) or {}
    if not isinstance(base_schema, dict):
        raise ValueError("Base schema YAML must be an object.")

    consensus_schema = deepcopy(base_schema)
    existing_fields = fields_by_name(consensus_schema.get("fields", []))

    for decision in decisions:
        if decision.decision not in PROMOTED_DECISIONS:
            continue
        if MANUAL_EDIT_PATCH_TYPES.intersection(decision.patch_types):
            continue
        existing_fields[decision.canonical_name] = field_payload_from_decision(
            decision,
            existing_fields.get(decision.canonical_name),
            include_consensus=True,
        )

    consensus_schema["fields"] = list(existing_fields.values())
    consensus_schema["consensus"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "promoted_decisions": sorted(PROMOTED_DECISIONS),
    }
    dump_yaml(consensus_schema, output_path)


def render_frequency_yaml(decisions: list[FieldDecision], output_path: str | Path) -> None:
    dump_yaml({"fields": [decision.to_dict() for decision in decisions]}, output_path)


def render_report(decisions: list[FieldDecision], output_path: str | Path) -> None:
    lines = [
        "# Schema Consensus Report",
        "",
        f"Generated at: {datetime.now(timezone.utc).isoformat()}",
        "",
        "| Field | Group | Frequency | Decision | Avg Confidence |",
        "| --- | --- | --- | --- | --- |",
    ]

    for decision in decisions:
        lines.append(
            "| "
            + " | ".join(
                [
                    decision.canonical_name,
                    decision.target_group,
                    decision.frequency_label,
                    decision.decision,
                    f"{decision.average_confidence:.3f}",
                ]
            )
            + " |"
        )

    lines.extend(["", "## Field Details", ""])
    for decision in decisions:
        lines.extend(
            [
                f"### {decision.canonical_name}",
                "",
                f"- Decision: {decision.decision}",
                f"- Group: {decision.target_group}",
                f"- Type: {decision.field_type}",
                f"- Frequency: {decision.frequency_label}",
                f"- Source runs: {', '.join(decision.source_runs) or 'n/a'}",
                f"- Aliases: {', '.join(decision.aliases) or 'none'}",
                f"- Description: {decision.description or 'n/a'}",
                "",
            ]
        )

    resolved_path = Path(output_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
