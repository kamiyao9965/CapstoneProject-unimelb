import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from src.PDFingestor.parser import PDFIngestor, estimate_table_confidence
from src.PDFingestor.vector_tables import recover
from src.PDFingestor.models import PageRepresentation
from src.PDFingestor.adapter import render_page


def circle(x, y, size, color):
    return dict(x0=x, x1=x+size, top=y, bottom=y+size, width=size, height=size,
                fill=True, stroke=False, non_stroking_color=color, stroking_color=0,
                path=[('m', (x,y)), *[('c',(x,y),(x+size,y),(x+size,y+size)) for _ in range(4)]])


def fixture():
    # Artificial geometry, unrelated to any proprietary policy wording.
    legends = [circle(200, y, 10, c) for y,c in [(10,'a'),(30,'b'),(50,'c')]]
    icons = [circle(x,y,14,c) for y in [125,155] for x,c in [(110,'a'),(150,'b')]]
    labels = {10:'Covered', 30:'Not covered', 50:'Optional cover'}
    page = NS(width=300, curves=legends+icons)
    page.crop = lambda box: NS(extract_text=lambda: labels.get(round(box[1]+1), ''))
    page.extract_words = lambda **kw: [dict(text='Example',x0=20,x1=80,top=y,bottom=y+10) for y in [125,155]]
    table = NS(bbox=(100,100,180,180), extract=lambda: [['Plan A','Plan B'],['',''],['','']],
               rows=[NS(bbox=(100,100,180,120),cells=[]),
                     NS(bbox=(100,120,180,150),cells=[(100,120,140,150),(140,120,180,150)]),
                     NS(bbox=(100,150,180,180),cells=[(100,150,140,180),(140,150,180,180)])])
    return page, table


class VectorQualityTest(unittest.TestCase):
    def test_same_page_legend_with_scaled_symbols(self):
        page, table = fixture()
        rows, bbox = recover(page,table)
        self.assertEqual(rows[1], ['Example','Covered','Not covered'])
        self.assertEqual(bbox[0],20)

    def test_unknown_symbol_is_not_guessed(self):
        page, table = fixture()
        page.curves[-1]['non_stroking_color'] = 'unknown'
        self.assertIsNone(recover(page,table))

    def test_missing_symbol_is_not_not_covered(self):
        page, table = fixture()
        page.curves.pop()
        self.assertIsNone(recover(page,table))

    def test_same_color_different_geometry_is_rejected(self):
        page, table = fixture()
        page.curves[-1]['path'].append(('l',(150,155)))
        self.assertIsNone(recover(page,table))

    def test_no_legend_no_recovery(self):
        page, table = fixture()
        page.curves = page.curves[3:]
        self.assertIsNone(recover(page,table))

    def test_degenerate_table_has_no_confidence(self):
        self.assertEqual(estimate_table_confidence([['a','b']], (0,0,200,200)),0)

    def test_warning_survives_prompt_rendering(self):
        page = PageRepresentation(page_num=1,width=100,height=100,warnings=['visual review required'])
        self.assertIn('visual review required',render_page(page))

    def test_rejected_table_does_not_mask_text(self):
        page = NS(page_number=1,width=300,height=300,
                  find_tables=lambda **kw:[NS(bbox=(10,10,20,200),extract=lambda:[['fragment']])],
                  extract_words=lambda **kw:[dict(text='Original intact sentence',x0=10,x1=19,top=20,bottom=30)])
        result = PDFIngestor(camelot_enabled=False)._parse_page(Path('synthetic.pdf'),page)
        self.assertEqual(result.blocks[0].content,'Original intact sentence')
        self.assertTrue(result.warnings)


class LocalDevelopmentRegression(unittest.TestCase):
    root = Path(__file__).resolve().parents[1] / 'data/car_insurance/development'

    def parse_page(self, relative, number):
        import pdfplumber
        path = self.root / relative
        if not path.exists():
            self.skipTest('Optional local development PDF is not distributed in Git')
        with pdfplumber.open(path) as doc:
            return PDFIngestor(camelot_enabled=False)._parse_page(path,doc.pages[number-1])

    def test_youi_development_page_four(self):
        page = self.parse_page('youi/pds/youi_car_2026.pdf',4)
        tables = [b for b in page.blocks if b.type=='table']
        self.assertEqual(len(tables),1)
        self.assertEqual(len(tables[0].rows),15)
        self.assertIn(['Fire','Covered','Covered','Not covered'],tables[0].rows)
        self.assertIn(['Hire Car for Other Insured Events','Optional cover','Not covered','Not covered'],tables[0].rows)

    def test_qbe_development_callout(self):
        page = self.parse_page('qbe/pds/qbe_comprehensive_1123.pdf',10)
        text = ' '.join(b.content for b in page.blocks if b.type=='text')
        self.assertIn('any other drivers who use your car',text)
        self.assertIn('reduce or refuse to pay a claim',text)
        self.assertFalse(any(b.type=='table' for b in page.blocks))

    def test_youi_continuation_page(self):
        page = self.parse_page('youi/pds/youi_car_2026.pdf',5)
        table = next(b for b in page.blocks if b.type=='table')
        self.assertEqual(len(table.rows),9)
        self.assertIn(['Uninsured Third Party','Not required','Covered','Covered'],table.rows)
        self.assertIn(['Business Items','Optional cover','Optional cover','Not covered'],table.rows)

    def test_aami_contents_order(self):
        page = self.parse_page('aami/pds/aami_comprehensive_2020.pdf',7)
        text = ' '.join(b.content for b in page.blocks if b.type=='text')
        self.assertLess(text.index('Contents'),text.index('3. Things'))

    def test_aami_sidebar_labels_and_body(self):
        page = self.parse_page('aami/pds/aami_comprehensive_2020.pdf',26)
        blocks = [b for b in page.blocks if b.type=='text']
        sections = [b.content for b in blocks if b.source_engine=='pdfplumber:label-region']
        self.assertEqual(len(sections),3)
        self.assertTrue(sections[0].startswith('We cover:\n'))
        self.assertIn('caused by an incident',sections[0])
        self.assertTrue(sections[1].startswith("We don't cover:\n"))
        self.assertIn('pages 17 to 24',sections[1])
        self.assertTrue(sections[2].startswith('Limit:\n'))
        self.assertNotIn('For examples',sections[2])
        self.assertFalse(any(b.content=='We' for b in blocks))

    def test_aami_next_benefit_heading_stays_outside_limit(self):
        page = self.parse_page('aami/pds/aami_comprehensive_2020.pdf',37)
        sections = [b.content for b in page.blocks if b.type=='text' and b.source_engine=='pdfplumber:label-region']
        self.assertFalse(any('Trailer cover' in s for s in sections))
        self.assertFalse(any(s.endswith('\n37') for s in sections))
        self.assertTrue(any(b.type=='text' and 'Trailer cover' in b.content for b in page.blocks))

    def test_aami_third_party_summary_matrix(self):
        page = self.parse_page('aami/pds/aami_third_party_2020.pdf',5)
        tables = [b for b in page.blocks if b.type=='table']
        self.assertEqual(len(tables),1)
        table=tables[0]
        self.assertEqual(table.headers,['What we cover','Fire, Theft & Third Party Property Damage cover','Third Party Property Damage cover','Page'])
        self.assertEqual(len(table.rows),10)
        self.assertEqual([r[1:] for r in table.rows[1:]],[
            ['Covered','Not covered','28'],['Limited cover','Limited cover','29'],
            ['Covered','Limited cover','31'],['Covered','Not covered','31'],
            ['Covered','Not covered','32'],['Covered','Limited cover','32'],
            ['Covered','Covered','33'],['Covered','Covered','33'],['Covered','Covered','34']])
        self.assertEqual(table.rows[1][0],'Hire car after theft up to 21 days')
        self.assertIn('come with your policy',table.rows[0][0])
        self.assertFalse(page.warnings)
        self.assertFalse(any(b.type=='text' and b.content=='cover' for b in page.blocks))

    def test_comprehensive_summary_pairs_and_groups(self):
        page=self.parse_page('aami/pds/aami_comprehensive_2020.pdf',5)
        table=next(b for b in page.blocks if b.type=='table')
        self.assertEqual(table.headers,['What we cover','Page'])
        self.assertEqual(len(table.rows),17)
        self.assertIn(['Hire car after theft up to 21 days','33'],table.rows)
        self.assertIn(['Hire car after an event for unlimited days','42'],table.rows)
        self.assertEqual([r[1] for r in table.rows if r[1]],['26','27','30','31','32','33','34','36','37','37','38','39','42','42','43'])
        self.assertTrue(any('pay extra' in r[0] and not r[1] for r in table.rows))

    def test_actual_continuation_heading_links_without_copying_limits(self):
        from src.PDFingestor.quality import annotate_continuations
        pages=[self.parse_page('aami/pds/aami_third_party_2020.pdf',n) for n in [33,34]]
        annotate_continuations(pages)
        link=pages[1].continuations[0]
        self.assertEqual(link['title'],'Substitute car')
        self.assertEqual(link['previous_page'],33)
        self.assertEqual(link['status'],'linked_explicit_title')

    def test_daily_limit_and_day_count_remain_separate_source_evidence(self):
        from src.PDFingestor.models import ParsedPDF
        from src.PDFingestor.quality import audit
        pages=[self.parse_page('aami/pds/aami_comprehensive_2020.pdf',n) for n in [33,42]]
        report=audit(ParsedPDF(pdf_id='fixture',pdf_hash='test',source_path='fixture',pages=pages))
        self.assertTrue(any('$90' in x['cues'] and 'per day' in x['cues'] for x in report['semantic_review']))
        self.assertTrue(any('21 days' in x['cues'] for x in report['semantic_review']))


class QualityGateTest(unittest.TestCase):
    def test_trial_review_accepts_only_exact_representation(self):
        import json
        import tempfile
        from src.PDFingestor.quality import require_quality, review_fingerprint, ParserQualityError
        doc=self.document('warning retained')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'review.json'
            path.write_text(json.dumps(dict(decision='accepted_for_schema_trial',reviewer='test user',reviewed_at='2026-10-03',document_fingerprints=[review_fingerprint(doc)])),encoding='utf-8')
            require_quality([doc],review_path=path)
            self.assertEqual(doc.pages[0].warnings,['warning retained'])
            doc.pages[0].blocks[0].content='Changed after review'
            with self.assertRaises(ParserQualityError):
                require_quality([doc],review_path=path)

    def document(self, warning=None, content='Policy text'):
        from src.PDFingestor.models import ParsedPDF, TextBlock
        return ParsedPDF(pdf_id='sample.pdf',pdf_hash='abc',source_path='sample.pdf',pages=[
            PageRepresentation(page_num=1,width=300,height=400,warnings=[warning] if warning else [],
                blocks=[TextBlock(block_id='b',content=content,top=1)])])

    def test_warning_blocks_prompt_route_but_preview_is_available(self):
        from unittest.mock import patch
        from src.PDFingestor.adapter import render_pdf_paths_for_prompt
        from src.PDFingestor.quality import ParserQualityError
        doc=self.document('unverified table')
        with patch('src.PDFingestor.adapter.ingest_pdfs',return_value=(doc,)):
            with self.assertRaises(ParserQualityError):
                render_pdf_paths_for_prompt(['sample.pdf'],enforce_quality=True)
        self.assertIn('unverified table',doc.to_llm_markdown())

    def test_unresolved_or_orphan_content_blocks_even_without_warnings(self):
        from src.PDFingestor.quality import require_quality, ParserQualityError
        for text in ['UNRESOLVED','We']:
            with self.subTest(text=text), self.assertRaises(ParserQualityError):
                require_quality([self.document(content=text)])

    def test_clean_document_has_no_detected_blockers_not_approval(self):
        from src.PDFingestor.quality import audit, require_quality
        doc=self.document()
        require_quality([doc])
        self.assertEqual(audit(doc)['status'],'no_detected_blockers_not_human_approved')

    def test_unmatched_continuation_blocks(self):
        from src.PDFingestor.quality import annotate_continuations, require_quality, ParserQualityError
        doc=self.document(content='Unseen benefit (cont’d)')
        annotate_continuations(doc.pages)
        self.assertEqual(doc.pages[0].continuations[0]['status'],'unresolved')
        with self.assertRaises(ParserQualityError):
            require_quality([doc])

    def test_car_discovery_and_extraction_block_before_provider_call(self):
        import tempfile
        from unittest.mock import patch
        from tests.test_car_insurance import RecordingProvider, synthetic_schema, synthetic_products
        from src.schema.discovery import SchemaDiscovery
        from src.schema_application.extractor import SchemaExtractor
        from src.verticals.manifest import resolve_manifest
        from src.PDFingestor.quality import ParserQualityError
        provider=RecordingProvider(synthetic_products())
        manifest=resolve_manifest(vertical='car_insurance')
        with tempfile.TemporaryDirectory() as tmp, patch('src.PDFingestor.adapter.ingest_pdfs',return_value=(self.document('bad table'),)):
            pdf=Path(tmp)/'sample.pdf'
            pdf.touch()
            discovery=SchemaDiscovery(manifest=manifest,provider=provider,parsed_markdown_dir=tmp)
            extractor=SchemaExtractor(schema_data=synthetic_schema(),manifest=manifest,provider=provider,parsed_markdown_dir=tmp)
            with self.assertRaises(ParserQualityError):
                discovery.discover(['sample.pdf'])
            with self.assertRaises(ParserQualityError):
                extractor.extract_one(pdf)
        self.assertFalse(provider.requests)


class SidebarGeometryTest(unittest.TestCase):
    def test_missing_graphic_is_unresolved_not_excluded(self):
        from src.PDFingestor.summary_tables import glyph_status
        self.assertEqual(glyph_status(NS(curves=[],lines=[]),(0,0,100,100)),'UNRESOLVED')

    def test_circle_without_tick_or_cross_is_unresolved(self):
        from src.PDFingestor.summary_tables import glyph_status
        self.assertEqual(glyph_status(NS(curves=[circle(10,10,12,'red')],lines=[]),(0,0,100,100)),'UNRESOLVED')

    def test_stacked_label_and_following_full_width_text(self):
        from src.PDFingestor.labelled_sections import extract
        from src.PDFingestor.parser import group_words_into_lines
        def word(text,x,y):
            return dict(text=text,x0=x,x1=x+20,top=y,bottom=y+10)
        words = [word('We',10,10),word('cover',10,24),word('Covered body',80,10),word('Continued',80,24),word('New paragraph',10,60)]
        blocks, remaining = extract(words,1,300,group_words_into_lines)
        self.assertEqual(len(blocks),1)
        self.assertIn('Covered body Continued',blocks[0].content)
        self.assertEqual([w['text'] for w in remaining],['New paragraph'])
        self.assertEqual(len(words),5)  # source word list is not mutated

    def test_inline_prose_is_not_a_sidebar(self):
        from src.PDFingestor.labelled_sections import extract
        from src.PDFingestor.parser import group_words_into_lines
        words = [dict(text=t,x0=x,x1=x+15,top=10,bottom=20) for t,x in [('We',10),('cover',30),('cars',60)]]
        blocks, remaining = extract(words,1,300,group_words_into_lines)
        self.assertFalse(blocks)
        self.assertEqual(remaining,words)


if __name__ == '__main__':
    unittest.main()
