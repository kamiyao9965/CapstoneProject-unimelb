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


PET_SCHEMA_DISCOVERY_PROMPT = """
You are designing a reusable extraction schema for Australian pet insurance PDFs.

Read the supplied PDFingestor representations and infer a schema for concrete
pet-insurance plans. A PDF can contain several plans, so distinguish document
metadata from product records. Discover stable product fields such as product
name, insurer, cover scope, eligible species, age/breed
eligibility, annual limits, benefit percentage, excess, waiting/exclusion
periods, sublimits, covered benefits, optional benefits, and exclusions.

Use cover_scopes for normalized risk scope (for example accident_only or
accident_and_illness); do not use pds, update, gold, dog, or cat as a cover
scope. The document_role is document metadata, not a product type.
Set document_roles to exactly the manifest taxonomy: pds, update,
combined_fsg_pds, supplementary_pds, policy_booklet, and renewal_pds. Do not
invent alternate labels such as spds, sfsg, fsg, or policy_terms.
For every field, set applies_to to product for product/plan facts, document for
document metadata, or both when the fact belongs in both places. A field may
also list a specific cover_scope when it is limited to that scope; never use
arbitrary labels such as product_type or company as an applicability target.

Return one JSON object governed by the supplied output contract. Do not extract
individual product records into the discovery response. Use canonical snake_case
names, stable cross-company fields, explicit units, nullability, and reusable
descriptions. Product name must be required. Only product_name, insurer_name,
and product_id may be required; all comparison fields remain optional.
When a PDS offers selectable benefit percentages, annual benefit limits, or
excesses, preserve every available option in a corresponding *_options field;
use the scalar field only for a selected or explicitly stated product value.
The canonical fields benefit_percentage_options,
annual_benefit_limit_options_aud, excess_options_aud, co_payment_percentage,
covered_benefit_categories and benefit_coverages must be present even when a sampled
document does not provide a value; mark them optional and describe their
nullability rather than deleting them. Keep
temporary_condition_reinstatement_after_months as a numeric month count.
Populate covered_benefit_categories as a list[enum] using the top-level
benefit_categories vocabulary and include only benefits built into the plan,
not optional add-ons. Preserve benefit_coverages as list[object] with required
item fields benefit_category (enum_ref="benefit_categories") and coverage_status
(inline enum: included, optional, excluded), required source_document_id,
source_block_id, source_page, and source_quote evidence fields, plus an optional
notes string. This
distinction is mandatory when one booklet makes a benefit included for one plan
but optional for another.
Every enum or list[enum] field must choose exactly one value source: either
use a non-empty inline values array with enum_ref=null, or use values=[] with
a non-null enum_ref. Never populate both sources and never leave both empty.
Always use these canonical referenced-enum shapes for the named pet fields:
- covered_benefit_categories: type="list[enum]", values=[], enum_ref="benefit_categories"
- benefit_coverages: type="list[object]"; required item fields benefit_category
  coverage_status, source_document_id, source_block_id, source_page, and source_quote;
  coverage_status values are included, optional, excluded
- document_role: type="enum", values=[], enum_ref="document_roles"
- cover_scope: type="enum", values=[], enum_ref="cover_scopes"
Use this canonical inline-enum shape for species:
- eligible_species: type="list[enum]", values=["dog", "cat"], enum_ref=null

Do not reintroduce these fields retired after holdout review: underwriter_name,
plan_tier, per_condition_annual_limit_aud, emergency_boarding_sub_limit_aud,
consultation_fee_sub_limit_aud, cruciate_ligament_limit_aud,
tick_paralysis_sub_limit_aud, hip_joint_surgery_sub_limit_aud,
third_party_property_damage_liability_limit_aud,
lost_pet_advertising_reward_limit_aud, holiday_cancellation_limit_aud,
cremation_burial_limit_aud, or optional_extras_max_entry_age_years.

The documents may be PDS, policy booklets, updates, SPDS, or combined FSG/PDS.
Treat updates as amendments and do not infer that an omitted field is absent
from the product. Do not place PDF names, page numbers, or table markers in
field descriptions. Return strict JSON without markdown fences or commentary.
""".strip()


PET_SCHEMA_PATCH_PROMPT = """
You are refining a production extraction schema for Australian pet insurance PDFs.
You will receive a current schema baseline and sampled document representations.
Propose only evidence-supported candidate patches. Keep document_role separate
from product cover_scope. Preserve stable comparison fields such
as annual limits, benefit percentage, excess, waiting periods, exclusions,
species eligibility, age/breed eligibility, covered benefit categories, and the
included/optional/excluded status in benefit_coverages.
Use cover_scopes, benefit_categories, and document_roles only as their matching
enum references. For applies_to, use product and/or document for pet fields;
specific cover scopes are also allowed when a field is scope-specific. Return
strict JSON governed by the supplied patch contract. For enum and list[enum]
fields, choose exactly one source: a non-empty values array with enum_ref=null,
or values=[] with a non-null enum_ref. Preserve these canonical shapes:
covered_benefit_categories uses enum_ref="benefit_categories", document_role
uses enum_ref="document_roles", and cover_scope uses enum_ref="cover_scopes";
all three use values=[].
benefit_coverages remains a list[object] whose required benefit_category item
uses enum_ref="benefit_categories" and whose required coverage_status item uses
the exact inline values included, optional, excluded. It also retains required
source_document_id, source_block_id, source_page, and source_quote evidence items.
eligible_species uses the exact inline values ["dog", "cat"] with
type="list[enum]" and enum_ref=null.
Do not reintroduce fields explicitly marked as retired in the current schema
notes, even if a sampled document contains that concept.
""".strip()
