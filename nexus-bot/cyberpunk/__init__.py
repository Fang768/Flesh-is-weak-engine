"""Cyberpunk V3.0 (CP4110) 规则引擎。

只用标准库。所有随机性都通过 :class:`cyberpunk.dice.Roller` 注入，
方便用脚本化骰子做确定性单元测试。

规则出处见 ``rules/cp_v3/docs/``：

* ``01-核心机制.md`` —— 检定公式、DV 阶梯、自然 10/1、修正值
* ``02-角色创建.md`` —— 十属性、派生属性公式
* ``03-战斗规则.md`` —— 先攻、命中部位、伤害链、护甲 SP/EV
"""

from .dice import Roller, RandomRoller, ScriptedRoller
from .stats import STAT_ORDER, DerivedStats, derived
from .checks import (
    DV,
    CheckResult,
    Modifier,
    resolve_check,
    opposed_check,
)
from .damage import (
    HitLocation,
    DamageType,
    Armor,
    DamageResult,
    apply_damage,
    death_save,
)
from .characters import Character
from .commands import (
    SKILL_STAT,
    parse_check_args,
    parse_notation,
    roll_verbose,
)

__all__ = [
    "Roller",
    "RandomRoller",
    "ScriptedRoller",
    "STAT_ORDER",
    "DerivedStats",
    "derived",
    "DV",
    "CheckResult",
    "Modifier",
    "resolve_check",
    "opposed_check",
    "HitLocation",
    "DamageType",
    "Armor",
    "DamageResult",
    "apply_damage",
    "death_save",
    "Character",
    "SKILL_STAT",
    "parse_check_args",
    "parse_notation",
    "roll_verbose",
]
