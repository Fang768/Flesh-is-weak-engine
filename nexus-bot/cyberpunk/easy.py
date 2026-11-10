"""简易版（Easy Mode）的认领模板。

数据：``rules/cpr_easy/data/easy_templates.json``（书 p37–46 的 5 张预设卡 + 自制卡）。
卡面数值（生命值、重伤阈值、死亡豁免）在简易版里是**卡上写死的**，不是算出来的：

* 生命值由卡给定（35 / 40 / 45 这几档）
* 重伤阈值 = 生命值 // 2
* 死亡豁免 = 体魄 BODY
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

TEMPLATES_FILE = (Path(__file__).resolve().parents[2]
                  / "rules" / "cpr_easy" / "data" / "easy_templates.json")


def _cyber_names(items) -> list[str]:
    """义体列表 → 名字列表（兼容旧字符串与新实例记录 dict）。"""
    out = []
    for x in items or []:
        if isinstance(x, dict):
            nm = str(x.get("name") or "")
            if nm:
                out.append(nm)
        elif x:
            out.append(str(x))
    return out

STAT_ZH = {
    "INT": "智力", "REF": "反应", "DEX": "敏捷", "TECH": "技术", "COOL": "镇定",
    "WILL": "意志", "LUCK": "幸运", "MOVE": "移动", "BODY": "体魄", "EMP": "共情",
}


@lru_cache(maxsize=1)
def _raw() -> list[dict]:
    if not TEMPLATES_FILE.exists():
        return []
    return list(json.loads(TEMPLATES_FILE.read_text(encoding="utf-8")).get("templates", []))


def templates() -> list[dict]:
    out = []
    for t in _raw():
        entry = dict(t)
        entry["wound"] = entry["hp"] // 2
        entry["death_save"] = entry["stats"].get("BODY", 0)
        out.append(entry)
    return out


def get(name: str) -> dict | None:
    want = (name or "").strip().lower()
    if not want:
        return None
    for t in templates():
        if t["name"].lower() == want or t["id"].lower() == want:
            return t
    # 允许用职业/别名匹配：「技工」「佣兵」
    for t in templates():
        if want in t["role"].lower() or want in t["blurb"].lower():
            return t
    return None


def names() -> list[str]:
    return [t["name"] for t in templates()]


def format_list() -> str:
    lines = ["可认领的简易版人物（发「/认领 名字」直接建卡）："]
    for t in templates():
        lines.append(f"· {t['name']}（{t['role']}）— {t['blurb']}")
    return chr(10).join(lines)


def format_card(t: dict) -> str:
    stats = " ".join(f"{STAT_ZH[k]}{v}" for k, v in t["stats"].items())
    skills = "、".join(f"{k}{v}" for k, v in list(t["skills"].items())[:8])
    lines = [
        f"🎭 {t['name']}（{t['role']}）",
        t["blurb"],
        f"属性：{stats}",
        f"生命 {t['hp']} · 重伤 {t['wound']} · 死亡豁免 {t['death_save']}",
        f"护甲：{t['armor']} SP {t['sp']}",
        "武器：" + "；".join(t["weapons"]),
        f"技能（部分）：{skills} …",
        f"职业能力：{t['ability']}",
        f"义体：{'；'.join(_cyber_names(t['cyber']))}",
        f"开场钩子：{t['hook']}",
    ]
    return chr(10).join(lines)


# ── 简易版 41+1 项技能的关联属性（书 p11–14 + 外科手术）────────────
STAT_ORDER = ("INT", "REF", "DEX", "TECH", "COOL", "WILL", "LUCK", "MOVE", "BODY", "EMP")

SKILL_STAT = {
    "会计": "INT", "表演": "COOL", "运动": "DEX", "徒手搏斗": "DEX", "贿赂": "COOL",
    "官僚世故": "INT", "商业": "INT", "创作": "INT", "藏匿/搜寻物品": "INT",
    "专注": "WILL", "交谈": "EMP", "犯罪学": "INT", "密码学": "INT", "推理": "INT",
    "驾驶地面载具": "REF", "教育": "INT", "电子/安防技术": "TECH", "闪避": "DEX",
    "急救": "TECH", "伪造": "TECH", "手枪": "REF", "察言观色": "EMP", "审讯": "COOL",
    "图书馆检索": "INT", "本地专家": "INT", "近战武器": "DEX", "医疗": "TECH",
    "觉察": "INT", "说服": "COOL", "摄影": "TECH", "撬锁": "TECH", "扒窃": "TECH",
    "乐器演奏": "TECH", "抵抗拷问/药物": "WILL", "抵肩枪械": "REF", "潜行": "DEX",
    "街头智慧": "COOL", "战术": "INT", "追踪": "INT", "交易": "COOL",
    "衣着与风格": "COOL", "外科手术": "TECH",
}


def skill_stat(skill: str) -> str | None:
    """技能 → 属性。

    **RED 优先**（``rules/red_core/data/skills.json``）：简易卡已把技能名统一成
    RED 正典，``藏匿&揭示物体`` / ``折磨&药物耐受`` / ``长枪射击`` 这些只在 RED
    表里。认不出再回退到 Easy Mode 自己那份 42 项表。
    """
    from . import skills as red

    found = red.stat_of(skill)
    if found:
        return found
    key = (skill or "").strip()
    if key in SKILL_STAT:
        return SKILL_STAT[key]
    for name, stat in SKILL_STAT.items():
        if key and (key in name or name in key):
            return stat
    return None


def resolve_skill(skill: str, known) -> tuple[str | None, int]:
    """在卡面技能里找最匹配的一个：先精确，再按 RED 正典名归一，再双向包含。

    返回 (规范技能名, 等级)；找不到返回 (None, 0)。
    """
    from . import skills as red

    key = (skill or "").strip()
    if not key:
        return None, 0
    if key in known:
        return key, int(known[key])
    # 玩家可能用 Easy 旧名 / V3 名来问，先折成 RED 正典再看卡上有没有
    canon = red.canonical(key)
    if canon in known:
        return canon, int(known[canon])
    for name in known:
        if key in name or name in key:
            return name, int(known[name])
    for name in SKILL_STAT:
        if (key in name or name in key) and name in known:
            return name, int(known[name])
    return None, 0


def ability_bonus(card: dict, *, skill: str = "",
                  initiative: bool = False) -> tuple[int, list[str]]:
    """职业能力加值（``ability_bonus`` 字段，运行时加，**不烘焙进卡面**）。

    简易书里「制造者：电子/安防技术 +4（已计入数据）」「战斗意识：先攻 +4」
    这类能力，现在拆成字段：技能加值按技能名精确匹配，``先攻`` 是特殊键
    （只在算先攻时加）。返回 ``(加值合计, 来源说明)``。
    """
    from . import skills as red

    table = card.get("ability_bonus") or {}
    if not isinstance(table, dict) or not table:
        return 0, []
    total = 0
    notes: list[str] = []
    if initiative:
        value = int(table.get("先攻") or 0)
        if value:
            total += value
            notes.append(f"职业能力先攻{value:+d}")
        return total, notes
    want = red.canonical(skill)
    for key, raw in table.items():
        if key == "先攻":
            continue
        if want and red.canonical(str(key)) == want:
            value = int(raw or 0)
            if value:
                total += value
                notes.append(f"职业能力{key}{value:+d}")
    return total, notes
