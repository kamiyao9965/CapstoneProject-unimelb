from __future__ import annotations

import json
from pathlib import Path

from src.models import EvaluationReport


class EvaluationReporter:
    def write_json(self, reports: list[EvaluationReport], summary: dict[str, float], output_path: str | Path) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "summary": summary,
            "reports": [report.model_dump(mode="python") for report in reports],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def write_markdown(
        self, reports: list[EvaluationReport], summary: dict[str, float], output_path: str | Path
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
            f"- Normalization accuracy: {summary['normalization_accuracy']:.3f}",
            f"- Coverage: {summary['coverage']:.3f}",
            f"- Hallucination rate: {summary['hallucination_rate']:.3f}",
            f"- Matched documents: {summary.get('matched_documents', len(reports)):.0f}/{summary.get('total_documents', len(reports)):.0f}",
            f"- Unmatched documents: {summary.get('unmatched_documents', 0):.0f}",
            f"- Low-confidence GT matches: {summary.get('low_confidence_matches', 0):.0f}",
            f"- Fallback extractions in eval set: {summary.get('fallback_documents', 0):.0f}",
            f"- Extraction errors: {summary.get('extraction_errors', 0):.0f}",
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
                    f"- Precision: {report.field_precision:.3f}",
                    f"- Recall: {report.field_recall:.3f}",
                    f"- Field presence recall: {report.field_presence_recall:.3f}",
                    f"- Value accuracy: {report.value_accuracy:.3f}",
                    f"- Coverage: {report.coverage:.3f}",
                    f"- Hallucination rate: {report.hallucination_rate:.3f}",
                    "",
                ]
            )
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
