"""Render consensus JSON artifacts and human-readable CLI summaries."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable

from src.refine.artifacts.schema_fields import applies_to_from_group, decision_is_auto_promotable, field_payload_from_decision, fields_by_name
from src.refine.candidates.aggregator import FieldDecision
from src.common.json_artifacts import build_success_artifact, write_artifact
from src.schema.loader import load_schema_data
from src.common.json_contracts import load_contract


def render_consensus_schema(
    base_schema_path: str | Path,
    decisions: list[FieldDecision],
    output_path: str | Path,
    *,
    schema_contract: str | None = None,
    schema_validator: Callable[[object], object] | None = None,
    valid_product_types: tuple[str, ...] | None = None,
    promoted_decisions: frozenset[str] | None = None,
    protected_fields: frozenset[str] | None = None,
    manifest=None,
) -> None:
    base_schema = load_schema_data(base_schema_path, manifest)
    from src.verticals.manifest import resolve_manifest
    from src.verticals.registry import get_schema_validator
    manifest = manifest or resolve_manifest(vertical=base_schema["vertical"])
    schema_contract = schema_contract or manifest.contract("discovered_schema")
    schema_validator = schema_validator or get_schema_validator(manifest)
    valid_product_types = valid_product_types if valid_product_types is not None else manifest.product_types
    promoted_decisions = promoted_decisions if promoted_decisions is not None else manifest.promoted_decisions
    protected_fields = protected_fields if protected_fields is not None else manifest.protected_fields
    consensus_schema = deepcopy(base_schema)
    existing_fields = fields_by_name(consensus_schema.get("fields", []))

    for decision in decisions:
        if not decision_is_auto_promotable(
            decision, promoted_decisions, protected_fields
        ):
            continue
        patch_type = decision.patch_types[0]
        existing_field = existing_fields.get(decision.canonical_name)
        if patch_type == "update_description" and existing_field is None:
            continue
        if patch_type == "add_field" and (
            not (
                decision.applies_to
                or applies_to_from_group(decision.target_group, valid_product_types)
            )
            or (decision.field_type == "enum" and not decision.values)
        ):
            continue
        existing_fields[decision.canonical_name] = field_payload_from_decision(
            decision,
            existing_field,
            valid_product_types,
        )

    consensus_schema["fields"] = list(existing_fields.values())
    schema_validator(consensus_schema)
    artifact = build_success_artifact(
        artifact_type="discovered_schema",
        contract_version="1.0.0",
        data=consensus_schema,
        provenance=_local_provenance([Path(base_schema_path).as_posix()]),
        data_contract_schema=load_contract(schema_contract, manifest=manifest),
    )
    write_artifact(
        output_path, artifact, data_contract_schema=load_contract(schema_contract, manifest=manifest)
    )


def render_frequency_json(
    decisions: list[FieldDecision],
    output_path: str | Path,
    *,
    data_contract: str = "schema_refinement/field_frequency",
) -> None:
    data = {"fields": [decision.to_dict() for decision in decisions]}
    artifact = build_success_artifact(
        artifact_type="field_frequency",
        contract_version="1.0.0",
        data=data,
        provenance=_local_provenance(),
        data_contract=data_contract,
    )
    write_artifact(
        output_path, artifact, data_contract=data_contract
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
