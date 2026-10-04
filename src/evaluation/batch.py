"""Per-document labelled evaluation and reporting for one extraction batch."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from src.common.models import EvaluationReport, ExtractionResult
from src.evaluation.metrics import ExtractionEvaluator, PrivateHealthGroundTruthStore
from src.evaluation.reporter import EvaluationReporter


@dataclass
class BatchEvaluation:
    gt_store: PrivateHealthGroundTruthStore
    evaluator: ExtractionEvaluator
    reporter: EvaluationReporter
    reports: list[EvaluationReport] = field(default_factory=list, init=False)
    diagnostics: list[dict[str, object]] = field(default_factory=list, init=False)
    unmatched_documents: int = field(default=0, init=False)
    low_confidence_matches: int = field(default=0, init=False)
    ambiguous_matches: int = field(default=0, init=False)

    def evaluate_one(self, pdf_path: Path, result: ExtractionResult) -> None:
        product_match, ground_truth = self.gt_store.load_ground_truth(pdf_path)
        if ground_truth:
            report = self.evaluator.evaluate(
                extracted=result,
                ground_truth=ground_truth,
                product_key=product_match.id_master if product_match else None,
            )
            if product_match:
                report.match_score = product_match.match_score
                report.low_confidence_match = product_match.low_confidence_match
                if product_match.low_confidence_match:
                    self.low_confidence_matches += 1
            self.reports.append(report)
        else:
            self.unmatched_documents += 1
            self.diagnostics.append({
                "source_path": str(pdf_path),
                "issue": "unmatched",
                "candidates": self.gt_store.rank_pdf_candidates(pdf_path),
            })
        if product_match and product_match.ambiguous_match:
            self.ambiguous_matches += 1
            self.diagnostics.append({
                "source_path": str(pdf_path),
                "issue": "ambiguous",
                "selected_id_master": product_match.id_master,
                "selected_score": product_match.match_score,
                "candidates": product_match.candidate_matches,
            })

    def write_reports(
        self, output_dir: Path, *, total_documents: int, extraction_errors: int,
    ) -> None:
        summary = self.evaluator.aggregate(
            self.reports,
            total_documents=total_documents,
            unmatched_documents=self.unmatched_documents,
            low_confidence_matches=self.low_confidence_matches,
            extraction_errors=extraction_errors,
        )
        summary["ambiguous_matches"] = float(self.ambiguous_matches)
        self.reporter.write_json(self.reports, summary, output_dir / "report.json")
        self.reporter.write_markdown(self.reports, summary, output_dir / "report.md")
        if self.diagnostics:
            diagnostics_path = output_dir / "ground_truth_match_diagnostics.json"
            diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
            diagnostics_path.write_text(
                json.dumps(self.diagnostics, indent=2), encoding="utf-8",
            )
