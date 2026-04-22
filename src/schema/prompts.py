SCHEMA_DISCOVERY_PROMPT = """
You are designing a reusable extraction schema for Australian private health
insurance PDFs.

Read the supplied PDFs and infer a schema that covers hospital, extras,
generalhealth, and combined products across companies. Do not extract individual
product records.

Return only YAML. Do not wrap it in markdown fences.

Use this shape:

vertical: private_health
version: 0.1-draft
description: ...
product_types: [...]
fields:
  - name: snake_case_name
    type: string | number | boolean | enum | list[object]
    description: ...
    applies_to: [...]
    required: false
    values: []
hospital_categories:
  - canonical_name: BackNeckSpine
    description: ...
    aliases: [...]
extras_services:
  - canonical_name: GeneralDental
    description: ...
    aliases: [...]
notes: [...]

Prefer stable cross-company fields, canonical names, and aliases observed in
the PDFs. Include product name, fund/company, product type, tier, coverage
status, waiting periods, limits, benefit amounts, excess/co-payment, conditions,
and evidence/source references where the documents support them.
""".strip()
