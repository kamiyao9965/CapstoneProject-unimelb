# Implementation Plan: Relational Storage Vertical Slice

## Overview

Add an opt-in PostgreSQL storage stage after validated extraction. The slice
will preserve raw JSON, normalise core product/release records, add Travel
dimensions, and prove idempotent transactional loads without changing existing
discovery or extraction behavior.

## Architecture decisions

- PostgreSQL is the production database; SQLite is test-only.
- SQLAlchemy Core owns schema and transactions; Psycopg 3 is the driver.
- Storage is additive and enabled per vertical manifest.
- Raw extraction is always retained in JSON before relational mapping.
- Database DDL is deterministic and never generated directly by the LLM.

## Task list

### Phase 1: Contracts and dependencies

- [ ] Record the storage PRD, ADR, approved dependencies, and mapping contract.
- [ ] Add manifest capability/path validation for opt-in storage.

### Checkpoint: Contracts

- [ ] Mapping and manifest contract tests pass.
- [ ] Existing manifest defaults remain backward compatible.

### Phase 2: Storage foundation

- [ ] Define core and Travel SQLAlchemy tables with PostgreSQL JSONB variants.
- [ ] Add deterministic schema creation and database URL validation.
- [ ] Prove table creation on SQLite and DDL compilation for PostgreSQL.

### Checkpoint: Schema

- [ ] Primary keys, foreign keys, unique constraints, and JSONB compile as
      specified.

### Phase 3: End-to-end loading

- [ ] Map one validated Travel artifact into deterministic storage records.
- [ ] Persist raw and relational records in one transaction with idempotent
      upserts.
- [ ] Add `storage-init` and `storage-load` CLI commands.

### Checkpoint: Complete

- [ ] Loading the same artifact twice leaves row counts unchanged.
- [ ] An invalid artifact leaves all tables unchanged.
- [ ] Full offline suite, compileall, and CLI help pass.
- [ ] README and project ownership documentation are current.

## Risks and mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| No local PostgreSQL server | PostgreSQL runtime cannot be exercised locally | SQLite end-to-end tests plus PostgreSQL dialect compilation; report live PG as unverified |
| Open-ended benefit objects | Premature relational tables would be unstable | Preserve in JSONB until a closed nested contract is approved |
| Schema discovery drift | Dynamic DDL could corrupt storage | Fixed canonical schema and versioned mapping boundary |
| Duplicate loads | Duplicate products/releases | Deterministic IDs, unique constraints, and conflict-safe inserts |
| Partial writes | Inconsistent product graph | One transaction per artifact |

## Open questions

None for this slice. Cloud provisioning and relational benefit modelling remain
explicitly out of scope.
