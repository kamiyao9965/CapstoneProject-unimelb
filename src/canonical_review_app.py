"""Streamlit approval UI for Travel Canonical Schema and DB mapping."""

from __future__ import annotations

import argparse
from pathlib import Path

import streamlit as st
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from src.common.json_artifacts import read_artifact, write_text_output
from src.common.json_codec import dumps_json
from src.schema.canonical import approve_canonical_schema
from src.storage.canonical import preview_vertical_storage_metadata
from src.verticals.travel_insurance import build_travel_canonical_candidate


def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--schema",
        default="outputs/travel_insurance/refine/round_1/consensus/reviewed_schema.json",
    )
    parser.add_argument(
        "--output",
        default="outputs/travel_insurance/canonical_schema_approved.json",
    )
    args, _ = parser.parse_known_args()
    return args


def main() -> None:
    st.set_page_config(page_title="Travel Schema Mapping Review", layout="wide")
    args = parse_cli_args()
    st.title("Travel Canonical Schema & database mapping")
    st.caption(
        "This page makes no LLM or database call. Approval creates a new JSON file; "
        "it does not execute the DDL."
    )
    schema_path = Path(st.sidebar.text_input("Reviewed schema", value=args.schema))
    output_path = Path(st.sidebar.text_input("Approved output", value=args.output))
    if not schema_path.exists():
        st.error(f"Reviewed schema not found: `{schema_path}`")
        st.stop()

    discovered = read_artifact(
        schema_path,
        expected_type="discovered_schema",
        data_contract="travel_insurance/discovered_schema",
    )["data"]
    candidate = build_travel_canonical_candidate(discovered)
    fields = candidate["fields"]
    st.subheader("Deterministic field mapping")
    st.dataframe(
        [
            {
                "field": field["name"],
                "type": field["type"],
                "required": field["required"],
                "nullable": field["nullable"],
                "storage": field["storage"]["strategy"],
                "target": field["storage"].get("target")
                or field["storage"].get("column")
                or candidate["extension"]["attributes_column"],
            }
            for field in fields
        ],
        use_container_width=True,
        hide_index=True,
    )
    ddl = str(
        CreateTable(preview_vertical_storage_metadata(candidate).table).compile(
            dialect=postgresql.dialect()
        )
    )
    with st.expander("PostgreSQL DDL preview"):
        st.code(ddl, language="sql")

    st.subheader("Human approval")
    reviewer = st.text_input("Reviewer", help="Name recorded in the approval contract")
    rationale = st.text_area(
        "Rationale",
        help="Confirm field meaning, identity bindings, and storage mapping were reviewed",
    )
    confirmed = st.checkbox(
        "I reviewed the identity fields and every extension-column/JSONB mapping."
    )
    if st.button("Approve Canonical Schema", type="primary", disabled=not confirmed):
        try:
            approved = approve_canonical_schema(
                candidate,
                reviewer=reviewer,
                rationale=rationale,
            )
            write_text_output(
                output_path,
                dumps_json(approved, ensure_ascii=False, indent=2) + "\n",
            )
        except Exception as exc:
            st.error(str(exc))
        else:
            st.success(f"Approved schema written to `{output_path}`. No SQL was executed.")


if __name__ == "__main__":
    main()
