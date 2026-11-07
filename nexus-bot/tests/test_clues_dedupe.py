"""线索、NPC 识别、事件去重。"""

import pytest

from core import clues, dedupe, npc_flow
from core.db import connect


@pytest.fixture()
def conn():
    return connect(":memory:")


CARD = {
    "name": "阿飞",
    "owner_qq": "1001",
    "stats": {"INT": 8, "REF": 6, "TECH": 5},
    "skills": {"觉察": 6, "地方专家": 2, "赛博技术": 0},
}


def test_match_by_keyword():
    assert clues.match("demo", "act1.dock", "我蹲下看看刹车痕") is not None
    assert clues.match("demo", "act1.dock", "我去买杯面") is None


def test_attempt_success_records_and_repeats():
    clue = clues.match("demo", "act1.dock", "看看刹车痕")
    from cyberpunk import dice

    # 1D10=10 + INT8 + 觉察6 = 24 ≥ 14
    conn = connect(":memory:")
    text = clues.attempt(
        conn, group_id="qq:g", campaign_id=1, card=CARD, clue=clue,
        roller=dice.ScriptedRoller([10]),
    )
    assert "🔎" in text
    assert clue["id"] in clues.seen(conn, "qq:g")
    again = clues.attempt(conn, group_id="qq:g", campaign_id=1, card=CARD, clue=clue)
    assert "查过了" in again


def test_attempt_failure_does_not_record():
    conn = connect(":memory:")
    clue = clues.match("demo", "act1.dock", "看看刹车痕")
    from cyberpunk import dice

    weak = {"name": "菜鸟", "owner_qq": "1002", "stats": {"INT": 2}, "skills": {}}
    text = clues.attempt(
        conn, group_id="qq:g", campaign_id=1, card=weak, clue=clue,
        roller=dice.ScriptedRoller([1]),
    )
    assert "❌" in text
    assert clue["id"] not in clues.seen(conn, "qq:g")


def test_npc_match_only_in_scene():
    assert npc_flow.match("demo", "act1.dock", "我跟米拉打招呼") is not None
    assert npc_flow.match("demo", "act1.dock", "我去找那个掮客") is None


def test_dedupe_first_then_duplicate(conn):
    assert dedupe.seen(conn, "qq:m1") is False
    assert dedupe.seen(conn, "qq:m1") is True
    assert dedupe.seen(conn, "qq:m2") is False


def test_dedupe_prune(conn):
    dedupe.seen(conn, "qq:old")
    conn.execute("UPDATE seen_events SET created_at = '2020-01-01T00:00:00+08:00'")
    conn.commit()
    assert dedupe.prune(conn) == 1
    assert dedupe.seen(conn, "qq:old") is False


def test_named_npc_beats_clue_keyword():
    """"米拉，这货是谁的？" 是跟人说话，不该被线索关键词抢走。"""
    assert npc_flow.match("demo", "act1.warehouse", "米拉，这货是谁的？") is not None
    # 单字关键词不再参与匹配
    assert clues.match("demo", "act1.warehouse", "米拉，这货是谁的？") is None


def test_dialogue_continuity_and_action_break():
    npc_flow._ACTIVE.clear()
    assert npc_flow.active_npc("qq:g", "u1") is None
    npc_flow.remember("qq:g", "u1", "mira")
    assert npc_flow.active_npc("qq:g", "u1") == "mira"
    # 别人不受影响
    assert npc_flow.active_npc("qq:g", "u2") is None
    # 动手就断
    assert npc_flow.looks_like_action("我掏枪打他")
    assert not npc_flow.looks_like_action("这货是谁的")
    npc_flow.forget("qq:g", "u1")
    assert npc_flow.active_npc("qq:g", "u1") is None
