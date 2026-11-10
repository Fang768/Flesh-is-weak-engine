"""B 类义体：人造肩膀改造 + 植入线性框架 Σ/β。

规则出处 ``rules/red_core/raw/full.txt``：

* 人造肩膀改造 ``:28621-28626``：在原双臂下再装 2 条赛博手臂（共 4 条），
  只能装一个。
* 植入线性框架 Σ ``:28628-28637``：体格**提到** 12（不到 13+）；前置 体格≥6
  + 1 个骨肉织绕。
* 植入线性框架 β ``:28646-28655``：体格**提到** 14（不到 15+）；前置 体格≥8
  + 2 个骨肉织绕。
"""

from __future__ import annotations

import pytest

from core import cyber_flow, growth, roster
from core.db import connect
from cyberpunk import cyber_bonus, dice

GID = "qq:frame"


@pytest.fixture()
def conn():
    return connect(":memory:")


def _pc(conn, eb=60000):
    roster.save(conn, GID, "u1", roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, GID, "u1")
    growth.grant_eb(card, eb)
    roster.save(conn, GID, "u1", card)


def _inst(conn, item):
    return cyber_flow.install(conn, GID, "u1", f"安装 {item}",
                              roller=dice.RandomRoller())


def _body(conn):
    return roster.to_character(roster.get(conn, GID, "u1")).effective_stat("BODY")


# ── 人造肩膀改造 ───────────────────────────────────────


def test_default_two_arms_only(conn):
    _pc(conn)
    assert "装上了" in _inst(conn, "赛博手臂")
    assert "装上了" in _inst(conn, "赛博手臂")
    text = _inst(conn, "赛博手臂")
    assert "装不了" in text and "最多装 2 件" in text, text


def test_shoulder_raises_arm_limit_to_four(conn):
    _pc(conn)
    for _ in range(2):
        assert "装上了" in _inst(conn, "赛博手臂")
    assert "装上了" in _inst(conn, "人造肩膀改造")
    assert "装上了" in _inst(conn, "赛博手臂")
    assert "装上了" in _inst(conn, "赛博手臂")
    text = _inst(conn, "赛博手臂")
    assert "装不了" in text and "最多装 4 件" in text, text


# ── 植入线性框架 ───────────────────────────────────────


def test_frame_sigma_requires_one_hug(conn):
    _pc(conn)
    text = _inst(conn, "植入线性框架Σ")
    assert "骨肉织绕" in text, text


def test_frame_sigma_sets_body_to_12(conn):
    _pc(conn)
    assert "装上了" in _inst(conn, "骨肉织绕")
    assert "装上了" in _inst(conn, "植入线性框架Σ")
    assert _body(conn) == 12
    # HP 也跟着涨
    hero = roster.to_character(roster.get(conn, GID, "u1"))
    assert hero.max_hp > roster.build(template="独狼", name="x")["stats"]["BODY"] * 2


def test_frame_beta_requires_two_hugs(conn):
    _pc(conn)
    assert "装上了" in _inst(conn, "骨肉织绕")
    text = _inst(conn, "植入线性框架β")
    assert "2 个骨肉织绕" in text, text


def test_frame_beta_sets_body_to_14(conn):
    _pc(conn)
    for _ in range(2):
        assert "装上了" in _inst(conn, "骨肉织绕")
    text = _inst(conn, "植入线性框架β")
    # 骨肉织绕 2 份 → BODY 10；β 前置要 ≥8，满足
    assert "装上了" in text, text
    assert _body(conn) == 14


def test_frame_shows_in_panel(conn):
    _pc(conn)
    _inst(conn, "骨肉织绕")
    _inst(conn, "植入线性框架Σ")
    lines = cyber_bonus.describe(roster.get(conn, GID, "u1"))
    assert any("植入线性框架Σ" in l and "12" in l for l in lines)


def test_frame_does_not_need_a_cyberlimb(conn):
    """线性框架是骨架件，不挂赛博义肢。"""
    _pc(conn)
    _inst(conn, "骨肉织绕")
    text = _inst(conn, "植入线性框架Σ")
    assert "装上了" in text, text
