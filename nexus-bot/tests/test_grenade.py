"""阶段 7：手榴弹投掷（书内页 151 投掷 / 153 爆炸物）。

规则口径：DEX+运动+1D10 对抗榴弹发射器距离 DV；爆心 5×5 格、伤害骰只掷一次；
掩体挡得住就免伤，打烂就吃满；没扔准时落点偏移；REF≥8 可闪避。
"""

import pytest

from core import combat as C
from core import roster, session
from core.db import connect
from cyberpunk import dice

GID = "qq:grenade"


@pytest.fixture()
def conn():
    return connect(":memory:")


def _setup(conn, enemies=None, gear=True):
    roster.save(conn, GID, "u1", roster.build(template="独狼", name="阿飞"))
    if gear:
        roster.add_gear(conn, GID, "u1", "手榴弹")
    gate = session.Gate(conn)
    camp = gate.start_new(GID, "手雷测试", "opera")
    gate.set_scene(camp.id, "a1.opera")
    C.start(
        conn,
        GID,
        campaign_id=camp.id,
        scene="a1.opera",
        enemies=enemies or [{"name": "小丑", "count": 1}],
        roller=dice.RandomRoller(1),
    )
    return C.load(conn, GID)


def _place(conn, fight, pc_pos, spots):
    pc = next(c for c in fight.order if c.side == "pc")
    pc.x, pc.y = pc_pos
    for name, pos in spots.items():
        c = next(c for c in fight.combatants if c.name == name)
        c.x, c.y = pos
    fight.turn_index = fight.order.index(pc)
    C._save(conn, fight)


def test_without_grenade_does_not_use_action(conn):
    fight = _setup(conn, gear=False)
    _place(conn, fight, (7, 7), {"小丑": (5, 7)})
    text = C.throw_grenade(conn, GID, "u1", "扔手榴弹炸小丑",
                           roller=dice.ScriptedRoller([10], loop=True))
    assert "没有手榴弹" in text
    assert C._slots(C.load(conn, GID))["u1"].action is False


def test_blast_hits_5x5_and_cover_shields(conn):
    fight = _setup(conn, enemies=[{"name": "小丑", "count": 3}])
    _place(conn, fight, (7, 7), {"小丑1": (3, 7), "小丑2": (3, 9),
                                 "小丑3": (9, 9)})
    # 伤害 6D6 = 24：爆心小丑1 掉 24-11=13 血；小丑2 隔着吧台（30 耐久）免伤。
    text = C.throw_grenade(
        conn, GID, "u1", "扔手榴弹炸小丑1",
        roller=dice.ScriptedRoller([10, 4, 4, 4, 4, 4, 4], loop=True),
    )
    assert "距离 8 米，DV 15" in text
    assert "爆心 5×5 格" in text and "2 个目标" in text
    assert "小丑1 被炸" in text and "掉 13 血" in text
    assert "小丑2 躲在吧台后面" in text
    assert "小丑3 被炸" not in text
    saved = C.load(conn, GID)
    assert saved.covers["bar"] == 6
    assert "手榴弹" not in (roster.get(conn, GID, "u1")["gear"] or [])


def test_miss_scatters_within_blast(conn):
    fight = _setup(conn)
    _place(conn, fight, (7, 7), {"小丑": (5, 7)})
    # 判定 1+7=8 < DV15 没扔准：方向 4 → (-1,+1)，偏 1 格到 (4,8)。
    text = C.throw_grenade(
        conn, GID, "u1", "扔手榴弹炸小丑",
        roller=dice.ScriptedRoller([1, 4, 1, 4, 4, 4, 4, 4, 4], loop=True),
    )
    assert "落点偏到 (4,8)" in text
    assert "小丑 被炸" in text


def test_ref_8_target_can_dodge(conn):
    fight = _setup(conn, enemies=[{"name": "小丑", "count": 1,
                                   "data": {"ref": 8}}])
    _place(conn, fight, (7, 7), {"小丑": (5, 7)})
    # 命中骰 10+7=17；小丑 REF8 闪避掷 10+8=18 > 17，闪出爆炸范围。
    text = C.throw_grenade(
        conn, GID, "u1", "扔手榴弹炸小丑",
        roller=dice.ScriptedRoller([10, 4, 4, 4, 4, 4, 4, 10], loop=True),
    )
    assert "闪出了爆炸范围" in text
    assert "小丑 被炸" not in text


def test_out_of_throw_range_is_refused(conn):
    fight = _setup(conn)
    _place(conn, fight, (1, 1), {"小丑": (16, 10)})
    text = C.throw_grenade(conn, GID, "u1", "扔手榴弹炸小丑",
                           roller=dice.ScriptedRoller([10], loop=True))
    assert "太远了" in text and "30 米" in text
    assert "手榴弹" in (roster.get(conn, GID, "u1")["gear"] or [])


def test_turn_flow_routes_grenade(conn, monkeypatch):
    """自由输入「扔手榴弹」由 turn_flow 直接路由，不交给模型编。"""
    import asyncio

    from core import turn_flow
    from core.session import Gate

    conn.execute(
        "INSERT INTO campaigns(id, title, status, scene, scenario, created_at, updated_at)"
        " VALUES (7, '测试', 'active', 'a1.opera', 'opera', 'now', 'now')"
    )
    conn.execute(
        "INSERT INTO group_sessions(group_id, campaign_id, mode, updated_at)"
        " VALUES (?, 7, 'active', 'now')",
        (GID,),
    )
    roster.save(conn, GID, "u1", roster.build(template="独狼", name="阿飞"))
    roster.add_gear(conn, GID, "u1", "手榴弹")
    C.start(conn, GID, campaign_id=7, scene="a1.opera",
            enemies=[{"name": "小丑", "count": 1}],
            roller=dice.RandomRoller(1))
    _place(conn, C.load(conn, GID), (7, 7), {"小丑": (5, 7)})

    monkeypatch.setattr(turn_flow, "CONN", conn)
    monkeypatch.setattr(turn_flow, "GATE", Gate(conn))
    out = asyncio.run(turn_flow.handle(
        GID, "u1", "扔手榴弹炸小丑",
        roller=dice.ScriptedRoller([10, 4, 4, 4, 4, 4, 4], loop=True),
    ))
    assert out and "爆炸伤害 6D6" in out
    assert "手榴弹" not in (roster.get(conn, GID, "u1")["gear"] or [])
