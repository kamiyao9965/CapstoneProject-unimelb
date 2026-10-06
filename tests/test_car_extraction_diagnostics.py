"""Offline repair and source-relation regressions; no real PDF/API required."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest

from src.car_insurance.extraction_diagnostics import (local_issues, policy_wide_rule_issues, source_issues,
                                                     relation_issues, validate_runtime)
from src.car_insurance.source_coverage import inventory
from src.common.structured_output import BusinessDiagnostics, StructuredOutputFailure, run_structured_output
from src.common.model_provider import StructuredOutputSpec
from tests import test_car_schema_revision_v4 as fixtures
from tests.test_car_schema_revision_v3 import limit, term
from tests.test_structured_output import request, SequenceProvider


LIABILITY = fixtures.source('In this section, your car includes an attached trailer as well as a substitute car.',
    'The most we pay for all legal liability claims arising from any one incident is $12,000,000.', page=37)
OPTION = fixtures.source('If we agree, you may add the Extended cover option. This option provides cover caused by fire, theft or attempted theft.',
    'If you have the Extended cover option, we pay repairs up to the amount on your Certificate of Insurance, or for a total loss the market value, whichever is lower.', page=42)
CUES = fixtures.source('General Exclusions', 'Condition of car',
    'There is no cover if, at the time of the incident, your car:',
    '• did not meet registration requirements; or • was unroadworthy, unless its condition did not cause the incident.',
    'Use of car', 'There is no cover if your car was being used:',
    '• to deliver goods for reward; • to carry passengers for reward, except when:',
    '• it is unpaid carpooling; or • it is declared ridesharing;',
    'Other loss', 'There is no cover for: • tyre damage unless caused in a covered incident; '
    '• mechanical failure, unless caused in a covered incident; • wear and tear.', 'Claims', 'Contact us.', page=9)


class DiagnosticsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.CarSchemaV4Test.setUpClass.__func__(cls)

    setUp = fixtures.CarSchemaV4Test.setUp
    add = fixtures.CarSchemaV4Test.add
    fill_missing = fixtures.CarSchemaV4Test.fill_missing
    check = fixtures.CarSchemaV4Test.check
    rules = fixtures.CarSchemaV4Test.rules

    def test_table_path_counts_and_exception_reported_together(self):
        clauses = self.rules()
        self.record['policy_rules'][0]['exceptions'] = []
        self.data['document_evidence']['coverage_summary_tables'] = [dict(
            title='Synthetic', columns=['Feature', 'Cover'], rows=[dict(label='Hire', cells=['costs'])],
            pdf_page_start=1, pdf_page_end=1, notes=None, footnotes=[])]
        self.fill_missing()
        with self.assertRaises(BusinessDiagnostics) as caught:
            validate_runtime(self.data, lambda _: self.check(), clauses, fixtures.RULE_SOURCE)
        errors = caught.exception.errors
        self.assertTrue(any(e['path'].endswith('.rows[0].cells') and 'expected 2, got 1' in e['message'] for e in errors))
        self.assertTrue(any(e['path'].endswith('.exceptions') and 'rule_id=rule_0' in e['message'] for e in errors))

    def test_exception_nested_full_quote_suffices_without_mutation(self):
        clauses = self.rules()
        self.record['policy_rules'][0]['evidence'].pop()
        before = deepcopy(self.data)
        self.assertEqual(source_issues(self.data, clauses), [])
        self.assertEqual(before, self.data)

    def test_partial_nested_exception_still_fails(self):
        clauses = self.rules()
        self.record['policy_rules'][0]['exceptions'][0]['evidence'][0]['quote'] = 'The above exclusions do not apply'
        self.assertTrue(any('explicit exceptions' in e['message'] for e in source_issues(self.data, clauses)))

    def test_wrong_page_nested_evidence_does_not_restore_coverage(self):
        clauses = self.rules()
        rule = self.record['policy_rules'][0]
        rule['evidence'].pop()
        rule['exceptions'][0]['evidence'][0]['pdf_page'] += 1
        errors = source_issues(self.data, clauses)
        self.assertTrue(any('evidence_mismatch' in e['message'] for e in errors))
        self.assertFalse(any('missing source clause' in e['message'] for e in errors))

    def test_exact_ordered_snippets_allowed_but_missing_words_not(self):
        clauses = self.rules()
        r = self.record['policy_rules'][-1]
        ev = r['evidence'][0]
        r['evidence'] = [{**ev, 'quote': 'There is no cover'}, {**ev, 'quote': 'for war or nuclear material.'}]
        self.assertEqual(source_issues(self.data, clauses), [])
        r['evidence'][1]['quote'] = 'for nuclear material.'
        self.assertTrue(source_issues(self.data, clauses))
        r['evidence'].reverse()
        self.assertTrue(source_issues(self.data, clauses))

    def test_missing_mapping_distinct_from_quote_mismatch(self):
        clauses = self.rules()
        self.record['policy_rules'].pop()
        errors = source_issues(self.data, clauses)
        self.assertTrue(any('source_mapping_missing' in e['message'] for e in errors))

    def cue_rules(self):
        clauses = self.rules(CUES)
        by_id = {sid: r for r in self.record['policy_rules'] for sid in r['source_clause_ids']}
        cue = {c.context: c for c in clauses if c.has_exception}
        return clauses, by_id, cue

    def quote_exception(self, rule, *quotes, page=9):
        rule['exceptions'] = [dict(rule['exceptions'][0] if rule['exceptions'] else dict(
            condition='Carve-out applies', effect='Cover is not excluded', preserved_cover='source_defined',
            not_restored_cover='unknown', scope_notes='Source-defined'),
            evidence=[dict(pdf_page=page, printed_page=None, section=None, quote=q) for q in quotes])]

    def test_cue_bullet_suffices_when_parent_has_full_block(self):
        clauses, by_id, cue = self.cue_rules()
        rule = by_id[cue['Condition of car'].source_id]
        self.quote_exception(rule, 'was unroadworthy, unless its condition did not cause the incident.')
        self.assertEqual(source_issues(self.data, clauses, CUES), [])

    def test_bare_unless_fragment_fails_and_names_required_bullet(self):
        clauses, by_id, cue = self.cue_rules()
        rule = by_id[cue['Condition of car'].source_id]
        self.quote_exception(rule, 'unless its condition did not cause the incident.')
        errors = [e['message'] for e in source_issues(self.data, clauses, CUES)]
        self.assertTrue(any('existing exceptions=1' in m and '"was unroadworthy, unless its condition did not cause the incident."' in m
                            for m in errors))

    def test_fragment_requires_parent_full_block(self):
        clauses, by_id, cue = self.cue_rules()
        rule = by_id[cue['Condition of car'].source_id]
        self.quote_exception(rule, 'was unroadworthy, unless its condition did not cause the incident.')
        rule['evidence'] = [e for e in rule['evidence'] if 'unroadworthy' not in e['quote']]
        self.assertTrue(any('evidence_mismatch' in e['message'] for e in source_issues(self.data, clauses, CUES)))

    def test_added_words_do_not_count_as_verbatim_fragment(self):
        clauses, by_id, cue = self.cue_rules()
        rule = by_id[cue['Condition of car'].source_id]
        self.quote_exception(rule, 'Note: was unroadworthy, unless its condition did not cause the incident.')
        self.assertTrue(any('explicit exceptions' in e['message'] for e in source_issues(self.data, clauses, CUES)))

    def test_colon_cue_satisfied_by_complete_following_block_only(self):
        clauses, by_id, cue = self.cue_rules()
        rule = by_id[cue['Use of car'].source_id]
        self.quote_exception(rule, '• it is unpaid carpooling; or • it is declared ridesharing;')
        self.assertEqual(source_issues(self.data, clauses, CUES), [])
        self.quote_exception(rule, '• it is unpaid carpooling;')
        errors = [e['message'] for e in source_issues(self.data, clauses, CUES)]
        self.assertTrue(any('following block on PDF page 9' in m for m in errors))
        # Without source text, the cross-block allowance is unavailable (fail closed).
        self.quote_exception(rule, '• it is unpaid carpooling; or • it is declared ridesharing;')
        self.assertTrue(source_issues(self.data, clauses))

    def test_every_cue_in_block_needs_an_exception(self):
        clauses, by_id, cue = self.cue_rules()
        rule = by_id[cue['Other loss'].source_id]
        self.quote_exception(rule, 'tyre damage unless caused in a covered incident;')
        errors = [e['message'] for e in source_issues(self.data, clauses, CUES)]
        self.assertTrue(any('"mechanical failure, unless caused in a covered incident;"' in m and 'tyre damage' not in m.split('bullet(s)')[-1]
                            for m in errors))
        self.quote_exception(rule, 'tyre damage unless caused in a covered incident;', 'mechanical failure, unless caused in a covered incident;')
        self.assertEqual(source_issues(self.data, clauses, CUES), [])

    def test_missing_exception_diagnostic_quotes_cue_sentence(self):
        self.cue_rules()
        rule = next(r for r in self.record['policy_rules'] if r['topic'] == 'Other loss')
        rule['exceptions'] = []
        errors = [e['message'] for e in local_issues(self.data)]
        self.assertTrue(any('"tyre damage unless caused in a covered incident;"' in m for m in errors))

    def test_glued_printed_page_number_may_be_omitted_but_not_words(self):
        text = fixtures.source('General Exclusions', 'Seizure',
                               'We do not cover confiscation or repossession of your car or its contents. 18', 'Claims', 'Contact us.', page=18)
        clauses = self.rules(text)
        rule = self.record['policy_rules'][0]
        rule['evidence'][0]['quote'] = 'We do not cover confiscation or repossession of your car or its contents.'
        self.assertEqual(source_issues(self.data, clauses, text), [])
        rule['evidence'][0]['quote'] = 'We do not cover confiscation of your car or its contents.'
        self.assertTrue(source_issues(self.data, clauses, text))

    def test_eg_abbreviation_does_not_split_cue_sentence(self):
        from src.car_insurance.source_coverage import exception_cues
        self.assertEqual(exception_cues('• costs to prove your loss (e.g. calls, postage) unless stated otherwise; • travel.'),
                         ['costs to prove your loss (e.g. calls, postage) unless stated otherwise;'])

    def scope_errors(self):
        return [e for e in local_issues(self.data) if 'exception_scope' in e['message']]

    def test_exception_scope_pairs_follow_convention(self):
        self.rules()
        exc = self.record['policy_rules'][0]['exceptions'][0]
        self.assertEqual(self.scope_errors(), [])  # own/liability with explicit liability evidence
        for pair, allowed in [(('both', 'none'), True), (('source_defined', 'source_defined'), True),
                              (('source_defined', 'unknown'), False), (('own_vehicle_damage', 'unknown'), False)]:
            exc['preserved_cover'], exc['not_restored_cover'] = pair
            self.assertEqual(self.scope_errors() == [], allowed, pair)

    def test_own_damage_split_requires_liability_evidence(self):
        self.rules()
        exc = self.record['policy_rules'][0]['exceptions'][0]
        exc['evidence'] = [{**exc['evidence'][0], 'quote': 'The above exclusions do not apply if you had no reason to suspect this.'}]
        errors = self.scope_errors()
        self.assertTrue(errors and 'explicitly distinguishes liability' in errors[0]['message'])

    def test_market_value_limit_must_be_per_vehicle(self):
        benefits = self.option()
        cap = self.record['limit_pools'][0]['limit']
        cap['basis'] = 'aggregate'
        errors = [e for e in local_issues(self.data) if 'limit_basis' in e['message']]
        self.assertEqual([e['path'] for e in errors], ['$.products[0].limit_pools[0].limit.basis'])
        cap['basis'] = 'per_vehicle'
        benefits[0]['limits'] = [limit('fixed_only', 100)]  # no market_value term: basis not constrained
        self.assertFalse(any('limit_basis' in e['message'] for e in local_issues(self.data)))

    def liability(self):
        benefits = [self.add(owner, category, category) for owner, category in [
            ('third_party_property_liability', 'third_party_property_damage'),
            ('substitute_car_liability_feature', 'substitute_car_liability'),
            ('caravans_and_trailers_tppd_extension', 'caravans_and_trailers_tppd_extension')]]
        for b in benefits:
            b['limit_pool_ids'] = ['liability']
        self.record['limit_pools'] = [dict(pool_id='liability', limit=limit('combined', 12000000),
            member_benefit_ids=[b['benefit_id'] for b in benefits], sub_limits=[], evidence=deepcopy(benefits[0]['evidence']))]
        return benefits

    def test_shared_liability_relation_not_just_correct_core_number(self):
        benefits = self.liability()
        self.assertEqual(relation_issues(self.data, LIABILITY), [])
        self.record['limit_pools'] = None
        for b in benefits:
            b['limit_pool_ids'] = []
        benefits[0]['limits'] = [limit('core', 12000000)]
        self.assertTrue(any('shared_liability_relation' in e['message'] for e in relation_issues(self.data, LIABILITY)))

    def test_policy_wide_liability_cap_applies_to_every_product(self):
        text = fixtures.source('The most we will pay for all claims from any one incident for legal liability covered by '
                               'this policy is $20 million, including all associated legal costs.', page=27)
        benefits = self.liability()
        self.record['limit_pools'][0]['limit']['terms'][0]['amount_aud'] = 20000000
        self.data['products'].append(deepcopy(self.record))
        self.assertEqual(relation_issues(self.data, text), [])
        second = self.data['products'][1]
        second['limit_pools'] = None
        for owner in ('third_party_property_liability', 'substitute_car_liability_feature', 'caravans_and_trailers_tppd_extension'):
            for b in second[owner]:
                b['limit_pool_ids'] = []
                b['limits'] = [limit(b['benefit_id'], 20000000)]
        errors = relation_issues(self.data, text)
        self.assertEqual([e['path'] for e in errors], ['$.products[1].limit_pools'])
        self.assertIn('parsed amount=20000000.0', errors[0]['message'])

    def test_policy_wide_cap_wrong_amount_rejected_single_member_exempt(self):
        text = fixtures.source('The most we will pay for all claims from any one incident for legal liability covered by '
                               'this policy is $20 million.', page=27)
        self.liability()  # pool amount 12,000,000 does not match the stated 20 million
        self.assertTrue(relation_issues(self.data, text))
        for owner in ('substitute_car_liability_feature', 'caravans_and_trailers_tppd_extension'):
            self.record[owner] = []
        self.record['limit_pools'] = None
        self.record['third_party_property_liability'][0]['limit_pool_ids'] = []
        self.assertEqual(relation_issues(self.data, text), [])

    def test_incomplete_liability_pool_rejected(self):
        benefits = self.liability()
        self.record['limit_pools'][0]['member_benefit_ids'].pop()
        benefits[-1]['limit_pool_ids'] = []
        self.assertTrue(relation_issues(self.data, LIABILITY))

    def test_common_pool_wrong_source_amount_or_period_rejected(self):
        self.liability()
        cap = self.record['limit_pools'][0]['limit']
        cap['terms'][0]['amount_aud'] = 1
        self.assertTrue(relation_issues(self.data, LIABILITY))
        cap['terms'][0]['amount_aud'] = 12000000
        cap['period'] = 'per_day'
        self.assertTrue(relation_issues(self.data, LIABILITY))

    def option(self):
        benefits = [self.add('covered_events', 'fire_and_theft', event) for event in ['fire', 'theft', 'attempted_theft']]
        for b, event in zip(benefits, ['fire', 'theft', 'attempted_theft']):
            b.update(status='optional', option_id='extra', limit_pool_ids=['events'])
            b['details']['event'] = event
        self.record['available_addons'] = [dict(option_id='extra', option_name='Extended cover option',
            availability='available', benefit_ids=[b['benefit_id'] for b in benefits])]
        cap = limit('events_cap')
        cap.update(combination='lesser_of', terms=[term('schedule_specific', None), term('market_value', None)])
        self.record['limit_pools'] = [dict(pool_id='events', limit=cap,
            member_benefit_ids=[b['benefit_id'] for b in benefits], sub_limits=[], evidence=deepcopy(benefits[0]['evidence']))]
        return benefits

    def test_three_events_keep_common_total_loss_rule(self):
        self.option()
        self.assertEqual(relation_issues(self.data, OPTION), [])
        self.record['covered_events'].pop()
        self.assertTrue(any('shared_event_payout' in e['message'] for e in relation_issues(self.data, OPTION)))

    def test_attempted_theft_repair_only_rejected(self):
        benefits = self.option()
        benefits[-1]['limit_pool_ids'] = []
        benefits[-1]['limits'] = [limit('repair')]
        self.record['limit_pools'][0]['member_benefit_ids'].pop()
        self.assertTrue(relation_issues(self.data, OPTION))

    def test_no_source_cue_no_invented_pool_requirement(self):
        self.option()
        self.record['limit_pools'] = None
        self.assertEqual(relation_issues(self.data, fixtures.source('These events have independent limits.')), [])
        self.assertEqual(relation_issues(self.data, OPTION.replace('Extended cover option', 'Another option')), [])

    def test_policy_wide_general_exclusions_apply_to_every_product(self):
        text = fixtures.RULE_SOURCE.replace('General Exclusions\n',
            'General Exclusions\n<!-- text block_id=p7_text_cue -->\nThese general exclusions apply to all sections of your policy.\n', 1)
        clauses = self.rules(text)
        self.data['products'].append(deepcopy(self.record))
        self.assertEqual(policy_wide_rule_issues(self.data, clauses, text), [])
        self.data['products'][1]['policy_rules'] = []
        errors = policy_wide_rule_issues(self.data, clauses, text)
        self.assertEqual([e['path'] for e in errors], ['$.products[1].policy_rules'])
        self.assertIn(f'maps 0/{len(clauses)}', errors[0]['message'])
        self.data['products'][1]['policy_rules'] = deepcopy(self.record['policy_rules'][:1])
        self.assertIn(f'maps 1/{len(clauses)}', policy_wide_rule_issues(self.data, clauses, text)[0]['message'])

    def test_general_exclusions_scope_not_inferred_without_cue(self):
        clauses = self.rules()
        self.data['products'].append(deepcopy(self.record))
        self.data['products'][1]['policy_rules'] = []
        self.assertEqual(policy_wide_rule_issues(self.data, clauses, fixtures.RULE_SOURCE), [])
        self.data['products'].pop()  # single product: checklist coverage already applies
        self.assertEqual(policy_wide_rule_issues(self.data, clauses,
            fixtures.RULE_SOURCE.replace('General Exclusions', 'General Exclusions apply to all sections of your policy')), [])

    def test_multi_product_scope_not_inferred(self):
        self.liability()
        self.record['limit_pools'] = None
        self.data['products'].append(deepcopy(self.record))
        self.assertEqual(relation_issues(self.data, LIABILITY), [])


class RepairContextTest(unittest.TestCase):
    def run_sequence(self, values, **kwargs):
        schema = dict(type='object', required=['a', 'b'], additionalProperties=False,
                      properties={'a': {'type': 'integer'}, 'b': {'type': 'integer'}})
        req = replace(request(), structured_output=StructuredOutputSpec(name='test', schema=schema))
        provider = SequenceProvider([json.dumps(v) for v in values])
        def validate(data):
            errors = [dict(path=f'$.{k}', message=f'{k} must be positive') for k, v in data.items() if v <= 0]
            if errors:
                raise BusinessDiagnostics(errors)
        result = run_structured_output(provider, req, data_contract_schema=schema, business_validator=validate,
            retain_repair_context=True, **kwargs)
        return result, provider

    def test_prior_fixes_and_errors_retained_with_latest_candidate_only(self):
        result, provider = self.run_sequence([{'a': 0, 'b': 1}, {'a': 2, 'b': 0}, {'a': 2, 'b': 3}])
        text = provider.requests[2].user_text
        self.assertIn('$.a: a must be positive', text)
        self.assertIn('$.b: b must be positive', text)
        self.assertIn('{"a":2,"b":0}', text)
        self.assertNotIn('{"a":0,"b":1}', text)
        self.assertEqual(result.data, {'a': 2, 'b': 3})

    def test_oversize_candidate_omitted_not_truncated(self):
        _, provider = self.run_sequence([{'a': 0, 'b': 1}, {'a': 2, 'b': 3}], repair_candidate_max_chars=1)
        self.assertIn('candidate omitted', provider.requests[1].user_text)
        self.assertNotIn('{"a":0', provider.requests[1].user_text)

    def test_reintroduced_old_error_still_fails_closed(self):
        with self.assertRaises(StructuredOutputFailure) as caught:
            self.run_sequence([{'a': 0, 'b': 1}, {'a': 2, 'b': 0}, {'a': 0, 'b': 3}])
        self.assertEqual(len(caught.exception.result.attempts), 3)
        self.assertIsNone(caught.exception.result.data)
        self.assertEqual(caught.exception.result.errors[0]['path'], '$.a')

    def test_diagnostic_and_history_growth_is_bounded(self):
        from src.common.structured_output import _repair_text, _repair_context, StructuredAttempt
        from src.common.model_provider import ModelResponse
        errors = [dict(path='$.a', message='x' * 100000)]
        text = _repair_text('original', errors, 1, detail_budget=32000)
        self.assertLess(len(text), 33000)
        response = ModelResponse(text='{"a":1}', provider='openai', model='synthetic')
        context = _repair_context([StructuredAttempt(1, response, tuple(errors))], 240000)
        self.assertLess(len(context), 33000)
        self.assertIn('{"a":1}', context)

    def test_default_path_does_not_include_prior_candidate(self):
        from src.common.structured_output import _repair_text
        text = _repair_text('source', [dict(path='$.x', message='wrong')], 1)
        self.assertNotIn('LATEST CANDIDATE', text)


if __name__ == '__main__':
    unittest.main()
