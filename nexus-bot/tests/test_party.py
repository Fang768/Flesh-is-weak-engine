"""多人同场：在场名单、轮流顺序、协助加成。"""

from __future__ import annotations

import pytest

from cyberpunk.dice import RandomRoller

from core import db, easy_roster, party, roster


@pytest.fixture()
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _two(conn, gid="qq:g1"):
    roster.save(conn, gid, "u1", roster.build(template="独狼", name="甲"))
    roster.save(conn, gid, "u2", roster.build(template="技工", name="乙"))


def test_members_lists_both_kinds(conn):
    _two(conn)
    from cyberpunk import easy as easy_rules

    easy_roster.save(conn, "qq:g1", "u3", easy_rules.templates()[0])
    people = party.members(conn, "qq:g1")
    assert [p.qq for p in people] == ["u1", "u2", "u3"]
    assert {p.kind for p in people} == {"red", "easy"}


def test_members_scoped_to_group(conn):
    _two(conn, "qq:g1")
    roster.save(conn, "qq:g2", "u9", roster.build(template="独狼", name="丙"))
    assert [p.qq for p in party.members(conn, "qq:g1")] == ["u1", "u2"]
    assert [p.qq for p in party.members(conn, "qq:g2")] == ["u9"]


def test_turn_order_advances(conn):
    _two(conn)
    assert party.current(conn, "qq:g1").qq == "u1"
    assert party.advance(conn, "qq:g1", "u1").qq == "u2"
    assert party.advance(conn, "qq:g1", "u2").qq == "u1"


def test_round_increments(conn):
    _two(conn)
    party.advance(conn, "qq:g1", "u1")
    party.advance(conn, "qq:g1", "u2")
    text = party.status_text(conn, "qq:g1")
    assert "第 2 轮" in text
    assert "现在轮到：甲" in text


def test_newcomer_appended(conn):
    roster.save(conn, "qq:g1", "u1", roster.build(template="独狼", name="甲"))
    party.current(conn, "qq:g1")
    roster.save(conn, "qq:g1", "u2", roster.build(template="技工", name="乙"))
    people = party.members(conn, "qq:g1")
    assert [p.qq for p in people] == ["u1", "u2"]
    # 新人接在后面，不会插队
    assert party.current(conn, "qq:g1").qq == "u1"


def test_empty_group(conn):
    assert party.current(conn, "qq:none") is None
    assert "还没有角色卡" in party.status_text(conn, "qq:none")


def test_reset(conn):
    _two(conn)
    party.advance(conn, "qq:g1", "u1")
    party.reset(conn, "qq:g1")
    assert party.current(conn, "qq:g1").qq == "u1"
