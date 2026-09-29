"""Presentation-only Streamlit checker for immutable quality review queues."""

from __future__ import annotations

import argparse
from pathlib import Path

import streamlit as st

from src.common.json_codec import dumps_json
from src.evaluation.quality_review import load_decisions, load_queue, quality_metrics, save_decision


DECISION_LABELS = {
    "Issue found": "issue_found",
    "No issue": "no_issue",
    "Uncertain": "uncertain",
}


def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quality-dir", default="outputs/travel_insurance/quality")
    args, _ = parser.parse_known_args()
    return args


def main() -> None:
    st.set_page_config(page_title="Extraction quality review", layout="wide")
    st.title("Extraction quality review")
    st.caption(
        "Check judge findings and a seeded sample of passes against the original PDF. "
        "Your decisions are separate from extraction JSON and PostgreSQL."
    )
    quality_dir = Path(st.sidebar.text_input(
        "Quality audit directory", value=parse_cli_args().quality_dir,
    ))
    queue_path = quality_dir / "review_queue.json"
    decisions_path = quality_dir / "review_decisions.json"
    try:
        queue = load_queue(queue_path)
        decisions = load_decisions(queue_path, decisions_path)
        metrics = quality_metrics(queue, decisions)
    except (OSError, ValueError) as exc:
        st.error(f"Could not open quality review queue: {exc}")
        st.stop()

    data = queue["data"]
    items = data["items"]
    prior = {entry["item_id"]: entry for entry in decisions["data"]["decisions"]}
    st.caption(
        f"Vertical: {data['vertical']} · Schema: {data['schema_version']} "
        f"({data['schema_sha256'][:12]}) · Queue: {data['queue_id'][:12]}"
    )
    columns = st.columns(4)
    columns[0].metric("Audited PDFs", metrics["audited_documents"])
    columns[1].metric("Judge-alerted PDFs", metrics["judge_alert_documents"])
    columns[2].metric("Review items", metrics["review_items"])
    columns[3].metric("Checked", metrics["reviewed_items"])
    st.caption(
        f"Human-confirmed alerts: {metrics['confirmed_alerts']} · "
        f"Dismissed alerts: {metrics['dismissed_alerts']} · "
        f"Misses in sampled passes: {metrics['sampled_pass_misses']} · "
        f"Still uncertain: {metrics['human_uncertain']}"
    )
    st.info("These are workload and calibration signals, not a measured extraction accuracy rate.")
    if not items:
        st.success("No items were selected for human review in this audit.")
        return

    pending_only = st.sidebar.checkbox("Pending only", value=True)
    visible = [item for item in items if not pending_only or item["item_id"] not in prior]
    if not visible:
        st.success("All selected items have a human decision. Uncheck Pending only to revisit one.")
        return
    labels = [
        f"{item['kind']} · {Path(item['source_document']).name} · "
        f"{item['field_name'] or 'document'} · {item['item_id'][:8]}"
        for item in visible
    ]
    selected_index = st.selectbox("Review item", range(len(visible)),
                                  format_func=lambda index: labels[index])
    item = visible[selected_index]
    item_id = item["item_id"]
    left, right = st.columns([3, 2])
    with left:
        st.subheader("Evidence and extracted value")
        st.write(f"**Source PDF:** `{item['source_document']}`")
        st.write(f"**Page:** {item['source_page'] or 'not supplied'}")
        st.write(f"**Judge concern:** {item['reason']}")
        if item["issue_type"]:
            st.write(f"**Issue type:** {item['issue_type']}")
        if item["source_quote"]:
            st.write(f"**Judge quote:** {item['source_quote']}")
            if item["citation_verified"]:
                st.caption("Quote text was found on the cited parsed page. Check its context in the PDF.")
            else:
                st.warning("Quote was not verified on that parsed page; check the source directly.")
        if item["field_name"]:
            st.write(f"**Product index:** {item['product_index']} · **Field:** `{item['field_name']}`")
            value_text = dumps_json(item["extracted_value"], ensure_ascii=False, indent=2)
            if len(value_text) > 20_000:
                st.warning("Extracted value display is limited to 20,000 characters; open the source artifact for the full value.")
                value_text = value_text[:20_000] + "…"
            st.code(value_text, language="json")
        st.caption(f"Extraction artifact: {item['source_artifact']}")
    with right:
        st.subheader("Human check")
        existing = prior.get(item_id)
        if existing:
            st.caption(f"Previous decision: {existing['decision']} by {existing['reviewer']}")
        labels_list = list(DECISION_LABELS)
        prior_label = next((label for label, value in DECISION_LABELS.items()
                            if existing and value == existing["decision"]), labels_list[0])
        label = st.selectbox("Decision", labels_list, index=labels_list.index(prior_label),
                             key=f"decision:{item_id}")
        notes = st.text_area("Notes", value=existing["notes"] if existing else "",
                             help="Required when an issue is found or the check remains uncertain.",
                             key=f"notes:{item_id}")
        reviewer = st.text_input("Reviewer name", key="reviewer")
        if st.button("Save human decision", type="primary", key=f"save:{item_id}"):
            try:
                save_decision(queue_path, decisions_path, item_id,
                              DECISION_LABELS[label], notes, reviewer)
            except (OSError, ValueError) as exc:
                st.error(str(exc))
            else:
                st.rerun()
