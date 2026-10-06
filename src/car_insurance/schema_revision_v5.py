"""Candidate v5: policy-wide rules stored once at document level and referenced by products."""
from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path

from . import schema_revision_v3 as v3
from . import schema_revision_v4 as v4
from .schema_revision import enum

PROFILE = 'car_insurance.review_v5'
VERSION = '2026-10-06.review.v5'
OWNERS = v4.OWNERS
APPLICABILITY = ['applies', 'does_not_apply']
V4_PLACEMENT = """Each checklist source_id must occur in source_clause_ids of the appropriate rule,
benefit or excess in at least one applicable product. IDs do not assert applicability
to all tiers: apply every global rule to each affected tier, preserving qualifications.
"""
V5_PLACEMENT = """Each checklist source_id must occur in source_clause_ids of the appropriate rule,
benefit or excess, in shared_policy_rules or in an applicable product.
Rules the source applies to the whole policy / all sections / all covers (general
exclusions, general conditions) go ONCE in top-level shared_policy_rules, also for a
single-product PDF; never copy them into products. Set every product's
shared_policy_rules_applicability: applies, or does_not_apply only with explicit evidence.
Rules that only some tiers have stay in those products' policy_rules.
"""
assert V4_PLACEMENT in v4.GUIDANCE
GUIDANCE = v4.GUIDANCE.replace('review_v4', 'review_v5').replace(V4_PLACEMENT, V5_PLACEMENT)


def build_revision(original):
    schema = v4.build_revision(original)
    schema.update(version=VERSION, validation_profile=PROFILE,
                  description='Review candidate v5: v4 plus document-level shared_policy_rules referenced by each product, '
                              'so policy-wide general exclusions are stored once. Not approved.')
    schema['fields'].append(dict(
        name='shared_policy_rules_applicability', type='enum', required=False, applies_to=deepcopy(schema['product_types']),
        values=list(APPLICABILITY),
        description='Whether the document-level shared_policy_rules apply to this product. applies when the source states '
                    'they apply to the whole policy/all sections; does_not_apply only with explicit evidence; null+_unfilled if unknown.'))
    schema['validation_rules'].update(
        shared_policy_rules='Policy-wide rules live once in top-level shared_policy_rules (same item schema as policy_rules, '
                            'applies_to_benefit_ids=[]). A source ID or rule text in shared_policy_rules may not be repeated in '
                            'a product. Each product states shared_policy_rules_applicability; applies requires nonempty shared rules.')
    schema['notes'] = [n.replace('v4', 'v5').replace('review_v4', 'review_v5') for n in schema['notes']]
    schema['notes'].append('v5 changes only where policy-wide rules are stored; v4 results are not migrated in place.')
    return schema


def shared_rules_contract(schema):
    rules = next(f for f in schema['fields'] if f['name'] == 'policy_rules')
    return {'type': 'array', 'items': deepcopy(rules['item_schema'])}


def validate_profile(schema, manifest):
    if manifest.vertical != 'car_insurance':
        raise ValueError('Car review_v5 requires car_insurance')
    expected = build_revision(schema)
    keys = ['name', 'type', 'required', 'applies_to', 'values', 'item_schema']
    actual = {f['name']: {k: f.get(k) for k in keys} for f in schema['fields']}
    wanted = {f['name']: {k: f.get(k) for k in keys} for f in expected['fields']}
    if actual != wanted or schema['product_types'] != expected['product_types']:
        raise ValueError('Car review_v5 field/profile mismatch')
    for key in ('$defs', 'document_evidence_schema', 'validation_rules', 'taxonomies'):
        if schema.get(key) != expected[key]:
            raise ValueError('Car review_v5 profile mismatch: ' + key)


def _text(rule):
    return ' '.join(rule['text'].casefold().split())


def validate_records(schema, payload):
    v4.validate_records(schema, payload)
    shared = payload['shared_policy_rules']
    v3.unique(shared, 'rule_id', 'shared_policy_rules')
    texts = [_text(r) for r in shared]
    v3.require(all(texts) and len(set(texts)) == len(texts), 'shared_policy_rules', 'duplicate/blank shared rule')
    for rule in shared:  # the same per-rule checks v4 applies to product policy_rules
        ids = rule['source_clause_ids']
        v3.require(all(i.strip() for i in ids) and len(ids) == len(set(ids)), 'source_accountability', 'blank/duplicate source ID')
        v3.require(not rule['applies_to_benefit_ids'], 'shared_policy_rules',
                   f"shared rule_id={rule['rule_id']} cannot reference product benefit IDs; express its scope with applies_to_events/text")
        v3.references(rule['applies_to_events'], v3.EVENTS, 'shared_policy_rules')
        evidence = ' '.join(e['quote'] for e in rule['evidence'])
        if v4.EXCEPTION.search(evidence):
            v3.require(bool(rule['exceptions']), 'scoped_exceptions', 'exception cue without explicit exception')
        for exception in rule['exceptions']:
            v3.require(all(exception[k].strip() for k in ('condition', 'effect', 'scope_notes')), 'scoped_exceptions',
                       'blank exception meaning/scope')
    shared_ids = {i for r in shared for i in r['source_clause_ids']}
    for pi, product in enumerate(payload['products']):
        own = product['policy_rules'] or []
        repeated = sorted(shared_ids & {i for r in own for i in r['source_clause_ids']})
        repeated += [r['rule_id'] for r in own if _text(r) in texts]
        v3.require(not repeated, 'shared_policy_rules',
                   f'$.products[{pi}].policy_rules repeats shared rules {repeated[:5]}; keep them only in shared_policy_rules')
        v3.require(product['shared_policy_rules_applicability'] != 'applies' or bool(shared), 'shared_policy_rules',
                   f'$.products[{pi}] applies shared_policy_rules but none are given')


def main():
    parser = argparse.ArgumentParser(description='Build v5 candidate offline without changing v4 artifacts.')
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    raw = args.source.read_bytes()
    source = json.loads(raw)
    schema = build_revision(source.get('data', source))
    from src.schema.contract import compile_extraction_contract
    artifacts = {'schema_review_v5.json': schema, 'extraction_contract_v5.json': compile_extraction_contract(schema),
                 'revision_v5_provenance.json': dict(source=str(args.source.resolve()), source_sha256=hashlib.sha256(raw).hexdigest(),
                     version=VERSION, status='review_candidate_not_approved', model_calls=0)}
    if any((args.output_dir / name).exists() for name in artifacts):
        raise FileExistsError('Refusing to overwrite v5 artifacts')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, value in artifacts.items():
        with (args.output_dir / name).open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
    print('Created v5 review candidate; no API calls.')


if __name__ == '__main__':
    main()
