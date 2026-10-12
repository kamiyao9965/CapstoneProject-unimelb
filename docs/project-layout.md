# Project layout

Keep data grouped by format and outputs grouped by insurance vertical. Use the
current paths for new work; there are no compatibility aliases for former paths.

## Directory responsibilities

| Directory | Contents |
| --- | --- |
| `src/` | Python source grouped by functional owner; see [architecture](architecture.md#package-ownership) |
| `configs/<vertical>/` | Manifests, source inventories, taxonomies and schema/mapping configuration |
| `prompts/` | Model prompts and shared request/repair templates |
| `contracts/` | Application-owned JSON Schema validation contracts |
| `docs/` | Current operator, interface, architecture, layout and dependency references |
| `tests/` | Offline tests; real PostgreSQL checks require an explicit test database |
| `data/pdf/<vertical>/` | Original PDFs, then insurer/category |
| `data/markdown/<vertical>/<parser>/` | Parsed Markdown mirroring source paths |
| `.cache/pdf/<vertical>/` | Reusable content/configuration-addressed parse caches |
| `.cache/pdf/shared/` | Parser default when called without a manifest |
| `outputs/<vertical>/` | Runtime artifacts, organized below |
| `outputs/archive/` | Preserved local metadata and historical runtime files |

`<vertical>` is `private_health`, `pet_insurance`, `travel_insurance` or
`car_insurance`. Directories are created when needed; their presence does not
prove that a workflow has run. Root README and local agent instructions remain
at the repository root. The prompt guide stays with the prompt bundle.

## Data conventions

Manifest paths `input_root`, `markdown_root`, `cache_root` and `output_root`
select the input, parsed views, cache and results. Health's optional
`KONKRD_DATA_ROOT` selects an external data root through the configured suffixes
`pdf/private_health` and `markdown/private_health`. It does not replace the
other verticals' paths. See [manifest contracts](api.md#2-manifest-and-model-selection).

Preserve original PDFs and historical parsed views. Health Markdown with unknown
parser identity remains under `data/markdown/private_health/legacy/`. Historical
labelled data may remain under `data/labelled/<vertical>/`; the unified workflow
does not run labelled evaluation. Do not infer a parser identity for old files.

## Output conventions

```text
outputs/<vertical>/
  schemas/                   Discovered and approved runtime schemas
    compiled/<version>/      Extraction contract and SQL preview
  extractions/<run>/         Extraction artifacts, each binding its complete PDF group
  quality/<run>/             Judge reports, review decisions, delivery proofs and usage
  logs/                      Default discovery and extraction usage logs
  experiments/<experiment>/  Refinement, consensus and stability runs
  acquisition/<run>/         Travel discovery/download metadata
  errors/<stage>/            Failure artifacts, where applicable
  exports/csv/               Joined product CSV exports
  exports/sql/               SQL exports
  archive/                   Historical bundles and parser comparisons
```

Explicit CLI output arguments can choose another location. Default discovery
writes `schemas/schema.json`; refinement, consensus and stability use their
subdirectories under `experiments/`. Keep a run's logs, queues, decisions and
schemas together so their provenance remains inspectable. Start new runs in
new directories. Historical `evaluation/` folders are preserved, not new outputs
of the unified workflow.

## Historical artifacts

Preserve recorded source paths, hashes, approvals, timestamps and model results.
Do not hand-edit historical provenance to pass current identity checks. Before
resuming or loading an older run, verify its source paths, hashes, selected schema
and review identities. Moving a run's files does not make its recorded identity
match the current inputs.
