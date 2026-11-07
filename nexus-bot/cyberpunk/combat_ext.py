"""方案 A：把 V3 的几条战斗细则接进简易版（Easy Mode）。

只加**规则细节**，不动核心：仍然是 `1d10 + 属性 + 技能 vs DV`、DV 13/15/17/21/24、
平局守方胜、单一 HP 池。这里提供五组纯函数：

1. :func:`derived` —— 建卡派生属性公式（简易版原本没有公式，只有卡面数字）
2. :func:`hit_location` —— 命中部位（1D10）
3. :func:`autofire_hits` / :func:`suppression_save` —— 全自动与压制射击
4. :func:`effective_sp` —— 弹药类型（AP / 锐器）
5. :func:`is_malfunction` —— 卡壳（来自可靠性 VR/ST/UR）
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Location(str, Enum):
    HEAD = "头部"
    VITALS = "要害"
    TORSO = "躯干"
    LIMB = "四肢"


LOCATION_MULTIPLIER = {
    Location.HEAD: 3,
    Location.VITALS: 2,
    Location.TORSO: 1,
    Location.LIMB: 1,
}


@dataclass(frozen=True, slots=True)
class Derived:
    """简易版的派生属性。公式来自 V3（书 p147），HP 与无量纲缩放后再用。"""

    hp: int           # V3 HITS=BODY×2，简易版人物卡是 40–60，所以 ×5 对齐
    wound: int        # 重伤阈值 = HP / 2（向下取整）
    death_save: int   # 死亡豁免目标 = BODY
    luck: int         # (INT + REF) // 2
    run: int          # MOVE × 2（米）
    leap: int         # MOVE（米）
    carry: int        # BODY × 10（公斤）


def derived(stats: dict[str, int], hp: int | None = None) -> Derived:
    """由十属性算派生值。``stats`` 用大写键（INT/REF/BODY/MOVE…）。

    简易版（RED Easy Mode）的生命值写在卡面上、**没有公式**（肆拾 BODY3→40、
    火炬 BODY6→35、搬运工 BODY7→45），所以传 ``hp=卡面值``；不传才退回
    V3 的 ``BODY×5`` 估算。死亡豁免始终 = BODY，重伤阈值始终 = hp//2。
    """
    body = int(stats.get("BODY", 5))
    hp = max(1, int(hp)) if hp else max(1, body * 5)
    return Derived(
        hp=hp,
        wound=hp // 2,
        death_save=body,
        luck=(int(stats.get("INT", 5)) + int(stats.get("REF", 5))) // 2,
        run=int(stats.get("MOVE", 5)) * 2,
        leap=int(stats.get("MOVE", 5)),
        carry=body * 10,
    )


def hit_location(d10: int) -> Location:
    """1D10 定位：1 头、2–3 要害、4–6 躯干、7–10 四肢。"""
    roll = max(1, min(10, int(d10)))
    if roll == 1:
        return Location.HEAD
    if roll <= 3:
        return Location.VITALS
    if roll <= 6:
        return Location.TORSO
    return Location.LIMB


def damage_after_location(raw: int, where: Location) -> int:
    """命中部位倍率。四肢不打折，但见 :func:`limb_penalty`。"""
    return int(raw) * LOCATION_MULTIPLIER[where]


def limb_penalty(where: Location) -> int:
    """中弹的四肢：所有用那条肢体的动作 −2（简易版原本没有这条）。"""
    return -2 if where is Location.LIMB else 0


def autofire_hits(margin: int, rounds: int, distance_band: int = 0) -> int:
    """全自动命中数 = 攻击骰超出 DV 的点数，且不超过打出的发数。

    ``distance_band``：每远一档，每 10 发 −1（V3 原规则），这里按
    ``rounds // 10`` 的倍数扣减命中上限。
    """
    if margin <= 0:
        return 0
    cap = int(rounds) - max(0, int(distance_band)) * (int(rounds) // 10)
    return max(0, min(int(margin), cap))


def suppression_save(rounds: int, width_m: float) -> int:
    """压制射击：目标必须掷 `1D10 ≤ 这个数` 才不被压住（V3：发数 ÷ 击杀区宽度）。

    例：64 发打 2 米宽的走廊 → 32（几乎压死）；64 发打 5 米 → 12。
    """
    if width_m <= 0:
        return int(rounds)
    return max(1, int(rounds / width_m))


def effective_sp(sp: int, *, ap: bool = False, melee: bool = False) -> int:
    """穿甲弹（AP）与锐器（近战/箭矢）只看一半护甲，向下取整，不叠加两次。"""
    value = max(0, int(sp))
    if ap or melee:
        return value // 2
    return value


def is_malfunction(natural: int, reliability: str) -> bool:
    """卡壳：VR 永不、ST 自然 1、UR 自然 1–2（与 doc 10 的武器表一致）。"""
    table = {"VR": (), "ST": (1,), "UR": (1, 2)}
    return int(natural) in table.get((reliability or "").strip().upper(), (1,))
