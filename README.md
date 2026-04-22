# Konkrd Schema Discovery - Testing Branch

This `testing` branch is scoped to one task:

> Take private health insurance PDF documents as input and generate a structured schema as output.

The branch is not currently focused on the full extraction/evaluation workflow. In this branch, the expected workflow is schema discovery for the `private_health` vertical.

## Current Scope

Input:

- Private health PDF files under `data/private_health/raw/PDFs/`
- Example structure:

```text
data/private_health/raw/PDFs/
  HCF/
    hospital/
      HCF-Hospital-Basic-Plus.pdf
      HCF-Hospital-Silver-Plus.pdf
    extras/
      HCF-Top-Extras.pdf
```

Output:

- A generated schema file for private health insurance.
- Recommended output path:

```text
data/private_health/schema.yaml
```

or, for testing without overwriting the existing schema:

```text
outputs/private_health/discovered-schema.yaml
```

## What This Branch Does

The schema discovery flow reads sample private health PDFs and proposes a draft YAML schema. That schema should describe the fields, categories, services, canonical names, and aliases needed to structure private health insurance data.

At this stage, the main goal is:

1. Read private health PDF documents.
2. Identify repeated domain concepts such as hospital tiers, clinical categories, extras services, limits, and waiting periods.
3. Generate a schema that can later be used by an extraction pipeline.

## What This Branch Is Not Focused On

The following parts may exist in the codebase, but they are not the current focus of this `testing` branch:

- Running extraction from PDF into final product JSON.
- Batch extraction across multiple verticals.
- Evaluating extracted JSON against labelled CSV ground truth.
- Crawling insurer websites for new PDFs.
- Supporting other verticals such as travel, car, or home insurance.

## Key Files

```text
src/run.py
```

Unified command-line entrypoint. For this branch, the most relevant command is `discover`.

```text
src/schema/discovery.py
```

Generates a draft schema from sample PDFs.

```text
src/pipeline/ingestor.py
```

Reads PDF text and tables so the discovery step can inspect document content.

```text
data/private_health/
```

Private health domain folder. This is the only vertical targeted by the current testing work.

## Usage

Install dependencies:

```bash
pip install -r requirements.txt
```

Generate a draft schema from private health PDF samples:

```bash
python src/run.py discover \
  --vertical private_health \
  --samples \
    data/private_health/raw/PDFs/HCF/hospital/HCF-Hospital-Basic-Plus.pdf \
    data/private_health/raw/PDFs/HCF/hospital/HCF-Hospital-Silver-Plus.pdf \
  --output outputs/private_health/discovered-schema.yaml
```

If you want the generated schema to become the active private health schema, write it to:

```bash
python src/run.py discover \
  --vertical private_health \
  --samples \
    data/private_health/raw/PDFs/HCF/hospital/HCF-Hospital-Basic-Plus.pdf \
    data/private_health/raw/PDFs/HCF/hospital/HCF-Hospital-Silver-Plus.pdf \
  --output data/private_health/schema.yaml
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
