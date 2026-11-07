"""回合引擎测试：模型只提议、程序定数值。"""

from __future__ import annotations

import asyncio

import pytest

from core import llm, roster, turn
from cyberpunk import ScriptedRoller


@pytest.fixture()
def card():
    c = roster.build(template="独狼", name="阿七")
    c["_story"] = "demo"
    return c


def test_decide_success_moves_scene(card):
    out = turn.decide(
        turn.Proposal(skill="潜行"),
        card=card,
        story_id="demo",
        scene_id="act1.dock",
        roller=ScriptedRoller([9]),
        is_easy=False,
    )
    assert out.success is True
    assert out.scene_to == "act1.warehouse"
    assert out.dv == 15


def test_decide_failure_keeps_scene(card):
    out = turn.decide(
        turn.Proposal(skill="潜行"),
        card=card,
        story_id="demo",
        scene_id="act1.dock",
        roller=ScriptedRoller([1, 3]),  # 自然 1 会再掷一次（大失败连锁）
        is_easy=False,
    )
    assert out.success is False
    assert out.scene_to == "act1.dock"
    assert out.note


def test_decide_does_not_swap_skill(card):
    """模型说要观察，就不能偷偷换成场景里的潜行闸门。"""
    out = turn.decide(
        turn.Proposal(skill="觉察"),
        card=card,
        story_id="demo",
        scene_id="act1.dock",
        roller=ScriptedRoller([6]),
        is_easy=False,
    )
    assert out.skill == "觉察"
    assert out.dv == turn.FALLBACK_DV
    assert out.scene_to == "act1.dock"


def test_decide_easy_uses_strict_greater_than():
    card = {
        "name": "简易",
        "stats": {"INT": 5, "REF": 5, "DEX": 5, "TECH": 5, "COOL": 5,
                  "WILL": 5, "LUCK": 5, "MOVE": 5, "BODY": 5, "EMP": 5},
        "skills": {"潜行": 5},
    }
    # 5 + 5 + 4 = 14，刚好等于 DV15 → 简易版算失败
    out = turn.decide(
        turn.Proposal(skill="潜行"),
        card=card,
        story_id="demo",
        scene_id="act1.dock",
        roller=ScriptedRoller([4]),
        is_easy=True,
    )
    assert out.total == 14
    assert out.success is False


def test_narrate_uses_model(monkeypatch, card):
    async def fake(system, user, **kw):
        return {"narration": "雨落在铁皮上，像有人在数钱。"}

    monkeypatch.setattr(llm, "complete_json", fake)
    out = turn.decide(
        turn.Proposal(skill="潜行"),
        card=card,
        story_id="demo",
        scene_id="act1.dock",
        roller=ScriptedRoller([9]),
        is_easy=False,
    )
    text = asyncio.run(
        turn.narrate(out, proposal=turn.Proposal(skill="潜行"), card=card, story_id="demo")
    )
    assert "雨落在铁皮上" in text


def test_narrate_survives_model_error(monkeypatch, card):
    async def boom(*a, **kw):
        raise llm.LLMError("conn reset")

    monkeypatch.setattr(llm, "complete_json", boom)
    out = turn.decide(
        turn.Proposal(skill="潜行"),
        card=card,
        story_id="demo",
        scene_id="act1.dock",
        roller=ScriptedRoller([9]),
        is_easy=False,
    )
    assert asyncio.run(
        turn.narrate(out, proposal=turn.Proposal(skill="潜行"), card=card, story_id="demo")
    ) == ""


def test_allowed_skills_from_card(card):
    names = turn.allowed_skills(card, is_easy=False)
    assert names and all(isinstance(n, str) for n in names)
    assert "觉察" in names


def test_story_gate_lookup():
    gate = turn.story.gate_for("demo", "act1.dock", "潜行")
    assert gate is not None
    assert gate["next"] == "act1.warehouse"
