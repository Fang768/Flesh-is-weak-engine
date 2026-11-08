"""射速（ROF）：一次攻击动作打两下（方案3）。

规则依据（``rules/red_core/raw/full.txt``）：
- :14477-14499「有些类型的攻击比其他的攻击速率更快，这让它们可以在一次攻击动作当中
  射击/挥砍两次…所有 ROF 为 2 的攻击都可以适用于分段移动」
- :14557「射速（ROF）：一次攻击动作中该武器能射击的最大次数。」
- :14666-14685 DATA「无论你持有多少武器，作为动作的一部分，你都不能进行超过两次攻击
  判定。即使你双持武器，在一个动作中也不能用两件 1ROF 的武器进行攻击。」
- :14523 瞄准射击把 ROF 限成 1（书中「瞄准 = 一个其他动作，不能配合 ROF 2」）

「约定」：
- 引擎默认仍只结算一发（``shots=None`` → 1），玩家明确「连打/两下/双发」才按 ROF 连打，
  免得把老玩家的对话节奏全部打乱。
- 本仓库按武器类别取代表 ROF（武器表逐枪：手枪 2、冲锋枪/步枪/霰弹/狙击/弓 1、
  近战轻/中/重 2）。
- 连打两下各扣一发子弹（each attack spends one round, :14691-14700）。
"""

import pytest

from core import combat as C
from core import roster
from core.db import connect
from cyberpunk import dice

GID = "qq:rof"


class _FixedRoller(dice.Roller):
    """d10 恒 10，d6 恒 3 —— 判定必中、2D6 恒 6（减护甲后每下 2 点）。"""

    def roll(self, sides: int = 10) -> int:
        return 10 if sides >= 10 else 3


@pytest.fixture()
def conn():
    return connect(":memory:")


def _fight(conn, *, skill=4, enemy=("打手", 1), distance="距离 8 米", enemy_hp=None):
    card = roster.build(template="独狼", name="阿飞",
                        skill_overrides={"手枪": skill, "自动武器": 6,
                                         "近战武器": 4})
    roster.save(conn, GID, "1001", card)
    C.start(conn, GID, enemies=(enemy,), scene="a1.opera",
            roller=dice.ScriptedRoller([9, 2]))
    if distance:
        C.close_range(conn, GID, "1001", distance)
    fight = C.load(conn, GID)
    me = next(c for c in fight.combatants if c.side == "pc")
    foe = next(c for c in fight.combatants if c.side == "enemy")
    if enemy_hp is not None:
        foe.hp = foe.max_hp = enemy_hp
    C._save(conn, fight)
    return fight, me, foe


def _me(conn) -> "C.Combatant":
    return next(c for c in C.load(conn, GID).combatants if c.side == "pc")


def _foe(conn) -> "C.Combatant":
    return next(c for c in C.load(conn, GID).combatants if c.side == "enemy")


def _shoot(conn, *, shots=None, rolls=None, melee=False, ammo=None, roller=None):
    fight = C.load(conn, GID)
    me = next(c for c in fight.combatants if c.side == "pc")
    if ammo is not None:
        me.ammo = me.mag = ammo
    fight.turn_index = fight.order.index(me)
    fight.round += 1
    C._reset_slots(fight)
    C._save(conn, fight)
    # 独立序列最难对齐 d10/d6，默认用「每面固定」roller：
    # d10 → 10（命中），d6 → 3（2D6=6，减护甲 4 后每下 2 点）
    roller = roller or _FixedRoller()
    return C.attack(conn, GID, "1001", "打手",
                    skill="近战武器" if melee else "手枪",
                    shots=shots, roller=roller)


# ---------- 表 / 辅助 ----------


def test_rof_defaults_match_the_book():
    assert C._rof_of("手枪", melee=False) == 2
    assert C._rof_of("近战武器", melee=True) == 2
    assert C._rof_of("冲锋枪", melee=False) == 1
    assert C._rof_of("突击步枪", melee=False) == 1
    assert C._rof_of("霰弹枪", melee=False) == 1
    assert C._rof_of("狙击步枪", melee=False) == 1
    assert C._rof_of("弓", melee=False) == 1
    assert C._rof_of("榴弹发射器", melee=False) == 1
    assert C._rof_of("火箭筒", melee=False) == 1


def test_rof_never_exceeds_cap():
    assert C.ROF_CAP == 2
    assert C._rof_of("手枪", melee=False) <= C.ROF_CAP
    assert C._rof_of("近战武器", melee=True) <= C.ROF_CAP


def test_shots_from_text():
    assert C.shots_from_text("连打两下") == 2
    assert C.shots_from_text("双发") == 2
    assert C.shots_from_text("连开两枪") == 2
    assert C.shots_from_text("打两枪") == 2
    assert C.shots_from_text("快速射击") == 2
    assert C.shots_from_text("开枪打他") is None
    assert C.shots_from_text("") is None


# ---------- 不开挂：默认仍是一发 ----------


def test_default_is_still_one_shot():
    """shots=None 时不连打，保持向后兼容（老对话节奏不变）。"""
    conn = _conn()
    _fight(conn, enemy_hp=40)
    before = _foe(conn).hp
    text = _shoot(conn, shots=None)
    assert "连打" not in text
    # 2D6=6 − 护甲4 = 2
    assert _foe(conn).hp == before - 2


# ---------- 远程：连打两下 ----------


def test_two_shots_on_autofire_and_single_both_land():
    conn = _conn()
    _fight(conn, enemy_hp=60)
    before = _foe(conn).hp
    text = _shoot(conn, shots=2)
    assert "连打 2 下" in text
    assert "（第 1 下）" in text and "（第 2 下）" in text
    # 两下 2D6=6：第一下对护甲 4 → 2 点；护甲磨掉 1 后第二下对 3 → 3 点。共 5。
    assert _foe(conn).hp == before - 5


def test_two_shots_spend_two_rounds():
    conn = _conn()
    _fight(conn, enemy_hp=60)
    _shoot(conn, shots=2)
    assert _me(conn).ammo == 6  # 满 8 发 − 2


def _first_miss_roller():
    """第 1 下 d10=1（打偏）、第 2 下 d10=10；d6 恒 3。"""
    calls = {"n": 0}

    def roll(sides=10):
        if sides >= 10:
            calls["n"] += 1
            return 1 if calls["n"] == 1 else 10
        return 3

    return type("MissFirst", (), {"roll": roll})()


def _first_miss_second_hit():
    conn = _conn()
    _fight(conn, enemy_hp=40)
    before = _foe(conn).hp
    text = _shoot(conn, shots=2, roller=_first_miss_roller())
    assert "没打中" in text
    # 第 2 下 2D6=6 − 4 = 2
    assert _foe(conn).hp == before - 2


def test_second_shot_stops_when_target_dies():
    conn = _conn()
    _fight(conn, enemy_hp=2)  # 第一下就打死
    text = _shoot(conn, shots=2)
    # 第一下 2D6=6 − 4 = 2 → 2 HP 归零，第二下不再结算
    assert _foe(conn).dead
    assert "（第 2 下）" not in text


def test_two_shots_only_take_one_action():
    """连打两下仍然只是「一次攻击动作」——结算中不会因动作已用被拦下。"""
    conn = _conn()
    _fight(conn, enemy_hp=60)
    text = _shoot(conn, shots=2)
    # 两次攻击都在同一个动作里完成，没有「动作已经用过了」的中途报错
    assert "动作已经用过了" not in text
    assert "（第 2 下）" in text


# ---------- 近战：连挥两下 ----------


def test_melee_burst():
    conn = _conn()
    # 贴脸
    _, _, foe = _fight(conn, enemy_hp=80, distance="距离 1 米")
    before = foe.hp
    # 近战：每下 2D6=6 − 护甲(4+1)//2=2 → 4 点，两下 8
    text = _shoot(conn, shots=2, melee=True)
    assert "连打 2 下" in text
    assert _foe(conn).hp == before - 4 * 2


# ---------- 装甲磨损：两下都算 ----------


def test_two_shots_both_ablate_armor():
    conn = _conn()
    _fight(conn, enemy_hp=60)
    _shoot(conn, shots=2)
    assert _foe(conn).sp_wear == 2


def _conn():
    return connect(":memory:")