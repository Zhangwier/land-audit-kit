"""Recalculate new candidates and export registered print sheets in private Office instances."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

from print_layout import load, sha

MATERIALS_FILENAME = '征地拆迁资料符合性核查表（改）.xlsx'


def candidate_name(kind, suffix):
    return MATERIALS_FILENAME if kind == 'materials' and suffix == '.xlsx' else kind + suffix


def private_instance(progid):
    import os
    import win32api
    import win32com.client
    import win32process
    import pywintypes
    before = set(win32process.EnumProcesses())
    app = win32com.client.DispatchEx(progid)
    try:
        _, pid = win32process.GetWindowThreadProcessId(app.Hwnd)
    except AttributeError:
        # Word does not expose Application.Hwnd on every installed version.
        # Identify the newly created executable without opening a document in
        # a potentially shared instance.
        expected = 'WINWORD.EXE' if progid == 'Word.Application' else 'WPS.EXE'
        matches = []
        for new_pid in set(win32process.EnumProcesses()) - before:
            try:
                handle = win32api.OpenProcess(0x410, False, new_pid)
                try:
                    name = win32process.GetModuleFileNameEx(handle, 0)
                    if os.path.basename(name).upper() == expected:
                        matches.append(new_pid)
                finally:
                    handle.Close()
            except (OSError, pywintypes.error):
                continue
        if len(matches) > 1 and progid == 'KWPS.Application':
            # WPS starts a new host plus a new child renderer. The root must
            # also be new; an existing user host is never accepted.
            wmi = win32com.client.GetObject('winmgmts:')
            parents = {int(p.ProcessId): int(p.ParentProcessId) for p in wmi.ExecQuery('SELECT ProcessId, ParentProcessId FROM Win32_Process')}
            roots = [p for p in matches if parents.get(p) not in matches]
            if len(roots) == 1:
                matches = roots
        if len(matches) != 1:
            raise RuntimeError('Cannot identify a unique new Office process; existing sessions left untouched')
        pid = matches[0]
    if pid in before:
        raise RuntimeError('Office returned an existing user process; left untouched')
    return app, pid


def calibrated_width(sheet, col, mm):
    column = sheet.Columns(col)
    desired = mm * 72 / 25.4
    for _ in range(4):
        current = float(column.Width)
        if abs(current - desired) < .6:
            break
        column.ColumnWidth = float(column.ColumnWidth) * desired / current
    if abs(float(column.Width) - desired) > 1.5:
        raise ValueError('Could not set physical column width')


def prepare_land_print(sheet, spec):
    if spec['category'] == 'summary':
        return  # already configured as one page wide and one page tall
    total = spec['total_row']
    intended = [s['first'] for s in spec['sections'][1:]]
    # The active printer may insert a break before a manual break. Reduce
    # scale only when that actually happens in the selected Office host.
    for scale in range(int(sheet.PageSetup.Zoom), 9, -1):
        sheet.PageSetup.Zoom = scale
        actual = [br.Location.Column for br in sheet.VPageBreaks]
        if actual == intended:
            break
    else:
        raise ValueError(f"{sheet.Name}: print columns cannot fit in the planned sections")
    if any(br.Location.Row == total for br in sheet.HPageBreaks):
        sheet.HPageBreaks.Add(sheet.Cells(total - 1, 1))


def land_pages(sheet, spec):
    start, total = spec['data_start'], spec['total_row']
    breaks = [] if spec['category'] == 'summary' else sorted({
        br.Location.Row for br in sheet.HPageBreaks if start < br.Location.Row <= total})
    bounds = [start, *breaks, total + 1]
    def text(row, col):
        cell = sheet.Cells(row, col)
        value = cell.Value2
        return '' if type(value) in (int, float) and value == 0 else str(cell.Text or '')
    return [
        {'rows': [[text(row, col) for col in section['columns']]
                  for row in range(first, stop)],
         'total': stop == total + 1, 'headers': section['headers']}
        for section in spec['sections'] for first, stop in zip(bounds, bounds[1:])
    ]


def export(config, output, engine='excel'):
    if engine not in {'excel', 'wps'}:
        raise ValueError('Unsupported Office engine')
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Use a new export directory')
    entries = config['files']
    kinds = [entry['kind'] for entry in entries]
    if len(kinds) != len(set(kinds)) or any(k not in {'audit', 'materials', 'opinion'} for k in kinds):
        raise ValueError('Distinct audit/materials/opinion kinds required')
    for entry in entries:
        file = Path(entry['file']).resolve()
        if file.suffix.lower() not in {'.xlsx', '.docx'}:
            raise ValueError('Only xlsx and docx candidates supported')
        if file.suffix.lower() == '.xlsx':
            layout = load(entry['layout'])
            if layout['workbook_sha256'] != sha(file) or Path(layout['workbook']).resolve() != file:
                raise ValueError('Layout belongs to another workbook version')
    output.mkdir(parents=True)
    result = {'schema_version': 2, 'batch': config['batch'], 'tool': engine,
              'files': {}, 'source_hashes': {}, 'exports': [], 'recalculated': False}
    spreadsheet = word = None
    try:
        for entry in entries:
            source = Path(entry['file']).resolve()
            before = sha(source)
            candidate = output / candidate_name(entry['kind'], source.suffix.lower())
            shutil.copy2(source, candidate)
            kind = entry['kind']
            if candidate.suffix == '.xlsx':
                if spreadsheet is None:
                    spreadsheet, pid = private_instance('Excel.Application' if engine == 'excel' else 'KET.Application')
                    spreadsheet.Visible = False
                    spreadsheet.DisplayAlerts = False
                    # Disable macros and external link updates when opening candidates.
                    spreadsheet.AutomationSecurity = 3
                    result['spreadsheet_version'] = str(spreadsheet.Version)
                    result['spreadsheet_pid'] = pid
                book = spreadsheet.Workbooks.Open(str(candidate), 0, False)
                layout = load(entry['layout'])
                try:
                    for spec in layout['sheets']:
                        sheet = book.Worksheets(spec['sheet'])
                        if spec.get('land_layout'):
                            sheet.Select(True)
                            prepare_land_print(sheet, spec)
                            continue
                        for col, width in enumerate(spec['widths_mm'], 1):
                            calibrated_width(sheet, col, width)
                        sheet.PageSetup.PaperSize = 9
                        sheet.PageSetup.Orientation = 2 if spec['orientation'] == 'landscape' else 1
                        sheet.PageSetup.Zoom = 100
                    spreadsheet.CalculateFull()
                    book.Worksheets(layout['sheets'][0]['sheet']).Select(True)
                    book.Save()
                    book.Close(False)
                    book = None
                    book = spreadsheet.Workbooks.Open(str(candidate), 0, True)
                    for index, spec in enumerate(layout['sheets'], 1):
                        sheet = book.Worksheets(spec['sheet'])
                        pdf = output / f'{kind}-{index:02d}.pdf'
                        # Worksheet.ExportAsFixedFormat can include every
                        # grouped tab. Select only this sheet before exporting.
                        sheet.Select(True)
                        sheet.ExportAsFixedFormat(0, str(pdf))
                        if spec.get('land_layout'):
                            expected_pages = land_pages(sheet, spec)
                        else:
                            expected_pages = []
                            for first, last in spec['page_rows']:
                                rows = []
                                for row in range(first, last + 1):
                                    rows.append([str(sheet.Cells(row, col).Text or '') for col in range(1, len(spec['columns']) + 1)])
                                expected_pages.append({'rows': rows, 'total': spec['total_row'] is not None and first <= spec['total_row'] <= last})
                        result['exports'].append({'kind': kind, 'source': str(candidate), 'pdf': str(pdf),
                            'pdf_sha256': sha(pdf), 'sheet': spec['sheet'], 'category': spec['category'],
                            'orientation': spec['orientation'], 'title': spec['title'],
                            'headers': spec.get('labels', []), 'expected_pages': expected_pages,
                            'land_layout': bool(spec.get('land_layout')),
                            'font_pt': None if spec.get('land_layout') else 12,
                            'title_pt': None if spec.get('land_layout') else 22,
                            'margins_mm': spec['margins_mm']})
                finally:
                    if book is not None:
                        book.Close(False)
                # Read from disk after the producing instance has released the file.
                from openpyxl import load_workbook
                readback = load_workbook(candidate, data_only=True)
                try:
                    if any(spec.get('land_layout') for spec in layout['sheets']):
                        if readback.sheetnames != [spec['sheet'] for spec in layout['sheets']]:
                            raise ValueError('Land workbook must contain exactly the summary and detail sheets')
                    for spec in layout['sheets']:
                        sheet = readback[spec['sheet']]
                        if spec.get('land_layout') and (
                            sheet['A1'].font.sz != 22 or not sheet['A1'].font.bold
                            or sheet['A1'].value != spec['title']
                            or sheet.oddHeader.center.text != spec['title']
                            or sheet.oddHeader.center.size != 22
                            or 'Bold' not in (sheet.oddHeader.center.font or '')
                            or sheet.sheet_view.view != 'pageBreakPreview'
                            or sheet.sheet_view.showZeros is not False
                        ):
                            raise ValueError(f'{sheet.title}: expected 22 pt bold matching titles, page-break preview and hidden zeros')
                        for row in sheet:
                            for cell in row:
                                if cell.data_type == 'e':
                                    raise ValueError(f'Formula error in {spec["sheet"]}!{cell.coordinate}')
                    layout['workbook'] = str(candidate)
                    layout['workbook_sha256'] = sha(candidate)
                    layout_path = output / f'{kind}.layout.json'
                    layout_path.write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding='utf8')
                finally:
                    readback.close()
            else:
                if word is None:
                    word, pid = private_instance('Word.Application' if engine == 'excel' else 'KWPS.Application')
                    word.Visible = False
                    word.DisplayAlerts = 0
                    word.AutomationSecurity = 3
                    result['word_version'] = str(word.Version)
                    result['word_pid'] = pid
                doc = word.Documents.Open(str(candidate), ReadOnly=False, AddToRecentFiles=False)
                try:
                    doc.Fields.Update()
                    doc.Repaginate()
                    for section in doc.Sections:
                        for part in list(section.Headers) + list(section.Footers):
                            if part.Exists:
                                part.Range.Fields.Update()
                    doc.Save()
                    pdf = output / f'{kind}.pdf'
                    doc.ExportAsFixedFormat(str(pdf), 17)
                    result['exports'].append({'kind': kind, 'source': str(candidate), 'pdf': str(pdf),
                        'pdf_sha256': sha(pdf), 'orientation': 'portrait', 'font_pt': entry.get('font_pt', 12),
                        'title_pt': None, 'headers': [], 'expected_pages': [],
                        'margins_mm': entry.get('margins_mm', {'left': 10, 'right': 10, 'top': 10, 'bottom': 10})})
                finally:
                    doc.Close(SaveChanges=False)
                from docx import Document
                Document(candidate)  # fresh package readback
            if sha(source) != before:
                raise ValueError('Original candidate changed during export')
            result['files'][kind] = str(candidate)
            result['source_hashes'][str(candidate)] = sha(candidate)
            print(f'{kind}: saved, read back and exported', flush=True)
        result['recalculated'] = True
    finally:
        if word is not None:
            word.Quit()
        if spreadsheet is not None:
            spreadsheet.Quit()
    result_path = output / 'export.json'
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True); parser.add_argument('--output', required=True)
    parser.add_argument('--engine', choices=['excel', 'wps'], default='excel')
    args = parser.parse_args()
    export(load(args.config), args.output, args.engine)


if __name__ == '__main__':
    main()
