"""Conservative quality checks; no insurance values or human approvals inferred."""
from __future__ import annotations

import re
import hashlib
import json
from pathlib import Path


def review_fingerprint(document):
    """Bind a trial waiver to PDF bytes, parser config and exact parsed pages."""
    payload = dict(pdf_hash=document.pdf_hash,
                   parser_config_hash=document.parser.get('parser_config_hash'),
                   pages=[p.model_dump(mode='json') for p in document.pages])
    return hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()


class ParserQualityError(ValueError):
    pass


def annotate_continuations(pages):
    """Link only explicit continuation headings with a unique previous-page title.

    Retain source blocks instead of copying limits across pages or products.
    """
    for index, page in enumerate(pages):
        for block in page.blocks:
            if block.type != 'text' or block.top > page.height * .25:
                continue
            match = re.match(r"^(.+?)\s*\(cont(?:inued|[’']d)?\.?\)", block.content, re.I)
            if not match:
                continue
            title = match[1].strip()
            previous = pages[index-1] if index else None
            candidates = [] if previous is None else [b for b in previous.blocks
                if b.type == 'text' and (b.content == title or b.content.startswith(title + ' Applies to')
                    or re.match(r'^'+re.escape(title)+r"\s*\(cont(?:inued|[’']d)?\.?\)", b.content, re.I))]
            link = dict(title=title, heading_block_id=block.block_id,
                        previous_page=previous.page_num if previous else None,
                        previous_block_id=candidates[0].block_id if len(candidates)==1 else None,
                        status='linked_explicit_title' if len(candidates)==1 else 'unresolved')
            page.continuations.append(link)
            if len(candidates)!=1:
                page.warnings.append(f'Unresolved continuation heading: {title}; visual review required')


def audit(document):
    issues, review_items = [], []
    for page in document.pages:
        for warning in page.warnings:
            issues.append(dict(page=page.page_num, code='parser_warning', detail=warning))
        if not page.blocks:
            issues.append(dict(page=page.page_num, code='empty_page', detail='No extracted blocks; inspect original'))
        for block in page.blocks:
            text = block.markdown if block.type=='table' else block.content
            if 'UNRESOLVED' in text:
                issues.append(dict(page=page.page_num, code='unresolved_value', detail=block.block_id))
            if text.strip().lower() in {'we', 'cover', "don't", 'don’t'}:
                issues.append(dict(page=page.page_num, code='orphan_label', detail=block.block_id))
            # Evidence-preserving review cues, NOT extracted/approved limits.
            cues = re.findall(r'\$[\d,]+(?:\.\d+)?|\b\d+\s+days?\b|\bper (?:day|item|claim|incident|policy)\b|\bexcess(?:es)?\b|\blimited cover\b|\boptional cover\b|\bnot required\b',text,re.I)
            if cues:
                review_items.append(dict(page=page.page_num,block_id=block.block_id,cues=cues,source_text=text))
            elif block.source_engine=='pdfplumber:label-region':
                review_items.append(dict(page=page.page_num,block_id=block.block_id,
                                         cues=['label/body boundary review'],source_text=text))
    return dict(pdf_id=document.pdf_id,pdf_hash=document.pdf_hash,
                parser_config_hash=document.parser.get('parser_config_hash'),
                status='blocked' if issues else 'no_detected_blockers_not_human_approved',
                issues=issues,semantic_review=review_items,
                continuations=[dict(page=p.page_num,**c) for p in document.pages for c in p.continuations])


def require_quality(documents, review_path=None):
    accepted=set()
    if review_path is not None:
        review=json.loads(Path(review_path).read_text(encoding='utf-8'))
        if review.get('decision')!='accepted_for_schema_trial' or not review.get('reviewer') or not review.get('reviewed_at'):
            raise ParserQualityError('Invalid parser trial review record')
        accepted=set(review.get('document_fingerprints',[]))
    problems=[]
    for doc in documents:
        report=audit(doc)
        if report['issues'] and review_fingerprint(doc) not in accepted:
            pages=sorted({item['page'] for item in report['issues']})
            problems.append(f'{doc.pdf_id}: PDF pages {pages}')
    if problems:
        raise ParserQualityError('Parser quality gate blocked model input. Review/fix local parsed artifacts first: ' + '; '.join(problems))
