"""Streamlit approval UI for configured Canonical Schema and DB mapping."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import streamlit as st
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from src.common.json_artifacts import write_text_output
from src.common.json_codec import dumps_json
from src.schema.canonical import approve_canonical_schema, build_canonical_candidate
from src.schema.loader import load_schema_data
from src.verticals.manifest import discover_manifests
from src.storage.canonical import preview_vertical_storage_metadata


def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest")
    parser.add_argument("--schema")
    parser.add_argument("--output")
    args, _ = parser.parse_known_args()
    return args


def main() -> None:
    st.set_page_config(page_title="Schema Mapping Review", layout="wide")
    args = parse_cli_args()
    st.title("Canonical Schema & database mapping")
    st.caption(
        "This page makes no LLM or database call. Approval creates a new JSON file; "
        "it does not execute the DDL."
    )
    manifests = {code: m for code, m in discover_manifests().items() if m.supports("storage")}
    if args.manifest:
        from src.verticals.manifest import resolve_manifest
        selected = resolve_manifest(args.manifest, operation="canonical_compile")
        manifests = {selected.vertical: selected}
    if not manifests:
        st.info("No configured vertical supports Canonical storage.")
        st.stop()
    vertical = st.sidebar.selectbox("Insurance vertical", list(manifests), format_func=lambda code: manifests[code].display_name)
    manifest = manifests[vertical]
    root = manifest.path("output_root")
    schema_path = Path(st.sidebar.text_input("Reviewed schema", value=args.schema or str(root / "refine/round_1/consensus/reviewed_schema.json"), key=f"schema:{vertical}"))
    output_path = Path(st.sidebar.text_input("Approved output", value=args.output or str(root / "canonical_schema_approved.json"), key=f"output:{vertical}"))
    try:
        discovered = load_schema_data(schema_path, manifest)
        candidate = build_canonical_candidate(discovered, manifest)
    except (OSError, ValueError) as exc:
        st.error(str(exc))
        st.stop()
    scope = hashlib.sha256((str(schema_path.resolve()) + str(output_path.resolve()) + dumps_json(candidate, sort_keys=True)).encode()).hexdigest()
    st.caption(f"Vertical: {vertical} · Source schema: {discovered['version']} · Approval: candidate")
    fields = candidate["fields"]
    st.header("Deterministic field mapping")
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

    st.header("Human approval")
    reviewer = st.text_input("Reviewer", key=f"reviewer:{scope}", help="Name recorded in the approval contract")
    rationale = st.text_area(
        "Rationale", key=f"rationale:{scope}",
        help="Confirm field meaning, identity bindings, and storage mapping were reviewed",
    )
    confirmed = st.checkbox(
        "I reviewed the identity fields and every extension-column/JSONB mapping.", key=f"confirm:{scope}"
    )
    if st.button("Approve Canonical Schema", type="primary", disabled=not (confirmed and reviewer.strip() and rationale.strip()), key=f"approve:{scope}"):
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
