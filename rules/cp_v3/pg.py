"""按页读取 CP4110 (Cyberpunk V3.0) 抽取文本。

用法:
    python pg.py index                 # 打印每页首行 + 页码
    python pg.py 45                    # 打印第 45 页(1-based, 按 \f 分页)
    python pg.py 45-52                 # 打印页码范围
    python pg.py grep "NETRUNNER"      # 全库搜索并返回命中页号
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "raw", "cp_v3_full.txt")

with io.open(SRC, encoding="utf-8", errors="replace") as f:
    RAW = f.read()

PAGES = RAW.split("\f")


def head(page: str) -> str:
    for line in page.splitlines():
        s = line.strip()
        if s:
            return s[:80]
    return ""


def cmd_index() -> None:
    for i, p in enumerate(PAGES, 1):
        print("%4d | %s" % (i, head(p)))


def cmd_show(lo: int, hi: int) -> None:
    for i in range(lo, hi + 1):
        if 1 <= i <= len(PAGES):
            print("\n========== page %d ==========" % i)
            print(PAGES[i - 1].rstrip())


def cmd_grep(pat: str) -> None:
    rx = re.compile(pat, re.I)
    for i, p in enumerate(PAGES, 1):
        if rx.search(p):
            print("%4d | %s" % (i, head(p)))


def main() -> None:
    a = sys.argv[1:]
    if not a or a[0] == "index":
        cmd_index()
    elif a[0] == "grep":
        cmd_grep(a[1])
    elif re.fullmatch(r"\d+(-\d+)?", a[0]):
        lo, _, hi = a[0].partition("-")
        cmd_show(int(lo), int(hi or lo))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
