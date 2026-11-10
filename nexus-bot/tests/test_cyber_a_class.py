"""A 类检定加值 + 能力型义体的归位。

核对原文后发现上一轮分类有误，这里把口径定清楚：

| 义体 | 原文 | 实际归类 |
|---|---|---|
| 嗅觉增强 | ``:9828-9832``「可以使用**追踪技能**追踪气味痕迹」 | **检定加值**（追踪 +2） |
| 触觉增强 | ``:9849-9853``「用手接触表面，即能觉察 20m 内的震动」 | 能力型（无数字） |
| 化学物质分析器 | ``:9815-9820``「检测化学成分并数据库对比」 | 能力型（无数字） |
| 生物监测器 | ``:9717-9721``「读出生命体征，可连掌上代理」 | 能力型（无数字） |
| 复眼改造 | ``:10473-10478``「再加装 5 个额外赛博义眼」 | **插槽扩容**（已单独做） |

书里没给数字的三条按「只入库、交 DM 裁定」处理，不硬编 +2。
"""

from __future__ import annotations

import pytest

from core import cyber_flow, db, growth, roster
from cyberpunk import cyber, cyber_bonus, dice, immunity

GID = "qq:aclass"


@pytest.fixture()
def conn():
    return db.connect(":memory:")


# ---------- 嗅觉增强：唯一的真加值 ----------


def test_smell_enhancement_in_bonus_table():
    card = {"state": {"cyber": ["嗅觉增强"]}}
    assert cyber_bonus.check_bonus(card, skill="追踪") == (2, ["嗅觉增强+2"])


def test_smell_enhancement_only_affects_tracking():
    card = {"state": {"cyber": ["嗅觉增强"]}}
    for other in ("觉察", "手枪", "推理", "潜行"):
        assert cyber_bonus.check_bonus(card, skill=other) == (0, []), other


def test_smell_enhancement_shows_in_panel():
    shown = cyber_bonus.describe({"state": {"cyber": ["嗅觉增强"]}})
    assert any("嗅觉增强" in x and "追踪" in x for x in shown)


def test_smell_enhancement_needs_chip_slot(conn):
    """它是插入式芯片，必须先有芯片插槽。"""
    from cyberpunk import slots

    assert "芯片插槽" in slots.check_install([], "嗅觉增强")
    assert slots.check_install(["神经链路", "芯片插槽"], "嗅觉增强") == ""


def test_smell_enhancement_install_flow(conn):
    roster.save(conn, GID, "u1", roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, GID, "u1")
    growth.grant_eb(card, 5000)
    roster.save(conn, GID, "u1", card)
    # 三件义体的 HL 骰数不同（神经链路 2d6、芯片插槽 2d6、嗅觉增强 2d6），
    # 用随机 roller 避免手算骰子个数。
    for item in ("神经链路", "芯片插槽", "嗅觉增强"):
        text = cyber_flow.install(conn, GID, "u1", f"安装 {item}",
                                  roller=dice.RandomRoller())
        assert "装上了" in text, text
    got = roster.get(conn, GID, "u1")
    assert cyber_bonus.check_bonus(got, skill="追踪")[0] == 2


# ---------- 能力型三条：只入库，不参与数值 ----------


@pytest.mark.parametrize("name", ["触觉增强", "化学物质分析器", "生物监测器"])
def test_ability_only_items_are_recorded(name):
    meta = immunity.items()[name]
    assert meta.get("ability"), f"{name} 应该有 ability 描述"
    assert meta.get("immunes") == [], f"{name} 不该被当成免疫"


@pytest.mark.parametrize("name", ["触觉增强", "化学物质分析器", "生物监测器"])
def test_ability_only_items_give_no_bonus(name):
    card = {"state": {"cyber": [name]}}
    for skill in ("觉察", "追踪", "急救", "感知", "推理"):
        assert cyber_bonus.check_bonus(card, skill=skill) == (0, []), skill


@pytest.mark.parametrize("name", ["触觉增强", "化学物质分析器", "生物监测器"])
def test_ability_only_items_give_no_immunity(name):
    card = {"state": {"cyber": [name]}}
    assert immunity.installed_immunities(card) == {}


@pytest.mark.parametrize("name", ["触觉增强", "化学物质分析器", "生物监测器"])
def test_ability_only_items_exist_in_hl_table(name):
    assert cyber.hl_of(name) is not None


# ---------- 复眼改造不是检定加值 ----------


def test_retinal_upgrade_is_not_a_skill_bonus():
    card = {"state": {"cyber": ["复眼改造"]}}
    for skill in ("觉察", "追踪", "手枪"):
        assert cyber_bonus.check_bonus(card, skill=skill) == (0, []), skill


def test_retinal_upgrade_is_an_expansion():
    from cyberpunk import slots

    assert slots.capacity_bonus("复眼改造")["eyes"] == 5
