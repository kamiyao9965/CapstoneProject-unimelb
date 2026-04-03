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
            f"- Normalization accuracy: {summary['normalization_accuracy']:.3f}",
            f"- Coverage: {summary['coverage']:.3f}",
            f"- Hallucination rate: {summary['hallucination_rate']:.3f}",
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
                    f"- Precision: {report.field_precision:.3f}",
                    f"- Recall: {report.field_recall:.3f}",
                    f"- Coverage: {report.coverage:.3f}",
                    f"- Hallucination rate: {report.hallucination_rate:.3f}",
                    "",
                ]
            )
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
