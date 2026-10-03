"""Car schema v2: unique field ownership, typed limits, explicit add-ons.

Builds a review candidate, never an approved canonical schema or gold labels.
"""
from copy import deepcopy

PROFILE='car_insurance.review_v2'
TYPES=['comprehensive','third_party_property_damage','third_party_fire_and_theft']
STATUSES=['included','optional','excluded','limited','conditional','unknown','not_required']


def string(nullable=False): return {'type':['string','null'] if nullable else 'string'}
def number(nullable=True): return {'type':['number','null'] if nullable else 'number','minimum':0}
def enum(values,nullable=False): return {'type':['string','null'] if nullable else 'string','enum':[*values,*([None] if nullable else [])]}
def array(items): return {'type':'array','items':items}
def obj(**props): return {'type':'object','additionalProperties':False,'required':list(props),'properties':props}
BOOL={'type':['boolean','null']}
EVIDENCE=obj(pdf_page={'type':'integer','minimum':1},printed_page=string(True),section=string(True),quote=string())
LIMIT=obj(amount_aud=number(),amount_kind=enum(['fixed','unknown','unlimited','reasonable_costs','schedule_specific']),
          period=enum(['per_claim','per_incident','per_policy_period','per_day','unknown']),
          basis=enum(['aggregate','per_person','per_item','per_driver','per_vehicle','unknown']),
          shared_with=array(string()),conditions=string(True),source_text=string(),evidence=array(EVIDENCE))
QUANTITY=obj(metric=enum(['days','years','kilometres','claims','items','callouts']),value=number(),
             comparison=enum(['eq','lt','lte','gt','gte','unlimited','unknown']),
             reference=string(),conditions=string(True),evidence=array(EVIDENCE))

# Each category has exactly one authoritative product-level owner.
OWNERS={
 'covered_events':['accidental_loss_or_damage','fire_and_theft'],
 'third_party_property_liability':['third_party_property_damage','legal_liability_features'],
 'hire_car_benefits':['hire_car_after_theft','hire_car_not_at_fault','hire_car_other_events'],
 'windscreen_and_window_glass_cover':['windscreen_and_window_glass'],
 'towing_and_storage':['towing_and_storage'],
 'transport_and_emergency_expenses_benefit':['transport_and_emergency_expenses'],
 'new_car_replacement':['new_car_replacement'],
 'repairs_and_choice_of_repairer':['repairs_and_parts_policy','choice_of_repairer'],
 'personal_belongings_benefit':['personal_property'],
 'baby_seats_benefit':['baby_seats'],
 'trailer_cover_benefit':['trailer_cover'],
 'caravans_and_trailers_tppd_extension':['caravans_and_trailers_tppd_extension'],
 'uninsured_driver_damage_benefit':['uninsured_driver_damage_benefit'],
 'substitute_car_liability_feature':['substitute_car_liability'],
 'temporary_replacement_vehicle_cover':['temporary_replacement_vehicle'],
 'keys_and_locks_benefit':['keys_and_locks'],
 'roadside_assist_feature':['roadside_assist'],
 'counselling_and_funeral_benefits':['counselling_services','funeral_expenses'],
 'additional_item_benefits':['business_items','campervan_motorhome_contents'],
 'other_coverages':['other_documented_benefit'],
}
DETAILS={
 'covered_events':obj(event=enum(['collision','impact','malicious_damage','hail','storm','cyclone','flood','fire','theft','attempted_theft','earthquake','other']),event_text=string(True)),
 'hire_car_benefits':obj(trigger=enum(['after_theft','not_at_fault','other_insured_event']),provider_required=BOOL,own_arrangement_allowed=BOOL,vehicle_class=string(True),stop_conditions=array(string())),
 'transport_and_emergency_expenses_benefit':obj(covered_components=array(enum(['personal_transport','emergency_transport','emergency_accommodation','emergency_repairs','other']))),
 'new_car_replacement':obj(first_owner_required=BOOL,demonstrator_included=BOOL,financier_consent_required=BOOL,on_road_costs=array(string())),
 'windscreen_and_window_glass_cover':obj(repair_excess_free=BOOL,replacement_excess_free=BOOL,glass_scope=string(True)),
 'repairs_and_choice_of_repairer':obj(repairer_selection=enum(['insurer_selects','choice_available','insured_selects','unknown']),guarantee=string(True),parts_policy=string(True)),
 'towing_and_storage':obj(destinations=array(string()),storage_covered=BOOL),
 'roadside_assist_feature':obj(provider_name=string(True)),
 'third_party_property_liability':obj(legal_costs_included=BOOL),
}


def benefit_item(categories,details):
    return obj(benefit_id=string(),category=enum(categories),variant=string(),source_label=string(),
               status=enum(STATUSES),option_id=string(True),limits=array(LIMIT),
               quantities=array(QUANTITY),conditions=array(string()),exclusions=array(string()),
               details=details,evidence=array(EVIDENCE))


def document_evidence_contract():
    return obj(metadata=array(obj(document_title=string(),preparation_date=string(True),effective_date=string(True),version_code=string(True))),
               coverage_summary_tables=array(obj(title=string(),pdf_page_start={'type':'integer','minimum':1},
                    pdf_page_end={'type':'integer','minimum':1},columns=array(string()),
                    rows=array(obj(label=string(),cells=array(string()))),footnotes=array(string()),notes=string(True))))


def build_revision(original):
    source=deepcopy(original)
    result={k:source[k] for k in ['vertical','product_types']}
    result.update(version='2026-10-03.review.v2',validation_profile=PROFILE,
                  description='Review candidate: one owner per benefit, AUD limits with period and basis, source-defined products separate from available add-ons. Document evidence is extracted once at document level. Not human-approved.')
    fields=[]
    def field(name,kind,description,values=None,item=None,required=False):
        entry=dict(name=name,type=kind,description=description,applies_to=TYPES,required=required,values=values or [])
        if item is not None: entry['item_schema']=item
        fields.append(entry)
    field('product_name','string','Name of the base product or named tier explicitly in the PDS. Never synthesize a product name by enabling an option.',required=True)
    field('product_type','enum','Classification of the source-defined base product/tier. Availability of a Fire & Theft option does not change a TPPD base product.',TYPES,required=True)
    field('product_basis','enum','Only source-defined products or named cover levels are separate records, not hypothetical option configurations.',['named_product','named_tier'],required=True)
    field('product_identity_evidence','list[object]','Evidence for the printed product name and separate product/tier status.',item=EVIDENCE,required=True)
    field('brand_name','string','Consumer-facing trading brand.')
    field('issuer_legal_name','string','Entity explicitly issuing the PDS. Do not infer from brand alone.')
    field('risk_underwriter_legal_name','string','Entity explicitly stated to underwrite the risk. May equal issuer when the same entity performs both roles; otherwise unknown remains null.')
    field('available_addons','list[object]','Available add-ons only, not purchased selections. No limits here; referenced benefits own limits. A brochure/PDS cannot establish that a customer selected an option.',item=obj(
        option_id=string(),option_name=string(),availability=enum(['available','conditional','unknown']),
        additional_premium_required=BOOL,conditions=array(string()),benefit_ids=array(string()),evidence=array(EVIDENCE)))
    field('valuation_basis','list[object]','Alternative settlement bases and their conditions; not standalone product variants.',item=obj(basis=enum(['agreed_value','market_value','schedule_amount','unknown']),availability=enum(['available','conditional','unknown']),conditions=array(string()),evidence=array(EVIDENCE)))
    field('excesses','list[object]','Deductibles, not indemnity limits. Preserve schedule-specific amounts as null; never sum unless a stated combination rule applies.',item=obj(
        excess_id=string(),excess_type=enum(['basic','voluntary','age','inexperienced','unlisted_driver','driver_history','additional_policy','additional_driver','windscreen_reduced','other']),
        amount_aud=number(),amount_kind=enum(['fixed','unknown','schedule_specific']),
        period=enum(['per_claim','per_incident','unknown']),conditions=array(string()),waiver_conditions=array(string()),combination_rule=string(True),evidence=array(EVIDENCE)))
    for owner,categories in OWNERS.items():
        details=DETAILS.get(owner,obj(notes=string(True)))
        field(owner,'list[object]',
              'Authoritative owner for '+', '.join(categories)+'. limits store money; quantities store durations/counts/age/distance with comparison and reference. Null amounts do not mean zero. variant identifies the distinct source benefit/trigger. Optional status requires option_id pointing to available_addons. Shared limits are stored once and reference existing benefit_ids. Preserve source evidence.',
              item=benefit_item(categories,details))
    field('policy_rules','list[object]','Single owner for use restrictions and general exclusions. Each source rule is stored once, classified as use_restriction or general_exclusion; do not repeat in another global list.',item=obj(
        rule_id=string(),kind=enum(['use_restriction','general_exclusion']),topic=string(),
        status=enum(['permitted','restricted','excluded','allowed_with_disclosure','conditional','unknown']),
        text=string(),conditions=array(string()),evidence=array(EVIDENCE)))
    result['fields']=fields
    categories=[c for group in OWNERS.values() for c in group]
    result['taxonomies']={'coverage_categories':[dict(canonical_name=c,description='Owned by '+next(k for k,v in OWNERS.items() if c in v)) for c in categories]}
    result['notes']=[
        'Human-review candidate, not canonical approval. Original model draft is retained unchanged.',
        'Document metadata and coverage_summary_tables are in top-level document_evidence, not repeated per product; operational path/hash/model metadata remains application provenance.',
        'AAMI explicitly named cover levels and Youi named cover types can be separate records. QBE TPPD Fire and theft cover option remains an available_addon of the TPPD base product.',
        'All money is AUD. amount_kind=fixed requires non-negative amount_aud; other kinds require null. Period (per_claim/per_incident/per_policy_period/per_day) and basis (per_person/per_item/etc.) are independent.',
        'Counselling can have period=per_claim and basis=per_person simultaneously. Funeral benefit may be per_policy_period. Emergency accommodation/transport/repairs may share ONE aggregate limit, not separate copies.',
        'Use one benefit with multiple covered_components for a combined limit where possible; shared_with references other benefit IDs only when necessary.',
        'other_coverages cannot repeat a reserved category. Additional unknown benefits require source_label and evidence; semantic novelty still requires human review.',
        'Status values are common across all benefit fields. limited/conditional/not_required are not aliases for included/excluded. No customer-specific option selection is inferred from a PDS.',
    ]
    return result


def validate_records(schema,payload):
    """Semantic checks after strict JSON Schema validation; no external lookups."""
    from src.common.json_contracts import validate_inline_contract
    from src.schema.contract import compile_extraction_contract
    validate_inline_contract(payload,compile_extraction_contract(schema))
    for record in payload['products']:
        for name in ['product_name','product_type','product_basis','product_identity_evidence']:
            if not record.get(name): raise ValueError(f'{name} cannot be null/empty')
        addons=record.get('available_addons') or []
        option_ids=[a['option_id'] for a in addons]
        if any(not x.strip() for x in option_ids) or len(set(option_ids))!=len(option_ids):
            raise ValueError('Duplicate/empty option_id')
        benefits=[(owner,b) for owner in OWNERS for b in record.get(owner) or []]
        ids=[b['benefit_id'] for _,b in benefits]
        if any(not x.strip() for x in ids) or len(set(ids))!=len(ids): raise ValueError('Duplicate/empty benefit_id')
        seen=set()
        for owner,b in benefits:
            key=(b['category'],b['variant'].strip().casefold(),b['option_id'])
            if key in seen: raise ValueError('Duplicate benefit category/variant/option')
            seen.add(key)
            if not b['variant'].strip() or not b['source_label'].strip(): raise ValueError('Benefit needs source identity')
            if b['status']=='optional' and b['option_id'] is None: raise ValueError('Optional benefit requires option_id')
            if b['option_id'] is not None:
                if b['option_id'] not in option_ids: raise ValueError('Unknown option_id')
                if b['status']=='included': raise ValueError('Add-on availability cannot become included base cover')
                addon=next(a for a in addons if a['option_id']==b['option_id'])
                if b['benefit_id'] not in addon['benefit_ids']: raise ValueError('Add-on reference must be reciprocal')
            for limit in b['limits']:
                _amount(limit)
                if len(set(limit['shared_with']))!=len(limit['shared_with']): raise ValueError('Duplicate shared-limit reference')
                if any(x not in ids or x==b['benefit_id'] for x in limit['shared_with']): raise ValueError('Invalid shared-limit reference')
                for target in limit['shared_with']:
                    if b['benefit_id'] in [x for _,other in benefits if other['benefit_id']==target for cap in other['limits'] for x in cap['shared_with']]:
                        raise ValueError('Shared limit must have one owner, not reciprocal copies')
            for quantity in b['quantities']:
                if (quantity['comparison'] in {'unknown','unlimited'}) != (quantity['value'] is None): raise ValueError('Quantity comparison/value mismatch')
                if not quantity['reference'].strip(): raise ValueError('Quantity needs a reference/baseline')
        for addon in addons:
            if len(set(addon['benefit_ids']))!=len(addon['benefit_ids']): raise ValueError('Duplicate addon benefit reference')
            for bid in addon['benefit_ids']:
                if bid not in ids or next(b for _,b in benefits if b['benefit_id']==bid)['option_id']!=addon['option_id']:
                    raise ValueError('Invalid addon benefit reference')
        for excess in record.get('excesses') or []: _amount(excess)
        _unique(record.get('excesses') or [],'excess_id')
        rules=record.get('policy_rules') or []
        _unique(rules,'rule_id')
        texts=[' '.join(r['text'].casefold().split()) for r in rules]
        if len(set(texts))!=len(texts): raise ValueError('Duplicate global policy rule')
        for item in _objects(record):
            if 'evidence' in item and not item['evidence']: raise ValueError('Nested record requires source evidence')
        for name in record['_unfilled']:
            if record.get(name) is not None: raise ValueError('_unfilled must not name populated fields')
        if {f['name'] for f in schema['fields'] if record[f['name']] is None} != set(record['_unfilled']):
            raise ValueError('_unfilled must enumerate null fields exactly')
    for table in payload['document_evidence']['coverage_summary_tables']:
        if table['pdf_page_end']<table['pdf_page_start']: raise ValueError('Invalid evidence page range')
        if any(len(r['cells'])!=len(table['columns']) for r in table['rows']): raise ValueError('Evidence table column mismatch')


def _amount(value):
    if (value['amount_kind']=='fixed') != (value['amount_aud'] is not None):
        raise ValueError('Only fixed amounts have numeric amount_aud; unknown/unlimited/schedule-specific are null')


def _unique(items,key):
    values=[x[key] for x in items]
    if any(not x.strip() for x in values) or len(set(values))!=len(values): raise ValueError(f'Duplicate/empty {key}')


def _objects(value):
    if isinstance(value,dict):
        yield value
        for child in value.values(): yield from _objects(child)
    elif isinstance(value,list):
        for child in value: yield from _objects(child)
