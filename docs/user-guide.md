# Health / Travel operator guide

For project development, experiments, and demonstrations. Run commands from the
repository root and replace example PDF paths with your own files. See the
[documentation index](README.md), [Python and file interfaces](api.md),
[architecture](architecture.md), and [directory conventions](project-layout.md).

## 1. Choose the right entry point

| Goal | Entry point | External services |
| --- | --- | --- |
| Configure and run workflows in a page | `src/ui/tool_app.py` | Depends on operation |
| Discover fields from PDFs | `src/run.py discover` | Model API |
| Extract using a schema | `src/run.py extract` / `batch` | Model API |
| Generate proposals, review, and continue | `src/refine/loop.py` + `src/ui/review_app.py` | Models for generation/extraction |
| Inspect existing extraction issues | `src/schema_application/analyze.py` | None |
| Compare existing schemas | `src/stability/compare.py` | None |
| Acquire public Travel documents | `src/run.py crawl` | Insurer websites |
| Approve a Travel storage contract | `src/ui/canonical_review_app.py` | None |
| Preview SQL / initialize or load tables | `canonical-compile` / `storage-init`, `storage-load`, `storage-load-batch` | Only initialization/loading connects to PostgreSQL |
| Screen Travel extraction quality | `src/run.py quality-audit` + `src/ui/quality_review_app.py` | Judge calls a model; human review does not |

The project has one Python engine, four local Streamlit pages, and file artifacts.
It has no REST service.

### Differences between verticals

| Configuration | Health | Travel |
| --- | --- | --- |
| Vertical code | `private_health` | `travel_insurance` |
| Manifest | `configs/private_health/manifest.json` | `configs/travel_insurance/manifest.json` |
| Sampling categories | `combined`, `extras`, `generalhealth`, `hospital` | `pds` |
| Extraction unit | One object per document | Multiple products/product releases per document |
| Product types | `hospital`, `extras`, `generalhealth`, `combined` | `international_single_trip`, `international_multi_trip`, `domestic`, `inbound`, `business`, `cruise` |
| Taxonomies | `hospital_categories`, `extras_services` | `coverage_categories` |
| Default proposal runs | 1 | 5 |
| Automatic loop holdout/feedback | Supported | Not configured |
| Labelled batch evaluation | Supported with local labels | Unsupported |
| PDF-evidence quality screening | Disabled | Supported; not an accuracy evaluation |
| Acquisition / Canonical / PostgreSQL | Disabled | Supported |

Travel's `pds` is a document category, not a product-type ground-truth label.

## 2. Installation and environment

Use the existing `.venv/bin/python` when available. For a new installation:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python src/run.py --help
```

See the [README](../README.md#installation) and [dependency policy](dependency-policy.md).
The repository does not contain credentials, source PDFs, or labelled data.

Workflows read the process environment and **do not load `.env` automatically**.
Set nonsensitive options in the terminal launching the CLI/Streamlit process,
and inject credentials through your own secure method:

```bash
export LLM_PROVIDER=openai
export LLM_MODEL=gpt-5
export LLM_DOCUMENT_INPUT=markdown
```

If you maintain a trusted local `.env`, import it yourself before launching:

```bash
set -a
source .env
set +a
```

This executes the file as shell content. Use it only for a trusted file you
maintain; never commit configuration secrets or paste them into logs/conversations.

| Variable | Meaning |
| --- | --- |
| `MY_OPENAI_API_KEY` / `OPENAI_API_KEY` | OpenAI credentials; the former takes precedence |
| `ANTHROPIC_API_KEY` | Anthropic credentials |
| `DEEPSEEK_API_KEY` | DeepSeek credentials |
| `LLM_PROVIDER`, `LLM_MODEL`, `LLM_DOCUMENT_INPUT` | Model selection; explicit CLI arguments take precedence |
| `OPENAI_MODEL` | OpenAI fallback when `LLM_MODEL` is unset |
| `KONKRD_DATA_ROOT` | Optional Health data root containing `pdf/private_health`, `markdown/private_health`, and `labelled/private_health` |
| `KONKRD_DATABASE_URL` | PostgreSQL connection string for Travel storage |

Defaults are `openai / gpt-5 / markdown`. Other providers require a model name.
[model_capabilities.json](../configs/model_capabilities.json) is the local allowlist;
it does not guarantee remote account access. Workflows prepare page-numbered
text blocks and Markdown tables first. Keep Markdown input enabled; provider-native
PDF support does not imply that the workflow supports switching to PDF input.

### Choosing a PDF parser

Both routes produce the same page/text/table representation and use the same
prompt, validation, and storage boundaries.

| Route | CLI / UI option | Typical use |
| --- | --- | --- |
| PDFingestor (default) | `--document-parser pdfingestor` / “PDFingestor (current route)” | Routine parsing with the existing pdfplumber implementation |
| MinerU | `--document-parser mineru` / “MinerU (local models, slower)” | Complex layouts, tables, OCR, and route comparisons |

- `discover`, `extract`, `batch`, and `refine/loop.py` support the option; their
  console forms expose the same choice. Standalone `refine/consensus.py` and
  `stability/measure.py` currently use PDFingestor only.
- MinerU runs the `pipeline` backend in a local subprocess. It needs local models
  from `mineru-models-download` or the model directory configured in `~/mineru.json`.
  It does not start an HTTP service or upload PDFs.
- A first MinerU pass on a long PDF can take minutes. A previously recorded local
  40-page PDS smoke test took about four minutes, versus about three seconds for
  PDFingestor; these are historical observations, not guarantees. Both routes
  now cache under `.cache/pdf/<vertical>/`, keyed by content and parser settings.
- MinerU can recognize merged cells but may concatenate English words split
  across lines, for example `transportationexpenses`. Consider this when comparing
  extraction quality.
- Keep the same `--document-parser` when using `--resume-review` or
  `--resume-feedback`; omitting it returns later stages to the default route.
- Provenance, `ExtractionResult`, and usage logs record `document_parser`.
- Discovery and extraction save the per-PDF text sent to the model under
  `data/markdown/<vertical>/pdfingestor/` or `.../mineru/`, mirroring the input
  tree. Outside-root PDFs use `<stem>_<path-hash>.md`. Unchanged text is not
  rewritten. These are derived views that can be regenerated; this migration
  preserves all existing copies.
- MinerU fails before model calls when it finds no text or tables. PDFingestor
  does not currently have this empty-content check, so inspect scanned PDFs first.

### Upgrading from earlier layouts

Use the [migration map](project-layout.md#migration-map). Python PDF imports now
use `src.pdf_ingestion`, shared result types use `src.common.models`, and Streamlit
entry points live in `src/ui/`. Manifests now require `markdown_root` and `cache_root`.
`KONKRD_DATA_ROOT` denotes the data root itself; migrate external data to the
same `pdf/`, `markdown/`, and `labelled/` structure before setting it.

The CLI/UI vertical selection model is unchanged. Change vertical rules in
`configs/<vertical>/`, and model-facing text in `prompts/`. Constructors no longer
accept duplicate prompt/contract/vertical overrides. See the
[Python constructor migration](api.md#5-discovery-and-extraction).

The unused `src.config.AppConfig` and former `common.document_preprocessor` were
retired earlier. The implicit neighboring `konkrd-data` search is also retired.
MinerU remains an explicit parser route, not the old `raw/Markdown` mirror utility.
Historical Markdown, schemas, and results are retained. Health evaluation writes
one report set: use `report.json`, not `report_model_only.json`.
`--no-fallback` is accepted for compatibility but does not change behavior.

### Preparing data

Preserve `insurer / category / file.pdf` below each input root:

```text
data/pdf/private_health/
  insurer_a/hospital/example.pdf
  insurer_a/extras/example.pdf
  insurer_b/hospital/example.pdf

data/pdf/travel_insurance/
  allianz/pds/example.pdf
  cover_more/pds/example.pdf
```

This is a layout example, not a claim that those files are supplied. Prepare the
other Health categories as needed. Sampling requires enough distinct insurers
per category and deduplicates PDF content: `--per-category 5` does not mean five
files from one insurer. Copying a PDF does not create another unique sample.
Holdout also excludes content used by discovery/proposals.

Travel is independent of `KONKRD_DATA_ROOT`. Use `--input-root` for a particular
discovery/batch/loop run. For later storage, the manifest input root must agree
with the actual source location. Optional Health labels belong under
`data/labelled/private_health/`; this migration does not create missing labels.

## 3. Extract with an existing schema

An existing usable schema avoids repeating discovery. These commands call a model.

Health, with a schema you generated or reviewed:

```bash
.venv/bin/python src/run.py extract \
  --manifest configs/private_health/manifest.json \
  --schema outputs/private_health/schemas/schema.json \
  --pdf data/pdf/private_health/insurer_a/hospital/example.pdf
```

Both schema and PDF must exist. Travel can use the repository's approved reference
Canonical contract:

```bash
.venv/bin/python src/run.py extract \
  --manifest configs/travel_insurance/manifest.json \
  --schema configs/travel_insurance/canonical_schema_v1.json \
  --pdf data/pdf/travel_insurance/allianz/pds/example.pdf
```

Inspect the JSON path printed by the command. `data` contains extracted values;
Travel products are in `data.products`. Missing fields can be null; inspect
`_unfilled` and `_notes`. Structural validity does not establish factual accuracy;
check key values against the PDF before a demonstration.

Defaults write below `outputs/<vertical>/extractions/`, mirroring the input-relative
path. Repeated single extraction uses a numbered filename. An explicit `--output`
that already exists is rejected; choose another name.

### Using the console

```bash
.venv/bin/python -m streamlit run src/ui/tool_app.py --server.address 127.0.0.1
```

1. Select Health or Travel.
2. Select an operation and fill PDF, schema, model, and output settings; choose
   MinerU in “PDF parsing route” when needed.
3. Review the command and vertical, then confirm operations that require it.
4. Inspect the command, exit status, and artifact paths.

Changing the vertical/operation resets forms, confirmation, and result state.
Changing command settings requires confirmation again. The console runs the CLI
synchronously without a background queue. Credentials come from the launching
process; restart the page process after changing its environment.

## 4. Health: discovery, review, holdout, and feedback

### A. Discover a draft

```bash
.venv/bin/python src/run.py discover \
  --manifest configs/private_health/manifest.json \
  --per-category 2 --seed 42 \
  --output outputs/private_health/experiments/demo/schema.json
```

This selects two insurers in each of four categories: eight content-distinct PDFs.
Reduce the count or add data if insufficient. `--samples path/to/a.pdf path/to/b.pdf`
bypasses automatic sampling. Output is a `discovered_schema` envelope with the
schema in `data`. Read the printed filename: discovery chooses an available suffix
even with explicit `--output`, rather than assuming overwrite.

### B. Review proposals

```bash
.venv/bin/python src/refine/loop.py \
  --manifest configs/private_health/manifest.json \
  --out-dir outputs/private_health/experiments/review-demo \
  --per-category 2 --eval-per-category 1 \
  --seed 42 --eval-seed 7 --review-ui
```

The first run stops after creating `round_1/consensus/`. Reusing the experiment
root creates the next free `round_N`; adjust paths accordingly. Health defaults
to one proposal, and `--review-ui` creates a queue even for that single run.
Open the review page on another port if the console already uses 8501:

```bash
.venv/bin/python -m streamlit run src/ui/review_app.py \
  --server.address 127.0.0.1 --server.port 8502 -- \
  --consensus-dir outputs/private_health/experiments/review-demo/round_1/consensus
```

Choose Accept, Reject, or Edit per item, save decisions, and Apply in the page.
Alternatively, save and exit the page, then Apply through the CLI:

```bash
.venv/bin/python src/refine/review.py apply \
  --consensus-dir outputs/private_health/experiments/review-demo/round_1/consensus
```

Apply writes `reviewed_schema.json` by default. Pending/rejected proposals do not
apply; Apply does not mean review is complete. Queue, decisions, and base schema
must belong to the same review; do not combine files from different experiments.
Continue with the same manifest, input root, and experiment directory:

```bash
.venv/bin/python src/refine/loop.py \
  --manifest configs/private_health/manifest.json \
  --out-dir outputs/private_health/experiments/review-demo \
  --eval-per-category 1 --eval-seed 7 \
  --resume-review outputs/private_health/experiments/review-demo/round_1
```

Resume validates the review, extracts unused holdout PDFs, writes
`refinement_feedback.json`, and publishes `final_schema.json` or a suffixed version
inside the experiment. It does not replace the production schema.

**Apply can save elsewhere with `--out`, but `--resume-review` reads only
`consensus/reviewed_schema.json`.** Later decision edits invalidate a previous
reviewed artifact; regenerate a matching one. Prefer new experiment directories
to preserve the audit trail.

### C. Start the next round from feedback

```bash
.venv/bin/python src/refine/loop.py \
  --manifest configs/private_health/manifest.json \
  --out-dir outputs/private_health/experiments/feedback-demo \
  --per-category 2 --eval-per-category 1 \
  --resume-feedback outputs/private_health/experiments/review-demo/round_1/refinement_feedback.json
```

Feedback must be a successful current-format artifact with matching vertical
provenance. The default is one round; `--autonomous --rounds N` runs N rounds and
increases model calls. `--autonomous` and `--review-ui` cannot be combined.

### D. Batch extraction and labelled evaluation

```bash
.venv/bin/python src/run.py batch \
  --manifest configs/private_health/manifest.json \
  --schema outputs/private_health/experiments/review-demo/final_schema.json \
  --evaluate
```

Use the actual printed final-schema filename. `--evaluate` requires local Health
labels under `data/labelled/private_health/` (or the explicit external data root).
Omit it when labels are unavailable. Batch calls a model for input PDFs and writes
extractions under the vertical output root; `--output-dir` selects a separate
extraction folder. It has no `--out-dir` option.

Reports are `outputs/private_health/evaluation/report.json` and `report.md`.
New runs do not generate duplicate `report_model_only.*`; historical copies remain.

## 5. Travel: acquisition, schema review, extraction, and storage

### A. Acquire public documents

```bash
.venv/bin/python src/run.py crawl \
  --manifest configs/travel_insurance/manifest.json \
  --discovery-only
```

Inspect acquisition metadata, then omit `--discovery-only` to download PDFs. The
flag still visits websites and writes metadata. Sources live in
[sources.json](../configs/travel_insurance/sources.json); repeat `--insurer CODE`
to restrict insurers.

Configured codes are `allianz`, `cover_more`, `scti`, `insureandgo`, `tick`, and
`onecover` (1Cover). InsureandGo, Tick, and Allianz have explicit PDS
`seed_documents` because page discovery previously mixed historical documents or
misclassified PDS as TMD. Update configured URLs when new versions are released.
Insurers with seed documents and no start pages download only the configured files.

```bash
.venv/bin/python src/run.py crawl \
  --manifest configs/travel_insurance/manifest.json \
  --insurer allianz --insurer insureandgo --insurer tick --insurer onecover
```

Automatic sampling selects at most one PDF per insurer per category;
`--per-category` cannot exceed the number of insurers with PDS files. Acquisition
supports PDS, SPDS, brochure, TMD, and FSG; automatic discovery sampling currently
uses only `pds` directories.

### B. Generate proposals and review

```bash
.venv/bin/python src/refine/loop.py \
  --manifest configs/travel_insurance/manifest.json \
  --out-dir outputs/travel_insurance/experiments/review-demo \
  --per-category 2 --seed 42 --review-ui
```

Travel defaults to five proposals. Follow section 4 to review `round_N/consensus`,
save decisions, Apply, and resume with the Travel manifest and this experiment
root. Only eligible core suggestions are promoted automatically; the queue keeps
items requiring judgment. `product_name` and `product_type` are protected. Voting
frequency is not accuracy; inspect semantics even for frequent suggestions.

Resume publishes a final schema without Health's automatic holdout/feedback loop.
Use that discovered schema directly for extraction, or continue to Canonical
approval for database storage.

### C. Approve the storage contract

```bash
.venv/bin/python -m streamlit run src/ui/canonical_review_app.py \
  --server.address 127.0.0.1 --server.port 8503 -- \
  --manifest configs/travel_insurance/manifest.json \
  --schema outputs/travel_insurance/experiments/review-demo/final_schema.json \
  --output outputs/travel_insurance/experiments/review-demo/canonical_approved.json
```

The page creates a candidate, displays fields and mappings, and requires a human
reviewer, rationale, and approval. Changes to inputs, destination, or mapping
choices invalidate confirmation. It makes no model or DB call and does not
overwrite the approved reference schema.

The mapping table's `target` is the database destination: `core_column` maps to
shared core fields (product name/type); `extension_column` maps to a named column
in `travel_product_details`; `jsonb` uses the `attributes` column keyed by field name.
Compatible fields retain their approved mappings. For unmapped fields:

- **JSONB attributes (default):** use `attributes`; later field changes need no
  new SQL columns.
- **SQL columns:** use same-named extension columns with appropriate numeric,
  Boolean, or enum types. Lists, already-approved JSONB fields, and reserved
  `release_id` / `attributes` names stay in JSONB.

Counts above the table show each storage strategy. More columns mean more future
schema migration work; `storage-init` does not alter existing tables.
Compile the extraction contract and SQL preview offline:

```bash
.venv/bin/python src/run.py canonical-compile \
  --manifest configs/travel_insurance/manifest.json \
  --schema outputs/travel_insurance/experiments/review-demo/canonical_approved.json \
  --output-dir outputs/travel_insurance/experiments/review-demo/compiled
```

The new, previously nonexistent folder contains `extraction_contract.json` and
`vertical_table.sql`. Compilation executes no SQL. A candidate is not an approved contract.

### D. Extract and load with the approved version

Re-extract using the **same approved schema** passed to storage. Changing the
version string of a discovered result is not a valid migration. Both CLI
`ExtractionResult` and holdout envelopes must have vertical/schema identity
matching the manifest and approved contract. Historical envelopes without it may
support analysis but require re-extraction before loading; never infer identity.

These commands write to PostgreSQL selected by `KONKRD_DATABASE_URL`; confirm the
target environment before running:

```bash
.venv/bin/python src/run.py storage-init \
  --manifest configs/travel_insurance/manifest.json \
  --schema outputs/travel_insurance/experiments/review-demo/canonical_approved.json

.venv/bin/python src/run.py storage-load \
  --manifest configs/travel_insurance/manifest.json \
  --schema outputs/travel_insurance/experiments/review-demo/canonical_approved.json \
  --artifact outputs/travel_insurance/extractions/allianz/pds/example.json \
  --insurer-code allianz
```

Use a real artifact. Its PDF must exist under the manifest input root at
`insurer/document_type/`, and the insurer must match. SQLite is unsupported.
Initialization creates missing tables, not migrations. Loading is transactional;
consistent repeated identities can be loaded again, while conflicts fail.

### E. Batch extraction and loading

The console provides “Batch extraction” (default PDS category, “Extraction output
folder”) and “Load extraction folder into PostgreSQL” (the same folder).
Equivalent commands are:

```bash
.venv/bin/python src/run.py batch \
  --manifest configs/travel_insurance/manifest.json \
  --schema configs/travel_insurance/canonical_schema_v1.json \
  --categories pds \
  --output-dir outputs/travel_insurance/extractions/run20

.venv/bin/python src/run.py storage-load-batch \
  --manifest configs/travel_insurance/manifest.json \
  --artifact-dir outputs/travel_insurance/extractions/run20
```

- `--categories pds` excludes auxiliary TMD/FSG folders.
- `--output-dir` mirrors `<insurer>/pds/<name>.json`. PDFs with existing results
  there are skipped when rerunning the same command after interruption. Use a
  fresh folder for a new batch/schema/model experiment.
- Batch storage uses one transaction per artifact, derives insurer from the
  recorded source path, and skips `errors/`. Multiple results for one PDF are all
  rejected; select one result before retrying. It prints per-file outcomes and a
  loaded/failed/total summary; any failure gives a nonzero exit status.
- Both UI operations are synchronous with a two-hour limit. Estimate large-batch
  duration using a small sample first.

### F. Optional LLM judge and human review

This independent post-extraction step screens PDF evidence and values. Successful
storage means structure/mapping was acceptable, not that facts are correct. The
judge receives parsed pages, approved field definitions, and extraction JSON,
and prioritizes possible errors or unsupported claims for humans. It does not
change values, re-extract, approve schemas, or block loading. Initial gold labels
are not required.

```bash
.venv/bin/python src/run.py quality-audit \
  --manifest configs/travel_insurance/manifest.json \
  --artifact-dir outputs/travel_insurance/extractions/run20 \
  --output-dir outputs/travel_insurance/quality/run20 \
  --sample-rate 0.05 --seed 42

.venv/bin/python -m streamlit run src/ui/quality_review_app.py -- \
  --quality-dir outputs/travel_insurance/quality/run20
```

In Travel's “Quality audit (LLM judge)” form, set the extraction artifacts folder
to batch `--output-dir` and choose a new, nonexistent quality folder for a first
run. Audit mode offers new run, resume, or JSON-only rebuilding without model
calls. Blank Schema/Source PDF root uses manifest defaults. Provider/model follow
normal selection; parser defaults to each extraction record. Pass sample rate is
`0.05`, seed `42`.

Use successful artifacts from one batch and one Canonical version. Set Source
PDF root explicitly if needed. The command reads PDFs locally and sends bounded
parsed text and extraction values, not PDF files. Long documents may need explicit
`--max-document-chars` and `--max-extraction-chars`, within model context limits;
overflow fails rather than silently truncating. Start with a small batch.

Document-level input/structured-output failures record safe `failure_kind` /
`error_code` and continue. Three new document failures stop the run by default
to limit cost. Unknown provider/runtime errors stop further model calls immediately
while preserving completed JSON/reports.

After failure, keep the same directory and use matching schema/provider/model
and parameters with `--resume`. It verifies and skips completed reports, retrying
unfinished ones. `--summary-only` can rebuild `results.json` without API credentials
or calls; absent historical failure reasons remain pending instead of being guessed.
These resume rules apply to identity-consistent runs; see the migration limitation
for [relocated historical artifacts](project-layout.md#historical-artifacts).

`results.json` is atomically updated after each PDF and includes complete successful
reports, safe failures, and pending entries. The review UI can display/download
it before a final queue exists. Only a fully valid batch gets `review_queue.json`
and human decisions; partial results are not a 19-document accuracy estimate.

For each review item, open the indicated source PDF/page, compare the quote's
context and extracted value, then choose `Issue found`, `No issue`, or `Uncertain`
and enter reviewer details. Issues/uncertainty require notes. The queue includes
all judge alerts/uncertainties and a seeded sample of judge passes.
`review_decisions.json` stores human conclusions separately; original extraction
and DB values are unchanged. Confirmed/dismissed alerts and sampled-pass misses
are calibration signals, not accuracy.

## 6. Outputs, quality, and cost

### Finding artifacts

| File / directory | Purpose |
| --- | --- |
| `outputs/<vertical>/schemas/schema*.json` | One-shot discovery results |
| `<experiment>/round_N/schema_draft.json` | Draft retained when consensus runs |
| `<experiment>/round_N/schema*.json` | Schema used for the round; resumed versions may have suffixes |
| `<experiment>/round_N/consensus/` | Patches, votes, queue, decisions, and reviewed schema |
| `<experiment>/round_N/extractions/` | Health holdout extractions |
| `<experiment>/round_N/refinement_feedback.json` | Health round feedback |
| `<experiment>/final_schema*.json` | Published experiment schema; use the printed version |
| `outputs/<vertical>/extractions/` | CLI single/batch extraction results |
| `outputs/travel_insurance/quality/<run>/` | Results JSON, per-PDF reports, completed review queue/decisions, usage log |
| `errors/<stage>/` | Failures below the corresponding output directory |
| `.cache/pdf/<vertical>/` | Reusable content/configuration-based parser cache |
| `data/markdown/<vertical>/<parser>/` | Per-PDF prompt text for route comparison |
| `outputs/<vertical>/archive/` | Preserved bundles and historical comparisons |

Discovery, consensus, review, and holdout use envelopes with `status`, `provenance`,
`data`, and `error`. CLI extract/batch uses `ExtractionResult` with top-level
`vertical`, `schema_version`, `source_path`, and `data`, but no envelope `status`.
File existence alone is not validation. Use the loaders in [api.md](api.md).

### Analyze completed extractions

```bash
.venv/bin/python src/schema_application/analyze.py \
  --manifest configs/private_health/manifest.json \
  --schema outputs/private_health/experiments/review-demo/final_schema.json \
  --extractions outputs/private_health/experiments/review-demo/round_1/extractions
```

Use the matching discovered schema and a dedicated results directory. This entry
point does not accept Canonical schemas. `--feedback-out new-file.json` saves feedback.
Fill rate counts populated applicable fields, not correct values. Health uses
trusted directory categories, so model `product_type` guesses do not change the
denominator. Travel lacks product labels; classification accuracy and product-specific
fill rates are N/A, while universal fields remain measurable. Multiple products
expand into multiple records, so record count can differ from PDF count.

Compare two existing schemas from the same vertical without model calls:

```bash
.venv/bin/python src/stability/compare.py \
  --manifest configs/private_health/manifest.json \
  --schemas outputs/private_health/schemas/schema.json outputs/private_health/schemas/schema_1.json
```

Stability measures changes in fields, product types, and taxonomies, not extraction
accuracy. `src/stability/measure.py` repeatedly calls discovery on a fixed sample;
budget for it separately.

### Inspect cost

```bash
.venv/bin/python src/cost/estimate.py \
  --manifest configs/private_health/manifest.json \
  --log outputs/private_health/experiments/review-demo/token_usage.jsonl
```

Loop discovery/proposal usage stays at the experiment root; holdout usage is in
`round_N/extraction_usage.jsonl`. CLI extract/batch global usage now lives at
`outputs/<vertical>/logs/extraction_usage.jsonl`, and one-shot discovery at
`logs/discovery_usage.jsonl`. One experiment may require multiple logs. Each
logical request allows the initial generation and at most two structural repairs;
repairs consume additional tokens.

Built-in estimates are not bills. Supply verified per-million-token rates with
`--input-rate` and `--output-rate` when precision matters, and account for different
rates in mixed-model logs. Never commit usage logs.

## 7. Editing manifests and prompts

Each vertical has a manifest. All editable model text remains in
[prompts/](../prompts/README.md):

```text
configs/<vertical>/manifest.json
prompts/<vertical>/discovery.md
prompts/<vertical>/patch.md
prompts/<vertical>/extraction.md
prompts/travel_insurance/quality_audit.md
prompts/shared/*.md
```

Manifests define paths, capabilities, document categories, product types,
taxonomies, identity fields, and consensus/review policy. Discovery prompts
propose the shared schema; patch prompts propose changes; extraction prompts
explain domain semantics. Travel quality prompts define judge rules;
`shared/quality_audit_request.md` arranges field definitions, values, and pages.

Inspect the existing manifests first; do not add duplicate Python vertical/prompt
lists. Validate changes offline with `load_vertical_manifest()`, then use a small
experiment. JSON Schema and business checks remain authoritative; prompts cannot
relax contracts. Preserve runtime `{name}` placeholders in shared templates.
Use new experiment directories after prompt changes; resuming old results does
not evaluate a new prompt.

There is no alias-management/synonym-merging feature or third vertical in this
scope. A future vertical fitting the existing data/workflow model can use another
configuration package; new acquisition protocols, evaluators, or stages may still
need code. Do not build a general rule language solely to claim zero-code extension.

## 8. Troubleshooting and recovery

| Symptom | Action |
| --- | --- |
| Operation absent from UI | Check vertical capability; Health has no storage, Travel has no labelled evaluation |
| Key missing after editing `.env` | Inject the environment into the launching process; the CLI does not read `.env`; do not print keys |
| Model/input rejected | Check local capability configuration; use Markdown input and explicit non-OpenAI model names |
| `Not enough unique PDFs` | Check category folders, insurer count, content deduplication, and holdout exclusions; reduce sample size or add data |
| Empty PDF text or misaligned tables | Inspect parsing offline; compare MinerU for scans/complex tables before a model experiment |
| MinerU missing or models fail to load | Install approved requirements in the venv and provide local pipeline models via download tooling or `~/mineru.json` |
| MinerU reports no text/table content | No model was called; inspect the original PDF first |
| MinerU is slow | Initial long-document parsing can be slow; later runs reuse cache; test one or two PDFs before batching |
| Repeated JSON failures | Inspect `errors/<stage>/`; align prompt, manifest, and schema; do not analyze failed data |
| `Removed structural noise before validation` | Temporary cleanup removed non-data keys or duplicate `_unfilled` items without changing extracted values; other failures remain errors |
| `Refusing to overwrite` / `FileExistsError` | Use a new file or experiment directory; do not delete audit-chain files to force reuse |
| Pending items remain after Apply | Expected: undecided proposals do not apply; the team decides when review is complete |
| `--resume-review` rejects input | Pass `round_N` with default `reviewed_schema.json` and matching queue/decisions/base |
| Historical queue cannot resume | Unbound queues are audit-only; regenerate with the current workflow or use the original version environment |
| Travel metrics are N/A | No trusted product labels exist; do not infer them from `pds` or replace N/A with zero |
| Storage rejects source/version | Check approved schema, PDF, manifest root, insurer folder, and artifact; never edit identity fields to bypass checks |
| A workflow was interrupted | Valid review permits `--resume-review`; Health feedback permits `--resume-feedback`; other cases normally need a new experiment, not arbitrary API-call resume |
| A migrated historical artifact names an old path | Locate it through the migration inventory; resuming/reloading relocated runs requires a separate identity review |

## 9. Routine verification

After code changes:

```bash
.venv/bin/python -m compileall src tests
env -u KONKRD_TEST_DATABASE_URL .venv/bin/python -m unittest discover -s tests
.venv/bin/python src/run.py --help
.venv/bin/python src/refine/loop.py --help
git diff --check
```

Before a demonstration, separately run a small approved live sample, record the
manifest, schema version, model, seeds, inputs, and output paths, and compare
values against original PDFs. Offline tests do not establish real API behavior,
PDF extraction quality, or database connectivity.

The layout migration ran offline checks only. Earlier project notes recorded a
single local MinerU PDS smoke test and acquisition-link checks without downloading
bodies; those historical checks are not a fresh verification of providers,
websites, or PostgreSQL.
