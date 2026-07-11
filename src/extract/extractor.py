from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from src.common.document_preprocessor import MarkdownPreprocessor, prepare_documents
from src.common.model_config import ModelSelection
from src.common.model_provider import (
    ModelProvider,
    ModelResponse,
    ProviderRequest,
    create_provider,
)
from src.common.openai_run import append_jsonl
from src.extract.prompts import EXTRACTION_PROMPT


class SchemaExtractor:
    """Extract one JSON record per PDF using a schema as the contract."""

    def __init__(
        self,
        schema_text: str,
        model: str = "gpt-5",
        client: object | None = None,
        selection: ModelSelection | None = None,
        provider: ModelProvider | None = None,
        cleanup_uploaded_files: bool = True,
        timeout_seconds: float = 600.0,
        usage_log_path: str | Path | None = "outputs/private_health/extraction_usage.jsonl",
        log: Callable[[str], None] | None = print,
        background: bool = True,
        poll_interval: float = 5.0,
        pdf_root: str | Path | None = None,
        preprocessor: MarkdownPreprocessor | None = None,
    ) -> None:
        self.schema_text = schema_text
        self.selection = selection or ModelSelection("openai", model, "pdf")
        self.model = self.selection.model
        self.provider = provider or create_provider(self.selection, client=client)
        # Markdown mode maps each PDF to its mirrored Markdown path before the
        # provider request; PDF mode never touches the preprocessor.
        self.pdf_root = Path(pdf_root) if pdf_root else None
        self.preprocessor = preprocessor
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

        started = time.perf_counter()
        self._log(f"Extracting {pdf_path.name} with {self.selection.provider}/{self.model}...")
        document_paths = prepare_documents(
            self.selection, (pdf_path,), self.pdf_root, self.preprocessor
        )
        response = self.provider.generate(
            ProviderRequest(
                selection=self.selection,
                system_prompt=EXTRACTION_PROMPT,
                user_text=(
                    f"Schema (YAML):\n{self.schema_text}\n\n"
                    "Extract from the attached PDF."
                ),
                document_paths=document_paths,
                timeout_seconds=self.timeout_seconds,
                cleanup_documents=self.cleanup_uploaded_files,
                request_params={},
                background=self.background,
                poll_interval=self.poll_interval,
                log=self.log,
            )
        )
        record = self._parse_json(response.text)
        self._log_usage(response, pdf_path, round(time.perf_counter() - started, 3))
        return record

    def extract_many(self, pdf_paths: list[str | Path], out_dir: str | Path) -> list[Path]:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        reserved_targets: set[Path] = set()
        for pdf_path in pdf_paths:
            pdf_path = Path(pdf_path)
            try:
                record = self.extract_one(pdf_path)
            except Exception as exc:  # keep going; one bad PDF should not stop the batch
                self._log(f"Extraction failed for {pdf_path.name}: {exc}")
                record = {"_error": str(exc), "_source": pdf_path.as_posix()}
            record.setdefault("_source", pdf_path.as_posix())
            target = out_dir / f"{pdf_path.stem}.json"
            suffix = 2
            while target in reserved_targets:
                target = out_dir / f"{pdf_path.stem}_{suffix}.json"
                suffix += 1
            reserved_targets.add(target)
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

    def _log_usage(self, response: ModelResponse, pdf_path: Path, duration: float) -> None:
        if self.usage_log_path is None:
            return
        usage = response.usage
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "extraction",
            "provider": response.provider,
            "model": response.model,
            "document_input": self.selection.document_input,
            "api_key_env": response.api_key_env,
            "source_pdf": pdf_path.as_posix(),
            "duration_seconds": duration,
            "input_tokens": usage.input_tokens if usage else None,
            "output_tokens": usage.output_tokens if usage else None,
            "total_tokens": usage.total_tokens if usage else None,
        }
        append_jsonl(
            self.usage_log_path,
            payload,
            log=self._log,
            error_label="extraction usage log",
        )
