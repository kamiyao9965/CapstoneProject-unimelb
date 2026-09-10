"""Allowlisted UI-to-CLI command contract and execution boundary."""

from __future__ import annotations

import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


from src.verticals.manifest import resolve_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUPPORTED_OPERATIONS = frozenset(
    {
        "discover",
        "extract",
        "batch",
        "crawl",
        "refine",
        "canonical_compile",
        "storage_init",
        "storage_load",
    }
)
PROVIDER_VALUES = frozenset({"openai", "anthropic", "deepseek"})
ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
INSURER_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_KEY_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD))\s*=\s*[^\s,;]+"
)
_BEARER = re.compile(r"(?i)(Bearer\s+)[^\s,;]+")
_DATABASE_URL = re.compile(
    r"(?i)\b((?:postgresql(?:\+psycopg)?|postgres)://)([^\s/@:]+):([^\s/@]+)@"
)


@dataclass(frozen=True)
class CommandRequest:
    operation: str
    options: Mapping[str, object]


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    exit_code: int
    output: str
    duration_seconds: float
    timed_out: bool = False

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def build_command(
    request: CommandRequest,
    *,
    python_executable: str | Path | None = None,
    project_root: str | Path = PROJECT_ROOT,
) -> list[str]:
    """Build a deterministic argv list for one allowlisted project operation."""
    operation = request.operation
    if operation not in SUPPORTED_OPERATIONS:
        raise ValueError(f"Unsupported operation: {operation!r}.")
    root = Path(project_root)
    python = str(python_executable or sys.executable)
    entry_point = (
        root / "src/refine/loop.py"
        if operation == "refine"
        else root / "src/run.py"
    )
    command_name = operation.replace("_", "-")
    if operation == "refine":
        command = [python, str(entry_point)]
    else:
        command = [python, str(entry_point), command_name]

    options = dict(request.options)
    manifest = resolve_manifest(options.get("manifest"), vertical=options.get("vertical"), operation=operation)
    if options.get("evaluate"):
        manifest.require_capability("evaluation")
    _add(command, "--manifest", manifest.source_path)

    if operation == "discover":
        _add_many(command, "--samples", options.get("samples"))
        _add(command, "--input-root", options.get("input_root"))
        _add_many(command, "--categories", options.get("categories"))
        _add_positive_int(command, "--per-category", options.get("per_category"))
        _add_int(command, "--seed", options.get("seed"))
        _add_provider(command, options.get("provider"))
        _add(command, "--model", options.get("model"))
        _add(command, "--document-input", options.get("document_input"))
        _add_positive_number(command, "--timeout", options.get("timeout"))
        _add_flag(command, "--keep-uploaded-files", options.get("keep_uploaded_files"))
        _add(command, "--output", options.get("output"))
        _add(command, "--usage-log", options.get("usage_log"))
    elif operation == "extract":
        _add_required(command, "--pdf", options.get("pdf"), "PDF path")
        _add_required(command, "--schema", options.get("schema"), "schema path")
        _add(command, "--output", options.get("output"))
        _add_provider(command, options.get("provider"))
        _add(command, "--model", options.get("model"))
    elif operation == "batch":
        _add_required(command, "--schema", options.get("schema"), "schema path")
        _add(command, "--input-root", options.get("input_root"))
        _add_flag(command, "--evaluate", options.get("evaluate"))
        _add_provider(command, options.get("provider"))
        _add(command, "--model", options.get("model"))
    elif operation == "crawl":
        _add(command, "--config", options.get("config"))
        _add(command, "--data-root", options.get("data_root"))
        _add(command, "--output-root", options.get("output_root"))
        for insurer in _string_list(options.get("insurers")):
            if not INSURER_CODE.fullmatch(insurer):
                raise ValueError(f"Invalid insurer code: {insurer!r}.")
            _add(command, "--insurer", insurer)
        _add_flag(command, "--include-archived", options.get("include_archived"))
        _add_flag(command, "--discovery-only", options.get("discovery_only"))
    elif operation == "refine":
        _add(command, "--input-root", options.get("input_root"))
        _add_positive_int(command, "--per-category", options.get("per_category"))
        _add_int(command, "--seed", options.get("seed"))
        _add_positive_int(command, "--eval-per-category", options.get("eval_per_category"))
        _add_int(command, "--eval-seed", options.get("eval_seed"))
        _add_provider(command, options.get("provider"))
        _add(command, "--model", options.get("model"))
        _add(command, "--document-input", options.get("document_input"))
        _add_positive_number(command, "--timeout", options.get("timeout"))
        _add(command, "--out-dir", options.get("out_dir"))
        _add_positive_int(command, "--rounds", options.get("rounds"))
        _add(command, "--resume-review", options.get("resume_review"))
        _add(command, "--resume-feedback", options.get("resume_feedback"))
        _add_flag(command, "--review-ui", options.get("review_ui"))
        _add_flag(command, "--autonomous", options.get("autonomous"))
        _add_positive_int(command, "--consensus-runs", options.get("consensus_runs"))
    elif operation == "canonical_compile":
        _add_required(command, "--schema", options.get("schema"), "schema path")
        _add_required(
            command, "--output-dir", options.get("output_dir"), "output directory"
        )
    elif operation == "storage_init":
        _add(command, "--schema", options.get("schema"))
        _add_database_environment(command, options.get("database_url_env"))
    elif operation == "storage_load":
        _add(command, "--schema", options.get("schema"))
        _add_required(command, "--artifact", options.get("artifact"), "artifact path")
        insurer_code = _required_text(options.get("insurer_code"), "insurer code")
        if not INSURER_CODE.fullmatch(insurer_code):
            raise ValueError(f"Invalid insurer code: {insurer_code!r}.")
        _add(command, "--insurer-code", insurer_code)
        _add_database_environment(command, options.get("database_url_env"))
    return command


def run_command(
    command: Sequence[str],
    *,
    project_root: str | Path = PROJECT_ROOT,
    timeout_seconds: float = 7200,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> CommandResult:
    """Execute one prebuilt argv list and return display-safe output."""
    argv = [str(value) for value in command]
    started = time.monotonic()
    try:
        completed = runner(
            argv,
            cwd=Path(project_root),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        partial = _text(exc.output)
        output = f"{partial}\nCommand timed out after {timeout_seconds:g} seconds."
        return CommandResult(
            command=tuple(argv),
            exit_code=124,
            output=redact_console_output(output.strip()),
            duration_seconds=round(time.monotonic() - started, 3),
            timed_out=True,
        )
    combined = "\n".join(
        part.strip() for part in (_text(completed.stdout), _text(completed.stderr)) if part
    )
    return CommandResult(
        command=tuple(argv),
        exit_code=int(completed.returncode),
        output=redact_console_output(combined),
        duration_seconds=round(time.monotonic() - started, 3),
    )


def redact_console_output(value: str) -> str:
    redacted = _KEY_ASSIGNMENT.sub(r"\1=[REDACTED]", value)
    redacted = _BEARER.sub(r"\1[REDACTED]", redacted)
    return _DATABASE_URL.sub(r"\1[REDACTED]@", redacted)


def _add(command: list[str], flag: str, value: object) -> None:
    if value is None or isinstance(value, bool):
        return
    text = str(value).strip()
    if text:
        command.extend([flag, text])


def _add_required(command: list[str], flag: str, value: object, label: str) -> None:
    _add(command, flag, _required_text(value, label))


def _required_text(value: object, label: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise ValueError(f"{label} is required.")
    return text


def _add_flag(command: list[str], flag: str, value: object) -> None:
    if value is True:
        command.append(flag)


def _add_many(command: list[str], flag: str, value: object) -> None:
    values = _string_list(value)
    if values:
        command.append(flag)
        command.extend(values)


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.splitlines() if item.strip()]
    if isinstance(value, Sequence):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ValueError("Multi-value option must be a list or newline-separated text.")


def _add_provider(command: list[str], value: object) -> None:
    if value is None or not str(value).strip():
        return
    provider = str(value).strip()
    if provider not in PROVIDER_VALUES:
        raise ValueError(f"Unsupported provider: {provider!r}.")
    _add(command, "--provider", provider)


def _add_int(command: list[str], flag: str, value: object) -> None:
    if value is None:
        return
    if isinstance(value, bool):
        raise ValueError(f"{flag} must be an integer.")
    _add(command, flag, int(value))


def _add_positive_int(command: list[str], flag: str, value: object) -> None:
    if value is None:
        return
    parsed = int(value)
    if isinstance(value, bool) or parsed <= 0:
        raise ValueError(f"{flag} must be greater than zero.")
    _add(command, flag, parsed)


def _add_positive_number(command: list[str], flag: str, value: object) -> None:
    if value is None:
        return
    parsed = float(value)
    if isinstance(value, bool) or parsed <= 0:
        raise ValueError(f"{flag} must be greater than zero.")
    _add(command, flag, f"{parsed:g}")


def _add_database_environment(command: list[str], value: object) -> None:
    name = str(value or "KONKRD_DATABASE_URL").strip()
    if not ENVIRONMENT_NAME.fullmatch(name):
        raise ValueError("Database URL environment variable has an unsafe name.")
    _add(command, "--database-url-env", name)


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
