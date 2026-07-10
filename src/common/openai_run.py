from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from openai import OpenAI

# Statuses a background response can be in while still running.
PENDING_STATUSES = {"queued", "in_progress"}
PROJECT_API_KEY_ENV = "MY_OPENAI_API_KEY"
DEFAULT_OPENAI_API_KEY_ENV = "OPENAI_API_KEY"


def resolve_api_key() -> tuple[str | None, str]:
    project_api_key = os.getenv(PROJECT_API_KEY_ENV)
    if project_api_key:
        return project_api_key, PROJECT_API_KEY_ENV

    default_api_key = os.getenv(DEFAULT_OPENAI_API_KEY_ENV)
    if default_api_key:
        return default_api_key, DEFAULT_OPENAI_API_KEY_ENV

    return None, PROJECT_API_KEY_ENV


def create_openai_client(
    client: object | None,
    *,
    api_key: str,
    timeout_seconds: float,
):
    if client is not None:
        return client
    return OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=5)


def usage_value(usage: object, *names: str) -> int | None:
    for name in names:
        if isinstance(usage, dict) and usage.get(name) is not None:
            return int(usage[name])
        value = getattr(usage, name, None)
        if value is not None:
            return int(value)
    return None


def append_jsonl(
    path: Path | None,
    payload: dict[str, object],
    *,
    log: Callable[[str], None] | None = None,
    error_label: str = "usage log",
) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as exc:
        if log:
            log(f"Failed to write {error_label}: {exc}")


def delete_uploaded_files(
    client,
    file_ids: list[str],
    log: Callable[[str], None] | None = None,
) -> None:
    for file_id in file_ids:
        try:
            client.files.delete(file_id)
        except Exception as exc:
            if log:
                log(f"Failed to delete uploaded file {file_id}: {exc}")


def upload_pdfs(
    client,
    pdf_paths: list[Path],
    log: Callable[[str], None] | None = None,
) -> list[str]:
    file_ids: list[str] = []
    try:
        for index, path in enumerate(pdf_paths, start=1):
            if not path.exists():
                raise FileNotFoundError(path)
            if log:
                log(f"Uploading PDF {index}/{len(pdf_paths)}: {path.name}")
            with path.open("rb") as handle:
                uploaded = client.files.create(file=handle, purpose="user_data")
            file_ids.append(uploaded.id)
    except Exception:
        delete_uploaded_files(client, file_ids, log)
        raise
    return file_ids


@contextmanager
def managed_uploaded_pdfs(
    client,
    pdf_paths: list[Path],
    *,
    cleanup: bool = True,
    log: Callable[[str], None] | None = None,
) -> Iterator[list[str]]:
    file_ids = upload_pdfs(client, pdf_paths, log)
    try:
        yield file_ids
    finally:
        if cleanup:
            if log:
                log("Cleaning up uploaded files...")
            delete_uploaded_files(client, file_ids, log)


def run_response(
    client,
    *,
    background: bool = True,
    poll_interval: float = 5.0,
    poll_timeout: float = 1800.0,
    log: Callable[[str], None] | None = None,
    **create_kwargs,
):
    """Create a response, optionally in background mode with polling.

    Long multi-file requests can be dropped by the gateway (Cloudflare 520)
    while the origin is still working. Background mode returns a response id
    almost immediately, so the create call is short-lived; we then poll
    responses.retrieve until the job reaches a terminal state. This keeps the
    connection off the long-request path that gets 520'd.
    """
    if not background:
        return client.responses.create(**create_kwargs)

    # Background responses must be stored so they can be retrieved by id.
    response = client.responses.create(background=True, store=True, **create_kwargs)
    deadline = time.time() + poll_timeout
    waited = 0.0
    while getattr(response, "status", None) in PENDING_STATUSES:
        if time.time() > deadline:
            raise TimeoutError(
                f"Response {response.id} still {response.status} after {poll_timeout:.0f}s"
            )
        time.sleep(poll_interval)
        waited += poll_interval
        if log and waited % 30 == 0:
            log(f"  ...still {response.status} ({waited:.0f}s elapsed)")
        response = client.responses.retrieve(response.id)

    status = getattr(response, "status", None)
    if status != "completed":
        detail = getattr(response, "error", None) or getattr(response, "incomplete_details", None)
        raise RuntimeError(f"Response {response.id} ended with status={status}: {detail}")
    return response
