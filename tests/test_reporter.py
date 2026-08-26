from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.evaluation.reporter import EvaluationReporter
from src.models import EvaluationReport


class EvaluationReporterJsonTest(unittest.TestCase):
    def test_decimal_claim_values_are_written_as_lossless_json_strings(self) -> None:
        report = EvaluationReport(
            source_path="example.pdf",
            field_precision=1.0,
            field_recall=1.0,
            normalization_accuracy=1.0,
            coverage=1.0,
            claim_evidence={
                "extras.services.dental.annual_limit": {
                    "status": "supported",
                    "extracted": Decimal("123.40"),
                }
            },
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path = EvaluationReporter().write_json(
                [report], {}, root / "report.json"
            )
            evidence_path = EvaluationReporter().write_claim_evidence(
                [report], root / "claim_evidence.json"
            )

            report_payload = json.loads(report_path.read_text(encoding="utf-8"))
            evidence_payload = json.loads(evidence_path.read_text(encoding="utf-8"))

        self.assertEqual(
            report_payload["reports"][0]["claim_evidence"]
            ["extras.services.dental.annual_limit"]["extracted"],
            "123.40",
        )
        self.assertEqual(
            evidence_payload["example.pdf"]["claims"]
            ["extras.services.dental.annual_limit"]["extracted"],
            "123.40",
        )


if __name__ == "__main__":
    unittest.main()
