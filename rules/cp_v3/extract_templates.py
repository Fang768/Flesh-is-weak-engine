"""从 V3.0 规则书 p60-72（PDF 61-73）抽取 26 个模板：属性 / 技能 / 装备与义体 / Perks。

模板页排版极规整：每页 1 个模板，属性是 10 个连续单行数字，技能是
(name, level, Yes/No, cost) 四元组，装备是 (name, location, cost) 三元组。
文字层里名字被字母间距拆成 'H a n d g u n'，需要先还原。
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pdftext  # noqa: E402

PAGES = range(61, 74)
STAT_ORDER = ["INT", "REF", "TECH", "DEX", "COOL", "WILL", "STR", "CON", "MOVE", "BODY"]
HEADER = re.compile(r"^(?:[A-Za-z\- ]{0,30}?)\s*Template:\s*(.+)$")


def despace(s: str) -> str:
    """把 'H a n d g u n' 还原成 'Handgun'，'CITY F I G H T E R' -> 'CITY FIGHTER'。

    规则：整行基本由单字符 token 构成时，把连续单字符合并成词，多字符 token 当独立词。
    """
    s = s.strip()
    if not s:
        return s
    parts = s.split(" ")
    if sum(1 for p in parts if len(p) == 1) < len(parts) * 0.5:
        return s
    words, buf = [], ""
    for p in parts:
        if len(p) == 1:
            buf += p
        else:
            if buf:
                words.append(buf)
                buf = ""
            words.append(p)
    if buf:
        words.append(buf)
    return " ".join(words)


SKILLS_MARK = "STARTINGSKILLS"


def _flat(s: str) -> str:
    return re.sub(r"\s+", "", s).upper()


def clean_name(s: str) -> str:
    """'SECURITY O P' -> 'SECURITY OP'；'CITY F I G H T E R' -> 'CITY FIGHTER'；'Combat Fighter' -> 'COMBAT FIGHTER'。"""
    s = despace(s)
    # 残留的单字母 token 与相邻词合并（O P -> OP）
    out, buf = [], ""
    for tok in s.split():
        if len(tok) == 1:
            buf += tok
        else:
            if buf:
                out.append(buf); buf = ""
            out.append(tok)
    if buf:
        out.append(buf)
    return " ".join(out).upper()


def num(s: str):
    """'1 0' / '10' -> 10；非数字返回 None。"""
    t = re.sub(r"\s+", "", s)
    return int(t) if t.isdigit() else None


def split_blocks(seg):
    """把一段文本按 STARTING SKILLS / OUTFIT 切成数据块，返回 (skills_blocks, outfit_blocks)。"""
    skills, outfit = [], []
    i = 0
    while i < len(seg):
        if _flat(seg[i]).startswith(SKILLS_MARK):
            j = i + 1
            rows = []
            while j + 3 < len(seg) and not _flat(seg[j]).startswith("OUTFIT") and not HEADER.match(seg[j]):
                nm, lv, chip, cost = seg[j], seg[j + 1], seg[j + 2], seg[j + 3]
                lv_n, cost_n = num(lv), num(cost)
                if lv_n is not None and _flat(chip) in ("YES", "NO") and cost_n is not None:
                    rows.append({"skill": despace(nm), "level": lv_n,
                                 "chipped": _flat(chip) == "YES", "cost": cost_n})
                    j += 4
                else:
                    j += 1
            skills.append(rows)
            i = j
        elif _flat(seg[i]).startswith("OUTFIT"):
            j = i + 1
            rows = []
            while j + 2 < len(seg) and not HEADER.match(seg[j]) and not _flat(seg[j]).startswith(SKILLS_MARK):
                nm, loc, cost = seg[j], seg[j + 1], seg[j + 2]
                cost_n = num(cost)
                if cost_n is not None and num(nm) is None:
                    rows.append({"item": despace(nm), "location": loc, "cost": cost_n})
                    j += 3
                else:
                    j += 1
            outfit.append(rows)
            i = j
        else:
            i += 1
    return skills, outfit


def parse_page(page_no: int):
    text = pdftext.page_text(page_no)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # 一页可有 2 个模板标题；数据块（技能/装备）在该页是整页级的，按出现顺序配给各模板
    heads = [i for i, ln in enumerate(lines) if HEADER.match(ln)]
    if not heads:
        return []
    skills_blocks, outfit_blocks = split_blocks(lines)

    # 页面级属性组：每个 "Stat Value" 后最多 10 个连续数字（派生属性块是 12 个/空白，不计入）
    page_stats = []
    k = 0
    while k < len(lines):
        if lines[k] == "Stat Value":
            nums, j = [], k + 1
            while j < len(lines) and re.fullmatch(r"\d{1,2}", lines[j]):
                nums.append(int(lines[j]))
                j += 1
            if len(nums) == 10:
                page_stats.append(nums)
            k = j
        else:
            k += 1

    # 一页两个模板时，技能/装备表常常只有一个表头、两块数据上下叠在一起：
    # 行数为偶数就按半径劈开，前半给左模板、后半给右模板。
    if len(heads) == 2:
        for blocks in (skills_blocks, outfit_blocks):
            if len(blocks) == 1 and blocks[0] and len(blocks[0]) % 2 == 0:
                half = len(blocks[0]) // 2
                blocks[:] = [blocks[0][:half], blocks[0][half:]]

    out = []
    idx = heads + [len(lines)]
    for order, (a, b) in enumerate(zip(idx, idx[1:])):
        seg = lines[a:b]
        m = HEADER.match(seg[0])
        if not m:
            continue
        name = clean_name(m.group(1))
        tpl = {"name": name, "source_page": page_no, "raw_title": seg[0],
               "stats": None,
               "skills": skills_blocks[order] if order < len(skills_blocks) else [],
               "outfit": outfit_blocks[order] if order < len(outfit_blocks) else [],
               "perks": None, "lifepath": ""}

        # LIFEPATH 描述
        lp = [s for s in seg if s.startswith("LIFEPATH:")]
        if lp:
            tpl["lifepath"] = lp[0][len("LIFEPATH:"):].strip()

        # 属性：按模板在本页的序号取第 order 个页面级 10 数字组
        if order < len(page_stats):
            tpl["stats"] = dict(zip(STAT_ORDER, page_stats[order]))

        # Perks & Talents：本模板段内出现的特长说明行
        tail = [s for s in seg if re.search(r"(Sense|Favor|Immunity|Reputation|Perk|Talent)", s)]
        if tail:
            tpl["perks"] = despace(" / ".join(tail))

        out.append(tpl)
    return out


def main():
    templates = []
    for p in PAGES:
        templates.extend(parse_page(p))
    out = Path(__file__).parent / "data" / "templates.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(templates, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"templates: {len(templates)} -> {out}")
    for t in templates:
        st = t["stats"]
        print(f"  {t['name']:38s} stats={'ok' if st else 'MISS'} skills={len(t['skills']):2d} "
              f"outfit={len(t['outfit']):2d} perks={'y' if t['perks'] else '-'}")
        if st:
            print("      " + " ".join(f"{k}{v}" for k, v in st.items()))


if __name__ == "__main__":
    main()
