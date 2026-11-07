"""角色卡：属性 + 技能 + 护甲 + 专长天赋，以及它们之间的加成关系。

规则基线是 **赛博朋克红（RED）**：

* 只有一个血池 HP（书 p79）
* 伤势三档：轻伤 / 重伤（HP < 重伤阈值，全检定 −2）/ 致命伤（HP < 1，全检定 −4、MOVE −6）
* 自然痊愈：稳定伤势成功后，休息一整天恢复 **体格** 点 HP（书 p222）
* 死亡豁免：``D10 < 体格`` 成功，掷 10 必败，每次失败/尝试后惩罚 +1

只放"规则层"需要的东西 —— 存档、campaign、LLM 都不在这里。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .checks import CheckResult, DV, resolve_check
from .damage import Armor, DamageResult, DamageType
from .dice import Roller
from .stats import STAT_ORDER, DerivedStats, derived, validate_stats

# 少数会直接改技能/属性的天赋。技能名与术语表一致。
PERK_SKILL_BONUS: dict[str, dict[str, int]] = {
    # 每级 +1 说服 / 表演 / 服饰&风格
    "beautiful": {"说服": 1, "表演": 1, "服饰&风格": 1},
}


class WoundStateError(RuntimeError):
    """死亡 / 状态不允许该操作。"""


@dataclass
class Character:
    """一个可投骰的角色。

    ``stats`` 必须是十项全齐的字典；``skills`` 是「技能名 → 等级」。
    """

    name: str
    stats: dict[str, int]
    skills: dict[str, int] = field(default_factory=dict)
    armor: Armor = field(default_factory=Armor)
    perks: dict[str, int] = field(default_factory=dict)  # 名字 → 等级（专长/天赋）
    hp: int | None = None
    hum: int | None = None
    death_penalty: int = 0  # 死亡豁免累积惩罚

    def __post_init__(self) -> None:
        validate_stats(self.stats)
        d = self.derived
        if self.hp is None:
            self.hp = d.hp
        if self.hum is None:
            self.hum = d.hum

    # ---------- 派生 ----------

    @property
    def derived(self) -> DerivedStats:
        return derived(self.stats)

    @property
    def max_hp(self) -> int:
        return self.derived.hp

    def effective_stat(self, key: str) -> int:
        """属性 + 专长修正（街头门路加到 COOL 上）。"""
        base = self.stats[key]
        if key == "COOL":
            base += self.perks.get("streetdeal", 0)
        return base

    def effective_skill(self, skill: str) -> int:
        """技能 + 专长/天赋修正。"""
        base = self.skills.get(skill, 0)
        for perk_name, bonuses in PERK_SKILL_BONUS.items():
            level = self.perks.get(perk_name, 0)
            if level and skill in bonuses:
                base += level * bonuses[skill]
        from cyberpunk.perks import TALENT_SKILL_BONUS

        for talent_name, bonuses in TALENT_SKILL_BONUS.items():
            level = self.perks.get(talent_name, 0)
            if level and skill in bonuses:
                base += level * bonuses[skill]
        return base

    # ---------- 伤势状态（书 p220）----------

    @property
    def hp_now(self) -> int:
        return int(self.hp or 0)

    @property
    def seriously_wounded(self) -> bool:
        """重伤：HP 少于重伤阈值 → 所有检定 −2。"""
        return 0 < self.hp_now < self.derived.wound

    @property
    def critically_wounded(self) -> bool:
        """致命伤：HP 少于 1 → 所有检定 −4、MOVE −6、每回合死亡豁免。"""
        return self.hp_now < 1

    @property
    def wound_penalty(self) -> int:
        """当前伤势带来的检定减值。"""
        if self.critically_wounded:
            return -4
        if self.seriously_wounded:
            return -2
        return 0

    @property
    def wound_state(self) -> str:
        if self.critically_wounded:
            return "致命伤"
        if self.seriously_wounded:
            return "重伤"
        return "轻伤"

    @property
    def move_now(self) -> int:
        """致命伤时移动 −6（最低 1）。"""
        base = self.stats["MOVE"] * 2
        if self.critically_wounded:
            return max(1, base - 6)
        return base

    # ---------- 检定 ----------

    def check(
        self,
        roller: Roller,
        *,
        stat: str,
        skill: str | None = None,
        dv: int = DV.EVERYDAY,
        modifiers: Iterable[int] = (),
        purpose: str | None = None,
        apply_wound: bool = True,
    ) -> CheckResult:
        """用「属性 + 技能」掷一次检定；默认把伤势惩罚算进去。"""
        stat_value = self.effective_stat(stat)
        skill_value = self.effective_skill(skill) if skill else 0
        mods = list(modifiers)
        if apply_wound and self.wound_penalty:
            mods.append(self.wound_penalty)
        return resolve_check(
            roller,
            stat=stat_value,
            skill=skill_value,
            dv=dv,
            modifiers=mods,
            purpose=purpose or (f"{self.name} 的{skill or stat}检定"),
        )

    # ---------- 伤害与死亡 ----------

    def take_damage(self, result: DamageResult) -> None:
        """把结算好的伤害落进 HP。RED 没有独立眩晕池。"""
        self.hp = max(0, self.hp_now - result.penetrated)

    @property
    def is_dead(self) -> bool:
        return self.hp_now < 1

    @property
    def is_critical(self) -> bool:
        """濒死/致命伤：需要每回合死亡豁免。"""
        return self.is_dead

    def death_save(self, roller: Roller, *, easy: int = 0) -> tuple[bool, int]:
        """死亡豁免：``D10 < 体格`` 成功；掷 10 必败；每次尝试后惩罚 +1。

        ``easy`` 是临时加成（例如「擅长硬抗」类专长），会加在有效体格上。
        """
        roll = roller.roll(10)
        effective = self.stats["BODY"] + easy - self.death_penalty
        self.death_penalty += 1
        if roll == 10:
            return False, roll
        return roll < effective, roll

    def reset_death_penalty(self) -> None:
        """稳定伤势恢复到 1 HP 时重置累积惩罚（书 p222）。"""
        self.death_penalty = 0

    def heal(self, amount: int) -> None:
        self.hp = min(self.max_hp, self.hp_now + max(0, amount))

    def rest(self) -> None:
        """稳定伤势后休息一整天：恢复等于体格的 HP（书 p222）。"""
        self.heal(self.stats["BODY"])

    @property
    def humanity(self) -> int:
        """当前人性（未扣赛博改造损失时为 EMP×10）。"""
        return int(self.hum or 0)

    def lose_humanity(self, amount: int) -> None:
        """扣人性；每掉满 10 点，共情 −1（最低 1）。"""
        before = self.humanity
        self.hum = max(0, before - max(0, amount))
        self.stats["EMP"] = max(1, self.hum // 10)

    def summary(self) -> str:
        d = self.derived
        return (
            f"{self.name}: HP {self.hp_now}/{d.hp}（{self.wound_state}）"
            f" 人性 {self.humanity}/{d.hum} 幸运 {d.luck}"
            f" SP {self.armor.sp} EV {self.armor.ev}"
        )
