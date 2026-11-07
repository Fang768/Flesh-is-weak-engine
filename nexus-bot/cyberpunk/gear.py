"""装备：武器中文名、风味描述与检索接口。

数据：``rules/cp_v3/data/weapons.json``（英文规则数据，抽取自书 p177–180）
+ ``weapons_zh.json``（中文名 + 一句话描述，键为英文全名）。

两者用英文全名对齐；为避免字母间距排版留下的空格差异，查找时用
:func:`_key` 做「只保留大写字母与数字」的归一化比较。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "rules" / "cp_v3" / "data"
WEAPONS_FILE = DATA_DIR / "weapons.json"
WEAPONS_ZH_FILE = DATA_DIR / "weapons_zh.json"

TYPE_ZH = {
    "P": "手枪",
    "SMG": "冲锋枪",
    "SHT": "霰弹枪",
    "SHG": "霰弹枪",
    "RIF": "步枪",
    "HVY": "重型武器",
    "MELEE": "近战武器",
    "EX": "异型武器",
    "BFG": "弹道飞镖枪",
    "RBFG": "轨道飞镖枪",
    "FABFG": "全自动飞镖枪",
}

CONCEAL_ZH = {"P": "可藏口袋", "J": "可藏夹克", "L": "只能长风衣", "N": "藏不住"}
AVAIL_ZH = {"E": "到处都有", "C": "常见", "P": "不好搞", "R": "稀有", "U": "只能靠人情"}
RELIABILITY_ZH = {"VR": "极可靠", "ST": "可靠", "UR": "不太可靠"}
COST_ZH = {"REF": "价格由裁判定", "GIRI": "要用人情债买", "NA": "—", "N/A": "—"}


def _key(name: str) -> str:
    """归一化：去掉空格、标点，只留大写字母与数字。"""
    return re.sub(r"[^A-Z0-9]", "", (name or "").upper())


@lru_cache(maxsize=1)
def _raw_weapons() -> list[dict]:
    if not WEAPONS_FILE.exists():
        return []
    return json.loads(WEAPONS_FILE.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _zh_map() -> dict[str, dict]:
    if not WEAPONS_ZH_FILE.exists():
        return {}
    items = json.loads(WEAPONS_ZH_FILE.read_text(encoding="utf-8")).get("items", {})
    return {_key(name): meta for name, meta in items.items()}


def weapons() -> list[dict]:
    """英文规则数据 + 中文名的合并视图。"""
    table = _zh_map()
    out: list[dict] = []
    for item in _raw_weapons():
        entry = dict(item)
        meta = table.get(_key(item["name"]), {})
        entry["name_zh"] = meta.get("zh") or item["name"]
        entry["note_zh"] = meta.get("note", "")
        entry["type_zh"] = TYPE_ZH.get(entry.get("type", ""), entry.get("type", ""))
        out.append(entry)
    return out


def get(name: str) -> dict | None:
    """按中文名或英文名精确取一把枪。"""
    want = _key(name)
    if not want:
        return None
    for entry in weapons():
        if _key(entry["name"]) == want or _key(entry["name_zh"]) == want:
            return entry
    return None


def search(keyword: str, limit: int = 6) -> list[dict]:
    """中文/英文关键词模糊查；类型词（手枪/步枪/近战…）也能命中。"""
    raw = (keyword or "").strip()
    if not raw:
        return []
    key = raw.lower()
    type_hits = set()
    for code, label in TYPE_ZH.items():
        if key in label or key in code.lower():
            type_hits.add(code)
            if code == "SHG":
                type_hits.add("SHT")

    scored: list[tuple[int, str, dict]] = []
    for entry in weapons():
        haystack = f"{entry['name']} {entry['name_zh']} {entry['note_zh']}".lower()
        name_hit = key in entry["name_zh"].lower()
        if key in haystack:
            scored.append((0 if name_hit else 1, entry["name_zh"], entry))
        elif type_hits and entry.get("type") in type_hits:
            scored.append((2, entry["name_zh"], entry))
    scored.sort(key=lambda row: (row[0], row[1]))
    return [row[2] for row in scored[:limit]]


def format_cost(cost: str) -> str:
    return COST_ZH.get((cost or "").upper(), cost)


def format_card(entry: dict) -> str:
    """给玩家看的中文武器卡。"""
    lines = [
        f"🔫 {entry['name_zh']}（{entry['name']}）",
        f"类型 {entry['type_zh']} · 伤害 {entry['damage']} · 射程 {entry['range']}"
        f" · 弹数 {entry['shots']} · 射速 {entry['rof']}",
        f"精度 {entry['wa']} · {CONCEAL_ZH.get(entry['conceal'], entry['conceal'])}"
        f" · {AVAIL_ZH.get(entry['availability'], entry['availability'])}"
        f" · {RELIABILITY_ZH.get(entry['reliability'], entry['reliability'])}"
        f" · {format_cost(entry['cost'])}",
    ]
    if entry.get("note_zh"):
        lines.append(f"「{entry['note_zh']}」")
    return chr(10).join(lines)
