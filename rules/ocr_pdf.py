# -*- coding: utf-8 -*-
"""把 PDF 逐页渲染成图片再用 RapidOCR 识别中文，输出每页 txt 与合并 txt。

用法:
  python ocr_pdf.py <pdf> <outdir> [--dpi 200] [--pages 1-49]

输出:
  <outdir>/page_001.txt ...    每页文本（按行）
  <outdir>/full.md             合并文本，页间用 '\\n\\n--- page N ---\\n\\n' 分隔
  <outdir>/ocr.log             进度与耗时
"""
import io
import os
import sys
import time

import fitz  # pymupdf
import numpy as np
from rapidocr_onnxruntime import RapidOCR


def parse_pages(spec, total):
    if not spec:
        return list(range(1, total + 1))
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return [p for p in out if 1 <= p <= total]


def order_by_column(result, img_width):
    """把 OCR 框按阅读顺序排列：先判断是否两栏，再按栏内 y 排序。"""
    items = []
    for box, txt, score in result:
        txt = (txt or "").strip()
        if not txt:
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        items.append((sum(xs) / 4.0, sum(ys) / 4.0, min(ys), min(xs), max(xs), txt))
    if not items:
        return []
    # 用 x 中心找最大间隙判断是否双栏
    centers = sorted(it[0] for it in items)
    gap, split_x = 0.0, None
    for a, b in zip(centers, centers[1:]):
        if b - a > gap:
            gap, split_x = b - a, (a + b) / 2.0
    two_col = (
        split_x is not None
        and gap > 0.12 * img_width
        and sum(1 for it in items if it[0] < split_x) >= 0.3 * len(items)
        and sum(1 for it in items if it[0] >= split_x) >= 0.3 * len(items)
    )
    if not two_col:
        items.sort(key=lambda it: (round(it[1] / 12.0), it[0]))
        return [it[5] for it in items]

    def col_of(it):
        # 跨栏的宽块（标题、页码）视为横跨整页，独立成组放在前面/后面处理
        if it[4] - it[3] > 0.55 * img_width:
            return 1
        return 0 if it[0] < split_x else 2

    spans = [it for it in items if col_of(it) == 1]
    left = sorted([it for it in items if col_of(it) == 0], key=lambda it: it[1])
    right = sorted([it for it in items if col_of(it) == 2], key=lambda it: it[1])
    # 跨栏块按其 y 位置插回：简单做法是 y 小于左栏首块的一半就放最前，否则放中间
    head, tail = [], []
    if left:
        mid_y = left[len(left) // 2][1]
    else:
        mid_y = 0
    for it in sorted(spans, key=lambda it: it[1]):
        (head if it[1] < mid_y else tail).append(it[5])
    return head + [it[5] for it in left] + tail + [it[5] for it in right]


def main():
    pdf_path = sys.argv[1]
    outdir = sys.argv[2]
    dpi = 200
    pages_spec = ""
    i = 3
    while i < len(sys.argv):
        if sys.argv[i] == "--dpi":
            dpi = int(sys.argv[i + 1]); i += 2
        elif sys.argv[i] == "--pages":
            pages_spec = sys.argv[i + 1]; i += 2
        else:
            i += 1

    os.makedirs(outdir, exist_ok=True)
    doc = fitz.open(pdf_path)
    total = doc.page_count
    pages = parse_pages(pages_spec, total)
    log = io.open(os.path.join(outdir, "ocr.log"), "w", encoding="utf-8", newline="\n")

    def say(msg):
        log.write(msg + "\n")
        log.flush()
        print(msg, flush=True)

    say("pdf=%s pages=%d dpi=%d target=%d" % (pdf_path, total, dpi, len(pages)))
    ocr = RapidOCR()
    combined = []
    t_all = time.time()
    for n in pages:
        t0 = time.time()
        page = doc.load_page(n - 1)
        pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        img = np.ascontiguousarray(img[:, :, ::-1])  # RGB -> BGR
        result, _ = ocr(img)
        lines = []
        if result:
            lines = order_by_column(result, pix.width)
        text = "\n".join(lines)
        with io.open(os.path.join(outdir, "page_%03d.txt" % n), "w",
                     encoding="utf-8", newline="\n") as f:
            f.write(text)
        combined.append("--- page %d ---\n\n%s" % (n, text))
        say("page %d: %d lines, %.1fs" % (n, len(lines), time.time() - t0))

    with io.open(os.path.join(outdir, "full.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n\n".join(combined) + "\n")
    say("done in %.1fs -> %s" % (time.time() - t_all, os.path.join(outdir, "full.md")))
    log.close()


if __name__ == "__main__":
    main()
