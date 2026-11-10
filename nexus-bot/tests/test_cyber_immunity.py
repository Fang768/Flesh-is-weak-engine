"""义体免疫：B 类 8 条。

规则依据（``rules/red_core/raw/full.txt``）：

* 防眩晕 ``:9871-9874`` 免疫闪光；需赛博义眼基础、双眼都装
* 水平阻尼降噪 ``:10019-10022`` 免疫巨大噪声
* 鼻滤器 ``:10130-10135`` 免疫毒气、废气
* 鳃 ``:10101-10105`` 水下呼吸
* 紫外 ``:9922-9929`` 无视黑暗/烟雾/雾气；强制双眼、每眼 2 槽
* 独立供氧 ``:10112-10117`` 30 分钟氧气
* 硬化防护 ``:10412-10418`` 免疫 EMP 冲击
* 雷达/声呐植入 ``:10136-10139`` 扫描 50m（感知，不是免疫）

免疫按**类别**匹配：剧本写 ``negate_if: ["闪光"]`` 或 ``["防眩晕"]`` 都生效。
"""

from __future__ import annotations

import pytest

from core import combat as C
from core import db, hazards, roster
from cyberpunk import dice, immunity

GID = "qq:immune"


@pytest.fixture()
def conn():
    return db.connect(":memory:")


def _pc(conn, cyber=(), qq="u1"):
    roster.save(conn, GID, qq, roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, GID, qq)
    card["state"] = {"cyber": list(cyber)}
    roster.save(conn, GID, qq, card)
    return roster.get(conn, GID, qq)


def _actor(card):
    return type("A", (), {"tags": C._card_tags(card)})()


# ---------- 数据自检 ----------


def test_all_eight_b_items_present():
    items = immunity.items()
    for name in ("防眩晕", "水平阻尼降噪", "鼻滤器", "鳃", "紫外",
                 "独立供氧", "硬化防护", "雷达/声呐植入"):
        assert name in items, f"缺 {name}"


def test_immunity_names_exist_in_hl_table():
    from cyberpunk import cyber

    table = cyber.hl_table()
    for name in immunity.items():
        assert name in table, f"免疫表里的「{name}」不在 cyberware_hl"


def test_both_eyes_flags():
    assert immunity.requires_both_eyes("防眩晕") is True
    assert immunity.requires_both_eyes("紫外") is True
    assert immunity.requires_both_eyes("鼻滤器") is False


# ---------- 类别查询 ----------


def test_categories_of_known_item():
    assert "闪光" in immunity.categories_of("防眩晕")
    assert "噪声" in immunity.categories_of("水平阻尼降噪")
    assert "毒气" in immunity.categories_of("鼻滤器")


def test_categories_via_alias():
    assert "噪声" in immunity.categories_of("声音抑制器")
    assert "黑暗" in immunity.categories_of("低光视觉")


def test_unknown_item_has_no_immunity():
    assert immunity.categories_of("瞄准目镜") == []


def test_radar_is_not_an_immunity():
    """雷达/声呐是感知能力，不该报免疫。"""
    assert immunity.categories_of("雷达/声呐植入") == []


# ---------- 卡上聚合 ----------


def test_installed_immunities_from_red_card():
    card = {"state": {"cyber": ["防眩晕", "鳃"]}}
    got = immunity.installed_immunities(card)
    assert got["闪光"] == "防眩晕"
    assert got["溺水"] == "鳃"


def test_installed_immunities_from_easy_card():
    """简易卡的 cyber 在顶层，也要认。"""
    card = {"cyber": ["鼻滤器"]}
    assert immunity.is_immune(card, "毒气") == "鼻滤器"


def test_empty_card_has_no_immunity():
    assert immunity.installed_immunities({}) == {}
    assert immunity.is_immune({}, "闪光") == ""


# ---------- 战斗修正：按义词/类别都能豁免 ----------


def test_negate_by_item_name():
    card = {"state": {"cyber": ["水平阻尼降噪"]}}
    assert C._negated({"negate_if": ["水平阻尼降噪"]}, _actor(card)) is True


def test_negate_by_category():
    """剧本写类别（闪光）也能被防眩晕豁免。"""
    card = {"state": {"cyber": ["防眩晕"]}}
    assert C._negated({"negate_if": ["闪光"]}, _actor(card)) is True


def test_negate_false_without_the_cyberware():
    card = {"state": {"cyber": []}}
    assert C._negated({"negate_if": ["闪光"]}, _actor(card)) is False


def test_negate_respects_alias():
    """数据里叫「声音抑制器」，剧本写「水平阻尼降噪」。"""
    card = {"state": {"cyber": ["声音抑制器"]}}
    assert C._negated({"negate_if": ["水平阻尼降噪"]}, _actor(card)) is True


def test_negate_handles_string_not_list():
    card = {"state": {"cyber": ["鼻滤器"]}}
    assert C._negated({"negate_if": "毒气"}, _actor(card)) is True


def test_negate_empty_tokens_is_not_immunity():
    card = {"state": {"cyber": ["鼻滤器"]}}
    assert C._negated({}, _actor(card)) is False


# ---------- hazards 接线 ----------


def test_gills_prevent_drowning(conn):
    _pc(conn, ["鳃"])
    h = hazards.drown(conn, GID, "u1", roller=dice.RandomRoller())
    assert h.damage == 0
    assert "鳃" in h.note


def test_oxygen_supply_prevents_drowning(conn):
    _pc(conn, ["独立供氧"])
    h = hazards.drown(conn, GID, "u1", roller=dice.RandomRoller())
    assert h.damage == 0
    assert "独立供氧" in h.note


def test_no_gills_means_drowning_hurts(conn):
    _pc(conn, [])
    h = hazards.drown(conn, GID, "u1", roller=dice.RandomRoller())
    assert h.damage > 0


def test_nasal_filter_blocks_poison_gas(conn):
    _pc(conn, ["鼻滤器"])
    h = hazards.poison(conn, GID, "u1", "强", roller=dice.RandomRoller())
    assert h.damage == 0
    assert "鼻滤器" in h.note


def test_poison_still_hurts_without_filter(conn):
    _pc(conn, [])
    h = hazards.poison(conn, GID, "u1", "致命", roller=dice.RandomRoller())
    assert h.damage > 0


# ---------- 面板展示 ----------


def test_immunity_description():
    card = {"state": {"cyber": ["防眩晕"]}}
    lines = immunity.describe(card)
    assert any("防眩晕" in x and "闪光" in x for x in lines)


def test_status_text_lists_immunity(conn):
    from core import cyber_flow

    _pc(conn, ["防眩晕"])
    text = cyber_flow.status_text(conn, GID, "u1")
    assert "免疫：" in text
