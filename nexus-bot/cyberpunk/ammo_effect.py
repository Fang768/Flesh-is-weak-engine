"""命中后检定：特殊弹种与附毒的效果结算。

数据源 ``rules/red_core/data/ammo_effects.json``（``full.txt:26362-26510``）。

规则口径（逐条对过原文）：

| 弹种 | 检定 | DV | 失败效果 | 原文 |
|---|---|---|---|---|
| 生物毒素弹 | 折磨&药物耐受 | 15 | 直接 3d6，护甲不削减 | `:26372-26380` |
| 毒气弹 | 折磨&药物耐受 | 13 | 直接 2d6，护甲不削减 | `:26442-26450` |
| 麻醉弹 | 折磨&药物耐受 | 13 | 倒地 + 失去意识 1 分钟 | `:26463-26472` |
| 催泪弹 | 折磨&药物耐受 | 13 | 眼部受损 1 分钟 | `:26503-26509` |
| 闪光弹 | 折磨&药物耐受 | 15 | 眼部受损 + 耳朵受损 1 分钟 | `:26419-26428` |
| EMP 弹 | 赛博技术 | 15 | 2 件赛博组件失效 1 分钟 | `:26383-26393` |

关键点：

* 这几类弹**「攻击不造成任何伤害」**——所以命中判定照常做（要确认打没打中），
  但命中后**不进护甲减伤流程**，改投一次检定。
* 伤害是**直接 HP**（``direct``）：「他们的护甲不会被削减，因为它不会与之产生交互」。
* 「打到肉的人」：纯机械目标（炮塔/无人机）不吃生物毒素/毒气/麻醉/催泪/闪光。
* 1 分钟 = **20 轮**（``:11327-11331`` 一个回合 3 秒）。
* 免疫：鼻滤器免毒气（``cyberpunk/immunity.py``）、防眩晕免闪光、
  硬化防护免 EMP（``:10412-10418``）。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA_FILE = (Path(__file__).resolve().parents[2]
             / "rules" / "red_core" / "data" / "ammo_effects.json")

#: 1 分钟 = 20 轮（1 轮 3 秒，full.txt:11327-11331）
ROUNDS_PER_MINUTE = 20

#: 弹种 → 免疫类别（用来查义体免疫，见 cyberpunk/immunity.py）
IMMUNITY_CATEGORY = {
    "毒气弹": "毒气",
    "闪光弹": "闪光",
    "催泪弹": "毒气",       # 催泪也是「气体」，书里没给单独条目，按毒气档
    "EMP 弹": "EMP",
}


@lru_cache(maxsize=1)
def _data() -> dict:
    if not DATA_FILE.exists():
        return {"items": {}, "notes": {}}
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def items() -> dict[str, dict]:
    return dict(_data().get("items", {}))


def spec_for(ammo_type: str) -> dict:
    """这个弹种的命中后检定规格；不是检定类返回空字典。"""
    return dict(items().get(str(ammo_type), {}))


def is_check_ammo(ammo_type: str) -> bool:
    """是不是「不造成伤害、改过检定」的弹种。"""
    spec = spec_for(ammo_type)
    return bool(spec.get("check"))


def immunity_for(ammo_type: str) -> str:
    return IMMUNITY_CATEGORY.get(str(ammo_type), "")


def describe(ammo_type: str) -> str:
    """一句话说明（给回执用）。"""
    spec = spec_for(ammo_type)
    if not spec:
        return ""
    if not spec.get("check"):
        return str(spec.get("note") or "")
    return f"过 DV{spec['dv']} {spec['check']}"


def on_fail_description(ammo_type: str) -> str:
    """失败效果的人话描述。"""
    spec = spec_for(ammo_type)
    fail = spec.get("on_fail") or {}
    bits: list[str] = []
    if fail.get("damage"):
        bits.append(f"直接 {fail['damage']} 伤害（护甲不削减）")
    if fail.get("status") == "unconscious":
        bits.append("倒地并失去意识 1 分钟")
    if fail.get("injury"):
        from cyberpunk import crit

        names = []
        for key in fail["injury"]:
            injury = crit.BY_KEY.get(key)
            names.append(getattr(injury, "zh", None) or getattr(injury, "label", None) or key)
        bits.append("获得「" + "」「".join(names) + "」（1 分钟后自愈）")
    if fail.get("disable_cyberware"):
        bits.append(f"{int(fail['disable_cyberware'])} 件赛博组件失效 1 分钟")
    return "；".join(bits) if bits else "无额外效果"
