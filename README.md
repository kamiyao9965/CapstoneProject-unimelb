# Private Health Schema Discovery

This testing branch does one thing:

```text
private_health PDFs -> OpenAI model -> outputs/private_health/schema.yaml
```

The code randomly samples representative PDFs, uploads them to OpenAI as file inputs, and asks the model to generate a reusable YAML schema.

## Project Structure

```text
data/private_health/raw/PDFs/   input PDFs
outputs/private_health/         generated schema output
outputs/private_health/token_usage.jsonl  per-run token usage log
src/run.py                      CLI entrypoint
src/schema/discovery.py         OpenAI file upload + schema request
src/schema/prompts.py           prompt used for schema generation
src/schema/sampler.py           random PDF sampling
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
