# Open issues

Confirmed issues that remain unresolved. Each entry records symptoms, causes,
recommended changes, and acceptance criteria. Move completed items to **Resolved**.
Directory migration does not imply that unrelated business-logic issues are fixed.

## 1. Enable strict OpenAI structured output for extraction

**Status:** Open, recorded 2026-09-16. See Resolved item 1 for the temporary mitigation.

**Symptoms**

- The complete 2026-09-15 batch in
  `outputs/travel_insurance/logs/extraction_usage.jsonl` used GPT-5, MinerU, and
  `canonical_approved.json` for 19 PDS files. It made 61 model calls: 19 eventually
  passed validation and 42 did not. At the then-standard uncached prices, failed
  calls accounted for about 68% of estimated total model cost.
- Earlier figures of 47 calls, 33 format failures, and five missing PDS results
  described an intermediate state before follow-up runs. There are now 19 results
  in `outputs/travel_insurance/extractions/run-gpt5`; use the complete log as baseline.
- Failures were structural, not judgments about extracted values: extra top-level
  `__typename` / `__proto__`, `__document_notes__` instead of `_document_notes`,
  and duplicate field names in `_unfilled`.

**Causes**

`strict` is wired through `SchemaExtractor` to `StructuredOutputSpec`, but
`src/schema_application/extractor.py` sets it false whenever it sees a
`list[object]` or a field with `required: false`. Travel has 13 lists and many
optional fields, so these requests were non-strict.

List contracts compile to `oneOf: [null, array of {"type": "object", "additionalProperties": true}]`.
Those open items do not meet strict-mode requirements. This is not a JSON parser
corrupting a Canonical Schema: the field model stores only `type: list[object]`
and a natural-language description, without item properties, types, or required
rules. `compile_canonical_extraction_contract` lacks the information needed to
close nested objects. Both Canonical representation and runtime compilation need work.

With strict mode disabled, a schema is guidance rather than enforced generation.
The extraction prompt already says `Never output "__typename"`, asserted by
`tests/test_travel_schema_migration.py`, but that instruction alone is insufficient.

**Recommended changes**

1. Extend `contracts/canonical_schema.schema.json` so `list[object]` carries an
   explicit item contract (implementation may call it `item_schema`). Include
   fixed properties, types, and nullability instead of prose alone.
2. Define closed item shapes in a new Travel Canonical candidate for all 13 lists:
   `waiting_period_rules`, `excess_rules`, `cruise_benefits`, `optional_add_ons`,
   `coverage_benefits`, `sub_limits`, `included_activities`, `optional_activity_cover`,
   `excluded_activities`, `depreciation_rules`, `general_exclusions`,
   `benefit_exclusions`, and `source_evidence`. Use `additionalProperties: false`,
   list every child in runtime `required`, and use null for absent source values.
3. Compile scalar fields as before and list `items` from the explicit item contract.
   Runtime requiredness includes every property, while nullable types represent
   business optionality. Business `required` flags need not all become true.
4. Stop silently disabling strict mode based on lists/optional fields. Compile
   the full contract, then check OpenAI projection compatibility. Approved Travel
   contracts must yield `strict: true`; incompatible contracts fail before calls
   rather than falling back to non-strict generation.
5. Preserve `_project_openai_schema` rejection of open objects, non-required
   properties, and unsupported keywords in `src/common/model_provider.py`.
6. Create a new candidate/version and obtain human review; never alter an approved
   schema in place. Re-extract representative PDS files with the changed runtime
   contract. List storage can remain JSONB; closed item shapes do not require tables.

**Prompt strategy after strict mode**

- Use prompts to explain field meaning. Retain one object per independent plan,
  null for missing values, and no inference beyond the source.
- Following issue 2, remove document-local `product_name` uniqueness, tier suffixes,
  and the incorrect `plan_tier` requirement; distinguish family name from `plan_name`.
- Use evaluation to identify repeated semantic confusion before adding few-shot
  examples. A small Gold/Silver or aggregate/sub-limit example may help; do not
  add complete JSON examples containing dozens of fields.
- Compare zero, one, and two micro-examples on a fixed sample. Record first-pass
  validity, field correctness, input/output tokens, and per-document cost. Keep
  examples only when consistent quality gains justify the extra token cost.

**Acceptance**

- Offline tests assert Travel `StructuredOutputSpec.strict is True`, every nested
  object is closed, and no item structure is unspecified.
- Trial 3–5 diverse PDS files, targeting at least 95% first-call validity and
  19–23 model calls for a comparable 19-document batch.
- Average retries and output tokens per document fall materially versus `run-gpt5`.
- Offline tests cover strict compilation, list item fields, and OpenAI projection.

## 2. Simplify Travel extraction identity and loading rules

**Status:** Open, reviewed again 2026-09-20. Supersedes the 2026-09-16 proposal to
force unique product names and append tiers.

**Symptoms**

`validate_canonical_extraction_identities` currently requires normalized
`product_name` uniqueness within a PDF. This binds family names to database
identity and forces artificial changes when Gold/Silver tiers share a family name.
`src/storage/repository.py` hashes vertical + insurer + product name for `product_id`,
then product + document for `release_id`. Model naming changes or corrections can
therefore merge/split entities incorrectly. Duplicate names also trigger model
repair even though name uniqueness should not incur model calls.

JSON Schema establishes structure, types, enums, and required fields, not agreement
with the PDF. A correctly typed but wrong coverage amount can pass validation.

**Decision and scope**

- Limit responsibility to structurally valid, traceable extraction records, without
  cross-PDF or cross-year entity resolution.
- One extraction returns top-level JSON. Each valid object in `products` is an
  independent database product record.
- PostgreSQL assigns a meaningless auto-increment `product_id`. Do not introduce
  `canonical_id` or encode vertical, insurer, family, plan, or type in the key.
- Neither `product_name` nor `plan_name` is unique. Remove Canonical document-local
  product-name uniqueness checks and repair calls caused only by repeated names.
- Use `run_id + document_id + item_index` as the import idempotency identity;
  `item_index` is the array position in `products`.
- Mark valid records as structurally acceptable/loadable, not factually proven.
  Preserve run, document, provider, model, schema version, and original artifact.
- Do not add candidate/product mappings, automatic merging, composite business
  codes, or new family/variant hierarchies. Separate records for the same real
  product in different runs are acceptable in this scope.
- Retain strict JSON, schema, approval, vertical/version, source, and transaction
  checks unrelated to name uniqueness.

**Implementation recommendations**

1. Make `products.product_id` a PostgreSQL-generated surrogate key, updating foreign
   keys and SQLAlchemy types while retaining the field name.
2. Stop deriving keys through `deterministic_product_id(vertical, insurer, product_name)`.
3. Store run/document/item index with an idempotent unique constraint; reloading the
   same artifact must not create new rows.
4. Remove duplicate product-name rejection from the Canonical paths of
   `SchemaExtractor` and `compile_canonical_load_plan`. Keep nonempty, type, enum,
   and required checks in JSON Schema/local contracts.
5. Remove prompt uniqueness/tier-suffix instructions and correct `plan_tier` to `plan_name`.
6. Keep full per-PDF extraction artifacts. Database tables are queryable projections,
   not a place to overwrite, guess, or silently correct model values.
7. Update ADR-001, ADR-002, architecture, root README, `docs/api.md`, and the operator guide.

**Factual quality and human review**

- Correctly typed values can still conflict with the PDF; do not guess corrections
  before loading as part of this issue.
- Operators can inspect loaded data using database tools, SQL, or views. Read-only
  anomaly reports may flag negative amounts, unusual ranges, extreme values,
  widespread missingness, or obvious intra-document conflicts.
- Store human findings separately from original extraction artifacts.
- The optional Travel teacher-model judge (`quality-audit`) now reads parsed PDF
  pages, field definitions, and values, returning structured correctness/evidence/
  uncertainty judgments. It remains outside synchronous extraction/loading and
  neither retries low scores automatically nor overwrites business data.
- Judge findings prioritize review; they are not ground truth. Review all alerts/
  uncertainties and a seeded sample of passes. Human labels accumulate here without
  requiring a separate initial labelling phase. Record provider, model, prompt/schema
  hashes, and an independent usage log.
- Calibrate the judge against accumulated human findings before a fixed evaluation
  set or release regression. Without representative labels, do not claim accuracy.

**Non-goals**

No cross-document deduplication/synonym merging, new family/variant/release hierarchy,
mandatory judge gate for loading, or teacher-model edits to extracted values.

**Acceptance**

- Valid Gold/Silver objects sharing `product_name` pass without name-uniqueness repair.
- Loading gives them distinct auto-increment IDs without business-meaning encoding.
- Repeating run/document/item identity adds no row; a new run may add new results.
- Invalid JSON Schema data still fails; retries follow issue 3, not name repetition.
- Traceability preserves vertical, schema version, run, PDF, provider, model, artifact.
- Supply at least one read-only anomaly query/report without changing raw values.
- Judge output, where used, is separate, traceable, small-sample human-calibrated,
  and never written back into extraction values.

## 3. Choose retry behavior by failure type

**Status:** Open, recorded 2026-09-20. Build on strict output in issue 1 and removal
of name-uniqueness repair in issue 2.

**Partial progress, 2026-09-29:** Travel `quality-audit` records typed `failure_kind`
and safe rule codes per output failure, preserves them in per-document `results.json`,
and supports resume. The shared retry decision table and truncation/transport/polling
policies remain unimplemented; this issue is still open.

**Symptoms**

`run_structured_output` reduces JSON parsing, schema, and business failures to
`{path, message}` and appends repair prompts uniformly, with up to two extra calls.
`ProviderResponseError`, parsing failure, and background timeout use different
exception paths without one retry-decision record. Message text is not a reliable
classifier. OpenAI, Anthropic, and DeepSeek SDKs already have transport retries;
transport retry, output repair, and whole-document rerun need distinct counters.

**Decision**

Do not ask a model whether it deserves another attempt or infer categories from
message keywords. Classify errors at their origin and use a deterministic table:

| `failure_kind` | Observable signal | Default action |
| --- | --- | --- |
| `transient_transport` | Disconnect, SDK HTTP 408/429/5xx | SDK exponential backoff; stop when exhausted; no LLM repair prompt |
| `poll_timeout` | Background response still pending beyond deadline | Prefer polling the same response ID; stop if uncertain rather than create a potentially duplicate billed generation |
| `output_truncated` | Explicit `incomplete/max_output_tokens` or `finish_reason=length` | At most one regeneration with a larger limit or smaller contract; record as regeneration, not repair |
| `json_parse` | Invalid JSON from non-strict/legacy mode | At most one format repair; in strict mode treat as provider/adapter anomaly and stop |
| `schema_validation` | Local JSON Schema path/keyword errors | At most one repair in non-strict compatibility mode; with `strict: true`, stop for a projection/adapter defect |
| `business_validation` | Name uniqueness, plausibility, other business rules | No format repair; move necessary rules to loading, anomaly reports, or human review |
| `refusal_or_filter` | Refusal, empty response, content filtering | No automatic retry; preserve failure for human judgment |
| `source_parse` | No usable PDF text, parser failure, damaged file | No model call; repair/change parsing route before an operator rerun |
| `content_uncertain` | Suspicious values, weak evidence, ambiguous but structurally valid data | Accept as structurally valid and flag review; no automatic re-extraction |

**Implementation recommendations**

1. Add stable enums/exception types for JSON, schema, business, truncation, refusal,
   and transient provider failures; do not reverse-parse user-facing messages.
2. Separate transport retry, polling, LLM format repair, and document rerun policies.
   Each needs its own limit and must not repeat work handled by another layer.
3. Strict Travel output should not normally need two repairs after issue 1. Keep
   a single-repair legacy compatibility path without treating it as normal operation.
4. Log `failure_kind`, `retry_action`, `retry_reason`, and whether a new model call
   occurred, alongside tokens, duration, and validation outcome for retry cost analysis.
5. Failure artifacts contain stable codes and safe structured detail, never raw
   PDF text, credentials, or full model output.

**Acceptance**

- Offline fake-provider tests cover every table entry with exact actions/call counts.
- Duplicate names, suspicious values, uncertainty, refusal, and source parsing do
  not trigger LLM format repair.
- Truncation gets at most one controlled regeneration; polling timeout never
  directly creates a second potentially billed response.
- Usage logs explain each retry's cause, action, and extra token cost.
- A comparable 19-document batch targets 0–4 format failures and 19–23 model calls.

## 4. Isolate extraction and feedback artifacts on holdout retry

**Status:** Open. Carried forward from [review R2](reviews/main-review-2026-09-13.md);
the code path was reconfirmed on 2026-09-30.

**Symptoms and cause:** `src/refine/pipeline/steps.py` calls `extract_many(...)`
in an existing directory, then `load_records(...)` over the whole directory.
Earlier successes can contaminate this attempt. Existing `refinement_feedback.json`
can also cause overwrite refusal after model calls have already been paid for.

**Recommendation and acceptance:** Identify each round/attempt's result set and
analyze only selected holdout documents. Preflight feedback destinations before
paid calls or implement identity-checked safe resume. Tests should seed old success
and feedback files and prove no mixing and no late discovery of output conflicts.

## 5. Do not silently omit list items in Health labelled evaluation

**Status:** Open. Carried forward from [review R3](reviews/main-review-2026-09-13.md);
the code path was reconfirmed on 2026-09-30.

**Symptoms and cause:** `_flatten` in `src/evaluation/metrics.py` skips object-list
items lacking `category/service/name`; `_keyed_items` overwrites duplicate identities
in a dictionary. Evaluation can undercount or hide duplicates without reporting
invalid input.

**Recommendation and acceptance:** Define explicit comparisons for unidentified/
duplicate entries or reject them with a validation error. Never silently discard
items. Offline regressions must show a visible comparison outcome or error for
every original entry in both cases.

## Resolved

### 3. Validate batch resume results before provider creation (2026-10-06)

`batch --output-dir` previously treated any existing path as a completed result.
Resume now validates native/enveloped results through the extraction-record owner,
including source, vertical/version, provider/model, parser, runtime contract and
business rules. Invalid or incompatible results stop before provider creation
without modifying files. Offline regressions cover corrupt JSON, directories,
identity mismatches, invalid payloads, valid reuse and Canonical identity rules.

### 1. Logged pre-validation cleanup of structural noise (2026-09-16)

`run_structured_output(..., drop_structural_noise=True)` removes undeclared
double-underscore keys, corrects misspelled declared keys only if the correct key
is absent, and deduplicates `uniqueItems` string arrays. Every cleanup is logged;
extracted values are unchanged and other format errors still fail.
`SchemaExtractor` enables this by default. It is a temporary mitigation, not a
replacement for issue 1's strict mode.

### 2. Consistent local data paths and relocated-cache identity (2026-10-01)

Health now defaults to the existing repository data layout, with explicit manifest
paths for PDFs, Markdown, caches, and outputs. Both parser routes bind reused
cached documents to the current PDF name/path in memory, preventing old source
paths from producing misplaced Markdown after migration. Original cache and
historical artifact bytes remain unchanged. A regression test covers both parser
routes, cache reuse, current Markdown paths, and unchanged stored cache content.
