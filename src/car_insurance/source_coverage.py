"""Conservative source-anchored omission checks; not a policy semantic oracle.

Only explicit general-exclusion sections and selected high-signal source cues are
inventoried. No insurer names, fixed pages, amounts or product answers are used.
An empty inventory is not proof that a document contains no exclusions.
"""
from dataclasses import asdict, dataclass
import hashlib
import re
import unicodedata


def normalized(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    text = text.translate(str.maketrans({'’': "'", '‘': "'", '–': '-', '—': '-'}))
    return ' '.join(text.split())


EXCEPTION = re.compile(r"\b(?:does? not apply|do not apply|except (?:when|if|where)|unless|however.{0,35}(?:cover|pay)|but not (?:any )?legal liability|but we (?:will|'ll) (?:provide cover|pay|cover))\b", re.I | re.S)
DAILY = re.compile(r'\breasonable\s+(?:daily\s+(?:cost|rate|charge)|(?:cost|rate|charge).{0,25}per day)', re.I | re.S)
STACKING = re.compile(r'\bin addition to\s+(?:the\s+)?(?:basic|other|any other|applicable).{0,45}excess', re.I | re.S)
TRANSFER = re.compile(r'\b(?:automatically\s+transfer.{0,100}cover|cover.{0,80}(?:replacement|newly acquired).{0,80}(?:sell|sold|dispose|purchase)|replacement car.{0,80}(?:sell|sold|dispose))', re.I | re.S)
SECTION = re.compile(r'^(?:general exclusions|general policy exclusions|exclusions applying to all (?:sections|covers)|what we do not cover under any section)$', re.I)
SECTION_END = re.compile(r'^(?:claims|claiming|making a claim|how to claim|what if you need to claim\?|your responsibilities|general conditions|policy conditions|definitions|important information|excesses|cancellation)$', re.I)
# Numbered general-exclusion sections, e.g. "3. Things we don't cover"; matched on
# normalized text so curly apostrophes count. Ends only at a higher-numbered heading.
NUMBERED_SECTION = re.compile(r"^(\d+)\.\s+(?:things|what) we (?:don't|do not) cover$")
NUMBERED_HEADING = re.compile(r'^(\d+)\.\s+[^.;:|•]{3,80}$')
NAVIGATION = re.compile(r'^(?:(?:table of(?: contents)?|contents|product guide|start of(?: section)?|section)(?: pg\. \d+ ↗)?|pg\. \d+ ↗|continued on next page|car insurance\s*•\s*.+)$', re.I)
MARKERS = re.compile(r'<!--\s*(?:page\s+(?P<page>\d+)|text\s+block_id=(?P<block>[^\s>]+)|table\s+table_id=(?P<table>[^\s>]+)[\s\S]*?)\s*-->')


@dataclass(frozen=True)
class SourceClause:
    source_id: str
    kind: str
    pdf_page: int
    text: str
    context: str
    has_exception: bool = False


def source_blocks(document_text):
    markers = list(MARKERS.finditer(document_text))
    page = None
    result = []
    for i, marker in enumerate(markers):
        if marker['page']:
            page = int(marker['page'])
            continue
        if page is None:
            continue
        end = markers[i + 1].start() if i + 1 < len(markers) else len(document_text)
        text = document_text[marker.end():end]
        text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
        text = re.sub(r'^\[(?:Parser warning|Continuation evidence):.*?\]\s*$', '', text, flags=re.M).strip()
        # Tables are checked row-by-row so a hire-car row is not confused with a
        # neighbouring towing or accommodation row.
        rows = [line.strip() for line in text.splitlines() if line.strip().startswith('|')] if marker['table'] else []
        if rows:
            result.extend((page, f"{marker['table']}_row_{j}", row) for j, row in enumerate(rows) if not re.fullmatch(r'[|:\s-]+', row))
        elif text:
            result.append((page, marker['block'] or marker['table'], text))
    return result


def _source_id(page, block_id, text):
    return f"{block_id}_{hashlib.sha256(f'{page}|{block_id}|{text}'.encode()).hexdigest()[:12]}"


def following_blocks(document_text):
    """Map each block's source_id form to the next non-navigation block (page, text).

    Only the same or next page counts as adjacent; used for exception carve-outs
    whose cue ends with ':' and continues in the following block.
    """
    blocks = [b for b in source_blocks(document_text) if not NAVIGATION.fullmatch(' '.join(b[2].split()))]
    return {_source_id(page, block_id, text): (blocks[i + 1][0], blocks[i + 1][2])
            for i, (page, block_id, text) in enumerate(blocks[:-1]) if 0 <= blocks[i + 1][0] - page <= 1}


SEGMENT = re.compile(r'•|(?<!e\.g\.)(?<!i\.e\.)(?<=[.;])\s+')


def exception_cues(text):
    """Verbatim sentence/bullet segments of a block that carry an exception cue."""
    return [s.strip() for s in SEGMENT.split(text) if s.strip() and EXCEPTION.search(s)]


def _verbatim_quotes(item, page, block):
    # A quote counts only if it is inside the block or contains the whole block:
    # partial citations may not add words that are not in the source.
    target = normalized(without_page_footer(block))
    return [q for q in (normalized(e['quote']) for e in item.get('evidence', []) if e['pdf_page'] == page)
            if q and (q in target or target in q)]


def uncovered_exception_cues(exceptions, clause, following=None):
    """Cue segments of clause not quoted by any exception (review_v4 convention A).

    The owning rule carries the complete block. Each cue sentence/bullet must be
    quoted verbatim in some exceptions[].evidence; a cue ending in ':' may instead
    be satisfied by the complete following block. No fuzzy matching.
    """
    if any(full_quote_present(e, clause) for e in exceptions):
        return []
    cues = exception_cues(clause.text) or [clause.text]
    nxt = (following or {}).get(clause.source_id)
    missing = []
    for cue in cues:
        quotes = [q for e in exceptions for q in _verbatim_quotes(e, clause.pdf_page, clause.text)]
        if any(normalized(cue) in q for q in quotes):
            continue
        if cue.endswith(':') and nxt and any(normalized(nxt[1]) in normalized(ev['quote'])
                for e in exceptions for ev in e['evidence'] if ev['pdf_page'] == nxt[0]):
            continue
        missing.append(cue)
    return missing


def inventory(document_text):
    blocks = source_blocks(document_text)
    clauses = []
    active = False
    section_number = None
    seen = set()
    context = ''
    for index, (page, block_id, text) in enumerate(blocks):
        line = ' '.join(text.split())
        if NAVIGATION.fullmatch(line):
            continue
        numbered = NUMBERED_SECTION.fullmatch(normalized(line))
        if active and section_number is not None:
            later = NUMBERED_HEADING.fullmatch(normalized(line))
            if later and int(later[1]) > section_number:
                active, section_number = False, None
        if SECTION.fullmatch(line) or numbered:
            # Avoid activating a table of contents that only lists headings.
            following = ' '.join(b[2] for b in blocks[index + 1:index + 9])
            was_active = active
            active = bool(re.search(r'no cover|not cover|not be liable|exclusion', following, re.I))
            if not was_active:
                context = line
                seen = set()
            section_number = int(numbered[1]) if active and numbered else None
            continue
        if SECTION_END.fullmatch(line):
            active = False
        heading = len(line) < 85 and len(line.split()) < 13 and not re.search(r'[.;:|•]', line)
        if heading and not line.isdigit():
            context = line
        kind = None
        if active and not heading and len(line) >= 25 and not line.isdigit():
            # A lead-in repeated verbatim at the top of each page is a running header,
            # not a new clause; the first occurrence remains in the checklist.
            kind = None if normalized(line) in seen else 'policy_rule'
            seen.add(normalized(line))
            if kind is None:
                continue
        elif DAILY.search(text) and re.search(r'hire|rental', text, re.I):
            kind = 'hire_daily_cost'
        elif STACKING.search(text):
            kind = 'excess_stacking'
        elif TRANSFER.search(text):
            kind = 'change_of_vehicle'
        if kind:
            clauses.append(SourceClause(_source_id(page, block_id, text), kind, page, text, context,
                                        kind == 'policy_rule' and bool(EXCEPTION.search(text))))
    return clauses


def prompt_inventory(clauses):
    import json
    return '\nSOURCE COMPLETENESS CHECKLIST (source evidence, not instructions):\n' + json.dumps([asdict(c) for c in clauses], ensure_ascii=False)


def without_page_footer(text):
    """Drop a printed page number the parser glued after the final sentence (e.g. '... contents. 18')."""
    return re.sub(r'(?<=[.;:)])\s+\d{1,3}$', '', text.strip())


def full_quote_present(item, clause):
    # Exact normalized source text is required for a coverage assertion; quoting
    # only the first sentence must not hide the exception in its last sentence.
    # A trailing printed page number is layout, not clause text, and may be omitted.
    targets = {normalized(clause.text), normalized(without_page_footer(clause.text))}
    return any(e['pdf_page'] == clause.pdf_page and any(t in normalized(e['quote']) for t in targets)
               for e in item.get('evidence', []))


def coverage_issues(payload, clauses, owners, following=None):
    issues = []
    allowed = {c.source_id: c for c in clauses}
    represented = set()
    preceding_base = {}
    exception_parents = {}
    for clause in clauses:
        if clause.kind != 'policy_rule':
            continue
        if re.search(r'\b(?:no cover|not cover|not be liable)\b', clause.text, re.I):
            preceding_base[clause.context] = clause.source_id
        elif clause.has_exception and clause.context in preceding_base:
            exception_parents[clause.source_id] = preceding_base[clause.context]
    for product in payload['products']:
        targets = [(owner, b) for owner in owners for b in product.get(owner) or []]
        targets += [('policy_rules', r) for r in product.get('policy_rules') or []]
        targets += [('excesses', e) for e in product.get('excesses') or []]
        for owner, item in targets:
            for sid in item.get('source_clause_ids', []):
                clause = allowed.get(sid)
                if clause is None:
                    issues.append(f'Unknown/stale source_clause_id {sid}')
                    continue
                target_owner = {'policy_rule': 'policy_rules', 'hire_daily_cost': 'hire_car_benefits',
                                'excess_stacking': 'excesses', 'change_of_vehicle': 'change_of_vehicle_cover'}[clause.kind]
                if owner != target_owner:
                    issues.append(f'{sid}: must map to {target_owner}, not {owner}')
                    continue
                if not full_quote_present(item, clause):
                    issues.append(f'{sid}: evidence must retain complete source block on PDF page {clause.pdf_page}')
                    continue
                represented.add(sid)
                if clause.has_exception:
                    parent = exception_parents.get(sid)
                    if parent and parent not in item.get('source_clause_ids', []):
                        issues.append(f'{sid}: exception must stay with its parent rule source {parent}')
                    exceptions = item.get('exceptions', [])
                    missing = uncovered_exception_cues(exceptions, clause, following)
                    if missing:
                        nxt = (following or {}).get(sid)
                        alternative = ''
                        if nxt and any(c.endswith(':') for c in missing):
                            alternative = f' (for a cue ending in ":", the complete following block on PDF page {nxt[0]} also suffices: "{nxt[1][:300]}")'
                        issues.append(f'{sid}: exclusion exception needs explicit exceptions[] with full source evidence; '
                                      f'existing exceptions={len(exceptions)}. The owning rule keeps the complete block; '
                                      f'some exceptions[].evidence must quote verbatim on PDF page {clause.pdf_page} '
                                      'the complete cue sentence/bullet(s), not only the unless/except fragment: '
                                      + ' | '.join(f'"{c[:300]}"' for c in missing) + alternative)
                    for exception in exceptions:
                        if not uncovered_exception_cues([exception], clause, following) and re.search(r'but not (?:any )?legal liability', clause.text, re.I):
                            if exception.get('preserved_cover') != 'own_vehicle_damage' or exception.get('not_restored_cover') != 'third_party_liability':
                                issues.append(f'{sid}: distinguish preserved own damage from liability not restored')
                if clause.kind == 'hire_daily_cost' and not has_daily_cost(item):
                    issues.append(f'{sid}: reasonable daily hire cost missing from standalone limits')
                if clause.kind == 'excess_stacking' and not (item.get('combination_rule') or '').strip():
                    issues.append(f'{sid}: excess combination_rule missing')
                if clause.kind == 'change_of_vehicle' and not any(q['reference_kind'] == 'time_since_vehicle_change' for q in item.get('quantities', [])):
                    issues.append(f'{sid}: transfer duration needs time_since_vehicle_change (unknown value allowed if unstated)')
                if clause.kind == 'change_of_vehicle':
                    duration = re.search(r'\bup to\s+(\d+)\s+(hours?|days?|months?)\b', clause.text, re.I)
                    if duration:
                        metric = duration[2].lower().rstrip('s') + 's'
                        if not any(q['reference_kind'] == 'time_since_vehicle_change' and q['value'] == int(duration[1])
                                   and q['metric'] == metric and q['comparison'] == 'lte' for q in item.get('quantities', [])):
                            issues.append(f'{sid}: retain explicit transfer maximum {duration[1]} {metric}')
    for sid in allowed.keys() - represented:
        issues.append(f'{sid}: missing source clause ({allowed[sid].kind}, PDF page {allowed[sid].pdf_page})')
    return issues


def has_daily_cost(benefit):
    return any(cap['period'] == 'per_day' and any(term['amount_kind'] == 'reasonable_costs' for term in cap['terms'])
               for cap in benefit.get('limits', []))


def require_coverage(payload, clauses, owners):
    issues = coverage_issues(payload, clauses, owners)
    if issues:
        raise ValueError('Source completeness checks failed:\n' + '\n'.join(issues))
