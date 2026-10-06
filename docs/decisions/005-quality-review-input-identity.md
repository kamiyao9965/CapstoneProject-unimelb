# ADR-005: Bind quality review decisions to all audited inputs

## Status

Accepted

## Date

2026-10-06

## Context

Quality queue identity covered findings and selected pass samples, but omitted
PDF hashes and judge configuration. Identical findings could allow an old human
decision to attach to changed PDF bytes or a different judge. Unsampled pass
documents also had no content identity in the queue.

## Decision

Emit `quality_review_queue` contract version `2.0.0` with a sorted
`audited_inputs` entry for every report. Bind extraction/PDF paths and hashes,
judge provider/model, parser and prompt bundle through the existing `queue_id`
hash. Keep schema identity, sample parameters and findings in that same hash.
The human-review owner validates bindings and provenance before accepting
decisions. Existing item IDs remain valid only within their exact queue.

## Consequences

- Changed audited inputs require a new queue and new human decisions.
- Legacy `1.0.0` queues and decisions remain readable but cannot be updated or
  resumed into a writable queue in place. Summary-only remains available.
- Verified completed reports can be copied into a separate output directory
  and resumed to create a new queue without repeating successful judge calls.
  Historical queues and decisions are preserved and are not transferred.
- Canonical approval, extraction values and PostgreSQL storage are unchanged.
