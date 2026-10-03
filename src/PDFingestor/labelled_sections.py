"""Recover stacked sidebar labels without mixing them into adjacent prose."""
from src.PDFingestor.models import TextBlock


def extract(words, page_num, page_width, group_lines, page_height=None):
    anchors = []
    for word in words:
        if word['text'] != 'We' or word['x0'] > page_width * .3:
            continue
        below = sorted([w for w in words if abs(w['x0']-word['x0']) < 2
                        and word['bottom'] <= w['top'] <= word['bottom']+36],
                       key=lambda w: w['top'])
        parts = [word]
        for candidate in below[:2]:
            if candidate['top']-parts[-1]['bottom'] > 12:
                break
            parts.append(candidate)
            label = ' '.join(w['text'] for w in parts).replace('’', "'")
            if label in {'We cover', "We don't cover"}:
                anchors.append((parts, label))
                break
    if not anchors:
        return [], words
    # A Limit label alone is not enough to activate sidebar interpretation.
    left_edges = [parts[0]['x0'] for parts, _ in anchors]
    anchors.extend(([w], 'Limit') for w in words if w['text']=='Limit'
                   and any(abs(w['x0']-x)<2 for x in left_edges))
    anchors.sort(key=lambda a: a[0][0]['top'])
    consumed, blocks = set(), []
    for index, (parts, label) in enumerate(anchors):
        top = parts[0]['top']
        left_end = max(w['x1'] for w in parts)
        aligned = [w for w in words if abs(w['top']-top)<3
                   and w['x0'] > left_end+8 and w['x0'] < page_width*.65]
        if not aligned:
            continue
        right_start = min(w['x0'] for w in aligned)
        body_size = min((w.get('size',0) for w in aligned), default=0)
        gutter = (left_end+right_start)/2
        end = anchors[index+1][0][0]['top']-1 if index+1<len(anchors) else float('inf')
        # Stop at a new full-width paragraph, callout or footer instead of
        # attaching everything until the next sidebar label.
        breaks = [w['top'] for w in words if w['top'] > parts[-1]['bottom']+1
                  and w['x0'] < gutter and id(w) not in {id(p) for p in parts}]
        if breaks:
            end = min(end, min(breaks)-1)
        headings = [w['top'] for w in words if body_size and w.get('size',0)>body_size+.5
                    and w['x0']>=gutter and w['top']>parts[-1]['bottom']+1]
        if headings:
            end = min(end,min(headings)-1)
        if page_height:
            end = min(end,page_height*.94)
        body = [w for w in words if top-1 <= w['top'] < end and w['x0'] >= gutter]
        if not body:
            continue
        selected = [*parts, *body]
        if any(id(w) in consumed for w in selected):
            continue
        lines = group_lines(body, page_width=page_width)
        content = label + ':\n' + '\n'.join(line['text'] for line in lines)
        bbox = (min(w['x0'] for w in selected), min(w['top'] for w in selected),
                max(w['x1'] for w in selected), max(w['bottom'] for w in selected))
        blocks.append(TextBlock(block_id=f'p{page_num}_label_{index}',content=content,
                                bbox=bbox,top=bbox[1],source_engine='pdfplumber:label-region'))
        consumed.update(id(w) for w in selected)
    return blocks, [w for w in words if id(w) not in consumed]
