"""阶段 2：campaign 演员表、Boss 命格退场、encounter ref 回归、过场 presence。"""

import json

import pytest

from core import combat as C
from core import roster, story, turn_flow
from core.db import connect
from cyberpunk import dice

GID = "qq:g1"
CAMP = 7


@pytest.fixture()
def conn():
    return connect(":memory:")


def _card(conn):
    card = roster.build(template="独狼", name="阿飞")
    roster.save(conn, GID, "1001", card)


def _boss_group(**over):
    group = {
        "name": "鲁思文勋爵",
        "count": 1,
        "faction": "russwin",
        "actor": "russwin",
        "fate": "cannot_die",
        "withdraw_at": 1,
        "hp": 5,
        "max_hp": 50,
        "data": {"sp": 0},
    }
    group.update(over)
    return group


def test_cannot_die_boss_escapes_and_returns_fresh(conn):
    _card(conn)
    C.start(conn, GID, campaign_id=CAMP, scene="a3.boss",
            enemies=(_boss_group(),),
            roller=dice.ScriptedRoller([10, 1]))
    C.close_range(conn, GID, "1001", "距离 8 米")
    # 攻击 10+7=17 命中；伤害 2+3=5（不掷出两个 6，避免触发严重伤势），
    # HP 5 → 0 触发 cannot_die 退场；伤势路径由 test_crit.py 单独覆盖
    text = C.attack(conn, GID, "1001", "鲁思文", skill="手枪",
                    roller=dice.ScriptedRoller([10, 2, 3]))
    assert "没有倒下" in text
    loaded = C.load(conn, GID)
    boss = next(c for c in loaded.combatants if c.side == "russwin")
    assert boss.escaped and not boss.dead and boss.hp == 1

    C.end(conn, GID)
    row = conn.execute(
        "SELECT * FROM campaign_actors WHERE campaign_id = ? AND actor_id = ?",
        (CAMP, "russwin"),
    ).fetchone()
    assert row is not None
    assert row["status"] == "escaped"
    assert row["hp"] == 1

    fresh = C.actor_group(conn, CAMP, "russwin", fresh=True)
    assert fresh is not None and fresh["hp"] == fresh["max_hp"] == 50
    hurt = C.actor_group(conn, CAMP, "russwin")
    assert hurt is not None and hurt["hp"] == 1

    C.start(conn, GID, campaign_id=CAMP, scene="b3.return",
            enemies=(fresh,), roller=dice.ScriptedRoller([10, 1]))
    again = C.load(conn, GID)
    boss = next(c for c in again.combatants if c.side == "russwin")
    assert boss.hp == 50 and not boss.escaped and boss.fate == "cannot_die"


def test_withdraw_at_threshold_marks_escaped(conn):
    _card(conn)
    C.start(conn, GID, campaign_id=CAMP, scene="b2",
            enemies=({"name": "僧侣", "count": 1, "faction": "monk",
                      "actor": "monk", "withdraw_at": 20, "hp": 30,
                      "max_hp": 45, "data": {"sp": 0}},),
            roller=dice.ScriptedRoller([10, 1]))
    C.close_range(conn, GID, "1001", "距离 8 米")
    # 攻击 10+7=17 命中；伤害 6+5=11，HP 30 → 19 ≤ withdraw_at 20 → 抽身退场。
    # 伤害骰不用两个 6（会触发严重伤势），那条路径由 test_crit.py 覆盖
    text = C.attack(conn, GID, "1001", "僧侣", skill="手枪",
                    roller=dice.ScriptedRoller([10, 6, 5]))
    assert "抽身退出了战斗" in text
    loaded = C.load(conn, GID)
    monk = next(c for c in loaded.combatants if c.side == "monk")
    assert monk.escaped and not monk.dead and monk.hp == 19


def test_encounter_ref_pulls_archived_actor(conn, monkeypatch):
    _card(conn)
    C.start(conn, GID, campaign_id=CAMP, scene="a3.boss",
            enemies=(_boss_group(),),
            roller=dice.ScriptedRoller([10, 1]))
    C.attack(conn, GID, "1001", "鲁思文", skill="手枪",
             roller=dice.ScriptedRoller([10, 2, 3]))
    C.end(conn, GID)

    conn.execute(
        "INSERT INTO campaigns (id, title, status, scene, scenario, created_at, updated_at)"
        " VALUES (?, ?, 'active', ?, 'fake', 'x', 'x')",
        (CAMP, "测试", "b3.return"),
    )
    conn.execute(
        "INSERT INTO group_sessions (group_id, campaign_id, mode, updated_at)"
        " VALUES (?, ?, 'active', 'x')",
        (GID, CAMP),
    )
    monkeypatch.setattr(story, "load", lambda sid: {
        "scenes": {"b3.return": {"encounter": {
            "groups": [{"ref": "russwin", "fresh": True}],
            "on_withdraw": "b3.after",
        }}},
        "scripts": {},
    })
    found = C._encounter_groups(conn, GID)
    assert found is not None
    groups, _mods = found
    assert groups[0]["actor"] == "russwin"
    assert groups[0]["hp"] == 50
    assert groups[0]["faction"] == "russwin"


def test_presence_render_and_untouchable_guard(monkeypatch):
    scene = {
        "presence": [
            {"name": "鲁思文勋爵", "line": "他站在祭坛边，像在看一出戏。",
             "untouchable": True},
            "唱诗班的孩子",
        ]
    }
    monkeypatch.setattr(story, "scene", lambda sid, scid: scene)

    lines = turn_flow._presence_lines("opera", "x")
    assert "👤 鲁思文勋爵：他站在祭坛边" in lines[0]
    assert "唱诗班的孩子" in lines[1]

    assert turn_flow._presence_guard("g", "opera", "x", "我看看四周") is None
    msg = turn_flow._presence_guard("g", "opera", "x", "我朝鲁思文勋爵开枪")
    assert msg is not None and "不是这场战斗里能攻击的目标" in msg
    resolved = turn_flow._resolve_pending("g")
    assert "招式落空" in resolved
