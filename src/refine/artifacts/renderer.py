"""Render consensus JSON artifacts and human-readable CLI summaries."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from src.refine.artifacts.schema_fields import (
    applies_to_from_group,
    decision_requires_manual_edit,
    field_payload_from_decision,
    fields_by_name,
)
from src.refine.candidates.aggregator import FieldDecision
from src.common.json_artifacts import (
    build_success_artifact,
    read_artifact,
    write_artifact,
)
from src.schema.validation import validate_schema_mapping


PROMOTED_DECISIONS = {"core", "conditional"}


def render_consensus_schema(
    base_schema_path: str | Path,
    decisions: list[FieldDecision],
    output_path: str | Path,
) -> None:
    base_schema = read_artifact(
        base_schema_path,
        expected_type="discovered_schema",
        data_contract="private_health/discovered_schema",
    )["data"]
    if not isinstance(base_schema, dict):
        raise ValueError("Base schema JSON must be an object.")

    consensus_schema = deepcopy(base_schema)
    existing_fields = fields_by_name(consensus_schema.get("fields", []))

    for decision in decisions:
        if decision.decision not in PROMOTED_DECISIONS:
            continue
        if decision.reject_votes or decision_requires_manual_edit(decision):
            continue
        patch_type = decision.patch_types[0]
        existing_field = existing_fields.get(decision.canonical_name)
        if patch_type in {"update_description", "add_alias"} and existing_field is None:
            continue
        if patch_type == "add_field" and (
            not (decision.applies_to or applies_to_from_group(decision.target_group))
            or (decision.field_type == "enum" and not decision.values)
        ):
            continue
        existing_fields[decision.canonical_name] = field_payload_from_decision(
            decision,
            existing_field,
        )

    consensus_schema["fields"] = list(existing_fields.values())
    validate_schema_mapping(consensus_schema)
    artifact = build_success_artifact(
        artifact_type="discovered_schema",
        contract_version="1.0.0",
        data=consensus_schema,
        provenance=_local_provenance([Path(base_schema_path).as_posix()]),
        data_contract="private_health/discovered_schema",
    )
    write_artifact(
        output_path, artifact, data_contract="private_health/discovered_schema"
    )


def render_frequency_json(decisions: list[FieldDecision], output_path: str | Path) -> None:
    data = {"fields": [decision.to_dict() for decision in decisions]}
    artifact = build_success_artifact(
        artifact_type="field_frequency",
        contract_version="1.0.0",
        data=data,
        provenance=_local_provenance(),
        data_contract="private_health/field_frequency",
    )
    write_artifact(
        output_path, artifact, data_contract="private_health/field_frequency"
    )


def render_report(decisions: list[FieldDecision]) -> str:
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

    return "\n".join(lines).rstrip() + "\n"


def _local_provenance(source_artifacts: list[str] | None = None) -> dict[str, object]:
    return {
        "run_id": None, "provider": None, "model": None,
        "document_input": None, "source_documents": [],
        "source_artifacts": source_artifacts or [],
    }
