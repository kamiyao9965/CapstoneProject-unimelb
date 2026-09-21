from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest import mock

from src.tool_ui.commands import (
    CommandRequest,
    build_command,
    redact_console_output,
    run_command,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ToolCommandBuilderTests(unittest.TestCase):
    def test_builds_manifest_driven_travel_discovery_command(self) -> None:
        command = build_command(
            CommandRequest(
                "discover",
                {
                    "vertical": "travel_insurance",
                    "input_root": "data/travel insurance/PDFs",
                    "per_category": 2,
                    "seed": 42,
                    "provider": "openai",
                    "model": "gpt-5",
                    "output": "outputs/travel_insurance/schema.json",
                },
            ),
            python_executable="/project/.venv/bin/python",
            project_root=PROJECT_ROOT,
        )

        self.assertEqual(
            command,
            [
                "/project/.venv/bin/python",
                str(PROJECT_ROOT / "src/run.py"),
                "discover",
                "--manifest",
                str(PROJECT_ROOT / "configs/travel_insurance/manifest.json"),
                "--input-root",
                "data/travel insurance/PDFs",
                "--per-category",
                "2",
                "--seed",
                "42",
                "--provider",
                "openai",
                "--model",
                "gpt-5",
                "--output",
                "outputs/travel_insurance/schema.json",
            ],
        )

    def test_refinement_uses_the_existing_loop_entry_point(self) -> None:
        command = build_command(
            CommandRequest(
                "refine",
                {
                    "vertical": "travel_insurance",
                    "per_category": 1,
                    "seed": 7,
                    "consensus_runs": 5,
                    "review_ui": True,
                },
            ),
            python_executable="python",
            project_root=PROJECT_ROOT,
        )

        self.assertEqual(command[1], str(PROJECT_ROOT / "src/refine/loop.py"))
        self.assertIn("--review-ui", command)
        self.assertEqual(command[-2:], ["--consensus-runs", "5"])

    def test_document_parser_is_allowlisted_for_pdf_operations(self) -> None:
        requests = {
            "discover": {"vertical": "travel_insurance"},
            "extract": {"vertical": "travel_insurance", "pdf": "a.pdf", "schema": "schema.json"},
            "batch": {"vertical": "travel_insurance", "schema": "schema.json"},
            "refine": {"vertical": "travel_insurance"},
        }
        for operation, options in requests.items():
            with self.subTest(operation=operation):
                command = build_command(
                    CommandRequest(operation, {**options, "document_parser": "mineru"}),
                    python_executable="python",
                    project_root=PROJECT_ROOT,
                )
                flag_index = command.index("--document-parser")
                self.assertEqual(command[flag_index + 1], "mineru")
                with self.assertRaisesRegex(ValueError, "Unsupported document parser"):
                    build_command(
                        CommandRequest(operation, {**options, "document_parser": "mineru; rm -rf /"}),
                        python_executable="python",
                        project_root=PROJECT_ROOT,
                    )

    def test_batch_category_filter_output_folder_and_folder_load_commands(self) -> None:
        batch = build_command(
            CommandRequest(
                "batch",
                {
                    "vertical": "travel_insurance",
                    "schema": "schema.json",
                    "categories": ["pds"],
                    "output_dir": "outputs/travel_insurance/extractions/run20",
                },
            ),
            python_executable="python",
            project_root=PROJECT_ROOT,
        )
        self.assertEqual(batch[batch.index("--categories") + 1], "pds")
        self.assertEqual(
            batch[batch.index("--output-dir") + 1], "outputs/travel_insurance/extractions/run20"
        )
        with self.assertRaisesRegex(ValueError, "Unknown categories"):
            build_command(
                CommandRequest(
                    "batch",
                    {"vertical": "travel_insurance", "schema": "schema.json", "categories": ["tmd"]},
                ),
                python_executable="python",
                project_root=PROJECT_ROOT,
            )

        load = build_command(
            CommandRequest(
                "storage_load_batch",
                {"vertical": "travel_insurance", "artifact_dir": "outputs/travel_insurance/extractions/run20"},
            ),
            python_executable="python",
            project_root=PROJECT_ROOT,
        )
        self.assertEqual(load[2], "storage-load-batch")
        self.assertEqual(
            load[load.index("--artifact-dir") + 1], "outputs/travel_insurance/extractions/run20"
        )
        self.assertEqual(load[load.index("--database-url-env") + 1], "KONKRD_DATABASE_URL")
        with self.assertRaisesRegex(ValueError, "extraction results folder is required"):
            build_command(
                CommandRequest("storage_load_batch", {"vertical": "travel_insurance"}),
                python_executable="python",
                project_root=PROJECT_ROOT,
            )

    def test_required_fields_fail_before_process_execution(self) -> None:
        with self.assertRaisesRegex(ValueError, "PDF path"):
            build_command(CommandRequest("extract", {"schema": "schema.json"}))

    def test_disabled_capabilities_fail_before_execution(self):
        for operation in ("crawl", "storage_init", "canonical_compile", "storage_load_batch"):
            with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, "does not support"):
                build_command(CommandRequest(operation, {"vertical": "private_health"}))
        with self.assertRaisesRegex(ValueError, "evaluation"):
            build_command(CommandRequest("batch", {"vertical": "travel_insurance", "evaluate": True}))

    def test_unknown_operation_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported operation"):
            build_command(CommandRequest("arbitrary-shell", {}))

    def test_shell_syntax_in_a_path_stays_one_literal_argument(self) -> None:
        command = build_command(
            CommandRequest(
                "canonical_compile",
                {
                    "schema": "schemas/reviewed;touch-pwned.json",
                    "output_dir": "outputs/compiled",
                },
            ),
            python_executable="python",
            project_root=PROJECT_ROOT,
        )

        self.assertIn("schemas/reviewed;touch-pwned.json", command)
        self.assertNotIn("touch-pwned.json", command)

    def test_database_environment_variable_must_be_a_safe_identifier(self) -> None:
        with self.assertRaisesRegex(ValueError, "environment variable"):
            build_command(
                CommandRequest(
                    "storage_init",
                    {"database_url_env": "KONKRD_DATABASE_URL;env"},
                )
            )


class ToolCommandRunnerTests(unittest.TestCase):
    def test_runner_uses_argv_without_a_shell_and_returns_redacted_output(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["python", "src/run.py", "storage-init"],
            returncode=1,
            stdout="failed postgresql+psycopg://user:secret@localhost/db\n",
            stderr="",
        )
        runner = mock.Mock(return_value=completed)

        result = run_command(
            ["python", "src/run.py", "storage-init"],
            project_root=PROJECT_ROOT,
            runner=runner,
        )

        self.assertEqual(result.exit_code, 1)
        self.assertNotIn("secret", result.output)
        kwargs = runner.call_args.kwargs
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["cwd"], PROJECT_ROOT)

    def test_timeout_has_stable_exit_code_and_message(self) -> None:
        runner = mock.Mock(
            side_effect=subprocess.TimeoutExpired(
                cmd=["python", "src/run.py", "discover"],
                timeout=10,
                output="partial output",
            )
        )

        result = run_command(
            ["python", "src/run.py", "discover"],
            timeout_seconds=10,
            runner=runner,
        )

        self.assertEqual(result.exit_code, 124)
        self.assertTrue(result.timed_out)
        self.assertIn("timed out", result.output.lower())

    def test_redacts_key_assignments_bearer_tokens_and_database_urls(self) -> None:
        output = redact_console_output(
            "MY_OPENAI_API_KEY=sk-example-secret\n"
            "Authorization: Bearer abc.def.ghi\n"
            "postgresql://alice:hunter2@db.internal/konkrd"
        )

        self.assertNotIn("example-secret", output)
        self.assertNotIn("abc.def.ghi", output)
        self.assertNotIn("hunter2", output)
        self.assertIn("[REDACTED]", output)


if __name__ == "__main__":
    unittest.main()
