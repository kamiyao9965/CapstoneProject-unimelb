"""Thin CLI forms. The workbench clears _form keys whenever vertical/operation changes."""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from src.tool_ui.commands import CommandRequest
from src.verticals.manifest import VerticalManifest


OPERATION_LABELS = {
    "Schema discovery": "discover",
    "Single PDF extraction": "extract",
    "Batch extraction": "batch",
    "Document acquisition": "crawl",
    "Schema refinement": "refine",
    "Compile Canonical Schema": "canonical_compile",
    "Initialize PostgreSQL storage": "storage_init",
    "Load extraction into PostgreSQL": "storage_load",
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
    confirmation_message: str | None


def render_operation_form(operation_label: str, manifest: VerticalManifest) -> FormState:
    """Render controls for one operation and return its command request."""
    operation = OPERATION_LABELS[operation_label]
    st.header(operation_label)
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
    options, confirmation = renderer(manifest)
    options.update(vertical=manifest.vertical, manifest=str(manifest.source_path))
    return FormState(
        request=CommandRequest(operation=operation, options=options),
        confirmation_message=confirmation,
    )


def _discovery(manifest: VerticalManifest) -> tuple[dict[str, object], str]:
    left, right = st.columns(2)
    with left:
        samples = st.text_area(
            "Specific PDF paths (optional, one per line)",
            help="Leave blank to sample from the selected vertical's configured input root.",
            key="_form:samples",
        )
        input_root = st.text_input("Input root override (optional)", key="_form:input_root")
        categories = st.text_area("Category filters (optional, one per line)", key="_form:categories")
        per_category = st.number_input("PDFs per category", min_value=1, value=5, key="_form:per_category")
        seed = st.number_input("Sampling seed", value=42, key="_form:seed")
    with right:
        provider = _provider(manifest)
        model = st.text_input("Model override (optional)", key="_form:model")
        output = st.text_input("Schema output path (optional)", key="_form:output")
        timeout = st.number_input("API timeout (seconds)", min_value=1.0, value=600.0, key="_form:timeout")
    options = {
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


def _extract(manifest: VerticalManifest) -> tuple[dict[str, object], str]:
    left, right = st.columns(2)
    with left:
        pdf = st.text_input("PDF path *", key="_form:pdf")
        schema = st.text_input("Approved schema path *", key="_form:schema")
        output = st.text_input("Output JSON path (optional)", key="_form:output")
    with right:
        provider = _provider(manifest)
        model = st.text_input("Model override (optional)", key="_form:model")
    return {
        "pdf": pdf,
        "schema": schema,
        "output": output,
        "provider": provider,
        "model": model,
    }, "I understand this operation can call an LLM and incur cost."


def _batch(manifest: VerticalManifest) -> tuple[dict[str, object], str]:
    left, right = st.columns(2)
    with left:
        schema = st.text_input("Approved schema path *", key="_form:schema")
        input_root = st.text_input("Input root override (optional)", key="_form:input_root")
        evaluate = st.checkbox(
            "Evaluate against labels",
            disabled=not manifest.supports("evaluation"),
            help="Requires the selected manifest to enable labelled evaluation.",
            key="_form:evaluate",
        )
    with right:
        provider = _provider(manifest)
        model = st.text_input("Model override (optional)", key="_form:model")
    return {
        "schema": schema,
        "input_root": input_root,
        "evaluate": evaluate,
        "provider": provider,
        "model": model,
    }, "I understand this operation can make multiple LLM calls and incur cost."


def _crawl(manifest: VerticalManifest) -> tuple[dict[str, object], str]:
    left, right = st.columns(2)
    with left:
        insurers = st.text_area("Insurer codes (optional, one per line)", key="_form:insurers")
        config = st.text_input("Source config override (optional)", key="_form:config")
        data_root = st.text_input("PDF data root override (optional)", key="_form:data_root")
        output_root = st.text_input("Acquisition output override (optional)", key="_form:output_root")
    with right:
        discovery_only = st.checkbox(
            "Discovery only (do not download PDFs)",
            value=True,
            key="_form:discovery_only",
        )
        include_archived = st.checkbox("Include archived documents", key="_form:include_archived")
    return {
        "insurers": insurers,
        "config": config,
        "data_root": data_root,
        "output_root": output_root,
        "discovery_only": discovery_only,
        "include_archived": include_archived,
    }, "I understand this operation accesses configured public insurance websites."


def _refine(manifest: VerticalManifest) -> tuple[dict[str, object], str]:
    st.info(
        f"{manifest.display_name}: {manifest.consensus_runs} proposal run(s) by default. "
        "Review controls whether extraction waits for human decisions."
    )
    left, right = st.columns(2)
    with left:
        input_root = st.text_input("Input root override (optional)", key="_form:input_root")
        per_category = st.number_input(
            "Discovery PDFs per category",
            min_value=1,
            value=5,
            key="_form:per_category",
        )
        seed = st.number_input("Discovery seed", value=42, key="_form:seed")
        consensus_runs = st.number_input(
            "Consensus proposal runs",
            min_value=1,
            value=manifest.consensus_runs,
            key="_form:consensus_runs",
        )
        rounds = st.number_input("Maximum rounds", min_value=1, value=1, key="_form:rounds")
    with right:
        provider = _provider(manifest)
        model = st.text_input("Model override (optional)", key="_form:model")
        output_dir = st.text_input("Refinement output directory (optional)", key="_form:output_dir")
        timeout = st.number_input("API timeout (seconds)", min_value=1.0, value=600.0, key="_form:timeout")
        review_ui = st.checkbox("Stop for human review", value=True, key="_form:review_ui")
        resume_review = st.text_input(
            "Reviewed round directory (optional)",
            help="Use after review decisions have produced consensus/reviewed_schema.json.",
            key="_form:resume_review",
        )
        resume_feedback = st.text_input("Validated feedback artifact (optional)", key="_form:resume_feedback")
    return {
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
    }, "I understand proposal runs and extraction can make multiple LLM calls and incur cost."


def _canonical_compile(manifest: VerticalManifest) -> tuple[dict[str, object], None]:
    st.caption("Compiles reviewed schema and mapping artifacts; it does not apply database DDL.")
    schema = st.text_input("Human-approved schema path *", key="_form:schema")
    output_dir = st.text_input("Compiled artifact output directory *", key="_form:output_dir")
    return {
        "schema": schema,
        "output_dir": output_dir,
    }, None


def _storage_init(manifest: VerticalManifest) -> tuple[dict[str, object], str]:
    st.warning("This creates missing PostgreSQL tables from the approved Canonical Schema.")
    schema = st.text_input("Canonical schema override (optional)", key="_form:schema")
    database_url_env = st.text_input(
        "Database URL environment variable",
        value="KONKRD_DATABASE_URL",
        key="_form:database_url_env",
    )
    return {
        "schema": schema,
        "database_url_env": database_url_env,
    }, "I understand this operation can create tables in the configured database."


def _storage_load(manifest: VerticalManifest) -> tuple[dict[str, object], str]:
    left, right = st.columns(2)
    with left:
        artifact = st.text_input("Validated extraction artifact path *", key="_form:artifact")
        insurer_code = st.text_input("Insurer code *", key="_form:insurer_code")
        schema = st.text_input("Canonical schema override (optional)", key="_form:schema")
    with right:
        database_url_env = st.text_input(
            "Database URL environment variable",
            value="KONKRD_DATABASE_URL",
            key="_form:database_url_env",
        )
    return {
        "artifact": artifact,
        "insurer_code": insurer_code,
        "schema": schema,
        "database_url_env": database_url_env,
    }, "I understand this operation writes validated records to the configured database."


def _provider(manifest: VerticalManifest) -> str | None:
    label = st.selectbox("LLM provider", list(PROVIDER_LABELS), key="_form:label")
    return PROVIDER_LABELS[label]
