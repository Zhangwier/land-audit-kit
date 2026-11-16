"""Regression scenarios for handoff, human edits and incomplete evidence."""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import zipfile
from xml.etree import ElementTree as ET

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import workflow_state as state
import check_outputs as check
import print_check
import pymupdf
from openpyxl import Workbook
from docx import Document

BRIDGE = Path(os.environ['LAND_AUDIT_GEMINI_BRIDGE']) if os.environ.get('LAND_AUDIT_GEMINI_BRIDGE') else None
gemini = None
if BRIDGE and BRIDGE.is_file():
    spec = importlib.util.spec_from_file_location('audit_gemini_task', BRIDGE)
    gemini = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gemini)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


class TemporaryCase(unittest.TestCase):
    def setUp(self):
        parent = os.environ.get('LAND_AUDIT_TEST_DIR')
        if not parent:
            raise RuntimeError('Set LAND_AUDIT_TEST_DIR to this session scratch directory')
        self.tmp = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()


class WorkflowTests(TemporaryCase):
    def setUp(self):
        super().setUp()
        self.run = self.root / 'run'
        self.source = self.root / 'source.txt'
        self.source.write_text('original', encoding='utf-8')
        self.info = self.root / '批次信息.md'
        self.info.write_text('演示批次', encoding='utf-8')
        self.outputs = {k: str(self.root / ('result_' + k)) for k in ('audit', 'materials', 'opinion')}
        self.config = {'package': '测试包', 'batches': [{'id': 'A', 'name': '批A', 'info': str(self.info),
            'inputs': [str(self.source)], 'outputs': self.outputs, 'user_records': False}]}
        state.init(self.run, self.config)

    def event(self, role, status, task='task', **values):
        return state.record(self.run, {'batch': 'A', 'role': role, 'task_id': task, 'status': status, **values})

    def prepare(self):
        self.event('prepare', 'running')
        self.event('prepare', 'done', artifacts=[str(self.info)])

    def publish(self, role, kind, content='automatic'):
        candidate = self.root / ('candidate_' + kind)
        candidate.write_text(content, encoding='utf-8')
        return state.guard(self.run, 'A', self.outputs[kind], candidate, role)

    def test_init_is_idempotent_and_preserves_state(self):
        self.prepare()
        self.assertEqual(state.init(self.run, self.config)['batches']['A']['roles']['prepare']['status'], 'done')

    def test_cannot_reset_changed_input_version(self):
        self.source.write_text('changed')
        with self.assertRaises(ValueError):
            state.init(self.run, self.config)

    def test_roles_wait_for_dependencies(self):
        with self.assertRaises(ValueError):
            self.event('table', 'running')
        self.prepare()
        self.event('table', 'running')

    def test_writer_starts_before_verifier(self):
        self.prepare()
        self.event('gemini', 'running', task='browser')
        result = self.root / 'gemini.md'
        result.write_text('result')
        self.event('gemini', 'done', task='browser', artifacts=[str(result)])
        self.event('writer', 'running', task='writer')
        self.publish('writer', 'opinion')
        batch = self.event('writer', 'done', task='writer', artifacts=[self.outputs['opinion']])
        self.assertEqual(batch['summary'], '初稿已交付，核实未完成')
        self.event('verify', 'running', task='verifier')

    def test_user_records_allow_draft_without_gemini(self):
        config = copy.deepcopy(self.config)
        config['batches'][0]['user_records'] = True
        other = self.root / 'other_run'
        state.init(other, config)
        state.record(other, {'batch':'A','role':'prepare','task_id':'p','status':'running'})
        state.record(other, {'batch':'A','role':'prepare','task_id':'p','status':'done','artifacts':[str(self.info)]})
        state.record(other, {'batch':'A','role':'writer','task_id':'w','status':'running'})

    def test_historical_file_never_claimed_or_overwritten(self):
        Path(self.outputs['opinion']).write_text('human final')
        original = Path(self.outputs['opinion']).read_bytes()
        result = state.guard(self.run, 'A', self.outputs['opinion'])
        self.assertFalse(result['allowed'])
        self.assertEqual(Path(self.outputs['opinion']).read_bytes(), original)

    def test_historical_file_moved_after_registration_is_not_recreated(self):
        target=Path(self.outputs['opinion'])
        target.write_text('historical')
        run=self.root/'historical_run'
        state.init(run,self.config)
        target.rename(self.root/'human_moved_file')
        result=state.guard(run,'A',target)
        self.assertFalse(result['allowed'])
        self.assertFalse(target.exists())

    def test_partial_batch_keeps_existing_file_and_publishes_only_missing(self):
        historical=Path(self.outputs['audit'])
        historical.write_text('human audit')
        run=self.root/'partial_run'
        state.init(run,self.config)
        def record(role,status,**extra):
            return state.record(run,{'batch':'A','role':role,'status':status,'task_id':'partial',**extra})
        record('prepare','running')
        record('prepare','done',artifacts=[str(self.info)])
        record('table','running')
        candidate=self.root/'new_materials'
        candidate.write_text('new')
        state.guard(run,'A',self.outputs['materials'],candidate,'table')
        record('table','done',artifacts=[str(historical),self.outputs['materials']])
        self.assertEqual(historical.read_text(),'human audit')

    def test_publish_then_human_edit_is_preserved(self):
        self.prepare()
        self.event('table', 'running')
        self.assertTrue(self.publish('table', 'audit')['published'])
        Path(self.outputs['audit']).write_text('human edit')
        result = self.publish('table', 'audit', 'must not overwrite')
        self.assertFalse(result['allowed'])
        self.assertEqual(Path(self.outputs['audit']).read_text(), 'human edit')
        self.assertTrue(state.status(self.run)['batches'][0]['protected_changes'])

    def test_automatic_revision_has_backup(self):
        self.prepare()
        self.event('table', 'running')
        self.publish('table', 'audit', 'version1')
        self.publish('table', 'audit', 'version2')
        self.assertEqual(Path(self.outputs['audit']).read_text(), 'version2')
        self.assertEqual(next((self.run/'备份').iterdir()).read_text(), 'version1')

    def test_manual_deletion_is_not_recreated(self):
        self.prepare()
        self.event('table', 'running')
        self.publish('table', 'audit')
        Path(self.outputs['audit']).unlink()
        self.assertFalse(self.publish('table', 'audit')['allowed'])
        self.assertFalse(Path(self.outputs['audit']).exists())

    def test_owner_lock_is_respected(self):
        path = Path(self.outputs['audit'])
        (path.parent / ('~$' + path.name)).write_text('lock')
        self.assertFalse(state.guard(self.run, 'A', path)['allowed'])

    def test_partial_delivery_and_busy_file_keep_table_unfinished(self):
        self.prepare();self.event('table','running')
        self.publish('table','audit')
        with patch.object(state,'exclusive_file',side_effect=OSError('synthetic busy file')):
            result=self.publish('table','materials')
        self.assertFalse(result['published'])
        self.assertEqual(state.status(self.run)['batches'][0]['summary'],'候选稿待交付')
        with self.assertRaises(ValueError):
            self.event('table','done',artifacts=[self.outputs['audit']])
        self.assertTrue(self.publish('table','materials')['readback_verified'])
        self.event('table','done',artifacts=[self.outputs['audit'],self.outputs['materials']])

    def test_missing_current_readback_receipt_blocks_done(self):
        self.prepare();self.event('table','running')
        self.publish('table','audit');self.publish('table','materials')
        path=self.run/'运行记录.json';data=state.read_json(path)
        data['batches']['A']['published'][self.outputs['audit']].pop('readback_sha256')
        state.atomic_json(path,data)
        with self.assertRaises(ValueError):
            self.event('table','done',artifacts=[self.outputs['audit'],self.outputs['materials']])

    def test_windows_exclusive_handle_prevents_racing_write(self):
        if os.name != 'nt':
            self.skipTest('Windows sharing semantics')
        target = self.root / 'locked'
        target.write_text('old')
        with state.exclusive_file(target):
            with self.assertRaises(OSError):
                target.write_text('unexpected')
        self.assertEqual(target.read_text(), 'old')

    def test_wrong_role_cannot_publish_word(self):
        self.prepare()
        self.event('table', 'running')
        with self.assertRaises(ValueError):
            self.publish('table', 'opinion')

    def test_done_requires_actual_published_files(self):
        self.prepare()
        self.event('table', 'running')
        with self.assertRaises(ValueError):
            self.event('table', 'done', artifacts=[str(self.info)])

    def test_pending_items_block_done(self):
        self.event('prepare', 'running')
        with self.assertRaises(ValueError):
            self.event('prepare', 'done', artifacts=[str(self.info)], pending=['missing'])

    def test_cancel_before_replacement_including_waiting_agent(self):
        self.event('prepare', 'running', task='old')
        self.event('prepare', 'waiting_input', task='old', pending=['wait'])
        with self.assertRaises(ValueError):
            self.event('prepare', 'running', task='new')
        self.event('prepare', 'canceled', task='old')
        self.event('prepare', 'running', task='new')

    def test_input_change_only_blocks_affected_batch(self):
        source2 = self.root / 'source2'
        source2.write_text('source2')
        config = copy.deepcopy(self.config)
        config['batches'].append({'id':'B','name':'批B','info':str(self.info),'inputs':[str(source2)],
            'outputs':{k:str(self.root/('B_'+k)) for k in self.outputs}})
        run = self.root/'two_batches'
        state.init(run, config)
        self.source.write_text('modified')
        snapshot = state.status(run)
        self.assertFalse(snapshot['batches'][0]['ready'])
        self.assertIn('prepare',snapshot['batches'][1]['ready'])
        state.record(run, {'batch':'B','role':'prepare','task_id':'B','status':'running'})

    def test_process_updates_do_not_lose_events(self):
        inputs = []
        for i in range(8):
            event = self.root/f'event{i}.json'
            write_json(event, {'batch':'A','role':'prepare','status':'pending','pending':[str(i)]})
            inputs.append(event)
        def call(path):
            return subprocess.run([sys.executable,str(TOOLS/'workflow_state.py'),'record','--run-dir',str(self.run),'--input',str(path)], capture_output=True).returncode
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(call,inputs)), [0]*8)
        self.assertEqual(len(state.read_json(self.run/'运行记录.json')['history']),8)


class CheckTests(TemporaryCase):
    def setUp(self):
        super().setUp()
        self.batch = '合成高速演示段第一批土地'
        self.files = {k:str(self.root/(self.batch+v)) for k,v in {
            'audit':'补偿审核表.xlsx','materials':'符合性表.xlsx','opinion':'初审意见.docx'}.items()}
        book = Workbook()
        summary = book.active
        summary.title = '汇总'
        summary['A1'] = self.batch
        summary['B3'] = 100
        summary['C3'] = '=SUM(明细!D3)'
        summary['D3'] = 0
        for c in ('B3','C3','D3'):
            summary[c].number_format='0.00'
        detail = book.create_sheet('明细')
        detail['B3']=10
        detail['C3']=10
        detail['D3']='=ROUND(B3*C3,2)'
        detail['E3']=100
        detail['D3'].number_format='0.00'
        book.save(self.files['audit'])
        self.cache(100)
        materials = Workbook()
        materials.active.title = '符合性'
        materials.active['A1']=self.batch
        materials.active['E4']='√'
        materials.save(self.files['materials'])
        doc = Document()
        doc.add_paragraph(self.batch+'补偿初审意见')
        doc.add_paragraph('1.本批土地测绘拼图（红线图）未加盖测绘公司公章，请补盖。')
        doc.save(self.files['opinion'])
        self.source = self.root/'原始表.xlsx'
        source=Workbook()
        source.active['A2']='地号A'
        source.save(self.source)
        exports=[]
        for kind, source_path in self.files.items():
            pdf=self.root/(kind+'.pdf')
            with pymupdf.open() as document:
                page=document.new_page(width=595.28,height=841.89)
                page.insert_text((40,70),'Registered synthetic output',fontsize=12)
                document.save(pdf)
            exports.append({'kind':kind,'source':source_path,'pdf':str(pdf),'pdf_sha256':check.sha(pdf),
                            'orientation':'portrait','font_pt':12,'headers':[],'expected_pages':[]})
        export=self.root/'export.json'
        write_json(export,{'schema_version':2,'batch':self.batch,'files':self.files,'tool':'synthetic fixture',
                           'source_hashes':{p:check.sha(p) for p in self.files.values()},'recalculated':True,'exports':exports})
        inspection=print_check.inspect(export,self.root/'pages')
        for page in inspection['page_evidence']:
            page['review']={'reviewer':'synthetic test reviewer','checks':{k:True for k in print_check.VISUAL_CHECKS}}
        self.print_report = self.root/'print.json'
        write_json(self.print_report,inspection)
        self.config={'batch':self.batch,'files':self.files,'stage':'initial',
            'money':[{'key':key,'value':value,'locations':[{'file':'audit','sheet':'汇总','cell':cell}]} for key,value,cell in [('submitted','100.00','B3'),('audited','100.00','C3'),('delta','0.00','D3')]],
            'formulas':[{'file':'audit','sheet':'明细','ranges':['D3:D3'],'submitted_columns':['E']}],
            'materials':[{'name':'红线图','carrier':'scan','content_status':'confirmed','electronic':{'file':'materials','sheet':'符合性','cell':'D4'},'scan':{'file':'materials','sheet':'符合性','cell':'E4'}}],
            'coverage':{'expected_objects':['地号A'],'objects':[{'id':'地号A','status':'confirmed','refs':[{'file':str(self.source),'cell':'Sheet!A2'}]}],'expected_problems':[],'problems':[]},
            'opinion_sources':[{'paragraph':2,'source':'project'}],'print_report':str(self.print_report)}

    def cache(self, value):
        path=Path(self.files['audit'])
        with zipfile.ZipFile(path) as z:
            parts={name:z.read(name) for name in z.namelist()}
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        for name in ['xl/worksheets/sheet1.xml','xl/worksheets/sheet2.xml']:
            root=ET.fromstring(parts[name])
            for cell in root.findall('.//s:c',ns):
                if cell.find('s:f',ns) is not None:
                    v=cell.find('s:v',ns)
                    v.text=None if value is None else str(value)
            parts[name]=ET.tostring(root,encoding='utf-8')
        with zipfile.ZipFile(path,'w') as z:
            for name,data in parts.items():z.writestr(name,data)

    def result(self):
        return check.Checker(self.config).run()

    def test_complete_registered_evidence_passes(self):
        self.assertEqual(self.result()['overall'],'pass')

    def test_different_project_header_is_rejected(self):
        book = Workbook()
        book.active['A1'] = '另一个项目演示段第一批土地'
        book.active['E4'] = '√'
        book.save(self.files['materials'])
        result = self.result()
        self.assertTrue(any(r['check'] == 'names' and r['status'] == 'fail' for r in result['results']))

    def test_different_batch_header_is_rejected(self):
        book = Workbook()
        book.active['A1'] = '合成高速演示段第二批土地'
        book.active['E4'] = '√'
        book.save(self.files['materials'])
        result = self.result()
        self.assertTrue(any(r['check'] == 'names' and r['status'] == 'fail' for r in result['results']))

    def test_missing_cache_is_not_pass(self):
        self.cache(None)
        result=self.result()
        self.assertNotEqual(result['overall'],'pass')
        self.assertTrue(any('缓存' in r['message'] for r in result['results']))

    def test_amount_mismatch_is_detected(self):
        self.config['money'][0]['value']='99.99'
        self.assertEqual(self.result()['overall'],'fail')

    def test_carrier_mismatch_is_detected(self):
        self.config['materials'][0]['carrier']='electronic'
        self.assertEqual(self.result()['overall'],'fail')

    def test_tick_does_not_prove_content_checked(self):
        self.config['materials'][0]['content_status']='pending'
        self.assertEqual(self.result()['overall'],'unverified')

    def test_road_or_pond_missing_from_coverage_is_unverified(self):
        self.config['coverage']['expected_objects'].append('农村道路A')
        self.assertEqual(self.result()['overall'],'unverified')

    def test_incomplete_config_is_unverified(self):
        for key in ('coverage','formulas','money','print_report','materials'):
            self.config.pop(key)
        self.assertEqual(self.result()['overall'],'unverified')

    def test_payment_uppercase_locations_required(self):
        self.config['stage']='payment'
        self.assertEqual(self.result()['overall'],'unverified')

    def test_formal_opinion_lists_and_policy_citations(self):
        paragraphs = [
            self.batch+'补偿的审核意见', '一、项目基本情况',
            '（一）征地及留用地补偿为100.00元。', '二、审核依据',
            '1.《合成项目补偿办法》', '三、审核意见', '（一）合规性审查',
            '1.请补充开户资料。', '（二）补偿费用审核',
            '1.重复列入一笔费用，审核予以核减。', '四、其他需说明事项',
            '1.补充转账凭证。', '附表：', '1.合成补偿审核表'
        ]
        self.assertEqual(check.opinion_entries(paragraphs), [(8,paragraphs[7]), (10,paragraphs[9])])
        d=Document()
        for p in paragraphs:
            d.add_paragraph(p)
        d.save(self.files['opinion'])
        self.config['stage']='payment'
        self.config['opinion_sources']=[{'paragraph':8,'source':'project'},
                                         {'paragraph':10,'source':'verified','decision':'confirmed'}]
        rows=self.result()['results']
        self.assertFalse(any(r['check'] in {'text','sources'} and r['status'] in {'fail','unverified'} for r in rows))

    def test_human_format_changes_only_advisory(self):
        doc=Document(self.files['opinion'])
        doc.paragraphs[1].text='3.本批《土地测绘拼图》未加盖测绘公司公章，请补盖。'
        doc.save(self.files['opinion'])
        self.config['historical']=True
        result=self.result()
        self.assertTrue(any(r['status']=='advisory' for r in result['results']))
        self.assertNotEqual(result['overall'],'fail')

    def test_section_headings_are_not_opinion_items(self):
        self.assertEqual(check.opinion_entries(['（一）合规性审查','1.请核实。','（二）补偿费用审核']),[(2,'1.请核实。')])

    def test_exact_batch_split_reports_only_missing_batch(self):
        response=self.root/'response.md'
        response.write_text('## 批次：批A\n问题A\n## 批次：批C\n问题C\n',encoding='utf-8')
        config={'batches':[{'batch':'批A','output':str(self.root/'A.md')},{'batch':'批B','output':str(self.root/'B.md')}]}
        result=check.split_gemini(config,response)
        self.assertEqual(result['missing'],['批B'])
        self.assertEqual(result['unexpected'],['批C'])
        self.assertEqual(result['completed'][0]['batch'],'批A')
        self.assertFalse((self.root/'B.md').exists())

    def test_duplicate_batch_does_not_write_result(self):
        response=self.root/'response.md'
        response.write_text('## 批次：批A\n甲\n## 批次：批A\n乙',encoding='utf-8')
        result=check.split_gemini({'batches':[{'batch':'批A','output':str(self.root/'A.md')}]},response)
        self.assertEqual(result['duplicate'],['批A'])
        self.assertFalse((self.root/'A.md').exists())

    def test_split_preserves_existing_different_result(self):
        response=self.root/'response.md'
        response.write_text('## 批次：批A\n新结果',encoding='utf-8')
        output=self.root/'A.md'
        output.write_text('已有人工结果',encoding='utf-8')
        result=check.split_gemini({'batches':[{'batch':'批A','output':str(output)}]},response)
        self.assertEqual(result['protected_conflicts'],['批A'])
        self.assertEqual(output.read_text(encoding='utf-8'),'已有人工结果')

    def test_uppercase_exact_decimal(self):
        for value, expected in [('0','零元整'),('1001.05','壹仟零壹元零伍分'),('100000001','壹亿零壹元整'),('22801.18','贰万贰仟捌佰零壹元壹角捌分')]:
            self.assertEqual(check.upper_money(value),expected)

    def final_flow(self):
        run=self.root/'integration'
        info=self.root/'批次信息.md'
        info.write_text(self.batch,encoding='utf-8')
        outputs={k:str(run/'成果'/Path(p).name) for k,p in self.files.items()}
        state.init(run,{'package':'integration','batches':[{'id':'A','name':self.batch,'info':str(info),'inputs':[str(self.source)],'outputs':outputs}]})
        def event(role,status,**extra):
            return state.record(run,{'batch':'A','role':role,'status':status,'task_id':role,**extra})
        event('prepare','running')
        event('prepare','done',artifacts=[str(info)])
        event('gemini','running')
        raw=self.root/'gemini.md'
        raw.write_text('Gemini fixture')
        event('gemini','done',artifacts=[str(raw)])
        for role,kinds in [('table',['audit','materials']),('writer',['opinion'])]:
            event(role,'running')
            for kind in kinds:
                state.guard(run,'A',outputs[kind],self.files[kind],role)
            event(role,'done',artifacts=[outputs[k] for k in kinds])
        event('verify','running')
        verified=self.root/'核实结论.json'
        write_json(verified,self.config['coverage'])
        event('verify','done',artifacts=[str(verified)])
        event('review','running')
        self.config['files']=outputs
        rebound=self.root/'delivered-print.json'
        print_check.rebind(self.print_report,outputs,rebound)
        self.print_report=rebound
        self.config['print_report']=str(rebound)
        config=self.root/'config.json'
        write_json(config,self.config)
        report=self.root/'actual_check.json'
        call=subprocess.run([sys.executable,str(TOOLS/'check_outputs.py'),'check','--input',str(config),'--output',str(report)],capture_output=True)
        self.assertEqual(call.returncode,0,call.stdout.decode('utf-8',errors='replace'))
        return run,event,outputs,report,verified

    def test_final_requires_real_current_check_report(self):
        run,event,outputs,report,verified=self.final_flow()
        event('review','done',artifacts=[str(verified)],check_report=str(report))
        event('final','running')
        event('final','done',artifacts=[str(report)],check_report=str(report))
        self.assertEqual(state.status(run)['batches'][0]['summary'],'验收完成')

    def test_report_becomes_stale_after_human_change(self):
        run,event,outputs,report,verified=self.final_flow()
        event('review','done',artifacts=[str(verified)],check_report=str(report))
        event('final','running')
        doc=Document(outputs['opinion'])
        doc.paragraphs[1].text='1.人工已改。'
        doc.save(outputs['opinion'])
        with self.assertRaises(ValueError):
            event('final','done',artifacts=[str(report)],check_report=str(report))
        self.assertEqual(Document(outputs['opinion']).paragraphs[1].text,'1.人工已改。')

    def test_unverified_report_cannot_complete_review(self):
        run,event,outputs,report,verified=self.final_flow()
        data=check.load(report)
        data['overall']='unverified'
        write_json(report,data)
        with self.assertRaises(ValueError):
            event('review','done',artifacts=[str(verified)],check_report=str(report))


@unittest.skipUnless(gemini is not None, 'Optional local Gemini adapter is not configured')
class GeminiTests(TemporaryCase):
    def fake_browser(self):
        page=argparse.Namespace(url='https://gemini.google.com/app/abcdef123456',query_selector=AsyncMock(return_value=None),goto=AsyncMock(),wait_for_timeout=AsyncMock())
        gw=argparse.Namespace(start=AsyncMock(),page=AsyncMock(return_value=page),new_chat=AsyncMock(return_value=True),_pick_model=AsyncMock(return_value='argon'),_answer_sig=AsyncMock(return_value=0),close=AsyncMock())
        return page,gw

    def test_checkpoint_written_before_timeout_and_resumes_without_upload(self):
        out=self.root/'result.md'
        page,gw=self.fake_browser()
        async def fail(*args):
            saved=json.loads(out.with_suffix('.meta.json').read_text(encoding='utf-8'))
            self.assertTrue(saved['final_sent'])
            self.assertEqual(saved['url'],page.url)
            raise TimeoutError('test timeout')
        base=[str(BRIDGE),'--prompt-file',str(self.root/'prompt.md'),'--out',str(out),'--work-dir',str(self.root/'work')]
        (self.root/'prompt.md').write_text('prompt')
        with patch.object(gemini,'LOCK',self.root/'lock'),patch.object(gemini,'ensure_browser'),patch.object(gemini,'acquire_lock'),patch.object(gemini,'GeminiWeb',return_value=gw),patch.object(gemini,'switch_mode',AsyncMock()),patch.object(gemini,'type_text',AsyncMock()),patch.object(gemini,'send',AsyncMock()),patch.object(gemini,'follow_conversation',AsyncMock()),patch.object(gemini,'wait_done',fail),patch.object(sys,'argv',base):
            self.assertEqual(gemini.main(),4)
        self.assertFalse(out.exists())
        self.assertEqual(json.loads(out.with_suffix('.meta.json').read_text())['status'],'timeout')
        resume=[str(BRIDGE),'--resume-url',page.url,'--out',str(out),'--work-dir',str(self.root/'work')]
        upload=AsyncMock()
        with patch.object(gemini,'LOCK',self.root/'lock'),patch.object(gemini,'ensure_browser'),patch.object(gemini,'acquire_lock'),patch.object(gemini,'GeminiWeb',return_value=gw),patch.object(gemini,'upload',upload),patch.object(gemini,'wait_done',AsyncMock(return_value='result')),patch.object(sys,'argv',resume):
            self.assertEqual(gemini.main(),0)
        upload.assert_not_called()
        self.assertEqual(out.read_text(),'result')

    def test_receipt_group_checkpoint_cannot_resume_as_final_result(self):
        out=self.root/'out.md'
        url='https://gemini.google.com/app/abcdef123456'
        write_json(out.with_suffix('.meta.json'),{'url':url,'final_sent':False,'status':'timeout'})
        with patch.object(sys,'argv',[str(BRIDGE),'--resume-url',url,'--out',str(out)]):
            self.assertEqual(gemini.main(),2)
        self.assertFalse(out.exists())

    def test_work_directory_does_not_change_browser_lock(self):
        lock=gemini.LOCK
        args=argparse.Namespace(work_dir=str(self.root/'work'))
        path=gemini.work_directory(args,'task_')
        self.assertEqual(path.parent,self.root/'work')
        self.assertEqual(gemini.LOCK,lock)

    def test_long_live_task_lock_is_never_stolen(self):
        lock=self.root/'lock'
        lock.write_text('123')
        os.utime(lock,(1,1))
        with patch.object(gemini,'LOCK',lock),patch.object(gemini,'_pid_alive',return_value=True):
            with self.assertRaises(TimeoutError):
                gemini.acquire_lock(wait_s=0)
        self.assertEqual(lock.read_text(),'123')

    def test_os_lock_releases_for_next_instance(self):
        lock=self.root/'os_lock'
        with patch.object(gemini,'LOCK',lock):
            gemini.acquire_lock(wait_s=0)
            try:
                if os.name=='nt':
                    with self.assertRaises(PermissionError):lock.read_text()
            finally:
                gemini.release_lock()
            self.assertEqual(lock.read_text(),'0')
            gemini.acquire_lock(wait_s=0)
            gemini.release_lock()

    def test_concurrent_browser_jobs_are_serialized(self):
        lock=self.root/'browser_lock'
        trace=self.root/'trace'
        code="""import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import gemini_task as g
g.LOCK=Path(sys.argv[2])
g.acquire_lock(wait_s=10)
try:
    with open(sys.argv[3],'a') as f:f.write('start\\n')
    time.sleep(.05)
    with open(sys.argv[3],'a') as f:f.write('end\\n')
finally:g.release_lock()
"""
        def call(_):
            return subprocess.run([sys.executable,'-c',code,str(BRIDGE.parent),str(lock),str(trace)],capture_output=True).returncode
        with ThreadPoolExecutor(max_workers=3) as pool:
            self.assertEqual(list(pool.map(call,range(3))),[0,0,0])
        self.assertEqual(trace.read_text().splitlines(),['start','end']*3)

    def test_pid_lookup_does_not_match_substring(self):
        if os.name!='nt':self.skipTest('Windows tasklist')
        result=argparse.Namespace(returncode=0,stdout='"python.exe","1234","Console","1","30 K"\n')
        with patch.object(gemini.subprocess,'run',return_value=result):
            self.assertFalse(gemini._pid_alive(123))
            self.assertTrue(gemini._pid_alive(1234))

    def test_zip_cannot_write_outside_work_directory(self):
        archive=self.root/'source.zip'
        with zipfile.ZipFile(archive,'w') as z:z.writestr('../outside.txt','bad')
        work=self.root/'work'
        work.mkdir()
        with self.assertRaises(ValueError):gemini.repack_zip(archive,work)
        self.assertFalse((work/'outside.txt').exists())

    def test_corrupt_checkpoint_is_reported_without_launch(self):
        out=self.root/'result.md'
        out.with_suffix('.meta.json').write_text('{broken')
        with patch.object(gemini,'ensure_browser') as launch,patch.object(sys,'argv',[str(BRIDGE),'--resume-url','https://gemini.google.com/app/abcdef123456','--out',str(out)]):
            self.assertEqual(gemini.main(),2)
            launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
