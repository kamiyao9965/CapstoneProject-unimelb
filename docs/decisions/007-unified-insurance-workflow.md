# ADR-007: One workflow for Health, Pet, Travel and Car

Status: accepted. User decisions reconfirmed on 2026-10-12.

## Problem and decision

Separate vertical branches duplicated workflow code and expressed nested fields,
source grouping and quality checks differently. Adapt their domain behavior to
the existing owners instead of merging competing engines. All four verticals
share discovery, patch consensus, human review, extraction/batch, final-result
judge and CSV. Manifests retain prompts, paths, inventory, classification,
taxonomies, identities, consensus policy and allowlisted domain adapters.

All new results contain `products`, including one-product Health results.
Classification is `product_type`; Pet's values retain cover-scope meaning.
Labelled evaluation, its modules and CLI/UI entry points are excluded from the
unified main. Historical Health branches and artifacts are preserved.

## Strict dynamic schemas

Closed `item_schema`, bounded local `$defs`, and declared `document_fields` retain
Car's nested expression. Discovery and patches use a fixed strict descriptor:
dynamic property names are values in a node graph, compiled locally to native
schemas. This supports adaptation without operators manually maintaining fields.
Descriptor, native and domain validation share one initial request plus at most
two repairs. Product extraction always uses a closed strict contract. Old open
schemas stop before new model requests; no non-strict fallback is introduced.
Strict workflows require JSON Schema-capable models.

Car's complete v5/v6 layout is this round's starting candidate, not a mandatory
37-field standard. Dynamic schemas can add, remove or change business structures.
Supported declared structures receive applicable checks, with applied and
not-applicable rules recorded explicitly. Common identity, missingness, closed
structure and document-qualified source validation remain. Unsupported dynamic
structures cannot claim to have passed the full historical Car semantic suite.

## Document groups and effective terms

Only inventory `amends_document_ids` links automatically combine PDFs. Names,
brands, product IDs and family hints do not. Parents precede amendments; paths,
hashes and links are validated. Unlinked SPDS/Update requires review. A PDF can
contain multiple products; a linked group can contain multiple PDFs/products.
Every source receives a document ID, per-document page/block indexes and a
receipt. Resume and holdout exclusion bind the complete group.

Full compatible single-PDF Car extraction retains source completeness/text
checks. In amendment groups those checks could wrongly require superseded base
clauses, because no per-clause replacement ledger exists. Therefore multi-PDF
groups retain reference membership and applicable structural/business checks,
record completeness/text checks as not applicable, and defer effective final
terms to judge. No clause-level supersession generation is added this round.

## Approval, judge and delivery

Canonical approval remains an explicit human action with reviewer and rationale;
approved files are never changed in place. A new unapproved Car v6 candidate
adds `source_document_id` to evidence. All verticals support the review page;
that capability does not enable their database storage.

Judge runs only after Canonical approval. It trusts approved definitions and
checks final extraction usability against all original source PDFs, including
amendments, rather than reviewing schema design. Judge does not rewrite values.
CSV delivery requires a current judge pass or explicit human delivery approval
bound to exact artifact/schema bytes and the complete current source group.
Changed inputs invalidate proof; a review queue alone is not delivery approval.

CSV capability is independent of PostgreSQL. All four export the same product
view with nested attributes, document fields, every source receipt and delivery
proof. Output is atomic and refuses overwrite; one invalid input prevents a
partial directory CSV. No database or model is called during export.

## Travel PostgreSQL

Travel alone keeps database capability. Grouped loads store every document and
release-document link in one transaction. Release identity incorporates the
ordered aliases/hashes/amendment links, so changing an SPDS creates a new release
even when the base PDS is unchanged. Single-PDF release IDs remain unchanged.
Existing databases need `docs/migrations/001-grouped-release-identity.sql`;
table initialization does not perform migration. The migration and live database
validation require separate authorization.

## Verification and limits

Offline fake-provider, parser, schema, grouped extraction/resume, judge/delivery,
CSV and transaction tests cover the new contracts and failure boundaries. They
do not prove extraction accuracy, real-PDF parsing quality, live provider behavior
or real PostgreSQL migration. Raw data, credentials, historical output and schema
approvals remain outside this implementation's automated actions. No dependency
or second provider abstraction is introduced.
