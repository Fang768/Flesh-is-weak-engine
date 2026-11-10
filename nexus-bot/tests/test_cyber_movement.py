"""移动型义体（2026-11-10）：攀爬脚 / 跳跃增强 / 蹼足 / 轮滑脚。

规则出处 ``rules/red_core/raw/full.txt``：

* ``:14488``「游泳，攀爬或者助跑跳，每经过 1m 都要花费 2m 的最大移动距离」
  → 攀爬/跳跃是**移动方式倍率 ×2**，不是地形。
* ``:10382``/``:28549`` 攀爬脚：攀爬时无视移速惩罚。
* ``:10388``/``:28556`` 跳跃增强：跳跃时无视移速惩罚。
* ``:10409``/``:28577`` 蹼足：游泳时无视移速惩罚（水格按 1 格）。
* ``:10395``/``:28563`` 轮滑脚：使用奔跑动作时移速 +6 米（本仓库 1 格 = 2 米 → +3 格）。

「约定」：战斗内移动不加检定，只按移动力结算（主人 2026-11-10 定）；
剧情里是否加检定由 DM 视情况定，不在引擎硬编码。
"""

from __future__ import annotations

import pytest

from core import combat as C
from core import map as gridmap
from core import roster
from core.db import connect
from cyberpunk import cyber_bonus, dice

GID = "qq:move"


@pytest.fixture()
def conn():
    return connect(":memory:")


def _model(*cyber: str) -> dict:
    return {"state": {"cyber": list(cyber)}}


# ── 地图层：花费倍率 / 水费覆盖 ────────────────────────────


def test_cell_cost_normal_and_water():
    m = gridmap._parse({"id": "t", "name": "t", "terrain": [".~", ".."]}, "t")
    assert m.cell_cost((0, 0)) == 1          # 地面
    assert m.cell_cost((1, 0)) == 2          # 水（游泳 ×2）
    assert m.cell_cost((1, 0), water_cost=1) == 1   # 蹼足


def test_cell_cost_climb_multiplier():
    m = gridmap._parse({"id": "t", "name": "t", "terrain": ["..", ".."]}, "t")
    assert m.cell_cost((1, 0), mult=2) == 2  # 攀爬 ×2


def test_move_climb_consumes_double():
    m = gridmap._parse({"id": "t", "name": "t", "terrain": ["...."]}, "t")
    route, used, arrived = m.move((0, 0), (2, 0), 10)
    assert used == 2 and arrived
    route2, used2, arrived2 = m.move((0, 0), (2, 0), 10, mult=2)
    assert used2 == 4 and arrived2           # 攀爬 2 格花 4 点


def test_move_water_discount():
    m = gridmap._parse({"id": "t", "name": "t", "terrain": ["...~"]}, "t")
    # 走到水里：普通水 2，蹼足 1
    _, normal_cost, _ = m.move((0, 0), (3, 0), 20)
    _, swim_cost, _ = m.move((0, 0), (3, 0), 20, water_cost=1)
    assert normal_cost - swim_cost == 1


# ── 义体豁免 ─────────────────────────────────────────────


def test_move_multiplier_normal_is_one():
    assert cyber_bonus.move_multiplier({}, mode="normal") == 1


def test_climb_multiplier_two_without_feet():
    assert cyber_bonus.move_multiplier({}, mode="climb") == 2


def test_climb_feet_removes_climb_penalty():
    assert cyber_bonus.move_multiplier(_model("攀爬脚"), mode="climb") == 1


def test_jump_multiplier_two_without_legs():
    assert cyber_bonus.move_multiplier({}, mode="jump") == 2


def test_jump_enhancement_removes_jump_penalty():
    assert cyber_bonus.move_multiplier(_model("跳跃增强"), mode="jump") == 1


def test_climb_feet_does_not_help_jump():
    # 攀爬脚只免攀爬，不免跳跃
    assert cyber_bonus.move_multiplier(_model("攀爬脚"), mode="jump") == 2


def test_water_cost_default_two_and_webbed_feet():
    assert cyber_bonus.water_cost({}) == 2
    assert cyber_bonus.water_cost(_model("蹼足")) == 1


def test_run_bonus_cells_only_with_skates():
    assert cyber_bonus.run_bonus_cells({}) == 0
    assert cyber_bonus.run_bonus_cells(_model("轮滑脚")) == 3


# ── 战斗内接线（act） ────────────────────────────────────


def _fight(conn, monkeypatch, terrain=("....", "....")):
    test_map = gridmap._parse(
        {"id": "t", "name": "t", "terrain": list(terrain), "cell": 2}, "t")
    monkeypatch.setattr(C, "_map_for_id", lambda _id: test_map)
    roster.save(conn, GID, "u1", roster.build(template="独狼", name="阿飞"))
    C.start(conn, GID, enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
    fight = C.load(conn, GID)
    pc = next(c for c in fight.order if c.side == "pc")
    pc.x, pc.y = 0, 0
    foe = next(c for c in fight.order if c.side != "pc")
    foe.x, foe.y = 5, 0
    fight.turn_index = fight.order.index(pc)
    C._save(conn, fight)
    return fight, pc


def _set_cyber(conn, *items):
    card = roster.get(conn, GID, "u1")
    state = dict(card.get("state") or {})
    state["cyber"] = list(items)
    card["state"] = state
    roster.save(conn, GID, "u1", card)


def test_act_climb_marked_and_double_cost(conn, monkeypatch):
    _fight(conn, monkeypatch)
    text = C.act(conn, GID, "u1", "攀爬到 1,0")
    assert "〔攀爬〕" in text or "攀爬/跳跃按 ×2" in text, text


def test_act_climb_with_feet_no_multiplier(conn, monkeypatch):
    _fight(conn, monkeypatch)
    _set_cyber(conn, "攀爬脚")
    text = C.act(conn, GID, "u1", "攀爬到 1,0")
    assert "〔攀爬〕" in text
    assert "×2" not in text


def test_act_jump_marked(conn, monkeypatch):
    _fight(conn, monkeypatch)
    text = C.act(conn, GID, "u1", "跳到 1,0")
    assert "〔跳跃〕" in text


def test_act_run_with_skates_bonus(conn, monkeypatch):
    _fight(conn, monkeypatch, terrain=("." * 12, "." * 12))
    _set_cyber(conn, "轮滑脚")
    # 先用移动动作，再「跑」——轮滑脚提示 +6 米，且额外移动真的多 3 格。
    C.act(conn, GID, "u1", "移动到 1,0")
    text = C.act(conn, GID, "u1", "跑")
    assert "轮滑脚" in text and "6 米" in text, text
    moved = C.act(conn, GID, "u1", "移动到 9,0")
    # 独狼 MOVE=5，轮滑脚 +3 → 8 格；地图够宽，能走到 9
    assert "走了 8 格" in moved, moved


def test_act_run_moves_before_skates_no_bonus(conn, monkeypatch):
    # 没轮滑脚：跑后的额外移动仍是 MOVE 格（5 格）
    _fight(conn, monkeypatch, terrain=("." * 12, "." * 12))
    C.act(conn, GID, "u1", "移动到 1,0")
    C.act(conn, GID, "u1", "跑")
    moved = C.act(conn, GID, "u1", "移动到 9,0")
    assert "走了 5 格" in moved, moved

