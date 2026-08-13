from __future__ import annotations

import time
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Callable, Mapping
from uuid import uuid4

from src.PDFingestor.adapter import (
    DEFAULT_CACHE_DIR,
    document_quality,
    ingest_pdfs,
    render_documents_for_prompt,
)
from src.common.json_artifacts import (
    ArtifactError,
    build_failure_artifact,
    build_success_artifact,
    read_artifact,
    write_artifact,
    write_failure_artifact,
)
from src.common.json_contracts import validate_contract
from src.common.json_codec import dumps_json
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
from src.schema.product_types import (
    DEFAULT_OVERRIDE_PATH,
    load_product_type_overrides,
    resolve_product_type,
)
from src.schema_application.normalizer import normalize_extraction
from src.schema_application.prompts import EXTRACTION_PROMPT, EXTRACTION_PROMPT_VERSION
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
        product_type_overrides_path: str | Path | None = DEFAULT_OVERRIDE_PATH,
        extraction_cache_dir: str | Path | None = None,
    ) -> None:
        validate_contract(schema_data, "private_health/discovered_schema")
        validate_schema_mapping(schema_data)
        self.schema_data = dict(schema_data)
        self.schema_hash = _schema_hash(self.schema_data)
        self.extraction_contract = compile_extraction_contract(schema_data)
        self.selection = selection or ModelSelection("openai", model, "markdown")
        if self.selection.document_input != "markdown":
            raise ValueError(
                "SchemaExtractor uses PDFingestor's inline text representation; "
                "set LLM_DOCUMENT_INPUT=markdown."
            )
        self.model = self.selection.model
        self.provider = provider or create_provider(self.selection, client=client)
        self.pdf_root = Path(pdf_root) if pdf_root else None
        self.product_type_overrides = load_product_type_overrides(product_type_overrides_path)
        self.preprocessor = preprocessor
        self.pdfingestor_cache_dir = Path(pdfingestor_cache_dir or DEFAULT_CACHE_DIR)
        self.extraction_cache_dir = Path(
            extraction_cache_dir or self.pdfingestor_cache_dir.parent / "extraction_cache"
        )
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
        documents = ingest_pdfs(
            (pdf_path,), cache_dir=self.pdfingestor_cache_dir, pdf_root=self.pdf_root
        )
        quality = document_quality(documents)
        if quality["hard_failures"]:
            raise ValueError(
                f"PDFingestor produced unusable input for {pdf_path.name}: "
                f"{', '.join(quality['hard_failures'])}; quality={quality}"
            )
        if not quality["has_key_heading"]:
            self._log(f"Input quality warning for {pdf_path.name}: no expected cover heading; {quality}")
        document_text = render_documents_for_prompt(
            documents,
            table_format="tsv",
            comment_level="none",
            include_document_metadata=False,
            conservative_filter=True,
        )
        request = ProviderRequest(
            selection=self.selection,
            system_prompt=EXTRACTION_PROMPT,
            user_text=(
                "Compact field guide for extraction semantics:\n"
                f"{_compact_field_guide(self.schema_data)}\n\n"
                "Extract from this PDFingestor structured representation. "
                "Text and delimited tables are already in source reading order; "
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
                strict=not any(
                    field["type"] == "list[object]"
                    for field in self.schema_data["fields"]
                ),
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
        return normalize_extraction(result.data, self.schema_data)

    def extract_many(self, pdf_paths: list[str | Path], out_dir: str | Path) -> list[Path]:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        reserved_targets: set[Path] = set()
        run_cache: dict[str, dict[str, object]] = {}
        for pdf_value in pdf_paths:
            pdf_path = Path(pdf_value)
            cached = self._cached_extraction(pdf_path, out_dir)
            if cached is not None:
                self._log(f"Reusing cached extraction for {pdf_path.name}: {cached}")
                written.append(cached)
                reserved_targets.add(cached)
                continue
            cache_key = self._extraction_cache_key(pdf_path)
            cached_data = run_cache.get(cache_key) or self._read_shared_cache(cache_key)
            logical_run_id = uuid4().hex
            try:
                record = cached_data or self.extract_one(pdf_path, run_id=logical_run_id)
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
                continue
            run_cache[cache_key] = record
            if cached_data is None:
                self._write_shared_cache(cache_key, record, pdf_path, logical_run_id)
            target = _available_target(out_dir / f"{pdf_path.stem}.json", reserved_targets)
            reserved_targets.add(target)
            artifact = build_success_artifact(
                artifact_type="extraction_result",
                contract_version="1.0.0",
                data=record,
                provenance=self._provenance(
                    pdf_path,
                    logical_run_id,
                    model_product_type=record.get("product_type"),
                ),
                data_contract_schema=self.extraction_contract,
            )
            write_artifact(target, artifact, data_contract_schema=self.extraction_contract)
            written.append(target)
        return written

    def _extraction_cache_key(self, pdf_path: Path) -> str:
        material = "\0".join((
            _file_hash(pdf_path), self.schema_hash, EXTRACTION_PROMPT_VERSION,
            self.selection.provider, self.selection.model,
        ))
        return sha256(material.encode("utf-8")).hexdigest()

    def _shared_cache_path(self, cache_key: str) -> Path:
        return self.extraction_cache_dir / cache_key[:2] / f"{cache_key}.json"

    def _read_shared_cache(self, cache_key: str) -> dict[str, object] | None:
        path = self._shared_cache_path(cache_key)
        if not path.exists():
            return None
        try:
            return dict(read_artifact(
                path, expected_type="extraction_result",
                data_contract_schema=self.extraction_contract,
            )["data"])
        except ArtifactError:
            return None

    def _write_shared_cache(
        self, cache_key: str, record: Mapping[str, object], pdf_path: Path, run_id: str
    ) -> None:
        cache_path = self._shared_cache_path(cache_key)
        if cache_path.exists():
            return
        artifact = build_success_artifact(
            artifact_type="extraction_result", contract_version="1.0.0", data=record,
            provenance=self._provenance(pdf_path, run_id),
            data_contract_schema=self.extraction_contract,
        )
        write_artifact(
            cache_path, artifact,
            data_contract_schema=self.extraction_contract,
        )

    def _provenance(
        self,
        pdf_path: Path,
        run_id: str,
        *,
        model_product_type: object | None = None,
    ) -> dict[str, object]:
        source_artifacts = [
            f"schema_sha256:{self.schema_hash}",
            f"pdf_sha256:{_file_hash(pdf_path)}",
            f"prompt_version:{EXTRACTION_PROMPT_VERSION}",
            f"provider:{self.selection.provider}",
            f"model:{self.selection.model}",
        ]
        resolution = resolve_product_type(
            pdf_path,
            input_root=self.pdf_root,
            overrides=self.product_type_overrides,
        )
        if resolution.directory_product_type:
            source_artifacts.append(
                f"directory_product_type:{resolution.directory_product_type}"
            )
        if resolution.override_product_type:
            source_artifacts.append(
                f"override_product_type:{resolution.override_product_type}"
            )
        if resolution.effective_product_type:
            source_artifacts.append(
                f"effective_product_type:{resolution.effective_product_type}"
            )
        if resolution.conflict:
            source_artifacts.append("product_type_conflict:directory_override")
        if isinstance(model_product_type, str) and model_product_type.strip():
            source_artifacts.append(
                f"model_product_type:{model_product_type.strip().lower()}"
            )
        return {
            "run_id": run_id, "provider": self.selection.provider,
            "model": self.selection.model,
            "document_input": self.selection.document_input,
            "source_documents": [pdf_path.as_posix()],
            "source_artifacts": source_artifacts,
        }

    def _cached_extraction(self, pdf_path: Path, out_dir: Path) -> Path | None:
        if not pdf_path.exists():
            return None
        expected_schema = f"schema_sha256:{self.schema_hash}"
        expected_pdf = f"pdf_sha256:{_file_hash(pdf_path)}"
        expected_prompt = f"prompt_version:{EXTRACTION_PROMPT_VERSION}"
        expected_provider = f"provider:{self.selection.provider}"
        expected_model = f"model:{self.selection.model}"
        for candidate in sorted(out_dir.glob("*.json")):
            try:
                artifact = read_artifact(
                    candidate,
                    expected_type="extraction_result",
                    data_contract_schema=self.extraction_contract,
                )
            except ArtifactError:
                continue
            provenance = artifact.get("provenance") or {}
            source_artifacts = set(provenance.get("source_artifacts") or [])
            if {expected_schema, expected_pdf, expected_prompt, expected_provider, expected_model} <= source_artifacts:
                return candidate
        return None

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


def _schema_hash(schema_data: Mapping[str, object]) -> str:
    return sha256(
        dumps_json(
            schema_data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _file_hash(path: Path) -> str:
    digest = sha256()
    if not path.exists():
        digest.update(f"missing:{path.as_posix()}".encode("utf-8"))
        return digest.hexdigest()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compact_field_guide(schema_data: Mapping[str, object]) -> str:
    lines = []
    for field in schema_data.get("fields", []):
        if not isinstance(field, Mapping):
            continue
        name = field.get("name")
        if not name:
            continue
        description = _compact_text(field.get("description"))
        applies_to = _compact_list(field.get("applies_to"))
        aliases = _compact_list(field.get("aliases"))
        details = []
        if description:
            details.append(f"description={description}")
        if applies_to:
            details.append(f"applies_to={applies_to}")
        if aliases:
            details.append(f"aliases={aliases}")
        lines.append(f"- {name}: " + "; ".join(details))
    extras_services = [
        str(item.get("canonical_name"))
        for item in schema_data.get("extras_services", [])
        if isinstance(item, Mapping) and item.get("canonical_name")
    ]
    if extras_services:
        lines.append("- extras_benefits.service_name allowed values: " + ", ".join(extras_services))
    return "\n".join(lines)


def _compact_text(value: object) -> str:
    return " ".join(str(value).split()) if value else ""


def _compact_list(value: object) -> str:
    if not isinstance(value, list):
        return ""
    return ", ".join(_compact_text(item) for item in value if _compact_text(item))
