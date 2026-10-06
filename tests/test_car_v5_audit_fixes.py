from copy import deepcopy
import unittest
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock
from src.car_insurance.source_coverage import inventory, following_blocks, uncovered_exception_cues
from src.car_insurance.extraction_diagnostics import trailer_liability_issues, classify_diagnostic
from tests.test_car_schema_revision_v4 import source


class AuditFixesTest(unittest.TestCase):
    def cue(self, text):
        return next(c for c in inventory(text) if c.has_exception)

    def test_next_exclusion_is_not_exception_continuation(self):
        text = source('General Exclusions', 'There is no cover for hire but we will provide cover if your car is:',
                      'Incorrect fuel usage loss caused by incorrect fuel.')
        c = self.cue(text)
        nxt = following_blocks(text)
        self.assertNotIn(c.source_id, nxt)
        self.assertTrue(uncovered_exception_cues([{'evidence': [{'pdf_page':7,
            'quote':'Incorrect fuel usage loss caused by incorrect fuel.'}]}], c, nxt))

    def test_inline_bullets_cannot_borrow_the_next_block(self):
        text = source('General Exclusions', 'There is no cover for hire except when: • it is unpaid carpooling;',
                      '• it is declared ridesharing;')
        self.assertNotIn(self.cue(text).source_id, following_blocks(text))

    def test_connected_bullets_remain_supported(self):
        text = source('General Exclusions', 'There is no cover for hire except when:',
                      '• it is unpaid carpooling; or • you have declared ridesharing;')
        c = self.cue(text)
        self.assertIn(c.source_id, following_blocks(text))

    def test_new_bullet_exclusion_and_far_page_rejected(self):
        base = source('General Exclusions', 'There is no cover for hire except when:')
        for child in [source('• No cover for incorrect fuel.', page=8),
                      source('• it is unpaid carpooling;', page=10)]:
            self.assertNotIn(self.cue(base).source_id, following_blocks(base + child))

    def test_unless_followed_by_explicit_condition_bullet(self):
        text = source('General Exclusions', 'There is no cover during the initial period unless:',
                      '• you had another policy immediately before this policy;')
        c = self.cue(text)
        self.assertIn(c.source_id, following_blocks(text))

    def text(self, include=True):
        return source('1. Legal Liability', 'This applies if you have Gold or Silver cover with us.',
            'What is covered?', 'The driver has legal liability for third party property damage. ' +
            ('This includes when the car is being used to tow a trailer or caravan.' if include else ''),
            'The most we will pay for each claim is $12,000,000.', 'What is not covered?',
            'Damage to a trailer or caravan.', page=39)

    def payload(self):
        return {'products': [dict(product_name=name, caravans_and_trailers_tppd_extension=[],
                                 third_party_property_liability=[], substitute_car_liability_feature=[], limit_pools=[])
                             for name in ['Gold','Silver','Bronze']]}

    def test_named_tiers_only_trailer_property_exclusion_not_confused(self):
        data = self.payload()
        before = deepcopy(data)
        errors = trailer_liability_issues(data, self.text())
        self.assertEqual(len(errors), 2)
        self.assertTrue(all('trailer_liability_missing' in e['message'] for e in errors))
        self.assertEqual(data, before)
        self.assertEqual(trailer_liability_issues(data, self.text(False)), [])

    def test_trailer_membership_and_per_claim_pool(self):
        data = self.payload()
        for p in data['products'][:2]:
            p['caravans_and_trailers_tppd_extension'] = [dict(benefit_id='trailer',status='included',limit_pool_ids=['pool'])]
            p['third_party_property_liability'] = [dict(benefit_id='core',status='included',limit_pool_ids=['pool'])]
            p['limit_pools'] = [dict(pool_id='pool',member_benefit_ids=['core','trailer'],limit=dict(
                period='per_claim',basis='aggregate',combination='single',terms=[dict(amount_kind='fixed',amount_aud=12000000)]))]
        self.assertEqual(trailer_liability_issues(data, self.text()), [])
        data['products'][0]['limit_pools'][0]['limit']['period'] = 'per_incident'
        self.assertEqual(len(trailer_liability_issues(data, self.text())),1)

    def test_evidence_failure_is_not_automatically_semantic_omission(self):
        for message in ['evidence_mismatch', 'exclusion exception needs explicit exceptions[] with full source evidence']:
            self.assertEqual(classify_diagnostic({'path':'$', 'message':message})['category'], 'evidence_issue')
        self.assertEqual(classify_diagnostic({'path':'$', 'message':'trailer_liability_missing'})['category'],
                         'structured_content_missing')

    def test_timeout_lookup_only_retrieves_and_preserves_status(self):
        from src.car_insurance.inspect_timeout import inspect
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / 'run_plan.json').write_text(json.dumps({'split':'development','provider':'openai'}), encoding='utf-8')
            status = dict(error_type='TimeoutError',error='Response resp_test123 still in_progress after 1800s')
            status_path = run_dir / 'run_status.json'
            status_path.write_text(json.dumps(status), encoding='utf-8')
            before = status_path.read_bytes()
            responses = SimpleNamespace(retrieve=Mock(return_value=SimpleNamespace(
                status='completed',created_at=1,completed_at=2502,usage=None,output_text='{}')))
            result = inspect(SimpleNamespace(responses=responses), run_dir)
            responses.retrieve.assert_called_once_with('resp_test123')
            self.assertEqual(result['model_creations'],0)
            self.assertEqual(result['status'],'completed')
            self.assertEqual(status_path.read_bytes(),before)
            responses.retrieve.side_effect = RuntimeError('secret must not be echoed')
            result = inspect(SimpleNamespace(responses=responses), run_dir)
            self.assertEqual(result['status'],'lookup_failed')
            self.assertNotIn('secret',json.dumps(result))


if __name__ == '__main__':
    unittest.main()
