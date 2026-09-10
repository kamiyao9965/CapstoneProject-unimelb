from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from src.PDFingestor.adapter import render_pdf_paths_for_prompt
from src.common.json_artifacts import (
    build_failure_artifact,
    write_failure_artifact,
)
from src.common.json_contracts import load_contract
from src.common.model_config import ModelSelection
from src.common.model_provider import (
    ModelProvider,
    ModelResponse,
    ProviderRequest,
    StructuredOutputSpec,
    create_provider,
)
from src.common.openai_run import append_jsonl
from src.common.structured_output import (
    StructuredOutputFailure,
    run_structured_output,
)
from src.verticals.manifest import resolve_manifest, VerticalManifest
from src.verticals.registry import get_prompt, get_schema_validator
from src.schema.validation import validate_schema_mapping
from src.refine.candidates.patch import parse_patch_payload


class SchemaDiscovery:
    def __init__(
        self,
        model: str = "gpt-5",
        client: object | None = None,
        selection: ModelSelection | None = None,
        provider: ModelProvider | None = None,
        cleanup_uploaded_files: bool = True,
        timeout_seconds: float = 600.0,
        usage_log_path: str | Path | None = None,
        log: Callable[[str], None] | None = print,
        request_params: dict[str, object] | None = None,
        extra_instructions: str | None = None,
        background: bool = True,
        poll_interval: float = 5.0,
        pdf_root: str | Path | None = None,
        preprocessor: object | None = None,
        pdfingestor_cache_dir: str | Path | None = None,
        vertical: str | None = None,
        discovery_contract: str | None = None,
        discovery_prompt: str | None = None,
        schema_validator: Callable[[object], object] | None = None,
        patch_contract: str | None = None,
        patch_prompt: str | None = None,
        patch_validator: Callable[[object], object] | None = None,
        manifest: VerticalManifest | None = None,
    ) -> None:
        manifest = manifest or resolve_manifest(vertical=vertical)
        manifest.require_capability("discovery")
        self.manifest = manifest
        self.selection = selection or ModelSelection("openai", model, "markdown")
        if self.selection.document_input != "markdown":
            raise ValueError(
                "SchemaDiscovery uses PDFingestor's inline text representation; "
                "set LLM_DOCUMENT_INPUT=markdown or pass --document-input markdown."
            )
        self.model = self.selection.model
        self.provider = provider or create_provider(self.selection, client=client)
        # Discovery always consumes PDFingestor's Silver-layer text/table
        # representation and sends it inline as Markdown-compatible text.
        self.pdf_root = Path(pdf_root) if pdf_root else None
        self.preprocessor = preprocessor
        self.pdfingestor_cache_dir = Path(pdfingestor_cache_dir or manifest.path("output_root") / "pdfingestor_cache")
        self.cleanup_uploaded_files = cleanup_uploaded_files
        self.timeout_seconds = timeout_seconds
        self.usage_log_path = Path(usage_log_path) if usage_log_path else None
        self.log = log
        # Background mode + polling avoids the long synchronous connection that
        # gateways drop with a 520 on heavy multi-file requests. On by default.
        self.background = background
        self.poll_interval = poll_interval
        # Appended to the system prompt. The refinement loop uses this to feed
        # back failures from the previous round ("field X was never extractable,
        # drop or clarify it").
        self.extra_instructions = extra_instructions
        # Extra kwargs forwarded to the provider request, e.g. {"temperature": 0} or
        # {"seed": 7}. Only what the caller sets is sent; support varies by model
        # (gpt-5 reasoning models may reject temperature), so this is opt-in.
        self.request_params = dict(request_params or {})
        self.vertical = manifest.vertical
        self.discovery_contract = discovery_contract or manifest.contract("discovered_schema")
        self.discovery_prompt = discovery_prompt or get_prompt(manifest.prompt("discovery"))
        self.schema_validator = schema_validator or get_schema_validator(manifest)
        self.patch_contract = patch_contract or manifest.contract("candidate_patch_set")
        self.patch_prompt = patch_prompt or get_prompt(manifest.prompt("patch"))
        self.patch_validator = patch_validator or (lambda payload: parse_patch_payload(payload, allowed_product_types=set(manifest.product_types)))

    def discover(
        self,
        sample_pdfs: list[str],
        output_path: str | Path | None = None,
        *,
        run_id: str | None = None,
    ) -> dict[str, object]:
        return self._generate_from_pdfs(
            sample_pdfs=sample_pdfs,
            system_prompt=self.discovery_prompt,
            user_text_factory=self._input_text,
            output_path=output_path,
            usage_event="schema_discovery",
            progress_message="Generating schema",
            run_id=run_id,
        )

    def discover_patches(
        self,
        sample_pdfs: list[str],
        current_schema: dict[str, object],
        output_path: str | Path | None = None,
        *,
        run_id: str | None = None,
    ) -> dict[str, object]:
        """Propose candidate JSON patches against an existing schema baseline.

        Consensus refinement calls this once per run and votes on the patches
        across runs, instead of regenerating the full schema each time.
        """
        self.manifest.require_capability("refinement")
        self.schema_validator(current_schema)
        return self._generate_from_pdfs(
            sample_pdfs=sample_pdfs,
            system_prompt=self.patch_prompt,
            user_text_factory=lambda pdf_paths: self._patch_input_text(pdf_paths, current_schema),
            output_path=output_path,
            usage_event="schema_consensus_patch",
            progress_message="Generating schema patch candidates",
            run_id=run_id,
        )

    def _generate_from_pdfs(
        self,
        sample_pdfs: list[str],
        system_prompt: str,
        user_text_factory: Callable[[list[Path]], str],
        output_path: str | Path | None,
        usage_event: str,
        progress_message: str,
        run_id: str | None,
    ) -> dict[str, object]:
        started_at = datetime.now(timezone.utc)
        started_perf = time.perf_counter()
        pdf_paths = [Path(path) for path in sample_pdfs]
        resolved_output_path = Path(output_path) if output_path else None
        self._log(f"{progress_message} with {self.selection.provider}/{self.model}. This can take a few minutes...")
        if self.extra_instructions:
            system_prompt += "\n\nRefinement feedback from the previous round:\n"
            system_prompt += self.extra_instructions
        logical_run_id = run_id or uuid4().hex
        try:
            document_text = render_pdf_paths_for_prompt(
                pdf_paths,
                cache_dir=self.pdfingestor_cache_dir,
                pdf_root=self.pdf_root,
            )
        except Exception as exc:
            self._write_failure(
                resolved_output_path, usage_event, logical_run_id, pdf_paths, exc
            )
            raise
        request = ProviderRequest(
            selection=self.selection,
            system_prompt=system_prompt,
            user_text=f"{user_text_factory(pdf_paths)}\n\n{document_text}",
            document_paths=(),
            timeout_seconds=self.timeout_seconds,
            cleanup_documents=self.cleanup_uploaded_files,
            request_params=self.request_params,
            background=self.background,
            poll_interval=self.poll_interval,
            log=self.log,
        )
        structured_contract = {
            "schema_discovery": (
                "discovered_schema",
                self.discovery_contract,
                self.schema_validator,
            ),
            "schema_consensus_patch": (
                "candidate_patch_set",
                self.patch_contract,
                self.patch_validator,
            ),
        }.get(usage_event)
        if structured_contract is not None:
            output_name, contract_name, business_validator = structured_contract
            request = replace(
                request,
                structured_output=StructuredOutputSpec(
                    name=output_name,
                    schema=load_contract(contract_name, manifest=self.manifest),
                ),
            )
            try:
                result = run_structured_output(
                    self.provider,
                    request,
                    data_contract_schema=request.structured_output.schema,
                    business_validator=business_validator,
                )
            except StructuredOutputFailure as exc:
                completed_at = datetime.now(timezone.utc)
                duration_seconds = round(time.perf_counter() - started_perf, 3)
                for attempt in exc.result.attempts:
                    self._log_usage(
                        attempt.response, pdf_paths, started_at, completed_at,
                        duration_seconds, resolved_output_path, usage_event,
                        run_id=logical_run_id, attempt_number=attempt.number,
                        validation_succeeded=False,
                    )
                self._write_failure(
                    resolved_output_path,
                    usage_event,
                    logical_run_id,
                    pdf_paths,
                    exc,
                    details=exc.result.errors,
                )
                raise
            except Exception as exc:
                self._write_failure(
                    resolved_output_path,
                    usage_event,
                    logical_run_id,
                    pdf_paths,
                    exc,
                )
                raise
            completed_at = datetime.now(timezone.utc)
            duration_seconds = round(time.perf_counter() - started_perf, 3)
            for attempt in result.attempts:
                self._log_usage(
                    attempt.response, pdf_paths, started_at, completed_at,
                    duration_seconds, resolved_output_path, usage_event,
                    run_id=logical_run_id, attempt_number=attempt.number,
                    validation_succeeded=not attempt.errors,
                )
            assert result.data is not None
            return validate_schema_mapping(result.data, manifest=self.manifest) if usage_event == "schema_discovery" else result.data

        raise RuntimeError(f"No structured output contract configured for {usage_event}.")

    def _write_failure(
        self,
        output_path: Path | None,
        stage: str,
        run_id: str,
        pdf_paths: list[Path],
        error: Exception,
        *,
        details=(),
    ) -> None:
        if output_path is None:
            return
        artifact = build_failure_artifact(
            artifact_type=f"{stage}_error",
            contract_version="1.0.0",
            provenance={
                "run_id": run_id,
                "provider": self.selection.provider,
                "model": self.selection.model,
                "document_input": self.selection.document_input,
                "source_documents": [path.as_posix() for path in pdf_paths],
                "source_artifacts": [],
            },
            error_code=(
                "structured_output_exhausted"
                if isinstance(error, StructuredOutputFailure)
                else f"{stage}_failed"
            ),
            message=str(error),
            details=details,
        )
        try:
            write_failure_artifact(output_path.parent, stage, run_id, artifact)
        except Exception as artifact_error:
            self._log(f"Could not write {stage} failure artifact: {artifact_error}")

    def _input_text(self, pdf_paths: list[Path]) -> str:
        sample_list = "\n".join(f"- {path.as_posix()}" for path in pdf_paths)
        return f"Generate a {self.vertical} schema from these PDFs:\n{sample_list}"

    def _patch_input_text(
        self,
        pdf_paths: list[Path],
        current_schema: dict[str, object],
    ) -> str:
        sample_list = "\n".join(f"- {path.as_posix()}" for path in pdf_paths)
        return (
            "Current JSON schema baseline:\n"
            f"{json.dumps(current_schema, ensure_ascii=False)}\n\n"
            "Generate candidate schema patches from these PDFs:\n"
            f"{sample_list}"
        )

    def _log(self, message: str) -> None:
        if self.log:
            self.log(message)

    def _log_usage(
        self,
        response: ModelResponse,
        pdf_paths: list[Path],
        started_at: datetime,
        completed_at: datetime,
        duration_seconds: float,
        output_path: Path | None,
        usage_event: str = "schema_discovery",
        *,
        run_id: str,
        attempt_number: int,
        validation_succeeded: bool,
    ) -> None:
        usage = response.usage
        input_tokens = usage.input_tokens if usage else None
        output_tokens = usage.output_tokens if usage else None
        total_tokens = usage.total_tokens if usage else None

        if usage is None:
            self._log(f"Task duration: {duration_seconds:.3f}s")
            self._log("Token usage: unavailable")
        else:
            self._log(f"Task duration: {duration_seconds:.3f}s")
            parts = []
            if input_tokens is not None:
                parts.append(f"input={input_tokens}")
            if output_tokens is not None:
                parts.append(f"output={output_tokens}")
            if total_tokens is not None:
                parts.append(f"total={total_tokens}")

            self._log("Token usage: " + (", ".join(parts) if parts else "unavailable"))

        append_jsonl(
            self.usage_log_path,
            {
                "timestamp": completed_at.isoformat(),
                "event": usage_event,
                "provider": response.provider,
                "model": response.model,
                "document_input": self.selection.document_input,
                "api_key_env": response.api_key_env,
                "artifact_output_path": output_path.as_posix() if output_path else None,
                "run_id": run_id,
                "attempt_number": attempt_number,
                "validation_succeeded": validation_succeeded,
                "started_at": started_at.isoformat(),
                "completed_at": completed_at.isoformat(),
                "duration_seconds": duration_seconds,
                "response_id": response.response_id,
                "sample_count": len(pdf_paths),
                "sample_pdfs": [path.as_posix() for path in pdf_paths],
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
            },
            log=self._log,
        )
