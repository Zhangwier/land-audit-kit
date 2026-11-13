"""Rebuild village/category summaries on a copied land template."""
from pathlib import Path
from copy import copy
import argparse
import json
from openpyxl import load_workbook
from openpyxl.formula.translate import Translator

DETAIL = '补偿审核明细表'
SUMMARY = '补偿审核汇总表'
VILLAGE, CATEGORY = 'BO', 'BP'
START = 6
SOURCES = {'D': 'C', 'F': 'K', 'G': 'L', 'I': 'N', 'J': 'O', 'K': 'P',
           'M': 'X', 'N': 'Y', 'P': 'AA', 'Q': 'AB', 'R': 'AU', 'S': 'AY',
           'T': 'AZ', 'U': 'BA', 'V': 'BE', 'W': 'BF', 'AD': 'BH', 'AE': 'BI'}
AUDITED = {'P', 'AL', 'AP', 'BE', 'X', 'Y', 'AA', 'AB', 'AO', 'AR', 'AS', 'BF', 'BI'}
AUDIT_INPUTS = {'W', 'Z', 'AL', 'AN', 'AP', 'AQ', 'BE', 'BD'}
COMBINED = {'X': ['AD', 'AH'], 'Z': ['AG', 'AJ'],
            'AA': ['AL', 'AP'], 'AC': ['AO', 'AR']}
AUDITED_SUMMARY = ({col for col, source in SOURCES.items() if source in AUDITED}
                   | {col for col, sources in COMBINED.items() if any(source in AUDITED for source in sources)}
                   | {'AF'})
LEGACY_FORMULAS = {
    'C': '=IF(A6="","",SUM(D6:I6))', 'P': '=IF(A6="","",SUM(Q6:V6))',
    'W': '=IF(A6="","",J6)', 'X': '=IF(A6="","",ROUND(P6*W6,2))',
    'Y': '=IF(OR(A6="",$BN$2=""),"",ROUND(P6*$BN$2,4))',
    'Z': '=IF(A6="","",M6)', 'AA': '=IF(OR(Y6="",Z6=""),"",ROUND(Y6*Z6,2))',
    'AB': '=IF(A6="","",IF(AND(C6>0,Y6=""),"",SUM(X6,AA6)))',
    'AO': '=IF(A6="","",ROUND(AL6*AN6,2))',
    'AR': '=IF(A6="","",ROUND(AP6*AQ6,2))',
    'BF': '=IF(A6="","",ROUND(BE6*BD6,2))',
    'AS': '=IF(A6="","",SUM(AO6,AR6))',
    'BI': '=IF(A6="","",IF(AND(C6>0,AB6=""),"",SUM(AB6,AS6,BF6)))',
    'AC': '=IF(AB6="","",ROUND(AB6-O6,2))',
    'AT': '=IF(A6="","",ROUND(AS6-AK6,2))',
    'BG': '=IF(A6="","",ROUND(BF6-AZ6,2))',
    'BJ': '=IF(BI6="","",ROUND(BI6-BH6,2))',
    **{target: f'=IF(A6="","",{source}6)' for target, source in
       [('AL', 'AD'), ('AN', 'AF'), ('AP', 'AH'), ('AQ', 'AI'), ('BD', 'AX'), ('BE', 'AY')]},
}


def _detail_guards(detail, last):
    """Keep missing quantities/rates distinguishable from explicit zero."""
    for r in range(START, last + 1):
        def put(col, body):
            cell = detail[f'{col}{r}']
            # A cleared audit input is a deliberate pending value; rebuilding
            # must not silently restore its submitted-value link.
            if col in AUDIT_INPUTS and cell.value in (None, ''):
                return
            desired = '=' + body
            legacy = Translator(LEGACY_FORMULAS[col], origin=col + '6').translate_formula(f'{col}{r}')
            previous_guard = desired
            if col == 'P':
                previous_guard = f'=IF(C{r}="","",SUM(Q{r}:V{r}))'
            if col in {'AL', 'AP', 'BE'}:
                source = {'AL': 'AD', 'AP': 'AH', 'BE': 'AY'}[col]
                previous_guard = f'=IF(OR(A{r}="",{source}{r}=""),"",{source}{r})'
            for audited, submitted in {'AO': [('AL', 'AD'), ('AN', 'AF')],
                                       'AR': [('AP', 'AH'), ('AQ', 'AI')],
                                       'BF': [('BE', 'AY'), ('BD', 'AX')]}.get(col, []):
                previous_guard = previous_guard.replace(f'{audited}{r}', f'{submitted}{r}')
            # Rebuilding a summary must preserve manually adopted audit values
            # and non-template formulas, including adjusted quantities/rates.
            if cell.value not in (None, '', desired, legacy, previous_guard):
                return
            cell.value = desired
        put('C', f'IF(A{r}="","",IF(COUNT(D{r}:I{r})=0,"",SUM(D{r}:I{r})))')
        put('P', f'IF(OR(C{r}="",COUNT(Q{r}:V{r})=0),"",SUM(Q{r}:V{r}))')
        put('W', f'IF(OR(A{r}="",J{r}=""),"",J{r})')
        put('X', f'IF(OR(A{r}="",P{r}="",AND(P{r}<>0,W{r}="")),"",IF(P{r}=0,0,ROUND(P{r}*W{r},2)))')
        put('Y', f'IF(OR(A{r}="",P{r}="",AND(P{r}<>0,$BN$2="")),"",IF(P{r}=0,0,ROUND(P{r}*$BN$2,4)))')
        put('Z', f'IF(OR(A{r}="",M{r}=""),"",M{r})')
        put('AA', f'IF(OR(A{r}="",Y{r}="",AND(Y{r}<>0,Z{r}="")),"",IF(Y{r}=0,0,ROUND(Y{r}*Z{r},2)))')
        put('AB', f'IF(OR(A{r}="",X{r}="",AA{r}=""),"",SUM(X{r},AA{r}))')
        # A submitted fee with missing quantity/rate remains unknown. A blank
        # quantity with no submitted fee is an absent category, not a finding.
        for out, quantity, price, submitted in [('AO', 'AL', 'AN', 'AG'),
                                                ('AR', 'AP', 'AQ', 'AJ'),
                                                ('BF', 'BE', 'BD', 'AZ')]:
            q, p, s = (f'{c}{r}' for c in (quantity, price, submitted))
            put(out, f'IF(A{r}="","",IF(AND({q}="",OR({s}="",{s}=0)),0,IF(OR({q}="",AND({q}<>0,{p}="")),"",IF({q}=0,0,ROUND({q}*{p},2)))))')
        for target, source in [('AL', 'AD'), ('AN', 'AF'), ('AP', 'AH'),
                               ('AQ', 'AI'), ('BD', 'AX'), ('BE', 'AY')]:
            if target in {'AL', 'AP', 'BE'}:
                submitted = {'AL': 'AG', 'AP': 'AJ', 'BE': 'AZ'}[target]
                put(target, f'IF(A{r}="","",IF(AND({source}{r}="",OR({submitted}{r}="",{submitted}{r}=0)),0,IF({source}{r}="","",{source}{r})))')
            else:
                put(target, f'IF(OR(A{r}="",{source}{r}=""),"",{source}{r})')
        put('AS', f'IF(OR(A{r}="",AO{r}="",AR{r}=""),"",SUM(AO{r},AR{r}))')
        put('BI', f'IF(OR(A{r}="",AB{r}="",AS{r}="",BF{r}=""),"",SUM(AB{r},AS{r},BF{r}))')
        for target, audited, submitted in [('AC', 'AB', 'O'), ('AT', 'AS', 'AK'),
                                            ('BG', 'BF', 'AZ'), ('BJ', 'BI', 'BH')]:
            put(target, f'IF({audited}{r}="","",ROUND({audited}{r}-{submitted}{r},2))')
    # A missing applicable row cannot disappear inside a numeric total.
    total = last + 1
    for col in AUDITED | {'AC', 'AT', 'BG', 'BJ'}:
        cell = detail[f'{col}{total}']
        desired = (f'=IF(OR(COUNTA(A{START}:A{last})=0,'
                   f'COUNTIFS(A{START}:A{last},"<>",{col}{START}:{col}{last},"")>0),'
                   f'"",SUM({col}{START}:{col}{last}))')
        legacy = f'=IF(COUNTA(A{START}:A{last})=0,"",SUM({col}{START}:{col}{last}))'
        if cell.value in (None, '', desired, legacy):
            cell.value = desired


def install(workbook, detail_end_row, fill=False):
    """Install summaries for rows 6..detail_end_row, excluding the next total."""
    if isinstance(detail_end_row, bool) or not isinstance(detail_end_row, int) or not START <= detail_end_row < 1048576:
        raise ValueError('detail_end_row must be an integer from 6 to 1048575')
    detail, summary = workbook[DETAIL], workbook[SUMMARY]
    last = detail_end_row
    if detail[f'A{last + 1}'].value != '合计':
        raise ValueError('The row immediately after detail_end_row must be the detail 合计 row')
    for r in range(START, last + 1):
        if detail[f'A{r}'].value == '合计' or str(detail[f'A{r}'].value or '').startswith('填表说明'):
            raise ValueError(f'Non-detail row {r} is inside the declared range')
    totals = [r for r in range(START, summary.max_row + 1) if summary[f'A{r}'].value == '合计']
    if len(totals) != 1 or totals[0] < START + 3:
        raise ValueError('Summary must contain exactly one 合计 row after at least three data rows')
    groups = []
    if fill:
        for r in range(START, last + 1):
            if not detail[f'A{r}'].value:
                continue
            village, category = detail[f'{VILLAGE}{r}'].value, detail[f'{CATEGORY}{r}'].value
            if not str(village or '').strip() or not str(category or '').strip():
                raise ValueError(f'Row {r} needs explicit village and category metadata')
            key = (str(village).strip(), str(category).strip())
            detail[f'{VILLAGE}{r}'], detail[f'{CATEGORY}{r}'] = key
            if key not in groups:
                groups.append(key)
    detail[f'{VILLAGE}5'] = '行政村（汇总对应）'
    detail[f'{CATEGORY}5'] = '地类/汇总类别（依据明细）'
    for col in (VILLAGE, CATEGORY):
        detail[f'{col}5']._style = copy(detail['BN5']._style)
        detail.column_dimensions[col].width = 22
    _detail_guards(detail, last)
    count = max(3, len(groups))
    total, old_total = START + count, totals[0]
    styles = [copy(c._style) for c in summary[START]]
    height = summary.row_dimensions[START].height
    if total > old_total:
        summary.insert_rows(old_total, total - old_total)
    elif total < old_total:
        summary.delete_rows(total, old_total - total)
    # Clear the generated region, including obsolete input rate cells.
    for r in range(START, total + 1):
        summary.row_dimensions[r].height = height
        for c in range(1, 33):
            summary.cell(r, c).value = None
            summary.cell(r, c)._style = copy(styles[c - 1])
    quote = "'" + DETAIL + "'!"
    vr, cr, ids = (f'{quote}${col}${START}:${col}${last}' for col in (VILLAGE, CATEGORY, 'A'))
    def amount(col, r):
        rg = f'{quote}${col}${START}:${col}${last}'
        guards = [f'$B{r}=""', f'$C{r}=""', f'COUNTIFS({vr},$B{r},{cr},$C{r},{ids},"<>")=0']
        if col in AUDITED:
            guards.append(f'COUNTIFS({vr},$B{r},{cr},$C{r},{ids},"<>",{rg},"")>0')
        return f'IF(OR({",".join(guards)}),"",SUMIFS({rg},{vr},$B{r},{cr},$C{r},{ids},"<>"))'
    for r in range(START, total):
        if fill and r - START < len(groups):
            summary[f'B{r}'], summary[f'C{r}'] = groups[r - START]
        summary[f'A{r}'] = f'=IF(B{r}="","",ROW()-5)'
        for col, source in SOURCES.items():
            summary[f'{col}{r}'] = '=' + amount(source, r)
        for col, sources in COMBINED.items():
            terms = [amount(source, r) for source in sources]
            missing = ','.join(term + '=""' for term in terms)
            summary[f'{col}{r}'] = f'=IF(OR({missing}),"",SUM({",".join(terms)}))'
        summary[f'AF{r}'] = f'=IF(AE{r}="","",ROUND(AE{r}-AD{r},2))'
    summary[f'A{total}'] = '合计'
    for col in SOURCES.keys() | COMBINED.keys() | {'AF'}:
        guards = [f'COUNTA(B{START}:B{total-1})=0']
        if col in AUDITED_SUMMARY:
            guards.append(f'COUNTIFS(B{START}:B{total-1},"<>",{col}{START}:{col}{total-1},"")>0')
        summary[f'{col}{total}'] = f'=IF(OR({",".join(guards)}),"",SUM({col}{START}:{col}{total-1}))'
    summary.print_area = f'A1:AF{total}'
    summary.sheet_view.view = 'pageBreakPreview'
    return {'groups': groups, 'last_detail_row': last, 'summary_total_row': total}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input')
    parser.add_argument('--output', required=True)
    parser.add_argument('--fill-summary', action='store_true')
    parser.add_argument('--detail-end-row', required=True, type=int)
    args = parser.parse_args(argv)
    source, output = Path(args.input).resolve(), Path(args.output).resolve()
    if source == output or output.exists():
        raise ValueError('Output must be a new file distinct from the input')
    workbook = load_workbook(source)
    try:
        result = install(workbook, args.detail_end_row, args.fill_summary)
        workbook.save(output)
    finally:
        workbook.close()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
