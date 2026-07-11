from __future__ import annotations

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
from src.schema.prompts import SCHEMA_DISCOVERY_PROMPT, SCHEMA_PATCH_PROMPT


class SchemaDiscovery:
    def __init__(
        self,
        model: str = "gpt-5",
        client: object | None = None,
        selection: ModelSelection | None = None,
        provider: ModelProvider | None = None,
        cleanup_uploaded_files: bool = True,
        timeout_seconds: float = 600.0,
        usage_log_path: str | Path | None = "outputs/private_health/token_usage.jsonl",
        log: Callable[[str], None] | None = print,
        request_params: dict[str, object] | None = None,
        extra_instructions: str | None = None,
        background: bool = True,
        poll_interval: float = 5.0,
        pdf_root: str | Path | None = None,
        preprocessor: MarkdownPreprocessor | None = None,
    ) -> None:
        self.selection = selection or ModelSelection("openai", model, "pdf")
        self.model = self.selection.model
        self.provider = provider or create_provider(self.selection, client=client)
        # Markdown mode maps sampled PDFs to their mirrored Markdown paths
        # before the provider request; PDF mode never touches the preprocessor.
        self.pdf_root = Path(pdf_root) if pdf_root else None
        self.preprocessor = preprocessor
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

    def discover(self, sample_pdfs: list[str], output_path: str | Path | None = None) -> str:
        return self._generate_from_pdfs(
            sample_pdfs=sample_pdfs,
            system_prompt=SCHEMA_DISCOVERY_PROMPT,
            user_text_factory=self._input_text,
            output_path=output_path,
            usage_event="schema_discovery",
            progress_message="Generating schema",
        )

    def discover_patches(
        self,
        sample_pdfs: list[str],
        current_schema: str,
        output_path: str | Path | None = None,
    ) -> str:
        """Propose candidate YAML patches against an existing schema baseline.

        Consensus refinement calls this once per run and votes on the patches
        across runs, instead of regenerating the full schema each time.
        """
        return self._generate_from_pdfs(
            sample_pdfs=sample_pdfs,
            system_prompt=SCHEMA_PATCH_PROMPT,
            user_text_factory=lambda pdf_paths: self._patch_input_text(pdf_paths, current_schema),
            output_path=output_path,
            usage_event="schema_consensus_patch",
            progress_message="Generating schema patch candidates",
        )

    def _generate_from_pdfs(
        self,
        sample_pdfs: list[str],
        system_prompt: str,
        user_text_factory: Callable[[list[Path]], str],
        output_path: str | Path | None,
        usage_event: str,
        progress_message: str,
    ) -> str:
        started_at = datetime.now(timezone.utc)
        started_perf = time.perf_counter()
        pdf_paths = [Path(path) for path in sample_pdfs]
        resolved_output_path = Path(output_path) if output_path else None
        self._log(f"{progress_message} with {self.selection.provider}/{self.model}. This can take a few minutes...")
        if self.extra_instructions:
            system_prompt += "\n\nRefinement feedback from the previous round:\n"
            system_prompt += self.extra_instructions
        document_paths = prepare_documents(
            self.selection, pdf_paths, self.pdf_root, self.preprocessor
        )
        response = self.provider.generate(
            ProviderRequest(
                selection=self.selection,
                system_prompt=system_prompt,
                user_text=user_text_factory(pdf_paths),
                document_paths=document_paths,
                timeout_seconds=self.timeout_seconds,
                cleanup_documents=self.cleanup_uploaded_files,
                request_params=self.request_params,
                background=self.background,
                poll_interval=self.poll_interval,
                log=self.log,
            )
        )
        completed_at = datetime.now(timezone.utc)
        duration_seconds = round(time.perf_counter() - started_perf, 3)
        self._log_usage(
            response,
            pdf_paths,
            started_at,
            completed_at,
            duration_seconds,
            resolved_output_path,
            usage_event,
        )
        return self._clean_yaml(response.text)

    def _input_text(self, pdf_paths: list[Path]) -> str:
        sample_list = "\n".join(f"- {path.as_posix()}" for path in pdf_paths)
        return f"Generate a private_health YAML schema from these PDFs:\n{sample_list}"

    def _patch_input_text(
        self,
        pdf_paths: list[Path],
        current_schema: str,
    ) -> str:
        sample_list = "\n".join(f"- {path.as_posix()}" for path in pdf_paths)
        return (
            "Current YAML schema baseline:\n"
            f"{current_schema}\n\n"
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
                "yaml_output_path": output_path.as_posix() if output_path else None,
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

    @staticmethod
    def _clean_yaml(text: str) -> str:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.removeprefix("```yaml").removeprefix("```").strip()
            cleaned = cleaned.removesuffix("```").strip()
        return cleaned + "\n"
