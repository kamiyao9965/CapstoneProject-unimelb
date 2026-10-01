# Travel Schema Consensus and Canonical Review

## Outcome

Travel insurance follows the same evidence-driven schema refinement principle as
private health while keeping the human workload small:

```text
PDS sample -> one discovered schema -> five independent patch runs
  -> normalize + vote -> auto-apply safe 4/5 or 5/5 proposals
  -> human review of uncertain/high-risk proposals only
  -> reviewed discovered schema -> deterministic Canonical Schema candidate
  -> human mapping approval -> approved schema for extraction and PostgreSQL
```

## Functional requirements

1. Travel refinement is selected through its vertical manifest and defaults to
   five patch-voting runs.
2. Each patch run uses a different reproducible sampling seed.
3. A proposal is auto-applied only when it has at least 80% support and has no
   reject vote, mixed patch semantics, rename, merge, move, or invalid field
   shape.
4. The review queue contains only proposals that were not safely auto-applied.
   The complete frequency and stability artifacts remain available for audit.
5. Travel product types and Travel discovered-schema validation are used at
   every patch, render, review, and apply boundary.
6. The reviewed Travel discovered schema can be converted without an LLM call
   into a Canonical Schema candidate. `product_name` and `product_type` use the
   fixed core identity bindings; known queryable scalar fields use deterministic
   extension columns; other fields use JSONB.
7. Candidate storage metadata may be previewed, but extraction compilation,
   database initialization, and loading continue to require an approved schema.
8. Approval requires an explicit reviewer, rationale, and user action. Approval
   creates a new output artifact and never overwrites the discovered schema.

## Non-goals

- No live provider or database call is part of offline verification.
- No automatic approval of identity, rename/merge/move, type-conflict, or
  storage-policy decisions.
- No labelled Travel evaluation dataset is introduced in this increment.
- No new dependency is added.

## Acceptance criteria

- The Travel manifest enables consensus refinement and declares its contracts,
  prompt, validator, and aliases.
- A five-run 4/5 proposal is safely promoted; 3/5 and unsafe proposals remain
  in the UI queue.
- Travel enum values pass through the shared refinement artifact contracts.
- The reviewed schema produces a valid Canonical Schema candidate with stable
  database mapping.
- Candidate DDL preview works without weakening the approval gate used by
  production compilation and storage.
- The offline unit suite and CLI help checks pass.
