from src.schema.business_fidelity import BUSINESS_FIDELITY_RULES
from src.schema.validation import canonical_item_policy_prompt


CANONICAL_ITEM_POLICY_RULES = canonical_item_policy_prompt()


SCHEMA_DISCOVERY_PROMPT = f"""
You are designing a reusable extraction schema for Australian private health
insurance PDFs.

Read the supplied PDFingestor structured representations and infer a schema that
covers hospital, extras, generalhealth, and combined products across companies.
Do not extract individual product records.

The documents are already parsed into reading-order text blocks and delimited
tables. Treat tables as first-class evidence, not as flattened prose. Field and
item descriptions must contain reusable business meaning, units, nullability,
and extraction conditions only. Never put PDF names, company-specific sample
details, page numbers, p3/p2_t1-style page tokens, or table_id markers in a
description or alias. Put discovery source locations in top-level notes instead.
General semantic examples introduced with e.g. are allowed when they are not
tied to a particular sample or source location.

Return one JSON object governed by the supplied output contract. Do not wrap it
in markdown fences or add commentary.

Prefer stable cross-company fields, canonical names, and aliases observed in
the PDFs. Include product name, fund/company, product type, tier, coverage
status, waiting periods, limits, benefit amounts, excess/co-payment, conditions,
and evidence/source references where the documents support them.

The product_type and product_name fields must always set required=true. Include
at least one insurer identity field named exactly fund_name or insurer_name and
set it required=true. These exact canonical names are mandatory: do not create
required identity fields named fund, company, company_name, health_fund, or any
other variation. Put observed alternative wording in aliases instead. Only
product_type, product_name, fund_name, and insurer_name may set required=true;
all other fields must set required=false.
These requirements are unconditional and must not be changed by refinement
feedback. Comparison fields such as waiting_periods,
annual_limits, exclusions, benefits, ambulance cover, excess, tier, and
clinical categories must remain optional so later extraction can use null or an
empty list when a short PDF does not state them.

Use list[string], list[number], list[boolean], or list[enum] for scalar lists.
Every list[object] field must declare a non-empty item_fields array containing
the exact canonical keys for each item. Item fields use only string, number,
boolean, or enum; enum fields declare exactly one of inline values or enum_ref.
Use enum_ref only for product_types, hospital_categories, or extras_services.
Define product type values only once: the product_type field must use
values=[] and enum_ref="product_types", referencing the top-level product_types
canonical set. Never copy the product type values inline into that field.
Never put aliases in item_fields and never leave a list[object] item shape open.
{CANONICAL_ITEM_POLICY_RULES}
Set unique_items=true only when duplicate scalar list values have no source
meaning (for example exclusions or membership types); otherwise set it false.

Across refinement rounds, preserve well-supported comparison fields from prior
schemas unless the feedback explicitly says they were unextractable or
duplicative. In particular, do not drop effective_date/as_at_date or structured
annual_limits solely because the latest sample also contains per-service limits.

Private-health monetary fields must preserve their business semantics:
- Model selectable hospital excesses as a list[object] field named
  hospital_excess_options, with an amount_aud item field and optional conditions.
  Do not collapse multiple advertised excess choices into one number, a numeric
  range, or free-text notes.
- Never encode "no annual limit" or "unlimited" as numeric zero. For an extras
  benefit, use an explicit annual_limit_type enum with values amount and
  unlimited; monetary annual-limit fields stay null when the type is unlimited.
- Every non-shared annual_limit_amount_aud must have an annual_limit_scope enum
  with values per_person, per_policy, and per_membership. If membership duration
  changes the limit, store the entry/base tier in annual_limit_amount_aud and
  preserve the complete stepped schedule in notes.
- Store each shared or combined extras amount exactly once in a top-level
  extras_shared_limits list[object], keyed by a stable shared_limit_group.
  Participating extras_benefits rows reference that identifier and leave their
  per-service annual-limit amounts null; never duplicate the shared amount onto
  every service row.
- Keep endodontic, vaccinations, and glucose_monitor as distinct canonical
  extras services. Root-canal/endodontic wording belongs to endodontic, not
  major_dental; vaccine/immunisation wording belongs to vaccinations; diabetic
  supply/blood-glucose monitor wording belongs to glucose_monitor.
- Model ambulance exactly once in a dedicated ambulance_coverage list[object].
  Do not include ambulance in extras_benefits or in the extras_services canonical
  set. Include annual_trip_limit_per_person and annual_trip_limit_per_policy item
  fields so claim/transport-count caps are not relegated to notes.

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
Keep field and item descriptions reusable: never put PDF names, company-specific
sample details, page numbers, p3/p2_t1-style page tokens, or table_id markers in
descriptions or aliases. Put all sample locations in evidence_documents or
rationale. General semantic examples introduced with e.g. remain allowed when
they are not tied to a particular sample or source location.

Do not rewrite the full schema. Return one JSON candidate-patch object governed
by the supplied output contract. Do not wrap the answer in markdown fences.
Use update_field_shape when changing an existing field's type, values/enum_ref,
item_fields, or unique_items; these shape properties must change atomically.
add_alias only adds an alias to an existing schema field. Do not use add_alias
for hospital_categories or extras_services. Do not propose taxonomy, category,
or service aliases.

Prefer stable cross-company fields. Use canonical snake_case names. If a field is
only promotional, rare, ambiguous, or unsupported by evidence, use reject_field
or give it low confidence. Preserve existing schema concepts unless the PDFs
provide evidence for a better production schema.

Only fields named exactly product_type, product_name, fund_name, or insurer_name
may set required=true. Never mark fund, company, company_name, health_fund, or
any other naming variation as required; add observed wording as aliases of the
canonical fund_name or insurer_name field instead. Keep comparison fields
optional, including waiting_periods and annual_limits, because valid short
product PDFs may omit them.

Candidate fields use scalar-list types when their items are scalar. Every
list[object] candidate must provide non-empty item_fields with exact canonical
keys. Item enums declare exactly one of values or enum_ref; supported enum_ref
values are product_types, hospital_categories, and extras_services.
{CANONICAL_ITEM_POLICY_RULES}
Set unique_items deliberately for every candidate: true only when list
deduplication is semantically safe, otherwise false.

Preserve these private-health monetary invariants in every patch:
- hospital_excess_options is a list[object] with amount_aud and optional
  conditions; never reduce multiple selectable excesses to one number or notes.
- "No annual limit" is represented by annual_limit_type=unlimited and null
  monetary limit fields, never by numeric zero.
- A non-shared annual_limit_amount_aud uses annual_limit_scope to distinguish
  per_person, per_policy, and per_membership. For stepped loyalty limits, keep
  the entry/base amount in annual_limit_amount_aud and the full schedule in notes.
- Combined extras limits are stored once in extras_shared_limits and use a stable
  shared_limit_group on all participating service rows. Per-service annual-limit
  amounts stay null when the limit comes from that shared group.
- Ambulance is represented only by ambulance_coverage, never by an
  extras_benefits service row. Preserve annual trip/claim caps as numeric
  annual_trip_limit_per_person or annual_trip_limit_per_policy item fields.

{BUSINESS_FIDELITY_RULES}
""".strip()
