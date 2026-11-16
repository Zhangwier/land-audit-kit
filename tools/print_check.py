"""Parse every exported page; keep visual review separate from automatic metrics."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

import pymupdf

from print_layout import load, sha

VISUAL_CHECKS = {'text', 'borders', 'title', 'total', 'pagination', 'readable'}


def normalized(text):
    return re.sub(r'\s+', '', str(text)).replace('／', '/').replace('（', '(').replace('）', ')')


def analyze(entry):
    findings, pages = [], []
    pdf = Path(entry['pdf'])
    try:
        with pymupdf.open(pdf) as document:
            if document.needs_pass or len(document) == 0 or not document.is_pdf:
                raise ValueError('PDF has no readable pages')
            expected = entry.get('expected_pages', [])
            if entry.get('sheet') and len(expected) != len(document):
                findings.append({'status': 'fail', 'message': 'Actual page count differs from layout'})
            for index, page in enumerate(document):
                issues = []
                width, height = page.rect.width, page.rect.height
                target = (841.89, 595.28) if entry['orientation'] == 'landscape' else (595.28, 841.89)
                if any(abs(a - b) > 2 for a, b in zip((width, height), target)):
                    issues.append(('fail', 'Actual paper is not the required A4 orientation'))
                text = page.get_text()
                spans = [s for b in page.get_text('dict')['blocks'] if 'lines' in b
                         for line in b['lines'] for s in line['spans'] if s['text'].strip()]
                if not spans:
                    issues.append(('unverified', 'No reliable text/font layer; visual-only evidence is insufficient'))
                # Footers may have independent fonts; all content above the
                # configured bottom margin must meet the declared body floor.
                bottom = height - entry.get('margins_mm', {}).get('bottom', 10) * 72 / 25.4
                body = [s for s in spans if s['bbox'][3] <= bottom]
                font_pt = entry.get('font_pt', 12)
                if body and font_pt is not None and min(s['size'] for s in body) < font_pt - .2:
                    issues.append(('fail', 'Actual body font is below the required size'))
                if '###' in text or '填表说明' in text:
                    issues.append(('fail', 'Overflow or filling instructions in printed content'))
                if re.search(r'【[^】]+】', text):
                    issues.append(('fail', 'Unfilled placeholder in printed content'))
                if not normalized(text) or (len(spans) <= 1 and len(normalized(text)) < 5):
                    issues.append(('fail', 'Blank page or isolated page number'))
                flat = normalized(text)
                labels = flat
                title = normalized(entry.get('title', ''))
                land_layout = entry.get('land_layout')
                if land_layout:
                    if not title or title not in flat:
                        issues.append(('fail', 'Missing full repeated title'))
                    else:
                        labels = flat.replace(title, '', 1)
                for header in entry.get('headers', []):
                    if normalized(header) not in flat:
                        issues.append(('fail', 'Missing repeated table header: ' + header))
                title_spans = []
                if land_layout:
                    title_spans = [s for s in spans if normalized(s['text']) == title]
                    if not title_spans and title in flat:
                        issues.append(('unverified', 'Printed title size cannot be measured'))
                elif entry.get('title_pt'):
                    title_spans = [s for s in spans if s['size'] >= entry['title_pt'] - .2]
                    if not title_spans or normalized(entry.get('title', '')) not in flat:
                        issues.append(('fail', 'Missing full-size repeated title'))
                    else:
                        # Excel synthesizes SimSun bold with a fill+stroke
                        # text pair; the font flags alone remain regular.
                        strokes = [t for t in page.get_texttrace() if t.get('type') == 1 and t['size'] >= entry['title_pt'] - .2 and (t.get('linewidth') or 0) > 0]
                        if any(not (s['flags'] & 16) and not any(pymupdf.Rect(t['bbox']).intersects(pymupdf.Rect(s['bbox'])) for t in strokes) for s in title_spans):
                            issues.append(('unverified', 'Title boldness could not be confirmed'))
                drawings = page.get_drawings()
                table_rects = [d['rect'] for d in drawings if d['rect'].width > 15 and d['rect'].y0 > 0]
                content_rect = None
                measured_margins = None
                rects = [pymupdf.Rect(s['bbox']) for s in body] + table_rects
                if rects:
                    content_rect = pymupdf.Rect(rects[0])
                    for rect in rects[1:]: content_rect |= rect
                    measured_margins = {'left': content_rect.x0 * 25.4 / 72,
                        'right': (width - content_rect.x1) * 25.4 / 72,
                        'top': content_rect.y0 * 25.4 / 72,
                        'bottom': (height - content_rect.y1) * 25.4 / 72}
                    for edge, minimum in entry.get('margins_mm', {}).items():
                        if measured_margins[edge] < float(minimum) - 2 * 25.4 / 72:
                            issues.append(('fail', 'Content extends beyond configured ' + edge + ' margin'))
                title_gap = None
                if entry.get('sheet'):
                    if not table_rects:
                        issues.append(('unverified', 'Table borders cannot be measured'))
                    if title_spans and table_rects:
                        title_bottom = max(s['bbox'][3] for s in title_spans)
                        table_top = min(r.y0 for r in table_rects)
                        title_gap = table_top - title_bottom
                        if title_gap < 3 * 72 / 25.4 - .5:
                            issues.append(('fail', 'Title/table gap is below 3 mm or overlaps'))
                if index < len(expected):
                    plan = expected[index]
                    for header in plan.get('headers', []):
                        if normalized(header) not in labels:
                            issues.append(('fail', 'Missing planned section: ' + header))
                    for row in plan['rows']:
                        for value in row:
                            if normalized(value) and normalized(value) not in flat:
                                issues.append(('fail', 'A registered row value is missing or clipped: ' + str(value)))
                    if plan['total'] and '合计' not in text:
                        issues.append(('fail', 'Final total is missing'))
                    if plan['total'] and len(plan['rows']) < 2:
                        issues.append(('fail', 'Total has no data row on its page'))
                if entry.get('kind') == 'opinion':
                    content = re.sub(r'\s+|\d+$', '', text)
                    if re.fullmatch(r'(?:二[〇○零]|[\d一二三四五六七八九十年月日○〇零])+', content):
                        issues.append(('fail', 'Isolated date on final opinion page'))
                pages.append({'physical_page': index + 1, 'width_pt': width, 'height_pt': height,
                    'min_body_font_pt': min((s['size'] for s in body), default=None),
                    'actual_title_font_pt': min((s['size'] for s in title_spans), default=None),
                    'title_gap_pt': title_gap, 'measured_content_margins_mm': measured_margins, 'text': text,
                    'findings': [{'status': s, 'message': m} for s, m in issues]})
                findings.extend({'page': index + 1, 'status': s, 'message': m} for s, m in issues)
    except (OSError, ValueError, RuntimeError) as error:
        findings.append({'status': 'unverified', 'message': 'PDF cannot be parsed: ' + str(error)})
    return pages, findings


def inspect(export_path, output):
    data = load(export_path)
    if data.get('schema_version') != 2 or data.get('recalculated') is not True:
        raise ValueError('A completed v2 Office export is required')
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Use a new inspection directory')
    output.mkdir(parents=True)
    report = {**data, 'export_file': str(Path(export_path).resolve()), 'export_sha256': sha(export_path),
              'pages_checked': False, 'no_blank_pages': False, 'no_orphan_total_or_date': False,
              'page_evidence': [], 'automatic_findings': []}
    for entry_index, entry in enumerate(data['exports']):
        if sha(entry['pdf']) != entry['pdf_sha256']:
            raise ValueError('Export PDF changed')
        pages, findings = analyze(entry)
        report['automatic_findings'].extend({'pdf': entry['pdf'], **f} for f in findings)
        if not pages:
            continue
        with pymupdf.open(entry['pdf']) as document:
            for item, page in zip(pages, document):
                dest = output / f'page-{entry_index + 1:03d}-{item["physical_page"]:04d}.png'
                page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False, colorspace=pymupdf.csRGB).save(dest)
                report['page_evidence'].append({'id': f'{entry_index + 1}:{item["physical_page"]}',
                    'pdf': entry['pdf'], 'image': str(dest), 'image_sha256': sha(dest),
                    'review': None, **item})
    dest = output / 'print_report.json'
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    return report


def validate_report(report, check_visual=True):
    findings, hashes = [], {}
    def issue(status, message, location=None):
        findings.append({'status': status, 'message': message, 'location': location})
    if report.get('schema_version') != 2:
        issue('unverified', 'Legacy print report requires a fresh v2 export')
        return findings, hashes
    for path, digest in report.get('source_hashes', {}).items():
        if not Path(path).is_file() or sha(path) != digest:
            issue('unverified', 'Print source changed', path)
        else:
            hashes[str(Path(path).resolve())] = digest
    if not report.get('tool') or report.get('recalculated') is not True:
        issue('unverified', 'Office tool or recalculation evidence is missing')
    export_path = report.get('export_file')
    if not export_path or not Path(export_path).is_file() or sha(export_path) != report.get('export_sha256'):
        issue('unverified', 'Original export record is missing or changed')
    else:
        hashes[str(Path(export_path).resolve())] = sha(export_path)
        original = load(export_path)
        if report.get('exports') != original.get('exports'):
            issue('unverified', 'Print targets or expected content differ from original export')
    exports = report.get('exports', [])
    if not exports:
        issue('unverified', 'No actual PDF exports')
    exported_sources = {str(Path(e['source']).resolve()) for e in exports}
    if exported_sources != {str(Path(p).resolve()) for p in report.get('source_hashes', {})}:
        issue('unverified', 'Export does not cover every source')
    seen = set()
    for index, entry in enumerate(exports):
        path = Path(entry['pdf'])
        if not path.is_file() or sha(path) != entry.get('pdf_sha256'):
            issue('unverified', 'PDF missing or changed', str(path)); continue
        hashes[str(path.resolve())] = sha(path)
        pages, automatic = analyze(entry)
        findings.extend({'location': str(path), **f} for f in automatic)
        if not pages:
            continue
        if check_visual:
            records = [p for p in report.get('page_evidence', []) if p['pdf'] == str(path)]
            if len(records) != len(pages):
                issue('unverified', 'Incomplete page-by-page evidence', str(path))
            with pymupdf.open(path) as document:
                for number, page in enumerate(document, 1):
                    key = f'{index + 1}:{number}'
                    matching = [r for r in records if r['id'] == key and r['physical_page'] == number]
                    if len(matching) != 1:
                        issue('unverified', 'Missing or duplicate page review', key); continue
                    record = matching[0]
                    seen.add(key)
                    image = Path(record['image'])
                    if not image.is_file() or sha(image) != record['image_sha256']:
                        issue('unverified', 'Reviewed image changed or missing', key); continue
                    rendered = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False, colorspace=pymupdf.csRGB).tobytes('png')
                    if hashlib.sha256(rendered).hexdigest() != record['image_sha256']:
                        issue('unverified', 'Image does not represent this PDF page', key)
                    hashes[str(image.resolve())] = record['image_sha256']
                    review = record.get('review') or {}
                    if not review.get('reviewer') or set(review.get('checks', {})) != VISUAL_CHECKS or any(v is not True for v in review.get('checks', {}).values()):
                        issue('unverified', 'Actual visual review is incomplete', key)
    if check_visual and len(seen) != len(report.get('page_evidence', [])):
        issue('unverified', 'Unexpected or duplicate page evidence')
    return findings, hashes


def review(input_path, decisions_path, output):
    report, decisions = load(input_path), load(decisions_path)
    ids = {p['id'] for p in report['page_evidence']}
    if set(decisions['pages']) != ids:
        raise ValueError('Visual decisions must cover exactly every rendered page')
    for page in report['page_evidence']:
        page['review'] = decisions['pages'][page['id']]
    findings, _ = validate_report(report)
    report['pages_checked'] = all(p.get('review') for p in report['page_evidence'])
    report['no_blank_pages'] = report['no_orphan_total_or_date'] = not findings
    report['overall'] = 'fail' if any(f['status'] == 'fail' for f in findings) else 'unverified' if findings else 'pass'
    report['findings'] = findings
    with Path(output).open('x', encoding='utf8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


def rebind(input_path, files, output):
    """Bind reviewed pixels to byte-identical guarded delivery paths."""
    report = load(input_path)
    if set(files) != set(report['files']):
        raise ValueError('Delivery must include all registered kinds')
    replacements = {}
    for kind, path in files.items():
        old = report['files'][kind]
        new = str(Path(path).resolve())
        if sha(new) != report['source_hashes'][old] or sha(old) != report['source_hashes'][old]:
            raise ValueError('Delivered bytes differ; redo export and review')
        replacements[old] = new
    report['files'] = {k: replacements[v] for k, v in report['files'].items()}
    report['source_hashes'] = {replacements[p]: h for p, h in report['source_hashes'].items()}
    for entry in report['exports']:
        entry['source'] = replacements[entry['source']]
    export_path = Path(str(output) + '.export.json').resolve()
    with export_path.open('x', encoding='utf8') as stream:
        json.dump({k: report[k] for k in ('schema_version', 'batch', 'tool', 'files', 'source_hashes', 'exports', 'recalculated')}, stream, ensure_ascii=False, indent=2)
    report['previous_export_file'] = report['export_file']
    report['previous_export_sha256'] = report['export_sha256']
    report['export_file'], report['export_sha256'] = str(export_path), sha(export_path)
    with Path(output).open('x', encoding='utf8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['inspect', 'review', 'rebind'])
    parser.add_argument('--input', required=True); parser.add_argument('--output', required=True)
    parser.add_argument('--decisions')
    parser.add_argument('--files', help='JSON kind-to-delivered-path map for rebind')
    args = parser.parse_args()
    if args.command == 'inspect': result = inspect(args.input, args.output)
    elif args.command == 'review': result = review(args.input, args.decisions, args.output)
    else: result = rebind(args.input, load(args.files), args.output)
    print(json.dumps({'pages': len(result['page_evidence']), 'overall': result.get('overall', 'unverified'),
                      'findings': len(result.get('findings', result.get('automatic_findings', [])))}, ensure_ascii=False))


if __name__ == '__main__':
    main()
