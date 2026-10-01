# ADR-004: Separate source, configuration, data, and runtime results

## Status

Accepted by the user on 2026-10-01.

## Context

Source files already lived under `src/`, but presentation entry points were
scattered, the PDF package used mixed-case naming, and the API reference was at
the root. Parsed Markdown and caches shared `outputs/` with schemas, runs, SQL,
CSV, and archives. Health's default manifest pointed at a missing legacy data
root even though local PDFs were present. Several maintained and historical
documents contained Chinese prose.

## Decision

Use the [project layout](../project-layout.md): functional source packages;
`src/ui/` for presentation; `src/pdf_ingestion/` for both parser routes;
`configs/<vertical>/` for manifests and their related configuration; English
project documentation in `docs/`; format-first PDF/Markdown data; parser caches
in `.cache/`; and vertical-first outputs grouped by purpose.

Keep prompts, JSON contract ownership, extraction algorithms, and Canonical
approval rules unchanged. Manifests own input, Markdown, cache, and output paths.
External Health data uses an explicit data-root override; no implicit search of
parent directories remains. Use new paths exclusively and update references.

Preserve all historical data and output bytes, record a checksum-based migration
inventory, and keep run-bound artifacts together. Rebind only the in-memory
location metadata of a reused parser cache to the currently requested PDF.
Historical results and approved schema contents are never rewritten.

## Alternatives

Documentation-only cleanup would not prevent future mixed outputs. A universal
format-first tree would separate related run artifacts. A universal vertical-first
tree would hide data formats. A per-run tree for all files would duplicate source
data and reusable caches. The selected mixed layout follows how collaborators
look for inputs and investigate results.

## Consequences

Commands and Python imports use new paths. Custom manifests must define
`markdown_root` and `cache_root` as well as `input_root` and `output_root`.
External Health data must use the documented layout. Historical provenance may
refer to former locations; use the inventory for inspection. Resuming or reloading
relocated historical runs requires a separate identity review and is outside
this migration. No model, network-download, or PostgreSQL operation is needed
for verification.
