"""Review v3: explicit definitions, shared pools and executable cross-field rules.

No model calls, policy approval or gold-label generation. v2 remains unchanged.
"""
from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path

from . import schema_revision as v2
from .schema_revision import array, enum, number, obj, string

PROFILE = 'car_insurance.review_v3'
VERSION = '2026-10-03.review.v3'
OWNERS = {
    **v2.OWNERS,
    'no_claim_bonus_protection': ['no_claim_bonus_protection'],
    'modifications_and_accessories': ['modifications_and_accessories'],
    'pet_injury_benefit': ['pet_injury'],
}

# Explicit boundaries, not assertions that any given policy includes this cover.
DEFINITIONS = {
    'accidental_loss_or_damage': 'Includes insured vehicle damage from collision, impact, weather and other accidental events. Excludes fire/theft/attempted theft (fire_and_theft), liability to others and separately named ancillary benefits.',
    'fire_and_theft': 'Includes fire, theft and attempted theft damage/loss of the insured vehicle, whether base cover or an option. Excludes theft-triggered hire-car expense (hire_car_after_theft) and personal belongings.',
    'third_party_property_damage': 'Includes the core indemnity for damage to other people\'s property caused by the insured vehicle. Excludes own-vehicle damage, separate legal defence/court-cost features, and specific substitute-car/trailer liability extensions.',
    'legal_liability_features': 'Includes separately stated legal defence/court-cost or ancillary liability features. Excludes a duplicate of the core third-party liability cap and specific substitute-car/trailer extensions; a statement that costs are within the core cap belongs in that core benefit\'s details.',
    'hire_car_after_theft': 'Includes payment/provision of a hire car triggered by theft. Excludes the hired car\'s damage cover and third-party liability while driving it; classify those under temporary_replacement_vehicle and substitute_car_liability.',
    'hire_car_not_at_fault': 'Includes hire-car cost/provision following a not-at-fault event and its stated conditions. Excludes theft-only hire car, replacement-car physical damage and substitute-car liability.',
    'hire_car_other_events': 'Includes hire-car cost/provision for other insured events, including optional accident hire car. Excludes theft-only/not-at-fault-specific benefits and damage/liability insurance on the replacement vehicle.',
    'windscreen_and_window_glass': 'Includes glass repair/replacement and its cover terms. Excludes general body repairs; store actual excess amounts in excesses and link them to this benefit.',
    'towing_and_storage': 'Includes towing and storage after an insured event. Excludes roadside breakdown assistance and transport of people.',
    'transport_and_emergency_expenses': 'Includes personal transport, emergency accommodation, emergency transport and emergency repairs. Excludes hire-car provision and ordinary towing; split components into benefits when pool sub-limits distinguish them.',
    'new_car_replacement': 'Includes replacement of a qualifying total-loss car with a new car and eligibility thresholds. Excludes temporary replacement vehicles and ordinary repair/market-value settlement.',
    'repairs_and_parts_policy': 'Includes repair guarantees, parts standards and repair arrangements. Excludes repairer selection as a separate feature (choice_of_repairer), glass-only benefits and new-car replacement.',
    'choice_of_repairer': 'Includes who may choose the repairer and an optional right to choose. Excludes parts standards, guarantees and duplicate repair cost caps.',
    'personal_property': 'Includes personal belongings carried in the car. Excludes fixed modifications/accessories, business items, baby seats, pets and campervan contents with their own named benefit.',
    'baby_seats': 'Includes child restraints, baby seats and capsules. Excludes other belongings and injury to passengers.',
    'trailer_cover': 'Includes physical loss/damage to a trailer itself. Excludes damage caused by a trailer to someone else\'s property (caravans_and_trailers_tppd_extension).',
    'caravans_and_trailers_tppd_extension': 'Includes third-party property liability arising from an attached/used caravan or trailer. Excludes physical loss/damage to the trailer or caravan itself.',
    'uninsured_driver_damage_benefit': 'Includes own-vehicle damage under a special uninsured-other-driver benefit and its identification/fault conditions. Excludes liability to other people and the general comprehensive own-damage grant.',
    'substitute_car_liability': 'Includes third-party property liability while using a qualifying substitute vehicle. Excludes substitute-vehicle physical damage (temporary_replacement_vehicle) and hire fees.',
    'temporary_replacement_vehicle': 'Includes physical loss/damage cover for a qualifying temporary replacement vehicle itself. Excludes its hire fees and liability to third parties; preserve any source restrictions on vehicle type/use.',
    'keys_and_locks': 'Includes lost/stolen key replacement, lock replacement and recoding. Excludes general vehicle theft loss and belongings.',
    'roadside_assist': 'Includes roadside/breakdown assistance services. Excludes insured-event towing/storage unless the source expressly puts that service within this assistance benefit.',
    'counselling_services': 'Includes counselling treatment/fees following qualifying events. Excludes funeral/death benefits and general bodily injury liability.',
    'funeral_expenses': 'Includes stated funeral/death-related expense cover. Excludes counselling and compulsory bodily-injury insurance.',
    'business_items': 'Includes explicitly covered work/business items carried in the car. Excludes ordinary personal property and permanently fitted vehicle accessories.',
    'campervan_motorhome_contents': 'Includes expressly named campervan/motorhome contents cover. Excludes the vehicle itself and ordinary car belongings; this slot does not broaden the intake scope.',
    'other_documented_benefit': 'Includes an evidenced benefit not covered by any named category. Excludes all reserved categories, global exclusions, valuation and excesses; explain its novelty in source_label/details.',
    'no_claim_bonus_protection': 'Includes protection of a no-claim bonus/rating after qualifying claims. Excludes an ordinary discount without protection and any promise that the total premium will not rise.',
    'modifications_and_accessories': 'Includes cover for fitted accessories or vehicle modifications and disclosure/acceptance conditions. Excludes loose personal belongings, business items and child seats.',
    'pet_injury': 'Includes explicitly covered pet injury/veterinary expenses. Excludes human injuries, pet damage to the car and liability for damage caused by animals.',
}

STATUS_DEFINITIONS = (
    'included: base cover applies; a numeric cap alone does not make it limited. '
    'optional: requires a separately selectable option, linked by option_id; this takes precedence over limited/conditional. '
    'limited: base cover is expressly restricted to a subset of the named risk/service, beyond a mere money cap. '
    'conditional: base entitlement depends on a stated eligibility/approval condition and cannot be treated as unconditional. '
    'excluded: explicitly not covered. unknown: source cannot establish status. '
    'not_applicable: source explicitly establishes the feature does not apply to this product/vehicle, not simply that purchase is unnecessary. '
    'Keep supporting conditions/evidence; ordinary claim conditions alone do not require conditional.'
)
EVENTS = v2.DETAILS['covered_events']['properties']['event']['enum']
PERIOD_DESCRIPTION = (
    'per_claim only for an explicit claim-based limit; per_incident for any one event/occurrence/incident; '
    'per_policy_period for the contract/insurance period, not automatically a calendar year; per_day for daily rates. '
    'Use unknown if ambiguous and retain original wording. Multiple limits apply simultaneously when their conditions apply.'
)
RULES = {
    'identity': 'Nonblank product name/type/basis and nonempty identity evidence. IDs are unique within each product across all benefit fields, not across different products/tiers.',
    'amount_terms': 'fixed iff amount_aud is nonnull and nonnegative. percentage iff percentage and percentage_of are nonnull; percentage is 0..100. All other amount kinds have null numeric amounts and percentage fields.',
    'combination': 'single has exactly one term; lesser_of/greater_of have at least two distinct known terms. Terms express one monetary expression, not unrelated periods. Independent limits are simultaneous constraints.',
    'quantity': 'unknown/unlimited iff value is null; other comparisons need a nonnegative value. Percent quantities are 0..100. reference_kind and nonblank reference identify what is measured.',
    'addon_links': 'optional requires an option_id. Benefit option_id and addon benefit_ids agree in both directions. Option-linked benefits cannot be included base cover.',
    'addon_availability': 'not_available has explicit evidence and no benefit_ids; do not infer from omission. Optional benefits link only to available/conditional options. Separately priced/named option tiers are separate option_ids sharing a group with one_of selection.',
    'event_category': 'fire/theft/attempted_theft require fire_and_theft; other known event values require accidental_loss_or_damage. other requires event_text. Hire-car categories agree with their trigger.',
    'benefit_uniqueness': 'Reject duplicate category/normalized variant/option and duplicate IDs. Renamed paraphrases still require human review.',
    'pools': 'Shared caps live once in product.limit_pools; member_benefit_ids and benefit.limit_pool_ids agree reciprocally. At least two distinct members; excluded/not_applicable benefits are not members. Same numeric cap does not imply sharing.',
    'sublimits': 'Sub-limits belong to their pool and reference member benefit IDs. Every limit_id is unique within a product. Reject a copied pool/sub-limit expression in member standalone limits; comparable fixed sub-limits cannot exceed the pool.',
    'excess_scope': 'Excess amount rules apply; applicability says all_coverages, benefits, events, benefits_and_events or unknown and matches the nonempty reference lists. Benefit refs exist in this product. quantities encode driver/vehicle age and other thresholds.',
    'policy_rules': 'IDs and normalized global rule text are unique. waiting_period has a time-since-policy-start quantity, or explicit unknown duration. Global rules are not copied into benefit exclusions; benefit exclusions are benefit-specific.',
    'evidence': 'Every asserted nested record has nonempty evidence with a nonblank quote and positive PDF page. Document metadata includes a nonblank title and evidence; missing version/date stays null. Citation truth is checked by humans.',
    'missingness': '_unfilled enumerates null product fields exactly. [] means known no entries, null means unknown. Neither means excluded/not_available without evidence.',
    'tables': 'Document evidence tables have ordered positive page ranges and each row has exactly as many cells as columns.',
}


def ref(name):
    return {'$ref': '#/$defs/' + name}


def described(schema, text):
    return {**schema, 'description': text}


def definitions():
    evidence = deepcopy(v2.EVIDENCE)
    evidence['properties']['quote']['minLength'] = 1
    quantity = obj(
        metric=enum(['hours', 'days', 'months', 'years', 'percent', 'kilometres', 'claims', 'items', 'callouts']),
        value=number(), comparison=enum(['eq', 'lt', 'lte', 'gt', 'gte', 'unlimited', 'unknown']),
        reference_kind=enum(['driver_age', 'vehicle_age', 'time_since_purchase', 'time_since_policy_start', 'rental_duration', 'repair_duration', 'distance_from_home', 'claim_count', 'item_count', 'callout_count', 'other', 'unknown']),
        reference=described(string(), 'Nonblank baseline; years alone never identifies driver age versus vehicle age. Monetary percentages belong in amount terms, not quantities.'),
        conditions=string(True), evidence=array(ref('evidence')))
    term = obj(
        amount_kind=enum(['fixed', 'percentage', 'market_value', 'agreed_value', 'sum_insured', 'unknown', 'unlimited', 'reasonable_costs', 'schedule_specific']),
        amount_aud=number(), percentage={**number(), 'maximum': 100},
        percentage_of=enum(['sum_insured', 'market_value', 'agreed_value', 'claim_amount', 'repair_cost', 'premium', 'other'], True),
        reference_text=string(True))
    limit = obj(
        limit_id=string(), period=described(enum(['per_claim', 'per_incident', 'per_policy_period', 'per_day', 'unknown']), PERIOD_DESCRIPTION),
        basis=enum(['aggregate', 'per_person', 'per_item', 'per_driver', 'per_vehicle', 'unknown']),
        combination=enum(['single', 'lesser_of', 'greater_of']), terms=array(ref('amount_term')),
        conditions=string(True), source_text=string(), evidence=array(ref('evidence')))
    pool = obj(pool_id=string(), limit=ref('limit'), member_benefit_ids=array(string()),
               sub_limits=array(obj(benefit_id=string(), limit=ref('limit'))), evidence=array(ref('evidence')))
    document = v2.document_evidence_contract()
    document['properties']['metadata']['items'] = obj(
        document_title=string(), preparation_date=string(True), effective_date=string(True), version_code=string(True),
        vehicle_types=array(enum(['private_passenger_car', 'motorcycle', 'commercial_vehicle', 'campervan_motorhome', 'other', 'unknown'])),
        applicability_notes=string(True), evidence=array(ref('evidence')))
    return dict(evidence=evidence, quantity=quantity, amount_term=term, limit=limit, limit_pool=pool, document_evidence=document)


def _replace_evidence(node):
    if isinstance(node, list):
        return [_replace_evidence(v) for v in node]
    if isinstance(node, dict):
        if node == v2.EVIDENCE:
            return ref('evidence')
        return {k: _replace_evidence(v) for k, v in node.items()}
    return node


def build_revision(original):
    result = _replace_evidence(v2.build_revision(original))
    result.update(version=VERSION, validation_profile=PROFILE,
                  description='Review candidate v3: defined categories, product-scoped shared limit pools, percentage/composite amounts, explicit document schema and executable validation rules. Not approved.')
    result['$defs'] = definitions()
    result['document_evidence_schema'] = ref('document_evidence')
    result['validation_rules'] = deepcopy(RULES)
    fields = result['fields']
    by_name = {f['name']: f for f in fields}
    for owner, categories in OWNERS.items():
        if owner not in by_name:
            field = dict(name=owner, type='list[object]', required=False, applies_to=deepcopy(v2.TYPES), values=[], description='')
            fields.append(field)
            by_name[owner] = field
        field = by_name[owner]
        field['description'] = 'Sole owner for ' + ', '.join(categories) + '. Consult category definitions. Standalone limits apply together; shared amounts live only in limit_pools. Exclusions here are benefit-specific, never global rules.'
        details = deepcopy(v2.DETAILS.get(owner, obj(notes=string(True))))
        field['item_schema'] = obj(
            benefit_id=string(), category=enum(categories), variant=string(), source_label=string(),
            status=described(enum(['included', 'optional', 'excluded', 'limited', 'conditional', 'unknown', 'not_applicable']), STATUS_DEFINITIONS),
            option_id=string(True), limits=array(ref('limit')), limit_pool_ids=array(string()),
            quantities=array(ref('quantity')), conditions=array(string()), exclusions=array(string()), details=details, evidence=array(ref('evidence')))
    addons = by_name['available_addons']
    addons['description'] = 'Source-stated options, including explicit not_available. Unknown/omitted is not not_available. Separate mutually exclusive option tiers use separate option_ids with the same group ID; never infer purchase.'
    props = addons['item_schema']['properties']
    props['availability'] = enum(['available', 'conditional', 'not_available', 'unknown'])
    props['option_group_id'] = string(True)
    props['selection_rule'] = enum(['independent', 'one_of'])
    addons['item_schema']['required'] = list(props)
    excess = by_name['excesses']
    excess['description'] += ' Scope refs link to benefits/events. All stated benefit and event filters apply together. Driver-age thresholds use quantities with reference_kind=driver_age.'
    props = excess['item_schema']['properties']
    props.update(applicability=enum(['all_coverages', 'benefits', 'events', 'benefits_and_events', 'unknown']),
                 applies_to_benefit_ids=array(string()), applies_to_events=array(enum(EVENTS)), quantities=array(ref('quantity')))
    excess['item_schema']['required'] = list(props)
    rules = by_name['policy_rules']
    rules['description'] = 'Single owner for global use restrictions, general exclusions and inception waiting periods. Benefit-specific exclusions stay with the benefit. Quantities preserve hours/months and starting event.'
    props = rules['item_schema']['properties']
    props['kind'] = enum(['use_restriction', 'general_exclusion', 'waiting_period'])
    props['quantities'] = array(ref('quantity'))
    props['applies_to_benefit_ids'] = array(string())
    props['applies_to_events'] = array(enum(EVENTS))
    rules['item_schema']['required'] = list(props)
    fields.append(dict(name='limit_pools', type='list[object]', required=False, applies_to=deepcopy(v2.TYPES), values=[],
                       description='Product-scoped shared aggregate caps, NOT document-level caps across products. Own the cap and all its sub-limits exactly once. Members refer via limit_pool_ids; independent same-value limits are not a pool.', item_schema=ref('limit_pool')))
    result['taxonomies']['coverage_categories'] = [dict(canonical_name=c, description=DEFINITIONS[c] + ' Owner: ' + owner + '.') for owner, categories in OWNERS.items() for c in categories]
    result['notes'] = [
        'Review candidate v3, not human approval; v2 and original model draft remain unchanged. New user-proposed categories are slots only, never evidence that a PDF offers them.',
        'fields describes PRODUCT records. document_evidence_schema explicitly describes the separate DOCUMENT-level member; $defs contains local reusable definitions. Runtime provenance holds source path/hash, not model guesses.',
        'validation_rules is an explicit registry enforced by the review_v3 business validator after JSON Schema validation. Cross-record references are not expressible using simple JSON Schema alone.',
        'One source-named product/tier per record. Repeated benefit IDs and different statuses across different product records are expected; identity and references are product-local. Add-on configurations never create products.',
        'Example: included theft hire car and optional accident hire car are two benefit variants/categories, not one conditional benefit. Option tiers 14/21 days are two mutually exclusive options in one option_group_id, with separate benefit variants.',
        'limit_pools live inside each product, not alongside products. Split emergency transport/accommodation/repairs into member benefits when their sub-limits differ; all members refer to the pool, and do not repeat its amount in standalone limits.',
        'Standalone limits and pool limits apply simultaneously when their conditions apply. Daily cap + duration + total cap are independent constraints. lesser_of/greater_of combine amount terms inside one limit with one period/basis; alternatives with different conditions remain separate source variants.',
        'Percentage retains its reference without calculating missing values. Example lesser_of(fixed AUD 10000, market_value); never fabricate the market value from the PDS.',
        'not_required from v2 is replaced by not_applicable with an explicit meaning; do not auto-map old records. ' + STATUS_DEFINITIONS,
        'PDS version/date/vehicle scope are document evidence; null means unstated, not current or expired. Mixed-scope metadata does not authorize ingesting motorcycle/commercial products outside the car manifest.',
        'Refinement that changes this profile needs a versioned profile/validator update; do not strip local definitions or bypass the profile guard. Semantic evidence truth and paraphrased duplication still need human review.',
    ]
    return result


def validate_profile(schema, manifest):
    from src.schema.nested import validate_item_schema
    if manifest.vertical != 'car_insurance':
        raise ValueError('Car review_v3 cannot apply to another vertical')
    expected = build_revision(schema)
    keys = ['name', 'type', 'required', 'applies_to', 'values', 'item_schema']
    actual_fields = {f['name']: {k: f.get(k) for k in keys} for f in schema['fields']}
    expected_fields = {f['name']: {k: f.get(k) for k in keys} for f in expected['fields']}
    if actual_fields != expected_fields or schema['product_types'] != v2.TYPES:
        raise ValueError('Car review_v3 fields disagree with its versioned profile')
    for key in ('$defs', 'document_evidence_schema', 'validation_rules', 'taxonomies'):
        if schema.get(key) != expected[key]:
            raise ValueError('Car review_v3 profile mismatch: ' + key)
    for definition in schema['$defs'].values():
        validate_item_schema(definition, schema['$defs'])
    validate_item_schema(schema['document_evidence_schema'], schema['$defs'])


def require(condition, rule, detail):
    if not condition:
        raise ValueError(f'{rule}: {detail}')


def unique(items, key, rule):
    values = [item[key] for item in items]
    duplicates = sorted({v for v in values if values.count(v) > 1 and v.strip()})
    require(all(value.strip() for value in values) and not duplicates, rule,
            f'duplicate/blank {key}' + (f'; duplicates={duplicates}' if duplicates else '; blank value present'))


def references(values, allowed, rule):
    require(len(values) == len(set(values)) and all(v in allowed for v in values), rule, 'duplicate/unknown reference')


def check_term(term):
    kind = term['amount_kind']
    require((kind == 'fixed') == (term['amount_aud'] is not None), 'amount_terms', 'fixed/amount_aud mismatch')
    require((kind == 'percentage') == (term['percentage'] is not None), 'amount_terms', 'percentage/value mismatch')
    require((kind == 'percentage') == (term['percentage_of'] is not None), 'amount_terms', 'percentage/reference mismatch')
    if term['percentage_of'] == 'other':
        require(bool((term['reference_text'] or '').strip()), 'amount_terms', 'other percentage needs reference_text')


def check_limit(limit):
    terms = limit['terms']
    require(bool(limit['source_text'].strip()), 'evidence', 'limit needs source_text')
    require(len(terms) == 1 if limit['combination'] == 'single' else len(terms) >= 2, 'combination', 'wrong number of terms')
    for term in terms:
        check_term(term)
    keys = [json.dumps(t, sort_keys=True) for t in terms]
    require(len(keys) == len(set(keys)), 'combination', 'duplicate terms')
    if limit['combination'] != 'single':
        require(all(t['amount_kind'] != 'unknown' for t in terms), 'combination', 'unknown operand; retain source as unresolved single unknown')


def check_quantity(q):
    require((q['comparison'] in {'unknown', 'unlimited'}) == (q['value'] is None), 'quantity', 'comparison/value mismatch')
    require(bool(q['reference'].strip()), 'quantity', 'reference must identify baseline')
    if q['metric'] == 'percent' and q['value'] is not None:
        require(q['value'] <= 100, 'quantity', 'percent exceeds 100')
    if q['reference_kind'] in {'driver_age', 'vehicle_age'}:
        require(q['metric'] in {'years', 'months'}, 'quantity', 'age must use years/months')


def fingerprint(limit):
    # Evidence/ID may differ; monetary expression, scope and conditions may not.
    return json.dumps({k: limit[k] for k in ('period', 'basis', 'combination', 'terms', 'conditions')}, sort_keys=True)


def fixed_amount(limit):
    if limit['combination'] == 'single' and limit['terms'][0]['amount_kind'] == 'fixed':
        return limit['terms'][0]['amount_aud']
    return None


def validate_records(schema, payload, *, owners=None):
    from src.common.json_contracts import validate_inline_contract
    from src.schema.contract import compile_extraction_contract
    validate_inline_contract(payload, compile_extraction_contract(schema))
    for record_index, record in enumerate(payload['products']):
        for key in ('product_name', 'product_type', 'product_basis', 'product_identity_evidence'):
            require(bool(record[key]) and (not isinstance(record[key], str) or bool(record[key].strip())), 'identity', key)
        benefits = [b for owner in (OWNERS if owners is None else owners) for b in record[owner] or []]
        unique(benefits, 'benefit_id', 'benefit_uniqueness')
        by_id = {b['benefit_id']: b for b in benefits}
        addons = record['available_addons'] or []
        unique(addons, 'option_id', 'addon_links')
        by_option = {a['option_id']: a for a in addons}
        variants = {}
        all_limits = []
        for b in benefits:
            key = (b['category'], ' '.join(b['variant'].casefold().split()), b['option_id'])
            require(key not in variants and bool(key[1]) and bool(b['source_label'].strip()), 'benefit_uniqueness',
                    f"duplicate/blank benefit identity at $.products[{record_index}] benefit_id={b['benefit_id']}; "
                    f"(category, variant, option_id)={key}" + (f'; same identity as benefit_id={variants[key]}' if key in variants else
                    '; variant and source_label must be nonblank'))
            variants[key] = b['benefit_id']
            option_id = b['option_id']
            require(b['status'] != 'optional' or option_id is not None, 'addon_links', 'optional requires option_id')
            if option_id is not None:
                require(option_id in by_option, 'addon_links', 'unknown option')
                a = by_option[option_id]
                require(b['benefit_id'] in a['benefit_ids'] and b['status'] != 'included', 'addon_links', 'nonreciprocal or included option')
                require(a['availability'] != 'not_available', 'addon_availability', 'unavailable option cannot supply benefits')
                if b['status'] == 'optional':
                    require(a['availability'] in {'available', 'conditional'}, 'addon_availability', 'optional requires confirmed availability')
            if b['category'] in {'accidental_loss_or_damage', 'fire_and_theft'}:
                event = b['details']['event']
                if event == 'other':
                    require(bool((b['details']['event_text'] or '').strip()), 'event_category', 'other needs event_text')
                else:
                    require((event in {'fire', 'theft', 'attempted_theft'}) == (b['category'] == 'fire_and_theft'), 'event_category', 'event/category mismatch')
            triggers = {'hire_car_after_theft': 'after_theft', 'hire_car_not_at_fault': 'not_at_fault', 'hire_car_other_events': 'other_insured_event'}
            if b['category'] in triggers:
                require(b['details']['trigger'] == triggers[b['category']], 'event_category', 'hire trigger/category mismatch')
            if b['status'] in {'excluded', 'not_applicable'}:
                require(not b['limits'] and not b['limit_pool_ids'], 'pools', 'excluded/not-applicable cover cannot have payable limits')
            all_limits.extend(b['limits'])
        groups = {}
        for a in addons:
            require(bool(a['option_name'].strip()), 'addon_links', 'blank option name')
            references(a['benefit_ids'], by_id, 'addon_links')
            require(all(by_id[bid]['option_id'] == a['option_id'] for bid in a['benefit_ids']), 'addon_links', 'reverse reference mismatch')
            if a['availability'] == 'not_available':
                require(not a['benefit_ids'], 'addon_availability', 'not_available cannot have benefits')
            group = a['option_group_id']
            require((a['selection_rule'] == 'one_of') == (group is not None), 'addon_availability', 'group/selection mismatch')
            if group is not None:
                require(bool(group.strip()), 'addon_availability', 'blank option group')
                groups.setdefault(group, []).append(a)
        require(all(len(members) >= 2 for members in groups.values()), 'addon_availability', 'option tier group needs at least two documented options')
        pools = record['limit_pools'] or []
        unique(pools, 'pool_id', 'pools')
        by_pool = {p['pool_id']: p for p in pools}
        pool_keys = [(tuple(sorted(p['member_benefit_ids'])), fingerprint(p['limit'])) for p in pools]
        require(len(pool_keys) == len(set(pool_keys)), 'pools', 'duplicate pool for the same members and limit')
        for b in benefits:
            references(b['limit_pool_ids'], by_pool, 'pools')
            require(all(b['benefit_id'] in by_pool[pid]['member_benefit_ids'] for pid in b['limit_pool_ids']), 'pools', 'nonreciprocal member reference')
        for pool in pools:
            members = pool['member_benefit_ids']
            references(members, by_id, 'pools')
            require(len(members) >= 2, 'pools', 'pool needs multiple distinct benefits')
            require(all(pool['pool_id'] in by_id[bid]['limit_pool_ids'] for bid in members), 'pools', 'nonreciprocal pool reference')
            all_limits.append(pool['limit'])
            for bid in members:
                require(all(fingerprint(cap) != fingerprint(pool['limit']) for cap in by_id[bid]['limits']), 'sublimits', 'pool cap copied into a member')
            sub_keys = set()
            for sub in pool['sub_limits']:
                require(sub['benefit_id'] in members, 'sublimits', 'sub-limit outside pool')
                cap = sub['limit']
                key = (sub['benefit_id'], fingerprint(cap))
                require(key not in sub_keys, 'sublimits', 'duplicate sub-limit')
                sub_keys.add(key)
                require(all(fingerprint(cap) != fingerprint(other) for other in by_id[sub['benefit_id']]['limits']), 'sublimits', 'sub-limit copied into benefit')
                all_limits.append(cap)
                # Validate expressions before safely inspecting their terms below.
                check_limit(cap)
                check_limit(pool['limit'])
                parent_amount, sub_amount = fixed_amount(pool['limit']), fixed_amount(cap)
                comparable = all(cap[k] == pool['limit'][k] for k in ('period', 'basis', 'conditions'))
                if comparable and parent_amount is not None and sub_amount is not None:
                    require(sub_amount <= parent_amount, 'sublimits', 'sub-limit exceeds pool cap')
        unique(all_limits, 'limit_id', 'sublimits')
        for limit in all_limits:
            check_limit(limit)
        excesses = record['excesses'] or []
        unique(excesses, 'excess_id', 'excess_scope')
        for excess in excesses:
            require((excess['amount_kind'] == 'fixed') == (excess['amount_aud'] is not None), 'excess_scope', 'amount mismatch')
            refs, events = excess['applies_to_benefit_ids'], excess['applies_to_events']
            references(refs, by_id, 'excess_scope')
            references(events, EVENTS, 'excess_scope')
            shape = {'all_coverages': (False, False), 'unknown': (False, False), 'benefits': (True, False), 'events': (False, True), 'benefits_and_events': (True, True)}
            require((bool(refs), bool(events)) == shape[excess['applicability']], 'excess_scope', 'scope/ref mismatch')
        rules = record['policy_rules'] or []
        unique(rules, 'rule_id', 'policy_rules')
        texts = [' '.join(r['text'].casefold().split()) for r in rules]
        require(all(texts) and len(set(texts)) == len(texts), 'policy_rules', 'duplicate/blank rule')
        for rule in rules:
            references(rule['applies_to_benefit_ids'], by_id, 'policy_rules')
            references(rule['applies_to_events'], EVENTS, 'policy_rules')
            if rule['kind'] == 'waiting_period':
                require(any(q['reference_kind'] == 'time_since_policy_start' and q['metric'] in {'hours', 'days', 'months', 'years'} for q in rule['quantities']), 'policy_rules', 'waiting period needs inception duration')
        for b in benefits:
            require(not any(' '.join(s.casefold().split()) in texts for s in b['exclusions']), 'policy_rules', 'global rule copied into benefit exclusions')
        for item in v2._objects(record):
            if 'quantities' in item:
                for q in item['quantities']:
                    check_quantity(q)
        nulls = {f['name'] for f in schema['fields'] if record[f['name']] is None}
        listed = set(record['_unfilled'])
        require(nulls == listed, 'missingness', f'_unfilled/null mismatch at $.products[{record_index}]; '
                f'null but not in _unfilled={sorted(nulls - listed)}; in _unfilled but not null={sorted(listed - nulls)}')
    metadata = payload['document_evidence']['metadata']
    require(bool(metadata) and all(m['document_title'].strip() for m in metadata), 'evidence', 'document title/evidence required; version/date may be unknown')
    for item in v2._objects(payload):
        if 'evidence' in item:
            require(bool(item['evidence']), 'evidence', 'empty evidence')
        if set(item) == set(v2.EVIDENCE['properties']):
            require(bool(item['quote'].strip()), 'evidence', 'blank source quote')
    for table in payload['document_evidence']['coverage_summary_tables']:
        require(table['pdf_page_end'] >= table['pdf_page_start'], 'tables', 'reversed page range')
        require(all(len(row['cells']) == len(table['columns']) for row in table['rows']), 'tables', 'column mismatch')


def main():
    parser = argparse.ArgumentParser(description='Generate a separate v3 review candidate without model calls.')
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    source_bytes = args.source.read_bytes()
    original = json.loads(source_bytes)
    schema = build_revision(original.get('data', original))
    from src.schema.contract import compile_extraction_contract
    contract = compile_extraction_contract(schema)
    provenance = dict(status='review_candidate_not_approved', source=str(args.source.resolve()),
                      source_sha256=hashlib.sha256(source_bytes).hexdigest(), version=VERSION, model_calls=0,
                      review_basis='User supplied schema review; engineering implementation, not blanket human approval.')
    artifacts = {'schema_review_v3.json': schema, 'extraction_contract_v3.json': contract, 'revision_v3_provenance.json': provenance}
    targets = [args.output_dir / name for name in artifacts]
    if any(path.exists() for path in targets):
        raise FileExistsError('Refusing to overwrite v3 artifacts; use a fresh directory')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, value in artifacts.items():
        with (args.output_dir / name).open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
    print(f'Created v3 review candidate: {len(schema["fields"])} fields, {len(DEFINITIONS)} categories; no model calls.')


if __name__ == '__main__':
    main()
