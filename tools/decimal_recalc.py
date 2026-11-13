"""Independent Decimal recalculation; rates/factors/rounding must come from current input."""
import argparse,json,re
from decimal import Decimal,ROUND_HALF_UP,InvalidOperation
from pathlib import Path

def num(v):
 if v is None or v=='':return None
 if not isinstance(v,str) or not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)',v):raise ValueError('数值须为普通十进制字符串或null')
 d=Decimal(v)
 if not d.is_finite():raise ValueError('非法数值')
 return d

def quant(v,q):return (v/q).quantize(Decimal('1'),rounding=ROUND_HALF_UP)*q

def calculate(data):
 aq=num(data.get('amount_quantum'));qq=num(data.get('quantity_quantum'));factor=num(data.get('quantity_factor','1'))
 if aq is None or aq<=0 or (qq is not None and qq<=0) or factor is None:raise ValueError('必须明确金额取位步长；数量取位如填写须大于零')
 rows=[];seen=set();mode=data.get('order','AMOUNT_ONLY')
 if mode not in {'AMOUNT_ONLY','QUANTITY_THEN_AMOUNT'} or (mode=='QUANTITY_THEN_AMOUNT' and qq is None):raise ValueError('取位顺序或数量步长不完整')
 for x in data.get('rows',[]):
  if not isinstance(x.get('id'),str) or not x['id'].strip() or x['id'] in seen:raise ValueError('原始行ID缺失或重复，不能自动去重')
  seen.add(x['id']);q=num(x.get('quantity'));p=num(x.get('price'));amount=num(x.get('amount'))
  raw=None if q is None or p is None else q*factor*p
  direct=None if raw is None else quant(raw,aq)
  byqty=None if raw is None or qq is None else quant(quant(q*factor,qq)*p,aq)
  calc=byqty if mode=='QUANTITY_THEN_AMOUNT' else direct
  rows.append(dict(id=x['id'],quantity=x.get('quantity'),price=x.get('price'),submitted=x.get('amount'),source_refs=x.get('source_refs',[]),raw=str(raw) if raw is not None else None,amount_only=str(direct) if direct is not None else None,quantity_then_amount=str(byqty) if byqty is not None else None,calculated=str(calc) if calc is not None else None,difference=str(calc-amount) if calc is not None and amount is not None else None,status='PENDING' if calc is None or amount is None else 'MATCH' if calc==amount else 'DIFFERENCE'))
 def total(key):
  vals=[num(r[key]) for r in rows]
  return None if not vals or any(v is None for v in vals) else sum(vals,Decimal(0))
 rawsum=total('raw');rounded=total('calculated');submitted=total('submitted')
 totals=dict(calculated=str(rounded) if rounded is not None else None,sum_rounded_rows=str(rounded) if rounded is not None else None,round_sum_raw=str(quant(rawsum,aq)) if rawsum is not None else None,submitted=str(submitted) if submitted is not None else None)
 return dict(rows=rows,totals=totals,complete=bool(rows) and all(r['status']!='PENDING' for r in rows),all_arithmetic_matches=bool(rows) and all(r['status']=='MATCH' for r in rows),parameters=dict(amount_quantum=str(aq),quantity_quantum=str(qq) if qq else None,quantity_factor=str(factor),order=mode),business_verified=False,note='只证明本次输入条件下的算术；不选定政策、真实数量或采用额。不混合不同单位/标准形成业务合计。')

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('input');p.add_argument('--output',required=True);a=p.parse_args()
 try:
  src=Path(a.input).resolve();dest=Path(a.output).resolve()
  if src==dest or (dest.exists() and src.samefile(dest)):raise ValueError('不能覆盖输入')
  result=calculate(json.loads(src.read_text(encoding='utf-8-sig')))
  with dest.open('x',encoding='utf8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
  print('已保存复算及差异：'+str(dest));return 0 if result['all_arithmetic_matches'] else 2
 except (OSError,ValueError,KeyError,TypeError,InvalidOperation) as e:print(str(e));return 1
if __name__=='__main__':raise SystemExit(main())
