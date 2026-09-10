# Architecture

This project is a Python CLI for discovering, evaluating, and refining a
versioned JSON extraction contract for Australian Health and Travel documents.
The same engine consumes a validated manifest plus three prompt files per vertical.

## Runtime flows

### Local operator UI

```text
Streamlit controls -> validated allowlisted argv -> existing CLI entry point
  -> redacted console result
```

`src/verticals/manifest.py` owns package discovery, default selection and operation
capabilities. `src/tool_app.py` selects vertical first and owns presentation and
result state; forms and results reset on context changes, and confirmation is
bound to command/configuration content. `src/tool_ui/forms.py`
owns operation-specific controls, while `src/tool_ui/commands.py` is the only
UI-to-process boundary. It validates supported operations and values, launches
an argument list without a shell, enforces timeouts, and redacts credential-like
console output. It does not accept arbitrary commands or credential values.

The UI contains no workflow logic. `src/run.py` and `src/refine/loop.py` remain
the authoritative parsers and orchestrators, so every UI action is reproducible
as the command preview shown on the page. The console is local-only and has no
authentication boundary.

### One-shot discovery

```text
PDF sample -> document preparation -> provider-native structured output
  -> JSON parse -> JSON Schema validation -> business validation
  -> manifest output_root / schema*.json
```

`src/run.py` owns the CLI and final envelope. `src/schema/discovery.py` owns the
request and bounded repair cycle. `src/schema/sampler.py` is the only sampler.

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
`src/schema/contract.py` compiles single or multiple product outputs. Product types,
identity fields, cardinality and taxonomy names come from manifest. Existing
approved Canonical contracts retain their own strict storage contract.

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
The UI is shared by all configured storage-capable verticals (currently Travel).
Candidate preview does not authorize extraction compilation, table creation, or
loading. Production paths continue to require an approved review record.

Within one multi-product extraction, the Canonical `product_name` identity must
be unique. When a PDS uses one umbrella series name for several marketed tiers,
each product name includes its tier label while `plan_tier` preserves the source
label separately. Duplicate identities fail during structured-output business
validation and enter the bounded repair cycle; they cannot reach storage.

## Structured-output boundary

- `src/common/model_config.py` resolves provider/model/input selection and the
  approved structured-output capability registry.
- `src/common/model_provider.py` translates one neutral output specification to
  OpenAI Responses JSON Schema, Anthropic `output_config.format`, or DeepSeek
  JSON object mode. Workflow modules contain no provider branches.
- `src/common/structured_output.py` performs strict JSON parsing, contract and
  business validation, and at most two repair retries after the first attempt.
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

Contracts, tracked configuration, JSONL usage logs, documentation, and optional
MinerU Markdown inputs are not runtime artifacts and are not enveloped.
Single/batch CLI extraction keeps `ExtractionResult` JSON for existing consumers.
Both output styles use the common atomic, no-clobber writer. Default paths respect
manifest output roots and distinguish same-named sources; explicit paths reject
collisions. Holdout failures are written below `errors/<stage>/`; they never occupy
a success path. Batch CLI reports failures and returns a nonzero status.
Raw model output and document contents are excluded from error artifacts.

## Package ownership

- `configs/*/`: one manifest and discovery/patch/extraction prompt files per vertical.
- `src/verticals/`: validated manifest resolution and existing executable adapters.
- `src/schema/`: discovery, sampling, schema loader/validation/compiler and Canonical candidate.
- `src/refine/candidates/`: patch parsing, normalization, voting, stability.
- `src/refine/artifacts/`: deterministic consensus data and CLI rendering.
- `src/refine/human_review/`: queue, decisions, UI, reviewed schema.
- `src/refine/pipeline/`: outer round and resume orchestration.
- `src/schema_application/`: shared extraction and applicability analysis.
- `src/evaluation/`: existing Health labelled matching and metrics.
- `src/storage/`: approved Canonical compilation, PostgreSQL transactions and idempotency.
- `src/scraper/`: existing Travel acquisition; no new crawler framework.
- `src/stability/`: semantic schema drift excluding envelope/provenance.
- `src/cost/`: JSONL token-cost estimation.

## Safety rules

- Treat `outputs/private_health/schema.json` as a production contract and never
  overwrite it automatically.
- Do not coerce, infer, or silently repair invalid model data.
- One initial request plus two repair attempts is the hard validation limit.
- Invalid data never reaches a downstream stage.
- Keep completed success and usage artifacts when a later attempt fails.

## Known gaps

- Offline tests use injected provider fakes. No live provider generation or
  real MinerU conversion is claimed by the test suite.
- `list[object]` extraction fields have intentionally open item shapes because
  the discovered schema does not define nested properties; local validation
  still enforces the top-level extraction contract. OpenAI requests use strict
  JSON Schema for every other contract; this open-item extraction shape uses
  non-strict JSON Schema mode plus the same local validation and bounded repair
  loop because OpenAI strict mode requires a closed nested object contract.
- Rename/merge/move patch types remain audit-only and require explicit
  schema-level editing.
