"""a3.entry：按上一场幸存者动态铺场（playbook_opera.txt:94）。"""

import pytest

from core import combat as C
from core import roster
from core.db import connect

GID = "qq:entry"
CAMP = 9


@pytest.fixture()
def conn():
    return connect(":memory:")


def _setup(conn, scene: str = "a3.entry", pc: int = 4):
    for i in range(pc):
        roster.save(conn, GID, str(2001 + i),
                    roster.build(template="独狼", name=f"队员{i}"))
    conn.execute(
        "INSERT INTO campaigns (id, title, status, scene, scenario, created_at, updated_at)"
        " VALUES (?, ?, 'active', ?, 'opera', 'x', 'x')",
        (CAMP, "歌剧院", scene),
    )
    conn.execute(
        "INSERT INTO group_sessions (group_id, campaign_id, mode, updated_at)"
        " VALUES (?, ?, 'active', 'x')",
        (GID, CAMP),
    )


def _actor(conn, actor_id: str, name: str, status: str):
    conn.execute(
        "INSERT INTO campaign_actors (campaign_id, actor_id, name, kind, status,"
        " hp, max_hp, scene, side, preset, fate, withdraw_at, script, data, updated_at)"
        " VALUES (?, ?, ?, 'enemy', ?, 10, 35, 'a1.opera', 'enemy', ?, '', 0, '{}', '{}', 'x')",
        (CAMP, actor_id, name, status, name),
    )


def test_both_survivors_guard_with_pc_minus_two_mooks(conn):
    _setup(conn)
    _actor(conn, "opera_clown", "小丑", "escaped")
    _actor(conn, "opera_monk", "僧侣", "escaped")

    groups, _mods = C._encounter_groups(conn, GID)
    names = [g["name"] for g in groups]
    assert "小丑" in names and "僧侣" in names
    mooks = sum(g["count"] for g in groups if g["name"] in ("审判官和尚", "博佐帮蠢蛋"))
    assert mooks == 2  # pc 4 - 2
    assert {g["name"] for g in groups if g["name"] in ("审判官和尚", "博佐帮蠢蛋")} == {
        "审判官和尚", "博佐帮蠢蛋"}


def test_only_monk_survives_all_mooks_are_judges(conn):
    _setup(conn)
    _actor(conn, "opera_monk", "僧侣", "escaped")
    _actor(conn, "opera_clown", "小丑", "dead")

    groups, _mods = C._encounter_groups(conn, GID)
    names = [g["name"] for g in groups]
    assert "僧侣" in names and "小丑" not in names
    assert "博佐帮蠢蛋" not in names
    assert next(g["count"] for g in groups if g["name"] == "审判官和尚") == 2


def test_no_survivors_skip_encounter(conn):
    _setup(conn)

    found = C._encounter_groups(conn, GID)
    assert found == ((), ())

    text = C.start_text(conn, GID, "")
    assert "跳过" in text
    assert C.active(conn, GID) is None


def test_count_expressions():
    assert C._count_value("=pc", 4) == 4
    assert C._count_value("=pc-2", 4) == 2
    assert C._count_value("=(pc-2)//2", 5) == 1
    assert C._count_value("=pc-2-(pc-2)//2", 5) == 2
    assert C._count_value("=pc-2", 2) == 0


def test_a1_groups_carry_actor_ids(conn):
    _setup(conn, scene="a1.opera")

    groups, _mods = C._encounter_groups(conn, GID)
    actors = {g["name"]: g["actor"] for g in groups}
    assert actors["小丑"] == "opera_clown"
    assert actors["僧侣"] == "opera_monk"
