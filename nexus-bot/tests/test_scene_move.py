"""阶段 6：非战斗坐标持久化 + 移动按 MOVE×2 米。"""

import pytest

from core import combat as C
from core import map as gridmap
from core import roster
from core.db import connect

GID = "qq:g1"
CAMP = 7
OWNER = "1001"


@pytest.fixture()
def conn():
    return connect(":memory:")


def _scene(conn, scene="a1.opera"):
    conn.execute(
        "INSERT INTO campaigns(id, title, status, scene, scenario, created_at, updated_at)"
        " VALUES (?, '测试', 'active', ?, 'opera', 'now', 'now')",
        (CAMP, scene),
    )
    conn.execute(
        "INSERT INTO group_sessions(group_id, campaign_id, mode, updated_at)"
        " VALUES (?, ?, 'active', 'now')",
        (GID, CAMP),
    )
    conn.commit()


def _card(conn, move=6):
    card = roster.build(template="独狼", name="阿飞")
    card["stats"]["MOVE"] = move
    roster.save(conn, GID, OWNER, card)


def _map():
    return gridmap.load("opera_hall")


def test_move_cells_defaults_to_six():
    assert C._move_cells({"stats": {}}) == 6
    assert C._move_cells({"stats": {"MOVE": 8}}) == 8
    assert C._move_cells(None) == 6


def test_scene_move_persists_position(conn):
    _scene(conn)
    _card(conn, move=6)
    game = _map()
    start = tuple(game.spawns["pc"])
    spots = C._spots_from(game, start)
    goal = next(p for p in spots if 0 < game.path(start, p)[1] <= 6)

    text = C.scene_move(conn, GID, OWNER, f"移动到 {goal[0]},{goal[1]}")
    assert text and "走到" in text
    saved = roster.get(conn, GID, OWNER)
    pos = saved["state"]["pos"]
    assert (pos["x"], pos["y"]) == goal
    assert pos["map"] == "opera_hall"


def test_scene_move_budget_then_continue(conn):
    _scene(conn)
    _card(conn, move=6)
    game = _map()
    start = tuple(game.spawns["pc"])
    far = max(C._spots_from(game, start), key=lambda p: game.path(start, p)[1])
    assert game.path(start, far)[1] > 6

    first = C.scene_move(conn, GID, OWNER, f"移动到 {far[0]},{far[1]}")
    assert first and "继续走" in first
    mid = roster.get(conn, GID, OWNER)["state"]["pos"]
    assert (mid["x"], mid["y"]) != far
    assert mid["goal"] == [far[0], far[1]]

    for _ in range(20):
        text = C.scene_move(conn, GID, OWNER, "继续走")
        if "走到 (" in text and "继续走" not in text:
            break
    saved = roster.get(conn, GID, OWNER)["state"]["pos"]
    assert (saved["x"], saved["y"]) == far
    assert "goal" not in saved


def test_map_text_marks_noncombat_positions(conn):
    _scene(conn)
    _card(conn)
    game = _map()
    start = tuple(game.spawns["pc"])
    spots = C._spots_from(game, start)
    goal = next(p for p in spots if 0 < game.path(start, p)[1] <= 6)
    C.scene_move(conn, GID, OWNER, f"移动到 {goal[0]},{goal[1]}")

    text = C.map_text(conn, GID, OWNER)
    assert "@" in text
    assert "非战斗移动" in text
    assert aim_row(text, goal)


def aim_row(text, pos):
    lines = text.splitlines()
    for y, line in enumerate(lines[2:]):
        stripped = line[2:] if len(line) > 2 else ""
        if len(stripped) > pos[0] and stripped[pos[0]] == "@":
            return y == pos[1]
    return False


def test_scene_move_without_map_returns_none(conn):
    _scene(conn, scene="a1.hook")
    _card(conn)
    assert C.scene_move(conn, GID, OWNER, "移动到 2,2") is None


def test_handle_routes_noncombat_move_without_model(conn, monkeypatch):
    import asyncio

    from core import turn_flow
    from core.session import Gate

    _scene(conn)
    _card(conn)
    monkeypatch.setattr(turn_flow, "CONN", conn)
    monkeypatch.setattr(turn_flow, "GATE", Gate(conn))
    game = _map()
    start = tuple(game.spawns["pc"])
    spots = C._spots_from(game, start)
    goal = next(p for p in spots if 0 < game.path(start, p)[1] <= 6)

    out = asyncio.run(turn_flow.handle(GID, OWNER, f"移动到 {goal[0]},{goal[1]}"))
    assert out and "走到" in out
    assert roster.get(conn, GID, OWNER)["state"]["pos"]["x"] == goal[0]
