"""Read-only XLSX facts: raw values, formula cache, visibility, explicit range sums."""
import argparse,hashlib,json,math
from decimal import Decimal
from pathlib import Path
from datetime import datetime,date,time
from openpyxl import load_workbook
from openpyxl.utils.cell import range_boundaries

def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def clean(v):
 if isinstance(v,(datetime,date,time)):return v.isoformat()
 if isinstance(v,float) and not math.isfinite(v):return str(v)
 return v

def inspect(path,ranges=None):
 path=Path(path).resolve();before=sha(path);w=load_workbook(path,data_only=False);cache=load_workbook(path,data_only=True);sheets=[]
 try:
  for s in w:
   cells=[]
   for row in s:
    for c in row:
     if c.value is None:continue
     formula=c.value if c.data_type=='f' else None;v=cache[s.title][c.coordinate].value if formula else c.value
     cells.append(dict(cell=c.coordinate,value=clean(c.value),formula=formula,cached=clean(v) if formula else None,type=c.data_type,number_format=c.number_format,row_hidden=bool(s.row_dimensions[c.row].hidden),column_hidden=any(d.hidden and d.min<=c.column<=d.max for d in s.column_dimensions.values() if d.min and d.max)))
   sheets.append(dict(sheet=s.title,state=s.sheet_state,merged=[str(x) for x in s.merged_cells.ranges],filter=s.auto_filter.ref,print_area=str(s.print_area),cells=cells))
  results=[]
  for item in ranges or []:
   s=w[item['sheet']];cs=cache[s.title];c1,r1,c2,r2=range_boundaries(item['range']);known=Decimal(0);unknown=[];text=[];errors=[];blanks=[]
   for row in s.iter_rows(min_row=r1,max_row=r2,min_col=c1,max_col=c2):
    for c in row:
     v=cs[c.coordinate].value if c.data_type=='f' else c.value
     if c.data_type=='e' or cs[c.coordinate].data_type=='e':errors.append(c.coordinate)
     elif c.data_type=='f' and v is None:unknown.append(c.coordinate)
     elif v is None:blanks.append(c.coordinate)
     elif isinstance(v,bool):text.append(c.coordinate)
     elif isinstance(v,(int,float)) and math.isfinite(v):known+=Decimal(str(v))
     else:text.append(c.coordinate)
   complete=not(unknown or errors or text or blanks)
   results.append(dict(**item,known_numeric_sum=str(known),sum=str(known) if complete else None,complete=complete,uncached_formulas=unknown,text_or_nonnumeric=text,error_cells=errors,blank_cells=blanks))
  if sha(path)!=before:raise ValueError('读取期间原件发生变化，请重新登记')
  return dict(calculation={key:getattr(w.calculation,key) for key in w.calculation.__attrs__} if w.calculation else None,source=str(path),sha256=before,sheets=sheets,ranges=results,note='仅电子表读取与指定范围数值求和；未执行公式、未核业务真实性；空白不当零，文本数字不自动转换。')
 finally:w.close();cache.close()

def save_report(source,out,report):
 source=Path(source).resolve();out=Path(out).resolve()
 if source==out or (out.exists() and source.samefile(out)):raise ValueError('不能覆盖原件')
 with out.open('x',encoding='utf8') as f:json.dump(report,f,ensure_ascii=False,indent=2)

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('input');p.add_argument('--output',required=True);p.add_argument('--ranges',help='JSON数组文件，每项为sheet和range，不猜业务范围');a=p.parse_args()
 try:
  specs=json.loads(Path(a.ranges).read_text(encoding='utf-8-sig')) if a.ranges else []
  if a.ranges and Path(a.output).resolve()==Path(a.ranges).resolve():raise ValueError('不能覆盖范围配置')
  save_report(a.input,a.output,inspect(a.input,specs));print('已保存只读检查报告：'+a.output);return 0
 except (ValueError,OSError,KeyError,TypeError) as e:print(str(e));return 1
if __name__=='__main__':raise SystemExit(main())
