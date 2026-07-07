from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from openai import OpenAI

from src.common.openai_run import run_response
from src.extract.prompts import EXTRACTION_PROMPT

PROJECT_API_KEY_ENV = "MY_OPENAI_API_KEY"
DEFAULT_OPENAI_API_KEY_ENV = "OPENAI_API_KEY"


class SchemaExtractor:
    """Extract one JSON record per PDF using a schema as the contract."""

    def __init__(
        self,
        schema_text: str,
        model: str = "gpt-5",
        client: OpenAI | None = None,
        cleanup_uploaded_files: bool = True,
        timeout_seconds: float = 600.0,
        usage_log_path: str | Path | None = "outputs/private_health/extraction_usage.jsonl",
        log: Callable[[str], None] | None = print,
        background: bool = True,
        poll_interval: float = 5.0,
    ) -> None:
        self.schema_text = schema_text
        self.model = model
        self.client = client
        self.cleanup_uploaded_files = cleanup_uploaded_files
        self.timeout_seconds = timeout_seconds
        self.usage_log_path = Path(usage_log_path) if usage_log_path else None
        self.log = log
        # Background mode + polling to dodge gateway 520s on long requests.
        self.background = background
        self.poll_interval = poll_interval

    def extract_one(self, pdf_path: str | Path) -> dict:
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(pdf_path)

        api_key, api_key_env = self._resolve_api_key()
        if not api_key:
            raise RuntimeError(
                f"{PROJECT_API_KEY_ENV} or {DEFAULT_OPENAI_API_KEY_ENV} must be set to extract."
            )

        # Higher max_retries than the SDK default so transient upstream 5xx
        # (e.g. Cloudflare 520) back off and retry instead of failing the batch.
        client = self.client or OpenAI(
            api_key=api_key, timeout=self.timeout_seconds, max_retries=5
        )
        started = time.perf_counter()
        self._log(f"Extracting {pdf_path.name}...")
        with pdf_path.open("rb") as handle:
            file_id = client.files.create(file=handle, purpose="user_data").id
        try:
            response = run_response(
                client,
                background=self.background,
                poll_interval=self.poll_interval,
                poll_timeout=self.timeout_seconds,
                log=self.log,
                model=self.model,
                input=[
                    {"role": "system", "content": EXTRACTION_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text",
                             "text": f"Schema (YAML):\n{self.schema_text}\n\nExtract from the attached PDF."},
                            {"type": "input_file", "file_id": file_id},
                        ],
                    },
                ],
            )
            record = self._parse_json(response.output_text)
            self._log_usage(response, pdf_path, api_key_env, round(time.perf_counter() - started, 3))
            return record
        finally:
            if self.cleanup_uploaded_files:
                try:
                    client.files.delete(file_id)
                except Exception:
                    pass

    def extract_many(self, pdf_paths: list[str | Path], out_dir: str | Path) -> list[Path]:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for pdf_path in pdf_paths:
            pdf_path = Path(pdf_path)
            try:
                record = self.extract_one(pdf_path)
            except Exception as exc:  # keep going; one bad PDF should not stop the batch
                self._log(f"Extraction failed for {pdf_path.name}: {exc}")
                record = {"_error": str(exc), "_source": pdf_path.as_posix()}
            record.setdefault("_source", pdf_path.as_posix())
            target = out_dir / f"{pdf_path.stem}.json"
            target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            written.append(target)
        return written

    @staticmethod
    def _parse_json(text: str) -> dict:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.removeprefix("```json").removeprefix("```").strip()
            cleaned = cleaned.removesuffix("```").strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            return {"_parse_error": str(exc), "_raw": cleaned[:2000]}
        return data if isinstance(data, dict) else {"_unexpected_type": type(data).__name__, "value": data}

    def _log(self, message: str) -> None:
        if self.log:
            self.log(message)

    def _log_usage(self, response: object, pdf_path: Path, api_key_env: str, duration: float) -> None:
        if self.usage_log_path is None:
            return
        usage = getattr(response, "usage", None)
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "extraction",
            "model": self.model,
            "api_key_env": api_key_env,
            "source_pdf": pdf_path.as_posix(),
            "duration_seconds": duration,
            "input_tokens": self._usage_value(usage, "input_tokens", "prompt_tokens"),
            "output_tokens": self._usage_value(usage, "output_tokens", "completion_tokens"),
            "total_tokens": self._usage_value(usage, "total_tokens"),
        }
        try:
            self.usage_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.usage_log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as exc:
            self._log(f"Failed to write extraction usage log: {exc}")

    @staticmethod
    def _usage_value(usage: object, *names: str) -> int | None:
        if usage is None:
            return None
        for name in names:
            if isinstance(usage, dict) and usage.get(name) is not None:
                return int(usage[name])
            value = getattr(usage, name, None)
            if value is not None:
                return int(value)
        return None

    @staticmethod
    def _resolve_api_key() -> tuple[str | None, str]:
        project = os.getenv(PROJECT_API_KEY_ENV)
        if project:
            return project, PROJECT_API_KEY_ENV
        default = os.getenv(DEFAULT_OPENAI_API_KEY_ENV)
        if default:
            return default, DEFAULT_OPENAI_API_KEY_ENV
        return None, PROJECT_API_KEY_ENV
