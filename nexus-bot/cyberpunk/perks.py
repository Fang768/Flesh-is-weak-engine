"""专长（Perk）与天赋（Talent）：2077 简中口径的中文名、成本与效果查询。

数据源：`rules/cp_v3/data/perks_zh.json`（源自书 p152–157）。
命名口径：Perk → 专长；Talent → 天赋。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

PERKS_FILE = Path(__file__).resolve().parents[2] / "rules/cp_v3/data/perks_zh.json"

#: 战役影响倍率（书 p152）：能左右世界 ×4，到"没什么影响" ×1
IMPACT_MULTIPLIER = {
    "very high": 4,
    "high": 3,
    "moderate": 2,
    "little": 1,
    "none": 1,
}

#: 这些天赋的加成是「每级 +N 技能」，供 characters.effective_skill 使用
TALENT_SKILL_BONUS = {
    "Beautiful or Handsome": {"说服": 1, "表演": 1, "衣着与风格": 1},
    "Perfect Pitch": {"乐器演奏": 2},
    "Lightning Calculator": {"会计": 2, "商业": 2, "密码学": 2},
}

#: 这些天赋给固定属性/检定加成，供展示与结算提示
TALENT_FLAT_BONUS = {
    "Combat Sense": {"先攻": 2},
    "High Pain Threshold": {"重伤惩罚": -1},
    "Rapid Healing": {"每日恢复": 3},
    "Acute Senses": {"觉察": 2},
    "Schtick": {"自选动作": 3},
}


@lru_cache(maxsize=1)
def _data() -> dict:
    with PERKS_FILE.open(encoding="utf-8") as fh:
        return json.load(fh)


def perks() -> dict[str, dict]:
    return dict(_data().get("perks", {}))


def talents() -> dict[str, dict]:
    return dict(_data().get("talents", {}))


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def find(name: str) -> tuple[str, str, dict] | None:
    """按中文名、英文名或片段查一条，返回 (类别, 英文键, 条目)。"""
    key = (name or "").strip()
    if not key:
        return None
    for kind, table in (("perk", perks()), ("talent", talents())):
        for en, item in table.items():
            if key == en or key == item["zh"]:
                return kind, en, item
    low = _norm(key)
    for kind, table in (("perk", perks()), ("talent", talents())):
        for en, item in table.items():
            if low and (low in _norm(en) or low in _norm(item["zh"])):
                return kind, en, item
    return None


def perk_cost(base: float, impact: str = "moderate") -> float:
    """最终 CP 花费 = 基础价 × 影响倍率。"""
    mult = IMPACT_MULTIPLIER.get((impact or "moderate").strip().lower(), 2)
    return round(float(base) * mult, 2)


def cost_table(impact: str = "moderate") -> list[tuple[str, str, float]]:
    """所有专长的 (中文名, 说明, 该战役风格下的花费)，按花费降序。"""
    rows = []
    for en, item in perks().items():
        base = float(item.get("base", 1 if en != "Favor" else 0.5))
        rows.append((item["zh"], item["note"], perk_cost(base, impact)))
    return sorted(rows, key=lambda r: (-r[2], r[0]))


def format_entry(kind: str, en: str, item: dict) -> str:
    tag = "🏅 专长" if kind == "perk" else "✨ 天赋"
    return f"{tag} · {item['zh']}（{en}）\n{item['note']}\n效果：{item['effect']}"


def search(keyword: str, limit: int = 6) -> list[tuple[str, str, dict]]:
    key = (keyword or "").strip()
    hits: list[tuple[str, str, dict]] = []
    for kind, table in (("perk", perks()), ("talent", talents())):
        for en, item in table.items():
            if not key or key in item["zh"] or key.lower() in en.lower() or key in item["note"] or key in item["effect"]:
                hits.append((kind, en, item))
    hits.sort(key=lambda h: (0 if h[2]["zh"] == key else 1, h[0], h[2]["zh"]))
    return hits[:limit]


def validate(names: list[str]) -> list[tuple[str, str, dict]]:
    """把玩家写的名字列表变成条目；有不认识的就抛 ValueError。"""
    out = []
    for raw in names or []:
        hit = find(raw)
        if hit is None:
            raise ValueError(f"没有「{raw}」这个专长或天赋")
        out.append(hit)
    return out


def talent_skill_bonus(names: list[str]) -> dict[str, int]:
    """把天赋列表折算成「技能 → 加值」。"""
    bonus: dict[str, int] = {}
    for raw in names or []:
        hit = find(raw)
        if hit and hit[0] == "talent":
            for skill, value in TALENT_SKILL_BONUS.get(hit[1], {}).items():
                bonus[skill] = bonus.get(skill, 0) + value
    return bonus


def summary(en_list: list[str]) -> str:
    """一行展示：专长 权威；天赋 俊美、夜视。"""
    p, t = [], []
    for raw in en_list or []:
        hit = find(raw)
        if not hit:
            continue
        (p if hit[0] == "perk" else t).append(hit[2]["zh"])
    parts = []
    if p:
        parts.append("专长 " + "、".join(p))
    if t:
        parts.append("天赋 " + "、".join(t))
    return "；".join(parts)
