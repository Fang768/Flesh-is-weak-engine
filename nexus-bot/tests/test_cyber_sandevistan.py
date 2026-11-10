"""桑德维斯坦：动作激活的加速组件。

规则原文（``rules/red_core/raw/full.txt``）：

* ``:9807-9812``「加速组件。当使用行动激活其之后，**增加 3 点先攻**，并
  **持续 1 分钟**。拥有 **1 小时冷却**。加速组件只能在身上安装其中一种。需要神经链路。」
* ``:11327-11331``「每一个 TURNS（回合）大概有 3 秒钟…一个 ROUND（轮次）
  也是差不多 3 秒钟」→ **1 分钟 = 20 轮，1 小时 = 1200 轮**。
* ``:9804-9805`` 克伦齐科夫 +2 常驻，与桑德维斯坦**互斥**（只能装一种）。
"""

from __future__ import annotations

import pytest

from core import combat as C
from core import db, roster
from cyberpunk import cyber_bonus, dice

GID = "qq:sandy"


@pytest.fixture(autouse=True)
def _clean_slots():
    """``combat._SLOTS`` 是模块级全局（按 group_id），跨测试文件会残留
    action 标记，导致「动作已用完」这类假失败。每个用例前后都清一次。"""
    C._SLOTS.pop(GID, None)
    yield
    C._SLOTS.pop(GID, None)


@pytest.fixture()
def conn():
    return db.connect(":memory:")


def _pc(conn, cyber_items=("神经链路", "桑德维斯坦"), qq="u1"):
    roster.save(conn, GID, qq, roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, GID, qq)
    card["state"] = {"cyber": list(cyber_items)}
    roster.save(conn, GID, qq, card)
    return roster.get(conn, GID, qq)


def _fight(conn):
    # 先清场：同 group_id 的上一场战斗记录会残留（combats 表按 group_id 一行），
    # 不清就会拿到上一场的回合指针。
    C.end(conn, GID)
    return C.start(conn, GID, enemies=(("打手", 1),), roller=dice.RandomRoller())


def _player(combat):
    return next(c for c in combat.combatants if c.side == "pc")


def _my_turn(conn):
    """把回合指针交回玩家（并清动作槽）。

    ``_advance`` 会顺带结算敌方回合，而敌人先攻可能更高——那样指针不在玩家
    身上，``act`` 会正确返回 None。测试要断言的是激活逻辑本身，所以直接把
    指针摆回玩家，绕开随机先攻带来的不确定性。
    """
    combat = C.load(conn, GID)
    me = _player(combat)
    combat.turn_index = combat.order.index(me)
    C._save(conn, combat)
    C._reset_slots(C.load(conn, GID))
    return me


# ---------- 时间换算与常量 ----------


def test_round_is_three_seconds():
    """书里 1 轮 ≈ 3 秒，所以 1 分钟 = 20 轮、1 小时 = 1200 轮。"""
    assert C.SANDY_DURATION_ROUNDS == 20
    assert C.SANDY_COOLDOWN_ROUNDS == 1200
    assert C.SANDY_INITIATIVE == 3


# ---------- 激活 ----------


def test_activate_grants_initiative(conn):
    _pc(conn)
    combat = _fight(conn)
    me = _player(combat)
    before = me.initiative
    text = C.act(conn, GID, "u1", "激活桑德维斯坦", roller=dice.RandomRoller())
    assert "激活了桑德维斯坦" in text
    assert "+3" in text
    assert _player(C.load(conn, GID)).initiative == before + 3


def test_activate_consumes_the_action(conn):
    _pc(conn)
    _fight(conn)
    C.act(conn, GID, "u1", "激活桑德维斯坦", roller=dice.RandomRoller())
    # 激活＝一个动作，会推进回合。把指针交回玩家后再试，应提示「已经在跑」。
    _my_turn(conn)
    text = C.act(conn, GID, "u1", "激活桑德维斯坦", roller=dice.RandomRoller())
    assert text and "已经开着" in text


def test_activate_requires_the_cyberware(conn):
    _pc(conn, cyber_items=[])
    _fight(conn)
    text = C.act(conn, GID, "u1", "激活桑德维斯坦", roller=dice.RandomRoller())
    assert "没装" in text


def test_activate_reports_duration_and_cooldown(conn):
    _pc(conn)
    _fight(conn)
    text = C.act(conn, GID, "u1", "开桑德维斯坦", roller=dice.RandomRoller())
    assert "1 分钟" in text or "20 轮" in text
    assert "冷却" in text


@pytest.mark.parametrize("phrase", ["激活桑德维斯坦", "开桑德维斯坦",
                                    "启动桑德维斯坦", "激活加速"])
def test_activation_phrases(phrase):
    assert C.looks_like_activate(phrase)


def test_non_activation_phrases():
    for phrase in ("攻击", "移动", "装填", "激活某个别的东西"):
        assert not C.looks_like_activate(phrase) or "桑德维斯坦" not in phrase


# ---------- 持续与冷却 ----------


def test_active_window_is_twenty_rounds(conn):
    _pc(conn)
    combat = _fight(conn)
    me = _player(combat)
    C.act(conn, GID, "u1", "激活桑德维斯坦", roller=dice.RandomRoller())
    reloaded = _player(C.load(conn, GID))
    assert C.sandy_active(reloaded, C.load(conn, GID))


def test_cooldown_starts_after_activation(conn):
    _pc(conn)
    _fight(conn)
    C.act(conn, GID, "u1", "激活桑德维斯坦", roller=dice.RandomRoller())
    combat = C.load(conn, GID)
    me = _player(combat)
    assert C.sandy_on_cooldown(me, combat)


def test_cannot_reactivate_during_cooldown(conn):
    _pc(conn)
    combat = _fight(conn)
    C.act(conn, GID, "u1", "激活桑德维斯坦", roller=dice.RandomRoller())
    # 把持续期推过去（模拟过了 20 轮），冷却还没到；同时放开动作槽
    combat = C.load(conn, GID)
    me = _player(combat)
    me.cyber_active_until = 0
    C._save(conn, combat)
    _my_turn(conn)
    text = C.act(conn, GID, "u1", "激活桑德维斯坦", roller=dice.RandomRoller())
    assert text and "冷却" in text


def test_ready_again_after_cooldown(conn):
    """冷却结束后可再激活。

    用脚本骰固定先攻顺序（玩家先动），否则敌人随机先攻更高时
    ``_advance`` 之后指针不在玩家身上，``act`` 会返回 None——那是**正确的**
    引擎行为，只是测试没法断言。用固定顺序把不确定性去掉。
    """
    _pc(conn)
    C.end(conn, GID)
    # 脚本骰：玩家先攻 10、敌人先攻 1（玩家先动，且两回合内不会再轮到敌人挡路）
    combat = C.start(conn, GID, enemies=(("打手", 1),),
                     roller=dice.ScriptedRoller([10, 1], loop=True))
    me = _player(combat)
    me.initiative = 30          # 确保玩家始终排第一
    C._save(conn, combat)

    text = C.act(conn, GID, "u1", "激活桑德维斯坦", roller=dice.RandomRoller())
    assert "激活了桑德维斯坦" in text

    # 模拟冷却结束 + 动作槽恢复
    reloaded = C.load(conn, GID)
    me = _player(reloaded)
    me.cyber_active_until = 0
    me.cyber_cooldown_until = 0
    C._save(conn, reloaded)

    _my_turn(conn)
    text = C.act(conn, GID, "u1", "激活桑德维斯坦", roller=dice.RandomRoller())
    assert text and "激活了桑德维斯坦" in text


def test_sandy_active_helpers():
    fight = C.Combat(group_id="g", campaign_id=None, scene="", round=5,
                     turn_index=0, active=True)
    me = C.Combatant(group_id="g", owner_qq="u1", name="阿飞", side="pc",
                     initiative=10, hp=20, max_hp=20)
    assert not C.sandy_active(me, fight)
    me.cyber_active_until = 10
    assert C.sandy_active(me, fight)
    me.cyber_active_until = 4
    assert not C.sandy_active(me, fight)


# ---------- 与克伦齐科夫互斥 ----------


def test_sandevistan_and_kerenzikov_are_exclusive():
    from cyberpunk import slots

    msg = slots.check_install(["克伦齐科夫"], "桑德维斯坦")
    assert "只能装一种" in msg
    msg = slots.check_install(["桑德维斯坦"], "克伦齐科夫")
    assert "只能装一种" in msg


def test_kerenzikov_gives_flat_plus_two(conn):
    """克伦齐科夫是常驻 +2，不需要激活。"""
    _pc(conn, cyber_items=["神经链路", "克伦齐科夫"])
    combat = _fight(conn)
    me = _player(combat)
    assert cyber_bonus.initiative_bonus(roster.get(conn, GID, "u1")) == 2
    assert me.init_bonus == 2
    assert not C.sandy_on_cooldown(me, combat)


# ---------- 持久化 ----------


def test_activation_state_survives_reload(conn):
    _pc(conn)
    _fight(conn)
    C.act(conn, GID, "u1", "激活桑德维斯坦", roller=dice.RandomRoller())
    combat = C.load(conn, GID)
    me = _player(combat)
    assert me.cyber_active_until > 0
    assert me.cyber_cooldown_until > 0


# ---------- 叠加规则（顺带修的真 bug） ----------


def test_same_cyberware_does_not_stack():
    """``:9698-9699``「多次安装相同赛博组件的增益不会叠加」。"""
    card = {"cyber": ["图像增强", "图像增强"]}
    assert cyber_bonus.check_bonus(card, skill="觉察")[0] == 2


def test_bone_lacing_does_stack_by_exception():
    """骨肉织绕书里明写「重复改造将叠加此效果」（``:10109-10110``）。"""
    assert cyber_bonus.attribute_bonus({"cyber": ["骨肉织绕"]}) == {"BODY": 2}
    assert cyber_bonus.attribute_bonus(
        {"cyber": ["骨肉织绕", "骨肉织绕"]}) == {"BODY": 4}


def test_armor_takes_max_not_sum():
    """护甲 SP 取高，不叠加。"""
    assert cyber_bonus.armor_sp(
        {"cyber": ["编织皮肤", "编织皮肤"]}) == 7
