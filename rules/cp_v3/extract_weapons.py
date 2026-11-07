"""CP4110 武器总表 -> data/weapons.json

抽取策略（对字母间距排版鲁棒）：
1. 按栏（页宽中线）分组，栏内按 y 聚成行（容差 4pt）；
2. 行内按词间 x 间隙切成单元格：gap < 1.5 直接相连（字母间距），
   1.5 ≤ gap < 7 视为词间空格，gap ≥ 7 视为单元格边界；
3. 3 格行为「名称|射程|价格」，8 格行为
   「类型|WA|隐蔽|可得|伤害|弹数|射速|可靠性」，其余为描述/分类标题。
"""
import io
import json
import os
import re

import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
PDF = r"D:\Code\Synthetic_Dawn\Cyberpunk v3 - CP4110 Core Rulebook.pdf"
PAGES = range(178, 183)

LETTER_GAP = 1.5
CELL_GAP = 7.0
ROW_TOL = 4.0
DAMAGE_RX = re.compile(r"\d+\s*D\s*6|D\s*6", re.I)
TYPE_SET = {"P", "SMG", "SHG", "RIF", "HVY", "MELEE", "EX", "BFG", "PISTOL"}


def cells_of(words):
    """words: [(x0,x1,text)] 已按 x 排序 -> 单元格文本列表"""
    out, cur, prev_x1 = [], "", None
    for x0, x1, t in words:
        if prev_x1 is not None:
            gap = x0 - prev_x1
            if gap >= CELL_GAP:
                out.append(cur.strip())
                cur = ""
            elif gap >= LETTER_GAP:
                cur += " "
        cur += t
        prev_x1 = x1
    out.append(cur.strip())
    return [c for c in out if c]


def page_rows(page):
    mid = page.rect.width / 2
    cols = {0: [], 1: []}
    for b in page.get_text("blocks"):
        if not b[4].strip():
            continue
        x0, y0, x1, y1 = b[0], b[1], b[2], b[3]
        for w in page.get_text("words"):
            if w[0] < x0 - 0.5 or w[2] > x1 + 0.5 or w[1] < y0 - 0.5 or w[3] > y1 + 0.5:
                continue
            side = 0 if (w[0] + w[2]) / 2 < mid else 1
            cols[side].append((w[1], w[0], w[2], w[4]))
        del x1, y1
    rows = []
    for side in (0, 1):
        ws = sorted(set(cols[side]), key=lambda w: (w[0], w[1]))
        lines = []
        for y, x0, x1, t in ws:
            if lines and abs(lines[-1][0] - y) <= ROW_TOL:
                lines[-1][1].append((x0, x1, t))
            else:
                lines.append([y, [(x0, x1, t)]])
        for y, words in lines:
            words.sort()
            rows.append((side, y, cells_of(words)))
    return rows


def parse_page(pno, page):
    rows = page_rows(page)
    out = []
    i = 0
    while i < len(rows):
        side, y, cells = rows[i]
        if len(cells) == 3 and re.search(r"\d+\s*M|NA|Giri|GIRI|—", cells[1], re.I):
            # 期望下一行是 8 格统计行
            for j in range(i + 1, min(i + 4, len(rows))):
                if rows[j][0] != side:
                    break
                c2 = list(rows[j][2])
                # 有时「可得性」与「伤害」被并成一格：'E 2D6+3(10mm)'
                if len(c2) == 9 and c2[-1].strip().isdigit():
                    c2 = c2[:-1]
                if len(c2) == 7:
                    m = re.match(r"^([ECPRUecpru])\s+?(\d.*[Dd]\s*6.*)$", c2[3])
                    if m:
                        c2 = c2[:3] + [m.group(1), m.group(2)] + c2[4:]
                if (
                    len(c2) == 8
                    and re.match(r"^[A-Z]{1,6}$", c2[0].strip())
                    and c2[4].strip()
                    and c2[7].strip().rstrip("†").upper() in ("VR", "ST", "UR")
                ):
                    rng = re.sub(r"\s*(REF|NA|N/A)$", "", " ".join(cells[1].split()))
                    rng = rng.replace("50MN", "50M")
                    name = " ".join(cells[0].split())
                    out.append(
                        {
                            "name": name,
                            "range": rng,
                            "cost": " ".join(cells[2].split()),
                            "type": c2[0].strip(),
                            "wa": c2[1].strip(),
                            "conceal": c2[2].strip(),
                            "availability": c2[3].strip(),
                            "damage": "".join(c2[4].split()),
                            "shots": c2[5].strip(),
                            "rof": c2[6].strip(),
                            "reliability": c2[7].strip(),
                            "page": pno,
                        }
                    )
                    i = j
                    break
        i += 1
    return out


def main() -> None:
    doc = pymupdf.open(PDF)
    items = []
    for pno in PAGES:
        got = parse_page(pno, doc.load_page(pno - 1))
        print("page", pno, "->", len(got))
        items.extend(got)
    os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
    dst = os.path.join(HERE, "data", "weapons.json")
    with io.open(dst, "w", encoding="utf-8", newline="\n") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    print("total", len(items), "->", dst)


if __name__ == "__main__":
    main()
