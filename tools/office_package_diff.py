"""Read-only Office ZIP package comparison, not a package-preserving writer or business audit."""
from pathlib import Path
from zipfile import ZipFile
import argparse,hashlib,json

def inventory(path):
 result={}
 with ZipFile(path) as z:
  for info in z.infolist():
   if info.is_dir():continue
   if info.filename in result:raise ValueError('文件包含重复部件名，不能可靠对比')
   if info.file_size>256*1024*1024:raise ValueError('单个部件超过256MB，请使用专门的大文件校验流程')
   with z.open(info) as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
   result[info.filename]=dict(sha256=digest,bytes=info.file_size)
 return result
def group(name):
 if 'cellimages' in name.lower() or '/media/' in name:return 'images'
 if 'vba' in name.lower():return 'macros'
 if name.endswith('.rels'):return 'relationships'
 if '/embeddings/' in name or 'oleObject' in name:return 'embedded_objects'
 if 'externalLinks' in name:return 'external_links'
 return 'other'
def file_hash(path):
 with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def compare(before,after):
 hashes=[file_hash(before),file_hash(after)]
 a,b=inventory(before),inventory(after)
 if hashes!=[file_hash(before),file_hash(after)]:raise ValueError('文件包读取期间改变，请重新比较')
 removed=sorted(set(a)-set(b));added=sorted(set(b)-set(a));changed=sorted(k for k in set(a)&set(b) if a[k]['sha256']!=b[k]['sha256'])
 return dict(before=str(Path(before).resolve()),after=str(Path(after).resolve()),before_sha256=hashes[0],after_sha256=hashes[1],added=added,removed=removed,changed=changed,changes=[dict(part=k,kind=kind,category=group(k)) for kind,items in [('added',added),('removed',removed),('changed',changed)] for k in items],package_identical=not(added or removed or changed),business_verified=False,note='仅比较ZIP部件解压字节。保存软件可能合法重写XML；差异不等于损坏，相同也不等于业务正确。需核对目标变更以及图片、关系、宏、嵌入物实际可用性。')
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('before');p.add_argument('after');p.add_argument('--output',required=True);a=p.parse_args()
 try:
  out=Path(a.output).resolve()
  if any(out==Path(x).resolve() for x in [a.before,a.after]):raise ValueError('输出不能覆盖来源')
  result=compare(a.before,a.after)
  with out.open('x',encoding='utf8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
  print('已生成部件差异报告');return 0
 except (ValueError,OSError,TypeError) as e:print(str(e));return 1
if __name__=='__main__':raise SystemExit(main())
