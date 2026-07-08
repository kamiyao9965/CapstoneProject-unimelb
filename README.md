# Private Health Schema Discovery

The core flow is:

```text
private_health PDFs -> OpenAI model -> outputs/private_health/schema.yaml
```

The code randomly samples representative PDFs, uploads them to OpenAI as file inputs, and asks the model to generate a reusable YAML schema. Around that sit cost estimation, schema stability measurement, field-level consensus refinement, and an extraction-driven refinement loop.

## Project Structure

```text
data/private_health/raw/PDFs/   input PDFs
outputs/private_health/         generated schema output
outputs/private_health/token_usage.jsonl  per-run token usage log
src/run.py                      CLI entrypoint (one-shot discovery)
src/schema/discovery.py         OpenAI file upload + schema/patch requests
src/schema/prompts.py           discovery and patch prompts
src/schema/sampler.py           random PDF sampling
src/cost/                       token usage -> dollar estimates
src/stability/                  schema drift measurement across runs
src/extract/                    holdout extraction + failure analysis
src/refine/loop.py              refinement loop CLI compatibility entry point
src/refine/pipeline/            generate -> consensus/review -> extract -> analyze loop
src/refine/consensus.py         field-level consensus orchestration
src/refine/candidates/          patch model, normalization, voting, patch stability
src/refine/artifacts/           consensus schema/report rendering
src/refine/human_review/        review queue, decisions, apply logic, Streamlit UI
src/review_app.py               thin Streamlit entry point
tests/                          stdlib unittest suite (no API calls)
requirements.txt                Python dependencies
```

## PDF Layout

Put PDFs under `data/private_health/raw/PDFs/`. The sampler looks for these category folder names anywhere in the path:

```text
combined
extras
generalhealth
hospital
```

Example:

```text
data/private_health/raw/PDFs/
  HCF/
    combined/
    extras/
    generalhealth/
    hospital/
```

## Setup

```bash
pip install -r requirements.txt
```

PowerShell:

```powershell
$env:MY_OPENAI_API_KEY="your_api_key_here"
```

Command Prompt:

```cmd
set MY_OPENAI_API_KEY=your_api_key_here
```

This project prefers `MY_OPENAI_API_KEY` for testing. If it is not set, it falls back to `OPENAI_API_KEY`.

## Run

Generate a schema using the default sampling strategy:

```bash
python src/run.py --seed 42
```

By default it selects 20 PDFs:

- 5 from `combined`
- 5 from `extras`
- 5 from `generalhealth`
- 5 from `hospital`

Within each category, it tries to choose PDFs from 5 different companies.

The output is written to:

```text
outputs/private_health/schema.yaml
```

If that file already exists, the next run writes to `schema_1.yaml`, then
`schema_2.yaml`, and so on.

## Options

Use another model:

```bash
python src/run.py --model gpt-5
```

Use a different number per category:

```bash
python src/run.py --per-category 3
```

Use a longer OpenAI timeout:

```bash
python src/run.py --timeout 1200
```

Provide exact PDFs instead of random sampling:

```bash
python src/run.py \
  --samples \
    data/private_health/raw/PDFs/HCF/hospital/HCF-Hospital-Basic-Plus.pdf \
    data/private_health/raw/PDFs/HCF/extras/HCF-Top-Extras.pdf
```

Uploaded files are deleted from OpenAI after schema generation. To keep them for debugging:

```bash
python src/run.py --keep-uploaded-files
```

Each successful run also appends one JSON line to:

```text
outputs/private_health/token_usage.jsonl
```

Each line records the timestamp, model, API key source env name, linked YAML output path, task duration, sample PDFs, and token usage split into `input_tokens`, `output_tokens`, and `total_tokens`.

## Cost estimation

Turn token usage logs into dollar figures (③ in the improvement plan).

```bash
# Per-run and per-document discovery cost from the log
python src/cost/estimate.py

# Project cost across verticals (per-doc token profile is a proxy until the
# extraction step reports real numbers)
python src/cost/estimate.py --project \
  --vertical private_health=1105 --vertical energy=800 --vertical mobile_plans=500
```

Rates live in `src/cost/pricing.py` and are marked `verified=False` until you
confirm them against <https://openai.com/api/pricing>. Override per run with
`--input-rate` / `--output-rate` (USD per 1M tokens).

## Schema stability

Schema discovery is non-deterministic, so measure drift before building on a
schema (① in the improvement plan).

```bash
# Compare schemas you already have (offline, no API cost)
python src/stability/compare.py --schemas run_1.yaml run_2.yaml run_3.yaml --show-items

# Generate N schemas on the SAME fixed sample and compare (uses the API)
python src/stability/measure.py --runs 3 --seed 42
```

The report gives per-dimension and overall stability (stable core vs drift) and
a verdict on whether the schema is reproducible enough to build on.

## Refinement loop

The generate -> extract -> analyze -> review -> update workflow (② in the
improvement plan).

```bash
# Human-in-the-loop (default): one round, then stop for review
python src/refine/loop.py --seed 42 --eval-seed 7

# After editing round_1/feedback.txt, feed it back in
python src/refine/loop.py --resume-feedback outputs/private_health/refine/round_1/feedback.txt

# Autonomous: iterate N rounds, feeding failures back automatically
python src/refine/loop.py --autonomous --rounds 3
```

Discovery samples with `--seed`; evaluation extracts on a **holdout** set
(`--eval-seed`, kept different) so the schema is not judged on the same PDFs it
was built from. Individual steps are also usable on their own:

```bash
python src/extract/analyze.py --schema outputs/private_health/schema.yaml \
  --extractions outputs/private_health/refine/round_1/extractions
```

## Consensus refinement

Schema fields drift between generations. When drift is visible (see
`src/stability/`), stabilize field decisions *before* extraction evaluation by
voting over multiple patch runs:

```bash
python src/refine/loop.py --seed 42 --eval-seed 7 --consensus-runs 5
```

Each round then generates a draft schema, asks the model `--consensus-runs`
times for candidate patches against it (each run on a different sample),
normalizes field/group names, and classifies each proposed field by how many
runs proposed it: `core` (>=80%), `conditional` (>=50%), `candidate` (>=20%),
`noise`. Core and conditional fields are merged into the schema that gets
evaluated. Round outputs:

```text
outputs/private_health/refine/round_1/
  schema_draft.yaml       single-generation draft (consensus baseline)
  schema.yaml             consensus schema the round was evaluated on
  consensus/
    candidate_patches/    raw patch YAML per run (auditable)
    field_frequency.yaml  per-field vote counts and evidence
    consensus_report.md   human-readable decision report
  extractions/            holdout extraction JSON
  feedback.txt            extraction-failure feedback for the next round
```

Cost scales with `--consensus-runs` (default 1 = off). Thresholds assume a
meaningful run count; with very few runs a field seen once can already reach
`conditional`, so prefer around 5 runs and keep `--per-category` small while
experimenting. The two refinement signals are intentionally separate:

```text
consensus refinement  = stabilizes schema fields before extraction
extraction refinement = improves the schema from holdout extraction failures
```

Extraction analysis remains the stronger signal: a field can be stable across
generations yet still unextractable from real PDFs.

Every consensus sweep also writes `patch_stability.yaml` (field drift measured
on the same N patch runs - no extra API cost) and `review_queue.yaml` for
human review. Field/group alias normalization is configured in
`configs/private_health/aliases.yaml`, not in code.

## Human review of schema updates

Unattended consensus auto-merges by thresholds. To gate updates on a human
decision instead, add `--review-ui`:

```bash
python src/refine/loop.py --seed 42 --eval-seed 7 --consensus-runs 5 --review-ui
```

The round stops after writing `round_N/consensus/review_queue.yaml`. Then:

```bash
# 1. Review each proposal (accept / reject / edit) in the browser
streamlit run src/review_app.py -- --consensus-dir outputs/private_health/refine/round_1/consensus

# 2. Apply your decisions (also a button in the UI)
python src/refine/review.py apply --consensus-dir outputs/private_health/refine/round_1/consensus

# 3. Evaluate the reviewed schema on the holdout set
python src/refine/loop.py --resume-review outputs/private_health/refine/round_1
```

Every click writes `review_decisions.yaml` immediately; the queue file itself
is never rewritten (an item with no decision stays pending, and pending items
are never applied). Accepted/edited updates produce `reviewed_schema.yaml`;
`consensus_schema.yaml` (what the thresholds would have accepted) is kept
alongside for comparison. Proposals from `rename_field` / `merge_fields` /
`move_field_group` patches are flagged in the UI - accepting them applies a
plain upsert, so prefer "Edit then accept" for those.

To review patches against the production baseline without running a loop
round, use the standalone sweep (writes to `outputs/private_health/consensus/`):

```bash
python src/refine/consensus.py --base-schema outputs/private_health/schema.yaml --runs 5
```

## Tests

```bash
python -m unittest discover -s tests
```

Pure-logic coverage for the consensus, human-review, and pipeline modules
(patch parsing, normalization, aggregation, rendering, review application, and
workflow orchestration with a stubbed model). No API calls, no PDFs needed.
