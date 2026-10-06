"""v4 runtime diagnostics revision 2. No mutation or automatic content repair.

Contract validation must precede these shape-dependent checks. Source relations
are conservative single-product cues, not a comprehensive semantic evaluator.
"""
from copy import deepcopy
import re

from src.common.structured_output import BusinessDiagnostics
from .schema_revision_v4 import OWNERS
from .source_coverage import (EXCEPTION, DAILY, STACKING, coverage_issues, exception_cues, following_blocks,
                              has_daily_cost, normalized, source_blocks)

GUIDANCE = """
Runtime diagnostics revision 2:
For coverage_summary_tables, columns describes ALL cells including a label column
if present. rows[].label is an additional row identifier, not a substitute for a cell.
Example: columns=["Feature","Cover"], rows=[{"label":"Hire","cells":["Hire","Reasonable costs"]}].
Never change policy content just to satisfy a column count.
Use only exact IDs from SOURCE COMPLETENESS CHECKLIST in source_clause_ids.
For blocks outside that checklist use ordinary page evidence and no invented checklist ID.
Complete source-block evidence can be on its owning record or that record's exceptions.
Adjacent evidence snippets may be joined in their original order on the same page;
do not omit intervening words, reorder text, remove sidebars or correct source text in quotes.
For records with source_clause_ids (and their exceptions), never use '...' or '…' to skip
source text inside a quote; quote each needed sentence or bullet as its own evidence entry.
Put readable interpretations in text/details, separate from verbatim evidence.
Exception evidence: the owning rule carries the complete source block. Each exception's
evidence must quote verbatim, on that page, at least the complete sentence or bullet
containing its unless/except/does-not-apply cue (the whole bullet 'X, unless Y.', not
just 'unless Y.'). If the cue ends with ':' and the carve-out continues in the next
block, quoting that complete next block also suffices. Every such cue needs an exception.
Shared liability caps include every covered member of the source-defined liability section.
A cap on 'all claims from any one incident for legal liability covered by this policy' is
policy-wide: in EACH product, all covered liability members share one such pool.
General exclusions that 'apply to all sections of your policy' (or give 'no cover under
any section of this policy') apply to EVERY product: repeat each one, with its evidence
and exceptions, in each product's policy_rules. Never leave such a product's rules [].
If a single option names fire, theft and attempted theft and gives one common payout
rule, retain that rule in one shared pool linked reciprocally to all three events.
Do not silently omit the total-loss branch for attempted theft or duplicate shared caps.
Limit basis convention: any limit with a market_value term (the insured car's value)
uses basis=per_vehicle, e.g. lesser_of(repair up to the scheduled amount, market value).
Exception scope convention, as (preserved_cover, not_restored_cover) pairs only:
(own_vehicle_damage, third_party_liability) only when the exception's evidence explicitly
separates own-car damage from liability; (both, none) when the carve-out lifts the whole
exclusion; otherwise (source_defined, source_defined). Do not use unknown here.
"""
V4_POLICY_WIDE_RULES = """General exclusions that 'apply to all sections of your policy' (or give 'no cover under
any section of this policy') apply to EVERY product: repeat each one, with its evidence
and exceptions, in each product's policy_rules. Never leave such a product's rules [].
"""
GUIDANCE_V5 = GUIDANCE.replace(V4_POLICY_WIDE_RULES, """General exclusions that 'apply to all sections of your policy' (or give 'no cover under
any section of this policy') are written ONCE in shared_policy_rules, and every product
sets shared_policy_rules_applicability=applies. Do not repeat them in products.
""")
assert GUIDANCE_V5 != GUIDANCE

SCOPE_PAIRS = {('own_vehicle_damage', 'third_party_liability'), ('both', 'none'), ('source_defined', 'source_defined')}


def _limits(node, path):
    """Every structured limit with its JSON path (benefits, pools and sub-limits)."""
    if isinstance(node, dict):
        if {'limit_id', 'terms', 'basis'} <= node.keys():
            yield path, node
        for key, value in node.items():
            yield from _limits(value, f'{path}.{key}')
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _limits(value, f'{path}[{i}]')


def issue(path, message):
    return dict(path=path, message=message)


def local_issues(payload):
    errors = []
    for ti, table in enumerate(payload['document_evidence']['coverage_summary_tables']):
        for ri, row in enumerate(table['rows']):
            actual, expected = len(row['cells']), len(table['columns'])
            if actual != expected:
                errors.append(issue(f'$.document_evidence.coverage_summary_tables[{ti}].rows[{ri}].cells',
                    f'tables: column mismatch: expected {expected}, got {actual}. label does not replace a cell. '
                    'Example columns=[Feature,Cover], label=Hire, cells=[Hire,Reasonable costs].'))
    groups = [(f'$.products[{pi}].policy_rules', p['policy_rules'] or []) for pi, p in enumerate(payload['products'])]
    groups += [('$.shared_policy_rules', payload.get('shared_policy_rules') or [])]
    for prefix, rules in groups:
        for ri, rule in enumerate(rules):
            path = f'{prefix}[{ri}].exceptions'
            context = f"rule_id={rule['rule_id']}; source_clause_ids={rule['source_clause_ids']}"
            if EXCEPTION.search(' '.join(e['quote'] for e in rule['evidence'])) and not rule['exceptions']:
                cues = [c for e in rule['evidence'] for c in exception_cues(e['quote'])]
                errors.append(issue(path, f'scoped_exceptions: exception cue without explicit exception; {context}; '
                                    'cue sentence(s): ' + ' | '.join(f'"{c[:300]}"' for c in cues)))
            for ei, exc in enumerate(rule['exceptions']):
                for key in ('condition', 'effect', 'scope_notes'):
                    if not exc[key].strip():
                        errors.append(issue(f'{path}[{ei}].{key}', f'Blank exception meaning/scope; {context}'))
                pair = (exc['preserved_cover'], exc['not_restored_cover'])
                if pair not in SCOPE_PAIRS:
                    errors.append(issue(f'{path}[{ei}].preserved_cover',
                        f'exception_scope: pair {pair} not allowed; use (own_vehicle_damage, third_party_liability), '
                        f'(both, none) or (source_defined, source_defined); {context}'))
                elif pair[0] == 'own_vehicle_damage' and not re.search(r'liabilit', ' '.join(e['quote'] for e in exc['evidence']), re.I):
                    errors.append(issue(f'{path}[{ei}].preserved_cover',
                        'exception_scope: own_vehicle_damage/third_party_liability needs exception evidence that explicitly '
                        f'distinguishes liability; otherwise use (source_defined, source_defined) or (both, none); {context}'))
    for pi, product in enumerate(payload['products']):
        root = f'$.products[{pi}]'
        for lpath, cap in _limits(product, root):
            if any(t['amount_kind'] == 'market_value' for t in cap['terms']) and cap['basis'] != 'per_vehicle':
                errors.append(issue(f'{lpath}.basis', f"limit_basis: limit_id={cap['limit_id']} has a market_value term; "
                                    f"use basis=per_vehicle, not {cap['basis']}"))
        for bi, benefit in enumerate(product['hire_car_benefits'] or []):
            if DAILY.search(' '.join(e['quote'] for e in benefit['evidence'])) and not has_daily_cost(benefit):
                errors.append(issue(f'{root}.hire_car_benefits[{bi}].limits',
                    f"structured_costs: reasonable daily cost missing; benefit_id={benefit['benefit_id']}"))
        for ei, excess in enumerate(product['excesses'] or []):
            text = ' '.join(excess['conditions'] + [e['quote'] for e in excess['evidence']])
            if STACKING.search(text) and not (excess['combination_rule'] or '').strip():
                errors.append(issue(f'{root}.excesses[{ei}].combination_rule',
                    f"excess_combination: missing stacking rule; excess_id={excess['excess_id']}"))
    return errors


def _joined_evidence(evidence):
    """Join only consecutive same-page snippets, preserving all intervening text."""
    result = list(evidence)
    group = []
    for e in evidence:
        if group and group[-1]['pdf_page'] != e['pdf_page']:
            group = []
        group.append(e)
        if len(group) > 1:
            result.append({**e, 'quote': ' '.join(x['quote'] for x in group)})
    return result


def source_issues(payload, clauses, document_text=None):
    # A validation view only: the stored/raw candidate is never rewritten.
    view = deepcopy(payload)
    paths = {}
    groups = [(f'$.products[{pi}].', p) for pi, p in enumerate(view['products'])]
    if 'shared_policy_rules' in view:  # review_v5: document-level rules count like one more rule owner
        shared = {'policy_rules': view.pop('shared_policy_rules')}
        groups.append(('$.shared_', shared))
        view['products'].append(shared)
    for prefix, p in groups:
        for owner in [*OWNERS, 'policy_rules', 'excesses']:
            for bi, item in enumerate(p.get(owner) or []):
                for sid in item['source_clause_ids']:
                    paths.setdefault(sid, []).append(f'{prefix}{owner}[{bi}].source_clause_ids')
                item['evidence'] = _joined_evidence(item['evidence'])
                for exc in item.get('exceptions', []):
                    exc['evidence'] = _joined_evidence(exc['evidence'])
                    item['evidence'].extend(exc['evidence'])
    following = following_blocks(document_text) if document_text is not None else None
    messages = coverage_issues(view, clauses, OWNERS, following)
    bad_quotes = {m.split(':', 1)[0] for m in messages if ': evidence must retain' in m}
    errors = []
    for message in messages:
        sid = message.split(':', 1)[0]
        if ': missing source clause' in message and sid in bad_quotes:
            continue  # Citation mismatch already explains this unverified mapping.
        if ': evidence must retain' in message:
            message += '; evidence_mismatch: mapped clause, not proof of semantic omission. Preserve full verbatim block/page; do not rewrite source layout.'
        elif ': missing source clause' in message:
            message += '; source_mapping_missing: no verified owner mapping, inspect source and candidate.'
        if message.startswith('Unknown/stale source_clause_id '):
            sid = message.split('Unknown/stale source_clause_id ', 1)[1]
        for path in paths.get(sid, ['$.products']):
            errors.append(issue(path, message))
    return [dict(path=path, message=message) for path, message in dict.fromkeys(
        (e['path'], e['message']) for e in errors)]


LIABILITY_OWNERS = ['third_party_property_liability', 'substitute_car_liability_feature', 'caravans_and_trailers_tppd_extension']
POLICY_LIABILITY_CAP = re.compile(r'all claims (?:arising )?from any one incident for (?:all )?legal liability covered by this policy'
                                  r'\s+is\s+\$([\d,]+(?:\.\d+)?)\s*(million)?')


def _shared_liability_pool(product, benefits, amount):
    ids = {b['benefit_id'] for b in benefits}
    for pool in product['limit_pools'] or []:
        cap = pool['limit']
        if (ids <= set(pool['member_benefit_ids']) and all(pool['pool_id'] in b['limit_pool_ids'] for b in benefits)
                and cap['period'] == 'per_incident' and cap['basis'] == 'aggregate' and cap['combination'] == 'single'
                and len(cap['terms']) == 1 and cap['terms'][0]['amount_kind'] == 'fixed'
                and (amount is None or cap['terms'][0]['amount_aud'] == amount)):
            return True
    return False


def policy_liability_issues(payload, document_text):
    """A cap stated for 'legal liability covered by this policy' is policy-wide.

    It applies to each product in the PDS: every covered liability member of a
    product (two or more) must share one per_incident/aggregate pool at the
    stated amount. One covered member needs no pool. No insurer/amount constants.
    """
    caps = [(page, POLICY_LIABILITY_CAP.search(normalized(text))) for page, _, text in source_blocks(document_text)]
    caps = [(page, m) for page, m in caps if m]
    if not caps:
        return []
    page, m = caps[0]
    amount = float(m[1].replace(',', '')) * (1_000_000 if m[2] else 1)
    if any(float(x[1].replace(',', '')) * (1_000_000 if x[2] else 1) != amount for _, x in caps):
        return []  # conflicting policy-wide amounts: leave to review rather than guess
    errors = []
    for pi, product in enumerate(payload['products']):
        benefits = [b for owner in LIABILITY_OWNERS for b in product[owner] or []
                    if b['status'] not in {'excluded', 'not_applicable'}]
        if len(benefits) >= 2 and not _shared_liability_pool(product, benefits, amount):
            errors.append(issue(f'$.products[{pi}].limit_pools',
                f'shared_liability_relation: PDF page {page} caps all legal liability covered by this policy at one '
                f'per-incident amount (parsed amount={amount}). Covered liability members '
                f"{[b['benefit_id'] for b in benefits]} need one reciprocal per_incident/aggregate pool with that amount; "
                'do not repeat the cap on each member.'))
    return errors


POLICY_WIDE_RULES = re.compile(r'\b(?:(?:apply|applies) to (?:all|every|each) sections? of (?:your|this|the) policy'
                               r'|(?:no cover|not covered|not be liable) under any section of (?:your|this|the) policy)\b')


def policy_wide_rule_issues(payload, clauses, document_text):
    """General exclusions stated to apply to every section of the policy apply to every product.

    A product's rules are its own policy_rules plus, in review_v5, the document-level
    shared_policy_rules when it marks them as applying. v4: multi-product documents only,
    since one product's checklist coverage is already enforced by coverage_issues.
    Without the explicit source cue nothing is inferred.
    """
    required = [c.source_id for c in clauses if c.kind == 'policy_rule']
    v5 = 'shared_policy_rules' in payload
    if (len(payload['products']) < 2 and not v5) or not required:
        return []
    cues = [(page, text) for page, _, text in ((p, b, normalized(t)) for p, b, t in source_blocks(document_text))
            if POLICY_WIDE_RULES.search(text)]
    if not cues:
        return []
    page, text = cues[0]
    m = POLICY_WIDE_RULES.search(text)
    sentence = text[text.rfind('. ', 0, m.start()) + 1:].strip()[:200]
    shared = {sid for rule in payload.get('shared_policy_rules') or [] for sid in rule['source_clause_ids']}
    errors = []
    for pi, product in enumerate(payload['products']):
        mapped = {sid for rule in product['policy_rules'] or [] for sid in rule['source_clause_ids']}
        if v5 and product['shared_policy_rules_applicability'] == 'applies':
            mapped |= shared
        missing = [sid for sid in required if sid not in mapped]
        if missing and v5:
            errors.append(issue(f'$.products[{pi}].shared_policy_rules_applicability',
                f'policy_wide_rules: PDF page {page} states "{sentence}", so the general exclusions apply to every '
                f"product. product_name={product['product_name']} has applicability="
                f"{product['shared_policy_rules_applicability']} and covers {len(required) - len(missing)}/{len(required)} "
                f'checklist rule IDs; missing e.g. {missing[:5]}. Write each general exclusion once in shared_policy_rules '
                '(full evidence and exceptions) and set shared_policy_rules_applicability=applies; do not repeat them in products.'))
        elif missing:
            errors.append(issue(f'$.products[{pi}].policy_rules',
                f'policy_wide_rules: PDF page {page} states "{sentence}", so the general exclusions apply to every '
                f"product. product_name={product['product_name']} maps {len(required) - len(missing)}/{len(required)} "
                f'checklist rule IDs; missing e.g. {missing[:5]}. Repeat each general exclusion, with its full evidence '
                'and exceptions, in every product; policy_rules=[] would wrongly mean none apply.'))
    return errors


ELLIPSIS = re.compile(r'\.\.\.|…')


def _checklist_evidence(payload):
    """Evidence of records mapped to checklist IDs (and their exceptions), with JSON paths."""
    groups = [(f'$.products[{pi}].', p) for pi, p in enumerate(payload['products'])]
    groups.append(('$.shared_', {'policy_rules': payload.get('shared_policy_rules') or []}))
    for prefix, p in groups:
        for owner in [*OWNERS, 'policy_rules', 'excesses']:
            for bi, item in enumerate(p.get(owner) or []):
                if not item['source_clause_ids']:
                    continue
                for ei, ev in enumerate(item['evidence']):
                    yield f'{prefix}{owner}[{bi}].evidence[{ei}]', ev
                for xi, exc in enumerate(item.get('exceptions', [])):
                    for ei, ev in enumerate(exc['evidence']):
                        yield f'{prefix}{owner}[{bi}].exceptions[{xi}].evidence[{ei}]', ev


def ellipsis_issues(payload, document_text):
    """Checklist-mapped quotes that skip source text with '...'/'…'.

    Only records carrying source_clause_ids need verbatim source; a quote is
    allowed when the page itself contains that exact text.
    """
    pages = {}
    for page, _, text in source_blocks(document_text):
        pages[page] = pages.get(page, '') + ' ' + normalized(text)
    errors = []
    for path, ev in _checklist_evidence(payload):
        quote = ev['quote'] or ''
        if ELLIPSIS.search(quote) and normalized(quote) not in pages.get(ev['pdf_page'], ''):
            errors.append(issue(f'{path}.quote',
                f"evidence_ellipsis: quote on PDF page {ev['pdf_page']} uses '...'/'…' to skip source text: "
                f'"{quote[:160]}". Evidence for checklist-mapped records must be contiguous verbatim text. Quote each '
                'needed sentence or bullet as a separate evidence entry instead of eliding the words between them.'))
    return errors


def relation_issues(payload, document_text):
    """Narrow, explicit source relations. No insurer, page or amount constants.

    Policy-wide liability caps apply to every product. Other relations are
    checked only for single-product documents: do not infer multi-tier
    applicability. Numeric truth and full semantics still require review.
    """
    policy_wide = policy_liability_issues(payload, document_text)
    if len(payload['products']) != 1:
        return policy_wide
    blocks = [(page, normalized(text)) for page, _, text in source_blocks(document_text)]
    p = payload['products'][0]
    pools = p['limit_pools'] or []
    errors = []

    def common_pool(benefits, *, lesser=False, liability=False, amount=None):
        ids = {b['benefit_id'] for b in benefits}
        for pool in pools:
            if not ids.issubset(set(pool['member_benefit_ids'])):
                continue
            if not all(pool['pool_id'] in b['limit_pool_ids'] for b in benefits):
                continue
            cap = pool['limit']
            if liability and (cap['period'] != 'per_incident' or cap['basis'] != 'aggregate'):
                continue
            if amount is not None and (cap['combination'] != 'single' or len(cap['terms']) != 1 or
                    cap['terms'][0]['amount_kind'] != 'fixed' or cap['terms'][0]['amount_aud'] != amount):
                continue
            if lesser and (cap['combination'] != 'lesser_of' or
                    len(cap['terms']) != 2 or
                    {'market_value', 'schedule_specific'} != {t['amount_kind'] for t in cap['terms']}):
                continue
            return True
        return False

    definitions = [(page, text) for page, text in blocks if
        re.search(r'in this section.{0,30}your car includes.{0,80}trailer.{0,80}substitute car', text)]
    for page, definition in definitions:
        caps = [text for cp, text in blocks if abs(cp - page) <= 1 and
                re.search(r'all legal liability claims.{0,50}(?:one|any) incident', text)]
        if not caps:
            continue
        owners = ['third_party_property_liability', 'substitute_car_liability_feature', 'caravans_and_trailers_tppd_extension']
        benefits = [b for owner in owners for b in p[owner] or [] if b['status'] not in {'excluded', 'not_applicable'}]
        number = re.search(r'\bis\s*\$([\d,]+(?:\.\d+)?)', caps[0])
        amount = float(number[1].replace(',', '')) if number else None
        absent = [owner for owner in owners if not p[owner]]
        if absent or not common_pool(benefits, liability=True, amount=amount):
            errors.append(issue('$.products[0].limit_pools',
                f'shared_liability_relation: PDF page {page} explicitly includes trailer/substitute car in one liability section '
                f'with one all-claims incident cap (parsed amount={amount}). Represent covered members and a reciprocal '
                'per_incident/aggregate pool preserving the stated amount; do not invent amounts.'
                + (f' Missing member field(s): {absent}; add each as a covered benefit in that pool.' if absent else '')))
        break
    if policy_wide and not any(e['message'].startswith('shared_liability_relation') for e in errors):
        errors.extend(policy_wide)
    for ai, addon in enumerate(p['available_addons'] or []):
        name = normalized(addon['option_name'])
        if not name or addon['availability'] == 'not_available':
            continue
        named = [(page, text) for page, text in blocks if name in text and
                 re.search(r'fire,? theft or attempted theft', text)]
        payout = [text for page, text in blocks if any(abs(page - np) <= 1 for np, _ in named)
                  and name in text and 'total loss' in text and 'market value' in text
                  and 'certificate of insurance' in text and 'whichever is lower' in text]
        if not payout:
            continue
        benefits = [b for b in p['covered_events'] or [] if b['option_id'] == addon['option_id']
                    and b['details']['event'] in {'fire', 'theft', 'attempted_theft'}
                    and b['status'] not in {'excluded', 'not_applicable'}]
        if {b['details']['event'] for b in benefits} != {'fire', 'theft', 'attempted_theft'} or not common_pool(benefits, lesser=True):
            errors.append(issue(f'$.products[0].available_addons[{ai}].benefit_ids',
                f"shared_event_payout: option_id={addon['option_id']} names fire/theft/attempted theft with one common payout. "
                'All three events need reciprocal membership in the common lesser_of(schedule_specific,market_value) pool; '
                'retain repair/total-loss conditions. Do not omit attempted-theft total loss.'))
    return errors


def validate_runtime(payload, base_validator, clauses, document_text):
    errors = []
    try:
        base_validator(payload)
    except ValueError as exc:
        errors.append(issue('$', str(exc)))
    # Shape is already checked by run_structured_output before this function.
    specific = local_issues(payload)
    if specific and errors and any(errors[0]['message'].startswith(prefix) for prefix in
            ('tables: column mismatch', 'scoped_exceptions:', 'structured_costs:', 'excess_combination:')):
        errors = []  # Replace only the known generic duplicate with its paths.
    errors.extend(specific)
    errors.extend(source_issues(payload, clauses, document_text))
    errors.extend(relation_issues(payload, document_text))
    errors.extend(policy_wide_rule_issues(payload, clauses, document_text))
    errors.extend(ellipsis_issues(payload, document_text))
    if errors:
        raise BusinessDiagnostics(errors)
