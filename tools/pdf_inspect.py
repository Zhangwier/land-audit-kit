"""Read-only PDF inventory, text locations and explicitly selected page renders."""
import argparse,hashlib,json,math
from pathlib import Path
import pymupdf

def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def render(page,dpi=144,clip=None):
 if not 36<=dpi<=300:raise ValueError('dpi须在36至300之间')
 rect=page.rect
 if clip is not None:
  if len(clip)!=4 or not all(math.isfinite(float(v)) for v in clip):raise ValueError('裁剪框须为4个有限坐标')
  rect=pymupdf.Rect(clip)
  if rect.is_empty or not page.rect.contains(rect):raise ValueError('裁剪框为空或超出页面，坐标单位为PDF点')
 if rect.width*rect.height*(dpi/72)**2>25000000:raise ValueError('渲染超过2500万像素，请降低dpi或明确裁剪区域')
 return page.get_pixmap(matrix=pymupdf.Matrix(dpi/72,dpi/72),clip=rect,alpha=False,colorspace=pymupdf.csRGB)
def inspect(source,output,pages=None,dpi=144):
 source=Path(source).resolve();output=Path(output).resolve();before=sha(source)
 with pymupdf.open(source) as d:
  if d.needs_pass:raise ValueError('加密PDF需先提供可读版本')
  selected=set(pages or [])
  if any(not isinstance(n,int) or not 1<=n<=len(d) for n in selected):raise ValueError('页码须为PDF物理页，且在有效范围内')
  if output.exists():raise FileExistsError('输出目录已存在，拒绝覆盖')
  output.mkdir(parents=True);rows=[]
  for n,p in enumerate(d,1):
   text=p.get_text();blocks=[dict(bbox=list(b[:4]),text=b[4]) for b in p.get_text('blocks') if len(b)>6 and b[6]==0]
   row=dict(physical_page=n,width_pt=p.rect.width,height_pt=p.rect.height,rotation=p.rotation,text=text,text_blocks=blocks,images=len(p.get_images()),vector_paths=len(p.get_drawings()),annotations=sum(1 for _ in p.annots()),text_layer_present=bool(text.strip()),requires_visual_review=True)
   if n in selected:
    dest=output/f'page-{n:04d}.png';render(p,dpi).save(dest);row['render']=dest.name
   rows.append(row)
  result=dict(source=str(source),sha256=before,page_count=len(d),pages=rows,rendered_physical_pages=sorted(selected),note='文本层可能残缺或为错误OCR；无文本层不代表空白。渲染/提取不是实际读页，不自动判断签章或红线有效性。')
 if sha(source)!=before:raise ValueError('原件读取期间变化，请重试')
 (output/'inventory.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');return result
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('input');p.add_argument('--output',required=True);p.add_argument('--pages',help='需渲染的物理页，例如1,3-5；不填只登记全部页');p.add_argument('--dpi',type=int,default=144);a=p.parse_args()
 try:
  pages=[]
  for part in (a.pages or '').split(','):
   if not part:continue
   if '-' in part:
    left,right=map(int,part.split('-'))
    if right<left:raise ValueError('页码范围不能倒序')
    pages.extend(range(left,right+1))
   else:pages.append(int(part))
  r=inspect(a.input,a.output,pages,a.dpi);print(json.dumps(dict(page_count=r['page_count'],output=a.output),ensure_ascii=False));return 0
 except (OSError,ValueError,TypeError) as e:print(str(e));return 1
if __name__=='__main__':raise SystemExit(main())
