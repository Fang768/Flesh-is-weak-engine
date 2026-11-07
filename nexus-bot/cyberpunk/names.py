"""中英术语表：让引擎吃英文键、对玩家说中文。

数据源 ``rules/cp_v3/data/glossary.json``（书 p157 的 95 项技能 + 十属性 + 常用战斗术语）。
译文口径：优先沿用《Cyberpunk RED Easy Mode》简中版已有译名，V3 独有技能按同风格直译。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

GLOSSARY_FILE = (
    Path(__file__).resolve().parents[2] / "rules" / "cp_v3" / "data" / "glossary.json"
)


@lru_cache(maxsize=1)
def _data() -> dict:
    if not GLOSSARY_FILE.exists():
        return {"stats": {}, "skills": [], "categories": {}, "terms": {}}
    return json.loads(GLOSSARY_FILE.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def stats_en_to_zh() -> dict[str, str]:
    return dict(_data().get("stats", {}))


@lru_cache(maxsize=1)
def skills() -> list[dict]:
    return list(_data().get("skills", []))


@lru_cache(maxsize=1)
def skill_zh() -> dict[str, str]:
    """英文技能名 → 中文。"""
    return {s["en"]: s["zh"] for s in skills()}


@lru_cache(maxsize=1)
def skill_en() -> dict[str, str]:
    """中文技能名 → 英文。"""
    return {s["zh"]: s["en"] for s in skills()}


@lru_cache(maxsize=1)
def skill_stat() -> dict[str, str]:
    """中文技能名 → 关联属性（引擎内部仍用英文属性键）。"""
    return {s["zh"]: s["stat"] for s in skills()}


@lru_cache(maxsize=1)
def skill_category() -> dict[str, str]:
    return {s["zh"]: s["cat"] for s in skills()}


def to_zh(name: str) -> str:
    """英文 → 中文；已经是中文就原样返回。"""
    text = (name or "").strip()
    if not text:
        return ""
    if text in skill_en():
        return text
    for en, zh in skill_zh().items():
        if en.lower() == text.lower():
            return zh
    return text


def to_en(name: str) -> str:
    """中文 → 英文；本来就是英文就原样返回。"""
    text = (name or "").strip()
    if not text:
        return ""
    if text in skill_zh():
        return text
    return skill_en().get(text, text)


def resolve_skill(text: str) -> str | None:
    """把玩家输入（中文/英文/别名/片段）解析成中文技能名。"""
    raw = (text or "").strip()
    if not raw:
        return None
    if raw in skill_en():
        return raw
    lower = raw.lower()
    for zh, en in skill_en().items():
        if en.lower() == lower:
            return zh
    # 片段匹配：唯一才认（"手枪"→手枪；"驾驶"有多个 → 拒绝，让玩家说清楚）
    hits = [zh for zh in skill_en() if raw in zh]
    if len(hits) == 1:
        return hits[0]
    hits_en = [zh for zh, en in skill_en().items() if lower in en.lower()]
    if len(hits_en) == 1:
        return hits_en[0]
    return None


# 属性中文名的主流译法（2077 简中优先）与可接受别名
STAT_ALIASES = {
    "智力": "INT", "反应": "REF", "技术": "TECH", "技术能力": "TECH",
    "敏捷": "DEX", "镇定": "COOL", "酷": "COOL", "意志": "WILL",
    "力量": "STR", "体格": "BODY", "身体": "BODY", "体质": "CON",
    "移动": "MOVE", "共情": "EMP", "幸运": "LUCK",
}


def stat_code(text: str) -> str | None:
    """把属性写成中文/英文/别名都认：镇定、酷、COOL、身体 → 代码。"""
    key = (text or "").strip()
    if not key:
        return None
    if key.upper() in stats_en_to_zh():
        return key.upper()
    if key in STAT_ALIASES:
        return STAT_ALIASES[key]
    for zh, code in stats_en_to_zh().items():
        if key == zh:
            return code
    return None


def stat_of(skill_zh_name: str) -> str | None:
    return skill_stat().get(skill_zh_name)


def category_of(skill_zh_name: str) -> str | None:
    return skill_category().get(skill_zh_name)


def display(name: str) -> str:
    """展示用：「中文（English）」，中文名就是原名时不加括号。"""
    zh = to_zh(name)
    en = to_en(zh)
    if not en or en == zh:
        return zh
    return f"{zh}（{en}）"


def term(text: str) -> str:
    return _data().get("terms", {}).get(text, text)


#: 模板里抽出来的技能名带字母间距残留、还混着书里的别名。
#: 这张表把原始串映射到 glossary 里的规范英文名（再 to_zh）。
TEMPLATE_SKILL_MAP = {
    "A rc h e ry or Handgun (choose)": "Archery",
    "Animal Kung Fu": "Martial Art",
    "Automatic We apons": "Submachinegun",
    "Aw areness": "Awareness/Notice",
    "Basic Te ch": "Basic Tech",
    "Brawling": "Brawling/Hand to Hand",
    "Dive Te ch": "Subdock",
    "Dodge/Escape": "Dodge & Escape",
    "Education": "Education & Gen.Know.",
    "Gunne ry": "Gunnery",
    "Handgun or Melee (choose)": "Handgun",
    "Heavy We apons": "Heavy Weapons",
    "Human Perc eption": "Human Perception",
    "Inte rf ace": "Interface",
    "Ju ry Rig": "Jury Rig",
    "Karate": "Martial Art",
    "Libra ry Searc h": "Library Search",
    "Link": "Link (mecha)",
    "Martial Arts": "Martial Art",
    "Mecha Te ch": "Mecha Tech",
    "Melee or Hvy Weapons (choose)": "Melee",
    "Pe rf orm": "Perform",
    "Persuasion": "Persuasion & Fast Talk",
    "Persuasion & Fast Ta lk": "Persuasion & Fast Talk",
    "Pilot (Fixed Wing) or Driving": "Pilot (Fixed Wing)",
    "Rifle or Handgun (choose)": "Rifle",
    "Rifle or Melee (choose)": "Rifle",
    "Subjock": "Subdock",
    "Tr ading": "Trading",
}


def normalize_template_skill(raw: str) -> str:
    """模板技能名 → 术语表中文名；认不出来就原样返回。"""
    text = (raw or "").strip()
    if not text:
        return ""
    mapped = TEMPLATE_SKILL_MAP.get(text)
    if mapped:
        return to_zh(mapped)
    return to_zh(text)


def markdown_table() -> str:
    """按分类生成 markdown 术语表（给 docs/00-术语表.md 用）。"""
    cats = _data().get("categories", {})
    order = ["Fighting", "Ranged", "Awareness", "Control", "Body",
             "Interaction", "Technique", "Performance", "Education"]
    lines: list[str] = []
    for cat in order:
        rows = [s for s in skills() if s["cat"] == cat]
        if not rows:
            continue
        lines.append(f"### {cats.get(cat, cat)}（{cat}）")
        lines.append("| 中文 | English | 关联属性 |")
        lines.append("|---|---|---|")
        for s in rows:
            lines.append(
                f"| {s['zh']} | {s['en']} | {s['stat']} {stats_en_to_zh().get(s['stat'], '')} |"
            )
        lines.append("")
    return chr(10).join(lines)
