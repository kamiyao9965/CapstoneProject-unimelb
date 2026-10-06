# ADR-006: Export a joined product CSV from validated extraction artifacts

## Status

Accepted; product scope confirmed by the user.

## Date

2026-10-07

## Context

The team and customers compare insurance products in Excel. They need an output
parallel to PostgreSQL without configuring a database. The user chose one joined
product CSV rather than one CSV per relational table.

## Decision

Implement a local product view in `src/storage/csv_export.py`. Read the same
extraction artifacts and approved Canonical Schema as PostgreSQL loading. Reuse
storage preparation, Canonical load plans and deterministic ID helpers. One row
joins a product release's core values, typed detail columns, JSON attributes and
source/run provenance. Directory discovery and source-duplicate checks have one
shared owner in `src/storage/service.py`.

Expose single-artifact and directory commands and console forms. Validate the
complete export before atomically creating a new UTF-8 BOM CSV; reject existing
destinations, invalid inputs, duplicate releases and formula-like text. Preserve
source values instead of adding inferred values or silent spreadsheet escaping.

## Consequences

- Portable output uses the same business mapping and IDs without DB/model calls
  or additional dependencies. PostgreSQL remains the relational storage system.
- Storage capability and human-approved mappings gate both outputs; Travel is
  available first. Private Health mapping design remains separate work.
- Nested details stay JSON, so Excel filters primarily operate on typed columns.
  Scalar null and empty string share an empty CSV cell. The export is a selected
  artifact view, not a lossless database backup or historical snapshot.
- CSV directory failures produce no partial file; PostgreSQL directory loading
  retains its existing independent per-artifact transaction behavior.
- Database-generated timestamps are omitted. All existing extraction formats,
  schemas, source files and PostgreSQL operations keep their current contracts.
