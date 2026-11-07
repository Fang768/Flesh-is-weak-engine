"""严重伤势（Critical Injury）——赛博朋克红（RED）核心规则。

出处：``rules/red_core/raw/full.txt`` 书内页 187–188（PDF p205–206）：
严重伤势一节 + 身体 / 头部严重伤势表。

触发：近战或远程攻击的伤害骰出现 **两个及以上 6** → 造成一次严重伤势
（full.txt:15743-15745）。投 2D6 查对应伤势表，直到得到目标目前没有的伤势
（full.txt:15746-15748）。瞄准头部用头部表，否则用身体表（full.txt:15747-15748）。

严重伤势额外造成 **5 点伤害**，不削减护甲、不受命中部位倍率影响，且无论
有没有击穿护甲都会生效（full.txt:15750-15752）。

「表现层」口径（AGENTS.md 第 4 条：规则书只给机械效果，没给机器人文案/内部
标签，以下为约定）：
- ``key``：内部稳定 id，用于去重（严重伤不可重复获得，full.txt:15746-15748）。
- ``zh``：给玩家看的伤势名；``sk`` 存进角色卡 ``state`` 的标签。
- 判定惩罚（action / ranged / visual / hearing / melee / speak）并入战斗内
  相应检定；``move_penalty`` 并入移动预算；``death_penalty`` 并入死亡豁免惩罚；
  状态类（no_dodge / no_speak / no_action_next / no_move_next / walk_damage）
  在对应动作处检查。
"""

from __future__ import annotations

from dataclasses import dataclass

SERIOUS_EXTRA_DAMAGE = 5  # 严重伤势额外伤害（不削护甲、不受部位倍率，full.txt:15750-15752）
SERIOUS_TRIGGER_SIXES = 2  # 伤害骰出现两个及以上 6 -> 触发严重伤势（full.txt:15743-15745）


@dataclass(frozen=True)
class CriticalInjury:
    key: str              # 内部稳定 id（去重用）
    zh: str               # 玩家看到的伤势名
    sk: str               # 存进角色卡 state 的标签
    # 判定类惩罚（全部取负值或 0；加到对应检定）
    action_penalty: int = 0   # 「所有动作判定 −N」
    ranged_penalty: int = 0   # 远程攻击判定 −N
    visual_penalty: int = 0   # 需要视觉的觉察判定 −N
    hearing_penalty: int = 0  # 需要听力的觉察判定 −N
    melee_penalty: int = 0    # 近战攻击判定 −N
    speak_penalty: int = 0    # 需要说话的动作判定 −N
    # 状态/移速类
    move_penalty: int = 0     # 移速 −N（最低 1）
    no_dodge: bool = False    # 无法闪避
    no_speak: bool = False    # 无法说话（气管破裂）
    no_action_next: bool = False  # 脊柱受伤：下回合只能移动
    walk_damage: bool = False     # 徒步行进 >4m 的回合重受此体外伤额外伤害
    no_move_next: bool = False    # 步行 >4m 后的下回合无法做移动动作（耳朵）
    head_mult: int = 0            # 颅骨骨裂：头部瞄准伤害倍数改 3（默认 2）
    death_penalty: int = 0    # 基础死亡豁免惩罚 +1


# ── 身体严重伤势表（full.txt:15760-15878）────────────
BODY_INJURIES: dict[int, CriticalInjury] = {
    2: CriticalInjury("arm_lost", "断臂", "断臂", death_penalty=1),
    3: CriticalInjury("hand_lost", "断手", "断手", death_penalty=1),
    4: CriticalInjury("pneumothorax", "气胸", "气胸",
                      move_penalty=2, death_penalty=1),
    5: CriticalInjury("rib_fracture", "肋骨骨折", "肋骨骨折", walk_damage=True),
    6: CriticalInjury("arm_fracture", "手臂骨折", "手臂骨折"),
    7: CriticalInjury("foreign_body", "体内异物", "体内异物", walk_damage=True),
    8: CriticalInjury("leg_fracture", "小腿/大腿骨折", "腿骨折", move_penalty=4),
    9: CriticalInjury("muscle_tear", "肌肉撕裂", "肌肉撕裂", melee_penalty=-2),
    10: CriticalInjury("spinal_injury", "脊柱受伤", "脊柱受伤",
                       no_action_next=True, death_penalty=1),
    11: CriticalInjury("finger_crush", "手指粉碎性骨折", "手指粉碎",
                       action_penalty=-4),
    12: CriticalInjury("leg_lost", "断腿", "断腿",
                       move_penalty=6, no_dodge=True, death_penalty=1),
}


# ── 头部严重伤势表（full.txt:15880-15930）────────────
HEAD_INJURIES: dict[int, CriticalInjury] = {
    2: CriticalInjury("eye_lost", "失去眼睛", "失去眼睛",
                      ranged_penalty=-4, visual_penalty=-4, death_penalty=1),
    3: CriticalInjury("brain_damage", "脑损伤", "脑损伤",
                      action_penalty=-2, death_penalty=1),
    4: CriticalInjury("eye_damage", "眼睛受损", "眼睛受损",
                      ranged_penalty=-2, visual_penalty=-2),
    5: CriticalInjury("concussion", "脑震荡", "脑震荡", action_penalty=-2),
    6: CriticalInjury("jaw_fracture", "颌骨骨折", "颌骨骨折", speak_penalty=-4),
    7: CriticalInjury("head_foreign_body", "头部异物", "头部异物", walk_damage=True),
    8: CriticalInjury("whiplash", "挥鞭伤", "挥鞭伤", death_penalty=1),
    9: CriticalInjury("skull_fracture", "颅骨骨裂", "颅骨骨裂",
                      death_penalty=1, head_mult=3),
    10: CriticalInjury("ear_damage", "耳朵受损", "耳朵受损",
                       hearing_penalty=-2, no_move_next=True),
    11: CriticalInjury("trachea_rupture", "气管破裂", "气管破裂",
                       no_speak=True, death_penalty=1),
    12: CriticalInjury("ear_lost", "失去耳朵", "失去耳朵",
                       hearing_penalty=-4, no_move_next=True, death_penalty=1),
}


def table_for(head: bool) -> dict[int, CriticalInjury]:
    """瞄准头部用头部表，否则用身体表（full.txt:15747-15748）。"""
    return HEAD_INJURIES if head else BODY_INJURIES


#: 角色卡 ``crits`` 里存的是伤势 key（字符串），不是 2D6 点数。
#: 所以要另建一张按 key 索引的表，供读取/汇总时反查（约定）。
BY_KEY: dict[str, CriticalInjury] = {
    inj.key: inj
    for inj in list(BODY_INJURIES.values()) + list(HEAD_INJURIES.values())
}


def by_key(key: str) -> CriticalInjury | None:
    """按伤势 key 反查伤势定义；未知 key 返回 None。"""
    return BY_KEY.get(key)


def roll_critical(roller, head: bool) -> CriticalInjury:
    """投 2D6 查伤势表，返回伤势。调用方必须保证已有伤势已去重。"""
    table = table_for(head)
    roll = roller.roll(6) + roller.roll(6)
    while roll not in table:  # 2D6 恒有值，防御性兜底
        roll = roller.roll(6) + roller.roll(6)
    return table[roll]


def aggregate_crits(crits: tuple[str, ...]) -> dict[str, int]:
    """把一个 Combatant 的 crits 汇总成 (字段, 累计值) 便于判定。"""
    acc = {}
    for key in crits:
        inj = BY_KEY.get(key)
        if not inj:
            continue
        for f in ("action_penalty", "ranged_penalty", "melee_penalty",
                  "visual_penalty", "hearing_penalty", "speak_penalty",
                  "death_penalty", "move_penalty", "head_mult"):
            v = getattr(inj, f)
            if v:
                acc[f] = acc.get(f, 0) + v
        if inj.no_dodge:
            acc["no_dodge"] = 1
        if inj.no_speak:
            acc["no_speak"] = 1
        if inj.no_action_next:
            acc["no_action_next"] = 1
        if inj.no_move_next:
            acc["no_move_next"] = 1
        if inj.walk_damage:
            acc["walk_damage"] = acc.get("walk_damage", 0) + 1
    return acc