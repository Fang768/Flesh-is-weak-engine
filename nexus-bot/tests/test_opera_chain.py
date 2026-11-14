"""《歌剧院之夜》真实战斗链：换场 → 遭遇 → on_win 三连推进 → 结局。

背景（2026-11-14 补测）：
    
之前只测了「a3 三场铺场 + 胜负钩子」的零件，**没有一条端到端路径测试**。
走真路径（encounter_text → 打赢 → aftermath）时发现一个真死路：

    a3.entry 只有 encounter、没有 exits；
    小丑和僧侣都没跑掉时，原文是「没有幸存者守门 → 跳过遭遇」，
    旧 start_text 只回一句「没人登场」，既不铺场也不推进，
    于是场景永远停在 a3.entry —— 玩家永久卡死，剧情推不动。

现在的口径：**跳过遭遇 = 按 encounter.on_win 推进**，和打赢同一条路径。
本文件把这条链钉住，防止以后再退化。
"""

from __future__ import annotations

import pytest

from core import combat, db, easy_roster, story, turn_flow
from core.session import Gate, Mode
from cyberpunk import dice, easy

GID = "g1"


@pytest.fixture()
def opera(monkeypatch):
    conn = db.connect(":memory:")
    gate = Gate(conn)
    camp = gate.start_new(GID, "歌剧院之夜", scenario="opera")
    gate.resume(GID)
    easy_roster.save(conn, GID, "u1", dict(easy.templates()[0]))
    monkeypatch.setattr(turn_flow, "CONN", conn)
    monkeypatch.setattr(turn_flow, "GATE", gate)
    yield conn, gate, camp
    conn.close()


def _win(conn, gate):
    """把这一场的敌人全打死 + 结束战斗，走官方 aftermath 推进。"""
    conn.execute(
        "UPDATE combatants SET hp = 0, dead = 1 WHERE group_id = ? AND side != 'pc'",
        (GID,),
    )
    conn.execute("UPDATE combats SET active = 0 WHERE group_id = ?", (GID,))
    conn.commit()
    return turn_flow.aftermath(GID) or ""


def _scene(gate):
    state = gate.state(GID)
    return state.campaign.scene if state.campaign else None


# ---------- 死路：跳过遭遇必须推进场景 ----------


def test_skipped_encounter_advances_the_scene(opera):
    """a3.entry 零幸存 → 不铺场，但必须推进到 a3.church（不能卡死）。"""
    conn, gate, camp = opera
    gate.set_scene(camp.id, "a3.entry")

    out = turn_flow.encounter_text(GID, "", campaign_id=camp.id, scene="a3.entry")

    assert "跳过" in out
    assert combat.active(conn, GID) is None
    assert _scene(gate) == "a3.church", "跳过遭遇却没换场 → 玩家卡在 a3.entry"
    assert gate.state(GID).mode is Mode.ACTIVE


def test_skipped_encounter_prints_the_new_scene(opera):
    """跳过之后要把新场景的名字/正文一起说出来，否则玩家不知道发生了啥。"""
    conn, gate, camp = opera
    gate.set_scene(camp.id, "a3.entry")

    out = turn_flow.encounter_text(GID, "", campaign_id=camp.id, scene="a3.entry")

    moved = story.scene("opera", "a3.church") or {}
    assert "📍" in out
    assert str(moved.get("name")) in out


def test_skip_does_not_fire_when_enemies_spawn(opera):
    """真刷出敌人时不推进——打赢才推。"""
    conn, gate, camp = opera
    gate.set_scene(camp.id, "a3.church")

    out = turn_flow.encounter_text(GID, "", campaign_id=camp.id, scene="a3.church")

    assert "⚔" in out
    assert combat.active(conn, GID) is not None
    assert _scene(gate) == "a3.church", "开打的同时不该换场"


def test_explicit_enemy_arg_is_never_skipped(opera):
    """玩家手打「开打 打手 2」时走的是手动铺场，跳过逻辑不介入。"""
    conn, gate, camp = opera
    gate.set_scene(camp.id, "a3.entry")

    out = turn_flow.encounter_text(GID, "打手 2", campaign_id=camp.id, scene="a3.entry")

    assert "⚔" in out
    assert combat.active(conn, GID) is not None
    assert _scene(gate) == "a3.entry"


def test_skip_without_campaign_is_safe(opera):
    """没开团时「开打」不该炸。"""
    conn, gate, camp = opera
    gate.end(GID)

    out = turn_flow.encounter_text(GID, "")

    assert isinstance(out, str)


# ---------- 端到端：一局跑到底 ----------


def test_full_chain_reaches_the_ending(opera):
    """a1.opera → a2.noodles → … → a3.boss → 结局，一路都能推进。"""
    conn, gate, camp = opera
    visited = []

    # 第一场：歌剧院（打赢 → a2.noodles）
    gate.set_scene(camp.id, "a1.opera")
    out = turn_flow.encounter_text(GID, "", campaign_id=camp.id, scene="a1.opera")
    assert "⚔" in out
    visited.append(_scene(gate))
    _win(conn, gate)
    assert _scene(gate) == "a2.noodles"

    # 剧情换场：拉面车 → 主人 → 教堂门口
    for line, expect in (
        ("约的是午夜，去歌剧院赴约", "a2.master"),
        ("拿上木桩，去联合教堂", "a3.entry"),
    ):
        import asyncio

        asyncio.run(turn_flow.handle(GID, "u1", line))
        assert _scene(gate) == expect, f"「{line}」没能换到 {expect}"

    # 教堂门口：零幸存 → 跳过 + 推进
    out = turn_flow.encounter_text(GID, "", campaign_id=camp.id, scene="a3.entry")
    assert "跳过" in out
    assert _scene(gate) == "a3.church"

    # 教堂：炮塔 + 鲁思文是**同一场**（原文 :102 炮塔一停他就出来）
    out = turn_flow.encounter_text(GID, "", campaign_id=camp.id, scene="a3.church")
    assert "⚔" in out
    assert "鲁思文" in out, "两波敌人都该在遭遇单里"
    tail = _win(conn, gate)
    assert "🏁" in tail
    assert gate.state(GID).mode is Mode.IDLE


def test_manual_spawn_drops_the_previous_encounter_note(opera):
    """手动「开打 打手 2」不该带着上一场遭遇的说明（炮塔注释串台）。"""
    conn, gate, camp = opera
    gate.set_scene(camp.id, "a3.church")
    turn_flow.encounter_text(GID, "", campaign_id=camp.id, scene="a3.church")
    combat.end(conn, GID)

    gate.set_scene(camp.id, "a1.hook")
    out = turn_flow.encounter_text(GID, "打手 2", campaign_id=camp.id,
                                   scene="a1.hook")

    assert "打手" in out
    assert "炮塔" not in out, "上一场遭遇的 note 泄漏到手动铺场"


def test_move_turn_does_not_nudge_the_old_scene(opera, monkeypatch):
    """换场那一回合不许提醒——否则玩家刚进门就被催「该动身了」。

    旧实现拿回合开始时的 scene_id 去评估提醒，换场分支已经把场景挪走了，
    于是提醒对着**刚离开**的场景触发，还会把「已提醒」记在旧场景上。
    """
    import asyncio

    from core import memory, story
    from core.turn import Proposal

    conn, gate, camp = opera
    gate.set_scene(camp.id, "a2.office")
    # 本场线索查完：满足 KIND_EXIT（「该动身了」）的触发条件
    prog = memory.load_progress(conn, camp.id, "a2.office")
    prog["clues_found"] = [c["id"] for c in
                           (story.scene("opera", "a2.office").get("clues") or [])]
    memory.save_progress(conn, camp.id, "a2.office", prog)

    seen = []

    async def fake_propose(text, *, card, scene, is_easy, board=""):
        return Proposal(action="check", skill="撬锁", approach="撬锁")

    async def fake_narrate(outcome, *, proposal, card, story_id):
        return "门开了。"

    async def fake_compose(conn_, cid, scene, *, kind, scene_def=None,
                           npc_names=None, clue_topics=None):
        seen.append((kind, scene))
        return "时候不早了。"

    monkeypatch.setattr(turn_flow.turn, "propose", fake_propose)
    monkeypatch.setattr(turn_flow.turn, "narrate", fake_narrate)
    monkeypatch.setattr(turn_flow.nudge_mod, "compose", fake_compose)

    msg = asyncio.run(turn_flow.handle(GID, "u1", "我撬锁",
                                       roller=dice.ScriptedRoller([10, 10, 10])))

    assert _scene(gate) == "a2.office_in", "前提没成立：这一回合应当换场"
    assert "💭" not in msg, "换场回合不该插提醒"
    assert seen == [], f"提醒不该在新旧任一场景上评估：{seen}"


def test_nudge_survives_inline_npc_scenes(opera, monkeypatch):
    """提醒触发的场景若用了内联 NPC 写法，过去会直接抛 TypeError。

    `a1.students` 的 `npcs` 是 `[{"key": "student", ...}]`；旧 `_maybe_nudge`
    拿这个 dict 去 `npc_table.get()`，抛 `unhashable type: 'dict'`，
    整个回合崩掉。无 gate 的场景又最容易连续空转触发提醒，所以踩中概率很高。
    """
    import asyncio

    from core import memory
    from core.turn import Proposal

    conn, gate, camp = opera
    gate.set_scene(camp.id, "a1.students")
    npcs = (story.scene("opera", "a1.students") or {}).get("npcs") or []
    assert isinstance(npcs[0], dict), "前提变了：a1.students 不再是内联写法"

    async def fake_propose(text, *, card, scene, is_easy, board=""):
        return Proposal(action="check", skill="交易", approach="买票")

    async def fake_narrate(outcome, *, proposal, card, story_id):
        return "（旁白）"

    async def fake_compose(conn_, cid, scene, *, kind, scene_def=None,
                           npc_names=None, clue_topics=None):
        return "你注意到六名学生挤在站台下。"

    monkeypatch.setattr(turn_flow.turn, "propose", fake_propose)
    monkeypatch.setattr(turn_flow.turn, "narrate", fake_narrate)
    monkeypatch.setattr(turn_flow.nudge_mod, "compose", fake_compose)

    # 连续 3 次空转 → 触发提醒（不崩就是本测试的重点）
    msg = ""
    for _ in range(3):
        msg = asyncio.run(turn_flow.handle(GID, "u1", "我买一张票",
                                           roller=dice.ScriptedRoller([10, 5])))

    assert "💭" in msg
    assert memory.load_progress(conn, camp.id, "a1.students")["nudged"]


def test_every_battle_scene_has_a_win_hook():
    """每场战斗都必须有 on_win，否则打完出不去。"""
    for sid in ("a1.opera", "a3.entry", "a3.church"):
        enc = (story.scene("opera", sid) or {}).get("encounter") or {}
        assert str(enc.get("on_win") or "").strip(), f"{sid} 打完没有 on_win"


def test_no_scene_is_a_dead_end():
    """每个场景要么有 exits、要么有 on_win、要么就是结局——不能是死路。"""
    for act in story.load("opera")["acts"]:
        for sc in act["scenes"]:
            enc = sc.get("encounter") or {}
            can_move = (
                bool(story.exits_of(sc))
                or bool(str(enc.get("on_win") or "").strip())
                or bool(sc.get("ending"))
            )
            assert can_move, f"{sc['id']} 是死路：没有 exits / on_win / ending"
