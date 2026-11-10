"""精神医疗疗程 + 安装手术 DV（2026-11-10）。

规则出处 ``rules/red_core/raw/full.txt``：

* 精神医疗 ``:19081-19118``：一般 500+100 耗材→2d6；极端 1000+500→4d6；
  每件赛博组件降人性上限 2，赛博格组件降 4；0 HL 的不降。
* 安装手术 ``:18770-18781``：购物中心 DV13 / 诊所 DV15 / 医院 DV17。
"""

from __future__ import annotations

import pytest

from core import cyber_flow, growth, roster
from core.db import connect
from cyberpunk import cyber, dice

GID = "qq:therapy"


@pytest.fixture()
def conn():
    return connect(":memory:")


def _pc(conn, eb=20000):
    roster.save(conn, GID, "u1", roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, GID, "u1")
    growth.grant_eb(card, eb)
    roster.save(conn, GID, "u1", card)


def _hum(conn):
    card = roster.get(conn, GID, "u1")
    return int((card.get("state") or {}).get("hum")
               or card["stats"]["EMP"] * 10)


def test_humanity_cap_reduces_per_cyberware():
    card = {"state": {"cyber": ["赛博手臂", "骨肉织绕"]}}
    # 两件都是 -2 → 100-4=96
    assert cyber_flow.humanity_cap(card) == 96
    # 赛博格组件 -4（人造肩膀）
    card2 = {"state": {"cyber": ["人造肩膀改造"]}}
    assert cyber_flow.humanity_cap(card2) == 96


def test_therapy_restores_dice_and_costs(conn):
    _pc(conn)
    cyber_flow.install(conn, GID, "u1", "安装 赛博手臂", roller=dice.RandomRoller())
    eb0 = growth.eb(roster.get(conn, GID, "u1"))
    h0 = _hum(conn)
    text = cyber_flow.therapy(conn, GID, "u1", "精神医疗 一般",
                              roller=dice.ScriptedRoller([6, 6]))
    assert "恢复 12 点人性" in text, text
    assert _hum(conn) == min(cyber_flow.humanity_cap(
        roster.get(conn, GID, "u1")), h0 + 12)
    assert growth.eb(roster.get(conn, GID, "u1")) == eb0 - 600


def test_therapy_extreme_uses_4d6(conn):
    _pc(conn)
    cyber_flow.install(conn, GID, "u1", "安装 赛博手臂", roller=dice.RandomRoller())
    text = cyber_flow.therapy(conn, GID, "u1", "精神医疗 极端",
                              roller=dice.ScriptedRoller([6, 6, 6, 6]))
    assert "恢复 24 点人性" in text, text


def test_therapy_not_enough_money(conn):
    _pc(conn, eb=10)
    text = cyber_flow.therapy(conn, GID, "u1", "精神医疗 一般",
                              roller=dice.RandomRoller())
    assert "做不起" in text, text


def test_surgery_dv_table(conn):
    """安装手术 DV：购物中心 13 / 诊所 15 / 医院 17（:18770-18781）。"""
    assert cyber_flow.SURGERY_DV == {"购物中心": 13, "诊所": 15, "医院": 17}
    # 赛博义眼 的设施是诊所
    assert cyber.hl_of("赛博义眼")["facility"] == "诊所"
