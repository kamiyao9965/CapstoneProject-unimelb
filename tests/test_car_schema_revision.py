import copy
import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile

from src.car_insurance.schema_revision import build_revision, PROFILE
from src.schema.contract import compile_extraction_contract
from src.schema.validation import validate_extraction_record, validate_schema_mapping
from src.schema_application.extractor import SchemaExtractor
from src.common.json_contracts import validate_inline_contract
from src.common.structured_output import StructuredOutputFailure
from src.verticals.manifest import resolve_manifest
from tests.test_car_insurance import synthetic_schema, RecordingProvider

EVIDENCE=[dict(pdf_page=1,printed_page='1',section='Synthetic test',quote='Synthetic evidence, not a real policy')]


def valid_record(schema):
    result={f['name']:None for f in schema['fields']}
    result.update(product_name='Example TPPD',product_type='third_party_property_damage',
                  product_basis='named_product',product_identity_evidence=copy.deepcopy(EVIDENCE),_notes=None)
    return result


def payload(schema):
    record=valid_record(schema)
    record['_unfilled']=[f['name'] for f in schema['fields'] if record[f['name']] is None]
    return dict(products=[record],_document_notes=None,document_evidence=dict(metadata=[],coverage_summary_tables=[]))


def benefit(schema,owner,category,identity='benefit_1'):
    spec=next(f['item_schema'] for f in schema['fields'] if f['name']==owner)
    def empty(spec):
        types=spec['type']
        if isinstance(types,list) and 'null' in types: return None
        if types=='object': return {k:empty(v) for k,v in spec['properties'].items()}
        if types=='array': return []
        if 'enum' in spec: return spec['enum'][0]
        if types in ('number','integer'): return 1
        if types=='string': return 'synthetic'
        return False
    b=empty(spec)
    b.update(benefit_id=identity,category=category,variant='base',source_label='Synthetic benefit',status='included',option_id=None,evidence=copy.deepcopy(EVIDENCE))
    return b


def cap(amount=1500,period='per_claim',basis='per_person'):
    return dict(amount_aud=amount,amount_kind='fixed',period=period,basis=basis,shared_with=[],conditions=None,
                source_text='Synthetic cap',evidence=copy.deepcopy(EVIDENCE))


class CarSchemaRevisionTest(unittest.TestCase):
    def setUp(self):
        self.schema=build_revision(synthetic_schema())
        self.manifest=resolve_manifest(vertical='car_insurance')
        self.contract=compile_extraction_contract(self.schema,manifest=self.manifest)
        self.data=payload(self.schema)
        self.record=self.data['products'][0]

    def add(self,owner,category,identity='benefit_1'):
        b=benefit(self.schema,owner,category,identity)
        if self.record[owner] is None:
            self.record[owner]=[]
            self.record['_unfilled'].remove(owner)
        self.record[owner].append(b)
        return b

    def check(self):
        validate_inline_contract(self.data,self.contract)
        validate_extraction_record(self.schema,self.data,manifest=self.manifest)

    def test_valid_candidate_and_document_evidence(self):
        validate_schema_mapping(self.schema,manifest=self.manifest)
        self.check()
        self.assertNotIn('coverage_summary_tables',self.record)
        self.assertNotIn('document_metadata',self.record)

    def test_distinct_counselling_and_funeral_scopes(self):
        a=self.add('counselling_and_funeral_benefits','counselling_services','counselling')
        a['limits']=[cap()]
        b=self.add('counselling_and_funeral_benefits','funeral_expenses','funeral')
        b['limits']=[cap(5000,'per_policy_period','aggregate')]
        self.check()

    def test_combined_emergency_limit_is_one_aggregate_cap(self):
        b=self.add('transport_and_emergency_expenses_benefit','transport_and_emergency_expenses')
        b['details']['covered_components']=['emergency_transport','emergency_accommodation','emergency_repairs']
        b['limits']=[cap(1000,'per_claim','aggregate')]
        self.check()
        b['limits'][0]['amount_aud']='1000 AUD'
        with self.assertRaises(ValueError): self.check()

    def test_daily_rate_and_unlimited_days(self):
        b=self.add('hire_car_benefits','hire_car_after_theft')
        b['limits']=[cap(90,'per_day','aggregate')]
        b['quantities']=[dict(metric='days',value=None,comparison='unlimited',reference='rental duration',conditions=None,evidence=copy.deepcopy(EVIDENCE))]
        self.check()
        b['quantities'][0]['value']=0
        with self.assertRaises(ValueError): self.check()

    def test_no_extra_nested_keys(self):
        b=self.add('baby_seats_benefit','baby_seats')
        b['unexpected']=True
        with self.assertRaises(ValueError): self.check()

    def test_missing_nested_key(self):
        b=self.add('baby_seats_benefit','baby_seats')
        del b['status']
        with self.assertRaises(ValueError): self.check()

    def test_wrong_scope_and_negative_amount(self):
        b=self.add('baby_seats_benefit','baby_seats')
        b['limits']=[cap()]
        for amount,period in [(-1,'per_claim'),(1,'per_year')]:
            b['limits'][0].update(amount_aud=amount,period=period)
            with self.subTest(amount=amount),self.assertRaises(ValueError): self.check()

    def test_unlimited_is_not_zero(self):
        b=self.add('baby_seats_benefit','baby_seats')
        b['limits']=[cap()]
        b['limits'][0].update(amount_kind='unlimited',amount_aud=None)
        self.check()
        b['limits'][0]['amount_aud']=0
        with self.assertRaises(ValueError): self.check()

    def test_shared_cap_requires_existing_target(self):
        b=self.add('baby_seats_benefit','baby_seats')
        b['limits']=[cap()]
        b['limits'][0]['shared_with']=['missing']
        with self.assertRaises(ValueError): self.check()

    def test_duplicate_benefit_cannot_hide_in_generic_owner(self):
        b=self.add('other_coverages','hire_car_after_theft')
        with self.assertRaises(ValueError): self.check()

    def test_duplicate_variant_rejected(self):
        self.add('baby_seats_benefit','baby_seats','first')
        self.add('baby_seats_benefit','baby_seats','second')
        with self.assertRaises(ValueError): self.check()

    def test_global_rules_not_duplicated(self):
        rule=dict(rule_id='a',kind='use_restriction',topic='keys',status='excluded',text='Leaving keys unattended',conditions=[],evidence=copy.deepcopy(EVIDENCE))
        self.record['policy_rules']=[rule,{**rule,'rule_id':'b','kind':'general_exclusion'}]
        self.record['_unfilled'].remove('policy_rules')
        with self.assertRaises(ValueError): self.check()

    def option(self):
        b=self.add('covered_events','fire_and_theft')
        b.update(status='optional',option_id='fire_theft')
        self.record['available_addons']=[dict(option_id='fire_theft',option_name='Fire and theft cover option',availability='conditional',additional_premium_required=True,conditions=['Insurer acceptance'],benefit_ids=[b['benefit_id']],evidence=copy.deepcopy(EVIDENCE))]
        self.record['_unfilled'].remove('available_addons')
        return b

    def test_option_does_not_change_base_type(self):
        self.option()
        self.check()
        self.assertEqual(self.record['product_type'],'third_party_property_damage')

    def test_option_not_included_and_not_standalone_configuration(self):
        b=self.option()
        b['status']='included'
        with self.assertRaises(ValueError): self.check()
        b['status']='optional'
        self.record['product_basis']='option_configuration'
        with self.assertRaises(ValueError): self.check()

    def test_customer_selection_not_invented(self):
        self.option()
        self.record['available_addons'][0]['selected']=True
        with self.assertRaises(ValueError): self.check()

    def test_unknown_option_reference(self):
        b=self.option()
        b['option_id']='missing'
        with self.assertRaises(ValueError): self.check()

    def test_profile_cannot_silently_drop_nested_contract(self):
        next(f for f in self.schema['fields'] if f['name']=='hire_car_benefits').pop('item_schema')
        with self.assertRaises(ValueError): compile_extraction_contract(self.schema)

    def test_remote_schema_references_rejected(self):
        self.schema['fields'][3]['item_schema']={'$ref':'https://example.invalid/schema'}
        with self.assertRaises(ValueError): validate_schema_mapping(self.schema,manifest=self.manifest)

    def test_legacy_schema_still_compiles(self):
        contract=compile_extraction_contract(synthetic_schema(),manifest=self.manifest)
        self.assertNotIn('document_evidence',contract['properties'])

    def test_profile_taxonomy_cannot_drift_from_field_owners(self):
        self.schema['taxonomies']['coverage_categories'].pop()
        with self.assertRaises(ValueError): validate_schema_mapping(self.schema,manifest=self.manifest)

    def test_provider_projection_preserves_closed_nested_objects(self):
        from src.common.model_provider import _project_openai_schema
        projected=_project_openai_schema(self.contract,strict=True)
        item=projected['properties']['products']['items']['properties']['hire_car_benefits']['items']
        self.assertFalse(item['additionalProperties'])
        self.assertEqual(set(item['required']),set(item['properties']))
        self.assertEqual(item['properties']['limits']['items']['properties']['amount_aud']['type'],['number','null'])

    def test_runtime_accepts_valid_nested_model_output(self):
        self.option()
        provider=RecordingProvider(self.data)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'fixture.pdf'
            path.touch()
            engine=SchemaExtractor(self.schema,manifest=self.manifest,provider=provider,log=None,usage_log_path=None)
            with patch('src.schema_application.extractor.render_pdf_paths_for_prompt',return_value='Synthetic text'):
                self.assertEqual(engine.extract_one(path),self.data)

    def test_runtime_rejects_invalid_nested_model_output(self):
        b=self.add('baby_seats_benefit','baby_seats')
        b['status']='maybe'
        provider=RecordingProvider(self.data)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'fixture.pdf'
            path.touch()
            engine=SchemaExtractor(self.schema,manifest=self.manifest,provider=provider,log=None,usage_log_path=None)
            self.assertTrue(engine.structured_output_strict)
            self.assertIn('"document_evidence" at top level',engine.extraction_prompt)
            with patch('src.schema_application.extractor.render_pdf_paths_for_prompt',return_value='Synthetic text'),self.assertRaises(StructuredOutputFailure):
                engine.extract_one(path)


if __name__=='__main__': unittest.main()
