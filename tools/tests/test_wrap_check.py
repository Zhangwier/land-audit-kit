"""Regression tests for the exported-table line-wrap detector."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wrap_check


class WrapCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ["LAND_AUDIT_TEST_DIR"])
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def build(self, lines):
        """lines: [(x, y, text)] 画在同一页上，使用内置中文字体。"""
        pdf = self.root / "page.pdf"
        with pymupdf.open() as doc:
            page = doc.new_page(width=842, height=595)
            for x, y, text in lines:
                page.insert_text((x, y), text, fontsize=10, fontname="china-s")
            doc.save(pdf)
        return pdf

    def test_reports_wrapped_cell_text(self):
        # 第二行与第一行左对齐、行距正常，但第一行右侧仍有余量 => 被迫折行
        pdf = self.build([(60, 100, "门口坡紫京"), (60, 114, "岭")])
        findings = wrap_check.check(pdf)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["line"], "门口坡紫京")
        self.assertEqual(findings[0]["page"], 1)

    def test_ignores_full_line(self):
        # 第一行已经排到页面右侧，换行是自然折行
        pdf = self.build([(60, 100, "村" * 78), (60, 114, "岭")])
        self.assertEqual(wrap_check.check(pdf), [])

    def test_ignores_sentence_ending(self):
        # 以句读结尾的行不视为被迫折行
        pdf = self.build([(60, 100, "两者不一致，"), (60, 114, "请核实。")])
        self.assertEqual(wrap_check.check(pdf), [])

    def test_ignores_paragraph_gap(self):
        # 行距超出阈值视为新段落
        pdf = self.build([(60, 100, "上一段"), (60, 140, "下一段")])
        self.assertEqual(wrap_check.check(pdf), [])

    def test_main_returns_two_on_finding(self):
        pdf = self.build([(60, 100, "门口坡紫京"), (60, 114, "岭")])
        with patch.object(sys, "argv", ["wrap_check.py", str(pdf)]):
            self.assertEqual(wrap_check.main(), 2)

    def test_main_returns_zero_when_clean(self):
        pdf = self.build([(60, 100, "上一段"), (60, 140, "下一段")])
        with patch.object(sys, "argv", ["wrap_check.py", str(pdf)]):
            self.assertEqual(wrap_check.main(), 0)


if __name__ == "__main__":
    unittest.main()
