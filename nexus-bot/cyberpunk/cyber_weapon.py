"""义体武器：从赛博组件里拿到的近战/远程武器。

数据源 ``rules/red_core/data/cyberware_weapons.json``，档位表来自
``full.txt:14976-15027``：

| 档位 | 伤害 | ROF | 能否藏匿 | 原文 |
|---|---|---|---|---|
| 轻型 | 1D6 | 2 | 能 | `:14987` |
| 中型 | 2D6 | 2 | 不能 | `:14997` |
| 重型 | 3D6 | 2 | 不能 | `:15007` |
| 超重型 | 4D6 | 1 | 不能 | `:15017` |

**覆盖语义**（主人 2026-11-10 拍板「义体武器覆盖近战伤害」）：

引擎里近战伤害是按**技能**写死的（``combat.WEAPON_DAMAGE``：近战武器 2D6、
武术 2D6、搏击 1D6）。装了义体武器之后，**用义体武器那一击按义体武器的档位算**，
不叠加、不取平均。

「约定」：

* 只有一个义体武器时，直接覆盖该技能的基础伤害。
* 装了多个义体武器且没指定用哪个 → 取**伤害最高**的（玩家不会故意用弱的）。
* 藏匿：书里明写「能够被藏匿」的才计入 ``conceal``；弹出式武器一律能藏
  （``:10265``、``:10282``「即使是通常状态下无法藏匿的」）。
* 隐藏枪套/皮下口袋不是武器，是**藏匿通道**，给 ``conceal_bonus``。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA_FILE = (Path(__file__).resolve().parents[2]
             / "rules" / "red_core" / "data" / "cyberware_weapons.json")

#: 档位 → (伤害骰, ROF)。与 full.txt:14976-15027 一致。
TIER_DAMAGE = {"轻型": "1D6", "中型": "2D6", "重型": "3D6", "超重型": "4D6"}
TIER_ROF = {"轻型": 2, "中型": 2, "重型": 2, "超重型": 1}


@lru_cache(maxsize=1)
def _data() -> dict:
    if not DATA_FILE.exists():
        return {"items": {}, "melee_tiers": {}}
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def items() -> dict[str, dict]:
    return dict(_data().get("items", {}))


def item_rule(name: str) -> dict:
    return dict(items().get(str(name), {}))


def tiers() -> dict[str, dict]:
    return dict(_data().get("melee_tiers", {}))


def _installed(card: dict) -> list[str]:
    """已装义体名。顶层 ``cyber``（简易卡）优先，避免两处重复计数。"""
    from . import cyber_bonus

    return cyber_bonus.installed_names(card)


def damage_of(name: str) -> str | None:
    """这件义体武器的伤害骰；不是武器返回 None。"""
    rule = item_rule(name)
    if rule.get("damage"):
        return str(rule["damage"])
    tier = rule.get("tier")
    if tier in TIER_DAMAGE:
        return TIER_DAMAGE[tier]
    if tier == "依赖":
        # 弹出式近战武器：没指定装了什么，按中型
        return TIER_DAMAGE["中型"]
    return None


def rof_of(name: str) -> int:
    rule = item_rule(name)
    tier = rule.get("tier")
    if tier in TIER_ROF:
        return TIER_ROF[tier]
    return 1


def _rolls(text: str) -> int:
    """``"3D6"`` → 3（用来比较大小）。"""
    digits = "".join(ch for ch in str(text).split("D")[0] if ch.isdigit())
    return int(digits or 0)


def weapons_on(card: dict) -> list[str]:
    """卡上装的义体武器名（按安装顺序）。"""
    table = items()
    return [name for name in _installed(card)
            if name in table and damage_of(name)]


def best_weapon(card: dict, *, skill: str = "") -> tuple[str, str] | None:
    """挑出这一击该用的义体武器 → ``(名字, 伤害骰)``；没有返回 None。

    ``skill`` 给定时只挑该技能的武器（近战武器 / 手枪 / 重武器）。
    """
    picks: list[tuple[str, str]] = []
    for name in weapons_on(card):
        rule = item_rule(name)
        want = str(rule.get("skill") or "")
        if skill and want and want != skill:
            continue
        dmg = damage_of(name)
        if dmg:
            picks.append((name, dmg))
    if not picks:
        return None
    picks.sort(key=lambda pair: _rolls(pair[1]), reverse=True)
    return picks[0]


def damage_override(card: dict, *, skill: str = "", weapon: str = "") -> tuple[str | None, str]:
    """战斗里覆盖伤害骰用。

    返回 ``(伤害骰 or None, 说明)``。玩家点名了 ``weapon`` 就用它，否则取最强的一件。
    """
    if weapon:
        rule = item_rule(weapon)
        if rule and damage_of(weapon):
            return damage_of(weapon), f"义体武器：{weapon}"
        return None, ""
    pick = best_weapon(card, skill=skill)
    if pick is None:
        return None, ""
    name, dmg = pick
    return dmg, f"义体武器：{name}（{dmg}）"


def poison_of(card: dict, *, weapon: str = "") -> str:
    """这一击带的毒素档位（轻/强/致命）；没装可附毒的义体返回空串。

    ``full.txt:10146-10151``：吸血鬼尖牙「使用者可以对其附加毒素以及生物毒素」。
    书里没写具体档位与 DV，走毒素表 ``:15358-15367``：
    轻 DV11 / 强 DV13 / 致命 DV15，都是直接 HP 伤害（护甲不削减）。
    「约定」：默认用「强」档（DV13，2d6）；玩家想换档可以用 ``weapon`` 指定，
    或后续做「附毒 <档位>」命令。
    """
    picks = [name for name in _installed(card)
             if item_rule(name).get("poison")]
    if weapon:
        picks = [name for name in picks if name == weapon] or (
            [weapon] if item_rule(weapon).get("poison") else [])
    if not picks:
        return ""
    return "强"


def conceal_bonus(card: dict) -> int:
    """藏匿通道给的加值（隐藏枪套 +2 / 皮下口袋 +1）。"""
    total = 0
    for name in _installed(card):
        total += int(item_rule(name).get("conceal_bonus") or 0)
    return total


def concealed_weapons(card: dict) -> list[str]:
    """能被藏进身体里的武器。"""
    return [name for name in _installed(card)
            if item_rule(name).get("conceal") and damage_of(name)]


def describe(card: dict) -> list[str]:
    """给 ``/义体`` 面板：列出义体武器与藏匿。"""
    out: list[str] = []
    for name in weapons_on(card):
        dmg = damage_of(name)
        bits = [f"{dmg}"]
        if item_rule(name).get("conceal"):
            bits.append("可藏匿")
        out.append(f"{name}（{'，'.join(bits)}）")
    bonus = conceal_bonus(card)
    if bonus:
        out.append(f"藏匿通道 +{bonus}")
    return out
