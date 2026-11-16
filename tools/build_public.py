"""Generate reading views and package only the explicit public allowlist."""
from pathlib import Path
from collections import defaultdict
import argparse,csv,json,zipfile
from public_check import allowed,digest,check,RELEASE_FILES

ROOT=Path(__file__).resolve().parents[1]
def joined(items):
    return "；".join(x.rstrip("。；; ") for x in items) + "。"

def generate():
    for name in sorted(RELEASE_FILES-{'AI_README.md','catalog/opinions.md'}):
        if not (ROOT/name).is_file():raise FileNotFoundError('Required release file: '+name)
    rows=json.loads((ROOT/'catalog/opinions_catalog.json').read_text('utf8'))
    grouped=defaultdict(list)
    ids=set()
    for r in rows:
        if r['id'] in ids:raise ValueError('Duplicate opinion ID')
        ids.add(r['id']);grouped[r['category']].append(r)
        for key in ['conditions','evidence','avoid','source_ids']:
            if not isinstance(r[key],list) or not r[key]:raise ValueError(f'{r["id"]}: missing {key}')
    parts=['# 审核意见分类句式\n\n由 opinions_catalog.json 生成。逐条核适用条件和证据后使用；占位符只用于母版。来源 ID 对应维护者私有对照记录，不代表当前批次事实。\n']
    for category,items in grouped.items():
        parts.append(f'\n## {category}\n')
        for r in items:
            parts.append(f'\n### {r["id"]} {r["issue"]}\n\n适用：'+ joined(r['conditions']).rstrip('。')+'。\n\n需核证据：'+joined(r['evidence']).rstrip('。')+'。\n\n> '+r['template']+'\n\n避免：'+joined(r['avoid']).rstrip('。')+'。\n\n结论强度：'+r['strength']+'。来源：'+', '.join(r['source_ids'])+'。\n')
    (ROOT/'catalog/opinions.md').write_text(''.join(parts),encoding='utf8',newline='\n')
    titles=['AI_START.md','rules/audit.md','rules/format.md','templates/使用说明.md','catalog/opinions.md','workflow.md','docs/lessons.md','examples/README.md']
    combined=['# 征地审核 AI 合订文本\n\n此文件由权威规则和句式生成。供无法逐文件访问的 AI 阅读；空白 Office 母版仍需另行下载。原文件中相对链接按仓库根及各自目录解析。\n']
    for name in titles:combined.append(f'\n---\n\n来源文件：{name}\n\n'+(ROOT/name).read_text('utf8'))
    with (ROOT/'catalog/check_catalog.csv').open(encoding='utf-8-sig',newline='') as stream:
        checks=list(csv.DictReader(stream))
    combined.append('\n---\n\n## 逐项检查目录\n\n来源文件：catalog/check_catalog.csv。按对象类别和当前范围选择适用项，完成记录不替代实质回源。\n')
    for row in checks:
        combined.append(f'\n### {row["check_id"]} {row["title"]}\n\n类别：{row["module"]}；范围：{row["modes"]}。\n\n输入：{row["inputs"]}。\n\n动作：{row["action"]}。\n\n记录：{row["record"]}。\n\n未核边界：{row["blocked_when"]}。\n')
    (ROOT/'AI_README.md').write_text(''.join(combined),encoding='utf8',newline='\n')
    paths=sorted(p for p in ROOT.rglob('*') if p.is_file() and allowed(p.relative_to(ROOT).as_posix()))
    version=json.loads((ROOT/'templates/template_manifest.json').read_text('utf8'))['version']
    manifest={'schema_version':1,'version':version,'files':[{'path':p.relative_to(ROOT).as_posix(),'sha256':digest(p),'license':'MIT' if p.suffix=='.py' or p.parts[-2]=='contracts' or p.name in {'LICENSE-CODE','requirements.txt','.gitignore','.gitattributes'} else 'CC-BY-4.0'} for p in paths]}
    (ROOT/'public_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    return manifest
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--zip');parser.add_argument('--denylist')
    args=parser.parse_args();manifest=generate()
    if args.zip:
        report=check(ROOT/'public_manifest.json',args.denylist,require_release=True)
        if not report['ok']:raise ValueError(json.dumps(report,ensure_ascii=False))
        destination=Path(args.zip).resolve()
        if destination.exists():raise FileExistsError(destination)
        names=[r['path'] for r in manifest['files']]+['public_manifest.json']
        sums=''.join(digest(ROOT/n)+'  '+n+'\n' for n in names)
        with zipfile.ZipFile(destination,'x',zipfile.ZIP_DEFLATED) as z:
            for name in names:z.write(ROOT/name,'land-audit-kit/'+name)
            z.writestr('land-audit-kit/SHA256SUMS.txt',sums)
    print(json.dumps({'files':len(manifest['files']),'version':manifest['version'],'zip':args.zip},ensure_ascii=False))
if __name__=='__main__':main()
