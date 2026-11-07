"""伤害结算：**先减护甲 SP，再乘命中部位倍率**（RED 核心规则）。

出处：核心书 `rules/red_core/raw/full.txt` 书内页 172–176（PDF p190–194）。

必须遵守的点：

* 普通攻击打在躯干，×1；**瞄准射击**命中部位才有倍率，头部 ×2（减甲之后翻倍）
* 严重伤势：伤害骰出现 **两个及以上的 6** → 追加 **5 点伤害**（不吃护甲、不受部位倍率）
* **AP 弹与锐器只吃一半 SP**（向下取整）
* 每次被护甲挡住伤害，护甲 **SP −1**（磨损，需修）
* RED 只有一个血池 HP —— 眩晕/药物伤害同样扣 HP
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum

from .dice import Roller


class HitLocation(IntEnum):
    HEAD = 1
    VITALS = 2  # 2–3
    TORSO = 4  # 4–6
    LIMB = 7  # 7–10


class DamageType(IntEnum):
    LETHAL = 0  # 掉 HITS
    STUN = 1  # 掉 STUN
    DRUGS = 2  # 毒物，走毒物规则（1D10 ≤ CON 抵抗）


LOCATION_MULTIPLIER: dict[HitLocation, int] = {
    HitLocation.HEAD: 2,  # 瞄准射击头部：减甲之后 ×2
    HitLocation.VITALS: 2,
    HitLocation.TORSO: 1,
    HitLocation.LIMB: 1,
}

CRITICAL_EXTRA_DAMAGE = 5  # 严重伤势追加伤害（书 p190）

DICE_RE = re.compile(r"^\s*(\d+)\s*[dD]\s*(\d+)\s*(?:([+-])\s*(\d+))?\s*$")


def roll_notation(roller: Roller, notation: str) -> int:
    """掷 ``2D6+1`` / ``1D10`` 这类记号，返回总和。"""
    totals, _ = roll_dice_full(roller, notation)
    return totals


def roll_dice_full(roller: Roller, notation: str) -> tuple[int, int]:
    """掷 ``2D6+1`` 这类记号，返回 ``(总和, 掷出 6 的个数)``。

    ``个 6 的个数`` 用于严重伤势判定（书 p190）：近战 / 远程攻击的伤害骰
    出现 **两个及以上 6** 就造成一次严重伤势。
    """
    match = DICE_RE.match(notation)
    if not match:
        raise ValueError(f"无法解析伤害记号 {notation!r}（支持 2D6+1 形式）")
    count, sides, sign, flat = match.groups()
    s = 0
    sixes = 0
    for _ in range(int(count)):
        r = roller.roll(int(sides))
        s += r
        if r == 6:
            sixes += 1
    if flat:
        s += int(flat) if sign == "+" else -int(flat)
    return s, sixes


def hit_location(roller: Roller) -> tuple[HitLocation, int]:
    """掷命中部位，返回 (部位, 伤害倍率)。"""
    roll = roller.roll(10)
    if roll == 1:
        loc = HitLocation.HEAD
    elif roll <= 3:
        loc = HitLocation.VITALS
    elif roll <= 6:
        loc = HitLocation.TORSO
    else:
        loc = HitLocation.LIMB
    return loc, LOCATION_MULTIPLIER[loc]


@dataclass(slots=True)
class Armor:
    """护甲：SP 减伤、EV 累加到 REF 惩罚。"""

    name: str = "无护甲"
    sp: int = 0
    ev: int = 0

    def effective_sp(self, *, ap: bool = False, melee: bool = False) -> int:
        """AP 弹或锐器（近战利器）只吃一半 SP。"""
        if ap or melee:
            return self.sp // 2
        return self.sp

    def absorb(self) -> None:
        """挡住一次伤害后 SP 永久 −1（书 p19/p239）。"""
        if self.sp > 0:
            self.sp -= 1


@dataclass(slots=True)
class DamageResult:
    """一次伤害结算的结果。"""

    raw: int  # 伤害骰原始值
    multiplier: int  # 命中部位倍率
    total: int  # 倍率之后、减甲之前
    absorbed: int  # 被护甲吃掉
    penetrated: int  # 实际进入血池
    sp_after: int  # 结算后护甲剩余 SP
    hit: HitLocation
    damage_type: DamageType

    def describe(self) -> str:
        loc = {
            HitLocation.HEAD: "头部",
            HitLocation.VITALS: "要害",
            HitLocation.TORSO: "躯干",
            HitLocation.LIMB: "四肢",
        }[self.hit]
        text = f"{loc} 伤害 {self.raw}×{self.multiplier}={self.total}"
        if self.absorbed:
            text += f"，护甲吸收 {self.absorbed}（SP→{self.sp_after}）"
        text += f" → 实际 {self.penetrated}"
        if self.damage_type is DamageType.STUN:
            text += "（眩晕）"
        return text


def apply_damage(
    raw: int,
    *,
    hit: HitLocation = HitLocation.TORSO,
    armor: Armor | None = None,
    ap: bool = False,
    melee: bool = False,
    damage_type: DamageType = DamageType.LETHAL,
    critical_extra: bool = False,
) -> DamageResult:
    """把一次伤害骰结果落到目标身上（不修改角色血池，只算数）。

    RED 的顺序是 **先减护甲 SP，再按命中部位翻倍**。``critical_extra``
    对应「伤害骰出现 ≥2 个 6」，会再加 5 点、且不吃护甲、不受倍率影响。
    """
    multiplier = LOCATION_MULTIPLIER[hit]
    absorbed = 0
    if armor is not None and raw > 0:
        eff = armor.effective_sp(ap=ap, melee=melee)
        absorbed = min(raw, eff)
        if absorbed:
            armor.absorb()
    through = max(0, raw - absorbed)
    penetrated = through * multiplier
    if critical_extra:
        penetrated += CRITICAL_EXTRA_DAMAGE
    return DamageResult(
        raw=raw,
        multiplier=multiplier,
        total=raw * multiplier,
        absorbed=absorbed,
        penetrated=penetrated,
        sp_after=armor.sp if armor else 0,
        hit=hit,
        damage_type=damage_type,
    )


def death_save(roller: Roller, sd: int, *, penalty: int = 0) -> tuple[bool, int]:
    """死亡豁免：``1D10 ≤ CON`` 成功；自然 10 必败；每次失败累加 +1 惩罚。

    返回值 ``(是否活着, 掷骰值)``。惩罚由调用方累加（``penalty``）。
    """
    roll = roller.roll(10)
    if roll == 10:
        return False, roll
    return roll <= sd - penalty, roll
