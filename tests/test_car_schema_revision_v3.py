"""Synthetic schema tests, never policy labels or real model calls."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.car_insurance.schema_revision_v3 import build_revision, OWNERS, RULES
from src.schema.contract import compile_extraction_contract
from src.schema.nested import resolve_local_refs, validate_item_schema
from src.schema.validation import validate_extraction_record, validate_schema_mapping
from src.schema_application.extractor import SchemaExtractor
from src.common.model_provider import _project_openai_schema
from src.common.structured_output import StructuredOutputFailure
from src.verticals.manifest import resolve_manifest
from tests.test_car_insurance import synthetic_schema, RecordingProvider
from tests.test_car_schema_revision import EVIDENCE


def empty(spec, definitions):
    spec = resolve_local_refs(spec, definitions)
    types = spec['type']
    if isinstance(types, list) and 'null' in types:
        return None
    if types == 'object':
        return {k: empty(v, definitions) for k, v in spec['properties'].items()}
    if types == 'array':
        return []
    if 'enum' in spec:
        return spec['enum'][0]
    if types in ('number', 'integer'):
        return 1
    return 'synthetic'


def term(kind='fixed', amount=750, percentage=None, percentage_of=None):
    return dict(amount_kind=kind, amount_aud=amount, percentage=percentage, percentage_of=percentage_of, reference_text=None)


def limit(identity='cap', amount=750):
    return dict(limit_id=identity, period='per_incident', basis='aggregate', combination='single',
                terms=[term(amount=amount)], conditions=None, source_text='Synthetic limit, not a real policy', evidence=deepcopy(EVIDENCE))


def quantity(metric='hours', value=72, comparison='lt', reference_kind='time_since_policy_start'):
    return dict(metric=metric, value=value, comparison=comparison, reference_kind=reference_kind,
                reference='Synthetic baseline', conditions=None, evidence=deepcopy(EVIDENCE))


class CarSchemaV3Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = build_revision(synthetic_schema())
        cls.manifest = resolve_manifest(vertical='car_insurance')
        cls.contract = compile_extraction_contract(cls.schema)

    def setUp(self):
        self.record = {f['name']: None for f in self.schema['fields']}
        self.record.update(product_name='Synthetic base', product_type='third_party_property_damage',
                           product_basis='named_product', product_identity_evidence=deepcopy(EVIDENCE), _notes=None, _unfilled=[])
        self.data = dict(products=[self.record], _document_notes=None, document_evidence=dict(
            metadata=[dict(document_title='Synthetic PDS', preparation_date=None, effective_date=None, version_code=None,
                           vehicle_types=['private_passenger_car'], applicability_notes=None, evidence=deepcopy(EVIDENCE))], coverage_summary_tables=[]))

    def add(self, owner='baby_seats_benefit', category='baby_seats', identity='benefit_1'):
        spec = next(f['item_schema'] for f in self.schema['fields'] if f['name'] == owner)
        b = empty(spec, self.schema['$defs'])
        b.update(benefit_id=identity, category=category, variant=identity, source_label='Synthetic benefit',
                 status='included', option_id=None, evidence=deepcopy(EVIDENCE))
        if self.record[owner] is None:
            self.record[owner] = []
        self.record[owner].append(b)
        return b

    def fill_missing(self):
        for record in self.data['products']:
            record['_unfilled'] = [f['name'] for f in self.schema['fields'] if record[f['name']] is None]

    def check(self):
        self.fill_missing()
        validate_extraction_record(self.schema, self.data, manifest=self.manifest)

    def option(self):
        b = self.add('hire_car_benefits', 'hire_car_other_events')
        b['details']['trigger'] = 'other_insured_event'
        b.update(status='optional', option_id='hire_14')
        a = dict(option_id='hire_14', option_name='Synthetic 14 day option', availability='available',
                 additional_premium_required=True, conditions=[], benefit_ids=[b['benefit_id']], evidence=deepcopy(EVIDENCE),
                 option_group_id=None, selection_rule='independent')
        self.record['available_addons'] = [a]
        return b, a

    def pool(self):
        a = self.add('transport_and_emergency_expenses_benefit', 'transport_and_emergency_expenses', 'transport')
        b = self.add('transport_and_emergency_expenses_benefit', 'transport_and_emergency_expenses', 'accommodation')
        a['details']['covered_components'] = ['emergency_transport']
        b['details']['covered_components'] = ['emergency_accommodation']
        a['limit_pool_ids'] = b['limit_pool_ids'] = ['pool']
        pool = dict(pool_id='pool', limit=limit('shared', 750), member_benefit_ids=['transport', 'accommodation'],
                    sub_limits=[dict(benefit_id='accommodation', limit=limit('room_cap', 500))], evidence=deepcopy(EVIDENCE))
        self.record['limit_pools'] = [pool]
        return a, b, pool

    def excess(self):
        b = self.add('windscreen_and_window_glass_cover', 'windscreen_and_window_glass')
        spec = next(f['item_schema'] for f in self.schema['fields'] if f['name'] == 'excesses')
        e = empty(spec, self.schema['$defs'])
        e.update(excess_id='glass_excess', excess_type='windscreen_reduced', amount_kind='fixed', amount_aud=100,
                 period='per_claim', applicability='benefits', applies_to_benefit_ids=[b['benefit_id']], evidence=deepcopy(EVIDENCE))
        self.record['excesses'] = [e]
        return e

    def rule(self):
        r = dict(rule_id='inception', kind='waiting_period', topic='weather', status='excluded', text='Synthetic initial weather exclusion',
                 conditions=[], evidence=deepcopy(EVIDENCE), quantities=[quantity()], applies_to_benefit_ids=[], applies_to_events=['storm', 'flood'])
        self.record['policy_rules'] = [r]
        return r

    def test_candidate_and_document_definition(self):
        validate_schema_mapping(self.schema)
        self.check()
        self.assertIn('document_evidence', self.contract['properties'])
        self.assertEqual(self.schema['document_evidence_schema'], {'$ref': '#/$defs/document_evidence'})
        self.assertEqual(set(self.schema['validation_rules']), set(RULES))

    def test_all_categories_have_boundaries_and_one_owner(self):
        categories = [c for values in OWNERS.values() for c in values]
        self.assertEqual(len(categories), len(set(categories)))
        for category in self.schema['taxonomies']['coverage_categories']:
            self.assertIn('Includes', category['description'])
            self.assertIn('Excludes', category['description'])

    def test_provider_projection_keeps_local_defs(self):
        projected = _project_openai_schema(self.contract, strict=True)
        self.assertIn('limit_pool', projected['$defs'])
        self.assertEqual(projected['properties']['document_evidence']['$ref'], '#/$defs/document_evidence')

    def test_v3_cannot_strip_hard_rules_or_definitions(self):
        for key in ('$defs', 'validation_rules', 'document_evidence_schema'):
            schema = deepcopy(self.schema)
            del schema[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                compile_extraction_contract(schema)

    def test_local_ref_unknown_remote_and_cycles_rejected(self):
        for ref, defs in [('https://invalid.example/schema', {}), ('#/$defs/missing', {}), ('#/$defs/a', {'a': {'$ref': '#/$defs/a'}})]:
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                validate_item_schema({'$ref': ref}, defs)

    def test_pool_and_sublimit_valid(self):
        self.pool()
        self.check()

    def test_pool_unknown_member(self):
        _, _, pool = self.pool()
        pool['member_benefit_ids'].append('missing')
        with self.assertRaisesRegex(ValueError, 'pools'): self.check()

    def test_pool_reciprocal_membership_required(self):
        a, _, _ = self.pool()
        a['limit_pool_ids'] = []
        with self.assertRaisesRegex(ValueError, 'pools'): self.check()

    def test_duplicate_pool_not_disguised_by_new_id(self):
        a, b, pool = self.pool()
        duplicate = deepcopy(pool)
        duplicate['pool_id'] = 'copy'
        duplicate['limit']['limit_id'] = 'copy_cap'
        duplicate['sub_limits'] = []
        self.record['limit_pools'].append(duplicate)
        a['limit_pool_ids'] = ['pool', 'copy']
        b['limit_pool_ids'] = ['pool', 'copy']
        with self.assertRaisesRegex(ValueError, 'duplicate pool'): self.check()

    def test_shared_pool_can_itself_have_per_person_basis(self):
        _, _, pool = self.pool()
        pool['limit']['basis'] = 'per_person'
        pool['sub_limits'][0]['limit']['basis'] = 'per_person'
        self.check()

    def test_no_shared_with_or_inline_pool_amount(self):
        a, _, pool = self.pool()
        a['limits'] = [deepcopy(pool['limit'])]
        a['limits'][0]['limit_id'] = 'copy'
        with self.assertRaisesRegex(ValueError, 'copied'): self.check()
        a['limits'] = []
        a['shared_with'] = ['accommodation']
        with self.assertRaises(ValueError): self.check()

    def test_sublimit_outside_pool(self):
        _, _, pool = self.pool()
        pool['sub_limits'][0]['benefit_id'] = 'missing'
        with self.assertRaisesRegex(ValueError, 'sublimits'): self.check()

    def test_sublimit_cannot_exceed_comparable_pool(self):
        _, _, pool = self.pool()
        pool['sub_limits'][0]['limit']['terms'][0]['amount_aud'] = 800
        with self.assertRaisesRegex(ValueError, 'exceeds'): self.check()

    def test_sublimit_not_copied_into_benefit(self):
        _, b, pool = self.pool()
        b['limits'] = [deepcopy(pool['sub_limits'][0]['limit'])]
        b['limits'][0]['limit_id'] = 'copy'
        with self.assertRaisesRegex(ValueError, 'copied'): self.check()

    def test_different_scopes_allow_separate_daily_cap(self):
        _, b, _ = self.pool()
        b['limits'] = [limit('daily', 100)]
        b['limits'][0]['period'] = 'per_day'
        self.check()

    def test_identical_independent_caps_need_no_pool(self):
        a = self.add(identity='a')
        b = self.add(identity='b')
        a['limits'] = [limit('a_cap', 500)]
        b['limits'] = [limit('b_cap', 500)]
        self.check()

    def test_percentage_and_market_value_combination(self):
        b = self.add()
        cap = limit()
        cap.update(combination='lesser_of', terms=[term(amount=10000), term('percentage', None, 10, 'sum_insured')])
        b['limits'] = [cap]
        self.check()
        cap['terms'][1] = term('market_value', None)
        self.check()
        cap['combination'] = 'greater_of'
        self.check()

    def test_percentage_requires_reference_and_no_aud_value(self):
        b = self.add()
        cap = limit()
        b['limits'] = [cap]
        for t in [term('percentage', None, 10, None), term('percentage', 100, 10, 'sum_insured'), term('percentage', None, 101, 'sum_insured')]:
            cap['terms'] = [t]
            with self.subTest(term=t), self.assertRaises(ValueError): self.check()

    def test_fixed_null_and_unknown_numeric_rejected(self):
        b = self.add()
        b['limits'] = [limit()]
        for t in [term(amount=None), term('unknown', 500), term('unlimited', 0), term(amount=-1)]:
            b['limits'][0]['terms'] = [t]
            with self.subTest(term=t), self.assertRaises(ValueError): self.check()

    def test_combination_arity_and_duplicate_terms(self):
        b = self.add()
        cap = limit()
        b['limits'] = [cap]
        for combination, terms in [('single', []), ('single', [term(), term(amount=100)]), ('lesser_of', [term()]), ('greater_of', [term(), term()])]:
            cap.update(combination=combination, terms=terms)
            with self.subTest(combination=combination), self.assertRaises(ValueError): self.check()

    def test_daily_days_and_total_cap(self):
        b = self.add('hire_car_benefits', 'hire_car_after_theft')
        b['details']['trigger'] = 'after_theft'
        b['limits'] = [limit('daily', 90), limit('total', 1000)]
        b['limits'][0]['period'] = 'per_day'
        b['quantities'] = [quantity('days', 14, 'lte', 'rental_duration')]
        self.check()

    def test_event_category_and_hire_trigger(self):
        b = self.add('covered_events', 'accidental_loss_or_damage')
        b['details']['event'] = 'theft'
        with self.assertRaisesRegex(ValueError, 'event_category'): self.check()
        b['category'] = 'fire_and_theft'
        self.check()
        hire = self.add('hire_car_benefits', 'hire_car_after_theft', 'hire')
        hire['details']['trigger'] = 'not_at_fault'
        with self.assertRaisesRegex(ValueError, 'event_category'): self.check()

    def test_other_event_needs_source_label(self):
        b = self.add('covered_events', 'accidental_loss_or_damage')
        b['details'].update(event='other', event_text=None)
        with self.assertRaisesRegex(ValueError, 'event_category'): self.check()

    def test_addon_reciprocal_and_included_rejected(self):
        b, a = self.option()
        self.check()
        a['benefit_ids'] = []
        with self.assertRaisesRegex(ValueError, 'addon_links'): self.check()
        a['benefit_ids'] = [b['benefit_id']]
        b['status'] = 'included'
        with self.assertRaisesRegex(ValueError, 'addon_links'): self.check()

    def test_explicit_unavailable_is_not_silence(self):
        b, a = self.option()
        a['availability'] = 'not_available'
        with self.assertRaisesRegex(ValueError, 'addon_availability'): self.check()
        self.record['hire_car_benefits'] = []
        a['benefit_ids'] = []
        self.check()
        a['evidence'] = []
        with self.assertRaisesRegex(ValueError, 'evidence'): self.check()

    def test_option_tiers_are_mutually_exclusive_options(self):
        b, a = self.option()
        a.update(option_group_id='hire_duration', selection_rule='one_of')
        with self.assertRaisesRegex(ValueError, 'two documented options'): self.check()
        b2 = deepcopy(b)
        b2.update(benefit_id='hire_21_benefit', variant='21_days', option_id='hire_21')
        self.record['hire_car_benefits'].append(b2)
        a2 = deepcopy(a)
        a2.update(option_id='hire_21', option_name='Synthetic 21 day option', benefit_ids=[b2['benefit_id']])
        self.record['available_addons'].append(a2)
        b['quantities'] = [quantity('days', 14, 'lte', 'rental_duration')]
        b2['quantities'] = [quantity('days', 21, 'lte', 'rental_duration')]
        self.check()

    def test_included_theft_and_optional_accident_remain_separate(self):
        self.option()
        b = self.add('hire_car_benefits', 'hire_car_after_theft', 'theft_hire')
        b['details']['trigger'] = 'after_theft'
        self.check()

    def test_different_products_can_repeat_ids_with_different_status(self):
        b, _ = self.option()
        other = deepcopy(self.record)
        other['product_name'] = 'Synthetic Plus'
        other['product_basis'] = 'named_tier'
        other['available_addons'] = []
        other['hire_car_benefits'][0].update(status='included', option_id=None)
        self.data['products'].append(other)
        self.check()
        other['product_name'] = self.record['product_name']
        with self.assertRaisesRegex(ValueError, 'Duplicate product identity'): self.check()

    def test_excess_scope_and_driver_age(self):
        e = self.excess()
        e['quantities'] = [quantity('years', 25, 'lt', 'driver_age')]
        self.check()
        e['applies_to_benefit_ids'] = ['unknown']
        with self.assertRaisesRegex(ValueError, 'excess_scope'): self.check()

    def test_excess_applicability_must_match_refs(self):
        e = self.excess()
        e['applicability'] = 'all_coverages'
        with self.assertRaisesRegex(ValueError, 'excess_scope'): self.check()

    def test_waiting_period_hours_and_vehicle_months(self):
        self.rule()
        b = self.add('new_car_replacement', 'new_car_replacement')
        b['quantities'] = [quantity('months', 24, 'lte', 'time_since_purchase')]
        self.check()

    def test_waiting_period_requires_duration_baseline(self):
        r = self.rule()
        r['quantities'] = []
        with self.assertRaisesRegex(ValueError, 'policy_rules'): self.check()

    def test_global_exclusion_not_repeated_in_benefit(self):
        r = self.rule()
        b = self.add()
        b['exclusions'] = [r['text']]
        with self.assertRaisesRegex(ValueError, 'policy_rules'): self.check()

    def test_unknown_unlimited_quantity_and_age_units(self):
        b = self.add()
        b['quantities'] = [quantity('days', None, 'unlimited', 'rental_duration')]
        self.check()
        for q in [quantity('days', 0, 'unlimited'), quantity('percent', 101), quantity('kilometres', 25, 'lt', 'driver_age')]:
            b['quantities'] = [q]
            with self.subTest(quantity=q), self.assertRaisesRegex(ValueError, 'quantity'): self.check()

    def test_new_named_categories(self):
        for owner, category in [('pet_injury_benefit', 'pet_injury'), ('no_claim_bonus_protection', 'no_claim_bonus_protection'), ('modifications_and_accessories', 'modifications_and_accessories')]:
            self.add(owner, category, owner)
        self.check()

    def test_document_title_and_evidence_required(self):
        self.data['document_evidence']['metadata'] = []
        with self.assertRaisesRegex(ValueError, 'evidence'): self.check()

    def test_blank_identity_evidence_quote_rejected(self):
        self.record['product_identity_evidence'][0]['quote'] = '  '
        with self.assertRaisesRegex(ValueError, 'evidence'): self.check()

    def test_limit_ids_unique_product_wide(self):
        a, _, _ = self.pool()
        a['limits'] = [limit('shared', 100)]
        with self.assertRaisesRegex(ValueError, 'limit_id'): self.check()

    def test_missingness_rejected(self):
        self.fill_missing()
        self.record['_unfilled'] = []
        with self.assertRaisesRegex(ValueError, 'missingness'):
            validate_extraction_record(self.schema, self.data, manifest=self.manifest)

    def test_runtime_valid_and_semantically_invalid_provider_output(self):
        self.pool()
        self.fill_missing()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'synthetic.pdf'
            path.touch()
            with patch('src.schema_application.extractor.render_pdf_paths_for_prompt', return_value='Synthetic input'):
                engine = SchemaExtractor(self.schema, manifest=self.manifest, provider=RecordingProvider(self.data), log=None)
                self.assertTrue(engine.structured_output_strict)
                self.assertIn('"document_evidence" at top level', engine.extraction_prompt)
                self.assertEqual(engine.extract_one(path), self.data)
                self.record['limit_pools'][0]['sub_limits'][0]['limit']['terms'][0]['amount_aud'] = 800
                engine = SchemaExtractor(self.schema, manifest=self.manifest, provider=RecordingProvider(self.data), log=None)
                with self.assertRaises(StructuredOutputFailure): engine.extract_one(path)


if __name__ == '__main__':
    unittest.main()
