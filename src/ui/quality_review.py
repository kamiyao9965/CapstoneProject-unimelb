"""Presentation-only Streamlit checker for immutable quality review queues."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import streamlit as st

from src.common.json_codec import dumps_json
from src.evaluation.quality import load_quality_results
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
    results_path = quality_dir / "results.json"
    if results_path.is_file():
        try:
            results = load_quality_results(results_path)
        except (OSError, ValueError) as exc:
            st.error(f"Could not open quality results JSON: {exc}")
            st.stop()
        _render_results(results["data"], results_path)
    if not queue_path.is_file():
        st.info("Human review is not available yet. The JSON overview remains visible while the batch is partial. If all reports are complete, run --resume to verify the source PDFs and create the review queue without repeating successful judge calls.")
        return

    st.divider()
    st.subheader("Human review queue")
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


def _render_results(data: dict, results_path: Path) -> None:
    st.subheader("Judge results")
    st.caption(
        f"{data['vertical']} · schema {data['schema_version']} · "
        f"{data['provider']} / {data['model']} · {data['status']}"
    )
    columns = st.columns(4)
    columns[0].metric("Total PDFs", data["total_documents"])
    columns[1].metric("Judged", data["completed_documents"])
    columns[2].metric("Failed", data["failed_documents"])
    columns[3].metric("Pending", data["pending_documents"])
    st.download_button(
        "Download results.json", data=results_path.read_bytes(),
        file_name="results.json", mime="application/json",
    )
    st.caption("Judge verdicts are screening signals, not measured extraction accuracy.")
    entries = data["documents"]
    st.dataframe([
        {
            "PDF": _document_label(item),
            "Status": item["status"],
            "Verdict": item["report"]["verdict"] if item["report"] else "—",
            "Findings": len(item["report"]["findings"]) if item["report"] else 0,
            "Failure code": item["failure"]["code"] if item["failure"] else "—",
        }
        for item in entries
    ], hide_index=True, width="stretch")
    selected = st.selectbox(
        "PDF result", range(len(entries)),
        format_func=lambda index: (
            f"{entries[index]['status']} · {_document_label(entries[index])}"
        ),
    )
    item = entries[selected]
    with st.expander("Files and provenance"):
        st.text(f"Extraction artifact: {item['source_artifact']}")
        if item["report_path"]:
            st.text(f"Per-PDF report: {item['report_path']}")
    if item["status"] == "failed":
        failure = item["failure"]
        st.error("No valid quality report was produced for this PDF.")
        st.write(f"Failure kind: `{failure['kind']}` · code: `{failure['code']}` · attempts: {failure['attempts']}")
        if failure["paths"]:
            st.caption("Validation paths: " + ", ".join(failure["paths"]))
        return
    if item["status"] == "pending":
        st.info("No successful report is available for this PDF. It may be unattempted or a legacy failure without a saved diagnostic. Resume the audit to process it.")
        return
    report = item["report"]
    st.write(
        f"Verdict: `{report['verdict']}` · correctness: `{report['correctness']}` · "
        f"evidence: `{report['evidence_support']}` · uncertainty: `{report['uncertainty']}`"
    )
    st.text(report["summary"])
    if not report["findings"]:
        st.success("No judge findings in this report.")
    for index, finding in enumerate(report["findings"], start=1):
        with st.expander(f"Finding {index}: {finding['field_name'] or 'document'} · {finding['issue_type']}"):
            st.text(finding["reason"])
            st.write(f"Product index: {finding['product_index']} · PDF page: {finding['source_page']}")
            if finding["source_quote"]:
                st.text(f"Judge quote: {finding['source_quote']}")
                st.caption("Quote found on parsed page" if finding["citation_verified"]
                           else "Quote was not verified on the cited parsed page")
            st.code(dumps_json(finding["extracted_value"], ensure_ascii=False, indent=2),
                    language="json")


def _document_label(item: dict) -> str:
    name = Path(item["source_document"] or item["source_artifact"]).name
    name = re.sub(r"^[a-f0-9]{64}_", "", name)
    artifact_path = Path(item["source_artifact"])
    if artifact_path.parent.name.lower() == "pds":
        return f"{artifact_path.parent.parent.name} · {name}"
    return name
