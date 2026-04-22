from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from openai import OpenAI

from src.schema.prompts import SCHEMA_DISCOVERY_PROMPT


class SchemaDiscovery:
    def __init__(
        self,
        model: str = "gpt-5",
        client: OpenAI | None = None,
        cleanup_uploaded_files: bool = True,
        timeout_seconds: float = 600.0,
        log: Callable[[str], None] | None = print,
    ) -> None:
        self.model = model
        self.client = client
        self.cleanup_uploaded_files = cleanup_uploaded_files
        self.timeout_seconds = timeout_seconds
        self.log = log

    def discover(self, sample_pdfs: list[str]) -> str:
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY must be set to generate a schema with OpenAI.")

        client = self.client or OpenAI(timeout=self.timeout_seconds)
        pdf_paths = [Path(path) for path in sample_pdfs]
        file_ids = self._upload_pdfs(client, pdf_paths)
        try:
            self._log(f"Generating schema with {self.model}. This can take a few minutes...")
            response = client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": SCHEMA_DISCOVERY_PROMPT},
                    {"role": "user", "content": self._input_content(pdf_paths, file_ids)},
                ],
            )
            self._log_usage(response)
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

    def _log_usage(self, response: object) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            self._log("Token usage: unavailable")
            return

        input_tokens = self._usage_value(usage, "input_tokens", "prompt_tokens")
        output_tokens = self._usage_value(usage, "output_tokens", "completion_tokens")
        total_tokens = self._usage_value(usage, "total_tokens")

        parts = []
        if input_tokens is not None:
            parts.append(f"input={input_tokens}")
        if output_tokens is not None:
            parts.append(f"output={output_tokens}")
        if total_tokens is not None:
            parts.append(f"total={total_tokens}")

        self._log("Token usage: " + (", ".join(parts) if parts else "unavailable"))

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
