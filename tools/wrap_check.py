# -*- coding: utf-8 -*-
"""Detect forced line wraps in exported table PDFs.

A cell whose text was pushed onto a second line while the first line still
had room to spare on the right is a layout defect: the column is too narrow
for its own content. Natural paragraph text is ignored because the caller
works on table sheets only.

Usage:
    python tools/wrap_check.py 导出.pdf [更多.pdf ...]
    python tools/wrap_check.py --json 报告.json 导出.pdf

Exit code 0 means no suspected wrap, 2 means at least one was found.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import pymupdf
except ImportError:  # pragma: no cover - dependency hint only
    pymupdf = None

# A new line starting at almost the same x, within this vertical gap, is a
# continuation of the previous line rather than a new paragraph.
MAX_LINE_GAP = 8.0
# Right-hand space above this many points means the line did not need to end.
MIN_TRAILING_SPACE = 40.0
# Sentences ending with these marks are allowed to end early.
SENTENCE_ENDINGS = ("。", "；", "：", "，", "、", "！", "？", ":", ";", ",")


def page_lines(page):
    lines = []
    for block in page.get_text("dict").get("blocks", []):
        if "lines" not in block:
            continue
        for line in block["lines"]:
            text = "".join(span["text"] for span in line["spans"]).strip()
            if not text:
                continue
            x0, y0, x1, y1 = line["bbox"]
            lines.append((round(y0, 1), round(y1, 1), round(x0, 1), round(x1, 1), text))
    lines.sort()
    return lines


def check_page(page):
    """Return (previous_text, current_text, trailing_space) for each suspect wrap."""
    findings = []
    lines = page_lines(page)
    width = page.rect.width
    for i in range(len(lines) - 1):
        y0, y1, x0, x1, text = lines[i]
        ny0, ny1, nx0, nx1, next_text = lines[i + 1]
        if abs(nx0 - x0) >= 2:          # not left-aligned with the previous line
            continue
        if not 0 < ny0 - y1 < MAX_LINE_GAP:
            continue
        if abs((ny1 - ny0) - (y1 - y0)) >= 2:   # different line height: heading or table row
            continue
        trailing = width - x1
        if trailing <= MIN_TRAILING_SPACE:
            continue
        if text.endswith(SENTENCE_ENDINGS):
            continue
        findings.append((text, next_text, round(trailing, 1)))
    return findings


def check(pdf_path):
    if pymupdf is None:
        raise RuntimeError("需要 PyMuPDF：python -m pip install pymupdf")
    findings = []
    with pymupdf.open(pdf_path) as document:
        for number, page in enumerate(document, 1):
            for text, next_text, trailing in check_page(page):
                findings.append({"page": number, "line": text, "next_line": next_text,
                                 "trailing_pt": trailing})
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", nargs="+")
    parser.add_argument("--json", help="把结果另存为 JSON 报告")
    args = parser.parse_args()

    report, total = {}, 0
    for path in args.pdf:
        items = check(path)
        total += len(items)
        report[str(Path(path).resolve())] = items
        print("%s：可疑折行 %d 处" % (Path(path).name, len(items)))
        for item in items:
            print("  第%d页 右侧余 %.1fpt  %s ⏎ %s" % (
                item["page"], item["trailing_pt"], item["line"][:40], item["next_line"][:30]))
    if args.json:
        Path(args.json).write_text(json.dumps({"total": total, "files": report},
                                              ensure_ascii=False, indent=2), encoding="utf8")
    return 0 if total == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
