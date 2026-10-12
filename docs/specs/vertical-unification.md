# Unified insurance workflow

Status: implemented and verified offline on 2026-10-12. Human schema approvals,
live provider/PDF trials and real PostgreSQL migration remain separate actions.

Health, Pet, Travel and Car use the existing discovery, consensus/review,
extraction and quality owners. Each manifest supplies its prompts, taxonomy,
business checks, source inventory and paths. No second engine, provider layer,
sampler or dependency is introduced.

## Confirmed boundaries

- All verticals use `product_type`; Pet values describe cover scope.
- All new extraction results use a `products` array, including one-product
  Health results. The selected extraction group may contain several products.
- Nested lists use Car's explicit `item_schema`, with bounded local `$defs`.
  Explicit historical `item_fields` can be converted without guessing keys.
- Discovery and patches must dynamically generate insurance fields; users do
  not manually maintain their nested schemas. Generation uses a fixed strict
  descriptor whose dynamic names are values, compiled to `item_schema` locally.
  It retains complete local validation and at most two repairs. New extraction
  uses closed strict contracts. Old schemas are not fallback options for new
  runs. Historical artifacts are preserved; approvals are never edited in place.
- The unified branch removes labelled evaluation and its CLI/UI entry points.
  The historical Health branch retains that implementation and its artifacts.
- One PDF can contain several products. Multiple PDFs are combined only when
  the inventory explicitly links them with `amends_document_ids`. Product IDs,
  names, brands and family hints do not create automatic links.
- The inventory is part of vertical configuration. Source documents are not
  moved or modified. Evidence and resumability bind every grouped source.
- Refinement uses structural, business and source-evidence checks. Judge audits
  the final extraction result against its complete source group, using the
  human-approved schema as the trusted field definition; it does not re-review
  that schema. Approval is a human action. Ordinary CSV delivery requires a
  verified judge pass or an explicit human delivery approval bound to the exact
  extraction, approved schema and complete PDF group. Judge never rewrites extraction.
- The existing CSV export is included through the integration branch ancestry
  and is extended to all four verticals with approved Canonical mappings. CSV
  support is independent of PostgreSQL support. Travel PostgreSQL loading is
  extended to complete document groups, with release identity bound to every
  PDF hash and amendment link, and atomic document/release links. Other verticals
  remain without database capability. Existing databases require the reviewed
  migration in `docs/migrations/001-grouped-release-identity.sql`.
- Prepare a new Car candidate with explicit `source_document_id` in evidence
  for human review. Old Car schemas are not the new workflow's extraction path.
- Car's complete v5/v6 layout is a candidate/reference, not a mandatory 37-field
  standard. Dynamic schemas may add/remove/adapt fields. Only supported declared
  structures get domain checks, recorded as applied/not applicable. Compatible
  single-PDF profiles retain source completeness and text-inference checks;
  amendment groups skip those checks and judge effective final terms against
  all PDFs. Qualified reference membership and applicable structural/business
  rules still run. No per-clause supersession fields are introduced.

## Integration sources

The integration starts from the CSV branch at
`b9cf38b3ad71b9003796c01358203b4a4bf9b9d8`, including its five commits after
main `8c493ac0497a8a9d92f4ce7506f9df06c82f4b6c`.
Pet behavior is adapted from `24473a2a1bc562f1d39db8aaccbeba2a6e8f25ea`;
Car schema, validation and parser behavior from
`571fc65a849dcb708a98da605c0103685efcf251`.
These are selective adaptations to current owners, rather than importing old
workflow copies. Historical Health labelled changes are excluded.

## Verification

Offline tests must cover nested schema propagation through review, explicit
amendment grouping, complete provenance and cache invalidation, per-vertical
business rules, approved-only judge and CSV source preservation. Compile, CLI
help and diff checks are required. Synthetic tests establish behavior; they do
not establish live provider accuracy, database loading or parsing quality on
real PDFs. Live runs, source changes and approvals require user authorization.

Final offline verification: 627 tests ran successfully (626 passed; one live PostgreSQL test skipped)
(`KONKRD_TEST_DATABASE_URL` unset); compileall, both CLI help commands and
`git diff --check` passed. GitNexus identifies broad extraction, refinement,
quality, UI and storage impact; source and tests establish correctness because
its flow graph is truncated and MCP staleness metadata may lag the refreshed index.
No provider call, PDF upload/download, real database operation or schema approval
was performed.
