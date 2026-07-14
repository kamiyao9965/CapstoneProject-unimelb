from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from src.common.json_artifacts import (
    ArtifactError,
    build_failure_artifact,
    build_success_artifact,
    read_artifact,
    write_artifact,
    write_failure_artifact,
)
from tests.test_json_contracts import VALID_DISCOVERED_SCHEMA


PROVENANCE = {
    "run_id": "run-001",
    "provider": "openai",
    "model": "gpt-5",
    "document_input": "pdf",
    "source_documents": ["inputs/sample.pdf"],
    "source_artifacts": [],
}


class JsonArtifactTest(unittest.TestCase):
    def test_success_artifact_round_trips_and_is_deterministic(self) -> None:
        artifact = build_success_artifact(
            artifact_type="discovered_schema",
            contract_version="1.0.0",
            data=VALID_DISCOVERED_SCHEMA,
            provenance=PROVENANCE,
            created_at="2026-07-14T08:00:00Z",
            data_contract="private_health/discovered_schema",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "schema.json"
            write_artifact(path, artifact, data_contract="private_health/discovered_schema")

            loaded = read_artifact(
                path,
                expected_type="discovered_schema",
                data_contract="private_health/discovered_schema",
            )

            self.assertEqual(loaded, artifact)
            self.assertTrue(path.read_text(encoding="utf-8").endswith("\n"))
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                artifact,
            )

    def test_writer_refuses_to_overwrite_by_default(self) -> None:
        artifact = build_success_artifact(
            artifact_type="discovered_schema",
            contract_version="1.0.0",
            data=VALID_DISCOVERED_SCHEMA,
            provenance=PROVENANCE,
            data_contract="private_health/discovered_schema",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schema.json"
            write_artifact(path, artifact, data_contract="private_health/discovered_schema")

            with self.assertRaisesRegex(FileExistsError, "Refusing to overwrite"):
                write_artifact(path, artifact, data_contract="private_health/discovered_schema")

    def test_invalid_data_is_rejected_before_persistence(self) -> None:
        invalid = dict(VALID_DISCOVERED_SCHEMA)
        invalid["fields"] = []

        with self.assertRaises(Exception):
            build_success_artifact(
                artifact_type="discovered_schema",
                contract_version="1.0.0",
                data=invalid,
                provenance=PROVENANCE,
                data_contract="private_health/discovered_schema",
            )

    def test_failure_artifact_is_structured_and_redacted(self) -> None:
        artifact = build_failure_artifact(
            artifact_type="schema_discovery_error",
            contract_version="1.0.0",
            provenance=PROVENANCE,
            error_code="contract_validation_failed",
            message="Model output did not satisfy the contract.",
            details=[{"path": "$.fields[0].values", "message": "must be an array"}],
            created_at="2026-07-14T08:00:00Z",
        )

        self.assertEqual(artifact["status"], "failed")
        self.assertIsNone(artifact["data"])
        self.assertNotIn("raw_output", json.dumps(artifact))

    def test_failure_artifact_redacts_secret_shaped_error_text(self) -> None:
        artifact = build_failure_artifact(
            artifact_type="schema_discovery_error",
            contract_version="1.0.0",
            provenance=PROVENANCE,
            error_code="provider_failed",
            message="DEEPSEEK_API_KEY=sk-secretvalue123456 request failed",
            details=[{"path": "$", "message": "Bearer tokenvalue123456789"}],
        )
        serialized = json.dumps(artifact)

        self.assertNotIn("sk-secretvalue123456", serialized)
        self.assertNotIn("tokenvalue123456789", serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_failure_writer_only_writes_below_errors_directory(self) -> None:
        artifact = build_failure_artifact(
            artifact_type="schema_discovery_error",
            contract_version="1.0.0",
            provenance=PROVENANCE,
            error_code="invalid_json",
            message="Invalid JSON.",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_failure_artifact(root, "discovery", "run-001", artifact)

            self.assertEqual(path.parent, root / "errors" / "discovery")
            self.assertTrue(path.exists())

            with self.assertRaisesRegex(ArtifactError, "safe path component"):
                write_failure_artifact(root, "../success", "run-002", artifact)

    def test_reader_rejects_failed_artifact_on_success_path(self) -> None:
        artifact = build_failure_artifact(
            artifact_type="discovered_schema",
            contract_version="1.0.0",
            provenance=PROVENANCE,
            error_code="invalid_json",
            message="Invalid JSON.",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "errors" / "schema.json"
            write_artifact(path, artifact)

            with self.assertRaisesRegex(ArtifactError, "not a successful artifact"):
                read_artifact(path, expected_type="discovered_schema")

    def test_writer_rejects_non_finite_numbers(self) -> None:
        contract = {
            "type": "object",
            "additionalProperties": False,
            "required": ["amount"],
            "properties": {"amount": {"type": "number"}},
        }
        artifact = build_success_artifact(
            artifact_type="extraction_result",
            contract_version="1.0.0",
            data={"amount": math.inf},
            provenance=PROVENANCE,
            data_contract_schema=contract,
        )

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                write_artifact(
                    Path(tmp) / "record.json",
                    artifact,
                    data_contract_schema=contract,
                )

    def test_writer_does_not_follow_predictable_temporary_symlink(self) -> None:
        artifact = build_success_artifact(
            artifact_type="discovered_schema",
            contract_version="1.0.0",
            data=VALID_DISCOVERED_SCHEMA,
            provenance=PROVENANCE,
            data_contract="private_health/discovered_schema",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / "schema.json"
            outside = root / "outside.txt"
            outside.write_text("do-not-touch", encoding="utf-8")
            destination.with_name(f".{destination.name}.tmp").symlink_to(outside)

            write_artifact(
                destination,
                artifact,
                data_contract="private_health/discovered_schema",
            )

            self.assertEqual(outside.read_text(encoding="utf-8"), "do-not-touch")
            self.assertEqual(
                read_artifact(
                    destination,
                    expected_type="discovered_schema",
                    data_contract="private_health/discovered_schema",
                ),
                artifact,
            )


if __name__ == "__main__":
    unittest.main()
