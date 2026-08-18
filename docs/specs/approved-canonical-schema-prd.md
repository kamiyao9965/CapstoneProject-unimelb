# Approved Canonical Schema PRD

**Status:** Approved for implementation

**Date:** 2026-08-18

**Initial vertical:** Australian travel insurance

## Objective

Make one human-approved Canonical Schema the authoritative business-data
contract for each vertical. The approved contract must drive:

1. the JSON Schema used to validate model extraction output;
2. the vertical relational table metadata used by PostgreSQL; and
3. the deterministic plan used to load validated JSON into those tables.

This removes the need to maintain an independent hand-written business mapping
beside the extraction schema. It does not allow unreviewed model output to
create or alter database objects.

## Workflow

```text
LLM discovered schema
  -> candidate Canonical Schema
  -> local contract and business validation
  -> human review and explicit approval
  -> immutable approved Canonical Schema version
       -> extraction JSON Schema
       -> vertical SQLAlchemy metadata
       -> deterministic load plan
```

Candidate and approved schemas are different lifecycle states. Compilers must
reject candidate schemas. Changing an approved schema produces a new version;
it never mutates the previous version in place.

## Authority boundaries

### Fixed application-owned tables

Operational provenance and shared identity tables remain deterministic
application code:

- `verticals`
- `insurers`
- `documents`
- `schema_versions`
- `extraction_runs`
- `raw_extractions`
- `products`
- `product_releases`
- `product_release_documents`

They contain system-generated identifiers, hashes, foreign keys, model-run
metadata, and source-document relationships that are not extracted from PDF
content.

### Canonical-Schema-owned business fields

The approved schema owns the vertical-specific business fields and their
storage decisions. A field may:

- bind to an allowlisted shared core column;
- become a typed column in a generated product-release extension table; or
- remain in the extension table's `JSONB` attributes column.

The initial implementation supports one generated extension entity per
vertical, keyed one-to-one by `product_releases.release_id`. Nested child
tables remain out of scope until nested item contracts become closed.

## Canonical Schema contract

Every Canonical Schema declares:

- `contract_version`, `vertical`, and immutable `version`;
- `status` (`candidate` or `approved`);
- an approval record for approved schemas;
- output collection and cardinality;
- one product-name identity field;
- typed extraction fields and enum values;
- one extension entity and physical table name; and
- a storage strategy for every field.

Approved schemas require a non-empty reviewer, ISO-8601 review timestamp, and
rationale. Candidate schemas must not carry a false approval record.

Supported field types in this slice are `string`, `number`, `boolean`, `enum`,
and `list[object]`. Storage rules are:

| Field type | Column SQL type | JSONB behavior |
| --- | --- | --- |
| `string` | `TEXT` | original value retained in raw artifact |
| `number` | `NUMERIC` | original value retained in raw artifact |
| `boolean` | `BOOLEAN` | original value retained in raw artifact |
| `enum` | `TEXT` plus generated check constraint | original value retained in raw artifact |
| `list[object]` | not supported as a scalar column | must use `jsonb` strategy |

Physical table and column identifiers must be canonical `snake_case` and are
validated before SQLAlchemy objects are created. Core bindings are allowlisted;
the schema cannot name arbitrary operational columns.

## Human-in-the-loop behavior

The human reviewer decides:

- field meaning and canonical name;
- type, requiredness, nullability, and enum values;
- product applicability;
- identity-field selection;
- whether a field is queryable as a relational column or retained in JSONB;
- extension-table and column names; and
- the rationale for approval.

Automation validates and compiles those decisions. It does not infer missing
approval or silently repair invalid storage annotations.

## Generated interfaces

The compiler exposes three explicit, side-effect-free operations:

```python
extraction_contract = compile_canonical_extraction_contract(schema)
vertical_metadata = compile_vertical_storage_metadata(schema)
load_plan = compile_canonical_load_plan(schema, extraction_payload)
```

Each operation validates the approved Canonical Schema at its boundary. The
load-plan compiler also validates the extraction payload against the generated
extraction contract before producing records.

## Backward compatibility and migration

- Existing discovered schemas continue to work for current discovery and
  extraction commands.
- Existing fixed storage metadata remains available while generated extension
  metadata is introduced additively.
- The hand-written Travel `storage_mapping.json` remains temporarily as a
  compatibility artifact, but no new business rule should be added to it.
- Removal of the compatibility mapping requires parity tests and a separate
  reviewed change.
- No existing production schema or extraction artifact is overwritten.

## Testing strategy

- Contract tests reject missing approval, invalid identifiers, unsupported
  storage combinations, duplicate columns, and unsafe core bindings.
- Compiler tests prove one approved schema produces a closed extraction
  contract and the expected SQLAlchemy extension table.
- PostgreSQL dialect tests prove generated flexible fields use `JSONB` and enum
  fields generate check constraints.
- Load-plan tests prove the same validated input produces identical records and
  unmapped/open fields are preserved in attributes.
- Compatibility tests prove existing Travel discovery/extraction contracts
  still compile unchanged.

## Boundaries

### Always

- Require explicit human approval before storage compilation.
- Validate identifiers and core bindings before creating SQLAlchemy metadata.
- Preserve the full validated extraction artifact in `raw_extractions`.
- Version approved schemas and generated database changes.

### Ask first

- Adding child-table generation for nested objects.
- Applying destructive database migrations.
- Removing legacy discovered-schema or mapping compatibility.

### Never

- Execute DDL from unreviewed LLM output.
- Treat a reviewer name alone as evidence that an invalid schema is safe.
- Drop or rename an existing column automatically.
- Silently discard fields assigned to JSONB storage.

## Success criteria

- A candidate Canonical Schema cannot compile extraction or storage outputs.
- An approved Travel fixture compiles into one strict products-array extraction
  contract.
- The same fixture compiles a one-to-one Travel extension table with typed
  columns, enum constraints, and JSONB attributes.
- A validated extraction payload compiles into deterministic core bindings and
  extension records without an LLM call.
- Existing offline tests remain green.
- No live PostgreSQL or OpenAI call is required for this implementation slice.

## Out of scope

- Automatically approving a discovered schema.
- A new review UI for storage annotations.
- Automatic execution of Alembic or destructive migrations.
- Relational generation for open-ended nested benefit objects.
- Live PostgreSQL provisioning.
