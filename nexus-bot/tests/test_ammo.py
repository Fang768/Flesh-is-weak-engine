"""弹药计数（A4）：单发扣弹、空仓拒绝、装填补满、弓不耗弹。

规则出处：``rules/red_core/raw/full.txt``
- :14543-14545 装填是「一个其他动作」，用一种子弹种类**完全重新装填**（补满整个弹匣），
  不能在一个弹匣里混装多种子弹
- :14436 战时动作简表：「装填武器——用一种弹药重新填满 / 更换武器的弹匣」
- :14691-14700 远程结算只看判定对 DV / 闪避，**没有**「未命中不耗弹」的说法，
  所以开枪就扣子弹
- :14898-14899 弓 / 十字弩「拈弓搭箭是用弓进行攻击的动作的一部分」，
  永远不需要用装填动作，即不耗弹也不会打空

「约定」：
- 单发攻击扣 1 发（书里按 ROF 允许一个攻击动作打多发，但本仓库一次攻击只结算一发，
  所以扣 1 发）
- 弹匣容量按武器类别取 ``_MAG_DEFAULT``（规则书里是逐枪的具体值，见 :8278-8300）
- NPC（敌人）不进入弹药计数：``ammo`` 恒为 ``-1``，不会打空
"""

import pytest

from core import combat as C
from core import roster
from core.db import connect
from cyberpunk import dice

GID = "qq:ammo"

#: 攻击 → 伤害 2D6 → 敌人回合攻击 → 敌人伤害 2D6。多给几个值不够就循环，
#: 这些用例只关心玩家侧的弹药，敌人那边的骰面不影响断言。
_ROLLS = [10, 3, 3, 1, 1]


def _roller(*extra):
    return dice.ScriptedRoller(list(extra) + _ROLLS, loop=True)


@pytest.fixture()
def conn():
    return connect(":memory:")


def _fight(conn, *, enemy=("打手", 1), distance="距离 8 米", skill=None):
    """真卡真战斗：独狼（补一手枪技能），玩家先攻，敌人已拉到 8 米（手枪 DV 15）。"""
    card = roster.build(template="独狼", name="阿飞",
                        skill_overrides={"手枪": 4, **(skill or {})})
    roster.save(conn, GID, "1001", card)
    C.start(conn, GID, enemies=(enemy,), roller=dice.ScriptedRoller([9, 2]))
    if distance:
        C.close_range(conn, GID, "1001", distance)
    return C.load(conn, GID)


def _me(conn):
    return next(c for c in C.load(conn, GID).combatants if c.side == "pc")


def _foe(conn):
    return next(c for c in C.load(conn, GID).combatants if c.side == "enemy")


def _set_ammo(conn, ammo, mag=0):
    combat = C.load(conn, GID)
    me = next(c for c in combat.combatants if c.side == "pc")
    me.ammo = ammo
    me.mag = mag
    C._save(conn, combat)


def _my_turn(conn) -> bool:
    """回合还停在玩家身上（= 刚才那句话没消耗动作）。"""
    combat = C.load(conn, GID)
    cur = combat.current
    return cur is not None and cur.side == "pc"


# ── 单发扣弹 ──────────────────────────────────────────────────


def test_first_shot_starts_counter_at_full_magazine(conn):
    """没计数过（ammo=-1）就先按满弹匣算，打一枪变 7/8。"""
    _fight(conn)
    assert _me(conn).ammo == -1
    text = C.attack(conn, GID, "1001", "打手", roller=_roller())
    assert "弹匣剩 7 发" in text
    me = _me(conn)
    assert me.ammo == 7
    assert me.mag == 0                     # mag 没学到，按武器类别推
    assert me.rounds_left("手枪") == 7
    assert me.rounds_left("冲锋枪") == 7    # ammo 已经比 30 小，取小值


def test_hit_spends_one_round(conn):
    _fight(conn)
    C.attack(conn, GID, "1001", "打手", roller=_roller())
    assert _me(conn).ammo == 7
    # 判定 10+7=17 ≥ DV15 命中；伤害 3+3=6，护甲 4 → 打掉 2
    assert _foe(conn).hp == 18


def test_miss_also_spends_round(conn):
    """规则书没给「未命中不耗弹」，开枪就扣（:14691-14700）。"""
    _fight(conn)
    text = C.attack(conn, GID, "1001", "打手",
                    roller=dice.ScriptedRoller([1, 1, 1, 1, 1], loop=True))
    assert "没打中" in text
    assert "弹匣剩 7 发" in text
    assert _me(conn).ammo == 7


# ── 空仓 ──────────────────────────────────────────────────────


def test_empty_magazine_refuses_without_spending_action(conn):
    _fight(conn)
    _set_ammo(conn, 0)
    text = C.attack(conn, GID, "1001", "打手", roller=_roller())
    assert "没子弹了" in text and "装填" in text
    assert _me(conn).ammo == 0
    assert _my_turn(conn)                  # 动作没被消耗，还轮到你
    assert _foe(conn).hp == 20


def test_can_still_act_after_empty_refusal(conn):
    """空仓被拒绝后，同一个回合还能改做别的动作（装填）。"""
    _fight(conn)
    _set_ammo(conn, 0)
    C.attack(conn, GID, "1001", "打手", roller=_roller())
    assert _my_turn(conn)
    text = C.act(conn, GID, "1001", "装填", roller=_roller())
    assert "装填" in text
    assert _me(conn).ammo == 8


# ── 装填 ──────────────────────────────────────────────────────


def test_reload_fills_the_whole_magazine(conn):
    _fight(conn)
    _set_ammo(conn, 3)
    C.act(conn, GID, "1001", "装填", roller=_roller())
    assert _me(conn).ammo == 8


def test_reload_reports_before_and_after(conn):
    _fight(conn)
    _set_ammo(conn, 3)
    text = C.act(conn, GID, "1001", "装填", roller=_roller())
    assert "3/8 → 8/8" in text


def test_reload_uses_learned_capacity(conn):
    """`mag` 学到具体枪的容量时，装填补到那个数（不是类别默认值）。"""
    _fight(conn)
    _set_ammo(conn, 5, mag=12)
    C.act(conn, GID, "1001", "装填", roller=_roller())
    me = _me(conn)
    assert me.ammo == 12
    assert me.rounds_left("手枪") == 12


def test_reload_does_not_share_round_between_shots(conn):
    """装填后还能接着打，且每枪扣一发。"""
    _fight(conn)
    _set_ammo(conn, 1)
    C.act(conn, GID, "1001", "装填", roller=_roller())
    assert _me(conn).ammo == 8
    C.attack(conn, GID, "1001", "打手", roller=_roller())
    assert _me(conn).ammo == 7


# ── 不耗弹的路径 ──────────────────────────────────────────────


def test_melee_does_not_spend_rounds(conn):
    _fight(conn)
    C.attack(conn, GID, "1001", "打手", skill="近战武器", roller=_roller())
    assert _me(conn).ammo == -1


def test_bow_never_needs_rounds(conn):
    """弓：拈弓搭箭是攻击动作的一部分，永不装填也不耗弹（:14898-14899）。"""
    _fight(conn)
    _set_ammo(conn, 0)                     # 没箭也能射（不计数）
    text = C.attack(conn, GID, "1001", "打手", skill="弓", roller=_roller())
    assert "没子弹了" not in text
    assert _me(conn).ammo == 0


def test_bow_reload_is_refused(conn):
    """弓没有「装填」这个动作，说了也是白说（不消耗动作）。"""
    _fight(conn)
    combat = C.load(conn, GID)
    me = next(c for c in combat.combatants if c.side == "pc")
    me.damage = "弓"
    C._save(conn, combat)
    text = C.act(conn, GID, "1001", "装填", roller=_roller())
    assert "永远不用装填" in text
    assert _my_turn(conn)


# ── 全自动与打掩体 ────────────────────────────────────────────


def test_autofire_spends_ten_rounds(conn):
    card = roster.build(template="独狼", name="阿飞",
                        skill_overrides={"自动武器": 6})
    roster.save(conn, GID, "1001", card)
    C.start(conn, GID, enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
    C.close_range(conn, GID, "1001", "距离 8 米")
    text = C.attack(conn, GID, "1001", "打手", autofire=True, weapon="冲锋枪",
                    roller=_roller())
    assert "打光 10 发" in text
    assert _me(conn).ammo == 20


# ── 存盘 ──────────────────────────────────────────────────────


def test_ammo_survives_save_and_load(conn):
    _fight(conn)
    _set_ammo(conn, 5, mag=12)
    C._save(conn, C.load(conn, GID))
    me = _me(conn)
    assert me.ammo == 5
    assert me.mag == 12
    assert me.rounds_left("手枪") == 5


# ── NPC 不计数 ────────────────────────────────────────────────


def test_enemy_ammo_stays_uncounted(conn):
    _fight(conn)
    assert _foe(conn).ammo == -1
    C.attack(conn, GID, "1001", "打手", roller=_roller())
    assert _foe(conn).ammo == -1


def test_rounds_left_clamps_to_capacity(conn):
    """ammo 被写坏也不会超过弹匣容量。"""
    _fight(conn)
    _set_ammo(conn, 99, mag=8)
    me = _me(conn)
    assert me.rounds_left("手枪") == 8
    assert me.rounds_left("冲锋枪") == 8      # 有 mag 就按 mag
