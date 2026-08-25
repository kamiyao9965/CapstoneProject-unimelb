"""Streamlit form controls for the allowlisted project operations."""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from src.tool_ui.commands import CommandRequest


OPERATION_LABELS = {
    "Schema discovery": "discover",
    "Single PDF extraction": "extract",
    "Batch extraction": "batch",
    "Travel document acquisition": "crawl",
    "Travel refinement": "refine",
    "Compile Canonical Schema": "canonical_compile",
    "Initialize PostgreSQL storage": "storage_init",
    "Load extraction into PostgreSQL": "storage_load",
}
VERTICAL_LABELS = {
    "Private Health": "private_health",
    "Travel Insurance": "travel_insurance",
}
PROVIDER_LABELS = {
    "Environment default": None,
    "OpenAI": "openai",
    "Anthropic": "anthropic",
    "DeepSeek": "deepseek",
}


@dataclass(frozen=True)
class FormState:
    request: CommandRequest
    confirmed: bool
    confirmation_message: str | None


def render_operation_form(operation_label: str) -> FormState:
    """Render controls for one operation and return its command request."""
    operation = OPERATION_LABELS[operation_label]
    st.subheader(operation_label)
    renderer = {
        "discover": _discovery,
        "extract": _extract,
        "batch": _batch,
        "crawl": _crawl,
        "refine": _refine,
        "canonical_compile": _canonical_compile,
        "storage_init": _storage_init,
        "storage_load": _storage_load,
    }[operation]
    options, confirmation = renderer()
    confirmed = True
    if confirmation:
        confirmed = st.checkbox(confirmation, key=f"confirm-{operation}")
    return FormState(
        request=CommandRequest(operation=operation, options=options),
        confirmed=confirmed,
        confirmation_message=confirmation,
    )


def _discovery() -> tuple[dict[str, object], str]:
    vertical = _vertical()
    left, right = st.columns(2)
    with left:
        samples = st.text_area(
            "Specific PDF paths (optional, one per line)",
            help="Leave blank to sample from the selected vertical's configured input root.",
        )
        input_root = st.text_input("Input root override (optional)")
        categories = st.text_area("Category filters (optional, one per line)")
        per_category = st.number_input("PDFs per category", min_value=1, value=5)
        seed = st.number_input("Sampling seed", value=42)
    with right:
        provider = _provider()
        model = st.text_input("Model override (optional)")
        output = st.text_input("Schema output path (optional)")
        timeout = st.number_input("API timeout (seconds)", min_value=1.0, value=600.0)
    options = {
        "vertical": vertical,
        "samples": samples,
        "input_root": input_root,
        "categories": categories,
        "per_category": per_category,
        "seed": seed,
        "provider": provider,
        "model": model,
        "output": output,
        "timeout": timeout,
    }
    return options, "I understand this operation can call an LLM and incur cost."


def _extract() -> tuple[dict[str, object], str]:
    vertical = _vertical()
    left, right = st.columns(2)
    with left:
        pdf = st.text_input("PDF path *")
        schema = st.text_input("Approved schema path *")
        output = st.text_input("Output JSON path (optional)")
    with right:
        provider = _provider()
        model = st.text_input("Model override (optional)")
    return {
        "vertical": vertical,
        "pdf": pdf,
        "schema": schema,
        "output": output,
        "provider": provider,
        "model": model,
    }, "I understand this operation can call an LLM and incur cost."


def _batch() -> tuple[dict[str, object], str]:
    vertical = _vertical()
    left, right = st.columns(2)
    with left:
        schema = st.text_input("Approved schema path *")
        input_root = st.text_input("Input root override (optional)")
        evaluate = st.checkbox(
            "Evaluate against labels",
            disabled=vertical != "private_health",
            help="Evaluation is currently available only for Private Health.",
        )
    with right:
        provider = _provider()
        model = st.text_input("Model override (optional)")
    return {
        "vertical": vertical,
        "schema": schema,
        "input_root": input_root,
        "evaluate": evaluate,
        "provider": provider,
        "model": model,
    }, "I understand this operation can make multiple LLM calls and incur cost."


def _crawl() -> tuple[dict[str, object], str]:
    left, right = st.columns(2)
    with left:
        insurers = st.text_area("Insurer codes (optional, one per line)")
        config = st.text_input("Source config override (optional)")
        data_root = st.text_input("PDF data root override (optional)")
        output_root = st.text_input("Acquisition output override (optional)")
    with right:
        discovery_only = st.checkbox("Discovery only (do not download PDFs)", value=True)
        include_archived = st.checkbox("Include archived documents")
    return {
        "vertical": "travel_insurance",
        "insurers": insurers,
        "config": config,
        "data_root": data_root,
        "output_root": output_root,
        "discovery_only": discovery_only,
        "include_archived": include_archived,
    }, "I understand this operation accesses configured public insurance websites."


def _refine() -> tuple[dict[str, object], str]:
    st.info(
        "Travel refinement defaults to five independent proposal runs, consensus "
        "voting, and a human review queue before holdout extraction."
    )
    left, right = st.columns(2)
    with left:
        input_root = st.text_input("Input root override (optional)")
        per_category = st.number_input("Discovery PDFs per category", min_value=1, value=5)
        seed = st.number_input("Discovery seed", value=42)
        consensus_runs = st.number_input("Consensus proposal runs", min_value=2, value=5)
        rounds = st.number_input("Maximum rounds", min_value=1, value=1)
    with right:
        provider = _provider()
        model = st.text_input("Model override (optional)")
        output_dir = st.text_input("Refinement output directory (optional)")
        timeout = st.number_input("API timeout (seconds)", min_value=1.0, value=600.0)
        review_ui = st.checkbox("Stop for human review", value=True)
        resume_review = st.text_input(
            "Reviewed round directory (optional)",
            help="Use after review decisions have produced consensus/reviewed_schema.json.",
        )
        resume_feedback = st.text_input("Validated feedback artifact (optional)")
    return {
        "vertical": "travel_insurance",
        "input_root": input_root,
        "per_category": per_category,
        "seed": seed,
        "provider": provider,
        "model": model,
        "out_dir": output_dir,
        "timeout": timeout,
        "rounds": rounds,
        "review_ui": review_ui,
        "consensus_runs": consensus_runs,
        "resume_review": resume_review,
        "resume_feedback": resume_feedback,
    }, "I understand five consensus runs can multiply LLM usage and cost."


def _canonical_compile() -> tuple[dict[str, object], None]:
    st.caption("Compiles reviewed schema and mapping artifacts; it does not apply database DDL.")
    schema = st.text_input("Human-approved schema path *")
    output_dir = st.text_input("Compiled artifact output directory *")
    return {
        "vertical": "travel_insurance",
        "schema": schema,
        "output_dir": output_dir,
    }, None


def _storage_init() -> tuple[dict[str, object], str]:
    st.warning("This creates missing PostgreSQL tables from the approved Canonical Schema.")
    schema = st.text_input("Canonical schema override (optional)")
    database_url_env = st.text_input(
        "Database URL environment variable", value="KONKRD_DATABASE_URL"
    )
    return {
        "vertical": "travel_insurance",
        "schema": schema,
        "database_url_env": database_url_env,
    }, "I understand this operation can create tables in the configured database."


def _storage_load() -> tuple[dict[str, object], str]:
    left, right = st.columns(2)
    with left:
        artifact = st.text_input("Validated extraction artifact path *")
        insurer_code = st.text_input("Insurer code *")
        schema = st.text_input("Canonical schema override (optional)")
    with right:
        database_url_env = st.text_input(
            "Database URL environment variable", value="KONKRD_DATABASE_URL"
        )
    return {
        "vertical": "travel_insurance",
        "artifact": artifact,
        "insurer_code": insurer_code,
        "schema": schema,
        "database_url_env": database_url_env,
    }, "I understand this operation writes validated records to the configured database."


def _vertical() -> str:
    label = st.selectbox("Insurance vertical", list(VERTICAL_LABELS))
    return VERTICAL_LABELS[label]


def _provider() -> str | None:
    label = st.selectbox("LLM provider", list(PROVIDER_LABELS))
    return PROVIDER_LABELS[label]
