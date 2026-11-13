"""Check an explicit public file manifest, including Office package contents."""
from pathlib import Path,PurePosixPath
import argparse,hashlib,json,re,zipfile
import xml.etree.ElementTree as ET
from xml.dom import minidom

PATTERNS={
    'identity_number':re.compile(r'(?<!\d)\d{17}[\dXx](?!\d)'),
    'phone_number':re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)'),
    'long_account_number':re.compile(r'(?<![\w])\d{16,19}(?![\w])'),
    'local_drive_path':re.compile(r'(?<![\w])[A-Za-z]:[\\/]'),
    'home_path':re.compile(r'/(?:'+'Users|home'+r')/[^/\s]+/'),
    'credential_assignment':re.compile(r'(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,})'),
}
ROOT_FILES={'README.md','AI_START.md','AI_README.md','AGENTS.md','workflow.md','requirements.txt','LICENSE-CODE','LICENSE-CONTENT','.gitignore'}
ROOT_DIRS={'rules','catalog','roles','templates','tools','docs','adapters','examples','contracts'}
SUFFIXES={'.md','.py','.json','.csv','.txt','.xlsx','.docx'}
RELEASE_FILES={'README.md','AI_START.md','AI_README.md','AGENTS.md','workflow.md',
    'rules/audit.md','rules/format.md','catalog/opinions_catalog.json','catalog/opinions.md',
    'catalog/check_catalog.csv','contracts/land_audit.schema.json','docs/tools.md',
    'docs/lessons.md','templates/使用说明.md','templates/template_manifest.json',
    'templates/土地_青苗竹果木.xlsx','templates/土地_青苗鱼塘.xlsx',
    'templates/房屋构建筑物.xlsx','templates/坟墓迁移.xlsx',
    'templates/资料符合性核查表.xlsx','templates/审核意见.docx','templates/初审意见.docx',
    'LICENSE-CODE','LICENSE-CONTENT','requirements.txt'}
def digest(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def allowed(name):
    p=PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or '\\' in name:return False
    if name in ROOT_FILES:return True
    return len(p.parts)>1 and p.parts[0] in ROOT_DIRS and all(not x.startswith('.') and x!='__pycache__' for x in p.parts) and p.suffix in SUFFIXES and not name.endswith(('.meta.json','.partial.md'))
def office_text(path):
    chunks=[];issues=[]
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if any(x in name.lower() for x in ['embeddings/','externallinks/','media/']):
                issues.append({'part':name,'kind':'embedded_or_external_asset'})
            if not name.endswith(('.xml','.rels')):continue
            body=z.read(name)
            element=ET.fromstring(body)
            raw=body.decode('utf8')
            if name.startswith('docProps/'):
                document=minidom.parseString(body)
                for node in document.getElementsByTagName('*'):
                    for attribute in node.attributes.values():
                        if attribute.namespaceURI!='http://www.w3.org/2001/XMLSchema-instance' or attribute.localName!='type' or ':' not in attribute.value:continue
                        prefix=attribute.value.split(':',1)[0];scope=node
                        while scope is not None and scope.nodeType==scope.ELEMENT_NODE:
                            if scope.hasAttribute('xmlns:'+prefix):break
                            scope=scope.parentNode
                        if scope is None or scope.nodeType!=scope.ELEMENT_NODE:
                            issues.append({'part':name,'kind':'undeclared_qname_prefix'})
                document.unlink()
            chunks.append((name,raw))
            chunks.append((name+':joined_text',''.join(element.itertext())))
            for node in element.iter():
                local=node.tag.split('}')[-1]
                if local in {'creator','lastModifiedBy','Company','Manager'} and (node.text or '').strip():
                    issues.append({'part':name,'kind':'personal_document_property'})
                if local=='Relationship' and node.get('TargetMode')=='External':
                    issues.append({'part':name,'kind':'external_relationship'})
                if local in {'ins','del','comment','commentEx'}:
                    issues.append({'part':name,'kind':'revision_or_comment'})
    return chunks,issues
def check(manifest,denylist=None,require_release=False):
    manifest=Path(manifest).resolve();root=manifest.parent
    data=json.loads(manifest.read_text('utf8'));rows=data['files']
    words=json.loads(Path(denylist).read_text('utf8')) if denylist else []
    if isinstance(words,dict):words=words['terms']
    findings=[];seen=set();inspected=0
    if require_release:
        listed={row['path'] for row in rows}
        for name in sorted(RELEASE_FILES-listed):
            findings.append({'file':name,'kind':'required_release_file_missing'})
        for p in root.rglob('*'):
            if not p.is_file():continue
            name=p.relative_to(root).as_posix()
            if allowed(name) and name not in listed:
                findings.append({'file':name,'kind':'public_file_not_in_manifest'})
        if data.get('schema_version')!=1 or not data.get('version'):
            findings.append({'kind':'invalid_release_manifest_version'})
    for row in rows:
        name=row['path']
        if name in seen:findings.append({'file':name,'kind':'duplicate_manifest_path'});continue
        seen.add(name)
        if not allowed(name):findings.append({'file':name,'kind':'outside_public_allowlist'});continue
        p=root/name
        if not p.is_file():findings.append({'file':name,'kind':'missing_file'});continue
        if p.is_symlink() or not p.resolve().is_relative_to(root):findings.append({'file':name,'kind':'external_file'});continue
        if digest(p)!=row['sha256']:findings.append({'file':name,'kind':'manifest_hash_mismatch'})
        try:
            if p.suffix in {'.xlsx','.docx'}:
                chunks,issues=office_text(p);findings.extend({'file':name,**x} for x in issues)
            else:chunks=[('text',p.read_text('utf8'))]
            for part,body in chunks:
                for kind,pattern in PATTERNS.items():
                    if pattern.search(body):findings.append({'file':name,'part':part,'kind':kind})
                for word in words:
                    if word and word in body:findings.append({'file':name,'part':part,'kind':'private_term','term_fingerprint':hashlib.sha256(word.encode()).hexdigest()[:12]})
            inspected+=1
        except (ValueError,OSError,zipfile.BadZipFile,UnicodeError,ET.ParseError) as e:
            findings.append({'file':name,'kind':'unreadable','error_type':type(e).__name__})
    return {'ok':not findings,'files_checked':inspected,'findings':findings,'denominator':'explicit_public_manifest','manual_review_required':True}
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',default='public_manifest.json');parser.add_argument('--denylist');parser.add_argument('--output')
    parser.add_argument('--release',action='store_true',help='Require all entrypoints/templates and a complete manifest')
    args=parser.parse_args();report=check(args.manifest,args.denylist,args.release)
    text=json.dumps(report,ensure_ascii=False,indent=2)
    if args.output:Path(args.output).write_text(text+'\n',encoding='utf8')
    print(text);return 0 if report['ok'] else 1
if __name__=='__main__':raise SystemExit(main())
