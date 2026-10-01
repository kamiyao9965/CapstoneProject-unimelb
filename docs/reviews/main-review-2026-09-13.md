# Main review: business validity, correctness, and simplification

Review date: 2026-09-13. Baseline: `main`, `2e4465f`. The working tree was clean
when this review began. The review used inspection and offline reproductions;
it did not modify business code, remove compatibility entry points, or change
dependencies.

This is a historical review, translated into English during the layout migration.
Line numbers, source counts, and findings describe that baseline. Links point to
current file locations where possible; consult [open issues](../fix.md) for
current unresolved work.

## Conclusion

Health and Travel already share discovery, patch consensus, human review, and
extraction engines. Manifests and prompts are a sound way to express vertical
differences. The code broadly matches the workflow; rebuilding it is unnecessary.

The reviewed version still had defects affecting evaluation credibility, recovery,
and storage identity. It was suitable for team development, experiments, and
demonstrations, but passing offline tests alone did not establish reliably accurate,
comparable, loadable insurance data.

A 5,000-line target is not an acceptance criterion. The narrowly defined core
packages contained about 4,352 effective Python lines, or 4,970 including the CLI.
All non-crawler runtime code contained 9,894 effective lines. The smaller subtotal
depends on supporting modules in the larger one and is not the complete system.

## Findings requiring fixes

### R1 · P1: Envelope storage did not check extraction vertical/schema version

Location: [storage/service.py](../../src/storage/service.py), `_artifact_values()`,
historical lines 214–233 versus 239–244.

The storage boundary accepted envelopes and legacy `ExtractionResult`. The legacy
branch checked vertical/schema version. The envelope branch validated success and
source, then returned data without comparing expected vertical/version. The load
plan was subsequently compiled using the selected approved schema.

Offline reproduction used the existing storage fixture and real `prepare_storage_load()`:

- Travel provenance declaring `different-schema-version` passed, with load-plan
  version `1.0.0` from the selected schema.
- Health provenance with structurally compatible data and a valid Travel source
  also passed and was prepared as Travel data.

Impact: old or wrong-vertical results could be attributed to another approved
contract, losing the trustworthy link to the schema used during extraction.
This demonstrated incorrect acceptance by complete storage preflight; no database
write was performed.

Recommendation: parse both formats through one vertical/version/source-identity
check. Reject explicit conflicts. Define a policy for historical envelopes lacking
identity rather than defaulting them to the selected version. Add symmetric tests.

### R2 · P2: Retried holdout review mixed old successes and could fail after extra calls

Locations: [refine/pipeline/steps.py](../../src/refine/pipeline/steps.py),
`evaluate_schema()`, historical lines 137–153 and 170–174;
[schema_application/extractor.py](../../src/schema_application/extractor.py), lines 235–245.

Every attempt used `round_N/extractions/`. The atomic writer retained successes,
and retries produced suffixed files. Analysis scanned the whole directory rather
than the files returned by this `extract_many()` call, without source/schema
deduplication. Feedback always targeted `refinement_feedback.json`.

Offline reproduction ran real evaluation and batch extraction, replacing only
sampling and the single-model extraction boundary:

1. Two PDFs: the first succeeded, the second simulated interruption.
2. Both succeeded on retry, but analysis reported `documents=3`, `error_docs=1`
   for only two distinct sources.
3. Running again after completion performed two more extractions, then raised
   `FileExistsError` because feedback already existed.

Feedback still said there were no systematic failures. With successful records,
`build_feedback_instructions()` did not include `error_docs`; recovery work should
address that too.

Impact: duplicate records changed metric weights, old failures contaminated new
attempts, and completed work could consume model calls before failing again.

Recommendation: define the result set of one evaluation attempt. A new directory
per attempt and analysis restricted to returned files is sufficient; alternatively,
reuse only source/schema-matched successes. Detect completed feedback before model
calls. No scheduler or general checkpoint framework is needed.

### R3 · P2: Health evaluation silently omitted unidentified or duplicate list items

Location: [evaluation/metrics.py](../../src/evaluation/metrics.py), `_flatten()`,
historical lines 394–401, and `_keyed_items()`, lines 546–549.

Object-list entries were counted only when they had `category`, `service`, or
`name`. Others were skipped. Repeated names used the same dictionary key, so later
values overwrote earlier ones. The public `list[object]` contract allowed both
cases through structural and business validation.

Reproductions used real-contract-validated flat Health records with a services field:

- A correct GeneralDental item plus an extra `benefit_name=Invented` item.
- Two GeneralDental items, first `covered=false`, then `covered=true`.

Against one correct label, both obtained `field_precision=1.0`,
`hallucination_rate=0.0`, and `service_precision=1.0`.

Impact: extra or contradictory model content could still receive perfect scores,
directly undermining the project's assessment of extraction quality.

Recommendation: count unidentified/duplicate entries explicitly as invalid,
unmatched, or duplicate, or fail evaluation of the record. Keep existing Health
identity rules; no alias system or arbitrary nested-schema framework is needed.

### R4 · P2: Empty parsed PDFs could still call a model and produce success

Locations: [PDF adapter](../../src/pdf_ingestion/adapter.py), historical lines
38–52 and 62–69; [extractor](../../src/schema_application/extractor.py), lines 148–155.
Discovery used the same renderer.

PDFingestor could validly return pages with zero blocks. Rendering still emitted
filename, path, hash, and page numbers, creating nonempty metadata text. No check
required actual text, tables, or visual content before a model request.

A real blank one-page PDF generated in a temporary directory produced `blocks=[]`
through real PDFingestor. With a fake provider returning a valid record, the real
extractor called the provider once and wrote `status=success`, `product_name=Example`.

Impact: a scanned or unsuccessfully parsed document could incur cost and produce
accepted but unsupported data based only on structural validity. This reproduction
proved a missing boundary, not hallucination by a real model on that input.

Recommendation: check each document for usable content in the shared PDF-to-prompt
boundary and fail clearly on zero content. OCR can be scoped separately; this fix
does not require another OCR platform.

## Fit between business goals and code

| Goal | Assessment at the review baseline |
| --- | --- |
| Shared Health/Travel engine | Largely achieved; manifests/prompts express most differences |
| Connected discovery, consensus, review, extraction | Normal flow has offline coverage; abnormal recovery needs R2 |
| Invalid data stopped at boundaries | Keep structural checks, bounded repair, and approval gates; R1/R4 are gaps |
| Evaluation reflects real business quality | Not fully established; R3 inflates quality, and fill rate/stability are not value accuracy |
| Traceable review and history | Queue/base/decision identity binding and no-overwrite rules are valuable |
| Similar future verticals | Configurable within existing models; different acquisition protocols/label adapters may need code |

Travel's lack of labelled evaluation, open `list[object]`, and audit-only
rename/merge/move limitations were documented scope boundaries, not new regressions.
Accepting open lists differs from silently ignoring their content during evaluation;
the latter still needs correction.

Health feedback informs later rounds, so repeatedly used holdout data is closer
to development validation. Keep a separate manually checked final sample that never
feeds refinement; adapted validation data cannot replace an independent test set.

An additional usability issue at this baseline: sources configured only Allianz,
Cover-More, and SCTI, while sampling defaulted to five insurers per category.
Discovery/loop with only those sources required lowering `per-category`. Sampling
preflight should show actual and required insurer counts before execution.

## Simplification opportunities

### S1: Retire unused configuration and PDF conversion entry points

`src/config.py` had 35 physical / 27 effective lines;
`src/common/document_preprocessor.py` had 123 / 92. `rg` and AST import inspection
found no runtime imports from other source modules. Tests of the old preprocessor
did not demonstrate use by current workflows.

AppConfig retained `gpt-4.1` and `KONKRD_LLM_*`, while workflows used ModelSelection
and manifests. The old MinerU route was separate from the PDFingestor entry point.
Candidates totaled 158 physical / 119 effective lines. External Python consumers
and independent uses of MinerU needed confirmation before deletion; this review
deleted neither.

### S2: Remove inactive batch fallback branches

Historical `src/run.py` lines 392–397 and 444–477 still counted heuristic fallback,
filtered model-only reports, and emitted two report sets. The current branch always
constructed `ExtractionResult` from supported providers, with empty warnings and
no heuristic fallback. Normal reports were therefore identical.

Aggregate valid model results once. Decide old-filename compatibility separately,
without preserving duplicate calculations. Removing an absent execution mode is
the main benefit, beyond several dozen lines of reduction.

### S3: Centralize extraction-file compatibility in one reader

CLI wrote legacy `ExtractionResult`; holdout wrote envelopes. Analysis and storage
interpreted formats and identities separately, contributing to R1. Retain both
historical formats but normalize into one internal record with shared identity
checks. Avoid more consumer-specific branches or a full historical-file rewrite.

### S4: Narrow discovery/extractor constructor parameters

Manifests already selected prompts, contracts, and validators, but constructors
allowed redundant overrides and multiple model/selection/client/provider entry
points. Both stored `preprocessor` without using it in the main workflow.

Prefer manifest, model selection, schema, and necessary test injection. Retire
unused overrides incrementally. Reuse validated compilation results to avoid
repeated normalization/validation. Keep boundary checks and reduce internal
repetition; do not add a generic configuration framework around the parameters.

### Avoid reductions made only to meet a number

Do not fragment modules by length, compress formatting, move code and call it
simpler, or remove provider repair, table parsing, transactions, source checks,
review identity, or necessary tests. Supporting capabilities require real code;
small cleanups cannot reduce the entire non-crawler system to 5,000 lines while
preserving scope.

## Code size

Counts covered Git-tracked Python only, excluding environments, prompt Markdown,
JSON configuration, data, and outputs. Physical lines include blanks, comments,
and docstrings. Effective lines use tokenize/AST to exclude blanks, comment-only
lines, and module/class/function docstrings, counting occupied physical lines
rather than statements. Ordinary multiline strings remain program code.

| Scope | Files | Physical lines | Effective lines |
| --- | ---: | ---: | ---: |
| All source | 83 | 13,470 | 11,571 |
| Source excluding scraper | 75 | 11,576 | 9,894 |
| schema + refine + schema_application + verticals | 35 | 5,051 | 4,352 |
| Those four packages + run.py | 36 | 5,742 | 4,970 |
| Tests separately | 46 | 8,357 | 7,147 |
| All tracked Python, including tests | 129 | 21,827 | 18,718 |

The four-package subtotal included Canonical compilation and review UI, but excluded
common, PDFingestor, operator UI, storage, labelled evaluation, cost, and stability.
It is an explicit subset, not all business Python.

| Historical directory | Effective lines |
| --- | ---: |
| refine | 2,333 |
| scraper | 1,677 |
| common | 1,440 |
| schema | 1,119 |
| Root source entry points/models | 905 |
| storage | 865 |
| PDFingestor | 805 |
| schema_application | 664 |
| evaluation | 595 |
| tool_ui | 470 |
| stability | 280 |
| verticals | 236 |
| cost | 182 |

A 5,000-line estimate was close for the four packages plus CLI, but not for the
complete runnable project excluding prompts and crawler. Line count was not the
acceptance criterion for fixes.

## Verification and recommended sequence

- `compileall -q src tests` passed.
- Offline unittest discovery ran 388 tests: 387 passed; one live PostgreSQL test
  skipped with `KONKRD_TEST_DATABASE_URL` unset.
- R1–R3 were reproduced with existing modules/fixtures; R4 used a temporary real
  PDF and fake provider.
- A generated PDF with a title and bordered table passed a real PDFingestor smoke
  check for headings, service names, and values. This did not cover complex real
  insurance layouts or scans.
- No paid provider, website crawl, real insurance/label data read, live DB, new
  browser test, or dependency-vulnerability scan was run for this review.

Recommended sequence at the time: fix R1–R4 separately with behavior tests, retire
obsolete entry points/fallback branches/format detection, then manually check
small real samples from each vertical for key fields, missing/wrong values,
product identity, and provenance. Keep fixes separate from behavior-preserving
simplification so quality changes remain attributable.
