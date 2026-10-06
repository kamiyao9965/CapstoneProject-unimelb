# Australian Insurance Schema Discovery

A local Python CLI and four Streamlit pages for Australian Private Health and
Travel Insurance PDFs. One engine discovers reusable fields, refines schemas
through voting and human review, extracts versioned JSON, and supports evaluation
or PostgreSQL storage where the selected vertical allows it. There is no REST API.

Start with the [operator guide](docs/user-guide.md), [API and file contracts](docs/api.md),
and [documentation index](docs/README.md). See the [project layout](docs/project-layout.md)
for directory rules and the migration from the former paths.

## Project structure

```text
src/                        Python source, grouped by responsibility
  pdf_ingestion/            PDFingestor and MinerU parsing, rendering, cache
  ui/                       Streamlit pages and UI-to-CLI controls
  common/                   Shared models, provider calls, contracts, file IO
  schema/                   Sampling, discovery, schema validation and compilation
  schema_application/       Extraction and applicability analysis
  refine/                   Consensus, human decisions and round orchestration
  evaluation/               Labelled evaluation and optional quality audit
  storage/                  PostgreSQL compilation and loading
  scraper/                  Travel document acquisition
  stability/                Schema stability measurement and comparison
  cost/                     Token cost analysis
  verticals/                Manifest discovery and validation
  run.py                    Main CLI
configs/<vertical>/         Manifest and related business configuration
prompts/                    Existing model prompts and shared templates
contracts/                  Application-owned JSON Schema contracts
docs/                       English project documentation
tests/                      Offline tests
data/pdf/<vertical>/        Source PDFs, then insurer/category
data/markdown/<vertical>/   Derived Markdown, then parser/insurer/category
outputs/<vertical>/         Schemas, extractions, quality, logs and experiments
.cache/pdf/<vertical>/      Reusable parser caches
```

Data, outputs, caches, credentials, and local agent notes are not committed.

## Installation

Use macOS/Linux and Python 3.10–3.13; development checks use `.venv/bin/python`
(Python 3.13). Bring local PDFs and credentials for any model operation.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python src/run.py --help
```

Model selection comes from explicit arguments or the process environment:
`LLM_PROVIDER`, `LLM_MODEL`, and `LLM_DOCUMENT_INPUT`; defaults are
`openai / gpt-5 / markdown`. Credentials use `MY_OPENAI_API_KEY` or `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, or `DEEPSEEK_API_KEY`. The CLI does not load `.env`.
See [environment setup](docs/user-guide.md#2-installation-and-environment).

## Local UI

```bash
.venv/bin/python -m streamlit run src/ui/tool_app.py --server.address 127.0.0.1
```

Choose a vertical and operation, review the generated command, and confirm when
required. These pages call the existing CLI, accept no credentials, and have no
authentication boundary. Use them on a trusted local machine.

## CLI entry points

Batch `--output-dir` resumes only validated results matching the selected source,
schema, model and parser. Invalid existing results stop before model calls; use
a new folder for a different experiment. See the operator guide for recovery.
`batch --output-dir --evaluate` evaluates the complete selected batch, including
validated cached results; a fully cached evaluation makes no model requests.

| Purpose | Entry point | External effects |
| --- | --- | --- |
| Discover schema | `src/run.py discover` | Model calls |
| Extract one PDF or a batch | `src/run.py extract` / `batch` | Model calls |
| Acquire Travel documents | `src/run.py crawl` | Website access; optional downloads |
| Refine a schema | `src/refine/loop.py` | Model calls and optional review stop |
| Vote on proposals | `src/refine/consensus.py` | Model calls |
| Apply human decisions | `src/refine/review.py apply` | Local artifacts |
| Compile contract and SQL preview | `src/run.py canonical-compile` | Local files; no SQL execution |
| Create tables or load records | `storage-init`, `storage-load`, `storage-load-batch` via `src/run.py` | PostgreSQL writes |
| Audit Travel extraction quality | `src/run.py quality-audit` | Model calls; `--summary-only` is local |
| Analyze existing extractions | `src/schema_application/analyze.py` | Local reads |
| Measure or compare stability | `src/stability/measure.py` / `compare.py` | Measurement calls models; comparison is local |
| Estimate cost | `src/cost/estimate.py` | Local usage-log reads |

Run commands from the repository root. For example, after preparing inputs:

```bash
.venv/bin/python src/run.py discover \
  --manifest configs/private_health/manifest.json --per-category 1 --seed 42

.venv/bin/python src/run.py extract \
  --manifest configs/travel_insurance/manifest.json \
  --schema configs/travel_insurance/canonical_schema_v1.json \
  --pdf data/pdf/travel_insurance/allianz/pds/example.pdf
```

Replace the example PDF with a real file. Confirm costs before model calls,
downloads, or database writes. Complete workflows, independent review pages,
parser options, and recovery instructions are in the [operator guide](docs/user-guide.md).

## Workflow and validation boundaries

Health extracts one object per document and can run labelled holdout evaluation.
Travel extracts multiple products per PDS, defaults to five proposal runs, and
supports human-approved Canonical Schemas for PostgreSQL. Its optional judge and
human review run independently after extraction. Travel's `pds` folder is a
document category, not a product-type label.

Manifests define capabilities, paths, categories, and contracts. Prompts remain
in [prompts/](prompts/README.md). Outputs pass strict JSON parsing, JSON Schema,
and business validation, with at most two structural repairs after the first
request. Extraction still uses logged, temporary structural-noise cleanup;
see [open issues](docs/fix.md). Valid structure or successful storage does not
establish factual accuracy. Judge reports prioritize review and never modify
extraction values, schemas, or the database.

Quality review queues bind every audited PDF and judge input to human decisions.
Legacy `1.0.0` quality queues remain readable but are read-only; see the
[operator guide](docs/user-guide.md#f-optional-llm-judge-and-human-review) for regeneration from verified reports.

Canonical approval requires a human reviewer and rationale. Never rewrite an
approved schema in place. Runtime artifact formats and provenance requirements
are documented in [API and file contracts](docs/api.md#10-json-file-boundaries).

## Offline verification

```bash
.venv/bin/python -m compileall src tests
env -u KONKRD_TEST_DATABASE_URL .venv/bin/python -m unittest discover -s tests
.venv/bin/python src/run.py --help
.venv/bin/python src/refine/loop.py --help
git diff --check
```

Module ownership is documented in [architecture](docs/architecture.md), dependency
rules in [dependency policy](docs/dependency-policy.md), and design rationale in
[ADRs](docs/README.md#design-decisions-and-historical-material).
