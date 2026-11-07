import pytest

from core import db, events, story, turn, turn_flow
from core.session import Gate
from cyberpunk import easy
from cyberpunk.dice import ScriptedRoller


@pytest.fixture()
def conn(monkeypatch):
    c = db.connect(":memory:")
    gate = Gate(c)
    monkeypatch.setattr(turn_flow, "CONN", c)
    monkeypatch.setattr(turn_flow, "GATE", gate)
    yield c, gate
    c.close()


@pytest.fixture()
def started(conn):
    c, gate = conn
    campaign = gate.start_new("g1", "霓虹血债")
    gate.resume("g1")
    return c, gate, campaign


def test_no_campaign_returns_none(conn):
    import asyncio

    assert asyncio.run(turn_flow.handle("g1", "u1", "我看看四周")) is None


def test_active_but_no_card(started):
    import asyncio

    msg = asyncio.run(turn_flow.handle("g1", "u1", "我看看四周"))
    assert "还没有角色卡" in msg


def test_turn_outputs_single_message(started, monkeypatch):
    import asyncio

    c, gate, campaign = started
    from core import easy_roster

    tpl = easy.templates()[0]
    easy_roster.save(c, "g1", "u1", tpl)

    async def fake_propose(text, *, card, scene, is_easy):
        return turn.Proposal(action="move", skill="潜行", approach="摸过去", target="act1.warehouse")

    async def fake_narrate(outcome, *, proposal, card, story_id):
        return "雨声盖住了脚步。"

    monkeypatch.setattr(turn_flow.turn, "propose", fake_propose)
    monkeypatch.setattr(turn_flow.turn, "narrate", fake_narrate)

    msg = asyncio.run(
        turn_flow.handle("g1", "u1", "我摸到仓库去", roller=ScriptedRoller([10, 6]))
    )
    assert isinstance(msg, str)
    assert "🎲" in msg           # 程序行
    assert "雨声盖住了脚步" in msg  # 旁白
    assert msg.count("\n\n") >= 1  # 分段落，但仍是一条消息
    kinds = [e["kind"] for e in events.recent(c, "g1", 10)]
    assert "roll" in kinds
    # 场景应该推进了
    assert gate.state("g1").campaign.scene != story.first_scene("demo")
