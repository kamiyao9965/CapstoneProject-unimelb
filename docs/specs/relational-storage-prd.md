# Relational Storage PRD

**Status:** Approved for implementation

**Date:** 2026-08-18

**Initial vertical:** Australian travel insurance

**Target branch:** `feat/travel-insurance`

## Objective

Extend the validated JSON pipeline with an additive storage stage that can:

1. create a stable relational schema;
2. preserve each validated extraction artifact without information loss;
3. map stable business entities into constrained relational tables;
4. load the same artifact idempotently inside one database transaction; and
5. reuse the core storage model when another insurance vertical is added.

The storage schema is not generated directly from an unreviewed LLM response.
The existing discovered schema remains an extraction contract. A deterministic
storage model and versioned mapping decide how validated extraction data enters
the database.

## Approved assumptions

- PostgreSQL is the production database.
- PostgreSQL relational tables and `JSONB` columns are used in one database;
  no separate document database is introduced.
- Storage execution targets PostgreSQL directly. SQLite is not used as a
  substitute because its JSON, constraint, and conflict semantics differ.
- PDFs remain on disk or in object storage. The database stores document
  identity, hashes, paths, and provenance, not PDF bytes.
- Travel insurance is the first storage-enabled vertical. Private health keeps
  its current behavior until a private-health storage mapping is specified.
- The first slice normalises products and releases. Open-ended benefit objects
  remain in JSON until a closed nested benefit contract exists.
- `SQLAlchemy>=2,<3` and `psycopg[binary]>=3.2,<4` are approved dependencies.

## User-visible commands

Create all current storage tables without deleting existing data:

```bash
set -a
source .env
set +a
.venv/bin/python src/run.py storage-init \
  --manifest configs/travel_insurance/manifest.json
```

Load one validated extraction artifact:

```bash
.venv/bin/python src/run.py storage-load \
  --manifest configs/travel_insurance/manifest.json \
  --schema outputs/travel_insurance/schema.json \
  --artifact outputs/travel_insurance/extractions/example.json \
  --insurer-code cover_more
```

Both commands resolve the database URL from `KONKRD_DATABASE_URL`. A different
environment-variable name may be supplied through `--database-url-env`. The
URL value is never printed.

## Architecture

```text
validated extraction artifact
  -> dynamic extraction-contract validation
  -> immutable raw extraction JSON
  -> deterministic vertical mapper
  -> relational row set
  -> one transaction with idempotent upserts
```

### Package ownership

```text
src/storage/schema.py       fixed SQLAlchemy tables and schema creation
src/storage/canonical.py    approved schema -> table metadata and load plan
src/storage/repository.py   transactional, idempotent PostgreSQL persistence
src/storage/service.py      boundary validation and storage orchestration
configs/<vertical>/canonical_schema_v*.json
                             reviewed business fields and storage annotations
tests/test_storage_*.py      offline boundary and PostgreSQL SQL tests
```

`src/run.py` remains the public CLI entry point. Existing discovery and
extraction modules do not import the storage package.

## Canonical storage model

### Stable core tables

- `verticals`
- `insurers`
- `documents`
- `schema_versions`
- `extraction_runs`
- `raw_extractions`
- `products`
- `product_releases`
- `product_release_documents`

### Travel extension

- `travel_product_details`

The travel table stores queryable dimensions such as geographic scope, trip
frequency, plan tier, customer segment, and cruise indicator. Remaining
extracted attributes are retained in JSON.

### Identity and idempotency

- Document identity is `sha256:<content hash>`.
- Schema identity is the pair `(vertical, version)`.
- Extraction-run identity comes from the artifact provenance `run_id`.
- Product identity is deterministic from vertical, insurer, and canonical
  extracted product name.
- Release identity is deterministic from product and document identity.
- Loading the same artifact twice must not create additional rows.
- Loading another run for the same product and document may add a new raw
  extraction run while upserting the same product release.

## Mapping contract

Each approved Canonical Schema declares:

- collection path (`products` for multi-product Travel output);
- product name and product type source fields;
- supported product-type decomposition into independent Travel dimensions;
- fields retained as stable relational attributes.

The compiler is strict, deterministic, and versioned. Fields assigned the
`jsonb` strategy remain in `raw_extractions` and the vertical extension
table's `attributes` column.

## Validation and error behavior

- Only successful `extraction_result` envelopes are accepted.
- The extraction data is revalidated against the contract compiled from the
  supplied approved Canonical Schema.
- The artifact vertical and schema vertical must match the manifest.
- The source document must exist so its content hash can be verified.
- Missing product names, invalid collection cardinality, duplicate product
  identities within an artifact, or absent database configuration fail before
  any write.
- Database writes occur in one transaction. Any failed row rolls back the
  complete artifact load.
- Existing schema objects and rows are not deleted by either command.

## Code style

Storage boundaries accept explicit typed records and return a summary rather
than leaking SQLAlchemy rows:

```python
summary = load_extraction_artifact(
    database_url=database_url,
    manifest=manifest,
    schema_path=schema_path,
    artifact_path=artifact_path,
    insurer_code=insurer_code,
)
```

Field and table names use canonical `snake_case`. SQL statements are produced
through SQLAlchemy constructs; caller-provided values are never interpolated
into SQL strings.

## Testing strategy

- Pure unit tests validate deterministic IDs, mapping, taxonomy decomposition,
  and fail-closed inputs.
- Offline tests validate pure mapping, PostgreSQL statement compilation, and
  transaction orchestration without connecting to another database dialect.
- PostgreSQL dialect compilation tests assert that JSON columns compile to
  `JSONB` and required constraints are present.
- A live PostgreSQL smoke test uses `KONKRD_TEST_DATABASE_URL` to create tables,
  load the same artifact twice, inspect stable row counts, and test rollback.

## Boundaries

### Always

- Validate external artifacts at the storage boundary.
- Preserve raw extraction JSON before normalising it.
- Use transactions and deterministic identities.
- Keep existing pipeline commands backward compatible.

### Ask first

- Adding another database technology or another dependency.
- Destructive migrations or automatic table/column deletion.
- Enabling storage for another vertical without a mapping and tests.

### Never

- Execute DDL proposed directly by the LLM.
- Print or persist a database password outside the configured connection.
- Commit `.env`, extraction outputs, source PDFs, or database dumps.
- Silently discard unmapped fields.

## Success criteria

- `storage-init` creates the complete schema on an empty supported database.
- PostgreSQL uses `JSONB` for raw and flexible attributes.
- `storage-load` accepts a valid Travel extraction artifact and creates its
  core and Travel rows in one transaction.
- Repeating the same load produces identical row counts.
- Invalid artifacts write no rows.
- The complete offline regression suite continues to pass alongside the new
  storage tests.
- CLI help and README document the new opt-in commands.

## Out of scope for this slice

- Live PostgreSQL provisioning or cloud deployment.
- A REST API or user interface over stored products.
- Relational benefit, exclusion, eligibility, and evidence tables whose source
  JSON remains open-ended.
- Automatic schema evolution from discovered fields.
- Storage support for private health or car insurance.
