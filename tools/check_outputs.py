"""Read-only output checks with explicit locations; unknown is never a pass."""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

from excel_snapshot import inspect


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save_new(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)


def paragraphs(path):
    with zipfile.ZipFile(path) as package:
        root = ET.fromstring(package.read("word/document.xml"))
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    return ["".join(t.text or "" for t in p.findall(".//w:t", ns)) for p in root.findall(".//w:p", ns)]


OPINION_HEADING = re.compile(r"^[一二三四五六七八九十]+、\s*审核意见[：:]?$")
SECTION_HEADING = re.compile(r"^[一二三四五六七八九十]+、|^附表[：:]")
OPINION_SUBHEADING = re.compile(r"^[（(][一二三四五六七八九十]+[)）]\s*(?:合规性审查|补偿费用审核)[：:]?$")


def opinion_groups(doc):
    """Keep formal report findings separate from bases, notices and attachments."""
    start = next((i for i, p in enumerate(doc, 1) if OPINION_HEADING.fullmatch(p.strip())), 0)
    groups = [[]]
    for i, text in enumerate(doc[start:], start + 1):
        text = text.strip()
        if start and SECTION_HEADING.match(text):
            break
        if start and OPINION_SUBHEADING.fullmatch(text):
            if groups[-1]:
                groups.append([])
            continue
        if (re.match(r"^\d+[.、．]", text)
                or (re.match(r"^[（(][一二三四五六七八九十]+[)）]", text) and len(text) > 15)):
            groups[-1].append((i, text))
    return groups


def opinion_entries(doc):
    return [entry for group in opinion_groups(doc) for entry in group]


def upper_money(value):
    """RMB uppercase from exact cents (no float roundtrip)."""
    n = Decimal(str(value)).quantize(Decimal("0.01"))
    negative = n < 0
    cents = int(abs(n) * 100)
    integer, fraction = divmod(cents, 100)
    digits = "零壹贰叁肆伍陆柒捌玖"
    def group(num):
        out, zero = "", False
        for i in range(3, -1, -1):
            digit = (num // (10 ** i)) % 10
            if digit:
                if zero:
                    out += "零"
                out += digits[digit] + ("", "拾", "佰", "仟")[i]
                zero = False
            elif out:
                zero = True
        return out
    groups = []
    while integer:
        groups.append(integer % 10000)
        integer //= 10000
    text, gap = "", False
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if not g:
            gap = bool(text)
            continue
        if text and (gap or g < 1000):
            text += "零"
        text += group(g) + ("", "万", "亿", "兆")[i]
        gap = False
    text = (text or "零") + "元"
    jiao, fen = divmod(fraction, 10)
    if not fraction:
        text += "整"
    else:
        if jiao:
            text += digits[jiao] + "角"
        elif groups:
            text += "零"
        if fen:
            text += digits[fen] + "分"
    return ("负" if negative else "") + text


class Checker:
    def __init__(self, spec):
        self.spec = spec
        self.files = {k: Path(v).resolve() for k, v in spec["files"].items()}
        self.hashes, self.books, self.docs, self.results = {}, {}, {}, []
        for key, path in self.files.items():
            if not path.is_file():
                self.add("files", "fail", "成果不存在", str(path))
                continue
            self.hashes[str(path)] = sha(path)
            try:
                if path.suffix.lower() == ".xlsx":
                    self.books[key] = inspect(path)
                elif path.suffix.lower() == ".docx":
                    self.docs[key] = paragraphs(path)
                else:
                    self.add("files", "unverified", "该格式需转换工作副本后核验", str(path))
            except (OSError, ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
                self.add("files", "fail", "文件无法读取：" + str(exc), str(path))

    def add(self, area, status, message, location=None):
        self.results.append({"check": area, "status": status, "message": message, "location": location})

    def cell(self, loc):
        book = self.books.get(loc["file"])
        if book is None:
            return None
        sheets = [s for s in book["sheets"] if s["sheet"] == loc["sheet"]]
        if not sheets:
            return None
        return next((c for c in sheets[0]["cells"] if c["cell"] == loc["cell"].replace("$", "").upper()), None)

    def value(self, loc):
        if "cell" in loc:
            cell = self.cell(loc)
            return cell["cached"] if cell and cell["formula"] else cell["value"] if cell else None
        doc = self.docs.get(loc["file"])
        if doc is None:
            return None
        if "paragraph" in loc:
            i = loc["paragraph"] - 1
            text = doc[i] if 0 <= i < len(doc) else ""
        else:
            text = "\n".join(doc)
        if "pattern" in loc:
            matches = re.findall(loc["pattern"], text)
            return matches[0] if len(matches) == 1 and isinstance(matches[0], str) else None
        return text

    def names_and_text(self):
        batch = self.spec["batch"]
        batch_pattern = self.spec.get("batch_pattern", r"([^\s，。；：:/\\]+?)第([^\s，。；：:/\\]+?)批")
        match = re.search(batch_pattern, batch)
        identity = match.groups() if match else None
        for key, path in self.files.items():
            sources = [("文件名", path.name)]
            if key in self.docs:
                nonempty = [p for p in self.docs[key] if p.strip()]
                sources += [("标题", p) for p in nonempty[:5] if re.search(batch_pattern, p) and "意见" in p]
                text = "\n".join(self.docs[key])
                patterns = {"内部用语": r"Gemini|核实员|底稿|(?:\bAI\b)", "表格坐标": r"Sheet\s|第\d+行|[A-Z]{1,3}列|[A-Z]{1,3}\d+单元格",
                            "错名或重复字": r"征收征地"}
                if not any(OPINION_HEADING.fullmatch(p.strip()) for p in nonempty):
                    patterns["书名号"] = r"[《》]"
                patterns.update(self.spec.get("forbidden_text_patterns", {}))
                for label, pattern in patterns.items():
                    if re.search(pattern, text):
                        self.add("text", "advisory" if self.spec.get("historical") else "fail", label, str(path))
                status = "advisory" if self.spec.get("historical") else "fail"
                for group in opinion_groups(self.docs[key]):
                    for _, p in group:
                        if not p.endswith("。"):
                            self.add("text", status, "编号意见缺句号", p[:100])
                    nums = [int(m.group(1)) for _, p in group if (m := re.match(r"^(\d+)[.、．]", p))]
                    if nums and nums != list(range(1, len(nums) + 1)):
                        self.add("text", status, "意见编号不连续", str(path))
            if key in self.books:
                from openpyxl import load_workbook
                workbook = load_workbook(path, read_only=False, data_only=False)
                try:
                    for sheet in workbook:
                        sources += [(sheet.title + " 页眉", str(getattr(sheet.oddHeader, part).text or "")) for part in ("left", "center", "right")]
                finally:
                    workbook.close()
                for sheet in self.books[key]["sheets"]:
                    for cell in sheet["cells"]:
                        value = cell["value"]
                        if isinstance(value, str) and not cell["formula"] and re.search(batch_pattern, value):
                            sources.append((sheet["sheet"] + "!" + cell["cell"], value))
                        if cell["type"] == "e" or (cell["formula"] and isinstance(cell["cached"], str) and cell["cached"].startswith("#")):
                            self.add("formula", "fail", "Excel 错误值", sheet["sheet"] + "!" + cell["cell"])
            matched = False
            for loc, text in sources:
                for found in re.finditer(batch_pattern, text):
                    matched = True
                    actual = found.groups()
                    same = actual == identity
                    if identity and len(identity) == 2 and len(actual) == 2:
                        same = actual[0].endswith(identity[0]) and actual[1] == identity[1]
                    if identity and not same:
                        self.add("names", "fail", "名称串批：" + found.group(), str(path) + " " + loc)
                for term in self.spec.get("forbidden_name_terms", []):
                    if term in text and loc in {"文件名", "标题"}:
                        self.add("names", "fail", "成果名称包含本项目禁用词", str(path))
            if not matched:
                self.add("names", "unverified", "没有可辨认的批次名称", str(path))
        for loc in self.spec.get("names", []):
            actual = self.value(loc)
            self.add("names", "unverified" if actual is None else "pass" if str(actual) == loc["expected"] else "fail", "显式标题或附表名称核对", loc)

    def money(self):
        if not self.spec.get("money"):
            self.add("money", "unverified", "未给送审、审核、核增减金额及定位")
        keys = {m["key"] for m in self.spec.get("money", []) if m.get("value") is not None}
        for missing in {"submitted", "audited", "delta"} - keys:
            self.add("money", "unverified", "缺金额：" + missing)
        for item in self.spec.get("money", []):
            if item.get("value") is None or not item.get("locations"):
                self.add("money", "unverified", "金额或定位尚未确认", item.get("key"))
                continue
            expected = Decimal(str(item["value"]))
            if self.spec.get("stage") == "payment" and item["key"] in {"submitted", "audited"}:
                if not any(loc.get("kind") == "uppercase" for loc in item["locations"]):
                    self.add("money", "unverified", "请款意见缺金额大写核对位置", item["key"])
            for loc in item["locations"]:
                actual = self.value(loc)
                if actual is None:
                    self.add("money", "unverified", "缺少数值、公式缓存或唯一定位", loc)
                    continue
                if loc.get("kind") == "uppercase":
                    ok = str(actual).replace("圆", "元").replace("正", "整") == upper_money(expected)
                else:
                    try:
                        ok = Decimal(str(actual).replace(",", "")) == expected
                    except InvalidOperation:
                        self.add("money", "unverified", "不是可读取的十进制金额", loc)
                        continue
                self.add("money", "pass" if ok else "fail", f"{item['key']}：实际 {actual}，应为 {expected}", loc)
                if "cell" in loc and (cell := self.cell(loc)) and not re.search(r"0\.00", cell["number_format"]):
                    self.add("format", "fail", "金额未显示两位小数", loc)
        equations = self.spec.get("equations", [])
        values = {m["key"]: Decimal(str(m["value"])) for m in self.spec.get("money", []) if m.get("value") is not None}
        for equation in equations:
            if any(k not in values for k in equation["parts"] + [equation["total"]]):
                self.add("money", "unverified", "分项合计条件不完整", equation)
            else:
                ok = sum((values[k] for k in equation["parts"]), Decimal(0)) == values[equation["total"]]
                self.add("money", "pass" if ok else "fail", "分项合计闭合", equation)
        submitted, audited, delta = (values.get(k) for k in ("submitted", "audited", "delta"))
        if all(v is not None for v in (submitted, audited, delta)):
            self.add("money", "pass" if audited - submitted == delta else "fail", "核增减 = 审核 − 送审")
        if "recalculation" in self.spec:
            from decimal_recalc import calculate
            result = calculate(self.spec["recalculation"])
            self.add("money", "pass" if result["all_arithmetic_matches"] else "unverified" if not result["complete"] else "fail", "Decimal 独立复算", result)

    def formulas(self):
        from openpyxl.utils.cell import range_boundaries
        if not self.spec.get("formulas"):
            self.add("formula", "unverified", "审核公式范围未登记")
        for spec in self.spec.get("formulas", []):
            if not spec.get("ranges") or not spec.get("submitted_columns"):
                self.add("formula", "unverified", "缺审核公式区域或送审金额列")
            for area in spec["ranges"]:
                c1, r1, c2, r2 = range_boundaries(area)
                from openpyxl.utils.cell import get_column_letter
                for row in range(r1, r2 + 1):
                    for col in range(c1, c2 + 1):
                        loc = {"file": spec["file"], "sheet": spec["sheet"], "cell": f"{get_column_letter(col)}{row}"}
                        cell = self.cell(loc)
                        if not cell or not cell["formula"]:
                            self.add("formula", "fail", "审核栏不是公式", loc)
                            continue
                        refs = re.findall(r"\$?([A-Z]{1,3})\$?(\d+)", cell["formula"].upper())
                        if any(column in spec.get("submitted_columns", []) for column, _ in refs):
                            self.add("formula", "fail", "审核公式引用送审金额列", loc)
                        if cell["cached"] is None:
                            self.add("formula", "unverified", "缺公式缓存，工作副本须由 WPS/Excel 重算", loc)

    def materials(self):
        if "materials" not in self.spec:
            self.add("materials", "unverified", "没有逐项收件与载体记录")
        opinion = "\n".join(self.docs.get("opinion", []))
        for item in self.spec.get("materials", []):
            e = self.value(item["electronic"]) or ""
            s = self.value(item["scan"]) or ""
            ticks = {"√", "✔", "✓"}
            carrier = item["carrier"]
            if carrier == "scan":
                ok = s in ticks and e == ""
            elif carrier == "electronic":
                ok = e in ticks and s == ""
            elif carrier == "both":
                ok = e in ticks and s in ticks
            elif carrier == "na":
                ok = e in {"", "/"} and s in {"", "/"} and "/" in {e, s}
            elif carrier == "missing":
                ok = item.get("scope") == "whole" and "×" in {e, s}
                if not item.get("opinion_text") or item["opinion_text"] not in opinion:
                    self.add("materials", "fail", "整批缺件没有对应意见", item["name"])
            elif carrier == "unknown":
                self.add("materials", "unverified", "收件暂记与内容核查分别记录", item["name"])
                continue
            else:
                raise ValueError("未知资料载体：" + carrier)
            self.add("materials", "pass" if ok else "fail", "资料载体与收件标记", item["name"])
            if item.get("content_status") not in {"confirmed", "missing", "na"}:
                self.add("materials", "unverified", "收件勾选不等于内容已核", item["name"])

    def coverage(self):
        coverage = self.spec.get("coverage")
        if coverage is None:
            self.add("coverage", "unverified", "缺少应核对象和问题逐项回源记录")
            return
        for expected_key, checked_key in (("expected_objects", "objects"), ("expected_problems", "problems")):
            if expected_key not in coverage or checked_key not in coverage:
                self.add("coverage", "unverified", "覆盖清单缺字段：" + expected_key)
                continue
            expected = coverage[expected_key]
            entries = coverage[checked_key]
            ids = [e["id"] for e in entries]
            if len(expected) != len(set(expected)) or any(n > 1 for n in Counter(ids).values()):
                self.add("coverage", "fail", "核查对象或问题 ID 重复")
            for missing in set(expected) - set(ids):
                self.add("coverage", "unverified", "漏核对象或问题", missing)
            for extra in set(ids) - set(expected):
                self.add("coverage", "advisory", "清单之外的补查项", extra)
            for entry in entries:
                if entry.get("status") not in {"confirmed", "unfounded", "na"}:
                    self.add("coverage", "unverified", "存疑或尚未核清", entry["id"])
                refs = entry.get("refs", [])
                if not refs and entry.get("status") != "na":
                    self.add("coverage", "unverified", "缺原件页码或单元格定位", entry["id"])
                for ref in refs:
                    p = Path(ref["file"]).resolve()
                    if not p.is_file() or not (ref.get("page") or ref.get("cell")):
                        self.add("coverage", "unverified", "定位缺失或原件不存在", entry["id"])
                    else:
                        self.hashes[str(p)] = sha(p)
        sources = self.spec.get("opinion_sources")
        if sources is None:
            self.add("sources", "unverified", "缺每条意见来源；历史人工删改不得恢复")
        else:
            numbered = {i for i, _ in opinion_entries(self.docs.get("opinion", []))}
            mapped = {s["paragraph"] for s in sources}
            if numbered - mapped:
                self.add("sources", "unverified", "有编号意见没有来源映射", sorted(numbered - mapped))
            for source in sources:
                kind = source["source"]
                if kind not in {"user", "project", "gemini", "verified"}:
                    self.add("sources", "fail", "未知意见来源", source)
                elif kind in {"gemini", "verified"} and source.get("decision") != "confirmed":
                    self.add("sources", "unverified", "自动意见尚未核实成立", source)

    def printing(self):
        report = self.spec.get("print_report")
        if not report:
            self.add("print", "unverified", "实际重算和打印页面尚未核验")
            return
        evidence = load(report)
        self.hashes[str(Path(report).resolve())] = sha(report)
        from print_check import validate_report
        findings, hashes = validate_report(evidence)
        self.hashes.update(hashes)
        for path, digest in evidence.get("source_hashes", {}).items():
            current = self.files.values()
            if Path(path).resolve() not in current or not Path(path).is_file() or sha(path) != digest:
                self.add("print", "unverified", "打印检查对应其他版本", path)
        if set(evidence.get("source_hashes", {})) != {str(p) for p in self.files.values()}:
            self.add("print", "unverified", "打印检查没有覆盖全部成果")
        for finding in findings:
            self.add("print", finding['status'], finding['message'], finding.get('location'))

    def run(self):
        self.names_and_text()
        self.money()
        self.formulas()
        self.materials()
        self.coverage()
        self.printing()
        for path, digest in self.hashes.items():
            if sha(path) != digest:
                raise ValueError("检查期间文件改变：" + path)
        overall = "fail" if any(r["status"] == "fail" for r in self.results) else "unverified" if any(r["status"] == "unverified" for r in self.results) else "pass"
        return {"batch": self.spec["batch"], "overall": overall, "source_hashes": self.hashes,
                "results": self.results, "counts": dict(Counter(r["status"] for r in self.results)),
                "business_verified": False, "note": "结构与已登记证据核验；回源判断和打印仍由对应角色负责。历史人工稿只列建议。"}


def split_gemini(spec, response):
    """Exact batch routing: never assign another batch's section by position."""
    text = Path(response).read_text(encoding="utf-8-sig")
    headers = list(re.finditer(r"^##\s+批次[：:]\s*(.+?)\s*$", text, re.M))
    sections = {}
    for i, match in enumerate(headers):
        name = match.group(1).strip()
        sections.setdefault(name, []).append(text[match.start():headers[i + 1].start() if i + 1 < len(headers) else len(text)].strip() + "\n")
    expected = {b["batch"]: Path(b["output"]).resolve() for b in spec["batches"]}
    if len(expected) != len(spec["batches"]) or len(set(expected.values())) != len(expected):
        raise ValueError("批次名称或输出路径重复")
    completed, missing, duplicate, conflicts = [], [], [], []
    for name, path in expected.items():
        if name not in sections:
            missing.append(name)
        elif len(sections[name]) != 1:
            duplicate.append(name)
        else:
            content = sections[name][0]
            if path.exists() and path.read_text(encoding="utf-8-sig") != content:
                conflicts.append(name)
                continue
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("x", encoding="utf-8") as output:
                    output.write(content)
            completed.append({"batch": name, "output": str(path), "sha256": sha(path)})
    unexpected = sorted(set(sections) - set(expected))
    return {"overall": "unverified" if missing or duplicate or unexpected or conflicts else "pass", "completed": completed,
            "missing": missing, "duplicate": duplicate, "unexpected": unexpected, "protected_conflicts": conflicts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "split"], nargs="?", default="check")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--response")
    args = parser.parse_args()
    try:
        config_hash = sha(args.input)
        spec = load(args.input)
        if Path(args.output).exists():
            raise ValueError("检查报告须另存，不能覆盖已有文件")
        result = split_gemini(spec, args.response) if args.command == "split" else Checker(spec).run()
        if sha(args.input) != config_hash:
            raise ValueError("核验配置在检查期间发生变化")
        result.setdefault("source_hashes", {})[str(Path(args.input).resolve())] = config_hash
        save_new(args.output, result)
        print(json.dumps({k: v for k, v in result.items() if k != "results" and k != "source_hashes"}, ensure_ascii=False))
        return 0 if result["overall"] == "pass" else 2
    except (OSError, ValueError, KeyError, TypeError, InvalidOperation, IndexError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
