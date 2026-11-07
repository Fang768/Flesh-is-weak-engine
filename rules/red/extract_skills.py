# -*- coding: utf-8 -*-
"""从《赛博朋克红2.50.15》核心书抽出技能表（书 p81–87 = PDF p99–105）。

技能行格式：``专注·············· WILL``（名字 + 若干间隔点 + 属性）。
分类行格式：``☒感官类技能``。
"""
import io
import json
import re

FULL = r"D:\Code\Synthetic_Dawn\rules\red_core\raw\full.txt"
OUT = r"D:\Code\Synthetic_Dawn\rules\red\data\skills.json"

HEAD = re.compile(r"^[\u2612\s]*(感官|体魄|操控|教育|战斗|表演|远程武器|社交|技术)类?技能")
ROW = re.compile(r"^(?P<name>[\u4e00-\u9fff&（）()A-Za-z0-9 ·/×]{2,24}?)[\s·]{3,}(?P<stat>[A-Z]{3,4})")
STAT_FIX = {"WIL": "WILL", "COO": "COOL", "TEC": "TECH"}


def main() -> None:
    pages = io.open(FULL, encoding="utf-8").read().split("\n\f\n")
    text = "\n".join(pages[98:106])
    cat = None
    rows, seen = [], set()
    for raw in text.splitlines():
        line = raw.strip()
        h = HEAD.match(line)
        if h:
            cat = h.group(1)
            continue
        m = ROW.match(line)
        if not m:
            continue
        name = m.group("name").replace(" ", "").strip(" ·")
        if not name or len(name) < 2:
            continue
        cost2 = "×2" in name
        name = re.sub(r"[（(]×2[)）]", "", name).strip()
        stat = STAT_FIX.get(m.group("stat"), m.group("stat"))
        if name in seen:
            continue
        seen.add(name)
        rows.append({"cat": cat, "zh": name, "stat": stat, "cost2": cost2})
    io.open(OUT, "w", encoding="utf-8", newline="\n").write(
        json.dumps(rows, ensure_ascii=False, indent=1))
    print("技能数:", len(rows))
    for r in rows:
        print(f'  {r["cat"] or "?":<4} {r["zh"]:<14} {r["stat"]:<4} {"×2" if r["cost2"] else ""}')


if __name__ == "__main__":
    main()
