"""Local Streamlit operator console for the project's allowlisted CLI flows."""

from __future__ import annotations

import shlex

import streamlit as st

from src.tool_ui.commands import CommandResult, build_command, run_command
from src.tool_ui.forms import OPERATION_LABELS, render_operation_form


st.set_page_config(page_title="Insurance schema operations", page_icon="🧰", layout="wide")
st.title("Insurance schema operations")
st.caption(
    "Configure and preview the existing CLI workflows. Credentials stay in the "
    "process environment, and commands never run until you confirm them."
)

operation_label = st.selectbox("Operation", list(OPERATION_LABELS))
operation = OPERATION_LABELS[operation_label]
if st.session_state.get("active_operation") != operation:
    st.session_state["active_operation"] = operation
    st.session_state.pop("tool_result", None)

form_state = render_operation_form(operation_label)
validation_error: str | None = None
command: list[str] | None = None
try:
    command = build_command(form_state.request)
except ValueError as exc:
    validation_error = str(exc)

st.subheader("Command preview")
if command:
    st.code(shlex.join(command), language="bash")
else:
    st.code("Complete the required fields to preview the command.", language="text")
if validation_error:
    st.info(validation_error)

ready = command is not None and form_state.confirmed
if form_state.confirmation_message and not form_state.confirmed:
    st.caption("Confirm the operation above to enable Run.")

if st.button("Run operation", key="run-command", type="primary", disabled=not ready):
    assert command is not None
    with st.spinner("Running the project CLI…"):
        st.session_state["tool_result"] = run_command(command)

result = st.session_state.get("tool_result")
if isinstance(result, CommandResult):
    st.divider()
    st.subheader("Latest result")
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
