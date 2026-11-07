"""全自动射击（A3）：动作与弹药门槛、专用 DV 表、伤害乘算、闪避。

规则出处：``rules/red_core/raw/full.txt``
- :14800-14813 花一个动作 + 10 发子弹；弹匣不足 10 发不能全自动；
  判定用「全自动射击」技能；查全自动距离表；不能瞄准；
  目标 REF≥8 仍可选择闪避；命中后 2D6 伤害 × (判定值 − DV)，差值上限是
  武器标注的数字（冲锋枪 3 / 突击步枪 4）；两个伤害骰都是 6 → 一次严重伤势
- :14816-14840 全自动距离 DV 表（冲锋枪 20/17/20/25/30；
  突击步枪 22/20/17/20/25）
- :14691-14700 远程结算总则：只有 REF≥8 的防守者可选闪避；平局守方胜
- :8324 / :8354 武器表标注「全自动射击（3）」「全自动射击（4）」

「约定」：本仓库技能表用「自动武器」（:7062 / :12206 的释义）对应书里的
「全自动射击」技能；玩家口语「全自动/连射/扫射/连发」都当全自动。
"""

import re

import pytest

from core import combat as C
from core import roster
from core.db import connect
from cyberpunk import dice

GID = "qq:autofire"


@pytest.fixture()
def conn():
    return connect(":memory:")


def _fight(conn, *, enemy=("打手", 1), distance="距离 8 米", skill=6):
    """真卡真战斗：独狼 + 自动武器技能，玩家先攻，敌人已拉到 8 米。"""
    card = roster.build(template="独狼", name="阿飞",
                        skill_overrides={"自动武器": skill})
    roster.save(conn, GID, "1001", card)
    C.start(conn, GID, enemies=(enemy,), roller=dice.ScriptedRoller([9, 2]))
    if distance:
        C.close_range(conn, GID, "1001", distance)
    return C.load(conn, GID)


def _me(conn):
    return next(c for c in C.load(conn, GID).combatants if c.side == "pc")


def _foe(conn):
    return next(c for c in C.load(conn, GID).combatants if c.side == "enemy")


def _total(text):
    found = re.search(r"：(-?\d+) vs ", text)
    assert found, text
    return int(found.group(1))


def _set_ammo(conn, ammo, mag=0):
    combat = C.load(conn, GID)
    me = next(c for c in combat.combatants if c.side == "pc")
    me.ammo = ammo
    me.mag = mag
    C._save(conn, combat)


# ── 文本识别 ──────────────────────────────────────────────────


def test_looks_like_autofire_words():
    for text in ("全自动打他", "扫射", "连射", "连发", "突突他"):
        assert C.looks_like_autofire(text), text
    for text in ("开枪打他", "砍他", "移动"):
        assert not C.looks_like_autofire(text), text


def test_autofire_weapon_from_text():
    assert C.autofire_weapon_from_text("用突击步枪扫射") == "突击步枪"
    assert C.autofire_weapon_from_text("冲锋枪全自动") == "冲锋枪"
    assert C.autofire_weapon_from_text("全自动打他") is None


# ── 门槛：都不消耗动作 ────────────────────────────────────────


def test_non_autofire_weapon_is_refused(conn):
    _fight(conn)
    text = C.attack(conn, GID, "1001", "打手", autofire=True, weapon="手枪",
                    roller=dice.ScriptedRoller([10, 3, 3]))
    assert "没有全自动模式" in text
    assert "冲锋枪" in text and "突击步枪" in text
    assert _me(conn).ammo == -1          # 一发都没打出去
    assert _foe(conn).hp == 20


def test_cannot_aim_with_autofire(conn):
    _fight(conn)
    text = C.attack(conn, GID, "1001", "打手", autofire=True, weapon="冲锋枪",
                    aim="头部", roller=dice.ScriptedRoller([10, 3, 3]))
    assert "不能瞄准" in text
    assert _me(conn).ammo == -1
    assert _foe(conn).hp == 20


def test_needs_ten_rounds(conn):
    _fight(conn)
    _set_ammo(conn, 9)
    text = C.attack(conn, GID, "1001", "打手", autofire=True, weapon="冲锋枪",
                    roller=dice.ScriptedRoller([10, 3, 3]))
    assert "只剩 9 发" in text and "10 发" in text
    assert _me(conn).ammo == 9           # 没扣弹
    assert _foe(conn).hp == 20


def test_needs_distance(conn):
    _fight(conn, distance=None)          # 没给距离 → 不猜 DV
    text = C.attack(conn, GID, "1001", "打手", autofire=True, weapon="冲锋枪",
                    roller=dice.ScriptedRoller([10, 3, 3]))
    assert text == C.RANGE_ASK
    assert _me(conn).ammo == -1
    assert _foe(conn).hp == 20


# ── 命中与伤害乘算（full.txt:14810）──────────────────────────


def test_uses_autofire_dv_table_and_multiplies(conn):
    """8 米 → 冲锋枪全自动 DV 17（单发表这里是 13）。"""
    _fight(conn, distance="距离 8 米")
    text = C.attack(conn, GID, "1001", "打手", autofire=True, weapon="冲锋枪",
                    roller=dice.ScriptedRoller([10, 3, 3, 1]))
    # 10 + REF7 + 技能6 = 23 vs DV17 → 差值 6，封顶 3 发
    assert _total(text) == 23
    assert "vs DV 17" in text
    assert "取 3 发" in text and "上限 3" in text
    # 伤害 2D6=6，×3=18，护甲 4 → 打掉 14
    assert "18" in text and "打掉 14 血" in text
    assert _foe(conn).hp == 6
    assert _me(conn).ammo == 20          # 打光 10 发，弹匣 30


def test_rifle_caps_at_four(conn):
    """14 米 → 突击步枪 DV 17，差值 9 也只取 4 发。"""
    _fight(conn, distance="距离 14 米")
    text = C.attack(conn, GID, "1001", "打手", autofire=True, weapon="突击步枪",
                    roller=dice.ScriptedRoller([10, 3, 2, 1]))
    assert "vs DV 17" in text
    assert "取 4 发" in text and "上限 4" in text
    assert "打掉 16 血" in text          # (3+2)×4=20 − 4
    assert _foe(conn).hp == 4


def test_margin_below_cap_is_used_as_is(conn):
    """差值 2 < 上限 3 → 只乘 2。"""
    _fight(conn)
    text = C.attack(conn, GID, "1001", "打手", autofire=True, weapon="冲锋枪",
                    roller=dice.ScriptedRoller([6, 3, 3, 1]))
    # 6 + 7 + 6 = 19 vs 17 → 差值 2
    assert _total(text) == 19
    assert "取 2 发" in text
    assert "打掉 8 血" in text           # 6×2=12 − 4
    assert _foe(conn).hp == 12


def test_tie_and_below_miss(conn):
    """平局守方胜：19 vs 17 之外的边界，判定等于 DV 也算没打中。"""
    _fight(conn)
    text = C.attack(conn, GID, "1001", "打手", autofire=True, weapon="冲锋枪",
                    roller=dice.ScriptedRoller([4, 3, 3, 1]))
    # 4 + 7 + 6 = 17 vs DV 17 → 差值 0，不算命中
    assert _total(text) == 17
    assert "没打中" in text
    assert _foe(conn).hp == 20
    assert _me(conn).ammo == 20          # 子弹照打光


def test_double_six_triggers_serious_injury(conn):
    _fight(conn)
    text = C.attack(conn, GID, "1001", "打手", autofire=True, weapon="冲锋枪",
                    roller=dice.ScriptedRoller([10, 6, 6, 2, 2, 1]))
    assert "严重伤" in text               # 2D6 查表 2+2 → 气胸
    assert "气胸" in text
    foe = _foe(conn)
    assert foe.crits == ("pneumothorax",)
    assert foe.death_save_penalty == 1


# ── 目标 REF≥8 可选闪避（full.txt:14691-14700）───────────────


def test_ref8_target_can_dodge(conn):
    _fight(conn)
    combat = C.load(conn, GID)
    foe = next(c for c in combat.combatants if c.side == "enemy")
    foe.ref, foe.dex, foe.dodge = 8, 6, 4
    C._save(conn, combat)

    # 判定 23 vs 闪避 3+6+4=13 → 差值 10，仍封顶 3 发
    text = C.attack(conn, GID, "1001", "打手", autofire=True, weapon="冲锋枪",
                    roller=dice.ScriptedRoller([10, 3, 3, 3, 1]))
    assert "vs 闪避 13" in text
    assert "取 3 发" in text
    assert _foe(conn).hp == 6


def test_dodge_can_beat_the_burst(conn):
    _fight(conn)
    combat = C.load(conn, GID)
    foe = next(c for c in combat.combatants if c.side == "enemy")
    foe.ref, foe.dex, foe.dodge = 8, 6, 4
    C._save(conn, combat)

    # 判定 18 vs 闪避 10+6+4=20 → 没打中
    text = C.attack(conn, GID, "1001", "打手", autofire=True, weapon="冲锋枪",
                    roller=dice.ScriptedRoller([5, 10, 1]))
    assert _total(text) == 18
    assert "vs 闪避 20" in text
    assert "没打中" in text
    assert _foe(conn).hp == 20


# ── 弹药写回 ─────────────────────────────────────────────────


def test_ammo_is_persisted(conn):
    _fight(conn)
    _set_ammo(conn, 28, mag=30)          # 弹匣里只剩 28 发
    C.attack(conn, GID, "1001", "打手", autofire=True, weapon="冲锋枪",
             roller=dice.ScriptedRoller([10, 3, 3, 1]))
    combat = C.load(conn, GID)
    me = next(c for c in combat.combatants if c.side == "pc")
    assert me.ammo == 18 and me.mag == 30
    assert me.rounds_left("冲锋枪") == 18


def test_rounds_left_uses_magazine_default(conn):
    _fight(conn)
    me = _me(conn)
    assert me.ammo == -1                 # 未计数 = 满弹匣
    assert me.rounds_left("冲锋枪") == 30
    assert me.rounds_left("手枪") == 8
    assert me.rounds_left("狙击步枪") == 5
