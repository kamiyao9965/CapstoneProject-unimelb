# API and file contract reference

This document describes recommended Python integration boundaries, CLI behavior,
and JSON file contracts. The project has no HTTP/REST API. See the
[root README](../README.md), [operator guide](user-guide.md), command `--help`,
and [architecture](architecture.md).

Run Python examples from the repository root with `.venv/bin/python`. These are
recommended integration points, not a promise of indefinite version compatibility.
Keep the versions of manifests, schemas, review artifacts, and calling code together.
Internal adapters, UI session state, and database implementation details are not
application integration interfaces.

## 1. Interface map

| Requirement | Authoritative module | Network / writes |
| --- | --- | --- |
| Vertical configuration | [`src.verticals.manifest`](../src/verticals/manifest.py) | Local reads |
| Model selection | [`src.common.model_config`](../src/common/model_config.py) | Environment/config reads |
| Sampling / PDF preparation | [`src.schema.sampler`](../src/schema/sampler.py), [`src.pdf_ingestion.adapter`](../src/pdf_ingestion/adapter.py) | Local PDFs, optional parse cache and Markdown writes |
| Schema loading and normalization | [`src.schema.loader`](../src/schema/loader.py), [`src.schema.validation`](../src/schema/validation.py) | Local / memory |
| Extraction contract compilation | [`src.schema.contract`](../src/schema/contract.py) | Memory |
| Discovery / patch generation | [`src.schema.discovery`](../src/schema/discovery.py) | Model API, cache, usage and failure diagnostics |
| Extraction | [`src.schema_application.extractor`](../src/schema_application/extractor.py) | Model API, cache, optional output artifacts |
| Proposal consensus | [`src.refine.consensus`](../src/refine/consensus.py) | Model API and review artifacts |
| Human decisions and Apply | [`src.refine.human_review`](../src/refine/human_review/__init__.py) | Local reads/writes |
| Applicability analysis | [`src.schema_application.analyze`](../src/schema_application/analyze.py) | Local / memory |
| Extraction parsing / identity | [`src.schema_application.records`](../src/schema_application/records.py) | Memory; no source PDF or DB access |
| Canonical approval | [`src.schema.canonical`](../src/schema/canonical.py) | Memory |
| PostgreSQL | [`src.storage.service`](../src/storage/service.py) | Local preflight; DB access for initialization/loading |
| Strict JSON / persistence | [`src.common.json_codec`](../src/common/json_codec.py), [`src.common.json_artifacts`](../src/common/json_artifacts.py) | Memory / local IO |

Pass the same explicit `VerticalManifest` throughout an integration. Do not guess
verticals, paths, cardinality, or taxonomies independently. Configuration comes
from `configs/*/manifest.json`; model prompts are in [prompts/](../prompts/README.md).
Do not reinstate static vertical/prompt lists in Python.

## 2. Manifest and model selection

```python
from src.verticals.manifest import discover_manifests, resolve_manifest
from src.common.model_config import resolve_selection

manifests = discover_manifests()
manifest = resolve_manifest(
    "configs/travel_insurance/manifest.json", operation="extract"
)
selection = resolve_selection(
    provider="openai", model="gpt-5", document_input="markdown"
)
assert manifest.vertical == "travel_insurance"
assert manifest.documents.output_cardinality == "multiple"
```

This example is offline and creates no model request.

### `src.verticals.manifest`

| Interface | Return / behavior |
| --- | --- |
| `discover_manifests(config_root=None)` | `dict[str, VerticalManifest]`; scans `*/manifest.json`, validates configuration, rejects duplicate codes |
| `load_vertical_manifest(path)` | Validated manifest; checks version, resource paths, and contract consistency |
| `resolve_manifest(path=None, *, vertical=None, operation=None)` | Selects configuration and checks capability; explicit path and vertical must agree |
| `default_manifest_path(vertical)` | `Path` discovered from configuration packages, not a hard-coded mapping |

With no explicit selection, Health is preferred. If an operation is unsupported
by the default vertical, the unique eligible vertical can be selected instead;
ambiguous selection fails. Automation should always pass a manifest.

`VerticalManifest` is a frozen dataclass. Common members are:

- `vertical`, `display_name`, `manifest_version`, `source_path`.
- `product_types`, `taxonomies`, `identity_fields`.
- `documents.categories`, `document_types`, `extraction_unit`,
  `output_cardinality`, `category_product_types`.
- `consensus_runs`, `promoted_decisions`, `manual_only_queue`, `protected_fields`.
- `supports(capability) -> bool` and `require_capability(capability) -> None`.
- `path(name) -> Path`, `contract(name) -> str`, `adapter(name) -> str`.
- `prompt(stage) -> str`: a validated **prompt file path**, not its text. Bundled
  manifests use `prompts/<vertical>/`; custom manifests may use package-local
  paths. `src.verticals.registry.get_prompt(path)` reads text;
  `get_shared_prompt(name)` reads templates from `prompts/shared/`.

Required paths are `input_root`, `markdown_root`, `cache_root`, and `output_root`.
The bundled layouts are `data/pdf/<vertical>`, `data/markdown/<vertical>`,
`.cache/pdf/<vertical>`, and `outputs/<vertical>`. Health's optional
`KONKRD_DATA_ROOT` points directly to a data root with `pdf/`, `markdown/`, and
`labelled/` subdirectories. See [layout migration](project-layout.md).

Missing resources, wrong verticals, path escapes, and unsupported operations raise
`ManifestValidationError`, a `ValueError` subclass. Adapters are limited to
registered implementations; manifests do not execute arbitrary Python code.

### `src.common.model_config`

```text
ModelSelection(provider: str, model: str, document_input: str)
resolve_selection(*, provider=None, model=None, document_input=None,
                  environment=None) -> ModelSelection
resolve_api_key(selection, environment=None) -> (key | None, environment_name)
require_structured_output_capability(selection) -> StructuredOutputCapability
```

Precedence is explicit arguments, process environment, then defaults. Defaults
are `openai / gpt-5 / markdown`; other providers require a model name. Variables
are `LLM_PROVIDER`, `LLM_MODEL`, and `LLM_DOCUMENT_INPUT`, with `OPENAI_MODEL` as
an OpenAI fallback. See [credentials](user-guide.md#2-installation-and-environment).
`.env` is not loaded automatically. Never print the key from `resolve_api_key()`.

`require_structured_output_capability()` checks provider/model/input support
against [model_capabilities.json](../configs/model_capabilities.json); its result's
`.mode` identifies structured-output mode. `resolve_selection()` does not verify
remote account access. Discovery and extraction workflows accept only `markdown`.

## 3. Schema and extraction models

The shared discovered-schema shape is shown below; this is illustrative, not
valid JSON to submit:

```text
{
  vertical, version, description,
  product_types: [product-type strings, ...],
  fields: [{name, type, description, applies_to, required, values}, ...],
  taxonomies: {taxonomy-name: [{canonical_name, description}, ...]},
  notes: [strings, ...]
}
```

`product_type` is a classifier in `fields`; Travel no longer uses a separate
`product_type_field`. Taxonomy keys come from the manifest. Field types are
`string / number / boolean / enum / list[object]`; names must be unique snake_case,
and `applies_to` references this vertical's product types. New aliases and
automatic synonym merging are not supported.

```text
load_schema_data(path, manifest=None) -> dict
normalize_schema(payload, manifest=None) -> dict
validate_schema_mapping(payload, *, manifest=None) -> dict
compile_extraction_contract(schema, *, data_contract=None,
    business_validator=None, output_cardinality=None, manifest=None) -> dict
validate_extraction_record(schema, payload, *, manifest) -> None
```

- `load_schema_data` accepts standalone schema JSON or a successful discovered
  schema envelope. Canonical schemas use a separate strict validation branch;
  the consuming boundary decides whether approval is required.
- `normalize_schema` validates current or supported historical contracts and
  returns a copy in the shared shape. Historical Health top-level taxonomies and
  Travel classifier/taxonomy layouts remain readable; source files are unchanged.
- `validate_schema_mapping` returns a normalized copy after classifier, product
  type, taxonomy, identity, and other business checks. Do not coerce model values
  or fill missing fields independently.
- `compile_extraction_contract` returns JSON Schema without model calls or file
  writes. Default contracts/cardinality come from the manifest; an explicit
  cardinality must agree.
- `validate_extraction_record` checks identity, applicability, and duplicate
  identities in multi-product output. Pair it with JSON Schema validation.

Health returns one object. Travel returns `{"products": [...], "_document_notes": null}`
with at least one product. Each product contains all schema fields, `_unfilled`,
and `_notes`, with no extra top-level fields. Discovered fields allow `null`;
business `required: true` does not necessarily compile to non-null values and
also drives missingness analysis. Manifest identity fields have separate nonempty
constraints. Objects inside `list[object]` remain open; there is no recursive
field DSL yet.

When product type is known, inapplicable fields cannot have non-null values.
Multi-product identities are compared after trimming and case folding; this
comparison does not rewrite the returned values.

## 4. Sampling and PDF ingestion

```text
select_samples(input_root: Path, categories=None, per_category=5,
               seed=None, exclude_paths=()) -> list[str]
category_from_path(path, categories=None) -> str | None
document_identity(path) -> str
```

These live in `src.schema.sampler`. Sampling selects different insurers per
category and deduplicates by content hash. `exclude_paths` excludes both paths
and identical content. Insufficient directories, insurers, or unique content
raise `ValueError`. Pass `manifest.documents.categories` explicitly so Travel
does not accidentally use Health defaults.

```text
render_pdf_paths_for_prompt(pdf_paths, *, cache_dir=None, pdf_root=None,
                            camelot_enabled=True,
                            document_parser="pdfingestor",
                            markdown_dir=None) -> str
save_document_markdown(document, markdown_dir, *, document_parser,
                       pdf_root=None) -> Path
document_markdown_path(markdown_dir, document_parser, source_path,
                       pdf_root=None) -> Path
ingest_pdfs(pdf_paths, *, cache_dir=None, pdf_root=None,
            camelot_enabled=True,
            document_parser="pdfingestor") -> tuple[ParsedPDF, ...]
build_ingestor(cache_dir=None, *, camelot_enabled=True) -> PDFIngestor
DOCUMENT_PARSERS = ("pdfingestor", "mineru")
```

These live in `src.pdf_ingestion.adapter`. `pdf_root` resolves relative paths
that are not otherwise found. Local parsing does not call an LLM. Cache identity
includes PDF content and parser configuration. Output retains pages, text blocks,
and tables; Camelot is an optional fallback. Visual extraction requires an
explicitly injected `vision_page_extractor`; workflows do not enable it automatically.

Unsupported `document_parser` values raise `ValueError`:

- `pdfingestor` (default): the existing pdfplumber route.
- `mineru`: [`MinerUIngestor`](../src/pdf_ingestion/mineru.py) runs a local MinerU
  pipeline subprocess and converts output to the same `ParsedPDF`. Local models
  are required; no HTTP service or PDF upload is used. Empty content fails before
  a provider request. See [parser setup](user-guide.md#choosing-a-pdf-parser).

Both routes share a cache directory with parser-specific keys. The standalone
adapter defaults to `.cache/pdf/shared/`; workflows use manifest `cache_root`.
On cache reuse, the in-memory source path and PDF name describe the current input,
even after a file move. Stored cache bytes are not rewritten just to relocate it.

When `markdown_dir` is provided, the exact per-PDF prompt text is saved as
`<markdown_dir>/<document_parser>/<path-relative-to-pdf_root>.md`. PDFs outside
`pdf_root` use `<stem>_<first-12-path-hash-characters>.md`. Unchanged text is not
rewritten. Without `markdown_dir`, the adapter writes no Markdown file.

Inspect structures with `PDFIngestor.ingest(pdf_path, *, use_cache=True) -> ParsedPDF`
or `MinerUIngestor(cache_dir).ingest(pdf_path)`; types are in
[`models.py`](../src/pdf_ingestion/models.py). Missing files or parser failures may
raise filesystem/library exceptions; there is no single guaranteed exception type.

## 5. Discovery and extraction

Constructors accept a manifest, model selection, injected provider, and operational
settings. Prompts, contracts, validators, and cardinality come from the manifest
and cannot be overridden separately in constructors.

### `SchemaDiscovery`

```text
SchemaDiscovery(*, selection=None, provider=None, cleanup_uploaded_files=True,
    timeout_seconds=600.0, usage_log_path=None, log=print, request_params=None,
    extra_instructions=None, background=True, poll_interval=5.0, pdf_root=None,
    pdfingestor_cache_dir=None, document_parser="pdfingestor",
    parsed_markdown_dir=None, manifest=None)
```

| Method | Return | Files and failures |
| --- | --- | --- |
| `discover(sample_pdfs, output_path=None, *, run_id=None)` | Validated, normalized schema `dict` | Does not save successful schemas itself; `output_path` supplies diagnostic context, and the CLI writes the success envelope |
| `discover_patches(sample_pdfs, current_schema, output_path=None, *, run_id=None)` | Validated patch-set `dict` | Checks base schema and refinement capability; failed proposals cannot be returned as success |

Without a manifest, `resolve_manifest()` selects the default; integrations should
pass one explicitly. Both engines use `resolve_selection()` when selection is
omitted, following the CLI's `LLM_*` rules. An explicit selection wins. Inject a
`ModelProvider` for tests; to inject an SDK client, use
`create_provider(selection, client=...)` first.

### `SchemaExtractor`

```text
SchemaExtractor(schema_data, *, selection=None, provider=None,
    cleanup_uploaded_files=True, timeout_seconds=600.0, usage_log_path=None,
    log=print, background=True, poll_interval=5.0, pdf_root=None,
    pdfingestor_cache_dir=None, document_parser="pdfingestor",
    parsed_markdown_dir=None, manifest=None)
```

Without a manifest, the schema's vertical selects it; an explicit manifest must
agree. Configuration arguments are keyword-only; `schema_data` may be positional.

Both engines pass `document_parser` to the shared renderer and record it in
success/failure provenance and usage logs. `pdfingestor_cache_dir` defaults to
manifest `cache_root`; `parsed_markdown_dir` defaults to manifest `markdown_root`
and is passed as renderer `markdown_dir`. Explicit directory arguments still win.

| Method | Return | Files and failures |
| --- | --- | --- |
| `extract_one(pdf_path, *, run_id=None)` | Extraction `dict` | Does not save success; not an `ExtractionResult` or envelope |
| `extract_many(pdf_paths, out_dir)` | `list[Path]` of successes | Writes per-PDF extraction envelopes with collision-safe names; on failure, writes diagnostics, raises, and stops later documents |

Accepts a shared discovered schema or approved Canonical Schema. Model output
must pass structural and business checks. Set `usage_log_path` when costs must
be aggregated. `extract_one` checks the given path exists before rendering;
use an absolute path or one resolvable from the working directory, not only
`pdf_root` as an implicit prefix.

Legacy independent `model`, `vertical`, prompt, contract, validator, and cardinality
overrides are no longer accepted. Pass `selection` and `manifest`, and inject a
provider for tests. Keep vertical rules in manifests/contracts.

## 6. Providers and bounded structural repair

`src.common.model_provider`:

| Type / function | Contract |
| --- | --- |
| `ModelProvider` | Protocol: `generate(request: ProviderRequest) -> ModelResponse` |
| `create_provider(selection, *, client=None)` | Selects OpenAI, Anthropic, or DeepSeek adapter; does not verify remote access |
| `StructuredOutputSpec(name, schema, strict=True)` | Provider-neutral output specification |
| `ProviderRequest` | Required: `selection, system_prompt, user_text, document_paths, timeout_seconds, cleanup_documents, request_params, background, poll_interval`; optional: `log=None, structured_output=None` |
| `ModelResponse` | `text, provider, model`; optional `response_id, usage, api_key_env` |
| `ModelUsage` | `input_tokens, output_tokens, total_tokens`, each possibly `None` |

`src.common.structured_output`:

```text
run_structured_output(provider, request, *, data_contract=None,
    data_contract_schema=None, business_validator=None,
    max_repair_attempts=2, drop_structural_noise=False) -> StructuredOutputResult
```

Requires `request.structured_output` and exactly one data-contract argument. The
sequence is strict JSON, JSON Schema, then optional business validation, with at
most two repairs after the first request by default. Returns `data` and per-call
`attempts`, including sequence, response, and validation errors. Refusals can stop
earlier; network exceptions need not enter structural repair.

With `drop_structural_noise=True`, pre-validation cleanup removes undeclared
double-underscore keys from closed objects (`__typename`, `__proto__`), corrects
misspelled declared keys such as `__document_notes__` only when the correct key
is absent, and deduplicates string arrays marked `uniqueItems`, such as `_unfilled`.
Every cleanup is logged through `request.log`. Other violations still fail.
`SchemaExtractor` currently enables this temporary mitigation.

Exhausted repair raises `StructuredOutputFailure`; `.result` retains attempts
and errors. Invalid output is never successful data. Transport/SDK retry is
separate from structural repair, so three logical attempts are not a fixed cost cap.

## 7. Consensus and human review

```text
SchemaConsensusRefinement(discovery, log=print, *, manifest=None)
  .refine(base_schema_path, input_root=None, categories=None,
          per_category=5, runs=None, seed=None, samples=None,
          base_sample_paths=(), output_dir=None,
          alias_config_path=None) -> ConsensusOutputs
```

`runs=None` uses the manifest. This calls models and writes patches, consensus
schema, frequency, stability, and review queue artifacts. Result fields are
`patch_dir, consensus_schema_path, frequency_path, report, stability_path,
queue_path, decisions, schema_build_samples`. Here `decisions` means aggregated
`FieldDecision` objects, not the human-decision file.

Votes count distinct proposal runs. Default thresholds are core ≥ 0.8,
conditional ≥ 0.5, candidate ≥ 0.2, otherwise noise; promotion also checks conflicts,
protected fields, and manifest policy. Health defaults to one run, Travel to five.
Nonempty `alias_config_path` is rejected; it remains only to reject legacy calls.

### Review file interfaces

All module prefixes below are `src.refine.human_review`.

| Interface | Behavior |
| --- | --- |
| `queue.load_review_queue(path, *, data_contract="schema_refinement/review_queue")` | Validated queue data |
| `decisions.load_review_decisions(path)` | Decision data |
| `decisions.empty_decisions(reviewer="", *, queue=None)` | New callers should pass queue to bind identity |
| `decisions.save_review_decision(path, item_id, action, reviewer_notes="", edited_update=None)` | Recommended update boundary; reads neighboring queue, validates identity, merges under lock, saves, returns data |
| `decisions.remove_review_decision(path, item_id)` | Removes decision and restores Pending; returns data |
| `decisions.derive_status(queue, decisions_payload)` | Validates identity, returns `dict[item_id, status]` |
| `apply.apply_review(queue, decisions_payload, base_schema, *, allowed_product_types=None)` | In-memory apply; returns `(reviewed_schema, summary)` |
| `apply.apply_review_files(consensus_dir, base_schema_path=None, output_path=None)` | Loads bound files, writes reviewed artifact, returns `(Path, summary)` |

Actions are `accept / reject / edit`; Edit requires a complete valid field in
`edited_update`. Summary groups item IDs into `applied / edited / rejected / pending`.
Pending and rejected items do not apply. Rename/merge/move cannot masquerade as
field upserts; historical `add_alias` is audit-only.

Identity includes queue ID, vertical, schema version, and base-schema hash. Do
not invent another identity or overwrite the base. `write_review_decisions()` is
a low-level whole-file writer; interactive callers should prefer incremental
saves so stale memory cannot overwrite newer decisions.

Default reviewed output is `consensus/reviewed_schema.json`, with overwrite refused.
The loop's `--resume-review` reads this exact filename and recomputes against the
queue, base, and current decisions. A custom `--out` is not automatically used by
resume. Historical unbound queues cannot be resumed directly. Standalone consensus
now defaults to `outputs/<vertical>/experiments/consensus/`.

## 8. Applicability analysis and stability

```text
load_field_specs(schema_data, manifest=None) -> list[FieldSpec]
load_records(extraction_dir: Path, extraction_contract, manifest=None,
             *, schema_version=None) -> (list[ExtractionRecord], failed_count)
analyze(records, specs, *, failed_artifacts=0) -> Analysis
print_report(analysis) -> None
build_feedback_data(analysis) -> dict
build_feedback(analysis) -> str
```

These live in `src.schema_application.analyze`. Pass the discovered schema,
manifest, and version used for extraction; `load_field_specs` does not accept
Canonical schemas. `load_records` accepts envelopes and CLI `ExtractionResult`,
excludes `errors/`, and rejects unknown/ambiguous source categories. Declared
vertical/version must agree; historical records missing some provenance remain
readable for analysis.

`ExtractionRecord` contains `data / source_document / source_category`.
Multi-product output expands to multiple records. Applicability uses trusted
manifest category mappings, never the model's classification as truth. Travel
has no product labels, so classification accuracy and product-specific field
metrics are N/A. `build_feedback_data` returns only data; CLI `--feedback-out`
can persist an envelope. Travel has no loop evaluation/feedback resume capability;
a standalone report does not enable that loop.

`src.stability.signature.signature_from_file(path, *, manifest=None) -> SchemaSignature`
extracts semantic signatures of fields, product types, and all taxonomies.
`signature_from_text(text, label, *, manifest=None)` and
`signature_from_artifact(artifact, label, *, manifest=None)` are also available.

`src.stability.compare.compare(signatures, show_items) -> float` prints comparisons
and returns overall stability; cross-vertical comparisons fail. Stability is not
value accuracy. Measurement defaults to `outputs/<vertical>/experiments/stability/`.

## 9. Canonical schemas, acquisition, and PostgreSQL

### Canonical schemas

| `src.schema.canonical` interface | Return / constraints |
| --- | --- |
| `build_canonical_candidate(payload, manifest, *, unmapped_storage="jsonb")` | Candidate copy from discovered schema; requires storage capability and uses the manifest Canonical schema as a template. Unmapped fields use `jsonb` by default, or `extension_column` named after the field; lists, reserved names, and occupied names still use JSONB. Other choices raise `ValueError` |
| `validate_canonical_schema(payload)` | Validated copy; allows valid candidate/approved states |
| `approve_canonical_schema(payload, *, reviewer, rationale, reviewed_at=None)` | Approved copy with review/content binding; no file, model, or DB access |
| `require_approved_canonical_schema(payload)` | Checks approval and content binding; returns a copy |
| `compile_canonical_extraction_contract(payload)` | Extraction JSON Schema from an approved contract |
| `validate_canonical_extraction_identities(schema_payload, extraction_payload)` | Checks product container and nonempty, unique product-name identities; returns original extraction. Validate complete structure separately with the compiled contract |

Incompatible identity-field storage types prevent automatic candidate creation.
New ordinary fields default to JSONB or can request extension columns. Approval
is more than changing `status`; changed content requires new review. Empty aliases
fields retained in existing Canonical contracts do not restore alias functionality.

### Acquisition

`src.scraper.travel.run_travel_acquisition(*, config_path, data_root, output_root, insurer_codes=(), include_archived=False, discovery_only=False, http_client=None, run_id=None) -> AcquisitionOutcome`.

Visits configured public sources and writes acquisition artifacts; download mode
also writes PDFs. `discovery_only=True` still performs network access and metadata
writes. Inject `http_client` for tests. Returns `artifact_path / data / pdf_paths`.
Generic workflows invoke the registered manifest acquisition adapter, not another crawler.

### Storage service

```text
resolve_database_url(environment_name="KONKRD_DATABASE_URL") -> str
prepare_storage_load(*, manifest, schema_path, artifact_path,
                     insurer_code) -> PreparedStorageLoad
initialize_storage(*, database_url, manifest, schema_path) -> None
load_extraction_artifact(*, database_url, manifest, schema_path,
                        artifact_path, insurer_code) -> StorageLoadSummary
load_extraction_directory(*, database_url, manifest, schema_path,
                          artifact_dir, load_one=None) -> list[DirectoryLoadResult]
```

`prepare_storage_load` reads the approved schema, artifact, and source PDF, checks
boundaries, and compiles a load plan without connecting to PostgreSQL. The PDF
must exist and satisfy manifest input-root, insurer, and document-type constraints.

Both extraction formats must carry `vertical` and `schema_version` matching the
manifest/approved schema, plus provider/model/run identity. Historical envelopes
without identity can support compatible analysis but cannot be loaded directly;
re-extract with the approved schema instead of inferring identity from selection.

`initialize_storage` creates missing core/vertical tables transactionally; it does
not migrate existing tables. `load_extraction_artifact` preflights, then writes in
one transaction. Repeated identities must pass consistency checks; conflicts do
not silently overwrite. Success includes `run_id / document_id / schema_version_id /
products_loaded / release_ids`. Services manage and dispose engines; they return
no live connection. SQLite is unsupported.

`load_extraction_directory` recursively reads `*.json`, skips `errors/`, derives
insurer code from source-PDF paths, and loads each artifact in a separate transaction.
If multiple results identify the same source PDF, all such results are skipped.
Returns path-sorted `DirectoryLoadResult(artifact_path, insurer_code, summary, error)`:
`summary` for success or `error` for failure. One failure does not prevent others.
Missing/empty folders raise `ValueError`; `load_one` is test injection only.

### Optional Travel quality audit

```text
audit_one(*, manifest, schema_path, artifact_path, source_root,
          selection, provider, usage_log_path=None,
          document_parser=None, max_document_chars=120000,
          max_extraction_chars=60000) -> quality_audit envelope
run_quality_audit(*, manifest, schema_path, artifact_dir, source_root,
                  output_dir, selection, provider, sample_rate=0.05,
                  seed=42, document_parser=None,
                  max_document_chars=120000,
                  max_extraction_chars=60000, resume=False,
                  summary_only=False, max_failures=3) -> QualityAuditRun
load_quality_results(results_path) -> quality_batch_results envelope
build_review_queue(reports, *, sample_rate, seed) -> quality_review_queue envelope
load_queue(queue_path) -> quality_review_queue envelope
load_decisions(queue_path, decisions_path) -> quality_review_decisions envelope
save_decision(queue_path, decisions_path, item_id, decision, notes, reviewer) -> Path
quality_metrics(queue, decisions) -> dict
```

`src.evaluation.quality` requires matching approved Canonical/extraction
vertical/version, revalidates structure and source path/hash, and sends only
PDFingestor/MinerU text, not PDF uploads. The injected provider goes through
`run_structured_output` with at most two repairs. Verdicts are `pass/review/uncertain`
with correctness, evidence-support, and uncertainty ratings. `citation_verified=true`
means the quote occurs in parsed content on the claimed page, not that the fact
has been established. `src.schema.sampler.sample_quality_passes` selects reproducible
pass samples. `src.evaluation.quality_review` binds human decisions to the queue
and allows only independent `issue_found/no_issue/uncertain` records. It never
changes extraction, schemas, or PostgreSQL. `quality_metrics` has workload and
human-feedback signals, no accuracy field.

`run_quality_audit` saves per-PDF progress to `results.json`; `QualityAuditRun`
returns `results_path`, optional `queue_path`, and completed/failed/pending counts.
The JSON embeds complete successful reports or safe failure kinds/codes, never
raw model responses. A first run requires a new directory. `resume=True` checks
extraction/schema/prompt/PDF hashes and model identity, skips valid reports, and
retries missing/failed items. `summary_only=True` rebuilds JSON from artifacts
without a provider or source-PDF reads. Unrecorded historical failure causes
remain pending rather than being inferred. The default allows three new document
failures before stopping. Only a complete valid batch gets a bound review queue.

New reports optionally save `prompt_bundle_sha256` over the main judge prompt
and shared request, repair, and DeepSeek format templates. Legacy reports lacking
it can resume only while shared templates still match the recorded migration baseline.

Contract names are `quality/judge_result`, `quality/audit_report`,
`quality/batch_results`, `quality/review_queue`, and `quality/review_decisions`.
The separate `quality_usage.jsonl` records each attempt's `failure_kind` and
`error_code`, without raw output. Relocated historical runs have the additional
restrictions described in [project layout](project-layout.md#historical-artifacts).

## 10. JSON file boundaries

### Strict JSON and contracts

`src.common.json_codec.loads_json()` rejects duplicate keys, non-JSON constants,
and invalid input. It does not accept Markdown, YAML, or inferred type fixes.
Use `dumps_json()` for output. `src.common.json_contracts` provides:

```text
load_contract(name, *, manifest=None) -> dict
validate_contract(payload, name, *, manifest=None) -> Any
validate_inline_contract(payload, schema, name="inline") -> Any
```

Validation returns the original payload or raises `ContractValidationError`.
Pass the manifest for dynamic discovered contracts; if omitted, configuration is
found from the vertical in the contract name. Obtain names through
`manifest.contract("discovered_schema")` and `manifest.contract("candidate_patch_set")`;
shared review contracts are in `schema_refinement/`. Never dynamically import a
validator from untrusted JSON strings.

Illustrative envelope:

```text
{
  artifact_type, contract_version, status, created_at,
  provenance: {
    run_id, provider, model, document_input,
    source_documents: [...], source_artifacts: [...],
    vertical?, schema_version?, document_parser?
  },
  data, error
}
```

Success has validated `data` and `error=null`; failure has `data=null` and an
error with code/message/details. Timestamps use UTC RFC 3339. `contract_version`
is the artifact data-contract version, not domain `data.version`. Current
extraction/feedback records carry vertical/schema identity; historical provenance
may omit it. `document_parser` is `pdfingestor`, `mineru`, or null, written by PDF
preparation stages (discovery, patch, holdout extraction). Older artifacts without
this field came from the then-only PDFingestor route.

### File reads and writes

```text
build_success_artifact(*, artifact_type, contract_version, data, provenance,
    data_contract=None, data_contract_schema=None, created_at=None) -> dict
write_artifact(path, artifact, *, data_contract=None,
    data_contract_schema=None, overwrite=False) -> Path
read_artifact(path, *, expected_type=None, data_contract=None,
    data_contract_schema=None) -> dict
write_text_output(path, text, *, overwrite=False) -> Path
next_available_path(path: Path, reserved: set[Path] | None = None) -> Path
```

Building/writing success requires exactly one data contract. Load dynamic
contracts with `load_contract(..., manifest=manifest)` and pass them through
`data_contract_schema=`. `read_artifact` returns the full envelope and rejects
failure, invalid JSON/envelopes, or mismatched expected types. Pass a contract to
validate data; expected type does not replace content validation.

Writes are atomic and refuse overwrite by default. `next_available_path` chooses
a name but does not reserve it. Use `build_failure_artifact(...)` and
`write_failure_artifact(output_root, stage, run_id, artifact)` for `errors/<stage>/`,
never a successful schema path.

**CLI `extract / batch` uses `src.common.models.ExtractionResult`.** Top-level
members are `vertical, schema_version, source_path, extracted_at, provider, model,
document_parser, data, evidences, normalized_names, warnings`. Missing historical
`document_parser` reads as null. Read with `ExtractionResult.model_validate(payload)`
and write with `.write_json(path)`; do not use `read_artifact`. Wrapper validation
is not extraction-data contract validation. Compatibility fields do not imply
that alias merging ran.

### Shared extraction reader

```text
parse_extraction_artifact(raw: bytes, *, vertical: str,
    schema_version: str | None = None, require_identity: bool = False
) -> ParsedExtraction
```

`src.schema_application.records` is the shared analysis/storage boundary. It
accepts both formats and validates JSON, wrapper, successful extraction type,
and exactly one nonempty source path. Conflicting vertical/schema versions fail;
`require_identity=True` also rejects missing/empty identities. Analysis uses the
compatible default; storage passes the approved version and requires identity.

The frozen result includes `artifact` (original object), `data`, `source_document`,
`vertical`, `schema_version`, `provider`, `model`, and `run_id`. Legacy
`ExtractionResult` run IDs remain SHA-256 of the original bytes for storage
idempotency; envelopes use provenance run IDs. This function neither reads PDFs
nor validates domain data; analysis/storage own those checks. Do not duplicate
format detection or infer file identity from the selected schema.

## 11. CLI boundaries

Entry points are in the [README](../README.md#cli-entry-points); procedures and
parameters are in the [operator guide](user-guide.md). Command `--help` is the
precise option reference. Manifest arguments are paths, and not every entry point
supports `--vertical`. `extract / batch` use shared model selection and Markdown
input. Deprecated `--no-fallback` does not activate a heuristic extraction route.

Success normally returns 0, failures nonzero, and argparse errors usually 2.
There is no stable universal JSON error protocol; some entry points raise exceptions.
Use Python interfaces for structured integration errors. CLI batch aggregates
per-file errors, unlike `SchemaExtractor.extract_many`, which stops on failure.

Batch `--output-dir` reuses existing results only after
`src.schema_application.records.load_cached_extraction` validates their source,
vertical/version, provider/model, parser route, runtime contract, and business
rules. Invalid files or directories cause a nonzero exit before provider creation;
existing bytes are preserved. Native results retain their evaluation metadata,
and successful envelopes are read through the same extraction boundary.

`batch --evaluate` writes `evaluation/report.json`, `evaluation/report.md`, and
matching diagnostics when needed. It no longer creates duplicate
`report_model_only.*` or includes `fallback_documents` in the summary. Historical
reports remain; readers of new reports should use `report.json`.

Default discovery output is `outputs/<vertical>/schemas/schema.json`; global
usage logs are `logs/discovery_usage.jsonl` and `logs/extraction_usage.jsonl` within
the vertical output root. Related experiment files remain together. See
[output conventions](project-layout.md#output-conventions).

## 12. Exceptions and verification scope

| Exception / condition | Caller action |
| --- | --- |
| `ManifestValidationError` | Correct configuration, resource paths, or capability selection |
| `StrictJSONError` / `ContractValidationError` | Reject input; inspect field paths; do not silently convert to success |
| `ArtifactError` | Inspect upstream file, status, type, and contract |
| `StructuredOutputFailure` | Stop dependent stages, retain usage/redacted diagnostics, correct the cause, start a new run |
| `FileExistsError` | Choose a new output path; do not assume overwrite |
| Other `ValueError` | Check schema, identity, review binding, or storage preflight |
| Provider / PDF / DB exceptions | Preserve exception chains and handle at the boundary; do not treat all as empty content |

Offline examples and CLI parsing need no credentials. Real model quality, PDF
parsing performance, website availability, and PostgreSQL transactions require
separate environments; this reference does not claim those external flows were run.
