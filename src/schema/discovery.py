from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from openai import OpenAI

from src.schema.prompts import SCHEMA_DISCOVERY_PROMPT

PROJECT_API_KEY_ENV = "MY_OPENAI_API_KEY"
DEFAULT_OPENAI_API_KEY_ENV = "OPENAI_API_KEY"


class SchemaDiscovery:
    def __init__(
        self,
        model: str = "gpt-5",
        client: OpenAI | None = None,
        cleanup_uploaded_files: bool = True,
        timeout_seconds: float = 600.0,
        usage_log_path: str | Path | None = "outputs/private_health/token_usage.jsonl",
        log: Callable[[str], None] | None = print,
    ) -> None:
        self.model = model
        self.client = client
        self.cleanup_uploaded_files = cleanup_uploaded_files
        self.timeout_seconds = timeout_seconds
        self.usage_log_path = Path(usage_log_path) if usage_log_path else None
        self.log = log

    def discover(self, sample_pdfs: list[str], output_path: str | Path | None = None) -> str:
        api_key, api_key_env = self._resolve_api_key()
        if not api_key:
            raise RuntimeError(
                f"{PROJECT_API_KEY_ENV} or {DEFAULT_OPENAI_API_KEY_ENV} must be set "
                "to generate a schema with OpenAI."
            )

        started_at = datetime.now(timezone.utc)
        started_perf = time.perf_counter()
        client = self.client or OpenAI(api_key=api_key, timeout=self.timeout_seconds)
        pdf_paths = [Path(path) for path in sample_pdfs]
        resolved_output_path = Path(output_path) if output_path else None
        file_ids = self._upload_pdfs(client, pdf_paths)
        try:
            self._log(
                f"Generating schema with {self.model} using {api_key_env}. "
                "This can take a few minutes..."
            )
            response = client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": SCHEMA_DISCOVERY_PROMPT},
                    {"role": "user", "content": self._input_content(pdf_paths, file_ids)},
                ],
            )
            completed_at = datetime.now(timezone.utc)
            duration_seconds = round(time.perf_counter() - started_perf, 3)
            self._log_usage(
                response,
                pdf_paths,
                api_key_env,
                started_at,
                completed_at,
                duration_seconds,
                resolved_output_path,
            )
            return self._clean_yaml(response.output_text)
        finally:
            if self.cleanup_uploaded_files:
                self._log("Cleaning up uploaded files...")
                self._delete_uploaded_files(client, file_ids)

    def _upload_pdfs(self, client: OpenAI, pdf_paths: list[Path]) -> list[str]:
        file_ids = []
        total = len(pdf_paths)
        for index, path in enumerate(pdf_paths, start=1):
            if not path.exists():
                raise FileNotFoundError(path)
            self._log(f"Uploading PDF {index}/{total}: {path.name}")
            with path.open("rb") as file:
                file_ids.append(client.files.create(file=file, purpose="user_data").id)
        return file_ids

    def _input_content(self, pdf_paths: list[Path], file_ids: list[str]) -> list[dict[str, str]]:
        sample_list = "\n".join(f"- {path.as_posix()}" for path in pdf_paths)
        content = [
            {
                "type": "input_text",
                "text": f"Generate a private_health YAML schema from these PDFs:\n{sample_list}",
            }
        ]
        content.extend({"type": "input_file", "file_id": file_id} for file_id in file_ids)
        return content

    def _delete_uploaded_files(self, client: OpenAI, file_ids: list[str]) -> None:
        for file_id in file_ids:
            try:
                client.files.delete(file_id)
            except Exception:
                pass

    def _log(self, message: str) -> None:
        if self.log:
            self.log(message)

    def _log_usage(
        self,
        response: object,
        pdf_paths: list[Path],
        api_key_env: str,
        started_at: datetime,
        completed_at: datetime,
        duration_seconds: float,
        output_path: Path | None,
    ) -> None:
        usage = getattr(response, "usage", None)
        input_tokens = None
        output_tokens = None
        total_tokens = None

        if usage is None:
            self._log(f"Task duration: {duration_seconds:.3f}s")
            self._log("Token usage: unavailable")
        else:
            input_tokens = self._usage_value(usage, "input_tokens", "prompt_tokens")
            output_tokens = self._usage_value(usage, "output_tokens", "completion_tokens")
            total_tokens = self._usage_value(usage, "total_tokens")

            self._log(f"Task duration: {duration_seconds:.3f}s")
            parts = []
            if input_tokens is not None:
                parts.append(f"input={input_tokens}")
            if output_tokens is not None:
                parts.append(f"output={output_tokens}")
            if total_tokens is not None:
                parts.append(f"total={total_tokens}")

            self._log("Token usage: " + (", ".join(parts) if parts else "unavailable"))

        self._append_usage_log(
            {
                "timestamp": completed_at.isoformat(),
                "event": "schema_discovery",
                "model": self.model,
                "api_key_env": api_key_env,
                "yaml_output_path": output_path.as_posix() if output_path else None,
                "started_at": started_at.isoformat(),
                "completed_at": completed_at.isoformat(),
                "duration_seconds": duration_seconds,
                "response_id": getattr(response, "id", None),
                "sample_count": len(pdf_paths),
                "sample_pdfs": [path.as_posix() for path in pdf_paths],
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
            }
        )

    def _append_usage_log(self, payload: dict[str, object]) -> None:
        if self.usage_log_path is None:
            return

        try:
            self.usage_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.usage_log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as exc:
            self._log(f"Failed to write usage log: {exc}")

    @staticmethod
    def _resolve_api_key() -> tuple[str | None, str]:
        project_api_key = os.getenv(PROJECT_API_KEY_ENV)
        if project_api_key:
            return project_api_key, PROJECT_API_KEY_ENV

        default_api_key = os.getenv(DEFAULT_OPENAI_API_KEY_ENV)
        if default_api_key:
            return default_api_key, DEFAULT_OPENAI_API_KEY_ENV

        return None, PROJECT_API_KEY_ENV

    @staticmethod
    def _usage_value(usage: object, *names: str) -> int | None:
        for name in names:
            if isinstance(usage, dict) and usage.get(name) is not None:
                return int(usage[name])
            value = getattr(usage, name, None)
            if value is not None:
                return int(value)
        return None

    @staticmethod
    def _clean_yaml(text: str) -> str:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.removeprefix("```yaml").removeprefix("```").strip()
            cleaned = cleaned.removesuffix("```").strip()
        return cleaned + "\n"
