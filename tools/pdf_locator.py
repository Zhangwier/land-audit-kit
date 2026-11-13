"""Exact native PDF token locator; absence of a text hit never means a missing drawing."""
import argparse,hashlib,json,unicodedata
from pathlib import Path
import pymupdf

def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def locate(source,targets,output,dpi=144):
 source=Path(source).resolve();output=Path(output).resolve();digest=sha(source)
 if not isinstance(targets,list) or not targets or any(not isinstance(t,str) or not t.strip() for t in targets):raise ValueError('编号须为非空字符串列表')
 norm=lambda t:unicodedata.normalize('NFKC',t).strip()
 keys={norm(t):t for t in targets}
 if len(keys)!=len(targets):raise ValueError('归一化后编号重复，须先明确原编号关系')
 if not 36<=dpi<=300:raise ValueError('dpi须在36至300之间')
 hits=[];no_text=[]
 with pymupdf.open(source) as d:
  if d.needs_pass:raise ValueError('请先提供可读PDF')
  if output.exists():raise FileExistsError('输出目录已存在')
  output.mkdir(parents=True)
  for n,page in enumerate(d,1):
   words=page.get_text('words')
   if not words:no_text.append(n)
   for word in words:
    token=norm(word[4])
    if token not in keys:continue
    box=pymupdf.Rect(word[:4]);h=max(box.height,4);clip=(box+(-6*h,-3*h,6*h,12*h))&page.rect
    if clip.width*clip.height*(dpi/72)**2>25000000:raise ValueError('定位区域过大，请降低dpi')
    filename=f'hit-{len(hits)+1:04d}.png';page.get_pixmap(matrix=pymupdf.Matrix(dpi/72,dpi/72),clip=clip,alpha=False).save(output/filename)
    hits.append(dict(target=keys[token],physical_page=n,raw_token=word[4],bbox_pt=list(box),clip_pt=list(clip),nearby_text=page.get_text(clip=clip),image=filename))
 if sha(source)!=digest:raise ValueError('原件读取期间改变')
 found={x['target'] for x in hits};r=dict(source=str(source),sha256=digest,hits=hits,not_text_located=[t for t in targets if t not in found],pages_without_native_text=no_text,business_verified=False,note='只匹配完整原生文字词框，保留连字符；文本拆字/标点/扫描件可能无法命中。不命中不等于缺图。裁图不是红线、面积或权属判断。')
 (output/'locations.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf8');return r
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('pdf');p.add_argument('--targets',required=True,help='UTF8 JSON编号数组');p.add_argument('--output',required=True);p.add_argument('--dpi',type=int,default=144);a=p.parse_args()
 try:r=locate(a.pdf,json.loads(Path(a.targets).read_text(encoding='utf-8-sig')),a.output,a.dpi);print(json.dumps(dict(hits=len(r['hits']),not_text_located=r['not_text_located']),ensure_ascii=False));return 0
 except (ValueError,OSError,TypeError) as e:print(str(e));return 1
if __name__=='__main__':raise SystemExit(main())
