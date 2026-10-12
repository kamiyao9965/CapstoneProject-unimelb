# Unified insurance workflow

Status: implementing. User decisions confirmed on 2026-10-12.

Health, Pet, Travel and Car use the existing discovery, consensus/review,
extraction and quality owners. Each manifest supplies its prompts, taxonomy,
business checks, source inventory and paths. No second engine, provider layer,
sampler or dependency is introduced.

## Confirmed boundaries

- All verticals use `product_type`; Pet values describe cover scope.
- Nested lists use Car's explicit `item_schema`, with bounded local `$defs`.
  Explicit historical `item_fields` can be converted without guessing keys.
- Discovery and patch requests use non-strict schema-generation contracts,
  with complete local validation and at most two repairs. New extraction uses
  closed strict contracts. Open historical schemas remain readable, but cannot
  start new extraction. Approved schemas are never modified in place.
- The unified branch removes labelled evaluation and its CLI/UI entry points.
  The historical Health branch retains that implementation and its artifacts.
- One PDF can contain several products. Multiple PDFs are combined only when
  the inventory explicitly links them with `amends_document_ids`. Product IDs,
  names, brands and family hints do not create automatic links.
- The inventory is part of vertical configuration. Source documents are not
  moved or modified. Evidence and resumability bind every grouped source.
- Refinement uses structural, business and source-evidence checks. Judge runs
  only against a human-approved Canonical Schema. Approval is a human action.
- The existing CSV export is included through the integration branch ancestry;
  it retains the approved Canonical/storage gates.

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
