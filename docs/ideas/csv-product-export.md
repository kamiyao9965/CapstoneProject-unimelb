# Product CSV export

Status: confirmed by the user on 2026-10-07; implementation in progress.

## Problem statement

The team and customers need to view, filter and compare extracted insurance
products in Excel without configuring PostgreSQL. The CSV should use the same
validated business records as PostgreSQL, with related product tables combined.

## Recommended direction

Add a local export under `src/storage/` that reads existing extraction artifacts
and an approved Canonical Schema. Reuse storage preparation, Canonical mapping,
deterministic product/release IDs and the existing atomic text writer. Join
product, release, vertical detail and source/provenance values into one CSV.

Alternatives considered: one CSV per PostgreSQL table preserves normalization
but requires users to join tables; a benefit-per-row CSV changes the selected
product-version grain; reading from PostgreSQL requires a database; exporting
during model extraction couples output to paid runs; XLSX adds a different file
format. The user selected a joined product CSV from existing extraction results.

## Key assumptions

- One row represents one product release in one source PDF; product and release
  IDs have the same definitions as PostgreSQL.
- Typed detail columns retain their PostgreSQL names. Flexible/nested values
  remain JSON in the schema's attributes column.
- The first version supports storage-enabled verticals with approved Canonical
  mappings; currently this is Travel. Private Health has no approved mapping.
- This exports selected extraction results, rather than a snapshot of database
  history. Database-generated timestamps are not invented.

## MVP scope

- Single-artifact and directory export through CLI and the operator console.
- The same approval, schema identity, product identity and PDF source checks as
  PostgreSQL preparation, without creating a database connection or model call.
- A deterministic column order, UTF-8 BOM for Excel, CSV escaping, JSON booleans
  and numbers, and empty cells for SQL-null scalar values.
- Directory export shares PostgreSQL's source discovery, skips `errors/`, and
  rejects competing results for the same source PDF. Validate every input before
  writing; any invalid input fails the export without a partial output.
- Atomic output creation that refuses to overwrite an existing file. Formula-like
  text cells fail validation instead of changing source values silently.
- Offline tests with synthetic PDFs and artifacts; CLI/UI regression checks.

## Not doing

Database snapshot export, model re-extraction, automatic CSV output during batch
extraction, Private Health mapping design, XLSX, benefit expansion, flattening
nested JSON, custom customer summaries, or changes to PostgreSQL writes.

## Open questions

None blocking the confirmed MVP. Later work can consider a separate Excel view
that expands benefits or a human-approved Private Health Canonical mapping.
