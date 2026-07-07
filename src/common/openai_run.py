from __future__ import annotations

import time
from typing import Callable

# Statuses a background response can be in while still running.
PENDING_STATUSES = {"queued", "in_progress"}


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
