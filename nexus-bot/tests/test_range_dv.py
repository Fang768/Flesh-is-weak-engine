"""阶段 8：距离（range_m / DM 估米数 / 动态任务 DV）。

口径（docs/剩余开发方案.md:122-130，2026-11-07 修订）：
① 场景/encounter 写了 range_m 就用它查武器距离表；
② 没有就请 DM 按场景估 {meters, reason}，校验 1–800 米并记战斗日志；
③ 都没有**不猜 DV**：请玩家/DM 明说米数（`combat.RANGE_ASK`，不消耗动作）。
世界始终有物理场景，所以不存在「无地图就按 15 米」的兜底。
非战斗任务：gate 基准 DV + DM ±2，最终夹在 9–29，带理由。
"""

import asyncio

import pytest

from core import combat as C
from core import roster, turn, turn_flow
from core.db import connect
from cyberpunk import dice

GID = "qq:range"


@pytest.fixture()
def conn():
    return connect(":memory:")


def _setup(conn, enemies=None):
    roster.save(conn, GID, "u1", roster.build(template="独狼", name="阿飞"))
    C.start(
        conn,
        GID,
        enemies=enemies or [{"name": "小丑", "count": 1}],
        roller=dice.RandomRoller(1),
    )
    return C.load(conn, GID)


def test_encounter_range_m_uses_weapon_table(conn):
    fight = _setup(conn, enemies=[{"name": "小丑", "range_m": 20}])
    # 手枪 13–25 米档 DV20
    text = C.attack(conn, GID, "u1", "小丑", skill="手枪",
                    roller=dice.ScriptedRoller([5, 3, 3], loop=True))
    c = next(c for c in fight.combatants if c.name == "小丑")
    assert c.range_m == 20
    assert "DV 20" in text
    assert "20 米（估）" in text


def test_scene_range_m_is_default(conn, monkeypatch):
    monkeypatch.setattr(C, "_scene_data", lambda conn, gid: {"range_m": 30})
    fight = _setup(conn)
    c = next(c for c in fight.combatants if c.name == "小丑")
    assert c.range_m == 30
    # 手枪 26–50 米档 DV25
    text = C.attack(conn, GID, "u1", "小丑", skill="手枪",
                    roller=dice.ScriptedRoller([5, 3, 3], loop=True))
    assert "DV 25" in text


def test_no_map_no_range_asks_instead_of_guessing(conn):
    """既没有地图也没有 range_m：不猜 DV15，直接请人说距离，且不消耗动作。"""
    fight = _setup(conn)
    pc = next(c for c in fight.combatants if c.side == "pc")
    text = C.attack(conn, GID, "u1", "小丑", skill="手枪",
                    roller=dice.ScriptedRoller([5, 3, 3], loop=True))
    assert text == C.RANGE_ASK
    assert "距离 20 米" in text
    after = C.load(conn, GID)
    assert after.round == fight.round and after.turn_index == fight.turn_index
    slot = C._SLOTS.get(GID, {}).get(pc.name)
    assert slot is None or not slot.action        # 没消耗动作


def test_close_range_sets_distance_for_everyone(conn):
    """玩家说「距离 20 米」→ 记成 20 米，之后 DV 按手枪表算（13–25 米档 = 20）。"""
    _setup(conn)
    told = C.close_range(conn, GID, "u1", "距离 20 米吧")
    assert told and "20 米" in told and "DV 20" in told
    # 全投 1：攻击 1+7+2=10 < DV20 未中，敌人 1+7+4=12 < 20 也未中，只验 DV 文案。
    text = C.attack(conn, GID, "u1", "小丑", skill="手枪",
                    roller=dice.ScriptedRoller([1] * 12, loop=True))
    assert "DV 20" in text and "20 米" in text


def test_close_range_ignores_text_without_number(conn):
    _setup(conn)
    assert C.close_range(conn, GID, "u1", "我看看四周") is None


def _turn_on(conn):
    fight = _setup(conn)
    enemy = next(c for c in fight.combatants if c.side != "pc")
    pc = next(c for c in fight.combatants if c.side == "pc")
    C.provoke(fight, pc.side, enemy.side)
    return fight, enemy, pc


def test_enemy_without_range_does_not_guess_dv(conn):
    """敌人回合没有距离数据时只「压过来」，不按 DV15 白打人。"""
    fight, enemy, pc = _turn_on(conn)
    lines: list[str] = []
    C._enemy_turn(conn, fight, enemy, dice.ScriptedRoller([10, 6, 6]), lines)
    assert any("压了过来" in x for x in lines)
    assert pc.hp == 40


def test_enemy_with_range_uses_weapon_table(conn):
    """有距离时敌人按自己的武器表算 DV。"""
    fight = _setup(conn, enemies=[{"name": "小丑", "range_m": 60}])
    enemy = next(c for c in fight.combatants if c.side != "pc")
    pc = next(c for c in fight.combatants if c.side == "pc")
    C.provoke(fight, pc.side, enemy.side)
    lines: list[str] = []
    C._enemy_turn(conn, fight, enemy, dice.ScriptedRoller([10, 6, 6]), lines)
    # 手枪 51–100 米档 DV30：10+7+4=21 < 30
    assert any("DV 30" in x for x in lines)
    assert pc.hp == 40


def test_dm_estimates_range_and_persists(conn, monkeypatch):
    fight = _setup(conn)
    turn_flow.CONN = conn

    async def fake_json(system, user, **kw):
        return {"meters": 40, "reason": "隔着半条街"}

    monkeypatch.setattr(turn_flow.llm, "complete_json", fake_json)
    asyncio.run(turn_flow._dm_estimate_range(fight, "小丑", "朝小丑开枪", GID))

    target = next(c for c in fight.combatants if c.name == "小丑")
    assert target.range_m == 40
    again = C.load(conn, GID)
    assert next(c for c in again.combatants if c.name == "小丑").range_m == 40
    from core import events

    got = events.recent(conn, GID, limit=3)
    assert any("DM 估距：40 米" in e["summary"] for e in got)


def test_dm_estimate_out_of_range_degrades(conn, monkeypatch):
    fight = _setup(conn)
    turn_flow.CONN = conn

    async def fake_json(system, user, **kw):
        return {"meters": 9999, "reason": "离谱"}

    monkeypatch.setattr(turn_flow.llm, "complete_json", fake_json)
    asyncio.run(turn_flow._dm_estimate_range(fight, "小丑", "开枪", GID))
    # 夹到 800 米；手枪这个距离已经没有档 → 攻击走超程 DV15 的说明
    target = next(c for c in fight.combatants if c.name == "小丑")
    assert target.range_m == 800

    async def boom(system, user, **kw):
        raise turn_flow.llm.LLMError("超时")

    fight2 = _setup(conn)
    monkeypatch.setattr(turn_flow.llm, "complete_json", boom)
    asyncio.run(turn_flow._dm_estimate_range(fight2, "小丑", "开枪", GID))
    assert next(c for c in fight2.combatants
                if c.name == "小丑").range_m == 0


def test_proposal_drops_adjust_without_reason():
    p = turn.Proposal.parse({"skill": "觉察", "dv_adjust": 2, "dv_reason": ""})
    assert p.dv_adjust == 0
    p = turn.Proposal.parse({"skill": "觉察", "dv_adjust": 5, "dv_reason": "雨夜"})
    assert p.dv_adjust == 2
    assert p.dv_reason == "雨夜"


def _easy_card():
    return {"name": "阿飞", "stats": {"INT": 6}, "skills": {"觉察": 2}}


def test_task_dv_adjust_clamped(monkeypatch):
    monkeypatch.setattr(turn.story, "gate_for",
                        lambda *a, **k: {"id": "g1", "skill": "觉察", "dv": 29})
    monkeypatch.setattr(turn.story, "scene", lambda *a, **k: {})
    monkeypatch.setattr(turn.story, "next_scene", lambda *a, **k: "")
    out = turn.decide(
        turn.Proposal(skill="觉察", dv_adjust=2, dv_reason="有掩体"),
        card=_easy_card(), story_id="s", scene_id="a", is_easy=True,
        roller=dice.RandomRoller(1),
    )
    assert out.dv == 29                      # 29+2 夹回 29
    assert "任务 DV 29" in out.dm_note

    monkeypatch.setattr(turn.story, "gate_for",
                        lambda *a, **k: {"id": "g1", "skill": "觉察", "dv": 10})
    out = turn.decide(
        turn.Proposal(skill="觉察", dv_adjust=-2, dv_reason="光线充足"),
        card=_easy_card(), story_id="s", scene_id="a", is_easy=True,
        roller=dice.RandomRoller(1),
    )
    assert out.dv == 9                       # 10-2 夹到 9
    assert "任务 DV 10 → 9" in out.dm_note
