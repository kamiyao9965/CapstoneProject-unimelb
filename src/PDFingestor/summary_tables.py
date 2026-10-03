"""Recover ruled coverage summaries whose vertical borders are not drawn."""
from src.PDFingestor.models import TableBlock

TICK = [(0.54,.813),(1,.187),(.863,0),(.404,.625),(.137,.270),(0,.458),(.267,.813),(.404,1)]


def glyph_status(page, box):
    objects = [c for c in [*page.curves,*page.lines] if box[0]<=c['x0'] and c['x1']<=box[2]
               and box[1]<=c['top'] and c['bottom']<=box[3]]
    circles = [c for c in objects if 8<c['width']<24 and abs(c['width']-c['height'])<.3
               and [p[0] for p in c.get('path',[])]==['m','c','c','c','c']]
    if len(circles)!=1:
        return 'UNRESOLVED'
    circle = circles[0]
    inside = [c for c in objects if c is not circle and c['x0']>=circle['x0'] and c['x1']<=circle['x1']
              and c['top']>=circle['top'] and c['bottom']<=circle['bottom']]
    if len(inside)==2 and all(c['object_type']=='line' for c in inside):
        a,b = inside
        # Both diagonals must cross at the same centre with comparable size.
        if all(abs(a[k]-b[k])<.5 for k in ['x0','x1','top','bottom']) and a['width']>4 and a['height']>4:
            pa,pb = a['path'],b['path']
            slope_a=(pa[-1][1][1]-pa[0][1][1])*(pa[-1][1][0]-pa[0][1][0])
            slope_b=(pb[-1][1][1]-pb[0][1][1])*(pb[-1][1][0]-pb[0][1][0])
            if slope_a*slope_b<0:
                return 'Not covered'
    if len(inside)==1:
        c=inside[0]
        if c['width']>4 and c['height']>4 and [p[0] for p in c.get('path',[])]==['m',*['l']*7,'h']:
            points=[((pt[0]-c['x0'])/c['width'],(pt[1]-c['top'])/c['height']) for cmd in c['path'] for pt in cmd[1:]]
            if len(points)==len(TICK) and all(abs(x-u)<.025 and abs(y-v)<.025 for (x,y),(u,v) in zip(points,TICK)):
                return 'Covered'
    return 'UNRESOLVED'


def recover(page, warnings, render):
    words=page.extract_words()
    if 'Summary of your cover' not in (page.extract_text() or ''):
        return None
    page_heads=[w for w in words if w['text']=='Page' and w['x0']>page.width*.7]
    if len(page_heads)!=1:
        return None
    head=page_heads[0]
    edges=[e for e in page.horizontal_edges if e['width']>20]
    right_edges=sorted({round(e['top'],2) for e in edges if e['x0']<=head['x0'] and e['x1']>=head['x1']})
    after=[y for y in right_edges if y>head['bottom']]
    if len(after)<4:
        return None
    header_bottom=after[0]
    segments=sorted([e for e in edges if abs(e['top']-header_bottom)<.1],key=lambda e:e['x0'])
    if len(segments)<4:
        return None
    # Last three ruled segments are two status columns and the page column.
    left=segments[0]['x0']
    header_words = ' '.join(w['text'] for w in words if head['top']-2<=w['top']<=head['bottom'])
    index_only = header_words == 'What we cover Page'
    cols=([left,segments[-1]['x0'],segments[-1]['x1']] if index_only
          else [left,*[e['x0'] for e in segments[-3:]],segments[-1]['x1']])
    if not all(a<b for a,b in zip(cols,cols[1:])):
        return None
    before=[y for y in right_edges if y<head['top']]
    if not before:
        return None
    top=before[-1]
    def text(box):
        return ' '.join((page.crop(box).extract_text(x_tolerance=1) or '').split())
    headers=[text((a,top,b,header_bottom)) for a,b in zip(cols,cols[1:])]
    if not all(headers):
        return None
    rows=[]
    for y0,y1 in zip(after,after[1:]):
        row=[text((a,y0,b,y1)) for a,b in zip(cols,cols[1:])]
        if not row[-1]:
            row=[text((left,y0,cols[-1],y1)),*['']*(len(cols)-2)]
        if not row[0]:
            warnings.append('Coverage summary has an empty benefit row; visual review required')
            return None
        if row[-1] and not row[-1].isdigit():
            warnings.append(f'Coverage summary row {len(rows)+1}: unverified page reference; visual review required')
        if row[-1].isdigit() and not index_only:
            for index in [1,2]:
                if not row[index]:
                    row[index]=glyph_status(page,(cols[index],y0,cols[index+1],y1))
                    if row[index]=='UNRESOLVED':
                        warnings.append(f'Coverage summary row {len(rows)+1}, column {index}: unresolved symbol; visual review required')
        rows.append(row)
    bbox=(left,top,cols[-1],after[-1])
    return TableBlock(block_id=f'p{page.page_number}_summary',table_id=f'p{page.page_number}_summary',
                      headers=headers,rows=rows,raw_rows=[headers,*rows],markdown=render(headers,rows),
                      bbox=bbox,top=top,source_engine='pdfplumber:ruled-summary',confidence=.75,
                      extraction_notes=['Rows inferred from horizontal rules; tick/cross geometry checked against visually verified development glyph shapes; unknowns explicit'])
