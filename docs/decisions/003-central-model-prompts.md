# ADR-003: Keep model-facing prompt text in one directory

## Status

Accepted

## Date

2026-09-30

## Context

Health and Travel share model-call machinery, but their system prompts were
stored inside separate `configs/<vertical>/prompts/` directories. Request,
feedback, repair, and provider-format instructions were also embedded in
Python strings. Reviewing the complete text sent to models required searching
across the codebase.

## Decision

Store per-vertical system prompts and shared text templates under the top-level
`prompts/` directory. Manifests continue to select the per-vertical files.
`src/verticals/` remains the sole owner of prompt loading and enforces that a
manifest can read its own central directory or its package-local directory,
but cannot traverse into another vertical or an unrelated path. Shared
templates use a filename-restricted loader. Runtime PDF/schema/value payloads
and deterministic JSON validation stay in code and contracts.

## Consequences

- The seven moved system-prompt files retain identical bytes and the existing
  Travel judge system-prompt hash.
- New Travel quality reports also record an optional bundle fingerprint of
  their shared request, repair, and provider-format templates. Legacy reports
  can be resumed only if those templates retain their migration-time text.
- A copied configuration package that uses the built-in central paths must
  also copy the sibling `prompts/<vertical>/` directory. Package-local prompt
  paths remain supported for custom packages.
- Prompt-template placeholders are part of the code/template interface and
  are covered by offline tests. Any prompt edit requires a new model-run output
  directory for a fair comparison; a quality-audit resume is not a prompt A/B
  run.
