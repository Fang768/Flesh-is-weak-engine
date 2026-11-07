"""技能检定：``1D10 + 属性 + 技能 + 修正 ≥ DV``。

出处：``rules/cp_v3/docs/01-核心机制.md``（书 p87–90）。

判定细节（容易踩错，务必按这个来）：

* **平局算成功**（Fuzion，与 RED Easy Mode 的"平局守方胜"相反）
* 自然 10 → 再加掷 1D10，**不连锁**
* 自然 1 → 再掷 1D10 减去，若结果 < 0 记为 fumble
* 对抗检定（opposed）平局 → **防守方胜**
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Iterable

from .dice import Roller

NATURAL_HIGH = 10
NATURAL_LOW = 1


class DV(IntEnum):
    """任务难度（书 p89）。"""

    EASY = 10
    EVERYDAY = 14
    CHALLENGING = 18
    HARD = 22
    VERY_HARD = 26
    EXTREME = 30


class Modifier(IntEnum):
    """常用修正值（书 p90 修正表）。

    负数表示减值。名字即语义，方便日志里直接打印。
    """

    COVER = -1  # 目标有遮挡
    SMOKE = -4  # 烟雾/视线受阻
    DODGE = -3  # 目标在闪避
    UNFAMILIAR_TOOL = -4  # 用不熟的专用工具（非通用工具则 -2）
    NO_INSTRUCTIONS = -2  # 有工具但没说明
    NO_TOOLS = -2  # 缺工具
    HARSH_ENVIRONMENT = -5  # 恶劣环境
    COMPLEX = -3  # 复杂操作（-3 或 -4，取 -3 保守值）
    NEVER_DONE = -1  # 从没做过
    STRESS = -3  # 高压/被威胁
    DRUNK = -4  # 醉酒/药物影响
    SNEAKY = -4  # 偷偷摸摸地做


@dataclass(slots=True)
class CheckResult:
    """一次检定的完整回执，便于存档与向玩家解释。"""

    purpose: str
    natural: int
    rolls: list[int]
    stat: int
    skill: int
    modifiers: list[int]
    total: int
    dv: int
    success: bool
    fumble: bool = False
    open_ended: bool = False

    @property
    def margin(self) -> int:
        """超出 DV 多少点（成功为正）。"""
        return self.total - self.dv

    def describe(self) -> str:
        parts = [f"1D10={self.natural}"]
        if self.open_ended:
            parts.append(f"追加{self.rolls[1:]}")
        parts.append(f"属性{self.stat:+d}")
        if self.skill:
            parts.append(f"技能{self.skill:+d}")
        if self.modifiers:
            parts.append("修正" + "".join(f"{m:+d}" for m in self.modifiers))
        verdict = "成功" if self.success else "失败"
        if self.fumble:
            verdict += "（大失败）"
        return (
            f"{self.purpose}: " + " ".join(parts) + f" = {self.total} vs DV{self.dv} → {verdict}"
            f"（差 {self.margin:+d}）"
        )


def resolve_check(
    roller: Roller,
    *,
    stat: int,
    skill: int = 0,
    dv: int = DV.EVERYDAY,
    modifiers: Iterable[int] = (),
    purpose: str = "检定",
) -> CheckResult:
    """掷一次技能检定。

    :param stat: 关联属性值（无技能时直接用它，书 p88「没有技能就用属性」）
    :param skill: 技能等级 0–10；无技能填 0
    """
    mods = [int(m) for m in modifiers]
    rolls: list[int] = []

    natural = roller.roll(10)
    rolls.append(natural)
    total = natural + stat + skill + sum(mods)

    open_ended = False
    fumble = False
    if natural == NATURAL_HIGH:
        extra = roller.roll(10)
        rolls.append(extra)
        total += extra
        open_ended = True
    elif natural == NATURAL_LOW:
        extra = roller.roll(10)
        rolls.append(extra)
        total -= extra
        fumble = total < 0

    return CheckResult(
        purpose=purpose,
        natural=natural,
        rolls=rolls,
        stat=stat,
        skill=skill,
        modifiers=mods,
        total=total,
        dv=int(dv),
        success=total >= int(dv),
        fumble=fumble,
        open_ended=open_ended,
    )


def opposed_check(attacker_total: int, defender_total: int) -> bool:
    """对抗检定：攻击方是否胜出。**平局防守方胜**。"""
    return attacker_total > defender_total
