from copy import deepcopy
import unittest

from src.car_insurance.extraction_diagnostics import (local_issues, policy_wide_rule_issues, source_issues,
                                                     validate_runtime)
from src.car_insurance.schema_revision_v5 import build_revision
from src.common.model_provider import _project_openai_schema
from src.common.structured_output import BusinessDiagnostics
from src.schema.contract import compile_extraction_contract
from src.schema_application.extractor import SchemaExtractor
from tests.test_car_insurance import synthetic_schema, RecordingProvider
from tests import test_car_schema_revision_v3 as v3tests
from tests import test_car_schema_revision_v4 as v4tests
from tests.test_car_schema_revision_v4 import RULE_SOURCE

POLICY_WIDE = RULE_SOURCE.replace('General Exclusions\n',
    'General Exclusions\n<!-- text block_id=p7_text_cue -->\nThese general exclusions apply to all sections of your policy.\n', 1)


class CarSchemaV5Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = build_revision(synthetic_schema())
        from src.verticals.manifest import resolve_manifest
        cls.manifest = resolve_manifest(vertical='car_insurance')
        cls.contract = compile_extraction_contract(cls.schema)

    def setUp(self):
        v3tests.CarSchemaV3Test.setUp(self)
        self.data['shared_policy_rules'] = []

    add = v3tests.CarSchemaV3Test.add
    fill_missing = v3tests.CarSchemaV3Test.fill_missing
    check = v3tests.CarSchemaV3Test.check

    def shared(self, text=RULE_SOURCE):
        clauses = v4tests.CarSchemaV4Test.rules(self, text)
        self.data['shared_policy_rules'] = self.record['policy_rules']
        self.record['policy_rules'] = []
        self.record['shared_policy_rules_applicability'] = 'applies'
        return clauses

    def test_v5_contract_requires_shared_rules_and_projects_strictly(self):
        self.check()
        self.assertIn('shared_policy_rules', self.contract['required'])
        self.assertIn('shared_policy_rules_applicability', {f['name'] for f in self.schema['fields']})
        _project_openai_schema(self.contract, strict=True)

    def test_shared_rules_satisfy_source_coverage_once(self):
        clauses = self.shared()
        self.check()
        self.assertEqual(source_issues(self.data, clauses, RULE_SOURCE), [])
        validate_runtime(self.data, lambda _: self.check(), clauses, RULE_SOURCE)

    def test_shared_rule_problems_reported_at_shared_paths(self):
        clauses = self.shared()
        self.data['shared_policy_rules'][0]['evidence'][0]['quote'] = 'There is no cover.'
        paths = [e['path'] for e in source_issues(self.data, clauses, RULE_SOURCE)]
        self.assertIn('$.shared_policy_rules[0].source_clause_ids', paths)
        self.data['shared_policy_rules'][0]['exceptions'][0]['preserved_cover'] = 'unknown'
        self.assertIn('$.shared_policy_rules[0].exceptions[0].preserved_cover',
                      [e['path'] for e in local_issues(self.data)])

    def test_repeating_shared_rule_in_product_rejected(self):
        self.shared()
        self.record['policy_rules'] = [deepcopy(self.data['shared_policy_rules'][0])]
        with self.assertRaisesRegex(ValueError, 'repeats shared rules'):
            self.check()

    def test_applies_needs_shared_rules_and_shared_rules_reference_no_benefits(self):
        self.record['shared_policy_rules_applicability'] = 'applies'
        with self.assertRaisesRegex(ValueError, 'none are given'):
            self.check()
        self.shared()
        self.data['shared_policy_rules'][0]['applies_to_benefit_ids'] = ['benefit_1']
        with self.assertRaisesRegex(ValueError, 'cannot reference product benefit IDs'):
            self.check()

    def test_policy_wide_cue_requires_every_product_to_apply_shared_rules(self):
        clauses = self.shared(POLICY_WIDE)
        self.data['products'].append(deepcopy(self.record))
        self.assertEqual(policy_wide_rule_issues(self.data, clauses, POLICY_WIDE), [])
        self.data['products'][1]['shared_policy_rules_applicability'] = None
        errors = policy_wide_rule_issues(self.data, clauses, POLICY_WIDE)
        self.assertEqual([e['path'] for e in errors], ['$.products[1].shared_policy_rules_applicability'])
        self.data['products'].pop()
        self.record['shared_policy_rules_applicability'] = 'does_not_apply'  # v5 checks a single product too
        self.assertTrue(policy_wide_rule_issues(self.data, clauses, POLICY_WIDE))

    def test_extractor_prompt_uses_v5_placement_guidance(self):
        engine = SchemaExtractor(self.schema, provider=RecordingProvider(self.data), manifest=self.manifest, log=None)
        self.assertIn('"shared_policy_rules" at top level', engine.extraction_prompt)
        self.assertIn('ONCE in shared_policy_rules', engine.extraction_prompt)
        self.assertNotIn("repeat each one, with its evidence", engine.extraction_prompt)
        self.assertNotIn('apply every global rule to each affected tier', engine.extraction_prompt)


if __name__ == '__main__':
    unittest.main()
