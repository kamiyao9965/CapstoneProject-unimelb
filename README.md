# Australian Private Health Schema Discovery

A Python CLI for discovering, stabilising, reviewing, and evaluating reusable
extraction schemas from Australian private health insurance PDFs.

The entire runtime pipeline is JSON-only. Model responses use the strongest
approved structured-output mode for the selected provider, are validated
locally against authoritative JSON Schema contracts, and receive at most two
repair retries. Invalid data never proceeds to the next stage.

## What the project can do

| Capability | Result |
| --- | --- |
| Schema discovery | Generate a reusable private-health extraction contract from a balanced PDF sample |
| Multi-provider execution | Switch between OpenAI, Anthropic, and DeepSeek through `.env` or CLI flags |
| Native structured output | OpenAI JSON Schema, Anthropic JSON Schema, or DeepSeek JSON object mode |
| PDFingestor preprocessing | Convert sampled PDFs into reading-order text blocks and Markdown tables before model requests |
| Stability measurement | Repeat discovery on the same sample and measure semantic schema drift |
| Candidate-patch consensus | Generate N patch sets, normalise aliases, vote on fields, and produce an auditable consensus |
| Human review | Accept, reject, or edit proposals in Streamlit before applying them |
| Holdout schema application | Compile discovered fields into a runtime extraction JSON Schema, extract unseen PDFs, and find schema failures |
| Failure analysis | Measure applicability, fill rate, required-field misses, enum violations, and model-reported unfilled fields |
| Refinement loop | Feed validated failure analysis into a later discovery round |
| Cost estimation | Estimate actual and projected spend from JSONL token-usage logs |
| Travel document acquisition | Discover current PDS, SPDS, brochure, TMD, and FSG PDFs and preserve their product-release relationships |
| Manifest-driven verticals | Select versioned paths, contracts, document models, capabilities, and allowlisted adapters without changing CLI orchestration |

## Safety guarantees

- Generated runtime files use a versioned artifact envelope with provenance,
  success/failure status, and separate `data` and `error` fields.
- JSON is parsed strictly; Markdown fences, partial JSON, YAML, type coercion,
  guessed values, and silent field repair are not accepted.
- The first model attempt may be followed by at most two repair attempts.
- Each billable attempt is recorded in the JSONL usage log under one logical
  run identity.
- Exhausted extraction failure writes below `errors/extraction/` and stops the
  stage before analysis or later documents.
- Existing success paths are not overwritten automatically.
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
contract identifiers, prompt identifiers, pipeline capabilities, and adapter
IDs:

```text
configs/private_health/manifest.json
configs/travel_insurance/manifest.json
contracts/vertical_manifest.schema.json
src/verticals/manifest.py
src/verticals/registry.py
```

Private health currently enables discovery, refinement, extraction, and
evaluation. Travel insurance currently enables acquisition only. Its manifest
already records `product_release` as the extraction unit and `multiple` as the
output cardinality, but discovery and extraction remain disabled until their
JSON contracts, prompts, validators, and tests exist. The CLI fails explicitly
if a disabled stage is requested.

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
schema commands and travel insurance for `crawl`.

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
Markdown report is persisted. Alias normalisation is configured in
`configs/private_health/aliases.json`.

Only safe core/conditional field operations are auto-promoted. Reject votes,
mixed actions, rename, merge, and move operations require human intervention.

## 8. Human review

Start the review UI:

```bash
.venv/bin/python -m streamlit run src/review_app.py -- \
  --consensus-dir outputs/private_health/consensus
```

The UI reads immutable `review_queue.json` and saves accept/reject/edit choices
to `review_decisions.json`. JSON edit errors are shown without replacing the
previous valid decision.

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

Only validated success envelopes contribute product values. Failed or malformed
artifacts increment the error count and never affect fill-rate denominators.

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
