"""严重伤势（A2）：触发、查表、去重、惩罚接入。

规则出处：``rules/red_core/raw/full.txt``
- :15743-15745 伤害骰出现两个及以上 6 → 一次严重伤势
- :15746-15748 投 2D6 查表，直到得到目标目前没有的伤势；瞄准头部用头部表
- :15750-15752 额外 5 点伤害，不削护甲、不受部位倍率，穿甲与否都生效
- :15760-15878 身体表 / :15880-15930 头部表
- :15856 脊柱受伤「下个回合无法使用其他动作，但仍可移动」
- :15912-15920 耳朵受损「步行超过 4m 后的下个回合无法做移动动作」
- :15774 / :15816 肋骨骨折、体内异物「徒步 >4m 的回合重受额外伤害」
"""

import re

import pytest

from core import combat as C
from core import roster
from core.db import connect
from cyberpunk import crit, dice

GID = "qq:crit"


@pytest.fixture()
def conn():
    return connect(":memory:")


def _combat(round_=1):
    return C.Combat(group_id=GID, campaign_id=None, scene="", round=round_,
                    turn_index=0, active=True)


def _pc(**kw):
    base = dict(group_id=GID, owner_qq="1001", name="阿飞", side="pc",
                initiative=20, hp=30, max_hp=30, sp=4, ref=7, dex=7, dodge=2)
    base.update(kw)
    return C.Combatant(**base)


def _enemy(**kw):
    base = dict(group_id=GID, owner_qq="enemy:1", name="打手", side="enemy",
                initiative=10, hp=20, max_hp=20, sp=4, ref=6, dex=6,
                dodge=4, skill=4)
    base.update(kw)
    return C.Combatant(**base)


def _injure(c, key):
    """给角色直接挂一条伤势（跳过掷骰，测惩罚用）。"""
    c.crits = c.crits + (key,)
    return c


def _fight(conn, **kw):
    """真卡真战斗：玩家先攻在前，且已拉到 8 米。"""
    card = roster.build(template="独狼", name="阿飞", **kw)
    roster.save(conn, GID, "1001", card)
    C.start(conn, GID, enemies=(("打手", 1),),
            roller=dice.ScriptedRoller([9, 2]))  # 16 vs 8，玩家先
    C.close_range(conn, GID, "1001", "距离 8 米")
    return C.load(conn, GID)


def _me(conn):
    return next(c for c in C.load(conn, GID).combatants if c.side == "pc")


def _foe(conn):
    return next(c for c in C.load(conn, GID).combatants if c.side == "enemy")


def _total(text):
    """从「……：18 vs DV 15」里取出判定总值。"""
    found = re.search(r"：(-?\d+) vs ", text)
    assert found, text
    return int(found.group(1))


# ── 表数据完整性（full.txt:15760-15930）───────────────────────


def test_tables_cover_every_2d6_result():
    assert sorted(crit.BODY_INJURIES) == list(range(2, 13))
    assert sorted(crit.HEAD_INJURIES) == list(range(2, 13))
    assert crit.SERIOUS_EXTRA_DAMAGE == 5
    assert crit.SERIOUS_TRIGGER_SIXES == 2


def test_table_for_switches_head_and_body():
    assert crit.table_for(head=True) is crit.HEAD_INJURIES
    assert crit.table_for(head=False) is crit.BODY_INJURIES


def test_representative_rows_match_the_book():
    body = crit.BODY_INJURIES
    assert body[4].move_penalty == 2 and body[4].death_penalty == 1   # 气胸
    assert body[8].move_penalty == 4                                  # 腿骨折
    assert body[12].move_penalty == 6 and body[12].no_dodge           # 断腿
    assert body[10].no_action_next                                    # 脊柱受伤
    assert body[5].walk_damage and body[7].walk_damage                # 肋骨/异物
    head = crit.HEAD_INJURIES
    assert head[9].head_mult == 3                                     # 颅骨骨裂
    assert head[10].no_move_next and head[12].no_move_next            # 耳朵
    assert head[11].no_speak                                          # 气管破裂


# ── 触发与查表 ────────────────────────────────────────────────


def test_one_six_does_not_trigger():
    c = _combat()
    pc = _pc()
    c.combatants = [pc]
    lines: list[str] = []
    C.maybe_serious_injury(c, pc, 1, head=False, roller=dice.ScriptedRoller([]),
                           lines=lines)
    assert pc.crits == () and pc.hp == 30 and lines == []


def test_two_sixes_trigger_body_injury():
    c = _combat()
    pc = _pc()
    c.combatants = [pc]
    lines: list[str] = []
    # 2D6 = 4 → 气胸：移速 −2、死亡豁免惩罚 +1、额外 5 点伤害
    C.maybe_serious_injury(c, pc, 2, head=False,
                           roller=dice.ScriptedRoller([2, 2]), lines=lines)
    assert pc.crits == ("pneumothorax",)
    assert pc.hp == 25
    assert pc.death_save_penalty == 1
    assert pc.move_penalty == 2
    assert any("气胸" in x for x in lines)
    assert any("严重伤" in x for x in lines)


def test_extra_damage_ignores_armor_and_head_mult():
    c = _combat()
    pc = _pc(sp=20)  # 护甲厚到能把普通伤害吃光
    c.combatants = [pc]
    C.maybe_serious_injury(c, pc, 2, head=True,
                           roller=dice.ScriptedRoller([2, 2]),
                           lines=[])
    # 头部表 4 → 眼睛受损；额外 5 点不削护甲、不乘部位倍率
    assert pc.crits == ("eye_damage",)
    assert pc.hp == 25


def test_head_table_used_when_aiming_head():
    c = _combat()
    enemy = _enemy()
    c.combatants = [enemy]
    C.maybe_serious_injury(c, enemy, 2, head=True,
                           roller=dice.ScriptedRoller([4, 5]), lines=[])
    assert enemy.crits == ("skull_fracture",)
    assert enemy.head_mult == 3


def test_same_injury_is_never_stacked():
    c = _combat()
    pc = _pc(crits=("pneumothorax",))
    c.combatants = [pc]
    # 先掷 4（已有气胸）→ 重掷 2+3=5（肋骨骨折），不能重复拿气胸
    C.maybe_serious_injury(c, pc, 2, head=False,
                           roller=dice.ScriptedRoller([2, 2, 2, 3]),
                           lines=[])
    assert pc.crits == ("pneumothorax", "rib_fracture")


def test_force_skips_the_sixes_check():
    """致命伤角色再挨打，不看出没出俩 6（full.txt:15741-15742）。"""
    c = _combat()
    pc = _pc(crits=(), hp=1)
    c.combatants = [pc]
    C.maybe_serious_injury(c, pc, 0, head=False,
                           roller=dice.ScriptedRoller([3, 3]), lines=[],
                           force=True)
    assert pc.crits == ("arm_fracture",)


# ── 惩罚属性汇总 ──────────────────────────────────────────────


def test_penalties_aggregate_from_multiple_injuries():
    pc = _pc(crits=("brain_damage", "concussion", "muscle_tear", "eye_lost",
                    "leg_fracture"))
    assert pc.action_penalty == -4          # 脑损伤 −2 + 脑震荡 −2
    assert pc.melee_penalty == -2           # 肌肉撕裂
    assert pc.ranged_penalty == -4          # 失去眼睛
    assert pc.move_penalty == 4             # 腿骨折
    # 伤口惩罚 = 基础（HP 满血时 0）+ 所有动作惩罚
    assert pc.wound_penalty == -4


def test_status_flags_come_from_injuries():
    pc = _pc(crits=("leg_lost", "trachea_rupture", "rib_fracture",
                    "ear_damage", "spinal_injury"))
    assert pc.no_dodge and pc.no_speak and pc.walk_damage
    assert pc.no_move_next and not pc.head_mult


# ── 判定接入：攻击检定 ────────────────────────────────────────


def test_ranged_penalty_lowers_attack_total(conn):
    _fight(conn)
    # 基准：9+REF7+手枪技能0 = 16（独狼卡没有手枪技能）
    text = C.attack(conn, GID, "1001", "打手", skill="手枪",
                    roller=dice.ScriptedRoller([9, 3, 3, 1]))
    assert _total(text) == 16

    C.end(conn, GID)
    _fight(conn)
    c = C.load(conn, GID)
    me = next(x for x in c.combatants if x.side == "pc")
    me.crits = ("eye_lost",)              # 失去眼睛：远程 −4
    C._save(conn, c)
    text = C.attack(conn, GID, "1001", "打手", skill="手枪",
                    roller=dice.ScriptedRoller([9, 3, 3, 1]))
    assert _total(text) == 12             # 9+7−4
    assert "没打中" in text                # 12 < DV 15


def test_melee_penalty_lowers_attack_total(conn):
    _fight(conn)
    c = C.load(conn, GID)
    me = next(x for x in c.combatants if x.side == "pc")
    me.crits = ("muscle_tear",)           # 肌肉撕裂：近战 −2
    C._save(conn, c)
    text = C.attack(conn, GID, "1001", "打手", skill="搏击",
                    roller=dice.ScriptedRoller([7, 4, 1]))
    assert _total(text) == 14             # 7+敏捷7+搏击2−2


def test_wound_penalty_still_applies_to_attack(conn):
    """伤势惩罚是叠在伤口惩罚之上的，不是替代它。"""
    _fight(conn)
    c = C.load(conn, GID)
    me = next(x for x in c.combatants if x.side == "pc")
    me.crits = ("finger_crush",)          # 手指粉碎：所有动作 −4
    me.hp = 15                            # ≤ max_hp/2 → 额外 −2
    C._save(conn, c)
    text = C.attack(conn, GID, "1001", "打手", skill="手枪",
                    roller=dice.ScriptedRoller([9, 3, 3, 1]))
    assert _total(text) == 10             # 9+7−4−2


def test_enemy_attack_also_suffers_ranged_penalty(conn):
    c = _combat()
    pc = _pc(range_m=8)
    enemy = _enemy(crits=("eye_lost",), skill=4, range_m=8)
    c.combatants = [pc, enemy]
    c.turn_index = 0
    lines: list[str] = []
    # 10+REF6+技能4−4 = 16（不减是 20）；后面伤害 3+3=6−护甲4=2
    C._enemy_turn(conn=conn, combat=c, enemy=enemy,
                  roller=dice.ScriptedRoller([10, 3, 3]), lines=lines)
    assert any("：16 vs DV 15" in x for x in lines), lines
    assert pc.hp == 28


# ── 无法闪避（断腿）──────────────────────────────────────────


def test_leg_lost_cannot_dodge_melee(conn):
    _fight(conn)
    c = C.load(conn, GID)
    foe = next(x for x in c.combatants if x.side == "enemy")
    foe.crits = ("leg_lost",)
    C._save(conn, c)
    me = _me(conn)
    # 只给攻击骰 + 伤害 1D6 + 敌人回合的骰：如果还要掷闪避，ScriptedRoller 会报不够
    text = C.attack(conn, GID, "1001", "打手", skill="搏击",
                    roller=dice.ScriptedRoller([1, 5, 3]))
    assert "无法闪避" in text
    assert me.alive


# ── 头部倍率 ──────────────────────────────────────────────────


def test_skull_fracture_triples_headshot(conn):
    _fight(conn, skill_overrides={"手枪": 6})
    c = C.load(conn, GID)
    foe = next(x for x in c.combatants if x.side == "enemy")
    foe.crits = ("skull_fracture",)
    C._save(conn, c)
    # 瞄准 −8：10+7+6−8=15 命中；伤害 3+3=6 − 护甲4 = 2，颅骨骨裂 ×3 = 6
    text = C.attack(conn, GID, "1001", "打手", skill="手枪", aim="头",
                    roller=dice.ScriptedRoller([10, 3, 3, 1]))
    assert "伤害×3" in text
    assert _foe(conn).hp == 14            # 20 − 6


# ── 移动预算与步行结算 ────────────────────────────────────────


def test_move_budget_subtracts_injury_penalty():
    card = {"stats": {"MOVE": 5}}
    assert C._move_budget(_pc(), card) == 5
    assert C._move_budget(_pc(crits=("pneumothorax",)), card) == 3   # −2
    assert C._move_budget(_pc(crits=("leg_fracture",)), card) == 1   # −4
    assert C._move_budget(_pc(crits=("leg_lost",)), card) == 1       # −6 → 最低 1


def test_move_budget_never_drops_below_one():
    assert C._move_budget(_pc(crits=("leg_lost", "pneumothorax")),
                          {"stats": {"MOVE": 1}}) == 1


def test_walk_damage_bites_after_more_than_four_meters():
    c = _combat()
    slot = C._slots(c)["1001"]
    pc = _pc(crits=("rib_fracture",))
    lines: list[str] = []
    C._after_walk(pc, 2, slot, c, lines)      # 4 米：不触发
    assert pc.hp == 30 and lines == []
    C._after_walk(pc, 3, slot, c, lines)      # 6 米：额外 5 点
    assert pc.hp == 25
    assert any("额外掉 5 点血" in x for x in lines)


def test_ear_injury_blocks_move_next_round():
    c = _combat(round_=3)
    slot = C._slots(c)["1001"]
    pc = _pc(crits=("ear_damage",))
    C._after_walk(pc, 3, slot, c, [])
    assert slot.no_move_round == 4            # 下个回合


def test_spinal_injury_blocks_actions_next_round():
    c = _combat(round_=2)
    pc = _pc()
    c.combatants = [pc]
    C.maybe_serious_injury(c, pc, 2, head=False,
                           roller=dice.ScriptedRoller([5, 5]), lines=[])
    assert pc.crits == ("spinal_injury",)
    assert C._slots(c)["1001"].no_action_round == 3


# ── 持久化 ────────────────────────────────────────────────────


def test_crits_and_penalties_survive_save_and_load(conn):
    _fight(conn)
    c = C.load(conn, GID)
    me = next(x for x in c.combatants if x.side == "pc")
    me.crits = ("leg_lost", "muscle_tear")
    me.death_save_penalty = 1
    C._save(conn, c)
    again = _me(conn)
    assert again.crits == ("leg_lost", "muscle_tear")
    assert again.move_penalty == 6 and again.melee_penalty == -2
    assert again.no_dodge and again.death_save_penalty == 1
