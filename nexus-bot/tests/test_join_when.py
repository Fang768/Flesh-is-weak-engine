"""增援入场（join_when）：炮塔毁掉的瞬间鲁思文登场。

原文（playbook_opera.txt:102）：
「一旦炮塔被处理完毕，角色们就会立即被从挂毯后面走出的高大人物打断。」

也就是说教堂那一场**是一场战斗**——炮塔倒下不是结束，是第二波出现的信号。
这里钉住引擎侧的三个行为：

1. 条件没满足时，增援「在场外」：不能被选中、不能行动
2. 炮塔全灭时不判胜，增援入场
3. 旧的 join_round 语义不受影响
"""

from __future__ import annotations

import pytest

from core import combat as C
from core import db, roster
from cyberpunk.dice import ScriptedRoller

GID = "qq:JW"
CAMP = 77


@pytest.fixture()
def conn():
    c = db.connect(":memory:")
    roster.save(c, GID, "3001", roster.build(template="独狼", name="队员"))
    c.execute(
        "INSERT INTO campaigns (id, title, status, scene, scenario,"
        " created_at, updated_at) VALUES (?, '增援', 'active', 's1', 'opera',"
        " 'x', 'x')",
        (CAMP,),
    )
    c.execute(
        "INSERT INTO group_sessions VALUES (?, ?, 'active', 'x')", (GID, CAMP))
    c.commit()
    yield c
    c.close()


def _arena(conn) -> C.Combat:
    return C.start(
        conn, GID, campaign_id=CAMP, scene="s1",
        enemies=[
            {"name": "自动炮塔", "count": 2, "faction": "turret"},
            {"name": "鲁思文勋爵", "count": 1, "faction": "russwin",
             "script": {"join_when": {"all_dead": "自动炮塔"}}},
        ],
        roller=ScriptedRoller([5], loop=True),
    )


def _boss(fight) -> C.Combatant:
    return next(c for c in fight.combatants if "鲁思文" in c.name)


def test_reinforcement_absent_until_condition(conn):
    fight = _arena(conn)
    assert not C.present(fight, _boss(fight))
    assert _boss(fight) in C.pending_join(fight)


def test_reinforcement_cannot_be_targeted_early(conn):
    fight = _arena(conn)
    reachable = {c.name for c in C.foes(fight, fight.pcs[0])}
    assert not any("鲁思文" in n for n in reachable)
    assert any("自动炮塔" in n for n in reachable)


def test_battle_does_not_end_when_reinforcement_is_waiting(conn):
    fight = _arena(conn)
    for c in fight.combatants:
        if "炮塔" in c.name:
            c.hp = 0
    lines: list = []
    C._check_over(fight, lines)
    assert fight.active, "鲁思文已经入场，战斗不该结束"
    assert any("登场" in ln for ln in lines), lines
    assert C.present(fight, _boss(fight)), "条件满足后鲁思文在场"


def test_battle_ends_when_everyone_is_down(conn):
    fight = _arena(conn)
    for c in fight.combatants:
        if c.side != "pc":
            c.hp = 0
    lines: list = []
    C._check_over(fight, lines)
    assert not fight.active


def test_boss_is_targetable_after_turrets_die(conn):
    fight = _arena(conn)
    for c in fight.combatants:
        if "炮塔" in c.name:
            c.hp = 0
    reachable = {c.name for c in C.foes(fight, fight.pcs[0])}
    assert any("鲁思文" in n for n in reachable)


def test_join_round_still_works(conn):
    fight = _arena(conn)
    late = fight.combatants[0]
    late.side = "enemy"
    late.script = {"join_round": 3}
    fight.round = 1
    assert not C.present(fight, late)
    fight.round = 3
    assert C.present(fight, late)


def test_no_join_when_means_always_present(conn):
    fight = _arena(conn)
    turret = next(c for c in fight.combatants if "炮塔" in c.name)
    assert C.present(fight, turret)
