"""Regression tests for actual PDF metrics, complete page review and stable calculation sheets."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pymupdf
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import print_check
import print_layout
import office_export
import workflow_state

ROOT = Path(__file__).resolve().parents[2]


class PrintingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ['LAND_AUDIT_TEST_DIR'])
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def fixture(self, fontsize=12, width=595.28, height=841.89, pages=1):
        source=self.root/'candidate.docx';source.write_bytes(b'synthetic source bytes')
        pdf=self.root/'output.pdf'
        with pymupdf.open() as doc:
            for _ in range(pages):
                page=doc.new_page(width=width,height=height)
                page.insert_text((40,70),'Synthetic registered output',fontsize=fontsize)
            doc.save(pdf)
        export=self.root/'export.json'
        data={'schema_version':2,'batch':'synthetic','files':{'opinion':str(source)},'tool':'synthetic',
              'recalculated':True,'source_hashes':{str(source):print_layout.sha(source)},
              'exports':[{'kind':'opinion','source':str(source),'pdf':str(pdf),
                          'pdf_sha256':print_layout.sha(pdf),'orientation':'portrait',
                          'font_pt':12,'headers':[],'expected_pages':[]}]}
        export.write_text(json.dumps(data),encoding='utf8')
        report=print_check.inspect(export,self.root/'images')
        for page in report['page_evidence']:
            page['review']={'reviewer':'synthetic fixture','checks':{k:True for k in print_check.VISUAL_CHECKS}}
        return report

    def test_real_a4_and_review_pass(self):
        findings,_=print_check.validate_report(self.fixture())
        self.assertEqual(findings,[])

    def test_small_actual_font_is_rejected_even_when_review_checked(self):
        findings,_=print_check.validate_report(self.fixture(fontsize=9))
        self.assertTrue(any(f['status']=='fail' and 'font' in f['message'] for f in findings))

    def test_wrong_physical_paper_is_rejected(self):
        findings,_=print_check.validate_report(self.fixture(width=700))
        self.assertTrue(any('A4' in f['message'] for f in findings))

    def test_invalid_pdf_cannot_pass_with_true_flags(self):
        report=self.fixture();entry=report['exports'][0]
        Path(entry['pdf']).write_bytes(b'%PDF-1.4\nnot a real PDF')
        entry['pdf_sha256']=print_layout.sha(entry['pdf'])
        export=print_check.load(report['export_file']);export['exports']=report['exports']
        Path(report['export_file']).write_text(json.dumps(export),encoding='utf8')
        report['export_sha256']=print_layout.sha(report['export_file'])
        report.update(pages_checked=True,no_blank_pages=True,no_orphan_total_or_date=True)
        findings,_=print_check.validate_report(report)
        self.assertTrue(any('parsed' in f['message'] for f in findings))

    def test_missing_second_page_review_is_unverified(self):
        report=self.fixture(pages=2);report['page_evidence'].pop()
        findings,_=print_check.validate_report(report)
        self.assertTrue(any(f['status']=='unverified' for f in findings))

    def test_changed_page_image_is_rejected(self):
        report=self.fixture();Path(report['page_evidence'][0]['image']).write_bytes(b'changed')
        findings,_=print_check.validate_report(report)
        self.assertTrue(any('image' in f['message'] for f in findings))

    def test_legacy_print_report_needs_new_export(self):
        findings,_=print_check.validate_report({'pages_checked':True,'recalculated':True})
        self.assertEqual(findings[0]['status'],'unverified')

    def test_changed_delivered_bytes_cannot_reuse_review(self):
        report=self.fixture();input=self.root/'report.json'
        input.write_text(json.dumps(report),encoding='utf8')
        delivered=self.root/'delivered.docx';delivered.write_bytes(b'different')
        with self.assertRaises(ValueError):
            print_check.rebind(input,{'opinion':str(delivered)},self.root/'rebound.json')

    def test_land_print_checks_the_group_on_each_page(self):
        report=self.fixture()
        entry=report['exports'][0]
        entry.update(kind='audit',sheet='补偿审核明细表',land_layout=True,font_pt=None,
                     title='Synthetic registered output',
                     expected_pages=[{'rows':[], 'total':False, 'headers':['Expected section']}])
        _, findings=print_check.analyze(entry)
        self.assertTrue(any('Expected section' in f['message'] for f in findings))

    def test_land_print_generation_preserves_adopted_values_and_input(self):
        source=self.root/'land.xlsx'
        w=load_workbook(ROOT/'templates/土地_青苗竹果木.xlsx')
        d=w['补偿审核明细表']
        d['A6']='001';d['B6']='合成人员';d['W6']=123.45;d['BE6']=None;d['AL6']=0
        d.row_dimensions[6].height=16
        d.row_dimensions[7].height=40
        w['补偿审核汇总表']['I6']='=0'
        w.save(source);w.close();before=print_layout.sha(source)
        output=self.root/'print.xlsx'
        result=print_layout.build(source,output,{'template_category':'land_tree','stage':'INITIAL',
            'categories':['land','tree'],'project_name':'合成项目','batch':'第一批','compensation_name':'合成补偿'})
        after=load_workbook(output)
        self.assertEqual(after['补偿审核明细表']['W6'].value,123.45)
        self.assertIsNone(after['补偿审核明细表']['BE6'].value)
        self.assertEqual(after['补偿审核明细表']['AL6'].value,0)
        self.assertEqual(after.sheetnames,['补偿审核汇总表','补偿审核明细表'])
        summary,detail=after.worksheets
        self.assertIs(summary.sheet_view.showZeros,False)
        self.assertIs(detail.sheet_view.showZeros,False)
        self.assertEqual(summary['I6'].value,'=0')
        self.assertEqual(detail.row_dimensions[6].height,29.25)
        self.assertEqual(detail.row_dimensions[7].height,40)
        for sheet in after:
            self.assertEqual(sheet['A1'].font.sz,22)
            self.assertTrue(sheet['A1'].font.bold)
            self.assertEqual(sheet.oddHeader.center.text,sheet['A1'].value)
            self.assertEqual(sheet.oddHeader.center.size,22)
            self.assertEqual(sheet.sheet_view.view,'pageBreakPreview')
        self.assertEqual(summary['A1'].value,'合成项目第一批合成补偿审核汇总表')
        self.assertTrue(summary.sheet_properties.pageSetUpPr.fitToPage)
        self.assertEqual((summary.page_setup.fitToWidth,summary.page_setup.fitToHeight),(1,1))
        self.assertEqual([br.id for br in detail.col_breaks.brk],[46])
        self.assertEqual(print_layout.sha(source),before)
        self.assertTrue(all(after['补偿审核明细表'].column_dimensions[c].hidden for c in ['BL','BM','BN','BO','BP']))
        self.assertEqual([s['sheet'] for s in result['sheets']],after.sheetnames)
        self.assertEqual([s['caption'] for s in result['sheets'][-1]['sections']],
                         ['征地及留用地补偿','竹果木补偿'])
        after.close()
        with self.assertRaises(FileExistsError):print_layout.build(source,output,result['config'])

    def test_materials_visible_rows_follow_stage_and_category(self):
        for stage,expected in [('INITIAL',list(range(29,36))),('PAYMENT',list(range(67,75)))]:
            output=self.root/(stage+'.xlsx')
            result=print_layout.build(ROOT/'templates/资料符合性核查表.xlsx',output,
                {'template_category':'materials','stage':stage,'categories':['grave'],
                 'project_name':'合成项目全称','batch':'第一批','compensation_name':'第一批坟墓迁移补偿'})
            self.assertEqual(result['sheets'][0]['source_rows'],expected)
            w=load_workbook(output)
            self.assertIsNone(w['资料符合性核查表']['A2'].value)
            self.assertTrue(w['资料符合性核查表'].row_dimensions[7].hidden)
            self.assertEqual(w['打印_资料符合性']['A2'].value,'项目名称：合成项目全称')
            w.close()

    def test_materials_preserves_source_wording_and_fits_regular_names(self):
        w=load_workbook(ROOT/'templates/资料符合性核查表.xlsx')
        s=w.worksheets[0]
        s.title='Sheet1'
        s['A2']='征地拆迁资料符合性核查表'
        s['F5']='测试说明原文\n/为选择提供'
        s['E14']='√'
        src=self.root/'form.xlsx'
        w.save(src)
        w.close()
        original=print_layout.sha(src)
        out=self.root/'materials.xlsx'
        result=print_layout.build(src,out,{
            'template_category':'materials','stage':'INITIAL','categories':['land','crop','tree'],
            'project_name':'合成项目','batch':'第一批','compensation_name':'土地补偿'})
        self.assertEqual(print_layout.sha(src),original)
        spec=result['sheets'][0]
        self.assertEqual(spec['title'],'征地拆迁资料符合性核查表')
        self.assertEqual(print_layout.line_count('征收竹、果树、林木现场清点数据原始登记表扫描件',
                                                spec['widths_mm'][2]),1)
        self.assertEqual(office_export.candidate_name('materials','.xlsx'),'征地拆迁资料符合性核查表（改）.xlsx')
        filled=load_workbook(out)
        self.addCleanup(filled.close)
        self.assertEqual(filled['Sheet1']['A2'].value,s['A2'].value)
        self.assertEqual(filled['Sheet1']['F5'].value,s['F5'].value)
        self.assertEqual(filled['Sheet1']['E14'].value,'√')
        self.assertEqual(filled[spec['sheet']]['F4'].value,'说明\n'+s['F5'].value)

    def test_grave_blank_row_keeps_formula_coordinates(self):
        result=print_layout.build(ROOT/'templates/坟墓迁移.xlsx',self.root/'grave.xlsx',
            {'template_category':'grave','stage':'INITIAL','categories':['grave'],
             'project_name':'合成项目','batch':'第一批','compensation_name':'坟墓补偿'})
        w=load_workbook(self.root/'grave.xlsx')
        self.assertTrue(w['坟墓审核明细表'].row_dimensions[2].hidden)
        self.assertIn('J4*K4',w['坟墓审核明细表']['L4'].value)
        for spec in result['sheets']:
            self.assertEqual(spec['page_rows'][-1][1],spec['total_row'])
        w.close()

    def test_full_land_print_has_three_sections_in_one_detail_sheet(self):
        source=ROOT/'templates/土地_青苗竹果木.xlsx'
        result=print_layout.build(source,self.root/'full.xlsx',
            {'template_category':'land_tree','stage':'INITIAL','categories':['land','crop','pond','tree'],
             'project_name':'合成项目','batch':'第一批','compensation_name':'土地及青苗补偿'})
        detail=result['sheets'][1]
        self.assertEqual([(s['first'],s['last']) for s in detail['sections']],[(1,29),(30,46),(47,63)])
        self.assertEqual(detail['sections'][-1]['last'],63)
        self.assertIn('补偿金额合计',detail['sections'][-1]['headers'])

    def test_pond_only_includes_combined_subtotal(self):
        result=print_layout.build(ROOT/'templates/土地_青苗鱼塘.xlsx',self.root/'pond.xlsx',
            {'template_category':'land_pond','stage':'INITIAL','categories':['land','pond'],
             'project_name':'合成项目','batch':'第一批','compensation_name':'鱼塘补偿'})
        detail=result['sheets'][1]
        self.assertEqual([s['caption'] for s in detail['sections']],['征地及留用地补偿','青苗及鱼塘搬迁补偿'])
        self.assertIn('补偿金额合计',detail['sections'][-1]['headers'])
        w=load_workbook(self.root/'pond.xlsx')
        self.assertTrue(w['补偿审核明细表'].column_dimensions['AD'].hidden)
        self.assertFalse(w['补偿审核明细表'].column_dimensions['AH'].hidden)
        self.assertEqual([s.title for s in w if s.sheet_view.tabSelected],[result['sheets'][0]['sheet']])
        w.close()
