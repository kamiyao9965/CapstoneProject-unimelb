# Architecture

This project is a Python CLI for discovering, evaluating, and refining a
versioned JSON extraction contract for Australian Health, Pet, Travel and Car documents.
The same engine consumes a validated manifest plus discovery, patch and extraction
prompts per vertical; each supplies a post-extraction quality prompt.

## Runtime flows

### Local operator UI

```text
Streamlit controls -> validated allowlisted argv -> existing CLI entry point
  -> redacted console result
```

`src/verticals/manifest.py` owns package discovery, default selection and operation
capabilities. `src/ui/tool_app.py` selects vertical first and owns presentation and
result state; forms and results reset on context changes, and confirmation is
bound to command/configuration content. `src/ui/tool/forms.py`
owns operation-specific controls, while `src/ui/tool/commands.py` is the only
UI-to-process boundary. It validates supported operations and values, launches
an argument list without a shell, enforces timeouts, and redacts credential-like
console output. It does not accept arbitrary commands or credential values.
On macOS/Linux each command has its own process group. Timeout sends `SIGTERM`,
allows a one-second grace period, then uses `SIGKILL` for remaining group members
and reaps the CLI. Captured output remains redacted. Other platforms retain
the direct subprocess timeout behavior.

The UI contains no workflow logic. `src/run.py` and `src/refine/loop.py` remain
the authoritative parsers and orchestrators, so every UI action is reproducible
as the command preview shown on the page. The console is local-only and has no
authentication boundary.

The shared `quality-audit` operation is an explicit, post-extraction
screen. `src/evaluation/quality.py` validates the exact approved Canonical
Schema and extraction identity, resolves the complete document group within an
allowed source root, renders every PDF through `src/pdf_ingestion/adapter.py`, and sends those pages,
field definitions and values through the shared structured-output model
boundary. It writes immutable per-extraction-group reports, an atomically refreshed,
contract-validated `results.json` that embeds complete reports or safe typed
failure diagnostics, and an independent usage log. Existing reports are reused
only after artifact/schema/prompt-bundle/model/PDF identity checks; summary-only mode
rebuilds the JSON without provider or source-PDF access. A bounded number of
document failures leaves a partial JSON instead of discarding the batch. The
review queue is created only when every report is valid, so later resumes
cannot silently change a queue already carrying human decisions.
`src/schema/sampler.py` owns deterministic sampling of
judge passes. `src/evaluation/quality_review.py` binds human decisions to the
queue identity; `src/ui/quality_review.py` only presents and records those decisions.
Queue version `2.0.0` includes every audited input's extraction/PDF hash and judge
provider/model/parser/prompt bundle in its identity, including unsampled passes.
`quality_review.py` validates these bindings and keeps legacy `1.0.0` queues
read-only. Regeneration reuses verified reports in a new directory and preserves
historical queues and decisions.
Neither judge nor human review imports storage or mutates extraction output.
The approved schema is trusted; judge reviews final extraction usability rather
than schema design. No labelled evaluation remains in the unified engine.
`quality_review.py` also owns immutable explicit human delivery approvals and
the CSV gate: a current judge pass or an approval bound to exact artifact/schema
bytes and every source PDF. Queue findings alone do not approve delivery.

### One-shot discovery

```text
PDF sample -> document preparation -> provider-native structured output
  -> JSON parse -> JSON Schema validation -> business validation
  -> manifest output_root / schemas / schema*.json
```

`src/run.py` owns the CLI and final envelope. `src/schema/discovery.py` owns the
request and bounded repair cycle. `src/schema/sampler.py` is the only sampler.
Generation uses `src/schema/generation.py`'s fixed strict node descriptor. Dynamic
field/property names are values, then compile locally to `item_schema` and
`document_fields`. Native schema validation remains inside the shared repair
budget. Closed nested schemas and bounded local `$defs` are owned by
`src/schema/nested.py`; no field names are inferred from existing outputs.

### Document preparation

```text
source PDF -> document_parser = pdfingestor | mineru
  -> ParsedPDF (pages, text blocks, Markdown tables) -> shared prompt renderer
```

`src/pdf_ingestion/adapter.py` is the only PDF-to-prompt entry and owns the parser
choice. `pdfingestor` (default) uses the pdfplumber parser. `mineru` uses
`src/pdf_ingestion/mineru.py`, which calls MinerU's `do_parse()` with the `pipeline`
backend in a separate Python process (no HTTP service), converts
`*_content_list.json` into the same `ParsedPDF` model, and
fails before any provider call when the document has no usable content. Both
parsers share the manifest's `cache_root` (`.cache/pdf/<vertical>/`) with
parser-specific cache keys. Cache hits rebind only the in-memory source path and
document name to the currently requested PDF. Discovery,
patch generation and holdout/CLI extraction record the route as
`document_parser` in provenance, `ExtractionResult` and usage logs, and save each
PDF's prompt text below `<markdown_root>/<document_parser>/`, normally
`data/markdown/<vertical>/<document_parser>/`,
mirroring the input tree, for side-by-side comparison. These Markdown files are
derived views, not runtime artifacts.

`src/verticals/documents.py` resolves connected components only through explicit
`amends_document_ids`, orders parents before amendments, validates paths/hashes,
and supplies document-qualified source receipts. Empty inventories create
independent single-PDF groups; declared amendment roles/folders without links
fail. Sampling excludes the contents of every group member from holdout.
The renderer builds evidence indexes from parsed pages/blocks, verifies source
hashes, and uses document IDs to disambiguate equal page numbers. Car's additional
table/continuation parsing and optional parser quality gate extend this owner.

### Refinement loop

```text
discovered schema artifact
  -> optional candidate-patch consensus
  -> optional human review
  -> dynamic extraction contract
  -> holdout extraction artifacts
  -> source-category-aware analysis + refinement_feedback.json
  -> next round
```

The schema actually evaluated is `round_N/schema.json`; when consensus is
enabled, the pre-consensus artifact remains `round_N/schema_draft.json`.
Exhausted extraction validation writes an error artifact and stops the stage
before analysis or later documents.

The shared schema has `fields` (including the classifier) and a `taxonomies` map.
`src/schema/validation.py` validates and normalizes actual historical JSON shapes;
`src/schema/contract.py` compiles a `products` array for all new outputs. Product types,
identity fields, cardinality and taxonomy names come from manifest. Existing
approved Canonical contracts retain their own storage mapping. New extraction
requires every object to be closed and every property declared, using nullable
values for unknowns; historical open schemas remain readable but cannot start
new extraction. Extraction no longer drops structural noise.

Allowlisted domain adapters extend validation through `src/verticals/registry.py`.
Pet preserves comparison fields and document/block/page evidence. Car's v6
candidate supplies the complete former layout as a starting point, while dynamic
schemas can change their fields. Supported declared structures get applicable
rules; artifacts/usage record applied and not-applicable rules. Full compatible
single-PDF Car schemas retain source completeness and text checks. Multi-PDF
amendment groups validate references/structure/relationships and defer clause
supersession/completeness to judge. Unsupported dynamic structures do not claim
to have passed the former full semantic suite.

Holdout applicability uses trusted sampling-category/product mappings declared in
manifest and each field's `applies_to`. Model classification never controls the
denominator. Unlabelled Travel records have N/A classification and product-specific
fill rates; universal fields remain measurable. Unknown/ambiguous source categories
are rejected from analysis. Historical CLI extraction records remain readable;
new extraction/feedback provenance includes vertical and schema version.

### Human review

Consensus policy (runs, promotion, protected fields, manual queue) is configured
in manifest. Aliases files, synonym merging and new `add_alias` proposals are
removed. Historical aliases remain audit data and cannot mutate a schema.

```text
review_queue.json + review_decisions.json + base schema artifact
  -> reviewed_schema.json
```

Queue data is immutable. Status is derived from decisions. Pending and rejected
items are never applied. Queue/decisions/base schema must agree on queue identity,
vertical, schema version and content hash. Resume recomputes the reviewed result
from that base and decisions, preventing another run from being resumed by path
alone. Legacy unbound queues are read-only until explicitly regenerated.

Travel uses one discovery plus five independent patch runs. Conflict-free 4/5
or 5/5 proposals are applied to the consensus base automatically; the review
queue contains only uncertain or unsafe work. Applying the queue starts from
that consensus base so automatic decisions are retained.

### Canonical review

```text
reviewed discovered schema -> deterministic Canonical candidate
  -> mapping + PostgreSQL DDL preview -> explicit human approval
  -> approved Canonical Schema -> extraction/storage gates
```

The candidate builder reuses field/storage mappings from the selected manifest's
approved contract, proposes unknown fields as JSONB, and deep-copies the source.
The UI is shared by all `canonical_review` verticals (all four). Travel reuses
its immutable approved mapping template; non-database verticals propose name/type
core identities and JSONB attributes for CSV. This does not enable their database capability.
Candidate preview does not authorize extraction compilation, table creation, or
loading. Production paths continue to require an approved review record.

Within one multi-product extraction, the Canonical `product_name` identity must
be unique. When a PDS uses one umbrella series name for several marketed tiers,
each product name includes its tier label while `plan_tier` preserves the source
label separately. Duplicate identities fail during structured-output business
validation and enter the bounded repair cycle; they cannot reach storage.

### Product CSV output

`src/storage/csv_export.py` is a portable product view alongside PostgreSQL
loading. Both use `prepare_storage_load`, Canonical load plans and repository
ID helpers. `inspect_extraction_directory` owns ordered directory discovery and
duplicate-source checks for both destinations. CSV joins product/release values,
typed vertical details and source/run provenance; nested attributes stay JSON.
It validates all inputs before using the existing atomic text writer, refuses
overwrites, and creates no database engine or model provider. CLI and console
controls call this owner. CSV capability is independent of PostgreSQL. Each row
also includes all source receipts, document fields and the validated delivery
proof. See the [CSV interface](api.md#product-csv-export) for columns, validation
and delivery requirements.

Travel PostgreSQL preparation and repository transactions persist every group
document and release link. Single-PDF releases retain their identity; grouped
releases bind every PDF hash, alias and amendment link. SPDS changes create a new
release. `create_all` creates missing tables but does not upgrade existing ones.
For an existing database, prepare and review migration SQL against its actual
schema. Live migration/loading is separately authorized.

## Structured-output boundary

- `src/common/model_config.py` resolves provider/model/input selection and the
  approved structured-output capability registry.
  Shared generation, extraction and judge require strict JSON Schema capability;
  the retained DeepSeek JSON-object adapter is unsuitable for these workflows.
- `src/common/model_provider.py` translates one neutral output specification to
  OpenAI Responses JSON Schema, Anthropic `output_config.format`, or DeepSeek
  JSON object mode. Workflow modules contain no provider branches.
- `src/common/structured_output.py` performs strict JSON parsing, contract and
  business validation, and at most two repair retries after the first attempt.
  Each attempt exposes a typed failure stage; callers may record safe rule codes
  without persisting raw model text or parser exception messages.
- `src/common/json_contracts.py` is the only authoritative contract loader.
- `src/common/json_artifacts.py` is the only envelope and JSON persistence
  boundary. It validates before reads/writes and refuses overwrite by default.
- `src/common/openai_run.py` remains the OpenAI client, file lifecycle,
  background polling, token helper, and JSONL owner.

## Artifact rules

Discovery/refinement and holdout extraction JSON use this envelope:

```text
artifact_type + contract_version + status + created_at + provenance
  + data (success only) + error (failure only)
```

Contracts, tracked configuration, JSONL usage logs, documentation, and local PDF
parser caches are not runtime artifacts and are not enveloped.
Single/batch CLI extraction keeps `ExtractionResult` JSON for existing consumers.
Both output styles use the common atomic, no-clobber writer. Default paths respect
manifest output roots and distinguish same-named sources; explicit paths reject
collisions. Holdout failures are written below `errors/<stage>/`; they never occupy
a success path. Batch CLI reports failures and returns a nonzero status.
Raw model output and document contents are excluded from error artifacts.

`src/schema_application/records.py` parses both extraction formats into one
`ParsedExtraction` record before analysis or storage. It owns wrapper, source-count,
vertical and schema-version checks. Analysis can read historical envelopes without
identity; storage requires explicit matching vertical and schema version. Missing
storage identity must be regenerated rather than inferred from the selected schema.
Legacy extraction run IDs retain the hash of the original file bytes.
Batch resume also uses this boundary to validate cached extraction sources,
model/parser identity, and the selected runtime contract/business rules before
creating a provider. Cached artifacts are never modified during validation.

## Package ownership

Engine constructors consume a manifest, model selection, an optional provider,
and operational settings. Prompts, contracts, validators and output cardinality
are derived from the manifest, with no constructor overrides. Both engines use
`resolve_selection()` when no selection is supplied. PDF preparation has one
entry with two parser routes (PDFingestor by default, MinerU opt-in); the unused
AppConfig and the old Markdown-mirror MinerU preprocessor remain retired.
Batch resume creates a provider only when uncached groups remain. Cached results
revalidate the selected contract, domain/source rules and complete receipt set;
their original files are never modified. Labelled batch metrics/reporters and
the CLI/UI `--evaluate` entry are removed; historical Health work remains on its branch.

- `configs/*/`: one manifest per vertical, including validated references to
  its model prompts.
- `prompts/`: all model-facing text; per-vertical system prompts plus shared
  request, feedback, repair, and provider-format templates. Manifests may
  resolve only their own central prompt directory or package-local prompts.
- `src/verticals/`: validated manifests, explicit document groups, allowlisted Pet/Car business adapters.
- `src/pdf_ingestion/`: PDF-to-prompt entry, pdfplumber parser, MinerU route and parse cache.
- `src/schema/`: discovery, sampling, schema loader/validation/compiler and Canonical candidate.
- `src/refine/candidates/`: patch parsing, normalization, voting, stability.
- `src/refine/artifacts/`: deterministic consensus data and CLI rendering.
- `src/refine/human_review/`: queue, decisions, reviewed schema.
- `src/refine/pipeline/`: outer round and resume orchestration.
- `src/schema_application/`: shared extraction and applicability analysis.
- `src/evaluation/`: final-result PDF-evidence quality screening, queue-bound
  human review and delivery approvals for all verticals.
- `src/storage/`: approved Canonical compilation, shared artifact preparation,
  PostgreSQL transactions/idempotency, and joined product CSV output.
- `src/scraper/`: existing Travel acquisition; no new crawler framework.
- `src/stability/`: semantic schema drift excluding envelope/provenance.
- `src/cost/`: JSONL token-cost estimation.
- `src/common/models.py`: shared extraction result models.
- `src/common/data_paths.py`: repository root; manifest resolution owns data paths.
- `src/ui/`: operator, schema-review, Canonical-review, and quality-review entry
  points and presentation helpers; `src/ui/tool/` owns operator controls.

## Safety rules

- Treat `outputs/private_health/schemas/schema.json` as a production contract and never
  overwrite it automatically.
- Do not coerce, infer, or silently repair invalid model data.
- One initial request plus two repair attempts is the hard validation limit.
- Invalid data never reaches a downstream stage.
- Keep completed success and usage artifacts when a later attempt fails.

## Known gaps

- Offline workflow tests use injected provider fakes. They do not establish live
  provider accuracy or parsing quality on real insurance documents.
- The bundled historical Travel v1 schema has open nested fields and cannot
  start new extraction. Generate a new closed candidate and obtain human review.
- Rename/merge/move patch types remain audit-only and require explicit
  schema-level editing.
