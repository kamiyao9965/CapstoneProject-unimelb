from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.car_insurance.schema_revision_v4 import build_revision, OWNERS
from src.car_insurance.source_coverage import inventory, coverage_issues, require_coverage
from src.schema.contract import compile_extraction_contract
from src.schema.validation import validate_extraction_record
from src.schema_application.extractor import SchemaExtractor
from src.common.model_provider import _project_openai_schema
from src.common.structured_output import StructuredOutputFailure
from tests.test_car_insurance import synthetic_schema, RecordingProvider
from tests import test_car_schema_revision_v3 as v3tests
from tests.test_car_schema_revision_v3 import empty, limit, quantity


def source(*blocks, page=7):
    return f'<!-- page {page} -->\n' + '\n'.join(f'<!-- text block_id=p{page}_text_{i} -->\n{text}\n' for i, text in enumerate(blocks))


RULE_SOURCE = source('General Exclusions', 'Driver', 'There is no cover if a driver is unlicensed.',
    'The above exclusions do not apply if you had no reason to suspect this. We cover otherwise covered damage to your car, but not any legal liability caused by that driver.',
    'War and nuclear', 'There is no cover for war or nuclear material.', 'Claims', 'Contact us to claim.')


def evidence(clause):
    return dict(pdf_page=clause.pdf_page, printed_page=None, section=clause.context, quote=clause.text)


class CarSchemaV4Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = build_revision(synthetic_schema())
        from src.verticals.manifest import resolve_manifest
        cls.manifest = resolve_manifest(vertical='car_insurance')
        cls.contract = compile_extraction_contract(cls.schema)

    def setUp(self):
        v3tests.CarSchemaV3Test.setUp(self)

    add = v3tests.CarSchemaV3Test.add
    fill_missing = v3tests.CarSchemaV3Test.fill_missing
    check = v3tests.CarSchemaV3Test.check

    def rules(self, text=RULE_SOURCE):
        clauses = inventory(text)
        rules = []
        for i, c in enumerate(clauses):
            r = dict(rule_id=f'rule_{i}', kind='general_exclusion', topic=c.context, status='excluded',
                     text=c.text, conditions=[], quantities=[], applies_to_benefit_ids=[], applies_to_events=[],
                     evidence=[evidence(c)], source_clause_ids=[c.source_id], exceptions=[])
            if c.has_exception:
                r['exceptions'] = [dict(condition='Insured had no reason to suspect the excluded driver circumstances',
                    effect='Otherwise covered own damage remains covered, but liability is not restored.',
                    preserved_cover='own_vehicle_damage', not_restored_cover='third_party_liability',
                    scope_notes='Separate own damage and third-party liability', evidence=[evidence(c)])]
            if c.has_exception and rules and rules[-1]['topic'] == c.context:
                rules[-1]['exceptions'].extend(r['exceptions'])
                rules[-1]['evidence'].extend(r['evidence'])
                rules[-1]['source_clause_ids'].extend(r['source_clause_ids'])
            else:
                rules.append(r)
        self.record['policy_rules'] = rules
        return clauses

    def test_v4_profile_and_provider_projection(self):
        self.check()
        _project_openai_schema(self.contract, strict=True)
        self.assertIn('change_of_vehicle_cover', {f['name'] for f in self.schema['fields']})

    def test_inventory_covers_exclusion_and_exception_across_topics(self):
        clauses = self.rules()
        self.assertEqual(len(clauses), 3)
        self.check()
        require_coverage(self.data, clauses, OWNERS)

    def test_source_omission_rejected_despite_valid_structure(self):
        clauses = self.rules()
        self.record['policy_rules'].pop()
        self.check()
        with self.assertRaisesRegex(ValueError, 'missing source clause'):
            require_coverage(self.data, clauses, OWNERS)

    def test_exception_cannot_be_only_in_evidence(self):
        self.rules()
        self.record['policy_rules'][0]['exceptions'] = []
        with self.assertRaisesRegex(ValueError, 'scoped_exceptions'): self.check()

    def test_wrong_restored_scope_rejected(self):
        clauses = self.rules()
        self.record['policy_rules'][0]['exceptions'][0]['not_restored_cover'] = 'none'
        self.check()
        with self.assertRaisesRegex(ValueError, 'liability not restored'):
            require_coverage(self.data, clauses, OWNERS)

    def test_partial_quote_cannot_claim_full_source_coverage(self):
        clauses = self.rules()
        self.record['policy_rules'][0]['evidence'][-1]['quote'] = 'The above exclusions do not apply'
        with self.assertRaisesRegex(ValueError, 'complete source block'):
            require_coverage(self.data, clauses, OWNERS)

    def test_stale_or_invented_source_ids_rejected(self):
        clauses = self.rules()
        self.record['policy_rules'][0]['source_clause_ids'] = ['invented']
        with self.assertRaisesRegex(ValueError, 'Unknown/stale'):
            require_coverage(self.data, clauses, OWNERS)

    def test_exception_cannot_be_detached_from_parent_rule(self):
        clauses = self.rules()
        parent = self.record['policy_rules'][0]
        orphan = deepcopy(parent)
        orphan['rule_id'] = 'orphan'
        orphan['source_clause_ids'] = [clauses[1].source_id]
        parent['source_clause_ids'] = [clauses[0].source_id]
        self.record['policy_rules'].append(orphan)
        with self.assertRaisesRegex(ValueError, 'parent rule'):
            require_coverage(self.data, clauses, OWNERS)

    def test_source_ids_bind_content_not_just_page(self):
        first = inventory(RULE_SOURCE)
        second = inventory(RULE_SOURCE.replace('nuclear material', 'terrorism'))
        self.assertNotEqual(first[-1].source_id, second[-1].source_id)

    def test_wrong_evidence_page_rejected(self):
        clauses = self.rules()
        self.record['policy_rules'][0]['evidence'][0]['pdf_page'] += 1
        with self.assertRaisesRegex(ValueError, 'complete source block'):
            require_coverage(self.data, clauses, OWNERS)

    def test_ids_cannot_be_attached_to_wrong_field_owner(self):
        clauses = self.rules()
        b = self.add()
        b['source_clause_ids'] = [clauses[0].source_id]
        b['evidence'] = [evidence(clauses[0])]
        self.record['policy_rules'].pop(0)
        with self.assertRaisesRegex(ValueError, 'must map to policy_rules'):
            require_coverage(self.data, clauses, OWNERS)

    def test_general_exclusions_continue_across_pages(self):
        text = source('General Exclusions', 'There is no cover for fraud.') + source('War', 'There is no cover for armed conflict.', page=8)
        self.assertEqual([c.pdf_page for c in inventory(text)], [7, 8])

    def test_toc_and_numeric_footers_not_claimed_as_rules(self):
        self.assertEqual(inventory(source('General Exclusions', '18', 'Claims', '20')), [])
        self.assertEqual(len(inventory(source('General Exclusions', 'There is no cover for fraud.', '7'))), 1)

    def test_claiming_stops_exclusion_inventory(self):
        text = source('General Exclusions', 'There is no cover for fraud.', 'Claiming', 'Call our claims department following an incident.')
        self.assertEqual(len(inventory(text)), 1)

    def test_running_navigation_not_classified_as_exclusion(self):
        text = source('General Exclusions', 'There is no cover for fraud.', 'Car Insurance • About your cover', 'Table of contents pg. 3 ↗')
        self.assertEqual(len(inventory(text)), 1)

    def test_daily_cost_requires_structured_limit(self):
        text = source('We pay reasonable daily cost of a hire car after theft.')
        c, = inventory(text)
        b = self.add('hire_car_benefits', 'hire_car_after_theft')
        b['details']['trigger'] = 'after_theft'
        b.update(evidence=[evidence(c)], source_clause_ids=[c.source_id])
        with self.assertRaisesRegex(ValueError, 'structured_costs'): self.check()
        cap = limit()
        cap['period'] = 'per_day'
        cap['terms'][0].update(amount_kind='reasonable_costs', amount_aud=None)
        b['limits'] = [cap]
        self.check()
        require_coverage(self.data, [c], OWNERS)

    def test_reasonable_towing_cost_not_forced_into_daily_hire_limit(self):
        self.assertEqual(inventory(source('We pay reasonable costs of towing to a repairer.')), [])

    def test_table_only_relevant_row_becomes_checklist_item(self):
        text = '<!-- page 3 -->\n<!-- table table_id=p3_t0 source=test -->\n| Service | Cover |\n| --- | --- |\n| Towing | Reasonable costs |\n| Hire car | Reasonable daily cost of hire car |'
        cs = inventory(text)
        self.assertEqual(len(cs), 1)
        self.assertNotIn('Towing', cs[0].text)

    def test_excess_stacking_not_only_conditions(self):
        c, = inventory(source('Age excess applies in addition to the basic excess for a claim.'))
        spec = next(f['item_schema'] for f in self.schema['fields'] if f['name'] == 'excesses')
        e = empty(spec, self.schema['$defs'])
        e.update(excess_id='age', amount_kind='schedule_specific', amount_aud=None, applicability='all_coverages',
                 evidence=[evidence(c)], source_clause_ids=[c.source_id])
        self.record['excesses'] = [e]
        with self.assertRaisesRegex(ValueError, 'excess_combination'): self.check()
        e['combination_rule'] = 'In addition to basic and any other applicable excess'
        self.check()
        require_coverage(self.data, [c], OWNERS)

    def test_no_stacking_cue_does_not_require_rule(self):
        self.assertEqual(inventory(source('Only the basic excess applies to this claim.')), [])

    def test_change_of_vehicle_has_distinct_owner_and_baseline(self):
        c, = inventory(source('We will automatically transfer cover to a replacement car for 10 days after you sell the old vehicle.'))
        b = self.add('change_of_vehicle_cover', 'change_of_vehicle_cover')
        b.update(evidence=[evidence(c)], source_clause_ids=[c.source_id])
        with self.assertRaisesRegex(ValueError, 'time_since_vehicle_change'):
            require_coverage(self.data, [c], OWNERS)
        b['quantities'] = [quantity('days', 10, 'lte', 'time_since_vehicle_change')]
        self.check()
        require_coverage(self.data, [c], OWNERS)

    def test_temporary_replacement_not_confused_with_transfer(self):
        self.assertEqual(inventory(source('Liability for a substitute car while your car is being repaired.')), [])

    def test_explicit_transfer_deadline_cannot_be_unknown_or_rental(self):
        c, = inventory(source('We automatically transfer cover to your replacement car for up to 9 days after sale.'))
        b = self.add('change_of_vehicle_cover', 'change_of_vehicle_cover')
        b.update(evidence=[evidence(c)], source_clause_ids=[c.source_id])
        b['quantities'] = [quantity('days', None, 'unknown', 'time_since_vehicle_change')]
        with self.assertRaisesRegex(ValueError, 'explicit transfer maximum'):
            require_coverage(self.data, [c], OWNERS)
        b['quantities'] = [quantity('days', 9, 'lte', 'time_since_vehicle_change')]
        self.check()
        require_coverage(self.data, [c], OWNERS)

    def test_every_product_keeps_its_own_source_references(self):
        cs = self.rules()
        second = deepcopy(self.record)
        second['product_name'] = 'Other tier'
        self.data['products'].append(second)
        self.check()
        require_coverage(self.data, cs, OWNERS)

    def test_runtime_blocks_omission_and_allows_complete_fixture(self):
        cs = self.rules()
        self.fill_missing()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'fixture.pdf'
            path.touch()
            with patch('src.schema_application.extractor.render_pdf_paths_for_prompt', return_value=RULE_SOURCE):
                provider = RecordingProvider(self.data)
                engine = SchemaExtractor(self.schema, provider=provider, manifest=self.manifest, log=None)
                self.assertEqual(engine.extract_one(path), self.data)
                self.record['policy_rules'].pop()
                engine = SchemaExtractor(self.schema, provider=RecordingProvider(self.data), manifest=self.manifest, log=None)
                with self.assertRaises(StructuredOutputFailure): engine.extract_one(path)

    def test_numbered_curly_apostrophe_section_detected_and_bounded(self):
        lead = 'You are not covered under any section of this policy for loss caused by or involving:'
        text = (source('Contents', '3. Things we don’t cover', '4. What we cover – the details', page=2)
                + source('3. Things we don’t cover', lead,
                         'Asbestos asbestos, asbestos fibres or derivatives of asbestos of any kind.', page=17)
                + source(lead, 'Test drives loss while demonstrated for sale but we will pay a claim if you are a passenger.',
                         '4. What we cover – the details', 'Fire damage to your car caused by fire or explosion.', page=18))
        clauses = inventory(text)
        self.assertEqual([c.pdf_page for c in clauses], [17, 17, 18])  # TOC skipped; repeated lead-in once
        self.assertEqual(sum(c.text == lead for c in clauses), 1)
        self.assertEqual([c.has_exception for c in clauses], [False, False, True])
        self.assertFalse(any('Fire damage' in c.text for c in clauses))  # ended at higher-numbered heading

    def test_lower_numbered_items_do_not_end_numbered_section(self):
        text = source('5. Things we do not cover', 'There is no cover for the items below.',
                      '1. Wear and tear', 'Gradual deterioration, rust or corrosion of any part of your car.', page=30)
        self.assertEqual([c.text for c in inventory(text)],
                         ['There is no cover for the items below.', 'Gradual deterioration, rust or corrosion of any part of your car.'])

    def test_legacy_profile_cannot_be_silently_relabelled(self):
        from src.car_insurance.schema_revision_v3 import build_revision as old_build
        old = old_build(synthetic_schema())
        old['validation_profile'] = 'car_insurance.review_v4'
        with self.assertRaises(ValueError): compile_extraction_contract(old)


if __name__ == '__main__':
    unittest.main()
