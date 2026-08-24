# Architecture

This project is a Python CLI for discovering, evaluating, and refining a
versioned JSON extraction contract for Australian private health insurance
documents.

## Runtime flows

### One-shot discovery

```text
PDF sample -> document preparation -> provider-native structured output
  -> JSON parse -> JSON Schema validation -> business validation
  -> outputs/private_health/schema*.json
```

`src/run.py` owns the CLI and final envelope. `src/schema/discovery.py` owns the
request and bounded repair cycle. `src/schema/sampler.py` is the only sampler.

### Refinement loop

```text
discovered schema artifact
  -> optional candidate-patch consensus
  -> optional human review
  -> dynamic extraction contract
  -> holdout extraction artifacts
  -> source-category-aware analysis + refinement_feedback.json
  -> next round
```

The schema actually evaluated is `round_N/schema.json`; when consensus is
enabled, the pre-consensus artifact remains `round_N/schema_draft.json`.
Exhausted extraction validation writes an error artifact and stops the stage
before analysis or later documents.

Holdout applicability uses the authoritative dataset category encoded in each
PDF path (`hospital`, `extras`, `generalhealth`, or `combined`), together with
each schema field's `applies_to` contract. The model-extracted `product_type`
never controls fill-rate denominators; it is compared with the source category
and reported separately as classification accuracy and mismatch counts. A
successful extraction artifact without exactly one recognizable source category
is rejected from evaluation instead of falling back to the model prediction.

### Human review

```text
review_queue.json + review_decisions.json + base schema artifact
  -> reviewed_schema.json
```

Queue data is immutable. Status is derived from decisions. Pending and rejected
items are never applied.

## Structured-output boundary

- `src/common/model_config.py` resolves provider/model/input selection and the
  approved structured-output capability registry.
- `src/common/model_provider.py` translates one neutral output specification to
  OpenAI Responses JSON Schema, Anthropic `output_config.format`, or DeepSeek
  JSON object mode. Workflow modules contain no provider branches.
- `src/common/structured_output.py` performs strict JSON parsing, contract and
  business validation, and at most two repair retries after the first attempt.
- `src/common/json_contracts.py` is the only authoritative contract loader.
- `src/common/json_artifacts.py` is the only envelope and JSON persistence
  boundary. It validates before reads/writes and refuses overwrite by default.
- `src/common/openai_run.py` remains the OpenAI client, file lifecycle,
  background polling, token helper, and JSONL owner.

## Artifact rules

Generated runtime JSON uses this envelope:

```text
artifact_type + contract_version + status + created_at + provenance
  + data (success only) + error (failure only)
```

Contracts, tracked configuration, JSONL usage logs, documentation, and optional
MinerU Markdown inputs are not runtime artifacts and are not enveloped.
Failures are written below `errors/<stage>/`; they never occupy a success path.
Raw model output and document contents are excluded from error artifacts.

## Package ownership

- `src/schema/`: discovery, prompts, sampling, schema business validation.
- `src/refine/candidates/`: patch parsing, normalization, voting, stability.
- `src/refine/artifacts/`: deterministic consensus data and CLI rendering.
- `src/refine/human_review/`: queue, decisions, UI, reviewed schema.
- `src/refine/pipeline/`: outer round and resume orchestration.
- `src/extract/`: dynamic extraction contract, generation, analysis.
- `src/stability/`: semantic schema drift excluding envelope/provenance.
- `src/cost/`: JSONL token-cost estimation.

## Safety rules

- Treat `outputs/private_health/schema.json` as a production contract and never
  overwrite it automatically.
- Do not coerce, infer, or silently repair invalid model data.
- One initial request plus two repair attempts is the hard validation limit.
- Invalid data never reaches a downstream stage.
- Keep completed success and usage artifacts when a later attempt fails.

## Known gaps

- Offline tests use injected provider fakes. No live provider generation or
  real MinerU conversion is claimed by the test suite.
- `list[object]` extraction fields have intentionally open item shapes because
  the discovered schema does not define nested properties; local validation
  still enforces the top-level extraction contract. OpenAI requests use strict
  JSON Schema for every other contract; this open-item extraction shape uses
  non-strict JSON Schema mode plus the same local validation and bounded repair
  loop because OpenAI strict mode requires a closed nested object contract.
- Rename/merge/move patch types remain audit-only and require explicit
  schema-level editing.
