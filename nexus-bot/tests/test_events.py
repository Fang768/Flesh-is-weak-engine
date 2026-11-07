"""事件流与成长的测试。"""

from __future__ import annotations

import pytest

from core import db, events, growth, roster


@pytest.fixture()
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def test_events_roundtrip(conn):
    events.record(conn, group_id="g1", kind="start", summary="开团《霓虹血债》")
    events.record(conn, group_id="g1", kind="scene", summary="你们停在码头，两个打手盯着")
    evs = events.recent(conn, "g1", 5)
    assert [e["kind"] for e in evs] == ["start", "scene"]
    assert "码头" in events.last_summary(conn, "g1")


def test_events_are_scoped_per_group(conn):
    events.record(conn, group_id="g1", kind="start", summary="A 团")
    events.record(conn, group_id="g2", kind="start", summary="B 团")
    assert len(events.recent(conn, "g1")) == 1
    assert events.recent(conn, "g1")[0]["summary"] == "A 团"


def test_render_lists_every_event(conn):
    events.record(conn, group_id="g1", kind="start", summary="开团")
    events.record(conn, group_id="g1", kind="pause", summary="暂停")
    text = events.render(events.recent(conn, "g1", 5))
    assert "开团" in text and "暂停" in text
    assert events.render([]) == "还没有记录。"


def test_ip_cost_grows_with_level():
    assert growth.ip_cost(1) == 2
    assert growth.ip_cost(4) == 8
    assert growth.ip_cost(9) == 18


def test_award_and_raise(conn):
    card = roster.build(template="独狼", name="阿七")
    growth.award(card, 12)
    level, spent = growth.raise_skill(card, "觉察", 1)
    assert spent == growth.ip_cost(level - 1)
    assert card["skills"]["觉察"] == level
    assert growth.state(card)["ip"] == 12 - spent


def test_raise_without_ip_fails(conn):
    card = roster.build(template="独狼", name="阿七")
    with pytest.raises(growth.GrowthError):
        growth.raise_skill(card, "觉察", 1)


def test_skill_cap(conn):
    card = roster.build(template="独狼", name="阿七")
    card["skills"]["觉察"] = 10
    growth.award(card, 100)
    with pytest.raises(growth.GrowthError):
        growth.raise_skill(card, "觉察", 1)


def test_favor_is_single_use(conn):
    card = roster.build(template="独狼", name="阿七", perks=["人情"])
    assert growth.favors_left(card) == ["人情"]
    growth.spend_favor(card, "人情")
    assert growth.favors_left(card) == []
    with pytest.raises(growth.GrowthError):
        growth.spend_favor(card, "人情")


def test_state_survives_save_and_load(conn):
    card = roster.build(template="独狼", name="阿七", perks=["权威", "夜视"])
    growth.award(card, 7)
    roster.save(conn, "g1", "u1", card)
    back = roster.get(conn, "g1", "u1")
    assert back["perks"] == ["Authority", "Night Vision"]
    assert growth.state(back)["ip"] == 7
