"""Set up A4 printing without moving calculation cells."""
from __future__ import annotations

import argparse
from copy import copy
import hashlib
import json
from pathlib import Path
import re

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.worksheet.dimensions import ColumnDimension
from openpyxl.worksheet.pagebreak import Break, ColBreak, RowBreak

ROOT = Path(__file__).resolve().parents[1]
PREFIX = '打印_'
PT_PER_MM = 72 / 25.4


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load(path):
    return json.loads(Path(path).read_text('utf-8-sig'))


def linked(sheet, cell):
    name = sheet.replace("'", "''")
    ref = f"'{name}'!{cell}"
    return f'=IF({ref}="","",{ref})'


def page_setup(sheet, landscape=True):
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.orientation = 'landscape' if landscape else 'portrait'
    sheet.page_setup.scale = 100
    sheet.sheet_properties.pageSetUpPr.fitToPage = False
    sheet.page_setup.fitToWidth = 0
    sheet.page_setup.fitToHeight = 0
    for edge in ('left', 'right', 'top', 'bottom'):
        setattr(sheet.page_margins, edge, round(10 / 25.4, 6))
    sheet.page_margins.header = sheet.page_margins.footer = round(5 / 25.4, 6)
    # A repeated worksheet title is reliable across Excel and WPS; it occupies
    # measured rows, rather than a printer-dependent floating header.
    sheet.oddHeader.center.text = ''
    sheet.oddFooter.center.text = '&P'
    sheet.oddFooter.center.font = '宋体'
    sheet.oddFooter.center.size = 12
    sheet.sheet_view.view = 'pageBreakPreview'


def land_layout(sheet, profile, end, title, config, categories):
    start, total = profile['start_row'], end + 1
    if sheet[f'A{total}'].value != '合计':
        raise ValueError(f'{sheet.title}: explicit end must precede 合计')
    summary = sheet.title == '补偿审核汇总表'
    tree = 'tree' in categories
    crop = 'crop' in categories
    pond = 'pond' in categories
    right = 32 if summary else 63
    original = {}
    for key, dim in list(sheet.column_dimensions.items()):
        first = dim.min or column_index_from_string(key)
        for col in range(first, (dim.max or first) + 1):
            original[col] = dim
    # Apply visibility to individual columns, including columns originally
    # covered by one grouped dimension.
    sheet.column_dimensions.clear()
    if summary:
        hidden = set(range(18, 24)) if not tree else set()
        if not (crop or pond):
            hidden.update(range(24, 30))
    else:
        hidden = set(range(64, 69))
        for present, columns in ((crop, (30,31,32,33,38,39,40,41)),
                                 (pond, (34,35,36,42,43,44)),
                                 (crop or pond, (37,45,46)),
                                 (tree, range(47,60))):
            if not present:
                hidden.update(columns)
        for submitted, adopted in zip(range(4,10), range(17,23)):
            if not any(sheet.cell(row, col).value not in (None, '', 0)
                       and sheet.cell(row, col).data_type != 'f'
                       for row in range(start, end + 1) for col in (submitted, adopted)):
                hidden.update(col for col in (submitted, adopted)
                              if col in original and original[col].hidden)
    for col in range(1, (32 if summary else 68) + 1):
        letter = get_column_letter(col)
        prior = original.get(col)
        dim = copy(prior) if prior is not None else ColumnDimension(sheet, width=10)
        dim.index = letter
        dim.min = dim.max = col
        dim.hidden = col in hidden
        sheet.column_dimensions[letter] = dim

    page_setup(sheet)
    sheet.sheet_view.showZeros = False
    sheet.page_margins.top = round(15 / 25.4, 6)
    for edge, mm in config.get('margins_mm', {}).items():
        if edge not in {'left', 'right', 'top', 'bottom'} or not 0 <= float(mm) <= 50:
            raise ValueError('Invalid margin override')
        setattr(sheet.page_margins, edge, round(float(mm) / 25.4, 6))
    sheet.print_area = f'A2:{get_column_letter(right)}{total}'
    sheet.print_title_rows = '2:5'
    sheet.print_title_cols = None if summary else 'A:B'
    sheet.row_breaks, sheet.col_breaks = RowBreak(), ColBreak()
    sheet.page_setup.pageOrder = 'downThenOver'
    sheet.page_setup.id = None
    sheet.print_options.horizontalCentered = True
    sheet.HeaderFooter.scaleWithDoc = False
    suffix = '审核汇总表' if summary else '审核明细表'
    sheet['A1'] = heading = title + suffix
    sheet['A1'].font = Font(name='宋体', size=22, bold=True)
    sheet['A1'].alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    sheet.row_dimensions[1].height = max(sheet.row_dimensions[1].height or 0, 30)
    sheet.oddHeader.center.text = heading
    sheet.oddHeader.center.font = '宋体,Bold'
    sheet.oddHeader.center.size = 22
    sheet.oddFooter.center.text = '第 &P 页'

    for row in sheet.iter_rows(min_row=2, max_row=total, max_col=right):
        for cell in row:
            if cell.font.sz != 12:
                font = copy(cell.font)
                font.sz = 12
                cell.font = font
            if cell.row < start and cell.value is not None:
                align = copy(cell.alignment)
                align.horizontal = align.vertical = 'center'
                align.wrap_text = True
                cell.alignment = align
    captions = [(1, '征地及留用地补偿')]
    if summary:
        sheet.page_setup.scale = None
        sheet.page_setup.fitToWidth = sheet.page_setup.fitToHeight = 1
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
    else:
        for row in range(start, total + 1):
            dim = sheet.row_dimensions[row]
            dim.height = max(29.25, dim.height or sheet.sheet_format.defaultRowHeight)
        if crop or pond:
            sheet['AD2'] = '青苗及鱼塘搬迁补偿' if pond else '基础青苗补偿'
            captions.append((30, sheet['AD2'].value))
        if tree:
            captions.append((47, '竹果木补偿'))
        for first, _ in captions[1:]:
            sheet.col_breaks.append(Break(id=first - 1, max=total - 1))
        sheet.page_setup.fitToWidth = sheet.page_setup.fitToHeight = None
    sections = []
    for index, (first, caption) in enumerate(captions):
        last = captions[index + 1][0] - 1 if index + 1 < len(captions) else right
        cols = list(range(first, last + 1))
        if first > 1:
            cols = [1, 2] + cols
        headers = [caption]
        if not summary and last == right:
            headers.append('补偿金额合计')
        sections.append({'first': first, 'last': last, 'caption': caption, 'headers': headers,
                         'columns': [col for col in cols if col not in hidden]})
    if not summary:
        width = max(sum(sheet.column_dimensions[get_column_letter(col)].width * 7 + 5
                        for col in s['columns']) for s in sections)
        budget = 297 * PT_PER_MM - (sheet.page_margins.left + sheet.page_margins.right) * 72
        sheet.page_setup.scale = min(100, max(10, int(0.84 * budget / (width * .75) * 100)))
    margins = {edge: getattr(sheet.page_margins, edge) * 25.4 for edge in ('left','right','top','bottom')}
    margins['top'] = sheet.page_margins.header * 25.4
    return {'sheet': sheet.title, 'category': 'summary' if summary else 'detail', 'land_layout': True,
            'title': heading, 'orientation': 'landscape', 'sections': sections, 'data_start': start,
            'total_row': total, 'margins_mm': margins}


def line_count(value, width_mm, size=12):
    text = str(value or '')
    # Conservative CJK/Latin physical widths, plus cell padding.
    capacity = max(1, (width_mm * PT_PER_MM - 6) / size)
    return max(1, sum(max(1, int(sum(1 if ord(c) > 255 else .6 for c in part) / capacity) + 1)
                      for part in text.split('\n')))


def title_rows(sheet, text, width_mm):
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=sheet.max_column)
    cell = sheet.cell(1, 1, text)
    cell.font = Font(name='宋体', size=22, bold=True)
    cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    sheet.row_dimensions[1].height = line_count(text, width_mm, 22) * 28 + 9
    sheet.row_dimensions[2].height = 9  # >= 3 mm title/table separation


def paginate(sheet, first, last, total, header_end, landscape=True):
    height = (210 if landscape else 297) * PT_PER_MM
    budget = height - (sheet.page_margins.top + sheet.page_margins.bottom) * 72 - 18
    headers = sum(sheet.row_dimensions[r].height or 18 for r in range(1, header_end + 1))
    used = headers
    breaks = []
    for r in range(first, last + 1):
        row_height = sheet.row_dimensions[r].height or 18
        # Keep the last data row together with its total.
        reserve = (sheet.row_dimensions[total].height or 18) if total and r == last else 0
        if row_height + reserve + headers > budget:
            raise ValueError(f'{sheet.title}: row {r} is taller than an A4 page; split the text explicitly')
        if used + row_height + reserve > budget:
            sheet.row_breaks.append(Break(id=r - 1))
            breaks.append(r)
            used = headers
        used += row_height
    ranges = []
    starts = [first] + breaks
    for i, start in enumerate(starts):
        end = starts[i + 1] - 1 if i + 1 < len(starts) else last
        ranges.append([start, total if total and i == len(starts) - 1 else end])
    return ranges


def make_band(book, source, profile, band, end, title, config):
    columns = band['columns']
    labels = band['labels']
    widths = band['widths_mm']
    total_width = sum(widths)
    margins = config.get('margins_mm', {})
    if total_width > 297 - margins.get('left', 10) - margins.get('right', 10) - 3 + 1e-3:
        raise ValueError('Print band exceeds A4 width at 100%')
    name = PREFIX + band['id']
    sheet = book.create_sheet(name)
    page_setup(sheet)
    for edge, value in margins.items():
        if edge not in {'left', 'right', 'top', 'bottom'} or not 0 <= float(value) <= 50:
            raise ValueError('Invalid margin override')
        setattr(sheet.page_margins, edge, round(float(value) / 25.4, 6))
    side = Side(style='thin', color='000000')
    border = Border(left=side, right=side, top=side, bottom=side)
    for col, (label, width) in enumerate(zip(labels, widths), 1):
        sheet.column_dimensions[get_column_letter(col)].width = (width * PT_PER_MM - 5) / 7
        c = sheet.cell(3, col, label)
        c.font = Font(name='宋体', size=12, bold=True)
        c.border = border
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    title_rows(sheet, title + ' ' + band['title'], total_width)
    sheet.row_dimensions[3].height = max(line_count(t, w) for t, w in zip(labels, widths)) * 18 + 6
    start = profile['start_row']
    # Include every declared record, even when it has a zero fee in this band.
    records = [r for r in range(start, end + 1) if source[f'{profile["key_column"]}{r}'].value not in (None, '')]
    if not records:
        records = [start]  # editable blank mother template
    rows = records + [end + 1]
    mapping = []
    for out, src in enumerate(rows, 4):
        values = []
        for c, letter in enumerate(columns, 1):
            original = source[f'{letter}{src}']
            target = sheet.cell(out, c, linked(source.title, original.coordinate))
            is_money = letter in band.get('money_columns', []) or any(term in labels[c - 1] for term in ('金额', '核增减'))
            target.number_format = '0.00' if is_money else original.number_format
            target.font = Font(name='宋体', size=12, bold=src == end + 1)
            target.border = border
            target.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            mapping.append({'print_cell': target.coordinate, 'source_cell': original.coordinate})
            values.append(original.value if original.data_type != 'f' else '00000000.00')
        sheet.row_dimensions[out].height = max(24, max(line_count(v, w) for v, w in zip(values, widths)) * 18 + 6)
    total_row = len(rows) + 3
    if source[f'{profile["key_column"]}{end + 1}'].value != '合计':
        raise ValueError(f'{source.title}: explicit end must precede 合计')
    # Some summary templates merge the key across two columns. Print a clear
    # total label rather than leaving the linked second key blank.
    sheet.cell(total_row, 1, '合计')
    sheet.print_area = f'A1:{get_column_letter(len(columns))}{total_row}'
    sheet.print_title_rows = '1:3'
    pages = paginate(sheet, 4, total_row - 1, total_row, 3)
    return {'sheet': name, 'source_sheet': source.title, 'category': band['category'],
            'title': sheet['A1'].value, 'header_row': 3, 'total_row': total_row,
            'data_start': 4, 'data_end': total_row - 1, 'source_rows': records,
            'columns': columns, 'labels': labels, 'widths_mm': widths, 'mapping': mapping,
            'page_rows': pages, 'orientation': 'landscape', 'font_pt': 12,
            'title_pt': 22, 'margins_mm': {k: getattr(sheet.page_margins, k) * 25.4 for k in ('left', 'right', 'top', 'bottom')}}


def materials_view(book, source, profile, config):
    stage = config['stage'].upper()
    if stage not in {'INITIAL', 'PAYMENT', 'SETTLEMENT'}:
        raise ValueError('Unknown stage')
    categories = set(config['categories'])
    visible = set()
    for group in profile['row_groups']:
        if stage in group['stages'] and categories.intersection(group['categories']):
            visible.update(range(group['start'], group['end'] + 1))
    for r in range(7, 75):
        source.row_dimensions[r].hidden = r not in visible
    sheet = book.create_sheet(PREFIX + '资料符合性')
    page_setup(sheet, False)
    sheet.page_margins.left = sheet.page_margins.right = round(5 / 25.4, 6)
    margins = config.get('margins_mm', {})
    for edge, value in margins.items():
        if edge not in {'left', 'right', 'top', 'bottom'} or not 0 <= float(value) <= 50:
            raise ValueError('Invalid margin override')
        setattr(sheet.page_margins, edge, round(float(value) / 25.4, 6))
    # Give the document-name column the available width. The receipt columns
    # only need enough room for three-character headings and single marks.
    widths = [19, 12, 0, 15, 15, 35]
    available = 207 - (sheet.page_margins.left + sheet.page_margins.right) * 25.4
    widths[2] = round(available - sum(widths), 2)
    if widths[2] < 82:
        raise ValueError('Materials name column is too narrow for A4; use smaller side margins')
    for i, w in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(i)].width = (w * PT_PER_MM - 5) / 7
    for r in range(1, 4):
        sheet.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
    texts = [source['A2'].value or '资料符合性核查表', source['A1'].value, source['A3'].value]
    for r, text in enumerate(texts, 1):
        c = sheet.cell(r, 1, text)
        c.font = Font(name='宋体', size=22 if r == 1 else 12, bold=r == 1)
        c.alignment = Alignment(horizontal='center' if r < 3 else 'left', vertical='center', wrap_text=True)
        sheet.row_dimensions[r].height = line_count(text, sum(widths), c.font.sz) * (28 if r == 1 else 18) + 9
    border = Border(**{k: Side(style='thin', color='000000') for k in ('left', 'right', 'top', 'bottom')})
    labels = [source.cell(4, c).value or '' for c in range(1, 7)]
    for col in (4, 5):
        marks = '／'.join(str(source.cell(r, col).value or '') for r in (5, 6))
        labels[col - 1] += '\n' + marks
    if source['F5'].value:
        labels[5] += '\n' + str(source['F5'].value)
    mapping = []
    selected = sorted(visible)
    for out, src in enumerate([None] + selected, 4):
        values = []
        for c in range(1, 7):
            original = source.cell(src, c) if src else None
            value = labels[c - 1] if src is None else original.value
            # Repeat the stage label instead of merging it across hidden rows.
            if src and c == 1:
                value = '初审资料' if stage == 'INITIAL' else '审核资料'
            cell = sheet.cell(out, c, linked(source.title, original.coordinate) if src and c != 1 else value)
            cell.font = Font(name='宋体', size=12, bold=not src or isinstance(source.cell(src, 2).value, str))
            cell.border = border
            cell.alignment = Alignment(horizontal='left' if c in {3, 6} else 'center', vertical='center', wrap_text=True)
            if src and c != 1:
                mapping.append({'print_cell': cell.coordinate, 'source_cell': original.coordinate})
            values.append(value if value is not None else '')
        sheet.row_dimensions[out].height = max(24, max(line_count(v, w) for v, w in zip(values, widths)) * 18 + 6)
    sheet.print_title_rows = '1:4'
    sheet.print_area = f'A1:F{sheet.max_row}'
    ranges = paginate(sheet, 5, sheet.max_row, None, 4, False)
    return {'sheet': sheet.title, 'source_sheet': source.title, 'category': 'materials',
            'title': texts[0], 'header_row': 4, 'total_row': None, 'data_start': 5,
            'data_end': sheet.max_row, 'source_rows': selected, 'columns': list('ABCDEF'),
            'labels': labels, 'widths_mm': widths, 'mapping': mapping, 'page_rows': ranges,
            'orientation': 'portrait', 'font_pt': 12, 'title_pt': 22,
            'margins_mm': {k: getattr(sheet.page_margins, k) * 25.4 for k in ('left', 'right', 'top', 'bottom')}}


def build(source_path, output, config, layout_output=None):
    source_path, output = Path(source_path).resolve(), Path(output).resolve()
    layout_output = Path(layout_output or str(output) + '.layout.json').resolve()
    if source_path == output or output.exists() or layout_output.exists() or layout_output == output:
        raise FileExistsError('Use new workbook and layout paths')
    manifest = load(ROOT / 'templates/template_manifest.json')
    item = next((i for i in manifest['items'] if i['category'] == config['template_category']), None)
    if item is None or 'print_profiles' not in item:
        raise ValueError('Unknown printable template category')
    categories = set(config['categories'])
    if not categories or not categories <= {'land', 'crop', 'pond', 'tree', 'building', 'grave'}:
        raise ValueError('Explicit supported categories required')
    before = sha(source_path)
    book = load_workbook(source_path)
    for s in list(book):
        if s.title.startswith(PREFIX):
            book.remove(s)
    result = {'schema_version': 1, 'source': str(source_path), 'source_sha256': before,
              'workbook': str(output), 'config': config, 'sheets': []}
    project, batch = config['project_name'], config['batch']
    title = config.get('title', batch if batch.startswith(project) else project + batch)
    land = config['template_category'] in {'land_tree', 'land_pond'}
    if land:
        if 'land' not in categories or (config['template_category'] == 'land_pond' and 'tree' in categories):
            raise ValueError('Land print categories do not match the selected template')
        if book.sheetnames != [p['sheet'] for p in item['print_profiles']]:
            raise ValueError('Land input must contain only the summary and detail calculation sheets')
        compensation = config['compensation_name']
        if not all(part.strip() for part in (title, batch, compensation)) or '【' in title + compensation:
            raise ValueError('Fill in the actual project, batch and compensation name')
        title += compensation.removeprefix(title).removeprefix(batch)
    for p in item['print_profiles']:
        source_name = p['sheet']
        if p.get('row_groups') and source_name not in book and len(book.worksheets) == 1:
            original_form = book.worksheets[0]
            if original_form['C4'].value == '资料名称' and original_form['F4'].value == '说明':
                source_name = original_form.title
        s = book[source_name]
        for row in s:
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith('填表说明'):
                    cell.value = None
        if config['template_category'] == 'grave' and '明细' in s.title:
            if all(c.value is None for c in s[2]):
                s.row_dimensions[2].hidden = True
        if p.get('row_groups'):
            # The original source retains stable receipt-cell addresses.
            for r in (1, 2, 3):
                for c in s[r]:
                    c.border = Border()
            s['A1'] = '项目名称：' + config['project_name']
            if not s['A2'].value:
                s.row_dimensions[2].hidden = True
            s['A3'] = '补偿项目：' + config['compensation_name']
            result['sheets'].append(materials_view(book, s, p, config))
            continue
        end = config.get('end_rows', {}).get(p['sheet'], p['default_end_row'])
        if isinstance(end, bool) or not isinstance(end, int) or end < p['start_row']:
            raise ValueError('Invalid explicit data end')
        if land:
            result['sheets'].append(land_layout(s, p, end, title, config, categories))
            continue
        for band in p['bands']:
            if band['category'] == 'common' or categories.intersection(band.get('categories', [band['category']])):
                result['sheets'].append(make_band(book, s, p, band, end, title, config))
    if not result['sheets']:
        raise ValueError('No matching print bands')
    book.active = book.sheetnames.index(result['sheets'][0]['sheet'])
    for s in book:
        s.sheet_view.tabSelected = s.title == result['sheets'][0]['sheet']
    if sha(source_path) != before:
        raise ValueError('Input changed while building')
    output.parent.mkdir(parents=True, exist_ok=True)
    layout_output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents a race with another producer.
    try:
        with output.open('xb') as stream:
            book.save(stream)
        result['workbook_sha256'] = sha(output)
        with layout_output.open('x', encoding='utf8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
    finally:
        book.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input'); parser.add_argument('--output', required=True)
    parser.add_argument('--config', required=True); parser.add_argument('--layout-output')
    args = parser.parse_args()
    result = build(args.input, args.output, load(args.config), args.layout_output)
    print(json.dumps({'workbook': result['workbook'], 'print_sheets': len(result['sheets'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
