"""武器目录：按**属性**回答「这把武器能不能装进赛博手臂」。

设计要点（主人 2026-11-13 定）：**不做弹出式专用白名单**。
判定只看武器自己的属性，所以以后加武器只要往
``rules/red_core/data/weapons_catalog.json`` 加一行，判定自动生效。

规则依据（``rules/red_core/raw/full.txt``）：

* 弹出式近战武器（``:10261-10269``）：「**任何轻型，中型，或重型武器**都能被安装
  于一条赛博手臂中。此武器能够被藏匿，**即使是通常状态下无法藏匿的**。」
  → 近战：``melee_class ∈ {轻型, 中型, 重型}``，**超重型不在列**。
* 弹出式远程武器（``:10278-10286``）：「安装于一条赛博手臂中的**任何单手持握的
  远程武器**。」
  → 远程：``hands == 1``。
* FAQ（``:521``）：「弹出式近战武器殖装允许你高效地按照轻型，中型，重型，
  **甚至是异种近战武器**的规格将它们装在自己的手臂上。」
  → ``category == "异种武器"`` 不额外排除，仍然只看上面两条属性。

**关键语义**：弹出式武器装的**是市面上的具体武器**，不是「弹出式专用武器」，
数值用那把武器自己的（伤害/ROF/弹匣/手持数），不套档位表。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

CATALOG_FILE = (Path(__file__).resolve().parents[2]
                / "rules" / "red_core" / "data" / "weapons_catalog.json")

#: 近战可以进手臂的类别（``:10261-10269`` 原文只列了这三档）
MOUNTABLE_MELEE_CLASSES = ("轻型", "中型", "重型")
#: 远程进手臂必须是单手持握（``:10280``「任何单手持握的远程武器」）
MOUNTABLE_RANGED_HANDS = 1


@lru_cache(maxsize=1)
def _data() -> dict:
    try:
        return json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"items": []}


def items() -> list[dict]:
    return list(_data().get("items") or [])


def _key(name: str) -> str:
    """归一化：去空格标点，兼容「弓 & 十字弩」「金田一03式」这类写法差异。"""
    return re.sub(r"[\s·・,，、/（）()\-—–]", "", (name or "")).lower()


def find(name: str) -> dict | None:
    """按名字找一把武器（精确 → 去标点 → 唯一包含匹配）。"""
    want = _key(name)
    if not want:
        return None
    pool = items()
    for it in pool:
        if _key(it["name"]) == want:
            return it
    hits = [it for it in pool if want in _key(it["name"])]
    return hits[0] if len(hits) == 1 else None


def search(keyword: str, limit: int = 6) -> list[dict]:
    want = _key(keyword)
    if not want:
        return []
    exact = [it for it in items() if _key(it["name"]) == want]
    loose = [it for it in items()
             if want in _key(it["name"]) and it not in exact]
    return (exact + loose)[:limit]


def is_melee(weapon: dict) -> bool:
    """近战武器（含异种里的近战）。判据是有没有 ``melee_class``。"""
    return bool(weapon.get("melee_class"))


def mount_problem(weapon: dict, *, mount: str) -> str:
    """把这把武器装进 ``mount``（弹出式近战/远程武器）有什么问题？

    纯属性判定，**不查白名单**。返回空串表示可装。
    """
    if weapon is None:
        return "找不到这把武器"
    if mount == "弹出式近战武器":
        if not is_melee(weapon):
            return (f"「{weapon['name']}」不是近战武器。弹出式近战武器只能装"
                    f"轻型/中型/重型近战武器（full.txt:10261-10269）。")
        cls = str(weapon.get("melee_class") or "")
        if cls not in MOUNTABLE_MELEE_CLASSES:
            return (f"「{weapon['name']}」是{cls}近战武器，装不进手臂——"
                    f"书里只允许轻型/中型/重型（full.txt:10261-10269）。")
        return ""
    if mount == "弹出式远程武器":
        if is_melee(weapon):
            return (f"「{weapon['name']}」是近战武器，不是远程武器。")
        hands = int(weapon.get("hands") or 1)
        if hands != MOUNTABLE_RANGED_HANDS:
            return (f"「{weapon['name']}」要 {hands} 只手，装不了——书里是"
                    f"「任何单手持握的远程武器」（full.txt:10280）。")
        return ""
    return f"不认识的安装位：{mount}"


def mountable(weapon: dict, *, mount: str) -> bool:
    return mount_problem(weapon, mount=mount) == ""


def list_for(mount: str) -> list[dict]:
    """这个安装位能装的所有武器（按目录顺序）。"""
    return [it for it in items() if mountable(it, mount=mount)]


def summary(mount: str) -> str:
    """给安装失败时看的候选清单。"""
    pool = list_for(mount)
    if not pool:
        return "目录里没有能装进这个位置的武器。"
    return "、".join(it["name"] for it in pool[:10])


def carried(weapon: dict) -> str:
    """弹出式武器的藏匿语义：一律可藏（``:10265``、``:10282``）。

    「即使是通常状态下无法藏匿的」——这正是弹出式之所以值钱的地方。
    """
    if weapon is None:
        return ""
    return "可藏匿（弹出式：即使通常无法藏匿的也能藏）"
