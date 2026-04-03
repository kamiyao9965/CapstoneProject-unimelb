# Konkrd Extraction Pipeline

Konkrd helps consumers compare complex products across competing brands — insurance, utilities, financial services. The barrier is that brands describe product features inconsistently across brochures, PDSs, and marketing material. This project builds a pipeline that extracts product features from those documents into structured, comparable data.

## How it works

The pipeline takes a PDF and a schema, and produces structured JSON. Two modes, same code:

- Discovery: sample docs, no schema → LLM reads the documents and proposes a draft schema → human reviews and locks it.
- Extraction: doc + locked schema → LLM extracts features into structured JSON → validate against ground truth.

## Private health insurance

The starting vertical is Australian private health insurance. This is the best vertical to start with because the government already publishes structured product data — so we have ground truth to validate against.

Read these in order:

1. [private-health.md](data/private_health/private-health.md) — How Australian health insurance works. Hospital tiers, clinical categories, extras cover, government references.
2. [konkrd-data.md](data/private_health/konkrd-data.md) — How Konkrd structures this data. Fund hierarchy, products, hospital inclusions, extras, shared limits. Includes a full walkthrough tracing one product across every table.
3. [db-reference.md](data/private_health/db-reference.md) — Column definitions and sample rows for all 8 database tables.

## What's in the repo

```
data/private_health/
├── raw/                    ← source PDFs from insurers (gitignored)
├── labelled/               ← Konkrd's structured database (CSVs)
├── private-health.md       ← how the industry works
├── konkrd-data.md          ← how Konkrd structures the data
└── db-reference.md         ← table schemas and sample rows

src/                        ← pipeline code (students build this)
outputs/                    ← extraction results
example.md                  ← worked example: one PDF → one JSON (TODO)
```

## Implemented pipeline

The project now includes:

- `src/pipeline/` — PDF ingestion, format routing, heuristic + Anthropic-backed extraction, and fuzzy normalisation.
- `src/schema/` — YAML schema loading, validation, and discovery-mode draft generation.
- `src/evaluation/` — ground-truth alignment for private health and field-level evaluation reports.
- `src/scraper/` — static and dynamic crawlers plus a deduplicating PDF downloader with `manifest.jsonl`.
- `src/run.py` — unified CLI entrypoint for crawl, discover, extract, and batch workflows.

## Usage

Install dependencies from `requirements.txt`, then run:

```bash
# Generate one extraction JSON
python src/run.py extract \
  --pdf data/private_health/raw/PDFs/HCF/hospital/HCF-Hospital-Silver-Plus.pdf \
  --schema data/private_health/schema.yaml

# Batch extract a vertical
python src/run.py batch \
  --vertical private_health \
  --schema data/private_health/schema.yaml

# Batch extract and evaluate against labelled CSVs
python src/run.py batch \
  --vertical private_health \
  --schema data/private_health/schema.yaml \
  --evaluate

# Discover a draft schema from sample PDFs
python src/run.py discover \
  --vertical travel \
  --samples data/travel/raw/PDFs/CTB/sample1.pdf data/travel/raw/PDFs/CTB/sample2.pdf

# Crawl PDFs for a configured vertical
python src/run.py crawl \
  --vertical travel \
  --config src/scraper/configs/travel_insurance.yaml
```

By default the extractor uses the offline heuristic implementation so the pipeline can run locally without API keys. If you set `ANTHROPIC_API_KEY` and pass `--provider anthropic`, the extractor will switch to schema-driven Claude tool use.

## The task

The PDFs in `data/private_health/raw/` are the input. The CSVs in `data/private_health/labelled/` are the ground truth. The pipeline's job is to read a PDF and produce output that matches the structured data Konkrd already has.

For hospital cover, this means reading a PDF and correctly identifying which of the 38 clinical categories are Covered, Restricted, or Excluded — normalised to canonical names like `BackNeckSpine`, `BoneJointMuscle`, etc.

For extras cover, this means extracting which services are covered, their waiting periods, per-person and per-policy limits, and how limits are shared across services.

The pipeline should be generic enough that adding a new vertical (travel insurance, mobile plans, utilities, home loans) is just a new directory under `data/` with a new schema. No code changes.

## Evaluation

Compare pipeline output against the labelled CSVs:

- Field-level precision — of the fields extracted, how many match ground truth?
- Field-level recall — of the fields in ground truth, how many were extracted?
- Normalisation accuracy — did "back, neck, spine" resolve to `BackNeckSpine`?
- Coverage — what percentage of schema fields have a non-null value?
- Hallucination rate — fields in the output that don't exist in the source document.

## Adding a new vertical

```
data/travel_insurance/
├── raw/           ← source PDFs
├── labelled/      ← ground truth (if available)
└── schema.yaml    ← extraction target
```

No code changes. The schema carries all the domain knowledge.
