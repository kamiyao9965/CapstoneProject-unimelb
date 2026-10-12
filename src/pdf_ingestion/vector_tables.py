"""Conservative recovery of circular vector symbols using a same-page legend.

No insurer names, product benefits or colour-to-coverage assumptions are encoded.
Unknown/ambiguous geometry rejects the whole recovery instead of guessing status.
"""
from __future__ import annotations


def circles(page):
    return [c for c in getattr(page, 'curves', [])
            if 6 <= c['width'] <= 24 and abs(c['width'] - c['height']) < .3
            and [p[0] for p in c.get('path', [])] == ['m', 'c', 'c', 'c', 'c']]


def signature(page, circle):
    x, y, size = circle['x0'], circle['top'], circle['width']
    shapes = []
    for c in page.curves:
        if c['x0'] < x-.05 or c['x1'] > circle['x1']+.05 or c['top'] < y-.05 or c['bottom'] > circle['bottom']+.05:
            continue
        shape = []
        for command in c.get('path', []):
            shape.append(command[0])
            for point in command[1:]:
                shape.extend(((point[0]-x)/size, (point[1]-y)/size))
        shapes.append((c.get('fill'), c.get('stroke'), str(c.get('non_stroking_color')), str(c.get('stroking_color')), shape))
    return shapes


def matches(first, second):
    if len(first) != len(second):
        return False
    for a, b in zip(first, second):
        if a[:4] != b[:4] or len(a[4]) != len(b[4]):
            return False
        for x, y in zip(a[4], b[4]):
            if isinstance(x, str) or isinstance(y, str):
                if x != y:
                    return False
            elif abs(x-y) > .025:
                return False
    return True


def recover(page, table):
    """Return label + status matrix and expanded bbox, or None."""
    symbols = circles(page)
    if not symbols or len(table.rows) < 3:
        return None
    legend = []
    for c in symbols:
        if c['bottom'] >= table.bbox[1]:
            continue
        text = page.crop((c['x1']+1, c['top']-1, min(page.width, c['x1']+110), c['bottom']+1)).extract_text() or ''
        text = ' '.join(text.split())
        if text.lower() in {'covered', 'optional cover', 'not covered', 'not required'}:
            legend.append((signature(page, c), text))
    if len({label for _, label in legend}) < 3:
        return None
    raw = table.extract()
    headers = [' '.join((cell or '').split()) for cell in raw[0]]
    if not all(headers) or len(headers) < 2:
        return None
    words = page.extract_words(use_text_flow=False)
    rows, label_x = [], table.bbox[0]
    for row in table.rows[1:]:
        cells = []
        for box in row.cells:
            if box is None:
                return None
            candidates = [c for c in symbols if box[0] <= c['x0'] and c['x1'] <= box[2]
                          and box[1] <= c['top'] and c['bottom'] <= box[3]]
            # Nested circles belong to the same icon, not extra statuses.
            candidates = [c for c in candidates if not any(d is not c and d['width'] > c['width']
                          and d['x0'] <= c['x0'] and d['x1'] >= c['x1']
                          and d['top'] <= c['top'] and d['bottom'] >= c['bottom'] for d in candidates)]
            if len(candidates) != 1:
                return None
            found = {label for template, label in legend if matches(signature(page, candidates[0]), template)}
            if len(found) != 1:
                return None
            cells.append(found.pop())
        label_words = [w for w in words if w['x1'] < table.bbox[0]
                       and row.bbox[1] <= (w['top']+w['bottom'])/2 < row.bbox[3]]
        if not label_words:
            return None
        label_x = min(label_x, *(w['x0'] for w in label_words))
        rows.append([' '.join(w['text'] for w in label_words), *cells])
    return [['Benefit', *headers], *rows], (label_x, table.bbox[1], table.bbox[2], table.bbox[3])
