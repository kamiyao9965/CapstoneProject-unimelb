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

SCHEMA_PATCH_PROMPT = """
You are refining a production extraction schema for Australian private health
insurance PDFs.

You will receive:
1. The current YAML schema baseline.
2. A sampled set of PDFs.

Do not rewrite the full schema. Return only YAML candidate patches. Do not wrap
the answer in markdown fences.

Use this shape:

patches:
  - patch_type: add_field | rename_field | merge_fields | move_field_group | update_description | add_alias | reject_field
    target_group: snake_case_group_name
    field_name: snake_case_observed_or_proposed_name
    canonical_name: snake_case_canonical_name
    type: string | number | boolean | enum | list[object]
    description: short production-oriented description
    applies_to: [hospital | extras | generalhealth | combined]
    required: false
    values: []  # required and non-empty for enum fields
    evidence_documents:
      - path: source PDF path when available
        quote_or_summary: short evidence summary
    confidence: 0.0
    rationale: why this patch should be considered

Prefer stable cross-company fields. Use canonical snake_case names. If a field is
only promotional, rare, ambiguous, or unsupported by evidence, use reject_field
or give it low confidence. Preserve existing schema concepts unless the PDFs
provide evidence for a better production schema.
""".strip()
