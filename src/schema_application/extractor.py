from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping
from uuid import uuid4

from src.PDFingestor.adapter import DEFAULT_CACHE_DIR, render_pdf_paths_for_prompt
from src.common.json_artifacts import (
    build_failure_artifact,
    build_success_artifact,
    write_artifact,
    write_failure_artifact,
)
from src.common.json_contracts import validate_contract
from src.common.model_config import ModelSelection
from src.common.model_provider import (
    ModelProvider,
    ModelResponse,
    ProviderRequest,
    StructuredOutputSpec,
    create_provider,
)
from src.common.openai_run import append_jsonl
from src.common.structured_output import StructuredOutputFailure, run_structured_output
from src.schema.contract import compile_extraction_contract
from src.schema.canonical import (
    compile_canonical_extraction_contract,
    is_canonical_schema,
    require_approved_canonical_schema,
)
from src.schema_application.prompts import EXTRACTION_PROMPT
from src.schema.validation import validate_schema_mapping


class SchemaExtractor:
    """Extract one validated, enveloped JSON record per PDF."""

    def __init__(
        self,
        schema_data: Mapping[str, object],
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
        preprocessor: object | None = None,
        pdfingestor_cache_dir: str | Path | None = None,
        schema_contract: str = "private_health/discovered_schema",
        schema_validator: Callable[[object], object] = validate_schema_mapping,
        output_cardinality: str = "single",
        extraction_prompt: str = EXTRACTION_PROMPT,
    ) -> None:
        self.schema_data = dict(schema_data)
        if is_canonical_schema(schema_data):
            canonical_schema = require_approved_canonical_schema(schema_data)
            output = canonical_schema["output"]
            assert isinstance(output, Mapping)
            if output["cardinality"] != output_cardinality:
                raise ValueError(
                    "Canonical Schema output cardinality does not match the vertical "
                    f"manifest: {output['cardinality']!r} != {output_cardinality!r}."
                )
            self.extraction_contract = compile_canonical_extraction_contract(
                canonical_schema
            )
            canonical_fields = canonical_schema["fields"]
            assert isinstance(canonical_fields, list)
            self.structured_output_strict = not any(
                field["type"] == "list[object]" or field["required"] is False
                for field in canonical_fields
            )
            self.schema_prompt_label = "Approved Canonical Schema"
        else:
            validate_contract(schema_data, schema_contract)
            schema_validator(schema_data)
            self.extraction_contract = compile_extraction_contract(
                schema_data,
                data_contract=schema_contract,
                business_validator=schema_validator,
                output_cardinality=output_cardinality,
            )
            self.structured_output_strict = not any(
                field["type"] == "list[object]"
                for field in self.schema_data["fields"]
            )
            self.schema_prompt_label = "Discovered schema"
        self.extraction_prompt = extraction_prompt
        self.selection = selection or ModelSelection("openai", model, "markdown")
        if self.selection.document_input != "markdown":
            raise ValueError(
                "SchemaExtractor uses PDFingestor's inline text representation; "
                "set LLM_DOCUMENT_INPUT=markdown."
            )
        self.model = self.selection.model
        self.provider = provider or create_provider(self.selection, client=client)
        self.pdf_root = Path(pdf_root) if pdf_root else None
        self.preprocessor = preprocessor
        self.pdfingestor_cache_dir = Path(pdfingestor_cache_dir or DEFAULT_CACHE_DIR)
        self.cleanup_uploaded_files = cleanup_uploaded_files
        self.timeout_seconds = timeout_seconds
        self.usage_log_path = Path(usage_log_path) if usage_log_path else None
        self.log = log
        self.background = background
        self.poll_interval = poll_interval

    def extract_one(
        self,
        pdf_path: str | Path,
        *,
        run_id: str | None = None,
    ) -> dict[str, object]:
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(pdf_path)
        started = time.perf_counter()
        logical_run_id = run_id or uuid4().hex
        self._log(f"Extracting {pdf_path.name} with {self.selection.provider}/{self.model}...")
        document_text = render_pdf_paths_for_prompt(
            (pdf_path,),
            cache_dir=self.pdfingestor_cache_dir,
            pdf_root=self.pdf_root,
        )
        request = ProviderRequest(
            selection=self.selection,
            system_prompt=self.extraction_prompt,
            user_text=(
                f"{self.schema_prompt_label} data:\n"
                f"{json.dumps(self.schema_data, ensure_ascii=False)}\n\n"
                "Extract from this PDFingestor structured representation. "
                "Text and Markdown tables are already in source reading order; "
                "do not assume there is an attached raw PDF.\n\n"
                f"{document_text}"
            ),
            document_paths=(),
            timeout_seconds=self.timeout_seconds,
            cleanup_documents=self.cleanup_uploaded_files,
            request_params={},
            background=self.background,
            poll_interval=self.poll_interval,
            log=self.log,
            structured_output=StructuredOutputSpec(
                name="extraction_result",
                schema=self.extraction_contract,
                strict=self.structured_output_strict,
            ),
        )
        try:
            result = run_structured_output(
                self.provider,
                request,
                data_contract_schema=self.extraction_contract,
            )
        except StructuredOutputFailure as exc:
            duration = round(time.perf_counter() - started, 3)
            for attempt in exc.result.attempts:
                self._log_usage(
                    attempt.response, pdf_path, duration, logical_run_id,
                    attempt.number, False,
                )
            raise
        duration = round(time.perf_counter() - started, 3)
        for attempt in result.attempts:
            self._log_usage(
                attempt.response, pdf_path, duration, logical_run_id,
                attempt.number, not attempt.errors,
            )
        assert result.data is not None
        return result.data

    def extract_many(self, pdf_paths: list[str | Path], out_dir: str | Path) -> list[Path]:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        reserved_targets: set[Path] = set()
        for pdf_value in pdf_paths:
            pdf_path = Path(pdf_value)
            logical_run_id = uuid4().hex
            try:
                record = self.extract_one(pdf_path, run_id=logical_run_id)
            except Exception as exc:
                failure = build_failure_artifact(
                    artifact_type="extraction_error",
                    contract_version="1.0.0",
                    provenance=self._provenance(pdf_path, logical_run_id),
                    error_code=(
                        "structured_output_exhausted"
                        if isinstance(exc, StructuredOutputFailure)
                        else "extraction_failed"
                    ),
                    message=str(exc),
                    details=(
                        list(exc.result.errors)
                        if isinstance(exc, StructuredOutputFailure)
                        else []
                    ),
                )
                write_failure_artifact(
                    out_dir, "extraction", logical_run_id, failure
                )
                self._log(f"Extraction failed for {pdf_path.name}: {exc}")
                raise
            target = _available_target(out_dir / f"{pdf_path.stem}.json", reserved_targets)
            reserved_targets.add(target)
            artifact = build_success_artifact(
                artifact_type="extraction_result",
                contract_version="1.0.0",
                data=record,
                provenance=self._provenance(pdf_path, logical_run_id),
                data_contract_schema=self.extraction_contract,
            )
            write_artifact(target, artifact, data_contract_schema=self.extraction_contract)
            written.append(target)
        return written

    def _provenance(self, pdf_path: Path, run_id: str) -> dict[str, object]:
        return {
            "run_id": run_id, "provider": self.selection.provider,
            "model": self.selection.model,
            "document_input": self.selection.document_input,
            "source_documents": [pdf_path.as_posix()], "source_artifacts": [],
        }

    def _log(self, message: str) -> None:
        if self.log:
            self.log(message)

    def _log_usage(
        self, response: ModelResponse, pdf_path: Path, duration: float,
        run_id: str, attempt_number: int, validation_succeeded: bool,
    ) -> None:
        usage = response.usage
        append_jsonl(
            self.usage_log_path,
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "extraction", "provider": response.provider,
                "model": response.model, "document_input": self.selection.document_input,
                "api_key_env": response.api_key_env, "source_pdf": pdf_path.as_posix(),
                "duration_seconds": duration, "run_id": run_id,
                "attempt_number": attempt_number,
                "validation_succeeded": validation_succeeded,
                "input_tokens": usage.input_tokens if usage else None,
                "output_tokens": usage.output_tokens if usage else None,
                "total_tokens": usage.total_tokens if usage else None,
            },
            log=self._log,
            error_label="extraction usage log",
        )


def _available_target(path: Path, reserved: set[Path]) -> Path:
    candidate = path
    suffix = 2
    while candidate.exists() or candidate in reserved:
        candidate = path.with_name(f"{path.stem}_{suffix}{path.suffix}")
        suffix += 1
    return candidate
