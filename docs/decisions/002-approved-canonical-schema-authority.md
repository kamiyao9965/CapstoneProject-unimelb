# ADR-002: Use an approved Canonical Schema as the business-data authority

## Status

Accepted

## Date

2026-08-18

## Context

ADR-001 established PostgreSQL relational tables with JSONB staging and
rejected DDL generated directly from an unreviewed discovered schema. The
initial storage design still required a separately maintained Travel mapping
and hand-written Travel extension table. That duplicates business decisions
and makes each new vertical more expensive to migrate.

The project already has a human-review boundary and compiles discovered schema
fields into runtime extraction contracts. It needs one reviewed source of truth
that can also describe queryable vertical storage fields.

## Decision

Promote a human-reviewed Canonical Schema to the authoritative business-data
contract for a vertical. Only schemas in the explicit `approved` lifecycle
state, with a valid review record, may compile extraction contracts, vertical
SQLAlchemy metadata, or deterministic load plans.

Keep operational provenance and shared identity tables in application code.
Generate vertical product-release extension metadata from approved storage
annotations. Preserve open or unmapped structures in PostgreSQL `JSONB`.

Treat the compiler as the mapping implementation. A generated load plan may
perform allowlisted core bindings and mechanical JSON-to-row conversion, but it
must not perform model inference.

This decision refines, rather than reverses, ADR-001: model output still cannot
directly execute DDL, and PostgreSQL plus JSONB remains the storage choice.

## Alternatives considered

### Keep a separate hand-written mapping per vertical

Rejected as the target architecture because extraction fields, mapping rules,
and database columns can drift independently. It remains temporarily for
backward compatibility during migration.

### Generate every database table from the approved schema

Rejected because document hashes, run provenance, schema identities, and
foreign-key lifecycle are application concerns rather than PDF-derived business
facts.

### Store approved extraction output only in JSONB

Rejected because stable business dimensions need typed columns, constraints,
joins, and predictable reporting.

## Consequences

- Human review becomes a release gate for both extraction and relational
  business structure.
- New verticals primarily add a reviewed Canonical Schema instead of new
  mapping code.
- Canonical Schema evolution requires additive database migration planning.
- The compiler and contract become high-value public interfaces and require
  compatibility tests.
- Nested, open-ended structures remain JSONB until their item contracts are
  explicitly reviewed and closed.
