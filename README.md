# Australian Insurance Schema Discovery

A Python CLI for discovering, stabilising, reviewing, and evaluating reusable
extraction schemas from Australian health and travel insurance PDFs.
Health and Travel share the same discovery, consensus, review and extraction engine.

The entire runtime pipeline is JSON-only. Model responses use the strongest
approved structured-output mode for the selected provider, are validated
locally against authoritative JSON Schema contracts, and receive at most two
repair retries. Invalid data never proceeds to the next stage.

## What the project can do

| Capability | Result |
| --- | --- |
| Schema discovery | Generate a reusable insurance extraction contract from a balanced PDF sample |
| Multi-provider execution | Switch between OpenAI, Anthropic, and DeepSeek through `.env` or CLI flags |
| Native structured output | OpenAI JSON Schema, Anthropic JSON Schema, or DeepSeek JSON object mode |
| PDFingestor preprocessing | Convert sampled PDFs into reading-order text blocks and Markdown tables before model requests |
| Stability measurement | Repeat discovery on the same sample and measure semantic schema drift |
| Candidate-patch consensus | Generate N patch sets, validate field names, vote on fields, and produce an auditable consensus |
| Human review | Accept, reject, or edit proposals in Streamlit before applying them |
| Holdout schema application | Compile discovered fields into a runtime extraction JSON Schema, extract unseen PDFs, and find schema failures |
| Failure analysis | Measure applicability, fill rate, required-field misses, enum violations, and model-reported unfilled fields |
| Refinement loop | Feed validated failure analysis into a later discovery round |
| Cost estimation | Estimate actual and projected spend from JSONL token-usage logs |
| Travel document acquisition | Discover current PDS, SPDS, brochure, TMD, and FSG PDFs and preserve their product-release relationships |
| Manifest-driven verticals | Select versioned paths, contracts, document models, capabilities, and allowlisted adapters without changing CLI orchestration |
| Approved Canonical Schema | Compile one human-reviewed business contract into extraction validation and vertical PostgreSQL DDL previews |
| PostgreSQL storage | Create core plus vertical tables and atomically load validated artifacts with deterministic IDs |

## Safety guarantees

- Discovery/refinement/holdout artifacts use a versioned envelope with provenance,
  success/failure status, and separate `data` and `error` fields. Single/batch CLI
  extraction keeps the existing `ExtractionResult` JSON format.
- JSON is parsed strictly; Markdown fences, partial JSON, YAML, type coercion,
  guessed values, and silent field repair are not accepted.
- The first model attempt may be followed by at most two repair attempts.
- Each billable attempt is recorded in the JSONL usage log under one logical
  run identity.
- Exhausted extraction failure writes below `errors/extraction/` and stops the
  stage before analysis or later documents.
- Existing success paths are not overwritten automatically. Explicit output paths
  reject collisions; automatically named extraction/final-schema paths gain a suffix.
- `.env`, source PDFs, MinerU mirrors, outputs, and usage logs are ignored and
  must not be committed.

## Requirements

- macOS or Linux
- Python 3.10–3.13 (MinerU constrains the supported range)
- At least one provider API key
- Existing source PDFs in the bundled `konkrd-data` dataset

The repository contains no API keys or source PDFs.

## 1. Install

```bash
git clone <repository-url>
cd CapstoneProject-unimelb
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Verify the offline suite before using credentials:

```bash
.venv/bin/python -m compileall src tests
.venv/bin/python -m unittest discover -s tests
```

## Local operator UI

Start the local Streamlit console from the project root:

```bash
.venv/bin/python -m streamlit run src/tool_app.py
```

Select the insurance vertical first. The console discovers `configs/*/manifest.json`
and shows only operations enabled for that vertical: discovery, extraction,
batch, refinement, and where supported, acquisition, Canonical compilation and
PostgreSQL storage. Changing vertical or operation clears form/confirmation/result
state. Confirmation belongs to the full command; changing parameters invalidates it.
Results display the vertical and command captured at launch. It displays the exact command
before execution and requires explicit confirmation for LLM, network, and
database operations.

The UI is not a second pipeline: `src/run.py` and `src/refine/loop.py` remain
authoritative. Commands are assembled from an allowlist and launched as an
argument vector without a shell. API keys and database URLs stay in environment
variables and are never entered into the page. Run the UI only on a trusted
local machine; it has no authentication or remote-deployment configuration.

## Travel insurance document acquisition

The `crawl` command is an acquisition step, not an LLM step. It needs no
OpenAI/Anthropic API key. The checked-in source configuration initially covers
Allianz, Cover-More, and Southern Cross Travel Insurance:

```bash
.venv/bin/python src/run.py crawl \
  --manifest configs/travel_insurance/manifest.json
```

Restrict a smoke test to one insurer by repeating `--insurer` as needed:

```bash
.venv/bin/python src/run.py crawl \
  --insurer cover_more \
  --discovery-only
```

The crawler honours `robots.txt`, allows only configured public HTTPS domains,
revalidates redirects, limits PDF size to 50 MiB, verifies the PDF signature,
and stores content by SHA-256. Default local outputs are:

```text
data/travel_insurance/raw/PDFs/<insurer>/<document_type>/<sha256>_<title>.pdf
outputs/travel_insurance/acquisition/<run_id>/acquisition.json
```

Both directories are intentionally ignored by Git. The acquisition artifact
records separate retrieval, validation, and parse statuses, plus PDS/SPDS/
brochure/TMD/FSG relationships and items that need human review.

## Vertical manifests

Each supported business vertical has one validated manifest under `configs/`.
The manifest is the declarative boundary for paths, document categories,
contract identifiers, prompt file paths, capabilities, product types, taxonomies,
identity fields and consensus policy. Three prompt files live alongside each manifest:

```text
configs/private_health/manifest.json
configs/private_health/prompts/{discovery,patch,extraction}.md
configs/travel_insurance/manifest.json
configs/travel_insurance/prompts/{discovery,patch,extraction}.md
contracts/vertical_manifest.schema.json
src/verticals/manifest.py
src/verticals/registry.py
```

Private health enables discovery, refinement, extraction, and evaluation.
Travel insurance enables acquisition, discovery, five-run consensus refinement,
extraction, and storage. Its manifest
records `product_release` as the extraction unit and `multiple` as the output
cardinality, so one PDS extraction produces a `products` array rather than
collapsing several named plans into one record. Travel ground-truth evaluation
remains disabled until a labelled dataset exists; refinement therefore stops
after human review instead of claiming a holdout accuracy result.

Both verticals now produce the same discovered-schema shape: `fields` includes
`product_type`, and `taxonomies` contains the named classification collections.
Allowed product types and taxonomy names come from the selected manifest.
`src/schema/loader.py` validates historical Health/Travel JSON before normalizing a
copy into this shape. Historical source files and approved Canonical contracts
are never rewritten. New model responses must satisfy the current request contract;
the legacy reader is not a fallback for invalid model output.

Sampling categories, document types and product types are separate concepts.
Travel `pds` is a sampling/document category, while `domestic` is a product type.
Only `documents.category_product_types` supplies trusted classification labels;
model predictions never determine analysis denominators.

Manifest files cannot import arbitrary Python functions. Executable behavior
must use an adapter ID registered in `src/verticals/registry.py`. This keeps a
configuration change from becoming an arbitrary-code execution path.
Dataset roots may still be supplied through an explicitly declared environment
override such as `KONKRD_DATA_ROOT`; command-line path flags take precedence.

Use an explicit manifest when selecting a vertical:

```bash
.venv/bin/python src/run.py crawl \
  --manifest configs/travel_insurance/manifest.json
```

Omitting `--manifest` preserves the existing defaults: private health for
schema commands and the sole configured capable vertical (currently Travel) for
acquisition/Canonical/storage. `resolve_manifest` owns these defaults for CLI/UI.

Run Travel schema discovery over PDS samples:

```bash
.venv/bin/python src/run.py discover \
  --manifest configs/travel_insurance/manifest.json \
  --per-category 2 \
  --seed 42
```

Apply the resulting schema to one PDS:

```bash
.venv/bin/python src/run.py extract \
  --manifest configs/travel_insurance/manifest.json \
  --schema outputs/travel_insurance/schema.json \
  --pdf data/travel_insurance/raw/PDFs/scti/pds/example.pdf
```

The Travel discovery sampler intentionally uses PDS documents first. SPDS,
brochures, TMDs, and FSGs remain recognised acquisition document types and will
be joined to product releases in a later relationship-aware refinement stage.

## Approved Canonical Schema

The discovered schema is a model-generated candidate. It must not define
database objects directly. After a human reviewer has finalised field meaning,
types, requiredness, enum values, identity fields, and storage strategies, the
reviewed file uses the Canonical Schema contract with:

```json
{
  "status": "approved",
  "review": {
    "reviewed_by": "reviewer-name",
    "reviewed_at": "2026-08-18T05:00:00Z",
    "rationale": "Approved after Travel product and storage review."
  }
}
```

The approval record is a release gate, not an automatic claim of quality. The
reviewer remains responsible for the business meaning and storage choices.
Changing an approved schema creates a new version rather than editing the old
version in place.

Compile an approved schema without connecting to a database:

```bash
.venv/bin/python src/run.py canonical-compile \
  --manifest configs/travel_insurance/manifest.json \
  --schema path/to/approved_canonical_schema.json \
  --output-dir outputs/travel_insurance/compiled_schema_v1
```

The output directory must not already exist. The command writes:

```text
extraction_contract.json  # runtime validation contract for model extraction
vertical_table.sql        # PostgreSQL DDL preview for human review
```

It does not execute SQL, create a database, call an LLM, or approve a candidate
schema. Candidate schemas and approved schemas with an invalid review record
fail before the output directory is created. Operational tables such as
documents, extraction runs, and raw JSONB remain fixed application-owned
metadata; the Canonical Schema governs vertical business fields.

### Initialize and load PostgreSQL storage

Storage is PostgreSQL-only. Add the connection URL to `.env`; successful
commands never print its value:

```dotenv
KONKRD_DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/konkrd
```

After exporting `.env`, create the missing fixed core tables and the extension
table compiled from the approved Travel Canonical Schema:

```bash
set -a
source .env
set +a

.venv/bin/python src/run.py storage-init \
  --manifest configs/travel_insurance/manifest.json
```

The manifest defaults `--schema` to
`configs/travel_insurance/canonical_schema_v1.json`. Supply `--schema` only for
another explicitly reviewed version.

Load one extraction artifact and its source-PDF provenance in one transaction:

```bash
.venv/bin/python src/run.py storage-load \
  --manifest configs/travel_insurance/manifest.json \
  --artifact outputs/travel_insurance/extractions/cover_more_business_canonical_v1.json \
  --insurer-code cover_more
```

The loader revalidates the approval record, extraction data, vertical and
schema version, source path, PDF signature, and content hashes before opening a
transaction. It preserves the exact artifact in `raw_extractions` before
upserting deterministic product and release rows. Repeating the same command
is idempotent; a reused run or schema version with different content fails and
rolls back the whole load. Flexible data uses PostgreSQL `JSONB`; no separate
JSON database or SQLite fallback is used.

Run the opt-in integration check only against a disposable PostgreSQL database:

```bash
KONKRD_TEST_DATABASE_URL=postgresql+psycopg:///konkrd_travel_smoke \
  .venv/bin/python -m unittest tests.test_postgres_storage_live -v
```

## 2. Use the known source documents

The project reads the known `konkrd-data` PDF dataset bundled with this
workspace by default:

```text
konkrd-data/data/private_health/raw/PDFs/
  AUF/
    hospital/
      product-a.pdf
  CBC/
    generalhealth/
      product-b.pdf
  NTF/
    combined/
      product-c.pdf
  RBH/
    extras/
      product-d.pdf
```

If your dataset lives somewhere else, set `KONKRD_DATA_ROOT` to that
`konkrd-data` directory or pass `--input-root` explicitly.

Default categories are `hospital`, `extras`, `generalhealth`, and `combined`.
Sampling is category-balanced, content-deduplicated by SHA-256, and supports a
fixed seed. Holdout selection excludes discovery documents by content identity.

Holdout field applicability uses the authoritative category encoded in each
source PDF path together with each field's `applies_to` contract. The model's
extracted `product_type` is reported separately as classification accuracy and
never controls fill-rate denominators. Fields with no applicable holdout sample
are reported as `N/A` rather than `0%`.

## 3. Configure `.env`

Create `.env` in the repository root. Keep all provider credentials so you can
switch models without editing the file structure:

```dotenv
# Active selection
LLM_PROVIDER=openai
LLM_MODEL=gpt-5
LLM_DOCUMENT_INPUT=markdown

# Provider credentials
MY_OPENAI_API_KEY=replace-with-your-openai-key
ANTHROPIC_API_KEY=replace-with-your-anthropic-key
DEEPSEEK_API_KEY=replace-with-your-deepseek-key

# PostgreSQL storage
KONKRD_DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/konkrd

# Optional compatible endpoint overrides
# OPENAI_BASE_URL=https://...
# ANTHROPIC_BASE_URL=https://...
# DEEPSEEK_BASE_URL=https://api.deepseek.com
```

Selection precedence is CLI flag, then `.env`, then the OpenAI defaults.
`OPENAI_API_KEY` is also accepted; `MY_OPENAI_API_KEY` has precedence when both
exist. `OPENAI_MODEL` is only a legacy OpenAI fallback; prefer `LLM_MODEL`.

The CLI does not load `.env` itself. Export it into the shell before running:

```bash
set -a
source .env
set +a
```

Do not paste real keys into issues, logs, screenshots, commits, or chat.

### Switch providers

OpenAI with PDFingestor text input:

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=gpt-5
LLM_DOCUMENT_INPUT=markdown
```

Anthropic with PDFingestor text input:

```dotenv
LLM_PROVIDER=anthropic
LLM_MODEL=claude-sonnet-4-5
LLM_DOCUMENT_INPUT=markdown
```

DeepSeek uses the same PDFingestor text input:

```dotenv
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-v4-pro
LLM_DOCUMENT_INPUT=markdown
```

The model identifier must exist at the configured endpoint. The project accepts
the approved `deepseek-*` family, but it cannot make an unavailable provider
model exist. Confirm the exact model ID with your DeepSeek account or proxy.

The same selection can be overridden for one discovery command:

```bash
.venv/bin/python src/run.py discover \
  --provider anthropic \
  --model claude-sonnet-4-5 \
  --document-input markdown
```

## 4. Run one-shot schema discovery

Quick smoke-sized sample:

```bash
.venv/bin/python src/run.py discover \
  --per-category 1 \
  --seed 42
```

Use backslashes exactly as shown when splitting a shell command across lines.
Without them, each following line becomes a separate command.

Default success output:

```text
outputs/private_health/schema.json
```

If that path exists, the CLI selects `schema_1.json`, then `schema_2.json`, and
so on. It never replaces the baseline automatically.

Use explicit documents when needed:

```bash
.venv/bin/python src/run.py discover \
  --samples \
    konkrd-data/data/private_health/raw/PDFs/AUF/hospital/product-a.pdf \
    konkrd-data/data/private_health/raw/PDFs/RBH/extras/product-d.pdf \
  --output outputs/private_health/manual_schema.json
```

Important options:

| Option | Default | Purpose |
| --- | --- | --- |
| `--input-root` | `konkrd-data/data/private_health/raw/PDFs` | PDF corpus root |
| `--categories` | four standard categories | Categories to sample |
| `--per-category` | `5` | Documents sampled per category |
| `--seed` | random | Reproducible sample selection |
| `--provider` | environment/default | Provider override |
| `--model` | environment/default | Model override |
| `--document-input` | `markdown` | Must be `markdown`; PDFingestor renders source PDFs to inline text |
| `--timeout` | `600` | Request/poll timeout seconds |
| `--output` | `outputs/private_health/schema.json` | Preferred success path |
| `--usage-log` | `outputs/private_health/token_usage.jsonl` | Per-attempt usage log |

On exhausted validation, the command exits non-zero and writes a redacted
artifact below `outputs/private_health/errors/schema_discovery/`.

## 5. PDFingestor document preparation

Schema discovery and extraction always parse each selected source PDF locally
with PDFingestor. The resulting reading-order text blocks and Markdown tables
are sent inline to the selected model provider; the application pipeline does
not upload the raw PDF or create a MinerU Markdown mirror.

For that reason, keep `LLM_DOCUMENT_INPUT=markdown`. Selecting `pdf` is reserved
for low-level provider calls that attach raw documents and is rejected by the
PDFingestor discovery and extraction classes. A parsing failure is fail-closed:
the provider is not called and the stage writes a failure artifact when an
output path is available.

## 6. Measure schema stability

Run discovery repeatedly on one fixed sample:

```bash
.venv/bin/python src/stability/measure.py \
  --runs 3 \
  --per-category 1 \
  --seed 42 \
  --out-dir outputs/private_health/stability
```

This writes `run_1.json`, `run_2.json`, and so on, then compares product types,
field contracts, hospital categories, and extras services. Envelope timestamps
and provenance do not count as semantic drift.

Compare existing artifacts without an API call:

```bash
.venv/bin/python src/stability/compare.py \
  --schemas \
    outputs/private_health/stability/run_1.json \
    outputs/private_health/stability/run_2.json \
  --show-items
```

Or compare every success artifact in one directory:

```bash
.venv/bin/python src/stability/compare.py \
  --dir outputs/private_health/stability
```

## 7. Run candidate-patch consensus

Consensus asks the model for changes to the baseline instead of repeatedly
rewriting the whole schema:

```bash
.venv/bin/python src/refine/consensus.py \
  --base-schema outputs/private_health/schema.json \
  --runs 5 \
  --per-category 1 \
  --seed 42 \
  --out-dir outputs/private_health/consensus
```

Outputs:

```text
outputs/private_health/consensus/
  candidate_patches/
    run_001.json
    run_002.json
  consensus_schema.json
  field_frequency.json
  patch_stability.json
  review_queue.json
```

The human-readable report is rendered in the terminal from validated JSON; no
Markdown report is persisted. There is no aliases configuration or synonym
merging. Spelling normalization is deterministic; `annual_limit` and `yearly_limit`
remain separate candidates. Historical alias data is audit-only and `add_alias`
cannot be applied. The retired `--alias-config` option fails explicitly.

Private Health preserves its existing safe core/conditional promotion policy.
Reject votes, mixed actions, rename, merge, and move operations require human
intervention.

For Travel, run the manifest-driven loop. Its default is five patch runs:

```bash
.venv/bin/python src/refine/loop.py \
  --manifest configs/travel_insurance/manifest.json \
  --review-ui
```

For Travel, only conflict-free 4/5 or 5/5 proposals are applied automatically.
Conditional fields, identity changes, contract conflicts, and unsafe operations
stay in the UI queue, while frequency and stability artifacts retain every
proposal for audit.

## 8. Human review

Start the review UI:

```bash
.venv/bin/python -m streamlit run src/review_app.py -- \
  --consensus-dir outputs/private_health/consensus
```

The UI reads immutable `review_queue.json` and saves accept/reject/edit choices
to `review_decisions.json`. JSON edit errors are shown without replacing the
previous valid decision. Queue, decisions and base schema identities must match,
including vertical, version and base content. Widget state is bound to the queue.
Old unbound review runs must be regenerated or completed with their original code
version; they are never silently adopted by the new engine.

After applying a Travel review, resume the round and then review the deterministic
Canonical database mapping:

```bash
.venv/bin/python src/refine/loop.py \
  --manifest configs/travel_insurance/manifest.json \
  --resume-review outputs/travel_insurance/refine/round_1

.venv/bin/python -m streamlit run src/canonical_review_app.py -- \
  --schema outputs/travel_insurance/refine/round_1/consensus/reviewed_schema.json
```

The mapping UI previews PostgreSQL DDL but does not execute it. Approval requires
a reviewer, rationale, and explicit confirmation, and writes a new Canonical
Schema file. Existing approved field mappings are reused; unknown fields are
proposed as JSONB and remain subject to human review. Changing the input content
or output path clears approval state; approved source contracts stay unchanged.

Apply saved decisions from the UI or CLI:

```bash
.venv/bin/python src/refine/review.py apply \
  --consensus-dir outputs/private_health/consensus
```

Result:

```text
outputs/private_health/consensus/reviewed_schema.json
```

Pending and rejected items are not applied. Unknown IDs, malformed decisions,
invalid field edits, and unsafe rename/merge/move upserts fail loudly.

## 9. Run schema generation with refinement

The schema-generation loop runs in this order:

```text
PDF samples
  -> schema discovery
  -> optional voting / consensus
  -> optional human review
  -> holdout schema application
  -> failure discovery
  -> refinement feedback
  -> next round schema discovery
  -> final_schema.json
```

Module responsibilities:

```text
src/schema/               schema discovery and schema contract generation
src/refine/               refinement-loop orchestration
src/schema_application/   apply schema to holdout PDFs and discover failures
src/refine/consensus.py   multi-run patch voting / consensus
src/refine/human_review/  review queue, decisions, and apply
```

### One round without consensus

```bash
.venv/bin/python src/refine/loop.py \
  --per-category 1 \
  --eval-per-category 1 \
  --seed 42 \
  --eval-seed 7
```

### Consensus with a human review stop

```bash
.venv/bin/python src/refine/loop.py \
  --per-category 1 \
  --eval-per-category 1 \
  --consensus-runs 3 \
  --review-ui \
  --seed 42 \
  --eval-seed 7
```

This stops after writing the consensus review queue. Review and apply the queue,
then resume with the reviewed schema; holdout extraction, failure discovery,
`refinement_feedback.json`, and `final_schema.json` are produced after resume:

```bash
.venv/bin/python src/refine/loop.py \
  --resume-review outputs/private_health/refine/round_1
```

### Feed reviewed-schema feedback into another round

```bash
.venv/bin/python src/refine/loop.py \
  --resume-feedback outputs/private_health/refine/round_1/refinement_feedback.json
```

### Autonomous multi-round execution

```bash
.venv/bin/python src/refine/loop.py \
  --autonomous \
  --rounds 3 \
  --per-category 1 \
  --eval-per-category 1
```

Autonomous mode removes the human stop; it does not weaken validation or the
three-attempt maximum.

Typical round contents:

```text
outputs/private_health/refine/
  final_schema.json              # latest completed round schema for extraction/evaluation

round_1/
  schema.json
  schema_draft.json              # when consensus is enabled
  extraction_usage.jsonl
  extractions/*.json
  errors/extraction/*.json       # only on failure
  refinement_feedback.json
  consensus/                     # when enabled
    candidate_patches/*.json
    consensus_schema.json
    field_frequency.json
    patch_stability.json
    review_queue.json
    review_decisions.json        # after review begins
    reviewed_schema.json         # after apply
```

Use `outputs/private_health/refine/final_schema.json` as the fixed schema for
the downstream extraction and ground-truth evaluation pipeline:

```bash
.venv/bin/python src/run.py batch \
  --schema outputs/private_health/refine/final_schema.json \
  --evaluate
```

## 10. Analyze extraction artifacts directly

```bash
.venv/bin/python src/schema_application/analyze.py \
  --schema outputs/private_health/refine/round_1/schema.json \
  --extractions outputs/private_health/refine/round_1/extractions \
  --feedback-out outputs/private_health/refine/round_1/manual_feedback.json
```

Both success envelopes and the existing CLI `ExtractionResult` JSON are validated
against the chosen schema. Directories are scanned recursively. Present vertical
and schema-version provenance must match; failed/malformed artifacts are counted
as errors and never affect denominators. Travel has no trusted product labels, so
classification accuracy and product-specific fill rates are N/A; universal fields
can still be analyzed. Regenerate historical feedback without vertical provenance
before using `--resume-feedback`.

## 11. Estimate cost

Actual usage summary:

```bash
.venv/bin/python src/cost/estimate.py \
  --log outputs/private_health/token_usage.jsonl
```

Project across document counts:

```bash
.venv/bin/python src/cost/estimate.py \
  --log outputs/private_health/token_usage.jsonl \
  --project \
  --vertical private_health=1000
```

Custom rates are USD per one million tokens:

```bash
.venv/bin/python src/cost/estimate.py \
  --log outputs/private_health/token_usage.jsonl \
  --input-rate 1.25 \
  --output-rate 10.00
```

## Artifact envelope

All generated runtime JSON has this common shape:

```json
{
  "artifact_type": "discovered_schema",
  "contract_version": "1.0.0",
  "status": "success",
  "created_at": "2026-07-14T08:00:00Z",
  "provenance": {
    "run_id": "...",
    "provider": "openai",
    "model": "gpt-5",
    "document_input": "pdf",
    "source_documents": [],
    "source_artifacts": []
  },
  "data": {},
  "error": null
}
```

Failure artifacts have `status: "failed"`, `data: null`, and a structured,
redacted `error`. Tracked contracts and configuration are ordinary JSON, and
token logs remain JSONL.

## Development verification

```bash
.venv/bin/python -m compileall src tests
.venv/bin/python -m unittest discover -s tests
.venv/bin/python src/run.py --help
.venv/bin/python src/run.py canonical-compile --help
.venv/bin/python src/refine/loop.py --help
.venv/bin/python src/refine/consensus.py --help
.venv/bin/python src/refine/review.py --help
.venv/bin/python src/stability/compare.py --help
.venv/bin/python src/stability/measure.py --help
.venv/bin/python src/schema_application/analyze.py --help
```

These checks are offline. A passing suite does not prove live credentials,
provider model availability, provider-side schema acceptance, or parsing every
source PDF in the corpus.

## Troubleshooting

### `... values must be a list`

The model returned data outside the contract. The current pipeline sends the
validation path back for up to two repairs. If all attempts fail, inspect the
redacted error artifact and usage log; invalid data is not saved as a schema.

### DeepSeek rejects PDF mode

Set `LLM_DOCUMENT_INPUT=markdown`. All application providers receive the
PDFingestor representation as inline text.

### Model is not approved for structured output

The model ID does not match `configs/model_capabilities.json`. Verify provider
documentation before changing the registry.

### Existing output path

One-shot discovery chooses a numeric suffix. Other governed stages refuse to
overwrite their expected artifacts; use a new output directory.

### No live API verification

The test suite intentionally makes no credentialed calls. Run a small
`--per-category 1` discovery when you are ready to spend API credit.
