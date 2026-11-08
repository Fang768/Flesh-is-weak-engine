"""义体加成效：从角色卡已安装的赛博组件（RED 义体），算出检定型加成。

数据表 ``rules/red_core/data/cyberware_bonus.json``：key = ``cyberware_hl.json``
（RED 义体表）的中文名，即 ``core/cyber_flow`` 安装后写进 ``state.cyber`` 的名字。
每条 ``{bonus:N, skills:[技能名]|scope:"远程", note}``。

角色卡 ``state.cyber`` 存的就是这个中文名（``core/cyber_flow._remember`` 用
``entry["zh"]``，entry 来自 ``cyber.hl_of(name)``）。所以这里**直接用
``state.cyber`` 的名字查表，不需要再反查 V3 义体**。

「约定」：
* ``check_bonus`` 只做**检定型**加成（加到检定结果上），不改卡面属性。
* ``hit_bonus`` 做**战斗命中修正**（瞄准/远视目镜）。
* ``attribute_bonus`` 做**永久属性加成**（如骨肉织绕 体格+2），运行时生效、
  不写死基础 stats；具体合成进 ``character.py`` 的 ``effective_stat``。
* ``armor_sp`` 做**防御端护甲 SP**（编织皮肤 SP7 / 皮下护甲 SP11），不叠加、取高。
* ``rest_multiplier`` 做**休整恢复倍率**（抗体增强 每日恢复体格×2）。
* ``scope:"远程"`` = 所有远程武器技能。``miss_modes`` 是额外限定（如"瞄准"）。
* 查不到对应加成 → 静默忽略（不阻断检定）。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "rules" / "red_core" / "data"
BONUS_FILE = DATA_DIR / "cyberware_bonus.json"

# 远程武器技能（与 core/combat 的 _WEAPON_TABLE 对齐；scope:"远程" 会展开成这些）
RANGED_SKILLS = ("手枪", "冲锋枪", "霰弹枪", "突击步枪", "狙击步枪", "重武器",
                 "自动武器")


@lru_cache(maxsize=1)
def bonus_table() -> dict[str, dict]:
    if not BONUS_FILE.exists():
        return {}
    data = json.loads(BONUS_FILE.read_text(encoding="utf-8"))
    return dict(data.get("items", {}))


def installed_with_rule(card: dict) -> list[tuple[str, dict]]:
    """卡片上装了的义体名，配上规则表里的加成条目（可能无）。

    返回 ``[(名字, rule)]``；``rule`` 在表里没有就是 ``None``。
    """
    state = card.get("state") or {}
    zhs = [str(x) for x in (state.get("cyber") or [])]
    table = bonus_table()
    return [(zh, table.get(zh)) for zh in zhs]


def check_bonus(card: dict, *, skill: str = "", stat: str = "") -> tuple[int, list[str]]:
    """这次检定（按技能名）能拿到的义体加成合计。

    规则命中逻辑：
    * 规则给了 ``skills`` → 只有检定技能名精确匹配才生效。
    * 规则给了 ``scope:"远程"``（且带 ``miss_modes`` 的同义体若无适用场景不硬加）→
      检定技能属于远程武器技能就生效。
    * 其它（仅 stat 说明）A 批不生效——检定型只认 skills/scope。

    返回 ``(加成合计, 来源说明列表)``，没命中就是 ``(0, [])``。
    """
    sk = (skill or "").strip()
    total = 0
    notes: list[str] = []
    for zh, rule in installed_with_rule(card):
        if not rule:
            continue
        bonus = int(rule.get("bonus") or 0)
        if not bonus:
            continue
        # 只认 skills 或 scope；命中类（miss_modes）A 批不做，避免误加。
        if rule.get("miss_modes") and not rule.get("skills"):
            continue
        if rule.get("skills"):
            if sk in [str(s) for s in rule["skills"]]:
                total += bonus
                notes.append(f"{zh}{bonus:+d}")
        elif rule.get("scope") == "远程":
            if sk in RANGED_SKILLS:
                total += bonus
                notes.append(f"{zh}{bonus:+d}")
    return total, notes


def describe(card: dict) -> list[str]:
    """列出这张卡上**生效**的义体加成（给 /义体 展示）。"""
    lines: list[str] = []
    for zh, rule in installed_with_rule(card):
        if not rule:
            continue
        if int(rule.get("bonus") or 0):
            where = (
                "、".join(str(s) for s in rule.get("skills") or [])
                or (rule.get("scope") == "远程" and "远程攻击" or "")
            )
            if not where and rule.get("stat"):
                where = rule.get("stat", "") + "属性"
            if not where:
                where = rule.get("miss_modes") and "命中修正" or ""
            lines.append(f"{zh} {int(rule['bonus']):+d}"
                         + (f"（{where}）" if where else ""))
        elif rule.get("sp"):
            lines.append(f"{zh} SP{int(rule['sp'])}（护甲）")
        elif rule.get("rest_mult"):
            lines.append(f"{zh}（休整恢复×{int(rule['rest_mult'])}）")
    return lines


def attribute_bonus(card: dict) -> dict[str, int]:
    """永久**属性型**义体加成：按已装义体返回 ``{属性键: 加值}``。

    例：骨肉织绕 体格+2 → ``{"BODY": 2}``。运行时合成进
    ``cyberpunk.characters.Character.effective_stat``，驱动 HP/重伤阈值/死亡豁免，
    但**不写死** ``state`` 里的基础 ``stats``（避免 ``validate_stats`` 越界）。
    """
    out: dict[str, int] = {}
    for zh, rule in installed_with_rule(card):
        if not rule:
            continue
        k = rule.get("stat")
        if k and int(rule.get("bonus") or 0):
            out[k] = out.get(k, 0) + int(rule["bonus"])
    return out


def armor_sp(card: dict) -> int:
    """防御端**护甲 SP** 义体：返回装了的义体带来的护甲值（取最高）。

    RED：编织皮肤 躯干+头部 SP7（`:10298-10302`）、皮下护甲 躯干+头部 SP11
    （`:10305-10308`），均**不与穿着护甲叠加**、可被削减。本引擎只跟踪一档全局
    SP（不分部位），故按「与穿着护甲**取高**」处理，见 combat.start。标「约定」。
    """
    best = 0
    for zh, rule in installed_with_rule(card):
        if not rule:
            continue
        sp = int(rule.get("sp") or 0)
        if sp > best:
            best = sp
    return best


def rest_multiplier(card: dict) -> int:
    """休整恢复倍率：装「抗体增强」则每休息日恢复体格×2（`full.txt:10220-10223`）。

    默认（无恢复型义体）每日恢复 体格 点 HP；装抗体增强翻倍为 体格×2。
    """
    mult = 1
    for zh, rule in installed_with_rule(card):
        if not rule:
            continue
        m = int(rule.get("rest_mult") or 0)
        if m > mult:
            mult = m
    return mult


#: 命中修正型义体（B 批）：装了就按条件在战斗命中里加值。
#: RED 规则书（rules/red_core/raw/full.txt）：
#: * 瞄准目镜 `:28330-28331`：瞄准射击时命中 +1，需赛博义眼。
#: * 远视目镜 `:28337-28340`：单发或瞄准射击、目标 ≥51 米时命中 +1，不叠加狙击瞄准镜。
HIT_BONUS = {
    "瞄准目镜": {"aim_only": True, "min_m": None, "note": "瞄准+1（:28330）"},
    "远视目镜": {"aim_only": False, "min_m": 51, "note": "远距≥51米+1（:28339）"},
}


def hit_bonus(card: dict, *, aiming: bool = False,
              meters: int | None = None) -> tuple[int, list[str]]:
    """战斗命中修正：按已装义体 + 本次攻击条件（是否瞄准 / 目标距离）。

    命中检定里加进去（combat.py 攻击）。返回 ``(加成合计, 来源说明)``。
    """
    state = card.get("state") or {}
    zhs = set(str(x) for x in (state.get("cyber") or []))
    total = 0
    notes: list[str] = []
    for zh, spec in HIT_BONUS.items():
        if zh not in zhs:
            continue
        if spec["aim_only"]:
            if not aiming:
                continue
        if spec["min_m"] is not None:
            if meters is None or meters < spec["min_m"]:
                continue
        total += 1
        notes.append(f"{zh}+1")
    return total, notes