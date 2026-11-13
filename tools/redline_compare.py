"""PDF drawing visual locator. No automatic registration, area or redline boundary certification."""
import argparse,json,hashlib,math
from pathlib import Path
import pymupdf
from PIL import Image,ImageChops

def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def raster(page,dpi,clip):
 if not 36<=dpi<=300:raise ValueError('dpi须在36至300之间')
 rect=page.rect
 if clip is not None:
  if len(clip)!=4 or not all(math.isfinite(float(v)) for v in clip):raise ValueError('裁剪框须为4个有限坐标')
  rect=pymupdf.Rect(clip)
  if rect.is_empty or not page.rect.contains(rect):raise ValueError('裁剪框超出页面或为空')
 if rect.width*rect.height*(dpi/72)**2>25000000:raise ValueError('超过2500万像素，请裁剪或降低dpi')
 pix=page.get_pixmap(matrix=pymupdf.Matrix(dpi/72,dpi/72),clip=rect,alpha=False,colorspace=pymupdf.csRGB)
 return Image.frombytes('RGB',[pix.width,pix.height],pix.samples),list(rect)
def compare(old,new,output,page_old=1,page_new=1,dpi=144,clip_old=None,clip_new=None,threshold=25,aligned=False):
 if not isinstance(threshold,int) or not 0<=threshold<=255:raise ValueError('像素阈值须为0至255整数')
 old,new=Path(old).resolve(),Path(new).resolve();output=Path(output).resolve();hashes=[sha(old),sha(new)]
 with pymupdf.open(old) as a,pymupdf.open(new) as b:
  if a.needs_pass or b.needs_pass:raise ValueError('加密PDF需先提供可读版本')
  if not 1<=page_old<=len(a) or not 1<=page_new<=len(b):raise ValueError('物理页码超出有效范围')
  p,q=a[page_old-1],b[page_new-1];left,lr=raster(p,dpi,clip_old);right,rr=raster(q,dpi,clip_new)
  if output.exists():raise FileExistsError('输出目录已存在，拒绝覆盖')
  output.mkdir(parents=True);left.save(output/'old.png');right.save(output/'new.png')
  report=dict(old=dict(path=str(old),sha256=hashes[0],physical_page=page_old,clip_pt=lr,rotation=p.rotation),new=dict(path=str(new),sha256=hashes[1],physical_page=page_new,clip_pt=rr,rotation=q.rotation),dpi=dpi,threshold=threshold,alignment_confirmed_by_caller=bool(aligned),boundary_verified=False,status='LOCATOR_ONLY',warning='仅定位像素差异。未自动配准；扫描位移、旋转、印章、线宽及噪声都会产生差异。零差异也不证明坐标、面积、政策或有效版本一致。')
  if left.size!=right.size or abs((lr[2]-lr[0])-(rr[2]-rr[0]))>.01 or abs((lr[3]-lr[1])-(rr[3]-rr[1]))>.01:
   report.update(status='NOT_COMPARABLE',reason='页面或裁剪框尺寸不同，未缩放拉伸；请核实同一图幅/比例尺并选同范围')
  else:
   diff=ImageChops.difference(left,right);channels=diff.split();peak=ImageChops.lighter(ImageChops.lighter(channels[0],channels[1]),channels[2]);mask=peak.point(lambda v:255 if v>threshold else 0)
   mask.save(output/'difference.png');count=mask.histogram()[255]
   lg=ImageChops.invert(left.convert('L'));rg=ImageChops.invert(right.convert('L'));overlay=Image.merge('RGB',(ImageChops.invert(lg),ImageChops.invert(rg),ImageChops.invert(ImageChops.darker(lg,rg))));overlay.save(output/'overlay.png')
   report.update(changed_pixels=count,changed_pixel_ratio=count/(left.width*left.height),pixel_bbox=mask.getbbox(),legend='overlay:旧图青色、新图品红、重合近黑；difference:白色为超过阈值的像素。 bbox为渲染图左上起像素坐标，不是测绘坐标。')
 if [sha(old),sha(new)]!=hashes:raise ValueError('读取期间原件发生变化，请重试')
 (output/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');return report

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('old');p.add_argument('new');p.add_argument('--output',required=True);p.add_argument('--page-old',type=int,default=1);p.add_argument('--page-new',type=int,default=1);p.add_argument('--dpi',type=int,default=144);p.add_argument('--threshold',type=int,default=25);p.add_argument('--clip-old',help='x0,y0,x1,y1 PDF点');p.add_argument('--clip-new');p.add_argument('--aligned',action='store_true',help='调用者已经核实图幅/方向/比例/控制点对齐，不代表工具已认证');a=p.parse_args()
 try:
  clip=lambda s:[float(x) for x in s.split(',')] if s else None
  r=compare(a.old,a.new,a.output,a.page_old,a.page_new,a.dpi,clip(a.clip_old),clip(a.clip_new),a.threshold,a.aligned);print(json.dumps(r,ensure_ascii=False));return 2 if r['status']=='NOT_COMPARABLE' else 0
 except (OSError,ValueError,TypeError) as e:print(str(e));return 1
if __name__=='__main__':raise SystemExit(main())
