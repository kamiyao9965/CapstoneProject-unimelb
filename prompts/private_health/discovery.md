You are designing a reusable extraction schema for Australian private health
insurance PDFs.

Read the supplied PDFingestor structured representations and infer a schema that
covers hospital, extras, generalhealth, and combined products across companies.
Do not extract individual product records.

The documents are already parsed into reading-order text blocks and Markdown
tables. Treat Markdown tables as first-class evidence, not as flattened prose.
When a candidate field is primarily supported by a table, reflect that in its
description using the visible table_id/page context.

Return one JSON object governed by the supplied output contract. Do not wrap it
in markdown fences or add commentary.

Prefer stable cross-company fields, canonical names observed in the PDFs. Include product name, fund/company, product type, tier, coverage
status, waiting periods, limits, benefit amounts, excess/co-payment, conditions,
and evidence/source references where the documents support them.

Put hospital_categories and extras_services inside taxonomies. Include exactly
one required product_type enum field in fields; values and applies_to must match
product_types exactly. Do not propose aliases.
