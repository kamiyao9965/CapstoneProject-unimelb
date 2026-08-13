from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.models import EvaluationReport


class EvaluationReporter:
    def write_json(self, reports: list[EvaluationReport], summary: dict[str, Any], output_path: str | Path) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "summary": summary,
            "reports": [report.model_dump(mode="python") for report in reports],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def write_markdown(
        self, reports: list[EvaluationReport], summary: dict[str, Any], output_path: str | Path
    ) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Extraction Evaluation",
            "",
            "## Summary",
            "",
            f"- Field precision: {summary['field_precision']:.3f}",
            f"- Field recall: {summary['field_recall']:.3f}",
            f"- Field presence recall: {summary.get('field_presence_recall', summary['coverage']):.3f}",
            f"- Value accuracy: {summary.get('value_accuracy', 0.0):.3f}",
            f"- Canonical-name recall: {summary.get('canonical_name_recall', summary['normalization_accuracy']):.3f}",
            f"- Coverage: {summary['coverage']:.3f}",
            f"- Hallucination rate: {_format_optional_metric(summary.get('hallucination_rate'))}",
            f"- Product accuracy: {summary.get('product_accuracy', 0.0):.3f}",
            f"- Hospital category recall: {summary.get('hospital_category_recall', 0.0):.3f}",
            f"- Hospital coverage accuracy: {summary.get('hospital_coverage_accuracy', 0.0):.3f}",
            f"- Extras service precision: {summary.get('extras_service_precision', 0.0):.3f}",
            f"- Extras service recall: {summary.get('extras_service_recall', 0.0):.3f}",
            f"- Extras waiting-period accuracy: {summary.get('extras_waiting_period_accuracy', 0.0):.3f}",
            f"- Extras limit accuracy: {summary.get('extras_limit_accuracy', 0.0):.3f}",
            f"- Matched documents: {summary.get('matched_documents', len(reports)):.0f}/{summary.get('total_documents', len(reports)):.0f}",
            f"- Unmatched documents: {summary.get('unmatched_documents', 0):.0f}",
            f"- Low-confidence GT matches: {summary.get('low_confidence_matches', 0):.0f}",
            f"- Ambiguous GT matches: {summary.get('ambiguous_matches', 0):.0f}",
            f"- Fallback extractions in eval set: {summary.get('fallback_documents', 0):.0f}",
            f"- Extraction errors: {summary.get('extraction_errors', 0):.0f}",
            "",
            "## Micro averages",
            "",
            *_metric_lines(summary.get("micro", {})),
            "",
            "## High-confidence GT matches only",
            "",
            *_metric_lines(summary.get("high_confidence_only", {}).get("macro", {})),
            f"- Documents: {summary.get('high_confidence_only', {}).get('matched_documents', 0):.0f}",
            f"- Excluded low-confidence documents: {summary.get('high_confidence_only', {}).get('excluded_low_confidence_documents', 0):.0f}",
            "",
            "## PHIS document classes",
            "",
            *[
                f"- {name}: {count}"
                for name, count in sorted(summary.get("document_classes", {}).items())
            ],
            "",
            "## Per-document",
            "",
        ]
        for report in reports:
            lines.extend(
                [
                    f"### {Path(report.source_path).name}",
                    "",
                    f"- Product key: {report.product_key or 'unmatched'}",
                    f"- GT match score: {report.match_score:.3f}" if report.match_score is not None else "- GT match score: n/a",
                    f"- Low-confidence match: {str(report.low_confidence_match).lower()}",
                    f"- PHIS document class: {report.phis_document_class}",
                    f"- PHIS classification evidence: `{json.dumps(report.phis_classification, sort_keys=True)}`",
                    f"- Precision: {report.field_precision:.3f}",
                    f"- Recall: {report.field_recall:.3f}",
                    f"- Field presence recall: {report.field_presence_recall:.3f}",
                    f"- Value accuracy: {report.value_accuracy:.3f}",
                    f"- Coverage: {report.coverage:.3f}",
                    f"- Hallucination rate: {_format_optional_metric(report.hallucination_rate)}",
                    f"- Section metrics: `{json.dumps(report.section_metrics, sort_keys=True)}`",
                    f"- Hallucinations by section: `{json.dumps(report.hallucinations_by_section, sort_keys=True)}`",
                    "",
                ]
            )
        path.write_text("\n".join(lines), encoding="utf-8")
        return path


def _format_optional_metric(value: float | None) -> str:
    return f"{value:.3f}" if value is not None else "not evaluated"


def _metric_lines(metrics: dict[str, Any]) -> list[str]:
    labels = {
        "field_precision": "Field precision",
        "field_recall": "Field recall",
        "field_presence_recall": "Field presence recall",
        "value_accuracy": "Value accuracy",
        "canonical_name_recall": "Canonical-name recall",
        "product_accuracy": "Product accuracy",
        "hospital_category_precision": "Hospital category precision",
        "hospital_category_recall": "Hospital category recall",
        "hospital_coverage_accuracy": "Hospital coverage accuracy",
        "extras_service_precision": "Extras service precision",
        "extras_service_recall": "Extras service recall",
        "extras_waiting_period_accuracy": "Extras waiting-period accuracy",
        "extras_limit_accuracy": "Extras limit accuracy",
    }
    return [f"- {label}: {metrics[key]:.3f}" for key, label in labels.items() if key in metrics]
