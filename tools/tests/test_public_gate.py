"""Public archive checks reject hidden data and stale manifests."""
from pathlib import Path
import hashlib,json,os,sys,tempfile,unittest,zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from public_check import check,allowed,digest

class PublicGateTests(unittest.TestCase):
    def setUp(self):
        parent=os.environ.get('LAND_AUDIT_TEST_DIR')
        if not parent:raise RuntimeError('Set LAND_AUDIT_TEST_DIR to the session working directory')
        self.tmp=tempfile.TemporaryDirectory(dir=parent);self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def manifest(self,name,content):
        p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True)
        if isinstance(content,bytes):p.write_bytes(content)
        else:p.write_text(content,encoding='utf8')
        m=self.root/'public_manifest.json';m.write_text(json.dumps({'files':[{'path':name,'sha256':digest(p)}]}),encoding='utf8');return m,p
    def test_private_and_parent_paths_rejected(self):
        for path in ['.local/evidence.json','tools/../../outside.md','/outside.md','tools/.local/raw.md']:
            self.assertFalse(allowed(path))
    def test_clean_content_and_stale_hash(self):
        m,p=self.manifest('rules/audit.md','合成规则，需核证据。')
        self.assertTrue(check(m)['ok']);p.write_text('修改后的内容',encoding='utf8')
        self.assertFalse(check(m)['ok'])
    def test_release_requires_complete_entrypoints_and_public_inventory(self):
        m,p=self.manifest('rules/audit.md','合成规则。')
        extra=self.root/'rules/unlisted.md';extra.write_text('未登记文件。',encoding='utf8')
        result=check(m,require_release=True)
        kinds={r['kind'] for r in result['findings']}
        self.assertIn('required_release_file_missing',kinds)
        self.assertIn('public_file_not_in_manifest',kinds)
        self.assertIn('invalid_release_manifest_version',kinds)
    def test_hidden_office_text_is_scanned(self):
        name='templates/test.xlsx';p=self.root/name;p.parent.mkdir(parents=True)
        # A synthetic marker exercises detection without storing a real identity.
        marker='1'+'38'+'0'*8
        with zipfile.ZipFile(p,'w') as z:
            z.writestr('xl/worksheets/sheet1.xml',f'<worksheet><hidden>{marker}</hidden></worksheet>')
        m=self.root/'public_manifest.json';m.write_text(json.dumps({'files':[{'path':name,'sha256':digest(p)}]}),encoding='utf8')
        result=check(m);self.assertFalse(result['ok']);self.assertTrue(any(x['kind']=='phone_number' for x in result['findings']))
    def test_private_word_not_echoed(self):
        m,p=self.manifest('rules/audit.md','合成敏感词')
        deny=self.root/'private.json';deny.write_text(json.dumps(['合成敏感词']),encoding='utf8')
        result=check(m,deny);self.assertFalse(result['ok']);self.assertNotIn('合成敏感词',json.dumps(result,ensure_ascii=False))
    def test_office_author_and_external_assets_rejected(self):
        name='templates/test.docx';p=self.root/name;p.parent.mkdir(parents=True)
        with zipfile.ZipFile(p,'w') as z:
            z.writestr('docProps/core.xml','<core><creator>合成作者</creator></core>')
            z.writestr('word/embeddings/test.bin',b'test')
        m=self.root/'public_manifest.json';m.write_text(json.dumps({'files':[{'path':name,'sha256':digest(p)}]}),encoding='utf8')
        result=check(m);self.assertFalse(result['ok']);kinds={x['kind'] for x in result['findings']}
        self.assertIn('personal_document_property',kinds);self.assertIn('embedded_or_external_asset',kinds)
    def test_office_xml_declared_encoding_must_parse_from_bytes(self):
        name='templates/test.xlsx';p=self.root/name;p.parent.mkdir(parents=True)
        with zipfile.ZipFile(p,'w') as z:
            z.writestr('docProps/core.xml',b'<?xml version="1.0" encoding="utf8"?><core><title>'+ '合成标题'.encode('utf8')+b'</title></core>')
        m=self.root/'public_manifest.json';m.write_text(json.dumps({'files':[{'path':name,'sha256':digest(p)}]}),encoding='utf8')
        self.assertFalse(check(m)['ok'])
    def test_office_property_qname_requires_in_scope_namespace(self):
        name='templates/test.xlsx';p=self.root/name;p.parent.mkdir(parents=True)
        xml='<core xmlns:ns2="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><ns2:created xsi:type="dcterms:W3CDTF">2026-01-01T00:00:00Z</ns2:created></core>'
        with zipfile.ZipFile(p,'w') as z:z.writestr('docProps/core.xml',xml)
        m=self.root/'public_manifest.json';m.write_text(json.dumps({'files':[{'path':name,'sha256':digest(p)}]}),encoding='utf8')
        result=check(m)
        self.assertFalse(result['ok'])
        self.assertTrue(any(r['kind']=='undeclared_qname_prefix' for r in result['findings']))
if __name__=='__main__':unittest.main()
