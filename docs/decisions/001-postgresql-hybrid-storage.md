# ADR-001: Use PostgreSQL relational tables with JSONB staging

## Status

Accepted

## Date

2026-08-18

## Context

The pipeline produces strictly validated JSON extraction artifacts, while the
target product needs stable joins, constraints, versioning, and idempotent
insertion across insurance verticals. Discovered fields can change during
refinement, and some extracted structures such as benefits are intentionally
open-ended.

## Decision

Use PostgreSQL as the single production database. Store stable cross-vertical
entities in relational tables and preserve original or long-tail extraction
data in `JSONB` columns.

The database schema is deterministic application code. Unreviewed LLM output
may discover extraction fields but cannot create or alter database objects.
Travel insurance is the first storage-enabled vertical and uses a versioned
mapping between its extraction contract and the canonical storage model.

Use SQLAlchemy Core as the database interface and Psycopg 3 as the PostgreSQL
driver. Storage commands and storage integration tests target PostgreSQL
directly; SQLite is not a supported substitute for the storage path.

## Alternatives considered

### PostgreSQL plus a separate document database

Rejected for the current scale. It adds synchronisation, consistency, backup,
and operational boundaries without providing a required capability that
PostgreSQL `JSONB` lacks.

### Pure relational schema with no raw JSON

Rejected because open-ended extraction fields would be lost or would require a
database migration before every schema experiment.

### Store every value only as JSON

Rejected because core product and release data needs primary keys, foreign
keys, uniqueness constraints, joins, and predictable reporting.

### Generate DDL directly from discovered schema

Rejected because model-generated schemas are candidate extraction contracts,
not trusted database migrations. Discovery drift could otherwise create
destructive or incompatible storage changes.

## Consequences

- One database provides relational integrity and flexible raw storage.
- Important query fields must be deliberately promoted into canonical tables.
- Every new vertical needs a mapping contract and tests before storage is
  enabled.
- SQLAlchemy and Psycopg become runtime dependencies.
- Offline tests cover validation, deterministic mapping, PostgreSQL SQL
  compilation, and transaction orchestration without substituting another
  database dialect. A live PostgreSQL smoke test verifies DDL, idempotency, and
  rollback behavior end to end.
