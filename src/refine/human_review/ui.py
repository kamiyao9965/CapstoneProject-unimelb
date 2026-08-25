"""Streamlit UI for reviewing consensus schema proposals."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import streamlit as st
from src.common.json_codec import loads_json

from src.refine.human_review import (
    DECISIONS_FILENAME,
    QUEUE_FILENAME,
    REVIEWED_SCHEMA_FILENAME,
    apply_review_files,
    decisions_by_id,
    derive_status,
    empty_decisions,
    load_review_decisions,
    load_review_queue,
    remove_review_decision,
    save_review_decision,
)

DEFAULT_CONSENSUS_DIR = "outputs/private_health/consensus"


def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--consensus-dir", default=DEFAULT_CONSENSUS_DIR)
    args, _ = parser.parse_known_args()
    return args


def load_decisions_or_empty(path: Path) -> dict:
    if path.exists():
        return load_review_decisions(path)
    return empty_decisions()


def save_decision(
    decisions_path: Path,
    decisions: dict,
    item_id: str,
    action: str,
    notes: str,
    edited_update: dict | None = None,
) -> None:
    latest = save_review_decision(
        decisions_path, item_id, action, notes, edited_update
    )
    decisions.clear()
    decisions.update(latest)


def main() -> None:
    st.set_page_config(page_title="Schema Patch Review", layout="wide")
    cli = parse_cli_args()

    st.sidebar.title("Schema Patch Review")
    consensus_dir = Path(
        st.sidebar.text_input("Consensus directory", value=cli.consensus_dir)
    )
    queue_path = consensus_dir / QUEUE_FILENAME
    decisions_path = consensus_dir / DECISIONS_FILENAME

    if not queue_path.exists():
        st.error(
            f"No {QUEUE_FILENAME} in `{consensus_dir}`. "
            "Run a consensus sweep first (loop --consensus-runs N, or "
            "python src/refine/consensus.py)."
        )
        st.stop()

    queue = load_review_queue(queue_path)
    decisions = load_decisions_or_empty(decisions_path)
    status = derive_status(queue, decisions)
    updates = queue.get("updates", [])

    pending = sum(1 for value in status.values() if value == "pending")
    st.sidebar.metric("Progress", f"{len(updates) - pending}/{len(updates)} reviewed")
    st.sidebar.caption(
        f"Queue generated: {queue.get('metadata', {}).get('generated_at', '?')} | "
        f"runs: {queue.get('metadata', {}).get('total_runs', '?')}"
    )

    status_filter = st.sidebar.multiselect(
        "Review status", ["pending", "accepted", "rejected", "edited"], default=[]
    )
    decision_filter = st.sidebar.multiselect(
        "Suggested decision",
        sorted({u["suggested_decision"] for u in updates}),
        default=[],
    )
    patch_type_filter = st.sidebar.multiselect(
        "Patch type",
        sorted({p for u in updates for p in u.get("patch_types", [])}),
        default=[],
    )
    group_filter = st.sidebar.multiselect(
        "Target group",
        sorted({u.get("target_group", "") for u in updates} - {""}),
        default=[],
    )

    def visible(item: dict) -> bool:
        if status_filter and status[item["id"]] not in status_filter:
            return False
        if decision_filter and item["suggested_decision"] not in decision_filter:
            return False
        if patch_type_filter and not set(item.get("patch_types", [])) & set(
            patch_type_filter
        ):
            return False
        if group_filter and item.get("target_group") not in group_filter:
            return False
        return True

    visible_items = [item for item in updates if visible(item)]

    st.sidebar.divider()
    if st.sidebar.button("Apply decisions -> reviewed_schema.json", type="primary"):
        out_path, summary = apply_review_files(consensus_dir=consensus_dir)
        st.sidebar.success(
            f"Wrote {out_path}\n\n"
            f"applied {len(summary['applied'])} + edited {len(summary['edited'])}, "
            f"rejected {len(summary['rejected'])}, pending {len(summary['pending'])}"
        )
        if summary["pending"]:
            st.sidebar.warning(
                "Pending (NOT applied): " + ", ".join(summary["pending"])
            )

    st.title("Proposed schema updates")
    if not visible_items:
        st.info("No items match the current filters.")
        st.stop()

    labels = {
        f"{item['canonical_name']}  [{item['suggested_decision']} "
        f"{item['frequency']}, {status[item['id']]}]": item
        for item in visible_items
    }
    selected_label = st.radio("Queue", list(labels), label_visibility="collapsed")
    item = labels[selected_label]
    item_id = item["id"]

    st.divider()
    left, right = st.columns([3, 2])

    with left:
        _render_review_item(item, status[item_id])

    with right:
        _render_decision_panel(item, decisions, decisions_path)


def _render_review_item(item: dict, status_label: str) -> None:
    st.subheader(item["canonical_name"])
    st.write(
        f"**Suggested:** `{item['suggested_decision']}` | "
        f"**Support:** {item['frequency']} | "
        f"**Reject votes:** {item['reject_votes']} | "
        f"**Avg confidence:** {item['average_confidence']} | "
        f"**Status:** `{status_label}`"
    )
    st.write(
        f"**Patch types:** {', '.join(item.get('patch_types', [])) or 'n/a'} | "
        f"**Group:** {item.get('target_group') or 'n/a'} | "
        f"**Aliases:** {', '.join(item.get('aliases', [])) or 'none'}"
    )
    if item.get("needs_schema_edit"):
        st.error(
            "rename/merge/move changes cannot be represented as one field upsert. "
            "This item is audit-only; edit the base schema contract directly."
        )
    elif item.get("needs_manual_edit"):
        st.warning(
            "This proposal combines patch actions. Plain Accept is disabled; "
            "review and save one explicit field payload."
        )
    if item.get("has_conflict"):
        st.warning(
            "Patch runs disagreed on field type, applicability, requiredness, or "
            "enum values. Review the explicit payload before accepting."
        )

    st.markdown("**Proposed update**")
    st.json(item["proposed_update"])

    if item.get("rationale_samples"):
        st.markdown("**Model rationale**")
        for sample in item["rationale_samples"]:
            st.markdown(f"- {sample}")
    if item.get("reject_rationale_samples"):
        st.markdown("**Reject rationale** :red[(negative signal)]")
        for sample in item["reject_rationale_samples"]:
            st.markdown(f"- {sample}")
    with st.expander(f"Evidence documents ({len(item.get('evidence_documents', []))})"):
        for doc in item.get("evidence_documents", []):
            st.markdown(
                f"- `{doc.get('path', '?')}` - {doc.get('quote_or_summary', '')}"
            )


def _render_decision_panel(
    item: dict,
    decisions: dict,
    decisions_path: Path,
) -> None:
    item_id = item["id"]
    st.subheader("Your decision")
    existing = decisions_by_id(decisions).get(item_id, {})
    notes = st.text_input(
        "Reviewer notes",
        value=existing.get("reviewer_notes", ""),
        key=f"notes:{item_id}",
    )

    accept_col, reject_col, clear_col = st.columns(3)
    if accept_col.button(
        "Accept",
        key=f"accept:{item_id}",
        type="primary",
        disabled=bool(item.get("needs_manual_edit")),
    ):
        save_decision(decisions_path, decisions, item_id, "accept", notes)
        st.rerun()
    if reject_col.button("Reject", key=f"reject:{item_id}"):
        save_decision(decisions_path, decisions, item_id, "reject", notes)
        st.rerun()
    if clear_col.button(
        "Clear",
        key=f"clear:{item_id}",
        help="Remove the decision; item returns to pending",
    ):
        latest = remove_review_decision(decisions_path, item_id)
        decisions.clear()
        decisions.update(latest)
        st.rerun()

    st.markdown("**Edit then accept**")
    default_edit = existing.get("edited_update") or item["proposed_update"]
    edited_text = st.text_area(
        "Field payload (JSON)",
        value=json.dumps(default_edit, ensure_ascii=False, indent=2),
        height=220,
        key=f"edit:{item_id}",
    )
    if st.button(
        "Save edit & accept",
        key=f"save_edit:{item_id}",
        disabled=bool(item.get("needs_schema_edit")),
    ):
        try:
            payload = loads_json(edited_text)
            if not isinstance(payload, dict) or not payload.get("name"):
                raise ValueError("Edited payload must be a mapping with a name")
        except Exception as exc:
            st.error(f"Invalid JSON: {exc}")
        else:
            save_decision(decisions_path, decisions, item_id, "edit", notes, payload)
            st.rerun()
