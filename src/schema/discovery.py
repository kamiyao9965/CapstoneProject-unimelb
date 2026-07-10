from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from src.common.openai_run import (
    DEFAULT_OPENAI_API_KEY_ENV,
    PROJECT_API_KEY_ENV,
    append_jsonl,
    create_openai_client,
    managed_uploaded_pdfs,
    resolve_api_key,
    run_response,
    usage_value,
)
from src.schema.prompts import SCHEMA_DISCOVERY_PROMPT, SCHEMA_PATCH_PROMPT


class SchemaDiscovery:
    def __init__(
        self,
        model: str = "gpt-5",
        client: object | None = None,
        cleanup_uploaded_files: bool = True,
        timeout_seconds: float = 600.0,
        usage_log_path: str | Path | None = "outputs/private_health/token_usage.jsonl",
        log: Callable[[str], None] | None = print,
        request_params: dict[str, object] | None = None,
        extra_instructions: str | None = None,
        background: bool = True,
        poll_interval: float = 5.0,
    ) -> None:
        self.model = model
        self.client = client
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
        # Extra kwargs forwarded to responses.create, e.g. {"temperature": 0} or
        # {"seed": 7}. Only what the caller sets is sent; support varies by model
        # (gpt-5 reasoning models may reject temperature), so this is opt-in.
        self.request_params = dict(request_params or {})

    def discover(self, sample_pdfs: list[str], output_path: str | Path | None = None) -> str:
        return self._generate_from_pdfs(
            sample_pdfs=sample_pdfs,
            system_prompt=SCHEMA_DISCOVERY_PROMPT,
            input_content_factory=self._input_content,
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
            input_content_factory=lambda pdf_paths, file_ids: self._patch_input_content(
                pdf_paths, file_ids, current_schema
            ),
            output_path=output_path,
            usage_event="schema_consensus_patch",
            progress_message="Generating schema patch candidates",
        )

    def _generate_from_pdfs(
        self,
        sample_pdfs: list[str],
        system_prompt: str,
        input_content_factory: Callable[[list[Path], list[str]], list[dict[str, str]]],
        output_path: str | Path | None,
        usage_event: str,
        progress_message: str,
    ) -> str:
        api_key, api_key_env = resolve_api_key()
        if not api_key:
            raise RuntimeError(
                f"{PROJECT_API_KEY_ENV} or {DEFAULT_OPENAI_API_KEY_ENV} must be set "
                "to generate a schema with OpenAI."
            )

        started_at = datetime.now(timezone.utc)
        started_perf = time.perf_counter()
        # max_retries above the SDK default (2) so transient upstream blips
        # (e.g. Cloudflare 520s) are absorbed with backoff instead of aborting a
        # run after the PDFs are already uploaded.
        client = create_openai_client(
            self.client,
            api_key=api_key,
            timeout_seconds=self.timeout_seconds,
        )
        pdf_paths = [Path(path) for path in sample_pdfs]
        resolved_output_path = Path(output_path) if output_path else None
        with managed_uploaded_pdfs(
            client,
            pdf_paths,
            cleanup=self.cleanup_uploaded_files,
            log=self._log,
        ) as file_ids:
            self._log(
                f"{progress_message} with {self.model} using {api_key_env}. "
                "This can take a few minutes..."
            )
            if self.extra_instructions:
                system_prompt += "\n\nRefinement feedback from the previous round:\n"
                system_prompt += self.extra_instructions
            response = run_response(
                client,
                background=self.background,
                poll_interval=self.poll_interval,
                poll_timeout=self.timeout_seconds,
                log=self.log,
                model=self.model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": input_content_factory(pdf_paths, file_ids)},
                ],
                **self.request_params,
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
                usage_event,
            )
            return self._clean_yaml(response.output_text)

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

    def _patch_input_content(
        self,
        pdf_paths: list[Path],
        file_ids: list[str],
        current_schema: str,
    ) -> list[dict[str, str]]:
        sample_list = "\n".join(f"- {path.as_posix()}" for path in pdf_paths)
        content = [
            {
                "type": "input_text",
                "text": (
                    "Current YAML schema baseline:\n"
                    f"{current_schema}\n\n"
                    "Generate candidate schema patches from these PDFs:\n"
                    f"{sample_list}"
                ),
            }
        ]
        content.extend({"type": "input_file", "file_id": file_id} for file_id in file_ids)
        return content

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
        usage_event: str = "schema_discovery",
    ) -> None:
        usage = getattr(response, "usage", None)
        input_tokens = None
        output_tokens = None
        total_tokens = None

        if usage is None:
            self._log(f"Task duration: {duration_seconds:.3f}s")
            self._log("Token usage: unavailable")
        else:
            input_tokens = usage_value(usage, "input_tokens", "prompt_tokens")
            output_tokens = usage_value(usage, "output_tokens", "completion_tokens")
            total_tokens = usage_value(usage, "total_tokens")

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
