# Private Health Schema Discovery

This `testing` branch is scoped to one task:

> Take private health insurance PDF documents as input and generate a structured schema as output.

Only the code needed for private health schema discovery is kept in this branch.

## Current Scope

Input:

- Private health PDF files under `data/private_health/raw/PDFs/`
- Example structure:

```text
data/private_health/raw/PDFs/
  HCF/
    combined/
    hospital/
      HCF-Hospital-Basic-Plus.pdf
      HCF-Hospital-Silver-Plus.pdf
    extras/
      HCF-Top-Extras.pdf
    generalhealth/
      HCF-Top-Extras.pdf
```

The sampler also supports the reverse layout, as long as the four category folder names appear in the path:

```text
data/private_health/raw/PDFs/
  combined/
    HCF/
    BUPA/
  extras/
    HCF/
    BUPA/
  generalhealth/
    HCF/
    BUPA/
  hospital/
    HCF/
    BUPA/
```

Output:

- A generated schema file for private health insurance.
- Recommended output path:

```text
outputs/private_health/schema.yaml
```

## What This Branch Does

The schema discovery flow reads sample private health PDFs and proposes a draft YAML schema. That schema should describe the fields, categories, services, canonical names, and aliases needed to structure private health insurance data.

At this stage, the main goal is:

1. Read private health PDF documents.
2. Identify repeated domain concepts such as hospital tiers, clinical categories, extras services, limits, and waiting periods.
3. Generate a schema that can later be used by an extraction pipeline.

## Retained Project Structure

```text
README.md
requirements.txt
src/
  run.py                  CLI entrypoint for schema discovery
  models.py               Minimal data models used by discovery
  pipeline/
    ingestor.py           Reads PDF text and tables
  schema/
    discovery.py          Generates the draft schema
data/
  private_health/
    raw/PDFs/             Private health PDF inputs
outputs/
  private_health/
    schema.yaml           Generated private health schema
```

The extraction, evaluation, crawling, labelled ground-truth, and non-private-health vertical files have been removed from this branch.

## Key Files

`src/run.py`

Unified command-line entrypoint. For this branch, the most relevant command is `discover`.

`src/schema/discovery.py`

Generates a draft schema from sample PDFs.

`src/pipeline/ingestor.py`

Reads PDF text and tables so the discovery step can inspect document content.

`data/private_health/`

Private health domain folder. This is the only vertical targeted by the current testing work.

## Usage

Install dependencies:

```bash
pip install -r requirements.txt
```

Generate a draft schema from private health PDF samples:

```bash
python src/run.py discover \
  --output outputs/private_health/schema.yaml
```

By default, this command randomly selects 20 PDFs from:

```text
data/private_health/raw/PDFs/
```

It picks 5 PDFs from each category:

- `combined`
- `extras`
- `generalhealth`
- `hospital`

Within each category it tries to choose PDFs from 5 different companies. For reproducible sampling, pass a seed:

```bash
python src/run.py discover \
  --seed 42 \
  --output outputs/private_health/schema.yaml
```

You can change the input folder or the number selected per category:

```bash
python src/run.py discover \
  --input-root data/private_health/raw/PDFs \
  --per-category 5 \
  --output outputs/private_health/schema.yaml
```

You can still provide explicit PDF samples if you do not want random selection:

```bash
python src/run.py discover \
  --vertical private_health \
  --samples \
    data/private_health/raw/PDFs/HCF/hospital/HCF-Hospital-Basic-Plus.pdf \
    data/private_health/raw/PDFs/HCF/hospital/HCF-Hospital-Silver-Plus.pdf \
  --output outputs/private_health/schema.yaml
```

## Expected Result

The output should be a YAML schema for `private_health`, for example:

```yaml
vertical: private_health
version: 0.1-draft
coverage:
  fields:
    - name: product_name
      type: string
      description: Marketing product name
```

The schema can later be reviewed, edited, and used as the contract for extracting structured data from private health insurance PDFs.
