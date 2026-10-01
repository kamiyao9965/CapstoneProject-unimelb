You are designing a reusable extraction schema for Australian travel insurance
Product Disclosure Statements (PDS).

Infer stable fields that support comparison across insurers and plans. A single
PDS can describe several distinct products or plan tiers; the later extraction
stage must be able to emit one product record for every plan described.

Use these product classifications when supported: international_single_trip,
international_multi_trip, domestic, inbound, business, and cruise. Car rental
vehicle excess is a travel-insurance benefit, not a separate car-insurance
product type.

Include exactly one product_type field in fields. It is a required enum whose
values and applies_to match product_types exactly.

Prioritise insurer, product and plan names, geographic scope, trip frequency,
age and eligibility rules, excess choices, cancellation, medical, luggage,
rental vehicle excess, personal liability, COVID-related cover, exclusions,
limits, sub-limits, waiting periods, and source evidence. Use
taxonomies.coverage_categories for stable cross-insurer benefit groupings.

The documents are PDFingestor reading-order text and Markdown tables. Treat
tables as first-class evidence. Return only the JSON object governed by the
supplied contract. Do not extract product records during schema discovery and
do not wrap the response in Markdown.
