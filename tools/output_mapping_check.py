"""Validate explicit fee/field -> saved Excel cell mappings; never infer ambiguous matches."""
import argparse,json,re,hashlib
from decimal import Decimal,InvalidOperation
from pathlib import Path
from openpyxl import load_workbook

def digest(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def check(path,spec):
 fees=spec.get('fee_ids',[]);fields=spec.get('fields',[])
 if not fees or not fields or any(not isinstance(x,str) or not x.strip() for x in fees+fields) or len(fees)!=len(set(fees)) or len(fields)!=len(set(fields)):raise ValueError('须给出明确、非空、唯一的本轮费用及字段分母')
 path=Path(path).resolve();before=digest(path);w=load_workbook(path,data_only=False);cache=load_workbook(path,data_only=True);seen=set();cells=set();errors=[];checks=[]
 try:
  for e in spec.get('entries',[]):
   key=(e.get('fee_id'),e.get('field'));location=(e.get('sheet'),str(e.get('cell','')).upper())
   if key not in {(a,b) for a in fees for b in fields}:errors.append('映射引用范围外费用或字段');continue
   if key in seen:errors.append('同一费用字段映射重复');continue
   if location in cells:errors.append('不同费用字段落在同一成果单元格');continue
   seen.add(key);cells.add(location)
   if location[0] not in w.sheetnames or not re.fullmatch('[A-Z]+[1-9][0-9]*',location[1]):errors.append('成果表或单元格地址无效');continue
   c=w[location[0]][location[1]];v=cache[location[0]][location[1]].value if c.data_type=='f' else c.value;status='MATCH'
   expected=e.get('expected');kind=e.get('kind')
   if e.get('blank_required') is True:status='MATCH' if v is None else 'DIFFERENCE'
   elif expected is None or v is None:status='PENDING'
   elif kind=='decimal':
    try:
     if isinstance(v,bool) or not isinstance(v,(int,float)) or not isinstance(expected,str):raise ValueError()
     a,b=Decimal(str(v)),Decimal(expected)
     if not a.is_finite() or not b.is_finite():raise ValueError()
     status='MATCH' if a==b else 'DIFFERENCE'
    except (ValueError,InvalidOperation):status='TYPE_ERROR'
   elif kind=='text':status='MATCH' if isinstance(v,str) and v==expected else 'DIFFERENCE'
   else:errors.append('须明确decimal或text比较方式');status='TYPE_ERROR'
   checks.append(dict(fee_id=key[0],field=key[1],sheet=location[0],cell=location[1],formula=c.value if c.data_type=='f' else None,actual=v,expected=expected,status=status))
 finally:w.close();cache.close()
 if digest(path)!=before:raise ValueError('成果文件读取期间改变')
 missing=[dict(fee_id=a,field=b) for a in fees for b in fields if (a,b) not in seen]
 return dict(source=str(path),sha256=before,errors=errors,missing_bindings=missing,checks=checks,mapping_complete=not(errors or missing) and all(x['status']=='MATCH' for x in checks),business_verified=False,note='只验证明确映射及当前保存值。fee_ids分母须回原件独立确认；不得取首个同值候选。公式无缓存为待核，不计算或保存原件。')
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('workbook');p.add_argument('--mapping',required=True);p.add_argument('--output',required=True);a=p.parse_args()
 try:
  out=Path(a.output).resolve()
  if out in {Path(a.workbook).resolve(),Path(a.mapping).resolve()}:raise ValueError('不能覆盖输入')
  r=check(a.workbook,json.loads(Path(a.mapping).read_text(encoding='utf-8-sig')))
  with out.open('x',encoding='utf8') as f:json.dump(r,f,ensure_ascii=False,indent=2,default=str)
  print('映射完整' if r['mapping_complete'] else '存在映射缺口或差异');return 0 if r['mapping_complete'] else 2
 except (OSError,ValueError,TypeError,KeyError) as e:print(str(e));return 1
if __name__=='__main__':raise SystemExit(main())
