"""按「行 → 单元格」结构化提取 CP4110 页内容（解决双栏与表格错位）。

pdftotext 会把双栏并成一行、把表格单元格拆成独立块。
这里用 pymupdf 的 blocks，先按栏（左右）分开，再在栏内按 y 聚成行，
行内按 x 排序，用 " | " 连接，得到稳定可解析的表格行。

用法:
    python pdftext.py text 178-180      # 栏内按行输出的纯文本
    python pdftext.py rows 178-180      # 每行用 | 分列
"""
import sys

import pymupdf

PDF = r"D:\Code\Synthetic_Dawn\Cyberpunk v3 - CP4110 Core Rulebook.pdf"
TOL = 3.0  # 同一行的 y 容差


def _clean(s: str) -> str:
    return " ".join(s.split())


def page_rows(page_no: int):
    """返回 [(栏, y, [单元格文本…]), …]，栏 0=通栏 1=左 2=右。"""
    doc = pymupdf.open(PDF)
    page = doc.load_page(page_no - 1)
    mid = page.rect.width / 2
    items = []  # (col, y0, x0, text)
    for b in page.get_text("blocks"):
        x0, y0, x1, y1, text = b[0], b[1], b[2], b[3], b[4]
        t = _clean(text)
        if not t:
            continue
        if x0 < mid - 40 and x1 > mid + 40:
            col = 0
        else:
            col = 1 if (x0 + x1) / 2 < mid else 2
        # 块内多行的，逐行拆开
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) <= 1:
            items.append((col, y0, x0, t))
        else:
            h = (y1 - y0) / max(len(lines), 1)
            for k, ln in enumerate(lines):
                items.append((col, y0 + k * h, x0, _clean(ln)))
    items.sort(key=lambda r: (r[0], round(r[1] / TOL), r[2]))
    rows = []
    for col, y, x, t in items:
        if rows and rows[-1][0] == col and abs(rows[-1][1] - y) <= TOL:
            rows[-1][2].append((x, t))
        else:
            rows.append([col, y, [(x, t)]])
        rows[-1][1] = (rows[-1][1] + y) / 2 if len(rows[-1][2]) > 1 else y
    out = []
    for col, y, cells in rows:
        cells.sort()
        out.append((col, y, [c[1] for c in cells]))
    return out


def page_text(page_no: int, joiner: str = " ") -> str:
    return "\n".join(joiner.join(cells) for _, _, cells in page_rows(page_no))


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "text"
    rng = sys.argv[2] if len(sys.argv) > 2 else "178"
    lo, _, hi = rng.partition("-")
    for i in range(int(lo), int(hi or lo) + 1):
        print("\n========== page %d ==========" % i)
        if mode == "rows":
            for col, _, cells in page_rows(i):
                print("[%d] %s" % (col, " | ".join(cells)))
        else:
            print(page_text(i))


if __name__ == "__main__":
    main()
