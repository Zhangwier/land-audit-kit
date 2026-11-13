"""把业主发来的 zip 解压到底稿目录，修正 GBK 文件名乱码；原 zip 只读不动。

用法：python unzip_gbk.py 源.zip 目标目录
包内若还有 zip，就地再解一层（解到同名文件夹）。目标已存在的同名文件跳过，不覆盖。
"""
import sys
import zipfile
from pathlib import Path


def fix_name(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x800:  # 已是 UTF-8
        return info.filename
    try:
        return info.filename.encode("cp437").decode("gbk")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def extract(src: Path, dst: Path) -> list[Path]:
    out = []
    with zipfile.ZipFile(src) as z:
        for info in z.infolist():
            name = fix_name(info)
            target = dst / name
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as r, open(target, "wb") as w:
                while chunk := r.read(1 << 20):
                    w.write(chunk)
            out.append(target)
    return out


def main():
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    files = extract(src, dst)
    for f in list(files):
        if f.suffix.lower() == ".zip":
            files += extract(f, f.with_suffix(""))
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"解压 {len(files)} 个文件 → {dst}")


if __name__ == "__main__":
    main()
