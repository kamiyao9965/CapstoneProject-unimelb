# Project layout and migration

The 2026-10-01 layout groups data by format and runtime results by business
vertical. All new commands, imports, and examples use the new paths. Old path
aliases are not installed.

## Directory responsibilities

| Directory | What belongs here |
| --- | --- |
| `src/` | Python source, with existing functional owners |
| `src/ui/` | Four Streamlit entry points, review presentation, and `tool/` controls |
| `src/pdf_ingestion/` | Shared PDF preparation, PDFingestor, MinerU, cache, and rendering |
| `src/common/` | Shared result models, provider calls, structured output, contracts, and IO |
| `configs/` | Model capabilities and per-vertical manifests, source settings, approved reference schema, and storage mapping |
| `prompts/` | Existing model-facing text; unchanged by this migration |
| `contracts/` | Application-owned JSON Schema definitions; these are validation contracts, not runtime results |
| `docs/` | English reference, procedures, architecture, decisions, specs, and historical reviews |
| `tests/` | Offline tests and injected fake providers |
| `data/pdf/<vertical>/` | Original PDFs, preserving insurer/category/name |
| `data/markdown/<vertical>/<parser>/` | Parsed Markdown mirroring the PDF input tree |
| `data/labelled/<vertical>/` | Optional labelled evaluation data; not supplied by this migration |
| `.cache/pdf/<vertical>/` | Content/configuration-addressed parse caches shared by both parser routes |
| `.cache/pdf/shared/` | Default cache for direct adapter calls without a manifest |
| `outputs/<vertical>/` | Runtime artifacts grouped as described below |
| `outputs/archive/` | The local migration inventory and preserved filesystem metadata |

The root README remains the repository entry point. `AGENTS.md` and `CLAUDE.md`
remain at the root so their tools can discover them. Requirements and Git settings
also remain at the root. `prompts/README.md` remains with the existing prompt bundle.

## Data conventions

```text
data/
  pdf/
    private_health/<insurer>/<category>/<name>.pdf
    travel_insurance/<insurer>/<document_type>/<name>.pdf
  markdown/
    private_health/legacy/<original-relative-path>.md
    private_health/pdfingestor/<insurer>/<category>/<name>.md
    private_health/mineru/<insurer>/<category>/<name>.md
    travel_insurance/pdfingestor/<insurer>/<document_type>/<name>.md
    travel_insurance/mineru/<insurer>/<document_type>/<name>.md
```

Directories are created when needed; this tree does not claim every route has
already run. The 25 pre-existing Health Markdown files have no verified parser
identity, so they are preserved under `legacy/`. The 38 Travel Markdown files
remain separated into 19 PDFingestor and 19 MinerU views.

`input_root`, `markdown_root`, `cache_root`, and `output_root` are required
manifest paths. Health's optional `KONKRD_DATA_ROOT` now denotes the data root
itself: `<root>/pdf/private_health`, `<root>/markdown/private_health`, and
`<root>/labelled/private_health`. Without it, Health uses this repository's
`data/`. Travel uses its configured paths independently.

Cache files retain their bytes. When a parser reuses a content-identical cache,
it binds the in-memory document name/path to the PDF requested now. Newly saved
Markdown therefore mirrors the new PDF location without rerunning the parser.

## Output conventions

```text
outputs/<vertical>/
  schemas/                  Discovery and approved runtime schema files
    compiled/<version>/     Extraction contract and SQL preview
  extractions/<run>/        Per-document extraction artifacts
  quality/<run>/            Judge results, reports, human review, run usage
  logs/                     Default discovery and extraction usage logs
  experiments/<experiment>/ Related schema/refinement/stability artifacts
  acquisition/<run>/        Download/discovery metadata
  evaluation/               Labelled evaluation reports
  errors/<stage>/           Local failure artifacts, where applicable
  exports/csv/              Manual database/query exports
  exports/sql/              Standalone SQL exports
  archive/                  Historical bundles and parser comparisons
```

Keep run-specific logs, queues, decisions, and schemas with their experiment;
separating them would obscure their provenance. Default discovery writes
`schemas/schema.json`, default global usage logs go to `logs/`, refinement defaults
to `experiments/refinement/`, standalone consensus to `experiments/consensus/`, and
stability measurement to `experiments/stability/`. Explicit output arguments still
choose an operator-supplied location. Use new run directories for new experiments.

## Migration map

| Former location | New location |
| --- | --- |
| `src/PDFingestor/` | `src/pdf_ingestion/` |
| `src/models.py` | `src/common/models.py` |
| `src/*_app.py` | `src/ui/*_app.py` |
| `src/tool_ui/` | `src/ui/tool/` |
| `src/refine/human_review/ui.py` | `src/ui/schema_review.py` |
| `src/evaluation/quality_review_ui.py` | `src/ui/quality_review.py` |
| Root `api.md` | `docs/api.md` |
| `data/<vertical>/raw/PDFs/` | `data/pdf/<vertical>/` |
| `data/private_health/raw/Markdown/` | `data/markdown/private_health/legacy/` |
| `outputs/<vertical>/parsed_markdown/` | `data/markdown/<vertical>/` |
| `outputs/<vertical>/pdfingestor_cache/` | `.cache/pdf/<vertical>/` |
| `outputs/<vertical>/schema.json` | `outputs/<vertical>/schemas/schema.json` |
| Root runtime `canonical_schema_approved.json` | `schemas/canonical_schema_approved.json` within its vertical |
| `outputs/travel_insurance/compiled_schema_v1/` | `outputs/travel_insurance/schemas/compiled/v1/` |
| `outputs/<vertical>/token_usage.jsonl` | `outputs/<vertical>/logs/discovery_usage.jsonl` |
| Root `extraction_usage.jsonl` within a vertical | `logs/extraction_usage.jsonl` within that vertical |
| `outputs/travel_insurance/refine/` | `outputs/travel_insurance/experiments/refinement/` |
| `outputs/travel_insurance/parser_compare/` | `outputs/travel_insurance/archive/parser_comparison/` |
| Loose SQL/CSV output | `exports/sql/` or `exports/csv/` within its vertical |
| `experiments.zip` | `archive/experiments.zip` within its vertical |
| PDF-tree `Archive.zip` | `outputs/travel_insurance/archive/source_bundles/Archive.zip` |

The local, ignored inventory at
`outputs/archive/layout-migration-2026-10-01.json` records every existing data/output
file plus orphaned Python bytecode: old path, new path, size, SHA-256, and whether
it moved. All 1,580 inventoried files passed preservation checks; 1,514 moved.
No file was discarded. `.DS_Store` files are preserved under
`outputs/archive/filesystem_metadata/`; bytecode-only retired source directories
were relocated to `.cache/legacy_python/`. An existing filename ending in `.pdf;`
is retained unchanged and remains outside normal `*.pdf` discovery.

## Historical artifacts

Historical source paths, schema approvals, hashes, timestamps, and model results
are preserved exactly as recorded. Use the migration inventory to locate their
files now. Relocated historical runs are retained for inspection; resuming them,
reloading them into PostgreSQL, or rebinding their review identities is outside
this migration. Start new runs under the new layout. Do not hand-edit historical
provenance to bypass identity checks.
