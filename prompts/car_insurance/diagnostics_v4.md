Runtime diagnostics revision 2:
For coverage_summary_tables, columns describes ALL cells including a label column
if present. rows[].label is an additional row identifier, not a substitute for a cell.
Example: columns=["Feature","Cover"], rows=[{"label":"Hire","cells":["Hire","Reasonable costs"]}].
Never change policy content just to satisfy a column count.
Use only exact IDs from SOURCE COMPLETENESS CHECKLIST in source_clause_ids.
For blocks outside that checklist use ordinary page evidence and no invented checklist ID.
Complete source-block evidence can be on its owning record or that record's exceptions.
Adjacent evidence snippets may be joined in their original order on the same page;
do not omit intervening words, reorder text, remove sidebars or correct source text in quotes.
For records with source_clause_ids (and their exceptions), never use '...' or '…' to skip
source text inside a quote; quote each needed sentence or bullet as its own evidence entry.
Put readable interpretations in text/details, separate from verbatim evidence.
Exception evidence: the owning rule carries the complete source block. Each exception's
evidence must quote verbatim, on that page, at least the complete sentence or bullet
containing its unless/except/does-not-apply cue (the whole bullet 'X, unless Y.', not
just 'unless Y.'). If the cue ends with ':' and the carve-out continues in the next
block, quoting that complete next block also suffices ONLY when the checker identifies
a connected exception bullet list. Never quote the next exclusion as a continuation.
Every such cue needs an exception.
Before returning, reconcile each owning rule against EACH source block it cites.
Merging source blocks into one rule does not permit dropping any block's evidence.
For every independent qualification (including prior authorisation and unless otherwise
stated), retain a structured exception, not merely a mention in the parent's evidence.
Keep the exception's full introductory sentence and all applicable list items together;
do not quote only the concluding carve-out. Use the source page for each quote.
When repairing an evidence diagnostic, first inspect the existing structured meaning:
preserve correct exceptions and repair their citations; add an exception only when missing.
Never delete source IDs, merge away qualifications or empty a coverage to silence checks.
Shared liability caps include every covered member of the source-defined liability section.
Explicit liability for towing a trailer/caravan belongs in caravans_and_trailers_tppd_extension
for every source-named applicable product, with the same liability pool, not [].
This is distinct from damage to the trailer itself and from towing/recovery expenses.
A cap on 'all claims from any one incident for legal liability covered by this policy' is
policy-wide: in EACH product, all covered liability members share one such pool.
General exclusions that 'apply to all sections of your policy' (or give 'no cover under
any section of this policy') apply to EVERY product: repeat each one, with its evidence
and exceptions, in each product's policy_rules. Never leave such a product's rules [].
If a single option names fire, theft and attempted theft and gives one common payout
rule, retain that rule in one shared pool linked reciprocally to all three events.
Do not silently omit the total-loss branch for attempted theft or duplicate shared caps.
Limit basis convention: any limit with a market_value term (the insured car's value)
uses basis=per_vehicle, e.g. lesser_of(repair up to the scheduled amount, market value).
Exception scope convention, as (preserved_cover, not_restored_cover) pairs only:
(own_vehicle_damage, third_party_liability) only when the exception's evidence explicitly
separates own-car damage from liability; (both, none) when the carve-out lifts the whole
exclusion; otherwise (source_defined, source_defined). Do not use unknown here.
