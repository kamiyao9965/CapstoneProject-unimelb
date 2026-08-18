# Dependency Policy

Dependencies require user approval, one owning module, and a documented reason
that the standard library or an existing dependency is insufficient.

## Existing approved dependencies

The existing pipeline dependencies remain listed in `requirements.txt` and are
owned by their current model-provider, PDF ingestion, validation, review,
normalisation, and evaluation modules.

## Relational storage dependencies

- `SQLAlchemy>=2.0,<3`
  - Owner: `src/storage/`.
  - Reason: defines one parameterised schema and transaction boundary that
    targets PostgreSQL in production and SQLite for offline integration tests.
    Hand-maintaining two SQL implementations would duplicate constraints and
    weaken test confidence.
  - Approval: user approved database dependencies on 2026-08-18.

- `psycopg[binary]>=3.2,<4`
  - Owner: PostgreSQL connection through `src/storage/`.
  - Reason: SQLAlchemy requires a DBAPI driver to connect to PostgreSQL. The
    binary extra supplies the client libraries for supported local platforms
    and avoids a system `libpq` build prerequisite.
  - Approval: user approved database dependencies on 2026-08-18.

## Rules

- Keep provider SDKs out of storage modules and database clients out of model
  request modules.
- Do not add a second ORM, migration framework, or document database without
  explicit approval.
- Update `requirements.txt` and this file together.
- Do not commit database URLs, passwords, dumps, or generated local databases.
