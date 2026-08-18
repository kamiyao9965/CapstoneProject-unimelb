# Implementation Plan: Approved Canonical Schema Compiler

## Overview

Introduce an additive Canonical Schema boundary that turns one human-approved
vertical contract into a runtime extraction JSON Schema, generated vertical
SQLAlchemy metadata, and a deterministic load plan. Preserve current discovery,
extraction, fixed core tables, and the legacy Travel mapping during migration.

## Architecture decisions

- Only explicitly approved Canonical Schemas can compile downstream artifacts.
- Operational and cross-vertical identity tables remain fixed application code.
- One generated product-release extension table represents vertical-specific
  queryable fields in the first slice.
- Open nested structures remain losslessly available in JSONB.
- Current discovered-schema consumers stay backward compatible.

## Task list

### Phase 1: Contract and lifecycle

- [x] Define and allowlist the Canonical Schema JSON contract.
- [x] Add semantic validation for approval, identifiers, bindings, and storage
      combinations.
- [x] Prove candidate schemas cannot compile downstream outputs.

### Checkpoint: Contract

- [x] Focused lifecycle and validation tests pass.
- [x] Existing contract catalog behavior remains compatible.

### Phase 2: Generated extraction interface

- [x] Compile an approved schema into a strict products-array JSON Schema.
- [x] Preserve field requiredness, enum values, null policy, and open JSONB
      fields without provider calls.

### Checkpoint: Extraction

- [x] Generated extraction contract accepts valid fixtures and rejects invalid
      values.
- [x] Existing discovered-schema extraction tests remain green.

### Phase 3: Generated storage interface

- [x] Compile the approved schema into vertical SQLAlchemy metadata linked to
      `product_releases`.
- [x] Generate deterministic core bindings and extension records from validated
      extraction payloads.
- [x] Preserve JSONB-designated fields in extension attributes.

### Checkpoint: Storage

- [x] SQLite table creation and PostgreSQL DDL compilation pass.
- [x] Repeated load-plan compilation produces identical records.

### Phase 4: Integration and documentation

- [ ] Add a reviewed-schema compile CLI that writes generated contracts without
      overwriting existing files.
- [ ] Document the human approval and schema-version workflow.
- [ ] Run compileall, full offline tests, CLI help, diff review, and secret scan.

## Risks and mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Candidate schema reaches DDL | Unreviewed structure becomes persistent | Require approved state and review record at every compiler entry point |
| Existing extraction breaks | Current vertical workflows regress | Additive contract and adapters; retain discovered-schema compiler |
| Arbitrary SQL identifiers | Unsafe or invalid DDL | Strict snake_case validation and allowlisted core bindings |
| Nested objects imply unstable tables | Premature relational model | JSONB-only strategy for `list[object]` in the first slice |
| Two mapping authorities persist | Configuration drift | Mark legacy mapping compatibility-only and add parity coverage before removal |

## Open questions

None for this implementation slice. A review UI and nested child-table
generation require separate approval.
