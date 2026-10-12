"""Candidate v4: exception scope, source accountability and change-of-car cover."""
from copy import deepcopy

from . import schema_revision_v3 as v3
from .schema_revision import array, enum, obj, string
from .source_coverage import DAILY, STACKING, EXCEPTION, has_daily_cost
from src.verticals.manifest import PROJECT_ROOT
from src.verticals.registry import get_prompt

GUIDANCE = get_prompt(str(PROJECT_ROOT / "prompts/car_insurance/review_v4.md"))

PROFILE = 'car_insurance.review_v4'
VERSION = '2026-10-05.review.v4'
OWNERS = {**v3.OWNERS, 'change_of_vehicle_cover': ['change_of_vehicle_cover']}


def build_revision(original):
    schema = v3.build_revision(original)
    schema.update(version=VERSION, validation_profile=PROFILE,
                  description='Review candidate v4: source-anchored omission checks, scoped rule exceptions, explicit transfer-of-cover and structured costs/excess combinations. Not approved.')
    fields = {f['name']: f for f in schema['fields']}
    transfer = deepcopy(fields['temporary_replacement_vehicle_cover'])
    transfer.update(name='change_of_vehicle_cover', description='Automatic/conditional transfer of cover after acquiring a replacement car or selling/disposing of the old car. NOT temporary substitution during repair, hire expenses, or new-car total-loss replacement.')
    transfer['item_schema']['properties']['category'] = enum(['change_of_vehicle_cover'])
    transfer['item_schema']['properties']['details'] = obj(trigger=string(), notification_requirement=string(True), notes=string(True))
    schema['fields'].append(transfer)
    for field in schema['fields']:
        if field['name'] in OWNERS or field['name'] in {'policy_rules', 'excesses'}:
            props = field['item_schema']['properties']
            props['source_clause_ids'] = array(string())
            field['item_schema']['required'] = list(props)
    rule = fields['policy_rules']
    rule['description'] += ' Preserve every operative clause and explicitly scoped exceptions; source_clause_ids bind source blocks, not broad topic labels.'
    scopes = ['own_vehicle_damage', 'third_party_liability', 'both', 'source_defined', 'none', 'unknown']
    rule['item_schema']['properties']['exceptions'] = array(obj(
        condition=string(), effect=string(), preserved_cover=enum(scopes), not_restored_cover=enum(scopes),
        scope_notes=string(), evidence=array(v3.ref('evidence'))))
    rule['item_schema']['required'].append('exceptions')
    schema['$defs']['quantity']['properties']['reference_kind']['enum'].append('time_since_vehicle_change')
    schema['taxonomies']['coverage_categories'].append(dict(canonical_name='change_of_vehicle_cover',
        description='Includes transfer of existing policy cover to an acquired replacement vehicle after sale/disposal/purchase, with notification deadlines. Excludes temporary substitute-vehicle physical damage/liability, hire fees and new-car total-loss replacement. Owner: change_of_vehicle_cover.'))
    schema['validation_rules'].update(
        source_accountability='At runtime inventory high-signal source blocks from the actual model input. Require each source ID in the correct field owner with full matching page evidence, at least once in the document. Unknown/stale IDs or missing blocks fail; this does not prove correct per-tier applicability or semantic interpretation.',
        scoped_exceptions='Source exclusion exception cues require exceptions[] with complete evidence; nonblank condition/effect/scope_notes. An own-damage restoration that excludes liability must preserve both scopes.',
        structured_costs='Reasonable daily hire cost in source/evidence must appear in limits as reasonable_costs/per_day, not only a quote.',
        excess_combination='Explicit in-addition-to excess evidence requires a nonempty combination_rule; it does not authorize adding unknown amounts.',
        transfer_cover='Change-of-vehicle source must map to its own field and a time_since_vehicle_change quantity; do not merge with rental or temporary substitution.')
    schema['notes'] = [n.replace('v3', 'v4').replace('review_v3', 'review_v4') for n in schema['notes']]
    schema['notes'].extend([
        'Scope/coverage checks are conservative source cues, not exhaustive legal interpretation. Evidence completeness does not establish that a summary is faithful. Unknown section formats and multi-tier applicability still need review.',
        'All source_clause_ids refer to the exact source input for this run. No automatic in-place migration of prior extraction results; retain original artifacts and rerun only with authorization.',
        'Run the actual SchemaExtractor to apply source-aware checks; standalone JSON/profile validation cannot know which source clauses were omitted.',
    ])
    return schema


def validate_profile(schema, manifest):
    if manifest.vertical != 'car_insurance':
        raise ValueError('Car review_v4 requires car_insurance')
    expected = build_revision(schema)
    keys = ['name', 'type', 'required', 'applies_to', 'values', 'item_schema']
    actual = {f['name']: {k: f.get(k) for k in keys} for f in schema['fields']}
    wanted = {f['name']: {k: f.get(k) for k in keys} for f in expected['fields']}
    if actual != wanted or schema['product_types'] != expected['product_types']:
        raise ValueError('Car review_v4 field/profile mismatch')
    for key in ('$defs', 'document_evidence_schema', 'validation_rules', 'taxonomies'):
        if schema.get(key) != expected[key]:
            raise ValueError('Car review_v4 profile mismatch: ' + key)


def validate_records(schema, payload):
    v3.validate_records(schema, payload, owners=OWNERS)
    for product in payload['products']:
        targets = [b for owner in OWNERS for b in product[owner] or []]
        targets += (product['policy_rules'] or []) + (product['excesses'] or [])
        for item in targets:
            ids = item['source_clause_ids']
            v3.require(all(i.strip() for i in ids) and len(ids) == len(set(ids)), 'source_accountability', 'blank/duplicate source ID')
        for rule in product['policy_rules'] or []:
            evidence = ' '.join(e['quote'] for e in rule['evidence'])
            if EXCEPTION.search(evidence):
                v3.require(bool(rule['exceptions']), 'scoped_exceptions', 'exception cue without explicit exception')
            for exception in rule['exceptions']:
                v3.require(all(exception[k].strip() for k in ('condition', 'effect', 'scope_notes')), 'scoped_exceptions', 'blank exception meaning/scope')
        for benefit in product['hire_car_benefits'] or []:
            evidence = ' '.join(e['quote'] for e in benefit['evidence'])
            if DAILY.search(evidence):
                v3.require(has_daily_cost(benefit), 'structured_costs', 'reasonable daily cost missing from limit')
        for excess in product['excesses'] or []:
            text = ' '.join(excess['conditions'] + [e['quote'] for e in excess['evidence']])
            if STACKING.search(text):
                v3.require(bool((excess['combination_rule'] or '').strip()), 'excess_combination', 'missing stacking rule')
