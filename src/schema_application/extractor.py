from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Callable, Mapping
from uuid import uuid4

from src.pdf_ingestion.adapter import (
    DEFAULT_DOCUMENT_PARSER,
    render_pdf_paths_for_prompt,
    require_document_parser,
)
from src.common.json_artifacts import (
    build_failure_artifact,
    next_available_path,
    build_success_artifact,
    write_artifact,
    write_failure_artifact,
)
from src.common.model_config import ModelSelection, resolve_selection, require_structured_output_capability
from src.common.model_provider import (
    ModelProvider,
    ProviderRequest,
    StructuredOutputSpec,
    create_provider,
)
from src.common.openai_run import append_jsonl
from src.common.structured_output import (
    StructuredOutputFailure,
    StructuredOutputResult,
    run_structured_output,
)
from src.schema.contract import compile_extraction_contract
from src.schema.nested import closed_extraction_contract
from src.schema.canonical import (
    compile_canonical_extraction_contract,
    is_canonical_schema,
    require_approved_canonical_schema,
    validate_canonical_extraction_identities,
)
from src.verticals.manifest import VerticalManifest, resolve_manifest
from src.verticals.registry import get_prompt, get_shared_prompt, get_business_rules, business_validation_summary
from src.verticals.documents import DocumentGroup, resolve_document_groups
from src.schema_application.records import extraction_binding
from src.schema.validation import validate_schema_mapping, validate_extraction_record


class SchemaExtractor:
    """Extract one validated record per explicitly linked document group."""

    def __init__(
        self,
        schema_data: Mapping[str, object],
        *,
        selection: ModelSelection | None = None,
        provider: ModelProvider | None = None,
        cleanup_uploaded_files: bool = True,
        timeout_seconds: float = 600.0,
        usage_log_path: str | Path | None = None,
        log: Callable[[str], None] | None = print,
        background: bool = True,
        poll_interval: float = 5.0,
        pdf_root: str | Path | None = None,
        pdfingestor_cache_dir: str | Path | None = None,
        document_parser: str = DEFAULT_DOCUMENT_PARSER,
        parsed_markdown_dir: str | Path | None = None,
        manifest: VerticalManifest | None = None,
    ) -> None:
        manifest = manifest or resolve_manifest(vertical=schema_data.get("vertical"))
        manifest.require_capability("extraction")
        if schema_data.get("vertical") != manifest.vertical:
            raise ValueError("Schema vertical does not match manifest.")
        self.manifest = manifest
        self.business_rules = get_business_rules(manifest)
        output_cardinality = manifest.documents.output_cardinality
        self.schema_data = dict(schema_data)
        self.extraction_business_validator: Callable[[object], object] | None = None
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
            self.extraction_business_validator = partial(
                validate_canonical_extraction_identities,
                canonical_schema,
            )
            self.schema_prompt_label = "Approved Canonical Schema"
        else:
            self.schema_data = validate_schema_mapping(dict(schema_data), manifest=manifest)
            self.extraction_business_validator = partial(validate_extraction_record, self.schema_data, manifest=manifest)
            self.extraction_contract = compile_extraction_contract(
                self.schema_data,
                manifest=manifest,
            )
            self.schema_prompt_label = "Discovered schema"
        self.extraction_contract = closed_extraction_contract(self.extraction_contract)
        if self.business_rules is not None:
            self.business_rules.validate_schema(self.schema_data, manifest)
        self.structured_output_strict = True
        self.extraction_prompt = get_prompt(manifest.prompt("extraction"))
        if self.business_rules is not None and hasattr(self.business_rules, "extraction_guidance"):
            self.extraction_prompt += self.business_rules.extraction_guidance(self.schema_data)
        self.binding = extraction_binding(self.schema_data, manifest)
        self.selection = selection or resolve_selection()
        if self.selection.document_input != "markdown":
            raise ValueError(
                "SchemaExtractor uses PDFingestor's inline text representation; "
                "set LLM_DOCUMENT_INPUT=markdown."
            )
        self.model = self.selection.model
        require_structured_output_capability(self.selection, strict=True)
        self.provider = provider or create_provider(self.selection)
        self.document_parser = require_document_parser(document_parser)
        self.pdf_root = Path(pdf_root) if pdf_root else None
        self.pdfingestor_cache_dir = Path(pdfingestor_cache_dir or manifest.path("cache_root"))
        self.parsed_markdown_dir = Path(parsed_markdown_dir or manifest.path("markdown_root"))
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
        group = self._groups([pdf_path])[0]
        return self.extract_group(group, run_id=run_id)

    def _groups(self, pdf_paths: list[str | Path]) -> list[DocumentGroup]:
        if not pdf_paths:
            return []
        paths = [Path(path).resolve() for path in pdf_paths]
        root = self.pdf_root
        if root is None:
            root = self.manifest.path("input_root") if self.manifest.documents.inventory else Path(os.path.commonpath([path.parent for path in paths]))
        return resolve_document_groups(self.manifest, paths, input_root=root)

    def extract_group(self, group: DocumentGroup, *, run_id: str | None = None) -> dict[str, object]:
        """Send the base PDFs and their explicit amendments in one request."""
        current = self._groups(list(group.pdf_paths))
        if len(current) != 1 or current[0].provenance() != group.provenance():
            raise ValueError("Document group changed before extraction; resolve it again.")
        pdf_path = group.primary_path
        started = time.perf_counter()
        logical_run_id = run_id or uuid4().hex
        self._log(f"Extracting {pdf_path.name} with {self.selection.provider}/{self.model}...")
        if self.document_parser == "mineru":
            self._log("Parsing the PDF locally with MinerU; an uncached document can take several minutes.")
        source_index: dict[str, dict] = {}
        parser_quality = self.manifest.adapters.get("parser_quality")
        if parser_quality not in {None, "car_parser_quality_v1"}:
            raise ValueError("Unknown parser quality adapter.")
        document_text = render_pdf_paths_for_prompt(
            group.pdf_paths,
            cache_dir=self.pdfingestor_cache_dir,
            pdf_root=self.pdf_root,
            document_parser=self.document_parser,
            markdown_dir=self.parsed_markdown_dir,
            document_refs=[ref.to_dict() for ref in group.documents],
            source_index=source_index if self.business_rules is not None else None,
            parser_quality_gate=parser_quality is not None,
            parser_review_path=self.manifest.path("parser_review") if "parser_review" in self.manifest.paths else None,
        )
        context = None
        if self.business_rules is not None:
            addendum, context = self.business_rules.source_context(self.schema_data, source_index)
            document_text += addendum

        def validate_result(payload: object) -> object:
            if is_canonical_schema(self.schema_data):
                self.extraction_business_validator(payload)
            else:
                validate_extraction_record(self.schema_data, payload, manifest=self.manifest, document_count=len(group.documents))
            if self.business_rules is not None:
                if is_canonical_schema(self.schema_data):
                    self.business_rules.validate_records(self.schema_data, payload, document_count=len(group.documents))
                self.business_rules.validate_sources(self.schema_data, payload, source_index, context)
            return payload
        request = ProviderRequest(
            selection=self.selection,
            system_prompt=self.extraction_prompt,
            user_text=get_shared_prompt("extraction_request").format(
                schema_label=self.schema_prompt_label,
                schema_json=json.dumps(self.schema_data, ensure_ascii=False),
                document_text=document_text,
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
                business_validator=validate_result,
                drop_structural_noise=False,
            )
        except StructuredOutputFailure as exc:
            self._log_usage(exc.result, group, started, logical_run_id)
            raise
        self._log_usage(result, group, started, logical_run_id)
        assert result.data is not None
        return result.data

    def extract_many(self, pdf_paths: list[str | Path], out_dir: str | Path) -> list[Path]:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        reserved_targets: set[Path] = set()
        groups = self._groups(pdf_paths)
        order: dict[Path, int] = {}
        for index, value in enumerate(pdf_paths):
            order.setdefault(Path(value).resolve(), index)
        groups.sort(key=lambda group: min(order[path] for path in group.pdf_paths if path in order))
        for group in groups:
            pdf_path = group.primary_path
            logical_run_id = uuid4().hex
            try:
                record = self.extract_group(group, run_id=logical_run_id)
            except Exception as exc:
                failure = build_failure_artifact(
                    artifact_type="extraction_error",
                    contract_version="1.0.0",
                    provenance=self._provenance(group, logical_run_id),
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
            target = next_available_path(out_dir / f"{pdf_path.stem}.json", reserved_targets)
            reserved_targets.add(target)
            artifact = build_success_artifact(
                artifact_type="extraction_result",
                contract_version="1.0.0",
                data=record,
                provenance=self._provenance(group, logical_run_id),
                data_contract_schema=self.extraction_contract,
            )
            write_artifact(target, artifact, data_contract_schema=self.extraction_contract)
            written.append(target)
        return written

    def _provenance(self, group: DocumentGroup, run_id: str) -> dict[str, object]:
        summary = business_validation_summary(self.manifest, self.schema_data, document_count=len(group.documents))
        return {
            "vertical": self.schema_data["vertical"],
            "schema_version": str(self.schema_data["version"]),
            "run_id": run_id, "provider": self.selection.provider,
            "model": self.selection.model,
            "document_input": self.selection.document_input,
            "document_parser": self.document_parser,
            **group.provenance(), **self.binding, "source_artifacts": [],
            **({"validation_checks": summary} if summary is not None else {}),
        }

    def _log(self, message: str) -> None:
        if self.log:
            self.log(message)

    def _log_usage(
        self, result: StructuredOutputResult, group: DocumentGroup,
        started: float, run_id: str,
    ) -> None:
        duration = round(time.perf_counter() - started, 3)
        for attempt in result.attempts:
            response = attempt.response
            usage = response.usage
            append_jsonl(
                self.usage_log_path,
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": "extraction", "provider": response.provider,
                    "model": response.model, "document_input": self.selection.document_input,
                    "document_parser": self.document_parser,
                    "api_key_env": response.api_key_env, "source_pdf": group.primary_path.as_posix(),
                    **group.provenance(), **self.binding,
                    "validation_plan": business_validation_summary(self.manifest, self.schema_data, document_count=len(group.documents)),
                    "duration_seconds": duration, "run_id": run_id,
                    "attempt_number": attempt.number,
                    "validation_succeeded": not attempt.errors,
                    "input_tokens": usage.input_tokens if usage else None,
                    "output_tokens": usage.output_tokens if usage else None,
                    "total_tokens": usage.total_tokens if usage else None,
                },
                log=self._log,
                error_label="extraction usage log",
            )
