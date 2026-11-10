"""C 类义体：能力型入库 + 面板 / 交互接口网行前置 / 侦测命令。

规则出处 ``rules/red_core/raw/full.txt``：

* 交互接口是网行必须的接入插口（``:871``、``:2173``）。
* 其余 C 类（内置代理/无线电/监测器/摄像/字幕/虹移/虚拟现实等）书里没给数值，
  只入库、面板展示、交 DM。
"""

from __future__ import annotations

import pytest

from core import combat, cyber_flow, db, easy_roster, growth, roster
from core.session import Gate
from cyberpunk import cyber_bonus, dice, easy


@pytest.fixture()
def conn():
    return db.connect(":memory:")


def _pc(conn, eb=20000):
    roster.save(conn, "g1", "u1", roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, "g1", "u1")
    growth.grant_eb(card, eb)
    roster.save(conn, "g1", "u1", card)


def _inst(conn, item):
    return cyber_flow.install(conn, "g1", "u1", f"安装 {item}",
                              roller=dice.RandomRoller())


# ── 能力型入库 + 面板 ──────────────────────────────────


def test_ability_table_has_c_class():
    table = cyber_bonus.ability_table()
    for name in ("交互接口", "内置代理", "无线电通讯", "窃听监测器",
                 "辐射监测器", "雷达监测器", "追踪器", "微型摄像",
                 "字幕功能", "虹移功能", "虚拟现实投影", "赛博碟板",
                 "记忆芯片", "扰码器/解码器"):
        assert name in table, name
        assert table[name]["ability"], name


def test_panel_shows_abilities(conn):
    _pc(conn)
    for x in ("神经链路", "赛博音频套装", "窃听监测器", "无线电通讯"):
        _inst(conn, x)
    text = cyber_flow.status_text(conn, "g1", "u1")
    assert "能力：" in text
    assert "窃听监测器" in text and "无线电通讯" in text


def test_abilities_lists_installed(conn):
    _pc(conn)
    for x in ("神经链路", "赛博音频套装", "内置代理"):
        _inst(conn, x)
    got = cyber_bonus.abilities(roster.get(conn, "g1", "u1"))
    assert any("内置代理" in x for x in got)


# ── 交互接口 = 网行前置 ────────────────────────────────


def _net_fixture(conn, with_interface: bool):
    gate = Gate(conn)
    camp = gate.start_new("g1", "歌剧院之夜", scenario="opera")
    gate.resume("g1")
    card = dict(easy.templates()[0])
    if with_interface:
        card["cyber"] = list(card.get("cyber") or []) + ["交互接口"]
    easy_roster.save(conn, "g1", "u1", card)
    gate.set_scene(camp.id, "a3.church")
    combat.start(conn, "g1", campaign_id=camp.id, scene="a3.church",
                 enemies=(("自动炮塔", 2),), roller=dice.RandomRoller())
    return camp


def test_net_access_needs_interface_plug(conn):
    _net_fixture(conn, with_interface=False)
    msg = combat.net_action(conn, "g1", "u1", "接入接线盒",
                            roller=dice.ScriptedRoller([10]))
    assert "交互接口" in msg, msg


def test_net_access_works_with_interface_plug(conn):
    _net_fixture(conn, with_interface=True)
    msg = combat.net_action(conn, "g1", "u1", "接入接线盒",
                            roller=dice.ScriptedRoller([10]))
    assert "接入成功" in msg, msg


# ── 侦测 ────────────────────────────────────────────────


def test_detect_without_sensors(conn):
    _pc(conn)
    text = cyber_flow.detect(conn, "g1", "u1")
    assert "没有可用的侦测义体" in text


def test_detect_lists_sensor_capabilities(conn):
    _pc(conn)
    for x in ("神经链路", "赛博音频套装", "窃听监测器", "无线电通讯"):
        _inst(conn, x)
    text = cyber_flow.detect(conn, "g1", "u1")
    assert "侦测义体待命" in text
    assert "窃听监测器" in text and "无线电通讯" in text
