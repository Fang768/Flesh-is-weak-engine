# -*- coding: utf-8 -*-
"""从核心书抽 10 个职业模板的属性表（书 p74–77 = PDF p92–95）。

版式：每个模板一个 ``▶`` 块，表头后是 **10 行 × 11 个数字**
（第 1 个是骰点 1–10，后面 10 个是 智力…共情）。
"""
import io
import json
import re

FULL = r"D:\Code\Synthetic_Dawn\rules\red_core\raw\full.txt"
OUT = r"D:\Code\Synthetic_Dawn\rules\red\data\templates.json"

STAT_ZH = ["智力", "反应", "敏捷", "技术", "酷", "意志", "运气", "移速", "体格", "共情"]
STAT_CODES = ["INT", "REF", "DEX", "TECH", "COOL", "WILL", "LUCK", "MOVE", "BODY", "EMP"]
NAMES = ["摇滚小子", "独狼", "网行者", "技工", "技医", "媒体人", "执法者", "主管", "掮客", "游民"]


def main() -> None:
    pages = io.open(FULL, encoding="utf-8").read().split("\n\f\n")
    text = "\n".join(pages[91:95])
    blocks = text.split("▶")[1:]
    out = []
    for blk in blocks:
        lines = [l.strip() for l in blk.splitlines() if l.strip()]
        name = next((n for n in NAMES if n in lines), None)
        if not name or any(t["name"] == name for t in out):
            continue
        nums = [int(l) for l in lines if re.fullmatch(r"\d{1,2}", l)]
        rows = []
        for i in range(0, len(nums) - 10, 11):
            chunk = nums[i:i + 11]
            if chunk[0] != len(rows) + 1:
                continue
            rows.append(chunk[1:])
            if len(rows) == 10:
                break
        if len(rows) != 10:
            print(f"!! {name} 只抽到 {len(rows)} 行")
            continue
        out.append({
            "name": name,
            "zh": name,
            "stat_order": STAT_CODES,
            "rows": [dict(zip(STAT_CODES, r)) for r in rows],
            "row1": dict(zip(STAT_CODES, rows[0])),
        })
    io.open(OUT, "w", encoding="utf-8", newline="\n").write(
        json.dumps(out, ensure_ascii=False, indent=1))
    print("模板数:", len(out))
    for t in out:
        r = t["row1"]
        print("  " + t["name"] + "  1行 " + "/".join(str(r[c]) for c in STAT_CODES))


if __name__ == "__main__":
    main()
