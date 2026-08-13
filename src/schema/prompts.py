from src.schema.business_fidelity import BUSINESS_FIDELITY_RULES


SCHEMA_DISCOVERY_PROMPT = f"""
You are designing a reusable extraction schema for Australian private health
insurance PDFs.

Read the supplied PDFingestor structured representations and infer a schema that
covers hospital, extras, generalhealth, and combined products across companies.
Do not extract individual product records.

The documents are already parsed into reading-order text blocks and delimited
tables. Treat tables as first-class evidence, not as flattened prose. When a
candidate field is primarily supported by a table, reflect that in its
description or aliases using the visible table_id/page context.

Return one JSON object governed by the supplied output contract. Do not wrap it
in markdown fences or add commentary.

Prefer stable cross-company fields, canonical names, and aliases observed in
the PDFs. Include product name, fund/company, product type, tier, coverage
status, waiting periods, limits, benefit amounts, excess/co-payment, conditions,
and evidence/source references where the documents support them.

Mark only core identity fields as required: product_type, product_name, and the
fund/insurer name field. Comparison fields such as waiting_periods,
annual_limits, exclusions, benefits, ambulance cover, excess, tier, and
clinical categories must remain optional so later extraction can use null or an
empty list when a short PDF does not state them.

Across refinement rounds, preserve well-supported comparison fields from prior
schemas unless the feedback explicitly says they were unextractable or
duplicative. In particular, do not drop effective_date/as_at_date or structured
annual_limits solely because the latest sample also contains per-service limits.

{BUSINESS_FIDELITY_RULES}
""".strip()

SCHEMA_PATCH_PROMPT = f"""
You are refining a production extraction schema for Australian private health
insurance PDFs.

You will receive:
1. The current JSON schema baseline.
2. A sampled set of PDFingestor structured representations.

The sampled documents contain reading-order text blocks and delimited tables
with table_id/page markers. Prefer changes supported by these explicit text or
table sources, and mention table-derived evidence in rationale fields when it
matters.

Do not rewrite the full schema. Return one JSON candidate-patch object governed
by the supplied output contract. Do not wrap the answer in markdown fences.

Prefer stable cross-company fields. Use canonical snake_case names. If a field is
only promotional, rare, ambiguous, or unsupported by evidence, use reject_field
or give it low confidence. Preserve existing schema concepts unless the PDFs
provide evidence for a better production schema.

Mark only core identity fields as required: product_type, product_name, and the
fund/insurer name field. Keep comparison fields optional, including
waiting_periods and annual_limits, because valid short product PDFs may omit
them.

{BUSINESS_FIDELITY_RULES}
""".strip()
