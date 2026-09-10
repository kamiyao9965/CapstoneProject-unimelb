"""Local Streamlit operator console for the project's allowlisted CLI flows."""

from __future__ import annotations

import shlex
import hashlib

import streamlit as st

from src.tool_ui.commands import CommandResult, build_command, run_command
from src.tool_ui.forms import OPERATION_LABELS, render_operation_form
from src.verticals.manifest import discover_manifests, OPERATION_CAPABILITIES


st.set_page_config(page_title="Insurance schema operations", page_icon="🧰", layout="wide")
st.title("Insurance schema operations")
st.caption(
    "Configure and preview the existing CLI workflows. Credentials stay in the "
    "process environment, and commands never run until you confirm them."
)

try:
    manifests = discover_manifests()
    if not manifests:
        raise ValueError("No vertical manifests found in configs/.")
except ValueError as exc:
    st.error(str(exc))
    st.stop()
vertical = st.selectbox(
    "Insurance vertical", list(manifests), key="vertical",
    format_func=lambda code: manifests[code].display_name,
)
manifest = manifests[vertical]
st.caption(f"Current vertical: {vertical} · Manifest: {manifest.source_path}")
operations = [label for label, code in OPERATION_LABELS.items()
              if manifest.supports(OPERATION_CAPABILITIES[code])]
if st.session_state.get("operation") not in operations:
    st.session_state.pop("operation", None)
operation_label = st.selectbox("Operation", operations, key="operation")
operation = OPERATION_LABELS[operation_label]
scope = (vertical, operation)
if st.session_state.get("active_scope") != scope:
    st.session_state["active_scope"] = scope
    st.session_state.pop("tool_result", None)
    for key in list(st.session_state):
        if key.startswith(("_form:", "_confirm:")):
            del st.session_state[key]

form_state = render_operation_form(operation_label, manifest)
validation_error: str | None = None
command: list[str] | None = None
try:
    command = build_command(form_state.request)
except ValueError as exc:
    validation_error = str(exc)

st.header("Command preview")
if command:
    st.code(shlex.join(command), language="bash")
else:
    st.code("Complete the required fields to preview the command.", language="text")
if validation_error:
    st.info(validation_error)

confirmed = True
if form_state.confirmation_message:
    # Confirmation belongs to the exact command and configuration, not the widget label.
    fingerprint = hashlib.sha256(
        (repr(command) + manifest.source_path.read_text()).encode()
    ).hexdigest()
    confirmed = st.checkbox(form_state.confirmation_message, key=f"_confirm:{fingerprint}")
ready = command is not None and confirmed
if form_state.confirmation_message and not confirmed:
    st.caption("Confirm the operation above to enable Run.")

if st.button("Run operation", key="run-command", type="primary", disabled=not ready):
    assert command is not None
    with st.spinner("Running the project CLI…"):
        st.session_state["tool_result"] = (scope, run_command(command))

saved_result = st.session_state.get("tool_result")
if saved_result and saved_result[0] == scope and isinstance(saved_result[1], CommandResult):
    result = saved_result[1]
    st.divider()
    st.header("Latest result")
    st.caption(f"Run vertical: {vertical} · Operation: {operation_label}")
    st.code(shlex.join(result.command), language="bash")
    if result.timed_out:
        st.warning("The operation timed out. Partial output is shown below.")
    elif result.succeeded:
        st.success("Operation completed successfully.")
    else:
        st.error(f"Operation failed with exit code {result.exit_code}.")
    exit_column, duration_column = st.columns(2)
    exit_column.metric("Exit code", result.exit_code)
    duration_column.metric("Duration", f"{result.duration_seconds:.1f} s")
    if result.output:
        st.code(result.output, language="text")
    else:
        st.info("The command completed without console output.")
